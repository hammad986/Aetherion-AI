"""
execution/runtime_coordination.py — Phase Z41A: Runtime Coordination Engine
============================================================================
Coordinates all adaptive runtime subsystems to prevent conflicts, overreaction,
and mutual destabilization.

Subsystems:
  • CoordinationGraph     — tracks subsystem dependencies and interactions
  • CoordinationArbitrator — resolves conflicts between competing subsystems
  • CoordinationSeverityEngine — classifies STABLE / COMPETING / CONFLICTING / THRASHING
"""

import time
import threading
import logging
from dataclasses import dataclass, field
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("nexora.runtime_coordination")


# ── Coordination severity ──────────────────────────────────────────────────────

class CoordinationSeverity:
    STABLE      = "STABLE"
    COMPETING   = "COMPETING"
    CONFLICTING = "CONFLICTING"
    THRASHING   = "THRASHING"


# ── Subsystem registry ─────────────────────────────────────────────────────────

KNOWN_SUBSYSTEMS = [
    "entropy",
    "stabilization",
    "budgeting",
    "replay_governance",
    "compression",
    "continuity",
    "load_balancing",
]

# Dependency map: subsystem → list of subsystems it reads from
DEPENDENCY_MAP: Dict[str, List[str]] = {
    "stabilization":    ["entropy"],
    "budgeting":        ["entropy", "stabilization"],
    "replay_governance": ["budgeting", "entropy"],
    "compression":      ["continuity", "budgeting"],
    "continuity":       ["compression"],
    "load_balancing":   ["entropy", "stabilization", "replay_governance"],
}

# Conflict rules: pairs that can compete if both are in high-pressure state
CONFLICT_PAIRS: List[Tuple[str, str]] = [
    ("stabilization", "budgeting"),       # stabilize → expand; budget → contract
    ("replay_governance", "compression"), # hydrate → retain; compress → discard
    ("compression", "continuity"),        # compress → reduce; continuity → preserve
    ("load_balancing", "replay_governance"),
]


@dataclass
class SubsystemState:
    name:        str
    pressure:    float = 0.0   # 0.0–1.0
    last_action: str  = "idle"
    last_updated: float = field(default_factory=time.time)

    def is_active(self) -> bool:
        return self.pressure > 0.30

    def to_dict(self) -> Dict:
        return {
            "name":        self.name,
            "pressure":    round(self.pressure, 4),
            "last_action": self.last_action,
            "last_updated": self.last_updated,
        }


# ── Coordination graph ─────────────────────────────────────────────────────────

class CoordinationGraph:
    """
    Maintains the live state of all subsystems and their interaction topology.
    """

    def __init__(self):
        self._states: Dict[str, SubsystemState] = {
            s: SubsystemState(name=s) for s in KNOWN_SUBSYSTEMS
        }
        self._interaction_log: deque = deque(maxlen=200)
        self._lock = threading.Lock()

    def update_subsystem(self, name: str, pressure: float, action: str = "update") -> None:
        if name not in self._states:
            return
        with self._lock:
            s = self._states[name]
            s.pressure    = max(0.0, min(1.0, pressure))
            s.last_action = action
            s.last_updated = time.time()

        self._interaction_log.append({
            "ts": time.time(), "subsystem": name,
            "pressure": pressure, "action": action,
        })

    def get_active_subsystems(self) -> List[SubsystemState]:
        with self._lock:
            return [s for s in self._states.values() if s.is_active()]

    def get_dependencies(self, name: str) -> List[str]:
        return DEPENDENCY_MAP.get(name, [])

    def snapshot(self) -> Dict:
        with self._lock:
            states = [s.to_dict() for s in self._states.values()]
        return {
            "subsystems":  states,
            "interaction_log": list(self._interaction_log)[-10:],
        }


# ── Conflict arbitrator ────────────────────────────────────────────────────────

class CoordinationArbitrator:
    """
    Detects conflicts between subsystems and produces arbitration verdicts.
    Arbitration is advisory — it records what SHOULD be done, not forces it.
    """

    CONFLICT_PRESSURE_THRESHOLD = 0.50

    def arbitrate(self, graph: CoordinationGraph) -> Dict:
        """
        Returns a list of detected conflicts and recommended resolutions.
        """
        conflicts = []

        for (a, b) in CONFLICT_PAIRS:
            state_a = graph._states.get(a)
            state_b = graph._states.get(b)
            if not state_a or not state_b:
                continue

            both_active = (
                state_a.pressure >= self.CONFLICT_PRESSURE_THRESHOLD and
                state_b.pressure >= self.CONFLICT_PRESSURE_THRESHOLD
            )
            if both_active:
                resolution = self._resolve(a, b, state_a.pressure, state_b.pressure)
                conflicts.append({
                    "pair":        (a, b),
                    "pressure_a":  round(state_a.pressure, 4),
                    "pressure_b":  round(state_b.pressure, 4),
                    "resolution":  resolution,
                })

        return {
            "arbitrated_at": time.time(),
            "conflict_count": len(conflicts),
            "conflicts":      conflicts,
        }

    def _resolve(self, a: str, b: str, pa: float, pb: float) -> str:
        """Priority rules for known conflict pairs."""
        rules = {
            ("stabilization", "budgeting"):          "stabilization_wins__defer_budget_cooling",
            ("replay_governance", "compression"):    "replay_governance_wins__defer_compression",
            ("compression", "continuity"):           "continuity_wins__partial_compression_only",
            ("load_balancing", "replay_governance"): "load_balancing_wins__throttle_hydration",
        }
        key = (a, b) if (a, b) in rules else (b, a)
        return rules.get(key, f"higher_pressure_wins: {'a' if pa >= pb else 'b'}")


# ── Coordination severity engine ───────────────────────────────────────────────

class CoordinationSeverityEngine:

    def classify(self, graph: CoordinationGraph, arbitration: Dict) -> str:
        active = graph.get_active_subsystems()
        n_active    = len(active)
        n_conflicts = arbitration.get("conflict_count", 0)

        # Detect rapid pressure oscillation (thrashing) from recent interaction log
        log = list(graph._interaction_log)[-20:]
        subsystem_changes: Dict[str, List[float]] = {}
        for entry in log:
            ss = entry["subsystem"]
            subsystem_changes.setdefault(ss, []).append(entry["pressure"])

        thrash_count = 0
        for ss, pressures in subsystem_changes.items():
            if len(pressures) >= 4:
                oscillations = sum(
                    1 for i in range(1, len(pressures))
                    if abs(pressures[i] - pressures[i - 1]) > 0.20
                )
                if oscillations >= 3:
                    thrash_count += 1

        if thrash_count >= 2:
            return CoordinationSeverity.THRASHING
        if n_conflicts >= 3:
            return CoordinationSeverity.CONFLICTING
        if n_conflicts >= 1 or n_active >= 4:
            return CoordinationSeverity.COMPETING
        return CoordinationSeverity.STABLE


# ── Unified coordination manager ──────────────────────────────────────────────

class RuntimeCoordinationManager:
    """Top-level facade for Z41A."""

    def __init__(self):
        self.graph      = CoordinationGraph()
        self.arbitrator = CoordinationArbitrator()
        self.severity   = CoordinationSeverityEngine()
        self._lock      = threading.Lock()

    def update(self, subsystem: str, pressure: float, action: str = "update") -> None:
        self.graph.update_subsystem(subsystem, pressure, action)

    def report(self) -> Dict:
        arb      = self.arbitrator.arbitrate(self.graph)
        sev      = self.severity.classify(self.graph, arb)
        snapshot = self.graph.snapshot()
        return {
            "reported_at":   time.time(),
            "severity":      sev,
            "arbitration":   arb,
            "graph_snapshot": snapshot,
        }


# Global singleton
_coordination_manager = RuntimeCoordinationManager()

def get_coordination_manager() -> RuntimeCoordinationManager:
    return _coordination_manager
