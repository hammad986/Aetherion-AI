"""
execution/adaptive_priority.py — Phase Z41D: Adaptive Priority Governance
=========================================================================
Classifies runtime chains by priority and dynamically rebalances based on
entropy, failures, escalation risk, and mission continuity.

Subsystems:
  • PriorityClassifier     — assigns CRITICAL / HIGH / NORMAL / BACKGROUND
  • DynamicPriorityEngine  — rebalances based on runtime conditions
  • LowPrioritySuppress    — suppresses background chains under stress
"""

import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("nexora.adaptive_priority")


# ── Priority levels ────────────────────────────────────────────────────────────

class Priority:
    CRITICAL   = "CRITICAL"
    HIGH       = "HIGH"
    NORMAL     = "NORMAL"
    BACKGROUND = "BACKGROUND"

    _RANK = {CRITICAL: 4, HIGH: 3, NORMAL: 2, BACKGROUND: 1}

    @classmethod
    def rank(cls, p: str) -> int:
        return cls._RANK.get(p, 2)

    @classmethod
    def from_score(cls, score: float) -> str:
        if score >= 0.80:
            return cls.CRITICAL
        if score >= 0.55:
            return cls.HIGH
        if score >= 0.30:
            return cls.NORMAL
        return cls.BACKGROUND


# ── Chain priority record ──────────────────────────────────────────────────────

@dataclass
class ChainPriority:
    chain_id:          str
    priority:          str
    score:             float
    entropy_factor:    float
    failure_factor:    float
    escalation_factor: float
    mission_factor:    float
    suppressed:        bool  = False
    last_updated:      float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return {
            "chain_id":          self.chain_id,
            "priority":          self.priority,
            "score":             round(self.score, 4),
            "entropy_factor":    round(self.entropy_factor, 4),
            "failure_factor":    round(self.failure_factor, 4),
            "escalation_factor": round(self.escalation_factor, 4),
            "mission_factor":    round(self.mission_factor, 4),
            "suppressed":        self.suppressed,
            "last_updated":      self.last_updated,
        }


# ── Priority classifier ────────────────────────────────────────────────────────

class PriorityClassifier:
    """
    Assigns initial priority to a runtime chain based on its characteristics.
    """

    def classify(
        self,
        chain_id: str,
        is_mission_critical: bool = False,
        failure_count: int = 0,
        replay_importance: float = 0.5,
        entropy: float = 0.0,
    ) -> ChainPriority:
        mission_factor    = 1.0 if is_mission_critical else max(0.0, replay_importance)
        failure_factor    = min(1.0, failure_count * 0.20)
        escalation_factor = min(1.0, entropy / 100.0)
        entropy_factor    = escalation_factor

        score = (
            mission_factor    * 0.40 +
            failure_factor    * 0.25 +
            escalation_factor * 0.20 +
            entropy_factor    * 0.15
        )
        priority = Priority.from_score(score)
        return ChainPriority(
            chain_id=chain_id,
            priority=priority,
            score=score,
            entropy_factor=entropy_factor,
            failure_factor=failure_factor,
            escalation_factor=escalation_factor,
            mission_factor=mission_factor,
        )


# ── Dynamic priority engine ────────────────────────────────────────────────────

class DynamicPriorityEngine:
    """
    Rebalances chain priorities based on current runtime conditions.
    Priority can be elevated or demoted dynamically.
    """

    def rebalance(
        self,
        cp: ChainPriority,
        current_entropy: float,
        current_resource_risk: float,
        coordination_severity: str,
    ) -> ChainPriority:
        """
        Returns an updated ChainPriority after rebalancing.
        Severity of coordination issues can elevate priority.
        """
        boost = 0.0

        # High entropy boosts priority of active chains
        if current_entropy > 60:
            boost += 0.15

        # High resource risk elevates critical/high chains further
        if current_resource_risk > 0.70 and cp.priority in (Priority.CRITICAL, Priority.HIGH):
            boost += 0.10

        # Coordination THRASHING → elevate everything by a tier
        if coordination_severity == "THRASHING":
            boost += 0.20

        new_score = min(1.0, cp.score + boost)
        new_priority = Priority.from_score(new_score)

        cp.score        = new_score
        cp.priority     = new_priority
        cp.last_updated = time.time()
        return cp


# ── Low-priority suppressor ────────────────────────────────────────────────────

class LowPrioritySuppress:
    """
    During runtime stress, suppresses hydration and processing
    for BACKGROUND and NORMAL chains.
    """

    SUPPRESS_ENTROPY_THRESHOLD  = 50.0
    SUPPRESS_RESOURCE_THRESHOLD = 0.60

    def evaluate(
        self,
        cp: ChainPriority,
        entropy: float,
        resource_risk: float,
    ) -> ChainPriority:
        should_suppress = (
            entropy > self.SUPPRESS_ENTROPY_THRESHOLD or
            resource_risk > self.SUPPRESS_RESOURCE_THRESHOLD
        ) and cp.priority == Priority.BACKGROUND

        if should_suppress and not cp.suppressed:
            cp.suppressed   = True
            cp.last_updated = time.time()
            logger.debug(
                "[PriorityGovernance] Suppressing BACKGROUND chain=%s "
                "(entropy=%.1f resource_risk=%.3f)",
                cp.chain_id, entropy, resource_risk
            )
        elif not should_suppress and cp.suppressed:
            cp.suppressed   = False
            cp.last_updated = time.time()

        return cp


# ── Adaptive priority manager ─────────────────────────────────────────────────

class AdaptivePriorityManager:
    """Top-level facade for Z41D."""

    def __init__(self):
        self._chains: Dict[str, ChainPriority] = {}
        self.classifier = PriorityClassifier()
        self.engine     = DynamicPriorityEngine()
        self.suppressor = LowPrioritySuppress()
        self._lock      = threading.Lock()

    def register(
        self,
        chain_id: str,
        is_mission_critical: bool = False,
        failure_count: int = 0,
        replay_importance: float = 0.5,
        entropy: float = 0.0,
    ) -> ChainPriority:
        cp = self.classifier.classify(chain_id, is_mission_critical, failure_count, replay_importance, entropy)
        with self._lock:
            self._chains[chain_id] = cp
        return cp

    def rebalance_all(
        self,
        entropy: float,
        resource_risk: float,
        coordination_severity: str = "STABLE",
    ) -> Dict:
        updated = []
        suppressed = 0
        with self._lock:
            chain_ids = list(self._chains.keys())

        for cid in chain_ids:
            with self._lock:
                cp = self._chains.get(cid)
            if cp is None:
                continue
            cp = self.engine.rebalance(cp, entropy, resource_risk, coordination_severity)
            cp = self.suppressor.evaluate(cp, entropy, resource_risk)
            with self._lock:
                self._chains[cid] = cp
            updated.append(cp.to_dict())
            if cp.suppressed:
                suppressed += 1

        return {
            "rebalanced_at":    time.time(),
            "chains_updated":   len(updated),
            "suppressed_count": suppressed,
            "chains":           sorted(updated, key=lambda c: c["score"], reverse=True),
        }

    def get(self, chain_id: str) -> Optional[ChainPriority]:
        with self._lock:
            return self._chains.get(chain_id)

    def snapshot(self) -> Dict:
        with self._lock:
            chains = [cp.to_dict() for cp in self._chains.values()]
        by_priority: Dict[str, int] = {}
        for c in chains:
            by_priority[c["priority"]] = by_priority.get(c["priority"], 0) + 1
        return {
            "chain_count":    len(chains),
            "by_priority":    by_priority,
            "suppressed":     sum(1 for c in chains if c["suppressed"]),
            "chains":         sorted(chains, key=lambda c: c["score"], reverse=True),
        }


# Global singleton
_priority_manager = AdaptivePriorityManager()

def get_priority_manager() -> AdaptivePriorityManager:
    return _priority_manager
