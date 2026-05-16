"""
execution/cognitive_load_balancing.py — Phase Z40F: Cognitive Load Balancing
=============================================================================
Prevents operational overload across runtime surfaces by distributing pressure,
applying adaptive calmness during high entropy, and implementing breathing logic
for idle stable systems.

Subsystems:
  • SurfacePressureDistributor — balances timeline density, DAG complexity, inspector, replay
  • AdaptiveCalmEngine         — reduces visual/operational noise during high entropy
  • RuntimeBreathingMonitor    — tracks idle stability and normalises pressure over time
"""

import time
import math
import threading
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("nexora.cognitive_load_balancing")


# ── Surface pressure distributor ──────────────────────────────────────────────

@dataclass
class SurfacePressure:
    timeline_density:   float = 0.0   # 0.0–1.0
    dag_complexity:     float = 0.0
    inspector_load:     float = 0.0
    replay_hydration:   float = 0.0

    def overall(self) -> float:
        weights = [0.35, 0.25, 0.15, 0.25]
        values  = [self.timeline_density, self.dag_complexity,
                   self.inspector_load,   self.replay_hydration]
        return round(sum(w * v for w, v in zip(weights, values)), 4)

    def to_dict(self) -> Dict:
        return {
            "timeline_density":  round(self.timeline_density,  4),
            "dag_complexity":    round(self.dag_complexity,     4),
            "inspector_load":    round(self.inspector_load,     4),
            "replay_hydration":  round(self.replay_hydration,   4),
            "overall":           self.overall(),
        }


class SurfacePressureDistributor:
    """
    Tracks per-surface pressure and redistributes load when any surface
    exceeds its safe operating band.
    """

    SURFACE_CAP = 0.80   # above this, redistribution is triggered

    def __init__(self):
        self._pressure = SurfacePressure()
        self._lock     = threading.Lock()
        self._history: List[Dict] = []

    def update(
        self,
        timeline_density: Optional[float] = None,
        dag_complexity:   Optional[float] = None,
        inspector_load:   Optional[float] = None,
        replay_hydration: Optional[float] = None,
    ) -> Dict:
        with self._lock:
            if timeline_density  is not None:
                self._pressure.timeline_density  = min(1.0, max(0.0, timeline_density))
            if dag_complexity    is not None:
                self._pressure.dag_complexity    = min(1.0, max(0.0, dag_complexity))
            if inspector_load    is not None:
                self._pressure.inspector_load    = min(1.0, max(0.0, inspector_load))
            if replay_hydration  is not None:
                self._pressure.replay_hydration  = min(1.0, max(0.0, replay_hydration))

        action = self._redistribute()
        snap = {**self._pressure.to_dict(), "redistribution_action": action, "ts": time.time()}
        self._history.append(snap)
        self._history = self._history[-200:]
        return snap

    def _redistribute(self) -> str:
        p = self._pressure
        actions = []
        if p.timeline_density > self.SURFACE_CAP:
            actions.append("throttle_timeline_expansion")
        if p.dag_complexity > self.SURFACE_CAP:
            actions.append("flatten_dag_display")
        if p.inspector_load > self.SURFACE_CAP:
            actions.append("reduce_inspector_polling")
        if p.replay_hydration > self.SURFACE_CAP:
            actions.append("defer_replay_hydration")
        return ", ".join(actions) if actions else "no_action"

    def snapshot(self) -> Dict:
        with self._lock:
            return {
                "pressure": self._pressure.to_dict(),
                "history_points": len(self._history),
                "last_5": self._history[-5:],
            }


# ── Adaptive calm engine ───────────────────────────────────────────────────────

class AdaptiveCalmEngine:
    """
    During high entropy, automatically reduces operational surface intensity.
    Returns a CalmDirective telling surfaces how much to dial back.
    """

    @dataclass
    class CalmDirective:
        entropy_level:          float   # 0.0–1.0
        timeline_expansion:     float   # 0.0=none, 1.0=full
        visual_noise_factor:    float   # 1.0=full, 0.0=silent
        replay_hydration_limit: float   # fraction of normal hydration
        speculative_allowed:    bool

        def to_dict(self) -> Dict:
            return {
                "entropy_level":            round(self.entropy_level, 4),
                "timeline_expansion":       round(self.timeline_expansion, 4),
                "visual_noise_factor":      round(self.visual_noise_factor, 4),
                "replay_hydration_limit":   round(self.replay_hydration_limit, 4),
                "speculative_allowed":      self.speculative_allowed,
            }

    def compute(self, chaos_index: float, calmness_score: float) -> "AdaptiveCalmEngine.CalmDirective":
        """
        chaos_index:   0–100  (higher = worse)
        calmness_score: 0–100  (higher = better)
        """
        entropy_level = min(1.0, chaos_index / 100.0)
        calm_level    = min(1.0, calmness_score / 100.0)

        # Timeline expansion: reduce as entropy rises
        timeline_expansion = max(0.10, calm_level * 0.8 + 0.2 - entropy_level * 0.5)

        # Visual noise: quiet down when chaotic
        visual_noise = max(0.20, 1.0 - entropy_level * 0.70)

        # Replay hydration: limit under entropy
        replay_limit = max(0.20, 1.0 - entropy_level * 0.60)

        # Speculative execution: disable above 70 chaos
        speculative = chaos_index < 70.0

        return self.CalmDirective(
            entropy_level=round(entropy_level, 4),
            timeline_expansion=round(timeline_expansion, 4),
            visual_noise_factor=round(visual_noise, 4),
            replay_hydration_limit=round(replay_limit, 4),
            speculative_allowed=speculative,
        )


# ── Runtime breathing monitor ─────────────────────────────────────────────────

class RuntimeBreathingMonitor:
    """
    Tracks idle stability and normalises pressure for stable, idle systems.
    Implements a "breathing" model: systems that have been idle and calm
    gradually reduce their operational footprint.
    """

    IDLE_THRESHOLD_SECS  = 300    # 5 minutes of no significant activity
    BREATH_INTERVAL_SECS = 60     # re-assess every minute

    def __init__(self):
        self._last_activity: Dict[str, float] = {}
        self._breath_level:  Dict[str, float] = {}   # 0.0=very calm, 1.0=active
        self._lock = threading.Lock()

    def record_activity(self, context_id: str, intensity: float = 1.0) -> None:
        with self._lock:
            self._last_activity[context_id] = time.time()
            self._breath_level[context_id]  = min(1.0, intensity)

    def get_breath_level(self, context_id: str) -> float:
        """Returns normalised breath level after applying idle decay."""
        now = time.time()
        with self._lock:
            last = self._last_activity.get(context_id, now)
            idle_secs = now - last
            base = self._breath_level.get(context_id, 0.5)

        if idle_secs < self.IDLE_THRESHOLD_SECS:
            return base

        # Gradual calm: after idle threshold, decay toward 0 with 10-min half-life
        half_life = 600
        decay = math.pow(0.5, (idle_secs - self.IDLE_THRESHOLD_SECS) / half_life)
        return round(base * decay, 4)

    def snapshot(self) -> Dict:
        now = time.time()
        with self._lock:
            entries = []
            for cid, level in self._breath_level.items():
                idle = now - self._last_activity.get(cid, now)
                entries.append({
                    "context_id":   cid,
                    "breath_level": self.get_breath_level(cid),
                    "idle_secs":    round(idle, 1),
                })
        entries.sort(key=lambda e: e["breath_level"], reverse=True)
        return {
            "tracked_contexts": len(entries),
            "contexts":         entries[:20],
        }


# ── Unified cognitive load balancer ───────────────────────────────────────────

class CognitiveLoadBalancer:
    """Top-level facade for Z40F."""

    def __init__(self):
        self.distributor = SurfacePressureDistributor()
        self.calm_engine = AdaptiveCalmEngine()
        self.breathing   = RuntimeBreathingMonitor()

    def assess(self, chaos_index: float = 0.0, calmness_score: float = 100.0) -> Dict:
        directive = self.calm_engine.compute(chaos_index, calmness_score)
        pressure  = self.distributor.snapshot()
        breath    = self.breathing.snapshot()
        return {
            "assessed_at":  time.time(),
            "calm_directive": directive.to_dict(),
            "surface_pressure": pressure["pressure"],
            "breathing":    breath,
        }

    def update_surface(self, **kwargs) -> Dict:
        return self.distributor.update(**kwargs)

    def record_activity(self, context_id: str, intensity: float = 1.0) -> None:
        self.breathing.record_activity(context_id, intensity)


# Global singleton
_load_balancer = CognitiveLoadBalancer()

def get_load_balancer() -> CognitiveLoadBalancer:
    return _load_balancer
