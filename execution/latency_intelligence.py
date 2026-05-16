"""
execution/latency_intelligence.py — Phase Z41C: Latency Intelligence
=====================================================================
Understands where runtime latency originates, classifies severity,
and provides a minimal latency heatmap.

Subsystems:
  • LatencyTracer       — causal latency tracing across runtime surfaces
  • LatencySeverity     — FAST / NORMAL / ELEVATED / DEGRADED / BLOCKED
  • LatencyHeatmap      — minimal per-surface latency indicators
"""

import time
import threading
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("nexora.latency_intelligence")


# ── Latency surfaces ───────────────────────────────────────────────────────────

LATENCY_SURFACES = [
    "replay_hydration",
    "dag_propagation",
    "stabilization_loop",
    "compression_pass",
    "inspector_rendering",
    "coordination",
    "scheduling",
]


# ── Latency severity ──────────────────────────────────────────────────────────

class LatencySeverity:
    FAST     = "FAST"       # < 100ms
    NORMAL   = "NORMAL"     # 100–500ms
    ELEVATED = "ELEVATED"   # 500ms–2s
    DEGRADED = "DEGRADED"   # 2s–10s
    BLOCKED  = "BLOCKED"    # > 10s

    @staticmethod
    def classify(ms: float) -> str:
        if ms < 100:
            return LatencySeverity.FAST
        if ms < 500:
            return LatencySeverity.NORMAL
        if ms < 2000:
            return LatencySeverity.ELEVATED
        if ms < 10000:
            return LatencySeverity.DEGRADED
        return LatencySeverity.BLOCKED

    @staticmethod
    def score(ms: float) -> float:
        """0.0 = instant, 1.0 = blocked."""
        return min(1.0, ms / 10000.0)


# ── Latency trace entry ────────────────────────────────────────────────────────

@dataclass
class LatencyTrace:
    trace_id:   str
    surface:    str
    duration_ms: float
    severity:   str
    caused_by:  str   # causal predecessor surface (empty if root cause)
    ts:         float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return {
            "trace_id":    self.trace_id,
            "surface":     self.surface,
            "duration_ms": round(self.duration_ms, 2),
            "severity":    self.severity,
            "caused_by":   self.caused_by,
            "ts":          self.ts,
        }


# ── Latency tracer ─────────────────────────────────────────────────────────────

class LatencyTracer:
    """
    Records latency observations per surface.
    Maintains a rolling window of the last 100 traces per surface.
    """

    WINDOW = 100

    def __init__(self):
        self._traces: Dict[str, deque] = {s: deque(maxlen=self.WINDOW) for s in LATENCY_SURFACES}
        self._lock = threading.Lock()
        self._seq  = 0

    def record(self, surface: str, duration_ms: float, caused_by: str = "") -> LatencyTrace:
        if surface not in self._traces:
            surface = "coordination"  # default unknown surfaces to coordination

        self._seq += 1
        trace = LatencyTrace(
            trace_id=f"lt-{self._seq:06d}",
            surface=surface,
            duration_ms=duration_ms,
            severity=LatencySeverity.classify(duration_ms),
            caused_by=caused_by,
        )
        with self._lock:
            self._traces[surface].append(trace)

        if trace.severity in (LatencySeverity.DEGRADED, LatencySeverity.BLOCKED):
            logger.warning(
                "[LatencyIntelligence] %s on surface=%s duration=%.1fms caused_by=%s",
                trace.severity, surface, duration_ms, caused_by or "root"
            )
        return trace

    def surface_stats(self, surface: str) -> Dict:
        with self._lock:
            traces = list(self._traces.get(surface, []))
        if not traces:
            return {"surface": surface, "count": 0, "avg_ms": 0.0, "p95_ms": 0.0,
                    "max_ms": 0.0, "severity": LatencySeverity.FAST, "score": 0.0}

        durations = sorted(t.duration_ms for t in traces)
        avg_ms = sum(durations) / len(durations)
        p95_ms = durations[int(len(durations) * 0.95)]
        return {
            "surface":   surface,
            "count":     len(traces),
            "avg_ms":    round(avg_ms, 2),
            "p95_ms":    round(p95_ms, 2),
            "max_ms":    round(durations[-1], 2),
            "severity":  LatencySeverity.classify(p95_ms),
            "score":     round(LatencySeverity.score(p95_ms), 4),
        }

    def all_surface_stats(self) -> List[Dict]:
        return [self.surface_stats(s) for s in LATENCY_SURFACES]

    def recent_traces(self, surface: str, limit: int = 10) -> List[Dict]:
        with self._lock:
            traces = list(self._traces.get(surface, []))
        return [t.to_dict() for t in traces[-limit:]]


# ── Latency heatmap ────────────────────────────────────────────────────────────

class LatencyHeatmap:
    """
    Produces a minimal operational latency heatmap: surface → severity indicator.
    No dashboards — operational indicators only.
    """

    SEVERITY_INDICATOR = {
        LatencySeverity.FAST:     "●",   # green
        LatencySeverity.NORMAL:   "●",   # blue
        LatencySeverity.ELEVATED: "●",   # yellow
        LatencySeverity.DEGRADED: "●",   # orange
        LatencySeverity.BLOCKED:  "●",   # red
    }

    def render(self, tracer: LatencyTracer) -> Dict:
        stats   = tracer.all_surface_stats()
        heatmap = {}
        for s in stats:
            heatmap[s["surface"]] = {
                "severity": s["severity"],
                "p95_ms":   s["p95_ms"],
                "score":    s["score"],
            }

        # Identify hottest surface
        hottest = max(stats, key=lambda s: s["score"]) if stats else None

        return {
            "rendered_at": time.time(),
            "heatmap":     heatmap,
            "hottest_surface": hottest["surface"] if hottest else None,
            "hottest_severity": hottest["severity"] if hottest else LatencySeverity.FAST,
        }


# ── Unified latency intelligence manager ─────────────────────────────────────

class LatencyIntelligenceManager:
    """Top-level facade for Z41C."""

    def __init__(self):
        self.tracer  = LatencyTracer()
        self.heatmap = LatencyHeatmap()

    def record(self, surface: str, duration_ms: float, caused_by: str = "") -> LatencyTrace:
        return self.tracer.record(surface, duration_ms, caused_by)

    def report(self) -> Dict:
        hm    = self.heatmap.render(self.tracer)
        stats = self.tracer.all_surface_stats()
        degraded = [s for s in stats if s["severity"] in (LatencySeverity.DEGRADED, LatencySeverity.BLOCKED)]
        return {
            "reported_at":     time.time(),
            "heatmap":         hm,
            "all_surfaces":    stats,
            "degraded_count":  len(degraded),
            "degraded_surfaces": degraded,
        }


# Global singleton
_latency_manager = LatencyIntelligenceManager()

def get_latency_manager() -> LatencyIntelligenceManager:
    return _latency_manager
