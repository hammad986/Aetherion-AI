"""
runtime/mission_control.py — Phase Z29A
========================================
Live operator runtime controls for mission execution.

Provides safe pause / resume / cancel / retry / inject / replan signals
with full audit trail and replay-safe event sourcing.

Design rules:
  - Signals are written to a per-session dict; agent.py reads them between steps
  - Every control action generates an immutable OperatorAction record
  - No direct thread kill — only cooperative signal injection
  - All state transitions are recorded and SSE-emitted when emit_fn provided
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("nexora.mission_control")

# ── Signal types ───────────────────────────────────────────────────────────────

class MissionSignal:
    NONE          = "none"
    PAUSE         = "pause"
    RESUME        = "resume"
    CANCEL        = "cancel"
    RETRY_STEP    = "retry_step"
    INJECT        = "inject"
    REPLAN        = "replan"


class MissionState:
    RUNNING   = "running"
    PAUSED    = "paused"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED    = "failed"
    UNKNOWN   = "unknown"


# ── Audit record ──────────────────────────────────────────────────────────────

@dataclass
class OperatorAction:
    action_id:      str
    sid:            str
    action:         str       # MissionSignal value
    operator_note:  str
    prev_state:     str
    next_state:     str
    payload:        dict
    ts:             float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "action_id":    self.action_id,
            "sid":          self.sid,
            "action":       self.action,
            "operator_note": self.operator_note,
            "prev_state":   self.prev_state,
            "next_state":   self.next_state,
            "payload":      self.payload,
            "ts":           self.ts,
        }


# ── In-memory registries ──────────────────────────────────────────────────────

_lock            = threading.Lock()
_signals:        dict[str, str]             = {}   # sid -> MissionSignal
_inject_queue:   dict[str, list[str]]       = {}   # sid -> [messages]
_states:         dict[str, str]             = {}   # sid -> MissionState
_audit_log:      list[OperatorAction]       = []
_MAX_AUDIT       = 4000


# ── Core control API ──────────────────────────────────────────────────────────

def _record(action: OperatorAction) -> None:
    with _lock:
        _audit_log.append(action)
        if len(_audit_log) > _MAX_AUDIT:
            del _audit_log[:_MAX_AUDIT // 4]


def _emit(emit_fn: Callable | None, event: str, payload: dict) -> None:
    if emit_fn:
        try:
            emit_fn(event, payload)
        except Exception as e:
            logger.debug(f"[MissionControl] emit error: {e}")


def _make_action(
    sid: str,
    action: str,
    note: str,
    prev: str,
    nxt: str,
    payload: dict | None = None,
) -> OperatorAction:
    return OperatorAction(
        action_id=f"{sid[:8]}-{action}-{int(time.time()*1000)%1000000}",
        sid=sid,
        action=action,
        operator_note=note,
        prev_state=prev,
        next_state=nxt,
        payload=payload or {},
    )


def get_mission_state(sid: str) -> str:
    with _lock:
        return _states.get(sid, MissionState.UNKNOWN)


def set_mission_state(sid: str, state: str) -> None:
    with _lock:
        _states[sid] = state


def register_mission(sid: str) -> None:
    with _lock:
        if sid not in _states:
            _states[sid] = MissionState.RUNNING
            _signals[sid] = MissionSignal.NONE
            _inject_queue[sid] = []


def complete_mission(sid: str, success: bool = True) -> None:
    with _lock:
        _states[sid] = MissionState.COMPLETED if success else MissionState.FAILED
        _signals[sid] = MissionSignal.NONE


# ── Control operations ────────────────────────────────────────────────────────

def pause_mission(
    sid: str,
    note: str = "Operator paused execution",
    emit_fn: Callable | None = None,
) -> OperatorAction:
    with _lock:
        prev = _states.get(sid, MissionState.UNKNOWN)
        _states[sid]  = MissionState.PAUSED
        _signals[sid] = MissionSignal.PAUSE
    act = _make_action(sid, MissionSignal.PAUSE, note, prev, MissionState.PAUSED)
    _record(act)
    _emit(emit_fn, "agent.mission_control", {
        "action": MissionSignal.PAUSE, "sid": sid,
        "note": note, "prev_state": prev, "action_id": act.action_id,
    })
    logger.info(f"[MissionControl] PAUSE {sid}: {note}")
    return act


def resume_mission(
    sid: str,
    note: str = "Operator resumed execution",
    emit_fn: Callable | None = None,
) -> OperatorAction:
    with _lock:
        prev = _states.get(sid, MissionState.UNKNOWN)
        _states[sid]  = MissionState.RUNNING
        _signals[sid] = MissionSignal.RESUME
    act = _make_action(sid, MissionSignal.RESUME, note, prev, MissionState.RUNNING)
    _record(act)
    _emit(emit_fn, "agent.mission_control", {
        "action": MissionSignal.RESUME, "sid": sid,
        "note": note, "prev_state": prev, "action_id": act.action_id,
    })
    # Also release any HITL pause via existing infrastructure
    try:
        from execution.hitl import resume_execution
        resume_execution(sid)
    except Exception:
        pass
    logger.info(f"[MissionControl] RESUME {sid}: {note}")
    return act


def cancel_mission(
    sid: str,
    note: str = "Operator cancelled execution",
    emit_fn: Callable | None = None,
) -> OperatorAction:
    with _lock:
        prev = _states.get(sid, MissionState.UNKNOWN)
        _states[sid]  = MissionState.CANCELLED
        _signals[sid] = MissionSignal.CANCEL
    act = _make_action(sid, MissionSignal.CANCEL, note, prev, MissionState.CANCELLED)
    _record(act)
    _emit(emit_fn, "agent.mission_control", {
        "action": MissionSignal.CANCEL, "sid": sid,
        "note": note, "prev_state": prev, "action_id": act.action_id,
    })
    logger.info(f"[MissionControl] CANCEL {sid}: {note}")
    return act


def retry_step(
    sid: str,
    note: str = "Operator triggered retry",
    emit_fn: Callable | None = None,
) -> OperatorAction:
    with _lock:
        prev = _states.get(sid, MissionState.UNKNOWN)
        _signals[sid] = MissionSignal.RETRY_STEP
        _states[sid]  = MissionState.RUNNING
    act = _make_action(sid, MissionSignal.RETRY_STEP, note, prev, MissionState.RUNNING)
    _record(act)
    _emit(emit_fn, "agent.mission_control", {
        "action": MissionSignal.RETRY_STEP, "sid": sid,
        "note": note, "prev_state": prev, "action_id": act.action_id,
    })
    logger.info(f"[MissionControl] RETRY_STEP {sid}: {note}")
    return act


def inject_instruction(
    sid: str,
    instruction: str,
    note: str = "Operator injected runtime instruction",
    emit_fn: Callable | None = None,
) -> OperatorAction:
    with _lock:
        prev = _states.get(sid, MissionState.UNKNOWN)
        if sid not in _inject_queue:
            _inject_queue[sid] = []
        _inject_queue[sid].append(instruction)
        _signals[sid] = MissionSignal.INJECT
    act = _make_action(
        sid, MissionSignal.INJECT, note, prev, prev,
        payload={"instruction": instruction[:500]},
    )
    _record(act)
    _emit(emit_fn, "agent.mission_control", {
        "action": MissionSignal.INJECT, "sid": sid,
        "note": note, "instruction": instruction[:200], "action_id": act.action_id,
    })
    # Also push to HITL inject queue
    try:
        from execution.hitl import hitl_inject
        hitl_inject(sid, instruction)
    except Exception:
        pass
    logger.info(f"[MissionControl] INJECT {sid}: {instruction[:80]}")
    return act


def request_replan(
    sid: str,
    note: str = "Operator requested replanning",
    emit_fn: Callable | None = None,
) -> OperatorAction:
    with _lock:
        prev = _states.get(sid, MissionState.UNKNOWN)
        _signals[sid] = MissionSignal.REPLAN
        _states[sid]  = MissionState.RUNNING
    act = _make_action(sid, MissionSignal.REPLAN, note, prev, MissionState.RUNNING)
    _record(act)
    _emit(emit_fn, "agent.mission_control", {
        "action": MissionSignal.REPLAN, "sid": sid,
        "note": note, "prev_state": prev, "action_id": act.action_id,
    })
    logger.info(f"[MissionControl] REPLAN {sid}: {note}")
    return act


# ── Agent polling helpers (called from agent.py between steps) ────────────────

def check_signal(sid: str) -> str:
    """Read and clear the pending control signal for this session."""
    with _lock:
        sig = _signals.get(sid, MissionSignal.NONE)
        if sig not in (MissionSignal.PAUSE, MissionSignal.CANCEL):
            _signals[sid] = MissionSignal.NONE
        return sig


def drain_inject_queue(sid: str) -> list[str]:
    """Drain and return all queued injected instructions."""
    with _lock:
        msgs = list(_inject_queue.get(sid, []))
        if sid in _inject_queue:
            _inject_queue[sid] = []
        if msgs and _signals.get(sid) == MissionSignal.INJECT:
            _signals[sid] = MissionSignal.NONE
        return msgs


def is_paused(sid: str) -> bool:
    with _lock:
        return _states.get(sid) == MissionState.PAUSED


def is_cancelled(sid: str) -> bool:
    with _lock:
        return _states.get(sid) == MissionState.CANCELLED


def wait_if_paused(sid: str, poll_interval: float = 0.5, timeout: float = 300.0) -> bool:
    """
    Block until the mission is resumed or timeout expires.
    Returns True if resumed, False if timed out or cancelled.
    Called from agent.py when pause signal is detected.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _lock:
            state = _states.get(sid, MissionState.UNKNOWN)
        if state == MissionState.RUNNING:
            return True
        if state in (MissionState.CANCELLED, MissionState.FAILED):
            return False
        time.sleep(poll_interval)
    return False


# ── Query API ─────────────────────────────────────────────────────────────────

def get_operator_actions(
    sid: str | None = None,
    limit: int = 50,
) -> list[dict]:
    with _lock:
        records = list(_audit_log)
    if sid:
        records = [r for r in records if r.sid == sid]
    return [r.to_dict() for r in records[-limit:]]


def list_missions() -> list[dict]:
    with _lock:
        return [
            {"sid": s, "state": st, "pending_signal": _signals.get(s, MissionSignal.NONE)}
            for s, st in _states.items()
        ]


def mission_snapshot(sid: str) -> dict:
    with _lock:
        return {
            "sid":            sid,
            "state":          _states.get(sid, MissionState.UNKNOWN),
            "pending_signal": _signals.get(sid, MissionSignal.NONE),
            "inject_queue":   list(_inject_queue.get(sid, [])),
            "ts":             time.time(),
        }
