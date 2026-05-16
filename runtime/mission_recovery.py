"""
runtime/mission_recovery.py — Phase Z29D
==========================================
Mission recovery tooling + runtime stability protection.

Features:
  - Failure taxonomy detection (runaway retries, loops, replan storms, etc.)
  - Per-session stability monitor with auto-pause
  - Recovery action dispatch (restart, checkpoint resume, branch)
  - Failure inspection and recovery snapshot API
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("nexora.mission_recovery")

# ── Failure taxonomy ───────────────────────────────────────────────────────────

class FailureType:
    RUNAWAY_RETRIES      = "runaway_retries"
    INFINITE_LOOP        = "infinite_loop"
    REPLAN_STORM         = "replan_storm"
    PROVIDER_INSTABILITY = "provider_instability"
    SSE_FLOOD            = "sse_flood"
    STUCK_MISSION        = "stuck_mission"
    CONFIDENCE_COLLAPSE  = "confidence_collapse"
    CONTEXT_OVERFLOW     = "context_overflow"


class RecoveryAction:
    PAUSE            = "pause"
    CANCEL           = "cancel"
    RESTART          = "restart"
    CHECKPOINT_RESUME= "checkpoint_resume"
    BRANCH           = "branch"
    REDUCE_RETRIES   = "reduce_retries"
    SWITCH_PROVIDER  = "switch_provider"
    COMPRESS_CONTEXT = "compress_context"
    OPERATOR_REVIEW  = "operator_review"


# ── Stability thresholds ──────────────────────────────────────────────────────

DEFAULT_THRESHOLDS = {
    "max_retries_per_step":      8,
    "max_replan_count":          5,
    "max_loop_count":            150,
    "max_consecutive_failures":  4,
    "stuck_timeout_s":           180.0,
    "max_sse_events_per_min":    300,
    "confidence_collapse_floor": 0.15,
    "context_overflow_pct":      98.0,
}


# ── Session health record ──────────────────────────────────────────────────────

@dataclass
class SessionHealth:
    sid:                     str
    retry_counts:            dict[str, int]   = field(default_factory=dict)   # step_id -> count
    replan_count:            int              = 0
    loop_count:              int              = 0
    consecutive_failures:    int              = 0
    last_step_ts:            float            = field(default_factory=time.time)
    sse_events_window:       list[float]      = field(default_factory=list)   # timestamps
    detected_failures:       list[dict]       = field(default_factory=list)
    auto_paused:             bool             = False
    recovery_history:        list[dict]       = field(default_factory=list)
    thresholds:              dict             = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))

    # ── Observation recording ─────────────────────────────────────────────────

    def record_step(self, step_id: str) -> None:
        self.last_step_ts = time.time()

    def record_retry(self, step_id: str) -> int:
        self.retry_counts[step_id] = self.retry_counts.get(step_id, 0) + 1
        return self.retry_counts[step_id]

    def record_replan(self) -> int:
        self.replan_count += 1
        return self.replan_count

    def record_loop(self) -> int:
        self.loop_count += 1
        return self.loop_count

    def record_failure(self) -> int:
        self.consecutive_failures += 1
        return self.consecutive_failures

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def record_sse_event(self) -> None:
        now = time.time()
        self.sse_events_window.append(now)
        # Prune events older than 60s
        self.sse_events_window = [t for t in self.sse_events_window if now - t < 60.0]

    # ── Anomaly detection ─────────────────────────────────────────────────────

    def detect_anomalies(self) -> list[dict]:
        anomalies = []
        th = self.thresholds

        # Runaway retries
        for step_id, count in self.retry_counts.items():
            if count >= th["max_retries_per_step"]:
                anomalies.append({
                    "type": FailureType.RUNAWAY_RETRIES,
                    "detail": f"Step '{step_id}' retried {count} times",
                    "severity": "high",
                    "recommended": RecoveryAction.REDUCE_RETRIES,
                })

        # Replan storm
        if self.replan_count >= th["max_replan_count"]:
            anomalies.append({
                "type": FailureType.REPLAN_STORM,
                "detail": f"Replanned {self.replan_count} times",
                "severity": "high",
                "recommended": RecoveryAction.OPERATOR_REVIEW,
            })

        # Infinite loop
        if self.loop_count >= th["max_loop_count"]:
            anomalies.append({
                "type": FailureType.INFINITE_LOOP,
                "detail": f"Loop count {self.loop_count} exceeds threshold {th['max_loop_count']}",
                "severity": "critical",
                "recommended": RecoveryAction.CANCEL,
            })

        # Consecutive failures
        if self.consecutive_failures >= th["max_consecutive_failures"]:
            anomalies.append({
                "type": FailureType.PROVIDER_INSTABILITY,
                "detail": f"{self.consecutive_failures} consecutive failures",
                "severity": "high",
                "recommended": RecoveryAction.SWITCH_PROVIDER,
            })

        # Stuck mission
        elapsed = time.time() - self.last_step_ts
        if elapsed >= th["stuck_timeout_s"] and self.loop_count > 0:
            anomalies.append({
                "type": FailureType.STUCK_MISSION,
                "detail": f"No step progress in {elapsed:.0f}s",
                "severity": "high",
                "recommended": RecoveryAction.PAUSE,
            })

        # SSE flooding
        if len(self.sse_events_window) >= th["max_sse_events_per_min"]:
            anomalies.append({
                "type": FailureType.SSE_FLOOD,
                "detail": f"{len(self.sse_events_window)} SSE events/min",
                "severity": "medium",
                "recommended": RecoveryAction.REDUCE_RETRIES,
            })

        return anomalies

    def stability_score(self) -> float:
        """0.0 = critical, 1.0 = healthy"""
        score = 1.0
        th = self.thresholds

        max_r = max(self.retry_counts.values(), default=0)
        score -= min(max_r / max(th["max_retries_per_step"], 1), 1.0) * 0.3
        score -= min(self.replan_count / max(th["max_replan_count"], 1), 1.0) * 0.25
        score -= min(self.consecutive_failures / max(th["max_consecutive_failures"], 1), 1.0) * 0.25
        score -= min(self.loop_count / max(th["max_loop_count"], 1), 1.0) * 0.2

        return max(0.0, round(score, 3))

    def to_dict(self) -> dict:
        return {
            "sid":                  self.sid,
            "stability_score":      self.stability_score(),
            "auto_paused":          self.auto_paused,
            "replan_count":         self.replan_count,
            "loop_count":           self.loop_count,
            "consecutive_failures": self.consecutive_failures,
            "retry_counts":         dict(self.retry_counts),
            "sse_rate_per_min":     len(self.sse_events_window),
            "stuck_for_s":          round(time.time() - self.last_step_ts, 1),
            "detected_failures":    list(self.detected_failures[-5:]),
            "recovery_history":     list(self.recovery_history[-5:]),
            "ts":                   time.time(),
        }


# ── Registry ──────────────────────────────────────────────────────────────────

_lock     = threading.Lock()
_sessions: dict[str, SessionHealth] = {}


def get_health(sid: str) -> SessionHealth:
    with _lock:
        if sid not in _sessions:
            _sessions[sid] = SessionHealth(sid=sid)
        return _sessions[sid]


def drop_health(sid: str) -> None:
    with _lock:
        _sessions.pop(sid, None)


# ── Recording helpers (thin wrappers for agent.py) ────────────────────────────

def on_step_start(sid: str, step_id: str) -> None:
    get_health(sid).record_step(step_id)


def on_retry(sid: str, step_id: str) -> int:
    return get_health(sid).record_retry(step_id)


def on_replan(sid: str) -> int:
    return get_health(sid).record_replan()


def on_loop_tick(sid: str) -> int:
    return get_health(sid).record_loop()


def on_failure(sid: str) -> int:
    return get_health(sid).record_failure()


def on_success(sid: str) -> None:
    get_health(sid).record_success()


def on_sse_event(sid: str) -> None:
    get_health(sid).record_sse_event()


# ── Stability check + auto-protection ────────────────────────────────────────

def check_stability(
    sid:     str,
    emit_fn: Callable | None = None,
    auto_pause: bool = True,
) -> list[dict]:
    """
    Run anomaly detection. If critical anomalies found and auto_pause=True,
    auto-pauses the mission and emits governance request.
    Returns list of detected anomalies.
    """
    health = get_health(sid)
    anomalies = health.detect_anomalies()

    if not anomalies:
        return []

    # Record into health
    for a in anomalies:
        a["ts"] = time.time()
        health.detected_failures.append(a)

    critical = [a for a in anomalies if a.get("severity") in ("critical", "high")]

    if critical and auto_pause and not health.auto_paused:
        health.auto_paused = True
        worst = max(critical, key=lambda a: {"critical": 2, "high": 1}.get(a["severity"], 0))

        # Emit auto-pause event
        if emit_fn:
            try:
                emit_fn("agent.stability_alert", {
                    "sid":         sid,
                    "anomaly":     worst["type"],
                    "detail":      worst["detail"],
                    "severity":    worst["severity"],
                    "recommended": worst["recommended"],
                    "auto_paused": True,
                })
            except Exception:
                pass

        # Auto-pause via mission control
        try:
            from runtime.mission_control import pause_mission, MissionSignal
            pause_mission(sid, note=f"Auto-paused: {worst['type']} — {worst['detail']}", emit_fn=emit_fn)
        except Exception:
            pass

        # Submit governance request
        try:
            from runtime.governance_engine import submit_approval_request
            submit_approval_request(
                sid=sid,
                op_type="mission_recovery",
                summary=f"Auto-paused: {worst['type']}: {worst['detail']}",
                context={"anomalies": anomalies, "stability_score": health.stability_score()},
                emit_fn=emit_fn,
            )
        except Exception:
            pass

        logger.warning(f"[MissionRecovery] Auto-paused {sid}: {worst['type']}")

    return anomalies


# ── Recovery actions ──────────────────────────────────────────────────────────

def apply_recovery(
    sid:          str,
    action:       str,
    params:       dict | None = None,
    emit_fn:      Callable | None = None,
) -> dict:
    """
    Apply a recovery action to a session. Returns result dict.
    """
    params = params or {}
    health = get_health(sid)
    result = {"ok": False, "action": action, "sid": sid, "ts": time.time()}

    try:
        if action == RecoveryAction.PAUSE:
            from runtime.mission_control import pause_mission
            act = pause_mission(sid, note=params.get("note", "Recovery pause"), emit_fn=emit_fn)
            result.update({"ok": True, "action_id": act.action_id})

        elif action == RecoveryAction.CANCEL:
            from runtime.mission_control import cancel_mission
            act = cancel_mission(sid, note=params.get("note", "Recovery cancel"), emit_fn=emit_fn)
            result.update({"ok": True, "action_id": act.action_id})

        elif action == RecoveryAction.REDUCE_RETRIES:
            from runtime.override_engine import apply_override
            budget = max(1, params.get("retry_budget", 3))
            ok, msg = apply_override(sid, "retry_budget", budget,
                                     operator_note="recovery: reduce retry pressure", emit_fn=emit_fn)
            result.update({"ok": ok, "msg": msg, "retry_budget": budget})

        elif action == RecoveryAction.SWITCH_PROVIDER:
            from runtime.override_engine import apply_override
            provider = params.get("provider", "")
            model    = params.get("model", "")
            if provider:
                apply_override(sid, "provider", provider,
                               operator_note="recovery: switch provider", emit_fn=emit_fn)
            if model:
                apply_override(sid, "model", model,
                               operator_note="recovery: switch model", emit_fn=emit_fn)
            result.update({"ok": True, "provider": provider, "model": model})

        elif action == RecoveryAction.OPERATOR_REVIEW:
            from runtime.governance_engine import submit_approval_request
            req = submit_approval_request(
                sid=sid, op_type="mission_recovery",
                summary=f"Operator review requested: {params.get('reason', 'stability concern')}",
                context={"health": health.to_dict()},
                emit_fn=emit_fn,
            )
            result.update({"ok": True, "request_id": req.request_id})

        elif action == RecoveryAction.CHECKPOINT_RESUME:
            # Signal mission_control to resume from last checkpoint
            from runtime.mission_control import resume_mission
            health.auto_paused = False
            act = resume_mission(sid, note="Recovery: resume from checkpoint", emit_fn=emit_fn)
            result.update({"ok": True, "action_id": act.action_id})

        elif action == RecoveryAction.COMPRESS_CONTEXT:
            from runtime.context_compression import get_session_context
            ctx = get_session_context(sid)
            ctx.compress()
            result.update({"ok": True, "msg": "context compressed"})

        else:
            result.update({"ok": False, "msg": f"Unknown recovery action: {action}"})

    except Exception as e:
        result.update({"ok": False, "error": str(e)})

    health.recovery_history.append({
        "action": action, "result": result.get("ok"), "ts": time.time(),
        "params": {k: str(v)[:40] for k, v in params.items()},
    })

    if emit_fn:
        try:
            emit_fn("agent.recovery_applied", {"sid": sid, "action": action, **result})
        except Exception:
            pass

    logger.info(f"[MissionRecovery] {action} on {sid}: ok={result.get('ok')}")
    return result


# ── Snapshot API ──────────────────────────────────────────────────────────────

def get_recovery_snapshot(sid: str) -> dict:
    health = get_health(sid)
    anomalies = health.detect_anomalies()
    return {
        "health":        health.to_dict(),
        "anomalies":     anomalies,
        "anomaly_count": len(anomalies),
        "critical_count": sum(1 for a in anomalies if a.get("severity") == "critical"),
        "recommended_actions": list({a["recommended"] for a in anomalies}),
    }


def stability_dashboard() -> dict:
    with _lock:
        sessions = list(_sessions.values())
    items = []
    for h in sessions:
        items.append({
            "sid":             h.sid,
            "stability_score": h.stability_score(),
            "auto_paused":     h.auto_paused,
            "anomaly_count":   len(h.detect_anomalies()),
        })
    items.sort(key=lambda x: x["stability_score"])
    return {
        "session_count":   len(items),
        "unstable_count":  sum(1 for i in items if i["stability_score"] < 0.5),
        "auto_paused":     sum(1 for i in items if i["auto_paused"]),
        "sessions":        items,
        "ts":              time.time(),
    }
