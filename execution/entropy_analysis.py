"""
execution/entropy_analysis.py — Phase Z39D: Execution Entropy Analysis
=======================================================================
Measures runtime chaos accumulation and tracks system stability.

Subsystems:
  • EntropyMetrics      — tracks retry density, escalations, branch divergence
  • ChaosIndexEngine    — produces a global 0–100 Runtime Chaos Index
  • EntropyMonitor      — lightweight continuous monitor, no dashboards
"""

import sqlite3
import time
import logging
from typing import Dict, List, Optional
from execution.store import ExecutionStore

logger = logging.getLogger("nexora.entropy_analysis")


# ── Entropy metrics ────────────────────────────────────────────────────────────

class EntropyMetrics:
    """
    Computes individual entropy dimensions from the execution store.
    All queries are read-only. Results represent a rolling window.
    """

    def __init__(self, store: ExecutionStore, window_secs: int = 3600):
        self.store = store
        self.window_secs = window_secs

    def _since(self) -> float:
        return time.time() - self.window_secs

    def retry_density(self) -> float:
        """Fraction of events that are retry-type relative to total events in window."""
        since = self._since()
        with sqlite3.connect(self.store.db_path) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE timestamp > ?", (since,)
            ).fetchone()[0]
            retries = conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE timestamp > ? AND event_type LIKE '%retry%'",
                (since,)
            ).fetchone()[0]
        return round(retries / max(total, 1), 4)

    def escalation_frequency(self) -> float:
        """Rate of failure events per hour in the window."""
        since = self._since()
        with sqlite3.connect(self.store.db_path) as conn:
            failures = conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE timestamp > ? AND event_type LIKE '%fail%'",
                (since,)
            ).fetchone()[0]
        hours = self.window_secs / 3600.0
        return round(failures / max(hours, 0.01), 4)

    def branch_divergence(self) -> int:
        """
        Count of executions with multiple terminal events (replay branch splits).
        """
        since = self._since()
        with sqlite3.connect(self.store.db_path) as conn:
            rows = conn.execute(
                """SELECT execution_id, COUNT(*) as c
                   FROM event_log
                   WHERE timestamp > ?
                     AND event_type IN ('task.completed','task.failed','task.cancelled')
                   GROUP BY execution_id
                   HAVING c > 1""",
                (since,)
            ).fetchall()
        return len(rows)

    def instability_spread(self) -> float:
        """Fraction of executions that failed or are still running (stuck) in window."""
        since = self._since()
        with sqlite3.connect(self.store.db_path) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM executions WHERE updated_at > ?", (since,)
            ).fetchone()[0]
            unstable = conn.execute(
                "SELECT COUNT(*) FROM executions WHERE updated_at > ? AND status IN ('failed','running')",
                (since,)
            ).fetchone()[0]
        return round(unstable / max(total, 1), 4)

    def replay_fragmentation(self) -> float:
        """
        Fraction of executions where event_count differs significantly from expected.
        An execution with < 2 events is considered fragmented.
        """
        since = self._since()
        with sqlite3.connect(self.store.db_path) as conn:
            total = conn.execute(
                "SELECT COUNT(DISTINCT execution_id) FROM event_log WHERE timestamp > ?", (since,)
            ).fetchone()[0]
            fragmented = conn.execute(
                """SELECT COUNT(*) FROM (
                       SELECT execution_id
                       FROM event_log
                       WHERE timestamp > ?
                       GROUP BY execution_id
                       HAVING COUNT(*) < 2
                   )""",
                (since,)
            ).fetchone()[0]
        return round(fragmented / max(total, 1), 4)

    def collect_all(self) -> Dict:
        return {
            "window_secs": self.window_secs,
            "collected_at": time.time(),
            "retry_density": self.retry_density(),
            "escalation_frequency": self.escalation_frequency(),
            "branch_divergence": self.branch_divergence(),
            "instability_spread": self.instability_spread(),
            "replay_fragmentation": self.replay_fragmentation(),
        }


# ── Chaos index engine ─────────────────────────────────────────────────────────

class ChaosIndexEngine:
    """
    Produces a single Runtime Chaos Index (0 = calm, 100 = catastrophic)
    from the five entropy dimensions.

    Weights:
      instability_spread    35%
      escalation_frequency  25%
      retry_density         20%
      replay_fragmentation  12%
      branch_divergence      8%
    """

    # Cap values used to normalise each metric to [0,1]
    CAPS = {
        "retry_density":       0.50,   # >50% retries = max
        "escalation_frequency": 20.0,  # >20 failures/hour = max
        "branch_divergence":   10,     # >10 splits = max
        "instability_spread":  0.80,   # >80% unstable = max
        "replay_fragmentation": 0.60,  # >60% fragmented = max
    }

    WEIGHTS = {
        "instability_spread":   0.35,
        "escalation_frequency": 0.25,
        "retry_density":        0.20,
        "replay_fragmentation": 0.12,
        "branch_divergence":    0.08,
    }

    def compute(self, metrics: Dict) -> Dict:
        scores = {}
        for key, cap in self.CAPS.items():
            raw = metrics.get(key, 0)
            scores[key] = min(1.0, raw / cap) if cap > 0 else 0.0

        chaos_raw = sum(scores[k] * self.WEIGHTS[k] for k in self.WEIGHTS)
        chaos_index = round(chaos_raw * 100, 1)

        if chaos_index < 20:
            stability_label = "CALM"
        elif chaos_index < 40:
            stability_label = "STABLE"
        elif chaos_index < 60:
            stability_label = "ELEVATED"
        elif chaos_index < 80:
            stability_label = "HIGH"
        else:
            stability_label = "CRITICAL"

        return {
            "chaos_index": chaos_index,
            "stability_label": stability_label,
            "dimension_scores": {k: round(v * 100, 1) for k, v in scores.items()},
        }


# ── Entropy monitor ────────────────────────────────────────────────────────────

class EntropyMonitor:
    """
    Lightweight continuous monitor. Caches results for 120s to avoid
    constant SQLite reads. Provides a single report() call.
    """

    def __init__(self, store: ExecutionStore, window_secs: int = 3600):
        self.metrics = EntropyMetrics(store, window_secs)
        self.chaos_engine = ChaosIndexEngine()
        self._cache: Optional[Dict] = None
        self._cache_ts: float = 0.0

    def report(self, force: bool = False) -> Dict:
        now = time.time()
        if not force and self._cache and (now - self._cache_ts) < 120:
            return self._cache

        raw_metrics = self.metrics.collect_all()
        chaos = self.chaos_engine.compute(raw_metrics)

        result = {
            "reported_at": now,
            **raw_metrics,
            **chaos,
        }
        self._cache = result
        self._cache_ts = now

        logger.info(
            "[EntropyMonitor] Chaos Index=%.1f (%s) | retry=%.3f | instability=%.3f | fragmentation=%.3f",
            chaos["chaos_index"], chaos["stability_label"],
            raw_metrics["retry_density"], raw_metrics["instability_spread"],
            raw_metrics["replay_fragmentation"],
        )
        return result
