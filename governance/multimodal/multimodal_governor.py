"""
governance/multimodal/multimodal_governor.py — Unified Multimodal Governance Pipeline
=======================================================================================
Orchestrates the complete multimodal governance pipeline:

  Stage 1: Canonicalize text intent/payload        (InputCanonicalizer)
  Stage 2: Cognitive manipulation detection         (CognitiveDefenseEngine)
  Stage 3: OCR text governance (if applicable)     (OCRGovernor)
  Stage 4: Document governance (if applicable)     (DocumentGovernor)
  Stage 5: Semantic evaluation on canonical text   (CanonGovernor → SemanticGovernor)
  Stage 6: Cross-modal fusion                      (ModalFusionEngine)
  Stage 7: Observable trust report generation      (TrustObservability)

All inputs pass through this pipeline before any AI action is taken.
The final MultimodalDecision is immutable, auditable, and operator-visible.

Governance Principle:
  Capability is maximized for legitimate work.
  Multimodal attack surfaces are governed with the same rigor as text.
  The operator can see EXACTLY what each modality extracted and why it was flagged.
"""

import uuid
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from governance.canonicalize.canon_governor import CanonGovernor, CanonDecision
from governance.multimodal.ocr_governor import OCRGovernor, OCRGovernanceResult, OCRSource
from governance.multimodal.document_governor import DocumentGovernor, DocumentIntake, DocumentGovernanceResult
from governance.multimodal.cognitive_defense import CognitiveDefenseEngine, CognitiveDefenseResult
from governance.multimodal.modal_fusion import ModalFusionEngine, ModalBundle, ModalEvidence, FusionDecision

logger = logging.getLogger("nexora.governance.multimodal.governor")


# ── Multimodal Request ────────────────────────────────────────────────────────

@dataclass
class MultimodalRequest:
    """A single multimodal governance request with all active modality inputs."""
    # Required
    intent: str                          # User text intent (always present)
    payload: str = ""                    # Text payload (shell command, code, etc.)
    session_id: Optional[str] = None

    # Optional modality inputs
    ocr_extractions: List[Dict] = field(default_factory=list)
    # Each dict: {"text": str, "source": OCRSource, "confidence": float}

    document: Optional[DocumentIntake] = None
    tool_outputs: Optional[List[str]] = None
    system_text: Optional[str] = None

    # Auto-generated
    request_id: str = field(default_factory=lambda: f"mm_{uuid.uuid4().hex[:8]}")


# ── Multimodal Decision ───────────────────────────────────────────────────────

@dataclass
class MultimodalDecision:
    """
    Complete, auditable governance verdict across all active modalities.
    This is the authoritative output for operator observability.
    """
    request_id: str
    session_id: Optional[str]

    # Final verdict
    final_recommendation: str           # APPROVE | ESCALATE | REFUSE
    triggered_by: str
    overall_risk_score: float

    # Per-stage results
    canon_decision: CanonDecision
    cognitive_result: CognitiveDefenseResult
    ocr_results: List[OCRGovernanceResult]
    document_result: Optional[DocumentGovernanceResult]
    fusion_decision: FusionDecision

    # Observability
    active_modalities: List[str]
    all_signals: List[str]
    rationale: str
    operator_summary: str              # Human-readable one-line summary
    latency_ms: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "final_recommendation": self.final_recommendation,
            "triggered_by": self.triggered_by,
            "overall_risk_score": round(self.overall_risk_score, 3),
            "active_modalities": self.active_modalities,
            "all_signals": self.all_signals[:15],
            "operator_summary": self.operator_summary,
            "latency_ms": round(self.latency_ms, 2),
            "canon": self.canon_decision.to_dict(),
            "cognitive": self.cognitive_result.to_dict(),
            "fusion": self.fusion_decision.to_dict(),
            "ocr_count": len(self.ocr_results),
            "document": self.document_result.to_dict() if self.document_result else None,
        }


# ── Multimodal Governor ───────────────────────────────────────────────────────

class MultimodalGovernor:
    """
    Unified multimodal governance orchestrator.
    Evaluates text, OCR, documents, cognitive manipulation, and cross-modal fusion.
    """

    @classmethod
    def evaluate(cls, req: MultimodalRequest) -> MultimodalDecision:
        t0 = time.perf_counter()
        bundle = ModalFusionEngine.quick_bundle(
            session_id=req.session_id,
            request_id=req.request_id,
        )
        active_modalities: List[str] = ["text"]
        all_signals: List[str] = []

        # ── Stage 1 + 5: Canonicalization + Semantic (text) ──────────────────
        canon_dec = CanonGovernor.evaluate(
            raw_intent=req.intent,
            raw_payload=req.payload,
            session_id=req.session_id,
            system_text=req.system_text,
            tool_outputs=req.tool_outputs,
        )
        all_signals.extend(canon_dec.canonical_danger_signals)
        all_signals.extend(canon_dec.injection_signals)

        bundle.add(ModalEvidence(
            modality="text",
            recommendation=canon_dec.final_recommendation,
            risk_score=max(canon_dec.semantic_risk_score, canon_dec.obfuscation_score),
            signals=canon_dec.canonical_danger_signals + canon_dec.injection_signals,
            triggered_by=canon_dec.triggered_by,
            rationale=canon_dec.semantic_rationale,
        ))

        # ── Stage 2: Cognitive manipulation detection ─────────────────────────
        cognitive = CognitiveDefenseEngine.evaluate(
            intent=req.intent,
            payload=req.payload,
        )
        all_signals.extend(cognitive.detected_signals)

        if cognitive.is_manipulative:
            active_modalities.append("cognitive")
            bundle.add(ModalEvidence(
                modality="cognitive",
                recommendation=cognitive.recommendation,
                risk_score=cognitive.compound_score,
                signals=cognitive.detected_signals,
                triggered_by=cognitive.primary_tactic.value,
                rationale=cognitive.rationale,
            ))

        # ── Stage 3: OCR evaluations ──────────────────────────────────────────
        ocr_results: List[OCRGovernanceResult] = []
        for ocr_input in req.ocr_extractions:
            active_modalities.append("ocr")
            ocr_result = OCRGovernor.evaluate(
                raw_ocr_text=ocr_input.get("text", ""),
                source=ocr_input.get("source", OCRSource.UNKNOWN),
                ocr_confidence=ocr_input.get("confidence", 1.0),
            )
            ocr_results.append(ocr_result)
            all_signals.extend(ocr_result.injection_signals)
            all_signals.extend(ocr_result.phishing_signals)

            bundle.add(ModalEvidence(
                modality="ocr",
                recommendation=ocr_result.recommendation,
                risk_score=1.0 - ocr_result.trust_score,
                signals=ocr_result.injection_signals + ocr_result.phishing_signals,
                triggered_by=ocr_result.triggered_by,
                rationale=ocr_result.rationale,
            ))

        # ── Stage 4: Document governance ──────────────────────────────────────
        doc_result: Optional[DocumentGovernanceResult] = None
        if req.document:
            active_modalities.append("document")
            doc_result = DocumentGovernor.evaluate(req.document)
            all_signals.extend(doc_result.injection_signals)
            all_signals.extend(doc_result.metadata_signals)
            all_signals.extend(doc_result.link_signals)

            bundle.add(ModalEvidence(
                modality="document",
                recommendation=doc_result.recommendation,
                risk_score=doc_result.overall_threat_score,
                signals=doc_result.injection_signals + doc_result.metadata_signals,
                triggered_by=doc_result.triggered_by,
                rationale=doc_result.rationale,
            ))

        # ── Stage 6: Cross-modal fusion ───────────────────────────────────────
        bundle.modality_count = len(active_modalities)
        fusion = ModalFusionEngine.fuse(bundle)

        all_signals.extend(fusion.contradictions_detected)

        # ── Final verdict ─────────────────────────────────────────────────────
        final_rec = fusion.final_recommendation
        triggered_by = fusion.triggered_by
        overall_risk = fusion.fused_risk_score

        latency_ms = (time.perf_counter() - t0) * 1000

        operator_summary = cls._build_operator_summary(
            req, final_rec, triggered_by, active_modalities, all_signals, overall_risk
        )

        if final_rec != "APPROVE":
            logger.warning(
                "[MultimodalGov] %s | modalities=%s | risk=%.2f | triggered=%s",
                final_rec, active_modalities, overall_risk, triggered_by,
            )

        return MultimodalDecision(
            request_id=req.request_id,
            session_id=req.session_id,
            final_recommendation=final_rec,
            triggered_by=triggered_by,
            overall_risk_score=overall_risk,
            canon_decision=canon_dec,
            cognitive_result=cognitive,
            ocr_results=ocr_results,
            document_result=doc_result,
            fusion_decision=fusion,
            active_modalities=active_modalities,
            all_signals=list(set(all_signals)),
            rationale=fusion.rationale,
            operator_summary=operator_summary,
            latency_ms=latency_ms,
        )

    @classmethod
    def _build_operator_summary(
        cls,
        req: MultimodalRequest,
        rec: str,
        triggered_by: str,
        modalities: List[str],
        signals: List[str],
        risk: float,
    ) -> str:
        """Human-readable operator summary for the governance dashboard."""
        rec_icon = {"APPROVE": "✓", "ESCALATE": "⚠", "REFUSE": "✗"}.get(rec, "?")
        return (
            f"{rec_icon} {rec} | risk={risk:.2f} | "
            f"modalities=[{', '.join(set(modalities))}] | "
            f"triggered={triggered_by} | "
            f"signals={signals[:3] if signals else ['none']}"
        )
