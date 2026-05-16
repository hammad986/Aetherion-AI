"""
runtime/scheduler.py — Phase Z26 Lightweight Temporal Runtime Scheduler
========================================================================
Safe beta-grade scheduling for delayed execution, timeouts, recurring
missions, and deadline tracking.

DOES NOT replace or interact with the heavy task queue in task_queue.py.
This is a lightweight overlay for temporal metadata and deadline enforcement.

FUTURE_RUNTIME_NOTE: A production-grade distributed scheduler (e.g. Celery,
APScheduler with Redis backend, or a dedicated worker service) must be added
before supporting multi-user concurrent scheduling. This module is
intentionally single-process and in-memory.

FUTURE_RUNTIME_NOTE: Do not store schedule state in this module's in-memory
dict for anything that must survive a server restart. Add a persistence
layer (SQLite or Redis) before marking scheduling as production-ready.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger("nexora.scheduler")


# ── Enums ─────────────────────────────────────────────────────────────────────

class ScheduleStatus(Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    EXPIRED   = "expired"
    CANCELLED = "cancelled"
    TIMEOUT   = "timeout"


class RecurrenceType(Enum):
    NONE     = "none"
    INTERVAL = "interval"   # every N seconds
    DAILY    = "daily"      # once per day at a fixed offset
    COUNT    = "count"      # repeat N times


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ScheduledMission:
    mission_id: str
    sid: str
    task_description: str
    run_at: float                              # unix timestamp
    timeout_secs: float = 300.0               # 5 min default
    deadline: float | None = None             # hard expiry timestamp
    recurrence: RecurrenceType = RecurrenceType.NONE
    recurrence_interval_secs: float = 0.0
    recurrence_count_max: int = 0
    recurrence_count_done: int = 0
    status: ScheduleStatus = ScheduleStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    last_alert_sent: float | None = None
    metadata: dict = field(default_factory=dict)

    def is_expired(self) -> bool:
        if self.deadline and time.time() > self.deadline:
            return True
        return False

    def is_due(self) -> bool:
        return (
            self.status == ScheduleStatus.PENDING
            and time.time() >= self.run_at
            and not self.is_expired()
        )

    def time_until_due(self) -> float:
        return max(0.0, self.run_at - time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id":         self.mission_id,
            "sid":                self.sid,
            "task":               self.task_description,
            "status":             self.status.value,
            "run_at":             self.run_at,
            "run_at_iso":         _ts_iso(self.run_at),
            "deadline":           self.deadline,
            "deadline_iso":       _ts_iso(self.deadline) if self.deadline else None,
            "timeout_secs":       self.timeout_secs,
            "recurrence":         self.recurrence.value,
            "recurrence_interval": self.recurrence_interval_secs,
            "recurrence_count_max": self.recurrence_count_max,
            "recurrence_count_done": self.recurrence_count_done,
            "time_until_due_secs": self.time_until_due(),
            "is_expired":         self.is_expired(),
            "created_at":         self.created_at,
            "started_at":         self.started_at,
            "completed_at":       self.completed_at,
            "metadata":           self.metadata,
        }


def _ts_iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    import datetime
    return datetime.datetime.utcfromtimestamp(ts).isoformat() + "Z"


# ── Registry ──────────────────────────────────────────────────────────────────

_missions: dict[str, ScheduledMission] = {}
_lock = threading.Lock()
_id_counter = 0
_id_lock = threading.Lock()


def _new_id() -> str:
    global _id_counter
    with _id_lock:
        _id_counter += 1
        return f"sched-{int(time.time())}-{_id_counter}"


# ── Public API ────────────────────────────────────────────────────────────────

def schedule_mission(
    sid: str,
    task_description: str,
    delay_secs: float = 0.0,
    run_at: float | None = None,
    timeout_secs: float = 300.0,
    deadline_secs: float | None = None,
    recurrence: str = "none",
    recurrence_interval_secs: float = 0.0,
    recurrence_count_max: int = 0,
    metadata: dict | None = None,
) -> ScheduledMission:
    """
    Schedule a mission for future execution.

    Parameters
    ----------
    sid                       : session id
    task_description          : what the agent should do
    delay_secs                : seconds from now (ignored if run_at set)
    run_at                    : explicit unix timestamp to run at
    timeout_secs              : how long the mission may run
    deadline_secs             : seconds from now after which mission expires
    recurrence                : "none" | "interval" | "count"
    recurrence_interval_secs  : interval for recurring missions
    recurrence_count_max      : max recurrences (0 = unlimited for interval)
    metadata                  : arbitrary tracking metadata
    """
    mid = _new_id()
    fire_at = run_at if run_at else time.time() + delay_secs
    deadline = (time.time() + deadline_secs) if deadline_secs else None

    try:
        rec_type = RecurrenceType(recurrence)
    except ValueError:
        rec_type = RecurrenceType.NONE

    m = ScheduledMission(
        mission_id=mid,
        sid=sid,
        task_description=task_description,
        run_at=fire_at,
        timeout_secs=timeout_secs,
        deadline=deadline,
        recurrence=rec_type,
        recurrence_interval_secs=recurrence_interval_secs,
        recurrence_count_max=recurrence_count_max,
        metadata=metadata or {},
    )

    with _lock:
        _missions[mid] = m

    logger.info(
        "[Scheduler] Scheduled mission %s for sid=%s in %.1fs | recurrence=%s",
        mid, sid[:12], m.time_until_due(), recurrence,
    )
    return m


def cancel_mission(mission_id: str) -> bool:
    with _lock:
        m = _missions.get(mission_id)
        if m and m.status == ScheduleStatus.PENDING:
            m.status = ScheduleStatus.CANCELLED
            logger.info("[Scheduler] Cancelled mission %s", mission_id)
            return True
    return False


def get_mission(mission_id: str) -> ScheduledMission | None:
    with _lock:
        return _missions.get(mission_id)


def list_missions(
    sid: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    with _lock:
        results = list(_missions.values())

    if sid:
        results = [m for m in results if m.sid == sid]
    if status:
        results = [m for m in results if m.status.value == status]

    results.sort(key=lambda m: m.run_at)
    return [m.to_dict() for m in results[-limit:]]


def get_due_missions() -> list[ScheduledMission]:
    """Return all missions that are due and not yet started."""
    with _lock:
        due = [m for m in _missions.values() if m.is_due()]
    return due


def mark_started(mission_id: str) -> bool:
    with _lock:
        m = _missions.get(mission_id)
        if m and m.status == ScheduleStatus.PENDING:
            m.status = ScheduleStatus.RUNNING
            m.started_at = time.time()
            return True
    return False


def mark_completed(mission_id: str, success: bool = True) -> bool:
    with _lock:
        m = _missions.get(mission_id)
        if not m:
            return False
        m.completed_at = time.time()
        m.status = ScheduleStatus.COMPLETED if success else ScheduleStatus.TIMEOUT

        if success and m.recurrence != RecurrenceType.NONE:
            _reschedule(m)

    return True


def _reschedule(m: ScheduledMission):
    """Create next recurrence. Called inside lock."""
    if m.recurrence == RecurrenceType.INTERVAL and m.recurrence_interval_secs > 0:
        if m.recurrence_count_max == 0 or m.recurrence_count_done < m.recurrence_count_max - 1:
            mid = _new_id()
            new_m = ScheduledMission(
                mission_id=mid,
                sid=m.sid,
                task_description=m.task_description,
                run_at=time.time() + m.recurrence_interval_secs,
                timeout_secs=m.timeout_secs,
                deadline=m.deadline,
                recurrence=m.recurrence,
                recurrence_interval_secs=m.recurrence_interval_secs,
                recurrence_count_max=m.recurrence_count_max,
                recurrence_count_done=m.recurrence_count_done + 1,
                metadata=dict(m.metadata),
            )
            _missions[mid] = new_m
            logger.info(
                "[Scheduler] Rescheduled %s → %s (run #%d)",
                m.mission_id, mid, new_m.recurrence_count_done + 1,
            )


# ── Deadline + timeout alert system ──────────────────────────────────────────

_alert_callbacks: list[Callable[[str, ScheduledMission], None]] = []


def register_alert_callback(fn: Callable[[str, ScheduledMission], None]):
    """Register a callable(alert_type, mission) for deadline/timeout alerts."""
    _alert_callbacks.append(fn)


def _fire_alert(alert_type: str, mission: ScheduledMission):
    for fn in _alert_callbacks:
        try:
            fn(alert_type, mission)
        except Exception as exc:
            logger.warning("[Scheduler] Alert callback error: %s", exc)


def check_deadlines_and_timeouts():
    """
    Must be called periodically (e.g. by a background thread or request hook).
    Marks expired/timed-out missions and fires alerts.
    """
    now = time.time()
    alerts = []

    with _lock:
        for m in list(_missions.values()):
            if m.status == ScheduleStatus.PENDING and m.is_expired():
                m.status = ScheduleStatus.EXPIRED
                alerts.append(("deadline_expired", m))

            elif m.status == ScheduleStatus.RUNNING:
                if m.started_at and (now - m.started_at) > m.timeout_secs:
                    m.status = ScheduleStatus.TIMEOUT
                    alerts.append(("execution_timeout", m))

            elif m.status == ScheduleStatus.PENDING:
                if m.deadline:
                    remaining = m.deadline - now
                    alert_threshold = min(300.0, m.timeout_secs * 0.2)
                    if remaining < alert_threshold and (
                        m.last_alert_sent is None or now - m.last_alert_sent > 60
                    ):
                        m.last_alert_sent = now
                        alerts.append(("deadline_approaching", m))

    for alert_type, mission in alerts:
        logger.warning("[Scheduler] Alert: %s | mission=%s", alert_type, mission.mission_id)
        _fire_alert(alert_type, mission)


# ── Background checker ────────────────────────────────────────────────────────

_checker_thread: threading.Thread | None = None
_checker_stop = threading.Event()


def start_background_checker(interval_secs: float = 10.0):
    """Start a daemon thread that calls check_deadlines_and_timeouts periodically."""
    global _checker_thread
    if _checker_thread and _checker_thread.is_alive():
        return

    _checker_stop.clear()

    def _loop():
        while not _checker_stop.wait(timeout=interval_secs):
            try:
                check_deadlines_and_timeouts()
            except Exception as exc:
                logger.error("[Scheduler] Checker error: %s", exc)

    _checker_thread = threading.Thread(target=_loop, daemon=True, name="nx-scheduler-checker")
    _checker_thread.start()
    logger.info("[Scheduler] Background checker started (interval=%.0fs)", interval_secs)


def stop_background_checker():
    _checker_stop.set()


# ── Telemetry snapshot ────────────────────────────────────────────────────────

def scheduler_telemetry() -> dict[str, Any]:
    with _lock:
        all_missions = list(_missions.values())

    by_status: dict[str, int] = {}
    for m in all_missions:
        by_status[m.status.value] = by_status.get(m.status.value, 0) + 1

    return {
        "total_missions":  len(all_missions),
        "by_status":       by_status,
        "pending_due_now": sum(1 for m in all_missions if m.is_due()),
        "snapshot_ts":     time.time(),
    }
