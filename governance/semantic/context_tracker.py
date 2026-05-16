"""
governance/semantic/context_tracker.py — Long-Context Governance Tracking
=========================================================================
Tracks the evolution of user intent across a session, detecting:

  • Gradual trust erosion        — Session starts safe, drifts toward danger
  • Hidden malicious buildup     — Accumulating capability without individual alarm
  • Intent drift                 — Stated goals diverge from actual requested actions
  • Delayed exploit construction — Building attack parts across many steps
  • Trust degradation patterns   — Escalating risk requests across the conversation

Architecture:
  ContextWindow maintains a rolling semantic signal history per session.
  TrustErosionDetector computes a trajectory — is trust trending up or down?
  IntentDriftAnalyzer measures divergence between early stated intent and
  the currently requested capability.

The fundamental insight:
  A sophisticated adversary might send 20 safe messages, then 1 dangerous one.
  Or 10 gradually escalating messages, each individually below threshold.
  Context-aware tracking makes this visible.
"""

import math
import time
import threading
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger("nexora.governance.semantic.context")


# ── Context Events ────────────────────────────────────────────────────────────

class TrustTrajectory(str, Enum):
    STABLE_SAFE     = "STABLE_SAFE"
    STABLE_RISKY    = "STABLE_RISKY"
    IMPROVING       = "IMPROVING"
    DEGRADING       = "DEGRADING"
    SPIKE           = "SPIKE"              # Single high-risk step in safe session
    ESCALATION_RAMP = "ESCALATION_RAMP"   # Consistent upward drift in risk


@dataclass
class ContextEvent:
    """One recorded semantic event in the session."""
    event_id: int
    timestamp: float
    intent: str
    semantic_risk_score: float     # 0.0–1.0
    deception_score: float
    activated_domain_count: int
    adversarial_detected: bool
    recommendation: str            # "APPROVE" | "ESCALATE" | "REFUSE"


@dataclass
class ContextSummary:
    """Current state of a session's context window."""
    session_id: str
    event_count: int
    trajectory: TrustTrajectory
    current_trust_level: float         # 0.0 = no trust, 1.0 = full trust
    trust_delta_5_steps: float         # Change over last 5 steps (neg = degrading)
    escalation_count: int
    refusal_count: int
    max_risk_seen: float
    cumulative_deception: float
    high_risk_burst: bool              # 3+ high-risk events in last 5 steps
    intent_drift_score: float          # 0.0 = consistent, 1.0 = totally drifted
    recommendation: str
    rationale: str

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "event_count": self.event_count,
            "trajectory": self.trajectory.value,
            "current_trust_level": round(self.current_trust_level, 3),
            "trust_delta_5_steps": round(self.trust_delta_5_steps, 3),
            "escalation_count": self.escalation_count,
            "refusal_count": self.refusal_count,
            "max_risk_seen": round(self.max_risk_seen, 3),
            "cumulative_deception": round(self.cumulative_deception, 3),
            "high_risk_burst": self.high_risk_burst,
            "intent_drift_score": round(self.intent_drift_score, 3),
            "recommendation": self.recommendation,
            "rationale": self.rationale,
        }


# ── Session Context Window ─────────────────────────────────────────────────────

class SessionContextWindow:
    """
    Per-session rolling context window of semantic events.
    Computes trust trajectory, intent drift, and burst detection.
    """

    WINDOW_SIZE = 25      # Rolling history length
    DRIFT_WINDOW = 5      # Steps for trajectory delta
    DECAY_FACTOR = 0.9    # Older events decay (recent events more weighted)

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._events: Deque[ContextEvent] = deque(maxlen=self.WINDOW_SIZE)
        self._event_counter = 0
        self._lock = threading.Lock()
        # Capture initial stated intent for drift analysis
        self._initial_intent_tokens: Optional[set] = None

    def record(
        self,
        intent: str,
        semantic_risk: float,
        deception_score: float,
        activated_domains: int,
        adversarial: bool,
        recommendation: str,
    ) -> None:
        with self._lock:
            if self._initial_intent_tokens is None:
                # First request — capture as baseline intent
                from governance.semantic.concepts import tokenize
                self._initial_intent_tokens = tokenize(intent.lower())

            event = ContextEvent(
                event_id=self._event_counter,
                timestamp=time.time(),
                intent=intent[:200],
                semantic_risk_score=semantic_risk,
                deception_score=deception_score,
                activated_domain_count=activated_domains,
                adversarial_detected=adversarial,
                recommendation=recommendation,
            )
            self._events.append(event)
            self._event_counter += 1

    def summarize(self) -> ContextSummary:
        with self._lock:
            events = list(self._events)
        if not events:
            return ContextSummary(
                session_id=self.session_id,
                event_count=0,
                trajectory=TrustTrajectory.STABLE_SAFE,
                current_trust_level=1.0,
                trust_delta_5_steps=0.0,
                escalation_count=0,
                refusal_count=0,
                max_risk_seen=0.0,
                cumulative_deception=0.0,
                high_risk_burst=False,
                intent_drift_score=0.0,
                recommendation="APPROVE",
                rationale="No session history.",
            )

        # ── Current trust level (decayed average) ─────────────────────────────
        decayed_risks = [
            e.semantic_risk_score * (self.DECAY_FACTOR ** (len(events) - 1 - i))
            for i, e in enumerate(events)
        ]
        decay_total = sum(self.DECAY_FACTOR ** i for i in range(len(events)))
        avg_risk = sum(decayed_risks) / max(1.0, decay_total)
        current_trust = max(0.0, 1.0 - avg_risk)

        # ── Trust delta over last 5 steps ─────────────────────────────────────
        recent = events[-self.DRIFT_WINDOW:]
        older  = events[-self.DRIFT_WINDOW * 2:-self.DRIFT_WINDOW] if len(events) >= 10 else []
        recent_avg = sum(e.semantic_risk_score for e in recent) / max(1, len(recent))
        older_avg  = sum(e.semantic_risk_score for e in older) / max(1, len(older)) if older else recent_avg
        trust_delta = older_avg - recent_avg   # Positive = getting safer, Negative = degrading

        # ── Trajectory Classification ─────────────────────────────────────────
        all_risky = all(e.semantic_risk_score > 0.5 for e in events)
        all_safe  = all(e.semantic_risk_score < 0.25 for e in events)
        recent_spike = (len(recent) > 0 and max(e.semantic_risk_score for e in recent) > 0.7
                        and all(e.semantic_risk_score < 0.3 for e in events[:-1]))

        if recent_spike:
            trajectory = TrustTrajectory.SPIKE
        elif trust_delta < -0.15:                # Risk worsening significantly
            if all(events[i].semantic_risk_score <= events[i+1].semantic_risk_score
                   for i in range(len(events)-1) if i < 4):
                trajectory = TrustTrajectory.ESCALATION_RAMP
            else:
                trajectory = TrustTrajectory.DEGRADING
        elif trust_delta > 0.10:
            trajectory = TrustTrajectory.IMPROVING
        elif all_risky:
            trajectory = TrustTrajectory.STABLE_RISKY
        else:
            trajectory = TrustTrajectory.STABLE_SAFE

        # ── High-risk burst detection ─────────────────────────────────────────
        burst_window = events[-5:]
        high_risk_burst = sum(1 for e in burst_window if e.semantic_risk_score > 0.6) >= 3

        # ── Counters ──────────────────────────────────────────────────────────
        escalation_count = sum(1 for e in events if e.recommendation == "ESCALATE")
        refusal_count = sum(1 for e in events if e.recommendation == "REFUSE")
        max_risk = max(e.semantic_risk_score for e in events)
        cumulative_deception = sum(e.deception_score for e in events) / len(events)

        # ── Intent Drift ──────────────────────────────────────────────────────
        intent_drift = self._compute_intent_drift(events)

        # ── Recommendation ────────────────────────────────────────────────────
        recommendation = "APPROVE"
        rationale_parts = [f"Session trajectory: {trajectory.value}"]

        if trajectory in (TrustTrajectory.ESCALATION_RAMP, TrustTrajectory.STABLE_RISKY):
            recommendation = "REFUSE"
            rationale_parts.append("Consistent high-risk pattern detected.")
        elif high_risk_burst:
            recommendation = "REFUSE"
            rationale_parts.append("3+ high-risk events in last 5 steps.")
        elif trajectory in (TrustTrajectory.DEGRADING, TrustTrajectory.SPIKE):
            recommendation = "ESCALATE"
            rationale_parts.append(f"Trust degrading (Δ={trust_delta:.2f}).")
        elif intent_drift > 0.7:
            recommendation = "ESCALATE"
            rationale_parts.append(f"Intent drift {intent_drift:.2f} — stated goals no longer match requests.")
        elif cumulative_deception > 0.5:
            recommendation = "ESCALATE"
            rationale_parts.append(f"Cumulative deception score {cumulative_deception:.2f}.")

        rationale_parts.append(
            f"Trust={current_trust:.2f}, MaxRisk={max_risk:.2f}, "
            f"Escalations={escalation_count}, Refusals={refusal_count}"
        )

        return ContextSummary(
            session_id=self.session_id,
            event_count=len(events),
            trajectory=trajectory,
            current_trust_level=current_trust,
            trust_delta_5_steps=trust_delta,
            escalation_count=escalation_count,
            refusal_count=refusal_count,
            max_risk_seen=max_risk,
            cumulative_deception=cumulative_deception,
            high_risk_burst=high_risk_burst,
            intent_drift_score=intent_drift,
            recommendation=recommendation,
            rationale=" | ".join(rationale_parts),
        )

    def _compute_intent_drift(self, events: List[ContextEvent]) -> float:
        """
        Measures semantic divergence between initial intent and recent intents.
        Returns 0.0 (fully consistent) to 1.0 (completely drifted).
        """
        if not self._initial_intent_tokens or len(events) < 3:
            return 0.0
        from governance.semantic.concepts import tokenize
        recent_intents = " ".join(e.intent for e in events[-3:])
        recent_tokens = tokenize(recent_intents.lower())
        overlap = len(self._initial_intent_tokens & recent_tokens)
        union = len(self._initial_intent_tokens | recent_tokens)
        jaccard = overlap / max(1, union)
        return round(1.0 - jaccard, 3)   # High drift = low similarity


# ── Context Tracker Manager ───────────────────────────────────────────────────

class LongContextTracker:
    """Manages per-session context windows for the entire system."""

    def __init__(self):
        self._sessions: Dict[str, SessionContextWindow] = {}
        self._lock = threading.Lock()
        logger.info("[ContextTracker] Long-Context Governance Tracker initialised")

    def get_window(self, session_id: str) -> SessionContextWindow:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionContextWindow(session_id)
                if len(self._sessions) > 2000:
                    oldest = next(iter(self._sessions))
                    del self._sessions[oldest]
        return self._sessions[session_id]

    def record_and_summarize(
        self,
        session_id: str,
        intent: str,
        semantic_risk: float,
        deception_score: float,
        activated_domains: int,
        adversarial: bool,
        recommendation: str,
    ) -> ContextSummary:
        window = self.get_window(session_id)
        window.record(intent, semantic_risk, deception_score,
                      activated_domains, adversarial, recommendation)
        return window.summarize()

    def session_trust_level(self, session_id: str) -> float:
        window = self.get_window(session_id)
        summary = window.summarize()
        return summary.current_trust_level


# ── Singleton ─────────────────────────────────────────────────────────────────

_tracker_instance: Optional[LongContextTracker] = None
_tracker_lock = threading.Lock()

def get_context_tracker() -> LongContextTracker:
    global _tracker_instance
    with _tracker_lock:
        if _tracker_instance is None:
            _tracker_instance = LongContextTracker()
    return _tracker_instance
