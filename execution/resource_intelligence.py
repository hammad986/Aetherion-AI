"""
execution/resource_intelligence.py — Phase Z40B: Resource Intelligence Engine
==============================================================================
Makes the runtime aware of resource pressure before failure occurs.

Subsystems:
  • ResourceTracker         — tracks token usage, replay growth, memory, timeline density
  • PressureForecaster      — estimates approaching overflow, overload, fragmentation
  • ResourceSeverityEngine  — classifies LIGHT / MODERATE / HEAVY / SATURATED / CRITICAL
"""

import time
import sqlite3
import logging
import threading
from enum import Enum
from typing import Dict, List, Optional
from execution.store import ExecutionStore

logger = logging.getLogger("nexora.resource_intelligence")


# ── Severity levels ────────────────────────────────────────────────────────────

class ResourceSeverity(str, Enum):
    LIGHT     = "LIGHT"
    MODERATE  = "MODERATE"
    HEAVY     = "HEAVY"
    SATURATED = "SATURATED"
    CRITICAL  = "CRITICAL"


# ── Resource tracker ───────────────────────────────────────────────────────────

class ResourceTracker:
    """
    Tracks per-session and global resource metrics using lightweight counters.
    All state is in-process; no extra tables required.
    """

    def __init__(self, store: ExecutionStore):
        self.store = store
        self._session_tokens:   Dict[str, int]   = {}
        self._session_replays:  Dict[str, int]   = {}
        self._session_memory:   Dict[str, int]   = {}   # items count
        self._session_dag_depth: Dict[str, int]  = {}
        self._lock = threading.Lock()

    def record_tokens(self, session_id: str, count: int) -> None:
        with self._lock:
            self._session_tokens[session_id] = self._session_tokens.get(session_id, 0) + count

    def record_replay(self, session_id: str, event_count: int) -> None:
        with self._lock:
            prev = self._session_replays.get(session_id, 0)
            self._session_replays[session_id] = max(prev, event_count)

    def record_memory_items(self, session_id: str, count: int) -> None:
        with self._lock:
            self._session_memory[session_id] = count

    def record_dag_depth(self, session_id: str, depth: int) -> None:
        with self._lock:
            prev = self._session_dag_depth.get(session_id, 0)
            self._session_dag_depth[session_id] = max(prev, depth)

    def get_session_metrics(self, session_id: str) -> Dict:
        with self._lock:
            return {
                "session_id":    session_id,
                "tokens_used":   self._session_tokens.get(session_id, 0),
                "replay_events": self._session_replays.get(session_id, 0),
                "memory_items":  self._session_memory.get(session_id, 0),
                "dag_depth":     self._session_dag_depth.get(session_id, 0),
            }

    def get_db_metrics(self) -> Dict:
        """Pull aggregate execution metrics from the store."""
        try:
            with sqlite3.connect(self.store.db_path) as conn:
                total_execs = conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
                total_events = conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
                running = conn.execute(
                    "SELECT COUNT(*) FROM executions WHERE status IN ('running','queued')"
                ).fetchone()[0]
        except Exception:
            total_execs = total_events = running = 0

        return {
            "total_executions": total_execs,
            "total_events":     total_events,
            "active_executions": running,
            "timeline_density":  total_events / max(total_execs, 1),
        }

    def global_snapshot(self) -> Dict:
        with self._lock:
            session_ids = set(
                list(self._session_tokens.keys()) +
                list(self._session_replays.keys())
            )
            total_tokens  = sum(self._session_tokens.values())
            max_replay    = max(self._session_replays.values(), default=0)
            total_memory  = sum(self._session_memory.values())
            max_dag_depth = max(self._session_dag_depth.values(), default=0)

        db = self.get_db_metrics()
        return {
            "tracked_sessions":  len(session_ids),
            "total_tokens_used": total_tokens,
            "max_replay_events": max_replay,
            "total_memory_items": total_memory,
            "max_dag_depth":      max_dag_depth,
            **db,
        }


# ── Pressure forecaster ────────────────────────────────────────────────────────

class PressureForecaster:
    """
    Estimates approaching resource pressure from current trajectory metrics.
    Returns a forecast dict with risk scores (0.0–1.0) per dimension.
    """

    # Thresholds at which a risk score = 1.0
    TOKEN_OVERFLOW_CAP     = 128_000   # typical LLM context limit
    REPLAY_OVERLOAD_CAP    = 5_000     # events per replay chain
    MEMORY_FRAGMENT_CAP    = 10_000    # items in working memory
    ENTROPY_ESCALATION_CAP = 70.0      # chaos index
    DAG_OVERLOAD_CAP       = 30        # dag depth

    def forecast(self, resource_snapshot: Dict, chaos_index: float = 0.0) -> Dict:
        tokens  = resource_snapshot.get("total_tokens_used", 0)
        replay  = resource_snapshot.get("max_replay_events", 0)
        memory  = resource_snapshot.get("total_memory_items", 0)
        dag     = resource_snapshot.get("max_dag_depth", 0)
        density = resource_snapshot.get("timeline_density", 0)

        risks = {
            "context_overflow":        min(1.0, tokens  / self.TOKEN_OVERFLOW_CAP),
            "replay_hydration_overload": min(1.0, replay / self.REPLAY_OVERLOAD_CAP),
            "memory_fragmentation":    min(1.0, memory  / self.MEMORY_FRAGMENT_CAP),
            "entropy_escalation":      min(1.0, chaos_index / self.ENTROPY_ESCALATION_CAP),
            "dag_complexity":          min(1.0, dag     / self.DAG_OVERLOAD_CAP),
            "timeline_density":        min(1.0, density / 100.0),
        }

        # Weighted overall risk
        weights = {
            "context_overflow":          0.30,
            "replay_hydration_overload": 0.20,
            "memory_fragmentation":      0.15,
            "entropy_escalation":        0.20,
            "dag_complexity":            0.10,
            "timeline_density":          0.05,
        }
        overall = sum(risks[k] * weights[k] for k in weights)

        return {
            "forecasted_at": time.time(),
            "risk_scores":   {k: round(v, 4) for k, v in risks.items()},
            "overall_risk":  round(overall, 4),
            "top_risk": max(risks, key=risks.get),
        }


# ── Severity engine ────────────────────────────────────────────────────────────

class ResourceSeverityEngine:
    """
    Maps overall_risk and individual dimension scores to ResourceSeverity levels.
    """

    def classify(self, forecast: Dict) -> ResourceSeverity:
        overall = forecast.get("overall_risk", 0.0)
        risks   = forecast.get("risk_scores", {})

        # Immediate CRITICAL if any dimension is maxed
        if any(v >= 0.90 for v in risks.values()):
            return ResourceSeverity.CRITICAL
        if overall >= 0.70 or any(v >= 0.75 for v in risks.values()):
            return ResourceSeverity.SATURATED
        if overall >= 0.50 or any(v >= 0.55 for v in risks.values()):
            return ResourceSeverity.HEAVY
        if overall >= 0.30 or any(v >= 0.35 for v in risks.values()):
            return ResourceSeverity.MODERATE
        return ResourceSeverity.LIGHT


# ── Unified resource intelligence manager ─────────────────────────────────────

class ResourceIntelligenceManager:
    """Top-level facade for Z40B."""

    def __init__(self, store: ExecutionStore):
        self.store    = store
        self.tracker  = ResourceTracker(store)
        self.forecast = PressureForecaster()
        self.severity = ResourceSeverityEngine()
        self._cache:    Optional[Dict] = None
        self._cache_ts: float = 0.0

    def report(self, chaos_index: float = 0.0, force: bool = False) -> Dict:
        now = time.time()
        if not force and self._cache and (now - self._cache_ts) < 60:
            return self._cache

        snapshot = self.tracker.global_snapshot()
        fc       = self.forecast.forecast(snapshot, chaos_index)
        sev      = self.severity.classify(fc)

        result = {
            "reported_at": now,
            "severity":    sev,
            "resources":   snapshot,
            "forecast":    fc,
        }
        self._cache    = result
        self._cache_ts = now

        if sev in (ResourceSeverity.SATURATED, ResourceSeverity.CRITICAL):
            logger.warning(
                "[ResourceIntelligence] Severity=%s | overall_risk=%.3f | top_risk=%s",
                sev, fc["overall_risk"], fc["top_risk"]
            )
        else:
            logger.debug("[ResourceIntelligence] Severity=%s | overall_risk=%.3f", sev, fc["overall_risk"])

        return result
