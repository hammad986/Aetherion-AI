"""
execution/predictive_scheduling.py — Phase Z41B: Predictive Execution Scheduling
==================================================================================
Moves from reactive runtime behavior to predictive runtime pacing.

Subsystems:
  • PressureForecaster       — forecasts likely spikes before they occur
  • ExecutionQueuePrioritizer — prioritises critical/unstable chains
  • PredictiveCooler         — pre-cools surfaces ahead of instability
"""

import time
import math
import threading
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("nexora.predictive_scheduling")


# ── Pressure history entry ─────────────────────────────────────────────────────

@dataclass
class PressureSample:
    ts:          float
    chaos_index: float
    resource_risk: float
    retry_rate:  float
    drift_score: float


# ── Pressure forecaster ────────────────────────────────────────────────────────

class PressureForecaster:
    """
    Uses a short rolling window of pressure samples to forecast future spikes.
    Simple linear trend extrapolation — no ML, no external dependencies.
    """

    WINDOW_SIZE = 20  # samples
    HORIZON_SECS = 300  # forecast 5 minutes ahead

    def __init__(self):
        self._samples: deque = deque(maxlen=self.WINDOW_SIZE)
        self._lock = threading.Lock()

    def record(
        self,
        chaos_index: float,
        resource_risk: float,
        retry_rate: float,
        drift_score: float,
    ) -> None:
        with self._lock:
            self._samples.append(PressureSample(
                ts=time.time(),
                chaos_index=chaos_index,
                resource_risk=resource_risk,
                retry_rate=retry_rate,
                drift_score=drift_score,
            ))

    def forecast(self) -> Dict:
        with self._lock:
            samples = list(self._samples)

        if len(samples) < 3:
            return {
                "forecast_available": False,
                "reason": "insufficient_samples",
                "samples_collected": len(samples),
            }

        def trend(values: List[float]) -> float:
            """Simple linear slope of last N values (positive = increasing)."""
            n = len(values)
            if n < 2:
                return 0.0
            xs = list(range(n))
            x_mean = sum(xs) / n
            y_mean = sum(values) / n
            num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
            den = sum((x - x_mean) ** 2 for x in xs)
            return num / den if den != 0 else 0.0

        chaos_vals    = [s.chaos_index / 100.0 for s in samples]
        resource_vals = [s.resource_risk for s in samples]
        retry_vals    = [s.retry_rate for s in samples]
        drift_vals    = [s.drift_score for s in samples]

        chaos_trend    = trend(chaos_vals)
        resource_trend = trend(resource_vals)
        retry_trend    = trend(retry_vals)
        drift_trend    = trend(drift_vals)

        # Current values
        current_chaos    = chaos_vals[-1]
        current_resource = resource_vals[-1]

        # Extrapolate: projected = current + trend * horizon_factor
        horizon_factor = 5.0  # ~5 samples into the future
        proj_chaos     = max(0.0, min(1.0, current_chaos    + chaos_trend    * horizon_factor))
        proj_resource  = max(0.0, min(1.0, current_resource + resource_trend * horizon_factor))
        proj_retry     = max(0.0, min(1.0, retry_vals[-1]   + retry_trend    * horizon_factor))
        proj_drift     = max(0.0, min(1.0, drift_vals[-1]   + drift_trend    * horizon_factor))

        # Spike risk: weighted combination of rising projections
        spike_risk = (
            proj_chaos    * 0.35 +
            proj_resource * 0.30 +
            proj_retry    * 0.20 +
            proj_drift    * 0.15
        )

        return {
            "forecast_available": True,
            "horizon_secs": self.HORIZON_SECS,
            "samples_collected": len(samples),
            "current": {
                "chaos_index":    round(current_chaos, 4),
                "resource_risk":  round(current_resource, 4),
                "retry_rate":     round(retry_vals[-1], 4),
                "drift_score":    round(drift_vals[-1], 4),
            },
            "trends": {
                "chaos_trend":    round(chaos_trend, 6),
                "resource_trend": round(resource_trend, 6),
                "retry_trend":    round(retry_trend, 6),
                "drift_trend":    round(drift_trend, 6),
            },
            "projected": {
                "chaos_index":   round(proj_chaos, 4),
                "resource_risk": round(proj_resource, 4),
                "retry_rate":    round(proj_retry, 4),
                "drift_score":   round(proj_drift, 4),
            },
            "spike_risk":        round(spike_risk, 4),
            "spike_risk_label": (
                "CRITICAL" if spike_risk >= 0.80 else
                "HIGH"     if spike_risk >= 0.60 else
                "MODERATE" if spike_risk >= 0.40 else
                "LOW"
            ),
        }


# ── Execution priority entry ───────────────────────────────────────────────────

@dataclass
class QueueEntry:
    execution_id: str
    priority:     str    # CRITICAL / HIGH / NORMAL / BACKGROUND
    score:        float  # 0.0–1.0 higher = run first
    reason:       str
    enqueued_at:  float = field(default_factory=time.time)


# ── Execution queue prioritizer ────────────────────────────────────────────────

class ExecutionQueuePrioritizer:
    """
    Maintains a priority-sorted execution queue.
    Priority is a function of instability, replay importance, entropy, and mission flags.
    """

    PRIORITY_SCORES = {
        "CRITICAL":   1.00,
        "HIGH":       0.70,
        "NORMAL":     0.40,
        "BACKGROUND": 0.10,
    }

    def __init__(self):
        self._queue: List[QueueEntry] = []
        self._lock = threading.Lock()

    def enqueue(
        self,
        execution_id: str,
        priority: str = "NORMAL",
        entropy_boost: float = 0.0,
        reason: str = "",
    ) -> QueueEntry:
        base_score = self.PRIORITY_SCORES.get(priority, 0.40)
        score = min(1.0, base_score + entropy_boost * 0.20)
        entry = QueueEntry(
            execution_id=execution_id,
            priority=priority,
            score=score,
            reason=reason or f"auto-{priority.lower()}",
        )
        with self._lock:
            self._queue.append(entry)
            self._queue.sort(key=lambda e: e.score, reverse=True)
            # Cap queue at 500 entries
            if len(self._queue) > 500:
                self._queue = self._queue[:500]
        return entry

    def dequeue_next(self) -> Optional[QueueEntry]:
        with self._lock:
            return self._queue.pop(0) if self._queue else None

    def peek(self, limit: int = 10) -> List[Dict]:
        with self._lock:
            return [
                {
                    "execution_id": e.execution_id,
                    "priority":     e.priority,
                    "score":        round(e.score, 4),
                    "reason":       e.reason,
                    "enqueued_at":  e.enqueued_at,
                }
                for e in self._queue[:limit]
            ]

    def queue_length(self) -> int:
        with self._lock:
            return len(self._queue)


# ── Predictive cooler ─────────────────────────────────────────────────────────

class PredictiveCooler:
    """
    Pre-cools runtime surfaces when spike_risk is elevated,
    before actual instability occurs.
    """

    PRECOOL_THRESHOLD = 0.50

    def compute_precool_directive(self, forecast: Dict) -> Dict:
        if not forecast.get("forecast_available", False):
            return {"precool": False, "reason": "no_forecast"}

        spike_risk = forecast.get("spike_risk", 0.0)

        if spike_risk < self.PRECOOL_THRESHOLD:
            return {
                "precool":    False,
                "spike_risk": spike_risk,
                "reason":     "spike_risk_below_threshold",
            }

        # Determine how much to pre-cool
        precool_strength = min(1.0, (spike_risk - self.PRECOOL_THRESHOLD) / 0.50)

        return {
            "precool":           True,
            "spike_risk":        spike_risk,
            "precool_strength":  round(precool_strength, 4),
            "recommended_actions": [
                "reduce_replay_hydration" if forecast["projected"].get("resource_risk", 0) > 0.50 else None,
                "throttle_compression_passes" if forecast["projected"].get("chaos_index", 0) > 0.60 else None,
                "suppress_background_tasks" if spike_risk > 0.70 else None,
                "activate_stabilization_mode" if spike_risk > 0.80 else None,
            ],
            "reason": f"spike_risk_{forecast['spike_risk_label']}",
        }


# ── Predictive scheduling manager ─────────────────────────────────────────────

class PredictiveSchedulingManager:
    """Top-level facade for Z41B."""

    def __init__(self):
        self.forecaster = PressureForecaster()
        self.queue      = ExecutionQueuePrioritizer()
        self.cooler     = PredictiveCooler()

    def record_sample(
        self,
        chaos_index: float,
        resource_risk: float,
        retry_rate: float = 0.0,
        drift_score: float = 0.0,
    ) -> None:
        self.forecaster.record(chaos_index, resource_risk, retry_rate, drift_score)

    def report(self) -> Dict:
        fc = self.forecaster.forecast()
        pd = self.cooler.compute_precool_directive(fc)
        return {
            "reported_at": time.time(),
            "forecast":    fc,
            "precool":     pd,
            "queue": {
                "length": self.queue.queue_length(),
                "top_10": self.queue.peek(10),
            },
        }


# Global singleton
_scheduling_manager = PredictiveSchedulingManager()

def get_scheduling_manager() -> PredictiveSchedulingManager:
    return _scheduling_manager
