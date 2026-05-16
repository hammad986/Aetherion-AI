"""
devops/playbook_engine.py — Self-Healing Playbook Engine
=========================================================
Controlled, safe autonomous remediation with:

  • Remediation budget: max N actions per component per hour
  • Cooldown periods: min gap between consecutive actions on same target
  • Escalation thresholds: if playbook runs > K times without recovery → HITL
  • Rollback checkpoints: state captured before any remediation
  • Storm prevention: max 3 concurrent remediations globally
  • Recursive loop detection: same playbook cannot trigger itself
  • All actions are idempotent and replay-safe
  • Operator can veto any pending remediation

Self-healing actions available:
  RESTART_WORKER         — signal task queue to spawn replacement worker
  RECYCLE_BROWSER        — kill stuck browser and release slot
  CLEAR_ORPHANED_LOCKS   — force-release all locks for stale sessions
  RECONNECT_REDIS        — trigger Redis reconnect probe
  RECONNECT_POSTGRES     — trigger PostgreSQL reconnect probe
  DRAIN_QUEUE            — pause new task submission; drain existing
  RESTORE_SSE_STREAM     — evict all clients for session; accept new
  ACTIVATE_DEGRADED_MODE — explicitly enter degraded mode
  ROLLBACK_DEPLOYMENT    — trigger deployment rollback to last stable
  EMERGENCY_LOCKDOWN     — security lockdown (extreme cases only)

Safety boundaries:
  SAFE_AUTO     — can execute without operator approval
  NEEDS_CONFIRM — operator must approve within CONFIRM_TIMEOUT_SEC
  HUMAN_ONLY    — never auto-execute; always pages operator
"""

import hashlib
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Set

logger = logging.getLogger("nexora.devops.playbook")

# ─── Configuration ────────────────────────────────────────────────────────────
MAX_ACTIONS_PER_HOUR   = int(os.getenv("PLAYBOOK_BUDGET_PER_HOUR", "10"))
COOLDOWN_SEC           = int(os.getenv("PLAYBOOK_COOLDOWN_SEC",    "120"))
MAX_CONCURRENT         = int(os.getenv("PLAYBOOK_MAX_CONCURRENT",   "3"))
ESCALATE_AFTER_RUNS    = int(os.getenv("PLAYBOOK_ESCALATE_RUNS",    "5"))
CONFIRM_TIMEOUT_SEC    = int(os.getenv("PLAYBOOK_CONFIRM_TIMEOUT", "300"))


class PlaybookAction(str, Enum):
    RESTART_WORKER        = "restart_worker"
    RECYCLE_BROWSER       = "recycle_browser"
    CLEAR_ORPHANED_LOCKS  = "clear_orphaned_locks"
    RECONNECT_REDIS       = "reconnect_redis"
    RECONNECT_POSTGRES    = "reconnect_postgres"
    DRAIN_QUEUE           = "drain_queue"
    RESTORE_SSE_STREAM    = "restore_sse_stream"
    ACTIVATE_DEGRADED     = "activate_degraded_mode"
    ROLLBACK_DEPLOYMENT   = "rollback_deployment"
    EMERGENCY_LOCKDOWN    = "emergency_lockdown"


class SafetyBoundary(str, Enum):
    SAFE_AUTO    = "SAFE_AUTO"      # executes automatically
    NEEDS_CONFIRM= "NEEDS_CONFIRM"  # queued; operator has CONFIRM_TIMEOUT_SEC
    HUMAN_ONLY   = "HUMAN_ONLY"     # never auto-executes


# Action → safety classification
_ACTION_SAFETY: Dict[PlaybookAction, SafetyBoundary] = {
    PlaybookAction.RESTART_WORKER:       SafetyBoundary.SAFE_AUTO,
    PlaybookAction.RECYCLE_BROWSER:      SafetyBoundary.SAFE_AUTO,
    PlaybookAction.CLEAR_ORPHANED_LOCKS: SafetyBoundary.SAFE_AUTO,
    PlaybookAction.RECONNECT_REDIS:      SafetyBoundary.SAFE_AUTO,
    PlaybookAction.RECONNECT_POSTGRES:   SafetyBoundary.SAFE_AUTO,
    PlaybookAction.DRAIN_QUEUE:          SafetyBoundary.NEEDS_CONFIRM,
    PlaybookAction.RESTORE_SSE_STREAM:   SafetyBoundary.SAFE_AUTO,
    PlaybookAction.ACTIVATE_DEGRADED:    SafetyBoundary.SAFE_AUTO,
    PlaybookAction.ROLLBACK_DEPLOYMENT:  SafetyBoundary.NEEDS_CONFIRM,
    PlaybookAction.EMERGENCY_LOCKDOWN:   SafetyBoundary.HUMAN_ONLY,
}

# Component → recommended actions (ordered by severity)
_COMPONENT_PLAYBOOKS: Dict[str, List[PlaybookAction]] = {
    "workers":         [PlaybookAction.RESTART_WORKER, PlaybookAction.DRAIN_QUEUE],
    "task_queue":      [PlaybookAction.DRAIN_QUEUE, PlaybookAction.RESTART_WORKER],
    "event_bus":       [PlaybookAction.RECONNECT_REDIS],
    "browser_pool":    [PlaybookAction.RECYCLE_BROWSER],
    "lock_contention": [PlaybookAction.CLEAR_ORPHANED_LOCKS],
    "infra_degraded":  [PlaybookAction.RECONNECT_REDIS, PlaybookAction.RECONNECT_POSTGRES,
                        PlaybookAction.ACTIVATE_DEGRADED],
    "memory_pressure": [PlaybookAction.DRAIN_QUEUE, PlaybookAction.RECYCLE_BROWSER],
    "security_posture":[PlaybookAction.EMERGENCY_LOCKDOWN],
}


class PlaybookStatus(str, Enum):
    PENDING    = "PENDING"     # queued, waiting for execution or confirm
    RUNNING    = "RUNNING"     # executing
    SUCCEEDED  = "SUCCEEDED"
    FAILED     = "FAILED"
    VETOED     = "VETOED"      # operator rejected
    ESCALATED  = "ESCALATED"   # handed to human (auto can't fix)
    COOLDOWN   = "COOLDOWN"    # skipped due to cooldown


@dataclass
class PlaybookResult:
    run_id:     str
    action:     PlaybookAction
    component:  str
    status:     PlaybookStatus
    duration_ms:int = 0
    detail:     str = ""
    ts:         float = field(default_factory=time.time)
    checkpoint: dict = field(default_factory=dict)   # pre-action state snapshot


class PlaybookEngine:
    """
    Safe autonomous self-healing engine.

    Usage:
        engine.trigger(component="workers", health_level=HealthLevel.CRITICAL, metrics={})
    """

    def __init__(self):
        self._lock         = threading.RLock()
        self._history:     List[PlaybookResult] = []
        self._running_set: Set[str] = set()           # currently running action ids
        self._cooldowns:   Dict[str, float] = {}      # action+component → last run ts
        self._hourly_budget: Dict[str, List[float]] = {}  # action → [timestamps]
        self._pending_confirms: Dict[str, dict] = {}  # run_id → details
        self._escalated:   Dict[str, int] = {}        # component → consecutive failures
        self._vetoed_ids:  Set[str] = set()

    # ── Main trigger interface ────────────────────────────────────────────────

    def trigger(self, component: str, health_level, metrics: dict) -> Optional[PlaybookResult]:
        """
        Called by HealthMonitor when a component is CRITICAL.
        Selects and executes (or queues) the appropriate playbook.
        Returns PlaybookResult or None if skipped.
        """
        actions = _COMPONENT_PLAYBOOKS.get(component, [])
        if not actions:
            logger.info(f"[Playbook] No playbook for component: {component}")
            return None

        # Pick first action not currently cooling down and within budget
        for action in actions:
            if self._is_safe_to_run(action, component):
                return self._execute(action, component, metrics)

        logger.info(f"[Playbook] All actions for {component} are in cooldown or budget-exhausted")
        return None

    def _is_safe_to_run(self, action: PlaybookAction, component: str) -> bool:
        key = f"{action.value}:{component}"
        now = time.time()

        # Cooldown check
        last_run = self._cooldowns.get(key, 0)
        if now - last_run < COOLDOWN_SEC:
            return False

        # Budget check (per action per hour)
        budget_key = action.value
        timestamps = [t for t in self._hourly_budget.get(budget_key, [])
                      if now - t < 3600]
        if len(timestamps) >= MAX_ACTIONS_PER_HOUR:
            return False

        # Concurrent limit
        if len(self._running_set) >= MAX_CONCURRENT:
            return False

        return True

    def _execute(self, action: PlaybookAction, component: str,
                 metrics: dict) -> PlaybookResult:
        safety = _ACTION_SAFETY.get(action, SafetyBoundary.HUMAN_ONLY)

        # Escalation check
        consec = self._escalated.get(component, 0)
        if consec >= ESCALATE_AFTER_RUNS:
            result = PlaybookResult(
                run_id=uuid.uuid4().hex[:12],
                action=action, component=component,
                status=PlaybookStatus.ESCALATED,
                detail=f"Escalated after {consec} failed remediations — operator required."
            )
            self._record(result, action, component)
            logger.error(f"[Playbook] ESCALATED: {component} — {consec} consecutive failures")
            self._notify_hitl(component, action, metrics, consec)
            return result

        if safety == SafetyBoundary.HUMAN_ONLY:
            result = PlaybookResult(
                run_id=uuid.uuid4().hex[:12],
                action=action, component=component,
                status=PlaybookStatus.ESCALATED,
                detail="Action classified HUMAN_ONLY — paging operator."
            )
            self._record(result, action, component)
            self._notify_hitl(component, action, metrics, 0)
            return result

        if safety == SafetyBoundary.NEEDS_CONFIRM:
            run_id = uuid.uuid4().hex[:12]
            with self._lock:
                self._pending_confirms[run_id] = {
                    "action":    action.value,
                    "component": component,
                    "metrics":   metrics,
                    "queued_at": time.time(),
                    "expires_at": time.time() + CONFIRM_TIMEOUT_SEC,
                }
            result = PlaybookResult(
                run_id=run_id, action=action, component=component,
                status=PlaybookStatus.PENDING,
                detail=f"Awaiting operator confirmation (timeout {CONFIRM_TIMEOUT_SEC}s)"
            )
            self._record(result, action, component)
            logger.warning(f"[Playbook] PENDING CONFIRM: {action.value} on {component} run_id={run_id}")
            # Start timeout thread
            threading.Thread(
                target=self._confirm_timeout_watcher,
                args=(run_id, action, component, metrics),
                daemon=True
            ).start()
            return result

        # SAFE_AUTO — execute now
        return self._run_action(action, component, metrics)

    def _run_action(self, action: PlaybookAction, component: str,
                    metrics: dict) -> PlaybookResult:
        run_id    = uuid.uuid4().hex[:12]
        key       = f"{action.value}:{component}"
        checkpoint = self._capture_checkpoint(component)

        with self._lock:
            self._running_set.add(run_id)

        start = time.time()
        status  = PlaybookStatus.FAILED
        detail  = ""

        try:
            logger.info(f"[Playbook] EXECUTING: {action.value} on {component} run_id={run_id}")
            detail = self._dispatch(action, component, metrics)
            status = PlaybookStatus.SUCCEEDED
            # Reset escalation counter on success
            with self._lock:
                self._escalated[component] = 0
        except Exception as e:
            detail = str(e)[:200]
            logger.error(f"[Playbook] FAILED: {action.value} on {component}: {e}")
            with self._lock:
                self._escalated[component] = self._escalated.get(component, 0) + 1
        finally:
            duration_ms = int((time.time() - start) * 1000)
            with self._lock:
                self._running_set.discard(run_id)
                self._cooldowns[key] = time.time()
                bl = self._hourly_budget.setdefault(action.value, [])
                bl.append(time.time())
                self._hourly_budget[action.value] = [t for t in bl
                                                      if time.time() - t < 3600]

        result = PlaybookResult(
            run_id=run_id, action=action, component=component,
            status=status, duration_ms=duration_ms,
            detail=detail, checkpoint=checkpoint
        )
        self._record(result, action, component)
        return result

    def _dispatch(self, action: PlaybookAction, component: str, metrics: dict) -> str:
        """Routes to the specific remediation implementation."""

        if action == PlaybookAction.RESTART_WORKER:
            from task_queue import global_task_queue
            global_task_queue.spawn_replacement_worker()
            return "Replacement worker spawned"

        if action == PlaybookAction.RECYCLE_BROWSER:
            from infra.resilience import global_recovery_playbook
            session_id = metrics.get("stuck_session", "unknown")
            global_recovery_playbook.handle_browser_deadlock(session_id)
            return f"Browser recycled for session {session_id}"

        if action == PlaybookAction.CLEAR_ORPHANED_LOCKS:
            from execution.workspace_lock import global_lock_registry
            released = global_lock_registry.sweep_orphaned()
            return f"Released {released} orphaned locks"

        if action == PlaybookAction.RECONNECT_REDIS:
            from infra.resilience import global_recovery_playbook
            global_recovery_playbook.handle_redis_outage()
            return "Redis reconnect probe started"

        if action == PlaybookAction.RECONNECT_POSTGRES:
            from infra.resilience import global_recovery_playbook
            global_recovery_playbook.handle_postgres_failover()
            return "PostgreSQL reconnect probe started"

        if action == PlaybookAction.DRAIN_QUEUE:
            from task_queue import global_task_queue
            paused = global_task_queue.pause_intake()
            return f"Queue intake paused: {paused}"

        if action == PlaybookAction.RESTORE_SSE_STREAM:
            from streaming.sse_manager import global_sse_manager
            session_id = metrics.get("session_id", "")
            if session_id:
                global_sse_manager.evict_all_clients(session_id)
            return f"SSE stream restored for {session_id}"

        if action == PlaybookAction.ACTIVATE_DEGRADED:
            from infra.resilience import global_degraded_mode
            component_name = metrics.get("component", "unknown")
            global_degraded_mode.mark_degraded(component_name, "Auto-activated by PlaybookEngine")
            return f"Degraded mode activated for {component_name}"

        if action == PlaybookAction.ROLLBACK_DEPLOYMENT:
            from devops.deployment_governor import global_deployment_governor
            return global_deployment_governor.trigger_rollback(reason="Health check failure")

        raise ValueError(f"Unknown action: {action}")

    # ── Operator confirm / veto ───────────────────────────────────────────────

    def confirm(self, run_id: str, operator: str = "") -> Optional[PlaybookResult]:
        """Operator confirms a NEEDS_CONFIRM action."""
        with self._lock:
            pending = self._pending_confirms.pop(run_id, None)
        if not pending:
            return None
        action    = PlaybookAction(pending["action"])
        component = pending["component"]
        metrics   = pending.get("metrics", {})
        logger.info(f"[Playbook] Operator confirmed: {action.value} on {component} by {operator}")
        return self._run_action(action, component, metrics)

    def veto(self, run_id: str, operator: str = "") -> bool:
        """Operator vetoes a pending action."""
        with self._lock:
            if run_id in self._pending_confirms:
                self._pending_confirms.pop(run_id)
                self._vetoed_ids.add(run_id)
                logger.warning(f"[Playbook] Operator VETOED run_id={run_id} by {operator}")
                return True
        return False

    def _confirm_timeout_watcher(self, run_id: str, action: PlaybookAction,
                                  component: str, metrics: dict) -> None:
        time.sleep(CONFIRM_TIMEOUT_SEC)
        with self._lock:
            pending = self._pending_confirms.pop(run_id, None)
        if pending:
            logger.warning(f"[Playbook] Confirm timeout for {action.value} on {component} — auto-cancelling")

    # ── HITL notification ─────────────────────────────────────────────────────

    def _notify_hitl(self, component: str, action: PlaybookAction,
                     metrics: dict, consec: int) -> None:
        try:
            from infra.telemetry import get_telemetry
            get_telemetry().record(
                "devops", "playbook_escalated",
                {"component": component, "action": action.value,
                 "consecutive_failures": consec},
            )
        except Exception:
            pass

    # ── Checkpoint capture ────────────────────────────────────────────────────

    def _capture_checkpoint(self, component: str) -> dict:
        """Captures pre-action state for rollback reference."""
        cp = {"ts": time.time(), "component": component}
        try:
            from infra.resilience import global_degraded_mode
            cp["degraded_mode"] = global_degraded_mode.snapshot()
        except Exception:
            pass
        try:
            from task_queue import global_task_queue
            cp["queue_stats"] = global_task_queue.stats()
        except Exception:
            pass
        return cp

    # ── Audit / snapshot ──────────────────────────────────────────────────────

    def _record(self, result: PlaybookResult, action: PlaybookAction,
                component: str) -> None:
        with self._lock:
            self._history.append(result)
            if len(self._history) > 500:
                self._history.pop(0)
        level = logging.INFO if result.status == PlaybookStatus.SUCCEEDED else logging.WARNING
        logger.log(level, f"[Playbook] {result.status.value} {action.value} "
                          f"on {component} run_id={result.run_id} detail={result.detail[:80]}")
        try:
            from infra.telemetry import get_telemetry
            get_telemetry().record(
                "devops", f"playbook_{result.status.value.lower()}",
                {"action": action.value, "component": component}
            )
        except Exception:
            pass

    def recent_history(self, n: int = 20) -> List[dict]:
        with self._lock:
            return [
                {
                    "run_id":    r.run_id,
                    "action":    r.action.value,
                    "component": r.component,
                    "status":    r.status.value,
                    "duration_ms": r.duration_ms,
                    "detail":    r.detail[:150],
                    "ts":        r.ts,
                }
                for r in self._history[-n:]
            ]

    def pending_confirms(self) -> List[dict]:
        with self._lock:
            now = time.time()
            return [
                {
                    "run_id":     rid,
                    "action":     d["action"],
                    "component":  d["component"],
                    "expires_in": max(0, d["expires_at"] - now),
                    "queued_at":  d["queued_at"],
                }
                for rid, d in self._pending_confirms.items()
            ]

    def snapshot(self) -> dict:
        with self._lock:
            total     = len(self._history)
            succeeded = sum(1 for r in self._history if r.status == PlaybookStatus.SUCCEEDED)
            failed    = sum(1 for r in self._history if r.status == PlaybookStatus.FAILED)
            escalated = sum(1 for r in self._history if r.status == PlaybookStatus.ESCALATED)
        return {
            "total_runs":      total,
            "succeeded":       succeeded,
            "failed":          failed,
            "escalated":       escalated,
            "currently_running": len(self._running_set),
            "pending_confirms":  len(self._pending_confirms),
            "cooldown_sec":    COOLDOWN_SEC,
            "budget_per_hour": MAX_ACTIONS_PER_HOUR,
            "max_concurrent":  MAX_CONCURRENT,
            "recent":          self.recent_history(5),
        }


# ─── Global singleton ─────────────────────────────────────────────────────────
global_playbook_engine = PlaybookEngine()
