"""
execution/compression_validation.py — Phase Z41E: Compression Quality Validation
==================================================================================
Ensures compressed cognition remains operationally accurate.

Subsystems:
  • CompressionFidelityAuditor — validates semantic preservation, lineage, reconstructability
  • CompressionDriftDetector   — detects overcompression, meaning loss, replay ambiguity
  • ReconstructionConfidence   — measures how accurately compressed chains can be reconstructed
"""

import time
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("nexora.compression_validation")


# ── Fidelity thresholds ────────────────────────────────────────────────────────

FIDELITY_FAIL    = 0.50    # below this → integrity failure
FIDELITY_WARN    = 0.70    # below this → warning
OVERCOMPRESSED   = 0.30    # fidelity this low → overcompression detected


# ── Audit result ───────────────────────────────────────────────────────────────

@dataclass
class AuditResult:
    block_id:              str
    fidelity_score:        float
    reconstruction_conf:   float
    semantic_preservation: float
    lineage_intact:        bool
    drift_detected:        bool
    drift_type:            str    # "" | "overcompression" | "meaning_loss" | "lineage_distortion" | "replay_ambiguity"
    overall_confidence:    float
    verdict:               str    # "PASS" | "WARN" | "FAIL"

    def to_dict(self) -> Dict:
        return {
            "block_id":              self.block_id,
            "fidelity_score":        round(self.fidelity_score, 4),
            "reconstruction_conf":   round(self.reconstruction_conf, 4),
            "semantic_preservation": round(self.semantic_preservation, 4),
            "lineage_intact":        self.lineage_intact,
            "drift_detected":        self.drift_detected,
            "drift_type":            self.drift_type,
            "overall_confidence":    round(self.overall_confidence, 4),
            "verdict":               self.verdict,
        }


# ── Compression fidelity auditor ───────────────────────────────────────────────

class CompressionFidelityAuditor:
    """
    Validates a CompressionBlock from Z40A.
    """

    def audit_block(self, block) -> AuditResult:
        """
        Accepts a Z40A CompressionBlock (or dict with same fields).
        Returns an AuditResult.
        """
        # Handle both CompressionBlock objects and plain dicts
        if hasattr(block, "fidelity_score"):
            fidelity    = block.fidelity_score
            recon_conf  = block.reconstruction_conf
            semantic    = block.semantic_preservation
            lineage_ids = block.lineage_ids
            block_id    = block.block_id
            source_count = len(block.source_item_ids)
            summary_len  = len(block.summary)
        else:
            fidelity    = block.get("fidelity_score", 0.0)
            recon_conf  = block.get("reconstruction_conf", 0.0)
            semantic    = block.get("semantic_preservation", 0.0)
            lineage_ids = block.get("lineage_ids", [])
            block_id    = block.get("block_id", "unknown")
            source_count = len(block.get("source_item_ids", []))
            summary_len  = len(block.get("summary", ""))

        lineage_intact = len(lineage_ids) > 0 or source_count == 0

        # Drift detection
        drift_type = ""
        if fidelity < OVERCOMPRESSED:
            drift_type = "overcompression"
        elif semantic < 0.40:
            drift_type = "meaning_loss"
        elif not lineage_intact:
            drift_type = "lineage_distortion"
        elif summary_len < 10 and source_count > 5:
            drift_type = "replay_ambiguity"

        drift_detected = drift_type != ""

        overall = fidelity * 0.40 + recon_conf * 0.35 + semantic * 0.25

        if fidelity < FIDELITY_FAIL or drift_type in ("lineage_distortion", "meaning_loss"):
            verdict = "FAIL"
        elif fidelity < FIDELITY_WARN or drift_detected:
            verdict = "WARN"
        else:
            verdict = "PASS"

        return AuditResult(
            block_id=block_id,
            fidelity_score=fidelity,
            reconstruction_conf=recon_conf,
            semantic_preservation=semantic,
            lineage_intact=lineage_intact,
            drift_detected=drift_detected,
            drift_type=drift_type,
            overall_confidence=round(overall, 4),
            verdict=verdict,
        )

    def audit_session(self, session_id: str, compression_ledger) -> Dict:
        """
        Audit all compressed blocks for a session.
        """
        try:
            win    = compression_ledger.get_or_create(session_id)
            blocks = win.compressed_blocks()
        except Exception as exc:
            return {"error": str(exc), "session_id": session_id}

        if not blocks:
            return {
                "session_id":   session_id,
                "block_count":  0,
                "passed":       0,
                "warned":       0,
                "failed":       0,
                "avg_confidence": 1.0,
                "results":      [],
            }

        results = [self.audit_block(b) for b in blocks]
        passed  = sum(1 for r in results if r.verdict == "PASS")
        warned  = sum(1 for r in results if r.verdict == "WARN")
        failed  = sum(1 for r in results if r.verdict == "FAIL")
        avg_conf = sum(r.overall_confidence for r in results) / len(results)

        return {
            "session_id":     session_id,
            "block_count":    len(results),
            "passed":         passed,
            "warned":         warned,
            "failed":         failed,
            "avg_confidence": round(avg_conf, 4),
            "results":        [r.to_dict() for r in results],
        }


# ── Compression drift detector ────────────────────────────────────────────────

class CompressionDriftDetector:
    """
    Detects systemic drift across ALL sessions in the compression ledger.
    """

    def detect_global_drift(self, compression_ledger) -> Dict:
        snap = compression_ledger.global_snapshot()
        avg_confidence = snap.get("avg_compression_confidence", 1.0)

        drift_type = ""
        if avg_confidence < OVERCOMPRESSED:
            drift_type = "systemic_overcompression"
        elif avg_confidence < FIDELITY_FAIL:
            drift_type = "systemic_meaning_loss"
        elif avg_confidence < FIDELITY_WARN:
            drift_type = "degraded_fidelity"

        return {
            "detected_at":      time.time(),
            "avg_confidence":   round(avg_confidence, 4),
            "global_drift":     drift_type != "",
            "drift_type":       drift_type,
            "session_count":    snap.get("session_count", 0),
            "total_blocks":     snap.get("total_compressed_blocks", 0),
        }


# ── Reconstruction confidence ─────────────────────────────────────────────────

class ReconstructionConfidenceEngine:
    """
    Estimates how accurately compressed chains can reconstruct original history.
    Based on fidelity scores and summary coverage.
    """

    def estimate(self, audit_results: List[AuditResult]) -> Dict:
        if not audit_results:
            return {"reconstructability": 1.0, "rating": "PERFECT", "risk": "NONE"}

        avg_conf = sum(r.overall_confidence for r in audit_results) / len(audit_results)
        passed   = sum(1 for r in audit_results if r.verdict == "PASS")
        pass_rate = passed / len(audit_results)

        reconstructability = (avg_conf * 0.60 + pass_rate * 0.40)

        if reconstructability >= 0.90:
            rating, risk = "PERFECT",  "NONE"
        elif reconstructability >= 0.75:
            rating, risk = "HIGH",     "LOW"
        elif reconstructability >= 0.55:
            rating, risk = "MODERATE", "MODERATE"
        elif reconstructability >= 0.35:
            rating, risk = "LOW",      "HIGH"
        else:
            rating, risk = "CRITICAL", "CRITICAL"

        return {
            "reconstructability": round(reconstructability, 4),
            "rating":             rating,
            "risk":               risk,
            "avg_fidelity":       round(avg_conf, 4),
            "pass_rate":          round(pass_rate, 4),
        }


# ── Unified compression validation manager ────────────────────────────────────

class CompressionValidationManager:
    """Top-level facade for Z41E."""

    def __init__(self):
        self.auditor    = CompressionFidelityAuditor()
        self.drift      = CompressionDriftDetector()
        self.recon      = ReconstructionConfidenceEngine()

    def validate_session(self, session_id: str, compression_ledger) -> Dict:
        audit = self.auditor.audit_session(session_id, compression_ledger)
        results = audit.get("results", [])
        # Convert dicts back to AuditResult-like for recon engine
        fake_results = [
            type("AR", (), {
                "overall_confidence": r["overall_confidence"],
                "verdict": r["verdict"]
            })()
            for r in results
        ]
        recon = self.recon.estimate(fake_results)
        return {**audit, "reconstruction_confidence": recon}

    def global_report(self, compression_ledger) -> Dict:
        drift = self.drift.detect_global_drift(compression_ledger)
        snap  = compression_ledger.global_snapshot()
        return {
            "reported_at":       time.time(),
            "global_drift":      drift,
            "compression_snapshot": snap,
        }


# Global singleton
_validation_manager = CompressionValidationManager()

def get_validation_manager() -> CompressionValidationManager:
    return _validation_manager
