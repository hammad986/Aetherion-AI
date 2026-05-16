"""
execution/memory_discipline.py — Phase Z39C: Adaptive Memory Discipline
========================================================================
Prevents historical cognition from becoming stale, toxic, or overweighted.

Subsystems:
  • HistoricalDecay         — older unstable patterns lose influence over time
  • RecoveryConfidenceDecay — successful recoveries gradually lose authority if unused
  • TrustRecalibrator       — dynamically updates confidence weighting
  • StaleCognitionDetector  — identifies dead patterns, obsolete recoveries, poisoned paths
"""

import sqlite3
import time
import math
import logging
from typing import Dict, List, Optional
from execution.store import ExecutionStore

logger = logging.getLogger("nexora.memory_discipline")

# ── Decay constants ────────────────────────────────────────────────────────────
HALF_LIFE_UNSTABLE_SECS = 3 * 24 * 3600   # 3 days
HALF_LIFE_STABLE_SECS   = 30 * 24 * 3600  # 30 days
RECOVERY_HALF_LIFE_SECS = 7 * 24 * 3600   # 7 days
STALE_THRESHOLD_SECS    = 14 * 24 * 3600  # 14 days without access = stale


def _decay_factor(age_seconds: float, half_life_seconds: float) -> float:
    """Exponential decay: f = 0.5^(age/half_life). Returns value in [0,1]."""
    if half_life_seconds <= 0:
        return 0.0
    return math.pow(0.5, age_seconds / half_life_seconds)


# ── Historical decay ───────────────────────────────────────────────────────────

class HistoricalDecay:
    """
    Applies time-based decay to the influence weight of historical patterns.
    Unstable patterns decay faster than stable ones.
    """

    def apply(self, pattern: Dict) -> Dict:
        """
        Computes decayed_weight for a pattern dict containing at least:
          created_at (float), base_weight (float), is_unstable (bool)
        Returns the input dict augmented with decayed_weight and decay_factor.
        """
        now = time.time()
        age = now - pattern.get("created_at", now)
        half_life = HALF_LIFE_UNSTABLE_SECS if pattern.get("is_unstable", False) else HALF_LIFE_STABLE_SECS
        factor = _decay_factor(age, half_life)
        base = pattern.get("base_weight", 1.0)
        pattern = dict(pattern)
        pattern["decay_factor"] = round(factor, 4)
        pattern["decayed_weight"] = round(base * factor, 4)
        pattern["age_days"] = round(age / 86400, 2)
        return pattern

    def apply_bulk(self, patterns: List[Dict]) -> List[Dict]:
        return [self.apply(p) for p in patterns]


# ── Recovery confidence decay ──────────────────────────────────────────────────

class RecoveryConfidenceDecay:
    """
    Tracks successful recovery patterns and decays their authority
    when they haven't been exercised recently.
    """

    def __init__(self):
        self._registry: Dict[str, Dict] = {}

    def register_recovery(self, pattern_id: str, confidence: float, label: str = "") -> None:
        """Record a new or refreshed recovery pattern."""
        self._registry[pattern_id] = {
            "pattern_id": pattern_id,
            "label": label,
            "confidence": confidence,
            "last_used": time.time(),
            "use_count": self._registry.get(pattern_id, {}).get("use_count", 0) + 1,
        }

    def touch(self, pattern_id: str) -> bool:
        """Mark a pattern as recently used, refreshing its authority."""
        if pattern_id in self._registry:
            self._registry[pattern_id]["last_used"] = time.time()
            self._registry[pattern_id]["use_count"] += 1
            return True
        return False

    def get_effective_confidence(self, pattern_id: str) -> float:
        """Returns the current decayed confidence for a recovery pattern."""
        entry = self._registry.get(pattern_id)
        if not entry:
            return 0.0
        age = time.time() - entry["last_used"]
        factor = _decay_factor(age, RECOVERY_HALF_LIFE_SECS)
        return round(entry["confidence"] * factor, 4)

    def snapshot(self) -> List[Dict]:
        now = time.time()
        result = []
        for pid, entry in self._registry.items():
            age = now - entry["last_used"]
            effective = round(entry["confidence"] * _decay_factor(age, RECOVERY_HALF_LIFE_SECS), 4)
            result.append({
                **entry,
                "effective_confidence": effective,
                "age_days": round(age / 86400, 2),
                "is_stale": age > STALE_THRESHOLD_SECS,
            })
        return sorted(result, key=lambda r: r["effective_confidence"], reverse=True)


# ── Trust recalibrator ─────────────────────────────────────────────────────────

class TrustRecalibrator:
    """
    Dynamically updates the confidence weighting of execution memory entries
    based on outcome feedback (success/failure) and age.
    """

    def __init__(self):
        self._weights: Dict[str, float] = {}    # entry_id → current weight
        self._feedback: Dict[str, List] = {}     # entry_id → [(outcome, ts), ...]

    def record_outcome(self, entry_id: str, success: bool) -> None:
        self._feedback.setdefault(entry_id, []).append((success, time.time()))
        # Keep last 20 outcomes per entry
        self._feedback[entry_id] = self._feedback[entry_id][-20:]
        self._recalibrate(entry_id)

    def _recalibrate(self, entry_id: str) -> None:
        outcomes = self._feedback.get(entry_id, [])
        if not outcomes:
            return
        now = time.time()
        weighted_sum = 0.0
        weight_total = 0.0
        for (success, ts) in outcomes:
            age = now - ts
            time_weight = _decay_factor(age, HALF_LIFE_STABLE_SECS)
            weighted_sum += (1.0 if success else 0.0) * time_weight
            weight_total += time_weight
        self._weights[entry_id] = round(weighted_sum / weight_total, 4) if weight_total > 0 else 0.5

    def get_weight(self, entry_id: str) -> float:
        return self._weights.get(entry_id, 0.5)

    def snapshot(self) -> Dict:
        return {
            "tracked_entries": len(self._weights),
            "weights": dict(self._weights),
        }


# ── Stale cognition detector ───────────────────────────────────────────────────

class StaleCognitionDetector:
    """
    Detects dead patterns, obsolete recoveries, permanently stable branches,
    and poisoned historical paths in the execution store.
    """

    def __init__(self, store: ExecutionStore, recovery_decay: RecoveryConfidenceDecay):
        self.store = store
        self.recovery_decay = recovery_decay

    def scan(self, max_executions: int = 500) -> Dict:
        now = time.time()
        stale_executions = []
        poisoned_paths = []
        permanently_stable = []

        with sqlite3.connect(self.store.db_path) as conn:
            conn.row_factory = sqlite3.Row

            rows = conn.execute(
                "SELECT execution_id, status, started_at, updated_at, payload FROM executions ORDER BY updated_at DESC LIMIT ?",
                (max_executions,)
            ).fetchall()

        for row in rows:
            age = now - (row["updated_at"] or now)
            started = row["started_at"] or now
            duration = (row["updated_at"] or now) - started

            # Stale: untouched for >14 days
            if age > STALE_THRESHOLD_SECS:
                stale_executions.append({
                    "execution_id": row["execution_id"],
                    "status": row["status"],
                    "age_days": round(age / 86400, 1),
                })

            # Poisoned: stuck in running/queued for >4 hours
            if row["status"] in ("running", "queued") and age > 4 * 3600:
                poisoned_paths.append({
                    "execution_id": row["execution_id"],
                    "status": row["status"],
                    "stuck_hours": round(age / 3600, 1),
                })

            # Permanently stable: completed quickly, old, no issues
            if row["status"] == "completed" and age > 7 * 86400 and duration < 300:
                permanently_stable.append(row["execution_id"])

        # Stale recovery patterns
        stale_recoveries = [r for r in self.recovery_decay.snapshot() if r["is_stale"]]

        return {
            "scanned_at": now,
            "stale_executions": stale_executions,
            "stale_execution_count": len(stale_executions),
            "poisoned_paths": poisoned_paths,
            "poisoned_path_count": len(poisoned_paths),
            "permanently_stable_count": len(permanently_stable),
            "stale_recovery_patterns": stale_recoveries,
            "stale_recovery_count": len(stale_recoveries),
        }


# ── Unified memory discipline manager ─────────────────────────────────────────

class MemoryDisciplineManager:
    """Facade combining all Z39C subsystems."""

    def __init__(self, store: ExecutionStore):
        self.store = store
        self.decay = HistoricalDecay()
        self.recovery_decay = RecoveryConfidenceDecay()
        self.trust = TrustRecalibrator()
        self.stale_detector = StaleCognitionDetector(store, self.recovery_decay)

    def full_report(self) -> Dict:
        stale = self.stale_detector.scan()
        recovery_snapshot = self.recovery_decay.snapshot()
        trust_snapshot = self.trust.snapshot()
        return {
            "generated_at": time.time(),
            "stale_cognition": stale,
            "recovery_patterns": recovery_snapshot,
            "trust_weights": trust_snapshot,
        }
