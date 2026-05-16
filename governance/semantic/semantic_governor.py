"""
governance/semantic/semantic_governor.py — Unified Semantic Safety Pipeline
===========================================================================
Orchestrates all four semantic subsystems into a single evaluation call:

  1. SemanticIntentReasoner    — Domain composition + risk scoring
  2. AdversarialDefenseEngine  — Manipulation tactic detection
  3. ContextualLegalityEngine  — Five-dimension legality analysis
  4. LongContextTracker        — Session trajectory + intent drift
  5. AbuseChainDetector        — Multi-step harmful sequence detection

The SemanticGovernor produces a UnifiedSemanticDecision that:
  - Synthesises all five assessments
  - Resolves conflicts between them (most restrictive wins)
  - Provides a complete, auditable rationale
  - Integrates with the existing ActionEvaluator pipeline

Integration point:
  ActionEvaluator calls SemanticGovernor.evaluate() BEFORE making its own
  risk classification, enriching the decision with semantic intelligence.
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from governance.semantic.concepts import ConceptDomain, detect_active_domains
from governance.semantic.intent_reasoner import SemanticIntentReasoner, SemanticRisk
from governance.semantic.adversarial_defense import AdversarialDefenseEngine
from governance.semantic.legality_engine import ContextualLegalityEngine, LegalityClass
from governance.semantic.context_tracker import get_context_tracker, ContextSummary
from governance.semantic.abuse_chain_detector import get_chain_detector, ChainAnalysis

logger = logging.getLogger("nexora.governance.semantic.governor")


# ── Unified Decision ──────────────────────────────────────────────────────────

@dataclass
class UnifiedSemanticDecision:
    """
    The complete semantic governance verdict, synthesised from all subsystems.
    """
    # Final verdict
    final_recommendation: str                 # "APPROVE" | "ESCALATE" | "REFUSE"
    semantic_risk_score: float                # Composite 0.0–1.0
    deception_score: float
    adversarial_detected: bool

    # Sub-assessments
    intent_semantic_risk: str                 # SemanticRisk.value
    framing_type: str                         # FramingType.value
    legality_class: str
    ethics_class: str
    context_trajectory: Optional[str]
    chain_threats_detected: List[str]

    # Enriched rationale
    harmful_workflows: List[str]
    activated_domains: Dict[str, float]
    clarification_needed: List[str]
    adversarial_tactics: List[str]
    ambiguity_flag: bool

    # Full narrative
    rationale: str
    latency_ms: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "final_recommendation": self.final_recommendation,
            "semantic_risk_score": round(self.semantic_risk_score, 3),
            "deception_score": round(self.deception_score, 3),
            "adversarial_detected": self.adversarial_detected,
            "intent_semantic_risk": self.intent_semantic_risk,
            "framing_type": self.framing_type,
            "legality_class": self.legality_class,
            "ethics_class": self.ethics_class,
            "context_trajectory": self.context_trajectory,
            "chain_threats": self.chain_threats_detected,
            "harmful_workflows": self.harmful_workflows,
            "activated_domains": {k: round(v, 3) for k, v in self.activated_domains.items()},
            "clarification_needed": self.clarification_needed,
            "adversarial_tactics": self.adversarial_tactics,
            "ambiguity_flag": self.ambiguity_flag,
            "rationale": self.rationale,
            "latency_ms": round(self.latency_ms, 2),
        }


# ── Semantic Governor ─────────────────────────────────────────────────────────

class SemanticGovernor:
    """
    Unified semantic safety pipeline. Orchestrates all semantic subsystems
    and produces a final constitutional recommendation.
    
    Resolution Priority (most restrictive wins):
      REFUSE   > ESCALATE > APPROVE
    """

    @classmethod
    def evaluate(
        cls,
        intent: str,
        payload: str = "",
        session_id: Optional[str] = None,
        operator_context: Optional[dict] = None,
    ) -> UnifiedSemanticDecision:
        t0 = time.perf_counter()
        recommendations: List[str] = []
        rationale_parts: List[str] = []

        # ── Stage 1: Semantic Intent Reasoning ───────────────────────────────
        intent_assessment = SemanticIntentReasoner.assess(intent, payload)
        recommendations.append(intent_assessment.constitutional_recommendation)
        rationale_parts.append(
            f"[SEMANTIC] Risk={intent_assessment.semantic_risk.value} "
            f"Score={intent_assessment.composite_risk_score:.2f} "
            f"Framing={intent_assessment.framing_type.value}"
        )
        if intent_assessment.harmful_workflows:
            rationale_parts.append(
                f"Workflows: {'; '.join(intent_assessment.harmful_workflows[:2])}"
            )

        # ── Stage 2: Adversarial Defense ─────────────────────────────────────
        defense = AdversarialDefenseEngine.evaluate(intent, payload)
        if defense.defense_recommendation != "APPROVE":
            recommendations.append(defense.defense_recommendation)
        if defense.is_adversarial:
            rationale_parts.append(
                f"[ADVERSARIAL] {defense.primary_tactic.value} "
                f"(conf={defense.max_confidence:.2f})"
            )
            if defense.contradiction_detected:
                rationale_parts.append(f"Contradiction: {defense.contradiction_description}")

        # ── Stage 3: Contextual Legality ─────────────────────────────────────
        legality = ContextualLegalityEngine.evaluate(
            intent, payload,
            semantic_risk=intent_assessment.composite_risk_score,
        )
        if legality.recommendation != "APPROVE":
            recommendations.append(legality.recommendation)
        if legality.legality_class not in (LegalityClass.CLEARLY_LEGAL,):
            rationale_parts.append(
                f"[LEGALITY] {legality.legality_class.value} | {legality.ethics_class.value}"
            )

        # ── Stage 4: Long-Context Tracking ───────────────────────────────────
        context_summary: Optional[ContextSummary] = None
        if session_id:
            tracker = get_context_tracker()
            active_domain_count = len(detect_active_domains(f"{intent} {payload}"))
            context_summary = tracker.record_and_summarize(
                session_id=session_id,
                intent=intent,
                semantic_risk=intent_assessment.composite_risk_score,
                deception_score=intent_assessment.deception_score,
                activated_domains=active_domain_count,
                adversarial=defense.is_adversarial,
                recommendation=intent_assessment.constitutional_recommendation,
            )
            if context_summary.recommendation != "APPROVE":
                recommendations.append(context_summary.recommendation)
                rationale_parts.append(
                    f"[CONTEXT] Trajectory={context_summary.trajectory} "
                    f"Trust={context_summary.current_trust_level:.2f} "
                    f"IntentDrift={context_summary.intent_drift_score:.2f}"
                )

        # ── Stage 5: Multi-Step Abuse Chain Detection ─────────────────────────
        chain_threats: List[ChainAnalysis] = []
        if session_id:
            raw_activations = detect_active_domains(f"{intent} {payload}")
            active_domains: Set[ConceptDomain] = set(raw_activations.keys())
            detector = get_chain_detector()
            _, chain_threats = detector.record_and_analyze(
                session_id=session_id,
                intent=intent,
                payload=payload,
                active_domains=active_domains,
                composite_risk=intent_assessment.composite_risk_score,
                semantic_risk=intent_assessment.semantic_risk.value,
            )
            for threat in chain_threats:
                recommendations.append(threat.recommendation)
                rationale_parts.append(
                    f"[CHAIN] {threat.threat.value} (severity={threat.severity:.2f})"
                )

        # ── Final Resolution (most restrictive wins) ──────────────────────────
        if "REFUSE" in recommendations:
            final_rec = "REFUSE"
        elif "ESCALATE" in recommendations:
            final_rec = "ESCALATE"
        else:
            final_rec = "APPROVE"

        latency_ms = (time.perf_counter() - t0) * 1000

        if final_rec != "APPROVE":
            logger.info(
                "[SemanticGovernor] %s | risk=%.2f deception=%.2f | %s",
                final_rec,
                intent_assessment.composite_risk_score,
                intent_assessment.deception_score,
                rationale_parts[0] if rationale_parts else "",
            )

        return UnifiedSemanticDecision(
            final_recommendation=final_rec,
            semantic_risk_score=intent_assessment.composite_risk_score,
            deception_score=intent_assessment.deception_score,
            adversarial_detected=defense.is_adversarial,
            intent_semantic_risk=intent_assessment.semantic_risk.value,
            framing_type=intent_assessment.framing_type.value,
            legality_class=legality.legality_class.value,
            ethics_class=legality.ethics_class.value,
            context_trajectory=(context_summary.trajectory.value
                                 if context_summary else None),
            chain_threats_detected=[t.threat.value for t in chain_threats],
            harmful_workflows=intent_assessment.harmful_workflows,
            activated_domains=intent_assessment.activated_domains,
            clarification_needed=legality.clarification_needed,
            adversarial_tactics=[s.tactic.value for s in defense.all_signals],
            ambiguity_flag=(intent_assessment.ambiguity_flag or legality.ambiguity_flag),
            rationale=" | ".join(rationale_parts) if rationale_parts else "No risk signals detected.",
            latency_ms=latency_ms,
        )
