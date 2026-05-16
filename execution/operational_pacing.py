"""
execution/operational_pacing.py — Phase Z41F: Operational Pacing + Synchronization
====================================================================================
Prevents runtime systems from competing chaotically through synchronization windows,
runtime pacing, and subsystem negotiation.

Subsystems:
  • SynchronizationWindow  — coordinates timing windows for costly operations
  • RuntimePacer           — enforces minimum spacing between adaptive sweeps
  • SubsystemNegotiator    — mediates cooperative scheduling between subsystems
"""

import time
import threading
import logging
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("nexora.operational_pacing")


# ── Synchronization window ─────────────────────────────────────────────────────

SYNC_INTERVALS: Dict[str, float] = {
    "replay_hydration":       30.0,
    "compression_pass":       120.0,
    "stabilization_sweep":    15.0,
    "governance_maintenance": 3600.0,
    "entropy_resample":       10.0,
    "priority_rebalance":     20.0,
    "coordination_report":    5.0,
}


class SynchronizationWindow:
    """
    Tracks last execution time per operation and enforces minimum intervals.
    Gate operations through `can_run()` before executing.
    """

    def __init__(self):
        self._last_run: Dict[str, float] = {}
        self._lock = threading.Lock()

    def can_run(self, operation: str, force: bool = False) -> Tuple[bool, float]:
        if force:
            return True, 0.0
        interval = SYNC_INTERVALS.get(operation, 60.0)
        now = time.time()
        with self._lock:
            last = self._last_run.get(operation, 0.0)
        elapsed = now - last
        wait = max(0.0, interval - elapsed)
        return (elapsed >= interval, wait)

    def mark_ran(self, operation: str) -> None:
        with self._lock:
            self._last_run[operation] = time.time()

    def snapshot(self) -> Dict:
        now = time.time()
        with self._lock:
            intervals = []
            for op, interval in SYNC_INTERVALS.items():
                last    = self._last_run.get(op, 0.0)
                elapsed = now - last
                wait    = max(0.0, interval - elapsed)
                intervals.append({
                    "operation": op,
                    "interval":  interval,
                    "last_ran":  last,
                    "elapsed":   round(elapsed, 1),
                    "ready":     elapsed >= interval,
                    "wait_secs": round(wait, 1),
                })
        return {"operations": intervals}


# ── Runtime pacer ──────────────────────────────────────────────────────────────

class RuntimePacer:
    """
    Enforces a global minimum pace between adaptive sweeps to prevent storm behavior.
    """

    def __init__(self, min_interval_secs: float = 2.0):
        self._min_interval = min_interval_secs
        self._last_tick    = 0.0
        self._tick_count   = 0
        self._storm_count  = 0
        self._lock = threading.Lock()

    def tick(self) -> Dict:
        now = time.time()
        with self._lock:
            elapsed  = now - self._last_tick
            in_pace  = elapsed >= self._min_interval
            self._tick_count += 1
            if not in_pace:
                self._storm_count += 1
            self._last_tick = now

        if not in_pace:
            logger.debug(
                "[RuntimePacer] Rapid tick (elapsed=%.2fs min=%.2fs storm_count=%d)",
                elapsed, self._min_interval, self._storm_count,
            )
        return {
            "in_pace":      in_pace,
            "elapsed_secs": round(elapsed, 3),
            "tick_count":   self._tick_count,
            "storm_count":  self._storm_count,
        }

    def is_storming(self, window: int = 10) -> bool:
        with self._lock:
            return self._storm_count >= window * 0.30

    def snapshot(self) -> Dict:
        with self._lock:
            storming = self._storm_count >= 10 * 0.30
            return {
                "tick_count":   self._tick_count,
                "storm_count":  self._storm_count,
                "storm_rate":   round(self._storm_count / max(self._tick_count, 1), 4),
                "min_interval": self._min_interval,
                "last_tick":    self._last_tick,
                "storming":     storming,
            }


# ── Subsystem negotiator ───────────────────────────────────────────────────────

class SubsystemNegotiator:
    """
    Mediates cooperative scheduling: subsystems declare their intent,
    the negotiator assigns safe execution slots (max MAX_CONCURRENT simultaneously).
    """

    MAX_CONCURRENT = 3

    def __init__(self):
        self._active:   Set[str]  = set()
        self._waitlist: List[str] = []
        self._log:      List[Dict] = []
        self._lock = threading.Lock()

    def request_slot(self, subsystem: str) -> Dict:
        with self._lock:
            if len(self._active) < self.MAX_CONCURRENT:
                self._active.add(subsystem)
                granted  = True
                queue_pos = 0
            else:
                if subsystem not in self._waitlist:
                    self._waitlist.append(subsystem)
                granted   = False
                queue_pos = self._waitlist.index(subsystem) + 1

        self._log.append({"ts": time.time(), "subsystem": subsystem, "granted": granted})
        self._log = self._log[-100:]
        return {"subsystem": subsystem, "granted": granted, "queue_pos": queue_pos}

    def release_slot(self, subsystem: str) -> None:
        with self._lock:
            self._active.discard(subsystem)
            if self._waitlist:
                next_ss = self._waitlist.pop(0)
                self._active.add(next_ss)

    def snapshot(self) -> Dict:
        with self._lock:
            return {
                "active":       list(self._active),
                "waitlist":     list(self._waitlist),
                "active_count": len(self._active),
                "recent_log":   self._log[-5:],
            }


# ── Operational pacing manager ────────────────────────────────────────────────

class OperationalPacingManager:
    """Top-level facade for Z41F."""

    def __init__(self):
        self.sync_window = SynchronizationWindow()
        self.pacer       = RuntimePacer()
        self.negotiator  = SubsystemNegotiator()

    def can_run(self, operation: str, force: bool = False) -> Dict:
        allowed, wait = self.sync_window.can_run(operation, force)
        if allowed:
            self.sync_window.mark_ran(operation)
        return {"operation": operation, "allowed": allowed, "wait_secs": round(wait, 1)}

    def tick(self) -> Dict:
        return self.pacer.tick()

    def report(self) -> Dict:
        return {
            "reported_at": time.time(),
            "sync_window": self.sync_window.snapshot(),
            "pacer":       self.pacer.snapshot(),
            "negotiator":  self.negotiator.snapshot(),
        }


# Global singleton
_pacing_manager = OperationalPacingManager()

def get_pacing_manager() -> OperationalPacingManager:
    return _pacing_manager
