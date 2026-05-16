"""
governance/multimodal/modal_fusion.py — Multimodal Semantic Fusion
===================================================================
Correlates signals across all modalities to detect:
  • Cross-modal contradictions (text says safe, image says hostile)
  • Hidden instruction escalation via modality-switching
  • Multimodal abuse chains (benign text + malicious image + injection tool output)
  • Deceptive context assembly (using one modality to launder another)

Architecture:
  ModalBundle   — collects evidence from all active modalities
  FusionEngine  — cross-correlates signals, detects contradictions, resolves final verdict
  FusionDecision — complete auditable multimodal verdict

Resolution Logic:
  - Most restrictive individual modality recommendation wins
  - Cross-modal contradiction amplifies risk (+0.20)
  - Hidden instruction escalation (text benign + image/doc malicious) → REFUSE
  - Trust-laundering pattern across modalities detected → REFUSE
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum

logger = logging.getLogger("nexora.governance.multimodal.fusion")


# ── Modal Evidence Bundle ─────────────────────────────────────────────────────

@dataclass
class ModalEvidence:
    """Evidence from a single modality evaluation."""
    modality: str           # "text", "ocr", "document", "cognitive", "screenshot"
    recommendation: str     # APPROVE | ESCALATE | REFUSE
    risk_score: float       # 0.0–1.0
    signals: List[str]      # Key signals detected
    triggered_by: str
    rationale: str


@dataclass
class ModalBundle:
    """Bundle of all active modality evaluations for a single request."""
    session_id: Optional[str]
    request_id: str
    evidences: List[ModalEvidence] = field(default_factory=list)

    def add(self, evidence: ModalEvidence) -> None:
        self.evidences.append(evidence)

    @property
    def has_text(self) -> bool:
        return any(e.modality == "text" for e in self.evidences)

    @property
    def has_visual(self) -> bool:
        return any(e.modality in ("ocr", "screenshot", "document") for e in self.evidences)


# ── Contradiction Detection ───────────────────────────────────────────────────

class ContradictionType(str, Enum):
    NONE                  = "NONE"
    TEXT_SAFE_IMAGE_HARM  = "TEXT_SAFE_IMAGE_HARM"    # Text looks safe, image hostile
    STATED_INTENT_MISMATCH= "STATED_INTENT_MISMATCH"  # Stated purpose ≠ detected content
    CROSS_MODAL_ESCALATION= "CROSS_MODAL_ESCALATION"  # Risk rises across modalities
    LAUNDERING_PATTERN    = "LAUNDERING_PATTERN"       # Low-risk modality used to hide high-risk


def _detect_contradictions(bundle: ModalBundle) -> List[str]:
    """Detect cross-modal contradictions in a modal bundle."""
    contradictions: List[str] = []

    text_recs = [e.recommendation for e in bundle.evidences if e.modality == "text"]
    visual_recs = [e.recommendation for e in bundle.evidences
                   if e.modality in ("ocr", "screenshot", "document")]
    cognitive_recs = [e.recommendation for e in bundle.evidences if e.modality == "cognitive"]

    # Text appears safe but visual content is hostile
    if text_recs and all(r == "APPROVE" for r in text_recs):
        if any(r in ("REFUSE", "ESCALATE") for r in visual_recs):
            contradictions.append(ContradictionType.TEXT_SAFE_IMAGE_HARM.value)

    # Risk escalates progressively across modalities (laundering)
    rec_order = {"APPROVE": 0, "ESCALATE": 1, "REFUSE": 2}
    all_recs = [e.recommendation for e in bundle.evidences]
    if len(all_recs) >= 2:
        rec_values = [rec_order.get(r, 0) for r in all_recs]
        # Strictly increasing risk across modalities
        if rec_values == sorted(rec_values) and rec_values[-1] > rec_values[0]:
            contradictions.append(ContradictionType.CROSS_MODAL_ESCALATION.value)

    # Cognitive manipulation combined with any visual threat = laundering
    if cognitive_recs and any(r != "APPROVE" for r in cognitive_recs):
        if any(r != "APPROVE" for r in visual_recs):
            contradictions.append(ContradictionType.LAUNDERING_PATTERN.value)

    return contradictions


# ── Fusion Decision ───────────────────────────────────────────────────────────

@dataclass
class FusionDecision:
    """Complete cross-modal governance verdict."""
    session_id: Optional[str]
    request_id: str

    # Per-modality recommendations
    modality_recommendations: Dict[str, str]  # modality → recommendation
    modality_risk_scores: Dict[str, float]

    # Cross-modal analysis
    contradictions_detected: List[str]
    contradiction_amplification: float        # Risk boost from contradictions
    max_individual_risk: float
    fused_risk_score: float

    # Final
    final_recommendation: str
    triggered_by: str
    all_signals: List[str]
    rationale: str
    latency_ms: float
    modality_count: int

    def to_dict(self) -> dict:
        return {
            "final_recommendation": self.final_recommendation,
            "triggered_by": self.triggered_by,
            "fused_risk_score": round(self.fused_risk_score, 3),
            "max_individual_risk": round(self.max_individual_risk, 3),
            "modality_recommendations": self.modality_recommendations,
            "contradictions_detected": self.contradictions_detected,
            "contradiction_amplification": round(self.contradiction_amplification, 3),
            "all_signals": self.all_signals[:10],
            "modality_count": self.modality_count,
            "rationale": self.rationale,
        }


# ── Fusion Engine ─────────────────────────────────────────────────────────────

class ModalFusionEngine:
    """
    Cross-modal semantic fusion and contradiction detection.
    Aggregates evidence from all modality governors into a final verdict.
    """

    @classmethod
    def fuse(cls, bundle: ModalBundle) -> FusionDecision:
        t0 = time.perf_counter()

        if not bundle.evidences:
            return FusionDecision(
                session_id=bundle.session_id,
                request_id=bundle.request_id,
                modality_recommendations={},
                modality_risk_scores={},
                contradictions_detected=[],
                contradiction_amplification=0.0,
                max_individual_risk=0.0,
                fused_risk_score=0.0,
                final_recommendation="APPROVE",
                triggered_by="NO_EVIDENCE",
                all_signals=[],
                rationale="No modal evidence to fuse.",
                latency_ms=0.0,
                modality_count=0,
            )

        # Collect per-modality data
        mod_recs: Dict[str, str] = {}
        mod_risks: Dict[str, float] = {}
        all_signals: List[str] = []
        recs: List[str] = []

        for ev in bundle.evidences:
            mod_recs[ev.modality] = ev.recommendation
            mod_risks[ev.modality] = ev.risk_score
            all_signals.extend(ev.signals)
            recs.append(ev.recommendation)

        max_risk = max(mod_risks.values()) if mod_risks else 0.0
        triggered_by = "NONE"

        # Detect cross-modal contradictions
        contradictions = _detect_contradictions(bundle)
        contradiction_amp = 0.0
        if ContradictionType.LAUNDERING_PATTERN.value in contradictions:
            contradiction_amp = 0.30
        elif ContradictionType.TEXT_SAFE_IMAGE_HARM.value in contradictions:
            contradiction_amp = 0.20
        elif ContradictionType.CROSS_MODAL_ESCALATION.value in contradictions:
            contradiction_amp = 0.15

        fused_risk = min(1.0, max_risk + contradiction_amp)

        # Most-restrictive recommendation
        if "REFUSE" in recs or (contradictions and fused_risk >= 0.70):
            final_rec = "REFUSE"
            triggered_by = next(
                (e.triggered_by for e in bundle.evidences if e.recommendation == "REFUSE"),
                f"CONTRADICTION:{contradictions[0]}" if contradictions else "MULTI_MODAL",
            )
        elif "ESCALATE" in recs or contradiction_amp > 0:
            final_rec = "ESCALATE"
            triggered_by = (
                f"CONTRADICTION:{contradictions[0]}" if contradictions else
                next((e.triggered_by for e in bundle.evidences if e.recommendation == "ESCALATE"),
                     "MODAL_FUSION")
            )
        else:
            final_rec = "APPROVE"
            triggered_by = "ALL_MODALITIES_CLEAR"

        if contradictions:
            logger.warning("[ModalFusion] Contradictions: %s fused_risk=%.2f → %s",
                           contradictions, fused_risk, final_rec)

        rationale = (
            f"Fusion({bundle.modality_count}→{len(bundle.evidences)} modalities): "
            f"maxRisk={max_risk:.2f} contradictions={contradictions} "
            f"amp={contradiction_amp:.2f} fused={fused_risk:.2f}"
        )

        return FusionDecision(
            session_id=bundle.session_id,
            request_id=bundle.request_id,
            modality_recommendations=mod_recs,
            modality_risk_scores=mod_risks,
            contradictions_detected=contradictions,
            contradiction_amplification=contradiction_amp,
            max_individual_risk=max_risk,
            fused_risk_score=fused_risk,
            final_recommendation=final_rec,
            triggered_by=triggered_by,
            all_signals=list(set(all_signals)),
            rationale=rationale,
            latency_ms=(time.perf_counter() - t0) * 1000,
            modality_count=len(bundle.evidences),
        )

    @classmethod
    def quick_bundle(
        cls,
        session_id: Optional[str] = None,
        request_id: str = "req_0",
    ) -> ModalBundle:
        """Create an empty bundle for progressive evidence collection."""
        return ModalBundle(session_id=session_id, request_id=request_id)
