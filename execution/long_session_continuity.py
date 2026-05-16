"""
execution/long_session_continuity.py — Phase Z40D: Long-Session Continuity
===========================================================================
Maintains coherent operational cognition during multi-hour sessions.

Subsystems:
  • ContinuityThread      — tracks mission/replay/reasoning/dependency continuity
  • DriftDetector         — detects semantic drift, incoherence, contradiction, stale loops
  • ContextRefresher      — rebuilds active context from compressed operational summaries
"""

import time
import hashlib
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("nexora.long_session_continuity")


# ── Continuity dimensions ─────────────────────────────────────────────────────

CONTINUITY_DIMENSIONS = ["mission", "replay", "reasoning", "dependency"]


@dataclass
class ContinuityState:
    session_id:     str
    dimension:      str
    last_updated:   float = field(default_factory=time.time)
    coherence:      float = 1.0    # 0.0 = incoherent, 1.0 = fully coherent
    anchor_hash:    str   = ""     # hash of last stable anchor point
    drift_score:    float = 0.0    # 0.0 = no drift, 1.0 = severe drift
    stale_since:    Optional[float] = None

    def is_stale(self, threshold_secs: float = 1800) -> bool:
        return (time.time() - self.last_updated) > threshold_secs

    def to_dict(self) -> Dict:
        return {
            "session_id":   self.session_id,
            "dimension":    self.dimension,
            "coherence":    round(self.coherence, 4),
            "drift_score":  round(self.drift_score, 4),
            "is_stale":     self.is_stale(),
            "last_updated": self.last_updated,
            "anchor_hash":  self.anchor_hash,
        }


# ── Continuity thread ─────────────────────────────────────────────────────────

class ContinuityThread:
    """
    Maintains all four continuity dimensions for a session.
    Records anchor points and computes coherence degradation over time.
    """

    STALE_THRESHOLD_SECS = 1800   # 30 minutes without an update = stale

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._states: Dict[str, ContinuityState] = {
            dim: ContinuityState(session_id=session_id, dimension=dim)
            for dim in CONTINUITY_DIMENSIONS
        }
        self._lock = threading.Lock()

    def anchor(self, dimension: str, content: str, coherence: float = 1.0) -> None:
        """Set a new anchor point for a continuity dimension."""
        if dimension not in CONTINUITY_DIMENSIONS:
            return
        h = hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()[:12]
        with self._lock:
            s = self._states[dimension]
            s.last_updated = time.time()
            s.anchor_hash  = h
            s.coherence    = max(0.0, min(1.0, coherence))
            s.stale_since  = None
            # If coherence improved, reduce drift
            s.drift_score  = max(0.0, s.drift_score - 0.10)

    def degrade(self, dimension: str, drift_amount: float = 0.10) -> None:
        """Apply drift degradation to a continuity dimension."""
        if dimension not in CONTINUITY_DIMENSIONS:
            return
        with self._lock:
            s = self._states[dimension]
            s.drift_score = min(1.0, s.drift_score + drift_amount)
            s.coherence   = max(0.0, s.coherence - drift_amount * 0.5)
            if s.stale_since is None and s.is_stale():
                s.stale_since = time.time()

    def snapshot(self) -> Dict:
        with self._lock:
            states = [s.to_dict() for s in self._states.values()]
        avg_coherence = sum(s["coherence"] for s in states) / len(states)
        avg_drift     = sum(s["drift_score"] for s in states) / len(states)
        return {
            "session_id":    self.session_id,
            "avg_coherence": round(avg_coherence, 4),
            "avg_drift":     round(avg_drift, 4),
            "dimensions":    states,
        }


# ── Drift detector ─────────────────────────────────────────────────────────────

class DriftDetector:
    """
    Detects semantic drift, replay incoherence, context contradiction,
    and stale reasoning loops across continuity threads.
    """

    DRIFT_SEVERITY = {
        "mild":     (0.0,  0.30),
        "moderate": (0.30, 0.60),
        "severe":   (0.60, 1.00),
    }

    def detect(self, thread: ContinuityThread) -> Dict:
        snap   = thread.snapshot()
        issues = []

        for dim_state in snap["dimensions"]:
            drift = dim_state["drift_score"]
            dim   = dim_state["dimension"]

            if drift >= 0.60:
                issues.append({
                    "type":      "severe_drift",
                    "dimension": dim,
                    "drift":     drift,
                    "severity":  "severe",
                })
            elif drift >= 0.30:
                issues.append({
                    "type":      "moderate_drift",
                    "dimension": dim,
                    "drift":     drift,
                    "severity":  "moderate",
                })

            if dim_state["is_stale"]:
                issues.append({
                    "type":      "stale_dimension",
                    "dimension": dim,
                    "severity":  "moderate",
                })

            if dim_state["coherence"] < 0.40:
                issues.append({
                    "type":      "incoherence",
                    "dimension": dim,
                    "coherence": dim_state["coherence"],
                    "severity":  "severe",
                })

        overall_drift    = snap["avg_drift"]
        overall_coherence = snap["avg_coherence"]

        return {
            "session_id":        thread.session_id,
            "detected_at":       time.time(),
            "issue_count":       len(issues),
            "issues":            issues,
            "overall_drift":     round(overall_drift, 4),
            "overall_coherence": round(overall_coherence, 4),
            "drift_status": (
                "SEVERE" if overall_drift >= 0.60 else
                "MODERATE" if overall_drift >= 0.30 else
                "MILD"
            ),
        }


# ── Context refresher ─────────────────────────────────────────────────────────

class ContextRefresher:
    """
    Rebuilds active context from compressed operational summaries.
    Works with the CompressionLedger from Z40A.
    """

    def refresh(
        self,
        session_id: str,
        thread: ContinuityThread,
        compression_ledger=None,
    ) -> Dict:
        """
        Returns a refreshed context string and anchors all dimensions
        to the current state (resetting drift).
        """
        rebuilt_context = ""

        if compression_ledger is not None:
            try:
                win = compression_ledger.get_or_create(session_id)
                rebuilt_context = win.rebuild_active_context()
            except Exception as exc:
                logger.warning("[ContextRefresher] Could not rebuild from ledger: %s", exc)
                rebuilt_context = "[context rebuild unavailable]"
        else:
            rebuilt_context = "[no compression ledger available]"

        # Anchor all dimensions to current moment
        for dim in CONTINUITY_DIMENSIONS:
            thread.anchor(dim, f"refresh@{time.time()}", coherence=0.80)

        return {
            "session_id":          session_id,
            "refreshed_at":        time.time(),
            "rebuilt_context_len": len(rebuilt_context),
            "rebuilt_context":     rebuilt_context[:500],
            "anchored_dimensions": CONTINUITY_DIMENSIONS,
        }


# ── Long-session continuity manager ──────────────────────────────────────────

class LongSessionContinuityManager:
    """Top-level facade for Z40D."""

    def __init__(self):
        self._threads:  Dict[str, ContinuityThread] = {}
        self._detector  = DriftDetector()
        self._refresher = ContextRefresher()
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str) -> ContinuityThread:
        with self._lock:
            if session_id not in self._threads:
                self._threads[session_id] = ContinuityThread(session_id)
            return self._threads[session_id]

    def anchor(self, session_id: str, dimension: str, content: str, coherence: float = 1.0) -> None:
        self.get_or_create(session_id).anchor(dimension, content, coherence)

    def degrade(self, session_id: str, dimension: str, amount: float = 0.10) -> None:
        self.get_or_create(session_id).degrade(dimension, amount)

    def detect_drift(self, session_id: str) -> Dict:
        thread = self.get_or_create(session_id)
        return self._detector.detect(thread)

    def refresh(self, session_id: str, compression_ledger=None) -> Dict:
        thread = self.get_or_create(session_id)
        return self._refresher.refresh(session_id, thread, compression_ledger)

    def global_snapshot(self) -> Dict:
        with self._lock:
            threads = list(self._threads.values())
        snapshots = [t.snapshot() for t in threads]
        drifted   = [s for s in snapshots if s["avg_drift"] >= 0.30]
        return {
            "session_count":   len(snapshots),
            "drifted_sessions": len(drifted),
            "sessions":        snapshots,
        }

    def remove_session(self, session_id: str) -> None:
        with self._lock:
            self._threads.pop(session_id, None)


# Global singleton
_continuity_manager = LongSessionContinuityManager()

def get_continuity_manager() -> LongSessionContinuityManager:
    return _continuity_manager
