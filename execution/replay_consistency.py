"""
execution/replay_consistency.py — Phase Z39B: Replay Consistency System
========================================================================
Ensures replays remain historically accurate, causally valid, and
operationally trustworthy.

Subsystems:
  • ReplayDriftDetector    — detects missing ancestors, divergence, temporal disorders
  • ReplayConfidenceScorer — scores each replay chain on consistency and hydration
  • ReplayRecoveryEngine   — attempts safe repair without mutating source history
"""

import time
import logging
from typing import Dict, List, Optional, Tuple
from execution.store import ExecutionStore
from execution.replay import ExecutionReplayEngine

logger = logging.getLogger("nexora.replay_consistency")


# ── Drift detector ─────────────────────────────────────────────────────────────

class ReplayDriftDetector:
    """
    Detects structural drift in a replay chain:
      • missing ancestor events
      • temporal ordering violations
      • inconsistent lineage history (duplicate or skipped event types)
      • branch divergence (replay produces different summary than snapshot)
    """

    # Minimum number of events expected per lifecycle transition
    EXPECTED_LIFECYCLE_EVENTS = {"task.started"}

    def __init__(self, store: ExecutionStore):
        self.store = store

    def detect(self, execution_id: str) -> Dict:
        events = self.store.get_events(execution_id)
        drift_flags = []
        missing_ancestors = []
        branch_divergence = False

        if not events:
            return {
                "execution_id": execution_id,
                "drifted": False,
                "drift_flags": ["no_events"],
                "missing_ancestors": [],
                "branch_divergence": False,
                "event_count": 0,
            }

        # Check temporal ordering
        prev_ts = None
        seen_types = []
        for i, evt in enumerate(events):
            ts = evt["timestamp"]
            if prev_ts is not None and ts < prev_ts - 0.001:
                drift_flags.append(f"out_of_order_event@index_{i}")
            prev_ts = ts
            seen_types.append(evt["event_type"])

        # Check for missing lifecycle start
        if "task.started" not in seen_types:
            drift_flags.append("missing_lifecycle_start")
            missing_ancestors.append("task.started")

        # Detect duplicate final states (replay ended more than once)
        terminal_events = [t for t in seen_types if t in ("task.completed", "task.failed", "task.cancelled")]
        if len(terminal_events) > 1:
            drift_flags.append(f"multiple_terminal_states:{','.join(terminal_events)}")
            branch_divergence = True

        # Detect very long gaps (>1 hour) between consecutive events
        for i in range(1, len(events)):
            gap = events[i]["timestamp"] - events[i - 1]["timestamp"]
            if gap > 3600:
                drift_flags.append(f"temporal_gap_{int(gap)}s@index_{i}")

        return {
            "execution_id": execution_id,
            "drifted": len(drift_flags) > 0,
            "drift_flags": drift_flags,
            "missing_ancestors": missing_ancestors,
            "branch_divergence": branch_divergence,
            "event_count": len(events),
        }


# ── Confidence scorer ──────────────────────────────────────────────────────────

class ReplayConfidenceScorer:
    """
    Assigns a confidence profile to each replay chain covering:
      • consistency_score     — structural validity of event sequence
      • drift_score           — inverse of drift severity (1.0 = no drift)
      • hydration_confidence  — completeness of event payload data
      • reconstruction_pct    — what fraction of the timeline can be reconstructed
    """

    def __init__(self, store: ExecutionStore, drift_detector: ReplayDriftDetector):
        self.store = store
        self.drift_detector = drift_detector

    def score(self, execution_id: str) -> Dict:
        events = self.store.get_events(execution_id)
        drift_result = self.drift_detector.detect(execution_id)

        if not events:
            return {
                "execution_id": execution_id,
                "consistency_score": 0.0,
                "drift_score": 0.0,
                "hydration_confidence": 0.0,
                "reconstruction_pct": 0.0,
                "overall_confidence": 0.0,
                "grade": "EMPTY",
            }

        # Consistency: penalise each drift flag
        n_flags = len(drift_result["drift_flags"])
        consistency_score = max(0.0, 1.0 - n_flags * 0.2)

        # Drift score (1 = clean, 0 = severely drifted)
        drift_score = 0.0 if drift_result["branch_divergence"] else max(0.0, 1.0 - n_flags * 0.15)

        # Hydration: fraction of events with non-empty payloads
        hydrated = sum(1 for e in events if e.get("payload") and e["payload"] != {})
        hydration_confidence = hydrated / len(events) if events else 0.0

        # Reconstruction: fraction of timeline points that can be rebuilt
        reconstructible_types = {"task.started", "task.completed", "task.failed",
                                  "tool.called", "file.modified", "stream.chunk"}
        reconstructible = sum(1 for e in events if e["event_type"] in reconstructible_types)
        reconstruction_pct = reconstructible / len(events) if events else 0.0

        overall = (consistency_score * 0.35 + drift_score * 0.25 +
                   hydration_confidence * 0.20 + reconstruction_pct * 0.20)

        grade = self._grade(overall)

        return {
            "execution_id": execution_id,
            "consistency_score": round(consistency_score, 3),
            "drift_score": round(drift_score, 3),
            "hydration_confidence": round(hydration_confidence, 3),
            "reconstruction_pct": round(reconstruction_pct, 3),
            "overall_confidence": round(overall, 3),
            "grade": grade,
        }

    def _grade(self, score: float) -> str:
        if score >= 0.90:
            return "EXCELLENT"
        if score >= 0.70:
            return "GOOD"
        if score >= 0.50:
            return "FAIR"
        if score >= 0.30:
            return "POOR"
        return "CRITICAL"


# ── Replay recovery engine ─────────────────────────────────────────────────────

class ReplayRecoveryEngine:
    """
    Attempts to produce a repaired view of a drifted replay chain.
    NEVER mutates the source event log — operates on a read-only copy.
    """

    def __init__(self, store: ExecutionStore, scorer: ReplayConfidenceScorer):
        self.store = store
        self.scorer = scorer

    def attempt_repair(self, execution_id: str) -> Dict:
        """
        Returns a repaired event sequence and a repair report.
        If the chain is already healthy, returns unchanged with no actions taken.
        """
        events = self.store.get_events(execution_id)
        if not events:
            return {"execution_id": execution_id, "repaired": False, "reason": "no_events", "actions": []}

        score = self.scorer.score(execution_id)
        if score["overall_confidence"] >= 0.85:
            return {
                "execution_id": execution_id,
                "repaired": False,
                "reason": "already_healthy",
                "confidence_before": score["overall_confidence"],
                "actions": [],
            }

        actions = []
        repaired_events = list(events)

        # Action 1: Re-sort out-of-order events by timestamp (safe, read-only view)
        sorted_events = sorted(repaired_events, key=lambda e: e["timestamp"])
        if sorted_events != repaired_events:
            actions.append("reordered_events_by_timestamp")
            repaired_events = sorted_events

        # Action 2: Deduplicate by event_id
        seen_ids = set()
        deduped = []
        for evt in repaired_events:
            if evt["event_id"] not in seen_ids:
                deduped.append(evt)
                seen_ids.add(evt["event_id"])
        if len(deduped) < len(repaired_events):
            actions.append(f"removed_{len(repaired_events) - len(deduped)}_duplicate_events")
            repaired_events = deduped

        # Action 3: Trim excess terminal states (keep first only)
        terminal_types = {"task.completed", "task.failed", "task.cancelled"}
        seen_terminal = False
        trimmed = []
        for evt in repaired_events:
            if evt["event_type"] in terminal_types:
                if not seen_terminal:
                    trimmed.append(evt)
                    seen_terminal = True
                else:
                    actions.append(f"dropped_duplicate_terminal_event:{evt['event_type']}")
            else:
                trimmed.append(evt)
        repaired_events = trimmed

        return {
            "execution_id": execution_id,
            "repaired": len(actions) > 0,
            "confidence_before": score["overall_confidence"],
            "event_count_before": len(events),
            "event_count_after": len(repaired_events),
            "actions": actions,
            "repaired_event_ids": [e["event_id"] for e in repaired_events],
            "note": "Source event log is UNCHANGED. This is a read-only repair view.",
        }


# ── Unified consistency manager ────────────────────────────────────────────────

class ReplayConsistencyManager:
    """Top-level facade combining all Z39B subsystems."""

    def __init__(self, store: ExecutionStore):
        self.store = store
        self.drift_detector = ReplayDriftDetector(store)
        self.scorer = ReplayConfidenceScorer(store, self.drift_detector)
        self.recovery = ReplayRecoveryEngine(store, self.scorer)
        self._cache: Dict[str, Dict] = {}
        self._cache_ts: Dict[str, float] = {}

    def analyse(self, execution_id: str, use_cache: bool = True) -> Dict:
        now = time.time()
        if use_cache and execution_id in self._cache and (now - self._cache_ts.get(execution_id, 0)) < 120:
            return self._cache[execution_id]

        drift = self.drift_detector.detect(execution_id)
        score = self.scorer.score(execution_id)
        repair = {}
        if drift["drifted"]:
            repair = self.recovery.attempt_repair(execution_id)

        result = {
            "execution_id": execution_id,
            "analysed_at": now,
            "drift": drift,
            "confidence": score,
            "repair": repair,
        }
        self._cache[execution_id] = result
        self._cache_ts[execution_id] = now
        return result

    def bulk_analyse(self, limit: int = 100) -> List[Dict]:
        """Score all recent executions and return a ranked summary."""
        with __import__("sqlite3").connect(self.store.db_path) as conn:
            rows = conn.execute(
                "SELECT execution_id FROM executions ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()

        results = []
        for (eid,) in rows:
            try:
                score = self.scorer.score(eid)
                results.append(score)
            except Exception as exc:
                logger.warning("[ReplayConsistency] Could not score %s: %s", eid, exc)

        results.sort(key=lambda r: r["overall_confidence"])
        return results
