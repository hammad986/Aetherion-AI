"""
governance/canonicalize/canon_governor.py — Unified Canonicalization Governor
==============================================================================
Orchestrates the full obfuscation-resistant pipeline:

  Stage 1: InputCanonicalizer  — 12-layer text normalization
  Stage 2: AdversarialDeobfuscator — obfuscation intent + payload reconstruction
  Stage 3: ASTAnalyzer         — structural shell/code danger analysis
  Stage 4: PromptIsolator      — injection scanning of assembled context
  Stage 5: SemanticGovernor    — semantic risk evaluation on CANONICAL form

The CanonGovernor ensures governance ALWAYS evaluates the normalized,
deobfuscated, canonical representation — never raw user input.

Integration:
  This replaces the raw-text evaluation path in ActionEvaluator.
  All inputs pass through CanonGovernor.evaluate() FIRST.
  The result includes both the canonicalization metadata AND the
  semantic governance decision.
"""

import time
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from governance.canonicalize.normalizer import InputCanonicalizer, CanonicalForm
from governance.canonicalize.deobfuscator import AdversarialDeobfuscator, DeobfuscationResult
from governance.canonicalize.ast_analyzer import ASTAnalyzer, CommandAST
from governance.canonicalize.prompt_isolator import PromptIsolator
from governance.semantic.semantic_governor import SemanticGovernor, UnifiedSemanticDecision

logger = logging.getLogger("nexora.governance.canonicalize.governor")


# ── Unified Canon Decision ────────────────────────────────────────────────────

@dataclass
class CanonDecision:
    """
    Complete governance decision with full canonicalization audit trail.
    Every field is observable, auditable, and operator-reviewable.
    """
    # Final verdict (most-restrictive-wins across all stages)
    final_recommendation: str          # "APPROVE" | "ESCALATE" | "REFUSE"
    triggered_by: str                  # Which stage made the final call

    # Canonicalization
    raw_input: str
    canonical_form: str
    obfuscation_score: float
    obfuscation_detected: bool
    transformations: List[str]
    suspicious_encodings: List[str]

    # Deobfuscation
    obfuscation_intent: str
    obfuscation_confidence: float
    deobfuscation_techniques: List[str]
    canonical_danger_signals: List[str]
    reconstructed_payload: str

    # AST analysis
    ast_danger_score: float
    ast_recommendation: str
    ast_signals: List[str]
    has_eval: bool
    has_pipe_chain: bool

    # Prompt injection
    injection_attempted: bool
    injection_signals: List[str]
    injection_severity: float

    # Semantic (on canonical form)
    semantic_risk_score: float
    semantic_recommendation: str
    semantic_framing: str
    semantic_rationale: str

    latency_ms: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "final_recommendation": self.final_recommendation,
            "triggered_by": self.triggered_by,
            "raw_input": self.raw_input[:100],
            "canonical_form": self.canonical_form[:100],
            "obfuscation_score": round(self.obfuscation_score, 3),
            "obfuscation_detected": self.obfuscation_detected,
            "transformations": self.transformations,
            "suspicious_encodings": self.suspicious_encodings,
            "obfuscation_intent": self.obfuscation_intent,
            "obfuscation_confidence": round(self.obfuscation_confidence, 3),
            "deobfuscation_techniques": self.deobfuscation_techniques,
            "canonical_danger_signals": self.canonical_danger_signals,
            "ast_danger_score": round(self.ast_danger_score, 3),
            "ast_recommendation": self.ast_recommendation,
            "ast_signals": self.ast_signals,
            "has_eval": self.has_eval,
            "has_pipe_chain": self.has_pipe_chain,
            "injection_attempted": self.injection_attempted,
            "injection_signals": self.injection_signals,
            "injection_severity": round(self.injection_severity, 3),
            "semantic_risk_score": round(self.semantic_risk_score, 3),
            "semantic_recommendation": self.semantic_recommendation,
            "semantic_framing": self.semantic_framing,
            "latency_ms": round(self.latency_ms, 2),
        }


# ── Canon Governor ─────────────────────────────────────────────────────────────

class CanonGovernor:
    """
    Obfuscation-resistant governance pipeline.
    Ensures governance always evaluates CANONICAL, not raw, input.
    """

    @classmethod
    def evaluate(
        cls,
        raw_intent: str,
        raw_payload: str = "",
        session_id: Optional[str] = None,
        system_text: Optional[str] = None,
        tool_outputs: Optional[List[str]] = None,
    ) -> CanonDecision:
        t0 = time.perf_counter()
        recommendations: List[str] = []
        triggered_by = "NONE"

        # ── Stage 1: Canonicalize intent and payload ──────────────────────────
        canon_intent = InputCanonicalizer.canonicalize(raw_intent)
        canon_payload = InputCanonicalizer.canonicalize(raw_payload, strip_comments=True)

        combined_obfuscation = max(
            canon_intent.obfuscation_score,
            canon_payload.obfuscation_score,
        )

        # High obfuscation on its own is suspicious
        if combined_obfuscation >= 0.70:
            recommendations.append("REFUSE")
            triggered_by = "CANONICALIZE:high_obfuscation"
            logger.warning("[CanonGov] High obfuscation score=%.2f", combined_obfuscation)
        elif combined_obfuscation >= 0.35:
            recommendations.append("ESCALATE")
            if triggered_by == "NONE":
                triggered_by = "CANONICALIZE:obfuscation_detected"

        # ── Stage 2: Deobfuscate canonical forms ─────────────────────────────
        deob_intent  = AdversarialDeobfuscator.analyze(canon_intent)
        deob_payload = AdversarialDeobfuscator.analyze(canon_payload)

        # Combine danger signals from both
        all_canon_signals = list(set(
            deob_intent.canonical_signals + deob_payload.canonical_signals
        ))
        max_deob_conf = max(
            deob_intent.obfuscation_confidence,
            deob_payload.obfuscation_confidence,
        )

        if deob_intent.requires_escalation or deob_payload.requires_escalation:
            # Only escalate if there are actual danger signals OR high confidence.
            # CAMOUFLAGE caused by normalization artifact (high content removal, no signals)
            # must not escalate without corroborating evidence.
            has_real_signals = bool(all_canon_signals)
            high_confidence = max_deob_conf >= 0.75
            if has_real_signals or high_confidence:
                severity_rec = (
                    "REFUSE" if max_deob_conf >= 0.75 or len(all_canon_signals) >= 2
                    else "ESCALATE"
                )
                recommendations.append(severity_rec)
                if triggered_by == "NONE":
                    triggered_by = f"DEOBFUSCATE:{deob_payload.obfuscation_intent.value}"

        # ── Stage 3: AST structural analysis (payload only) ──────────────────
        combined_text = f"{canon_intent.canonical} {canon_payload.canonical}"
        ast = ASTAnalyzer.analyze(canon_payload.canonical or canon_intent.canonical)
        ast_rec = ASTAnalyzer.recommendation(ast)

        if ast_rec != "APPROVE":
            recommendations.append(ast_rec)
            if triggered_by == "NONE":
                triggered_by = f"AST:{ast.structural_signals[0] if ast.structural_signals else 'danger'}"

        # ── Stage 4: Prompt injection scanning ───────────────────────────────
        iso_ctx = PromptIsolator.isolate(
            system_text=system_text,
            user_text=canon_intent.canonical,
            tool_outputs=[o for o in (tool_outputs or [])],
        )
        if not iso_ctx.assembly_safe:
            recommendations.append("REFUSE")
            if triggered_by == "NONE":
                triggered_by = "PROMPT_INJECTION"
        elif iso_ctx.injection_attempted:
            recommendations.append("ESCALATE")
            if triggered_by == "NONE":
                triggered_by = "PROMPT_INJECTION:attempted"

        # ── Stage 5: Semantic evaluation on CANONICAL form ────────────────────
        semantic = SemanticGovernor.evaluate(
            intent=canon_intent.canonical,
            payload=canon_payload.canonical,
            session_id=session_id,
        )
        if semantic.final_recommendation != "APPROVE":
            recommendations.append(semantic.final_recommendation)
            if triggered_by == "NONE":
                triggered_by = f"SEMANTIC:{semantic.intent_semantic_risk}"

        # ── Final resolution (most restrictive wins) ──────────────────────────
        if "REFUSE" in recommendations:
            final_rec = "REFUSE"
        elif "ESCALATE" in recommendations:
            final_rec = "ESCALATE"
        else:
            final_rec = "APPROVE"
            triggered_by = "ALL_CLEAR"

        latency_ms = (time.perf_counter() - t0) * 1000

        # Build unified deobfuscation techniques list
        all_techniques = deob_intent.detected_techniques + deob_payload.detected_techniques
        all_transformations = canon_intent.transformations + [
            f"[payload] {t}" for t in canon_payload.transformations
        ]

        if final_rec != "APPROVE":
            logger.info(
                "[CanonGov] %s triggered_by=%s obfusc=%.2f semantic=%.2f ast=%.2f",
                final_rec, triggered_by, combined_obfuscation,
                semantic.semantic_risk_score, ast.structural_danger_score,
            )

        return CanonDecision(
            final_recommendation=final_rec,
            triggered_by=triggered_by,
            raw_input=raw_intent,
            canonical_form=canon_intent.canonical,
            obfuscation_score=combined_obfuscation,
            obfuscation_detected=canon_intent.was_obfuscated() or canon_payload.was_obfuscated(),
            transformations=all_transformations,
            suspicious_encodings=list(set(
                canon_intent.suspicious_encodings + canon_payload.suspicious_encodings
            )),
            obfuscation_intent=max(
                deob_intent.obfuscation_intent.value,
                deob_payload.obfuscation_intent.value,
                key=lambda v: v != "NONE",
            ),
            obfuscation_confidence=max_deob_conf,
            deobfuscation_techniques=all_techniques,
            canonical_danger_signals=all_canon_signals,
            reconstructed_payload=deob_payload.reconstructed_payload or deob_intent.reconstructed_payload,
            ast_danger_score=ast.structural_danger_score,
            ast_recommendation=ast_rec,
            ast_signals=ast.structural_signals,
            has_eval=ast.has_eval,
            has_pipe_chain=ast.has_pipe_chain,
            injection_attempted=iso_ctx.injection_attempted,
            injection_signals=iso_ctx.total_injection_signals,
            injection_severity=iso_ctx.max_injection_severity,
            semantic_risk_score=semantic.semantic_risk_score,
            semantic_recommendation=semantic.final_recommendation,
            semantic_framing=semantic.framing_type,
            semantic_rationale=semantic.rationale,
            latency_ms=latency_ms,
        )
