"""
execution/self_stabilization.py — Phase Z39F: Operational Self-Stabilization
=============================================================================
The runtime actively stabilises itself against pressure amplification loops,
recursive retry storms, and long-session drift.

Subsystems:
  • PressureStabilizer       — detects and dampens amplification loops
  • RecoveryLoopGuard        — detects recursive retry storms and applies backoff
  • StabilityCoolingEngine   — normalises stabilised branches over time
  • RuntimeCalmMonitor       — ensures long-session operational calmness
"""

import time
import threading
import logging
from collections import defaultdict, deque
from typing import Dict, List, Optional, Callable

logger = logging.getLogger("nexora.self_stabilization")


# ── Pressure stabilizer ────────────────────────────────────────────────────────

class PressureStabilizer:
    """
    Tracks pressure events per execution branch and detects amplification loops.
    Applies exponential dampening when pressure exceeds safe thresholds.
    """

    MAX_PRESSURE_EVENTS = 10   # per window before dampening
    WINDOW_SECS         = 300  # 5-minute rolling window

    def __init__(self):
        self._pressure: Dict[str, deque] = defaultdict(lambda: deque())
        self._dampened: Dict[str, float] = {}   # execution_id → dampen_until ts
        self._lock = threading.Lock()

    def record_pressure(self, execution_id: str, severity: float = 1.0) -> Dict:
        """
        Records a pressure event for an execution.
        Returns stabilization action if dampening is applied.
        """
        now = time.time()
        with self._lock:
            q = self._pressure[execution_id]
            q.append((now, severity))
            # Evict events outside window
            while q and (now - q[0][0]) > self.WINDOW_SECS:
                q.popleft()

            total_pressure = sum(s for _, s in q)
            event_count = len(q)

            if execution_id in self._dampened and now < self._dampened[execution_id]:
                return {
                    "execution_id": execution_id,
                    "action": "DAMPENED",
                    "dampen_until": self._dampened[execution_id],
                    "event_count": event_count,
                    "total_pressure": round(total_pressure, 2),
                }

            if event_count >= self.MAX_PRESSURE_EVENTS:
                # Exponential backoff: 2^(events-MAX) * 10 seconds, max 10 minutes
                backoff = min(10 * (2 ** (event_count - self.MAX_PRESSURE_EVENTS)), 600)
                self._dampened[execution_id] = now + backoff
                logger.warning(
                    "[PressureStabilizer] Amplification loop detected for %s — dampening for %.0fs",
                    execution_id, backoff
                )
                return {
                    "execution_id": execution_id,
                    "action": "DAMPEN_APPLIED",
                    "backoff_seconds": backoff,
                    "event_count": event_count,
                    "total_pressure": round(total_pressure, 2),
                }

        return {
            "execution_id": execution_id,
            "action": "RECORDED",
            "event_count": event_count,
            "total_pressure": round(total_pressure, 2),
        }

    def is_dampened(self, execution_id: str) -> bool:
        return time.time() < self._dampened.get(execution_id, 0)

    def snapshot(self) -> Dict:
        now = time.time()
        with self._lock:
            return {
                "tracked_executions": len(self._pressure),
                "currently_dampened": [eid for eid, until in self._dampened.items() if now < until],
                "dampened_count": sum(1 for until in self._dampened.values() if now < until),
            }


# ── Recovery loop guard ────────────────────────────────────────────────────────

class RecoveryLoopGuard:
    """
    Detects recursive retry storms:
    when the same execution is retried more than MAX_RETRIES times
    within a short window, the guard blocks further retries and emits an alert.
    """

    MAX_RETRIES    = 5
    WINDOW_SECS    = 120   # 2-minute window
    COOLDOWN_SECS  = 300   # 5-minute cooldown after loop detected

    def __init__(self):
        self._retries: Dict[str, deque] = defaultdict(lambda: deque())
        self._blocked: Dict[str, float] = {}
        self._alerts: List[Dict] = []
        self._lock = threading.Lock()

    def record_retry(self, execution_id: str) -> Dict:
        """Returns a block verdict if a retry storm is detected."""
        now = time.time()
        with self._lock:
            # Check if still cooling down
            if execution_id in self._blocked and now < self._blocked[execution_id]:
                return {
                    "execution_id": execution_id,
                    "verdict": "BLOCKED",
                    "reason": "retry_storm_cooldown",
                    "cooldown_remaining_secs": round(self._blocked[execution_id] - now, 1),
                }

            q = self._retries[execution_id]
            q.append(now)
            while q and (now - q[0]) > self.WINDOW_SECS:
                q.popleft()

            if len(q) > self.MAX_RETRIES:
                self._blocked[execution_id] = now + self.COOLDOWN_SECS
                alert = {
                    "ts": now,
                    "execution_id": execution_id,
                    "retry_count": len(q),
                    "window_secs": self.WINDOW_SECS,
                    "cooldown_applied_secs": self.COOLDOWN_SECS,
                }
                self._alerts.append(alert)
                self._alerts = self._alerts[-50:]
                logger.error(
                    "[RecoveryLoopGuard] Retry storm on %s: %d retries in %ds — blocked for %ds",
                    execution_id, len(q), self.WINDOW_SECS, self.COOLDOWN_SECS
                )
                return {"execution_id": execution_id, "verdict": "BLOCKED", "reason": "retry_storm_detected", **alert}

        return {"execution_id": execution_id, "verdict": "ALLOWED", "retry_count": len(q)}

    def is_blocked(self, execution_id: str) -> bool:
        return time.time() < self._blocked.get(execution_id, 0)

    def get_alerts(self, limit: int = 20) -> List[Dict]:
        return self._alerts[-limit:]

    def snapshot(self) -> Dict:
        now = time.time()
        with self._lock:
            return {
                "tracked_executions": len(self._retries),
                "blocked_count": sum(1 for until in self._blocked.values() if now < until),
                "storm_alerts": len(self._alerts),
                "recent_alerts": self._alerts[-5:],
            }


# ── Stability cooling engine ───────────────────────────────────────────────────

class StabilityCoolingEngine:
    """
    Stabilised branches gradually normalise over time.
    Tracks "heat" per branch and cools it linearly every second.
    """

    COOLING_RATE = 1.0 / 60   # lose 1 heat unit per 60 seconds

    def __init__(self):
        self._heat: Dict[str, float] = {}
        self._last_cooled: Dict[str, float] = {}
        self._lock = threading.Lock()

    def add_heat(self, branch_id: str, amount: float = 1.0) -> float:
        """Add heat to a branch (e.g. on error/retry). Returns new heat level."""
        self._apply_cooling(branch_id)
        with self._lock:
            self._heat[branch_id] = self._heat.get(branch_id, 0.0) + amount
            return self._heat[branch_id]

    def _apply_cooling(self, branch_id: str) -> None:
        now = time.time()
        with self._lock:
            last = self._last_cooled.get(branch_id, now)
            elapsed = now - last
            if branch_id in self._heat:
                cool = elapsed * self.COOLING_RATE
                self._heat[branch_id] = max(0.0, self._heat[branch_id] - cool)
            self._last_cooled[branch_id] = now

    def get_heat(self, branch_id: str) -> float:
        self._apply_cooling(branch_id)
        with self._lock:
            return round(self._heat.get(branch_id, 0.0), 3)

    def is_hot(self, branch_id: str, threshold: float = 5.0) -> bool:
        return self.get_heat(branch_id) >= threshold

    def snapshot(self) -> Dict:
        now = time.time()
        hot_branches = []
        with self._lock:
            for bid, heat in self._heat.items():
                if heat > 0.1:
                    hot_branches.append({"branch_id": bid, "heat": round(heat, 3)})
        hot_branches.sort(key=lambda b: b["heat"], reverse=True)
        return {
            "tracked_branches": len(self._heat),
            "hot_branches": hot_branches[:20],
            "hottest": hot_branches[0] if hot_branches else None,
        }


# ── Runtime calm monitor ───────────────────────────────────────────────────────

class RuntimeCalmMonitor:
    """
    Ensures the workspace remains operationally calm across long sessions.
    Produces a composite calmness score (0=chaotic, 100=calm).
    Emits warnings when the platform is showing sustained instability.
    """

    WARN_THRESHOLD = 40.0   # calmness score below this triggers a warning

    def __init__(
        self,
        pressure: PressureStabilizer,
        loop_guard: RecoveryLoopGuard,
        cooling: StabilityCoolingEngine,
    ):
        self.pressure = pressure
        self.loop_guard = loop_guard
        self.cooling = cooling
        self._start_time = time.time()
        self._last_report: Optional[Dict] = None

    def assess(self) -> Dict:
        p_snap  = self.pressure.snapshot()
        lg_snap = self.loop_guard.snapshot()
        c_snap  = self.cooling.snapshot()

        # Calmness scoring
        dampened_penalty = p_snap["dampened_count"] * 8
        blocked_penalty  = lg_snap["blocked_count"] * 12
        heat_penalty     = min(30, c_snap["tracked_branches"])
        alerts_penalty   = min(20, lg_snap["storm_alerts"] * 4)

        calmness = max(0.0, 100.0 - dampened_penalty - blocked_penalty - heat_penalty - alerts_penalty)

        uptime_hours = (time.time() - self._start_time) / 3600
        verdict = "CALM" if calmness >= 70 else ("STRESSED" if calmness >= 40 else "TURBULENT")

        if calmness < self.WARN_THRESHOLD:
            logger.warning(
                "[RuntimeCalm] Calmness=%.1f (%s) — dampened=%d blocked=%d hot_branches=%d",
                calmness, verdict, p_snap["dampened_count"],
                lg_snap["blocked_count"], c_snap["tracked_branches"]
            )

        report = {
            "assessed_at": time.time(),
            "uptime_hours": round(uptime_hours, 2),
            "calmness_score": round(calmness, 1),
            "verdict": verdict,
            "pressure": p_snap,
            "loop_guard": lg_snap,
            "cooling": c_snap,
        }
        self._last_report = report
        return report


# ── Global singletons ──────────────────────────────────────────────────────────

_pressure_stabilizer  = PressureStabilizer()
_loop_guard           = RecoveryLoopGuard()
_cooling_engine       = StabilityCoolingEngine()
_calm_monitor         = RuntimeCalmMonitor(_pressure_stabilizer, _loop_guard, _cooling_engine)


def get_stabilization_snapshot() -> Dict:
    """Convenience function for API routes and the Z39 endpoint."""
    return _calm_monitor.assess()

def record_execution_pressure(execution_id: str, severity: float = 1.0) -> Dict:
    return _pressure_stabilizer.record_pressure(execution_id, severity)

def record_retry(execution_id: str) -> Dict:
    _cooling_engine.add_heat(execution_id, 1.0)
    return _loop_guard.record_retry(execution_id)

def is_execution_blocked(execution_id: str) -> bool:
    return _loop_guard.is_blocked(execution_id) or _pressure_stabilizer.is_dampened(execution_id)
