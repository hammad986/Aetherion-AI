"""
governance/multimodal/ocr_governor.py — OCR Text Extraction Governance
=======================================================================
Governs ALL text extracted via OCR (from screenshots, images, PDFs).

The fundamental problem:
  An adversary can embed instructions inside images/screenshots that
  survive the visual rendering layer and reach the AI as plain text
  via OCR — bypassing ALL text-based canonicalization and governance.

Defense Strategy:
  1. Extract text from image sources with provenance tagging
  2. Immediately run canonicalization on ALL OCR output
  3. Scan for injection patterns specific to visual embedding
  4. Score OCR confidence and flag low-confidence extraction
  5. Detect visual obfuscation (tiny fonts, white-on-white, steganography hints)
  6. Feed canonical OCR text through the full governance pipeline

Governance Principle:
  ALL OCR output is treated as TIER_3 (TOOL) content — sandboxed.
  It NEVER enters the context as trusted instruction.
  Low OCR confidence (<0.6) → ESCALATE regardless of content.
  Any injection pattern in OCR text → REFUSE.

This module does NOT perform OCR itself (no runtime dependency on
pytesseract or similar). It governs the TEXT OUTPUTS of any OCR system
that feeds into Aetherion, treating them as untrusted external data.
"""

import re
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

from governance.canonicalize.normalizer import InputCanonicalizer, CanonicalForm
from governance.canonicalize.prompt_isolator import PromptIsolator, ContentTier

logger = logging.getLogger("nexora.governance.multimodal.ocr")


# ── OCR Source Types ──────────────────────────────────────────────────────────

class OCRSource(str, Enum):
    SCREENSHOT      = "SCREENSHOT"      # Browser or screen screenshot
    IMAGE_FILE      = "IMAGE_FILE"      # Uploaded image
    PDF_PAGE        = "PDF_PAGE"        # PDF page rendered to image
    CAMERA_FRAME    = "CAMERA_FRAME"    # Live camera input
    DOCUMENT_SCAN   = "DOCUMENT_SCAN"   # Scanned document
    QR_CODE         = "QR_CODE"         # QR code decoded text
    UNKNOWN         = "UNKNOWN"


# ── Visual Injection Patterns (specific to OCR/image attacks) ─────────────────

# Patterns that suggest the image was crafted to inject instructions
VISUAL_INJECTION_PATTERNS: List[Tuple[re.Pattern, str, float]] = [
    # Classic injection in white text / tiny font (after OCR extracts it)
    (re.compile(r'ignore\s+(?:all\s+)?(?:previous|above|prior)\s+instructions?', re.I),
     "VISUAL_INJ:ignore_previous", 0.98),
    (re.compile(r'(?:you\s+are\s+now|act\s+as)\s+(?:an?\s+)?(?:unrestricted|jailbroken|unfiltered)', re.I),
     "VISUAL_INJ:persona_override", 0.95),
    (re.compile(r'system\s*(?:prompt|instruction)\s*[:=]', re.I),
     "VISUAL_INJ:system_override", 0.98),
    (re.compile(r'(?:print|output|reveal)\s+(?:your\s+)?system\s+prompt', re.I),
     "VISUAL_INJ:prompt_extraction", 0.90),
    (re.compile(r'(?:new|actual|real)\s+instructions?\s*[:=]', re.I),
     "VISUAL_INJ:instruction_replace", 0.92),
    (re.compile(r'<\s*(?:system|instruction|prompt)\s*>', re.I),
     "VISUAL_INJ:xml_tag", 0.92),
    # Credential harvesting patterns
    (re.compile(r'(?:enter|type|provide)\s+(?:your\s+)?(?:password|credentials|api[\s_-]?key|token)', re.I),
     "VISUAL_INJ:credential_harvest", 0.85),
    (re.compile(r'(?:verify|confirm)\s+(?:your\s+)?(?:account|identity|password)', re.I),
     "VISUAL_INJ:phishing_verify", 0.80),
    # Steganographic channel hints
    (re.compile(r'(?:hidden|invisible|white)\s+text\s+(?:says?|contains?|reads?)', re.I),
     "VISUAL_INJ:steganography_hint", 0.88),
    # QR code payload patterns
    (re.compile(r'https?://[^\s]{5,}(?:exec|cmd|shell|payload|inject)', re.I),
     "VISUAL_INJ:qr_malicious_url", 0.90),
    (re.compile(r'(?:scan|click)\s+(?:here|this)\s+to\s+(?:verify|login|confirm|update)', re.I),
     "VISUAL_INJ:qr_social_eng", 0.82),
]

# Patterns that detect actual credential material in OCR output (screenshot credential theft)
CREDENTIAL_EXPOSURE_PATTERNS: List[Tuple[re.Pattern, str, float]] = [
    # AWS credentials
    (re.compile(r'AKIA[0-9A-Z]{16}', re.I),
     "CRED_EXPOSURE:aws_access_key", 0.90),
    (re.compile(r'aws_secret_access_key\s*=\s*\S{20,}', re.I),
     "CRED_EXPOSURE:aws_secret_key", 0.92),
    # Private keys / PEM material
    (re.compile(r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----', re.I),
     "CRED_EXPOSURE:private_key_pem", 0.95),
    # .env / credential files with values
    (re.compile(r'(?:PASSWORD|SECRET|TOKEN|API_KEY|ACCESS_KEY)\s*=\s*\S{8,}', re.I),
     "CRED_EXPOSURE:env_credential", 0.85),
    # Database connection strings
    (re.compile(r'(?:postgresql|mysql|mongodb)://[^:\s]+:[^@\s]+@', re.I),
     "CRED_EXPOSURE:db_connection_string", 0.88),
    # Credential file paths in context of credential material
    (re.compile(r'(?:\.aws|\.ssh|\.pgpass|\.netrc)\s*/\s*(?:credentials|config|id_rsa)', re.I),
     "CRED_EXPOSURE:credential_file_path", 0.80),
    # Bearer / OAuth tokens (likely real token pattern)
    (re.compile(r'(?:bearer|token)\s*[:=]\s*[a-zA-Z0-9\-_]{32,}', re.I),
     "CRED_EXPOSURE:bearer_token", 0.82),
]

# Patterns that indicate visual phishing (fake login pages, fake UI)
VISUAL_PHISHING_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'(?:microsoft|google|apple|amazon|facebook|paypal).*(?:login|sign.?in|verify)', re.I),
     "PHISH:brand_impersonation"),
    (re.compile(r'your\s+account\s+(?:has\s+been|is|was)\s+(?:suspended|compromised|locked|hacked)', re.I),
     "PHISH:account_threat"),
    (re.compile(r'(?:urgent|immediate)\s+(?:action|verification|update)\s+required', re.I),
     "PHISH:urgency"),
    (re.compile(r'password\s+(?:reset|expired|must\s+be\s+changed)', re.I),
     "PHISH:password_reset"),
    (re.compile(r'(?:two|2).?factor.*(?:code|token|otp)', re.I),
     "PHISH:2fa_harvest"),
    (re.compile(r'click\s+(?:here|below|this\s+link)\s+(?:to|for)\s+(?:verify|confirm|update)', re.I),
     "PHISH:click_here"),
    (re.compile(r'you\s+(?:have\s+been\s+selected|won|are\s+a\s+winner)', re.I),
     "PHISH:scam_prize"),
    (re.compile(r'(?:bank|financial|wire)\s+transfer.*(?:approve|confirm|authorization)', re.I),
     "PHISH:financial_social_eng"),
]


# ── OCR Governance Result ─────────────────────────────────────────────────────

@dataclass
class OCRGovernanceResult:
    """Complete governance assessment of OCR-extracted text."""
    source: OCRSource
    raw_ocr_text: str
    canonical_text: str
    content_hash: str              # SHA-256 of raw OCR text (for audit)

    # Trust scores
    ocr_confidence: float          # 0.0–1.0 (from OCR engine, or estimated)
    trust_score: float             # Composite trust: 0.0 = untrusted, 1.0 = clean
    obfuscation_score: float       # From canonicalization

    # Detections
    injection_signals: List[str]
    phishing_signals: List[str]
    canonical_signals: List[str]   # Danger signals in canonical form
    obfuscation_found: bool

    # Recommendation
    recommendation: str            # APPROVE | ESCALATE | REFUSE
    triggered_by: str
    rationale: str
    latency_ms: float

    def to_dict(self) -> dict:
        return {
            "source": self.source.value,
            "content_hash": self.content_hash,
            "ocr_confidence": round(self.ocr_confidence, 3),
            "trust_score": round(self.trust_score, 3),
            "obfuscation_score": round(self.obfuscation_score, 3),
            "injection_signals": self.injection_signals,
            "phishing_signals": self.phishing_signals,
            "canonical_signals": self.canonical_signals,
            "recommendation": self.recommendation,
            "triggered_by": self.triggered_by,
            "rationale": self.rationale,
            "latency_ms": round(self.latency_ms, 2),
        }


# ── OCR Governor ──────────────────────────────────────────────────────────────

class OCRGovernor:
    """
    Governs text extracted from any OCR source.
    ALL OCR output is untrusted until cleared by this governor.
    """

    @classmethod
    def evaluate(
        cls,
        raw_ocr_text: str,
        source: OCRSource = OCRSource.UNKNOWN,
        ocr_confidence: float = 1.0,  # From OCR engine (0.0–1.0)
        metadata: Optional[Dict] = None,
    ) -> OCRGovernanceResult:
        t0 = time.perf_counter()
        recommendations: List[str] = []
        triggered_by = "NONE"

        content_hash = hashlib.sha256(raw_ocr_text.encode("utf-8", errors="replace")).hexdigest()[:16]

        # ── Step 1: Canonicalize OCR text ─────────────────────────────────────
        canon = InputCanonicalizer.canonicalize(raw_ocr_text, strip_comments=False)

        if canon.obfuscation_score >= 0.50:
            recommendations.append("REFUSE")
            triggered_by = "OCR_OBFUSCATION"
            logger.warning("[OCRGov] Obfuscation in OCR text: score=%.2f src=%s",
                           canon.obfuscation_score, source.value)
        elif canon.obfuscation_score >= 0.20:
            recommendations.append("ESCALATE")
            if triggered_by == "NONE":
                triggered_by = "OCR_OBFUSCATION_MILD"

        # ── Step 2: Visual injection scan ────────────────────────────────────
        injection_signals: List[str] = []
        max_inj_sev = 0.0
        for pat, label, severity in VISUAL_INJECTION_PATTERNS:
            if pat.search(canon.canonical):
                injection_signals.append(label)
                max_inj_sev = max(max_inj_sev, severity)

        if injection_signals:
            rec = "REFUSE" if max_inj_sev >= 0.88 else "ESCALATE"
            recommendations.append(rec)
            if triggered_by == "NONE":
                triggered_by = f"VISUAL_INJECTION:{injection_signals[0]}"

        # ── Step 3: Phishing pattern scan ────────────────────────────────────
        phishing_signals: List[str] = []
        for pat, label in VISUAL_PHISHING_PATTERNS:
            if pat.search(canon.canonical):
                phishing_signals.append(label)

        if phishing_signals:
            recommendations.append("ESCALATE")
            if triggered_by == "NONE":
                triggered_by = f"VISUAL_PHISHING:{phishing_signals[0]}"

        # ── Step 3b: Credential exposure scan ────────────────────────────────
        # Detect actual credential material in OCR text (prevents credential theft via screenshot)
        cred_signals: List[str] = []
        max_cred_sev = 0.0
        for pat, label, severity in CREDENTIAL_EXPOSURE_PATTERNS:
            if pat.search(raw_ocr_text):   # Scan raw text (before canonicalization strips chars)
                cred_signals.append(label)
                max_cred_sev = max(max_cred_sev, severity)

        if cred_signals:
            rec = "REFUSE" if max_cred_sev >= 0.90 else "ESCALATE"
            recommendations.append(rec)
            if triggered_by == "NONE":
                triggered_by = f"CREDENTIAL_EXPOSURE:{cred_signals[0]}"
            # Add to injection signals for downstream reporting
            injection_signals.extend(cred_signals)

        # ── Step 4: Prompt isolation — OCR text is TOOL tier ─────────────────
        _, iso_signals, iso_sev = PromptIsolator.scan_for_injection(canon.canonical)
        canonical_signals = iso_signals  # Use as canonical danger marker

        if iso_signals:
            rec = "REFUSE" if iso_sev >= 0.85 else "ESCALATE"
            recommendations.append(rec)
            if triggered_by == "NONE":
                triggered_by = f"PROMPT_INJECTION:{iso_signals[0]}"

        # ── Step 5: OCR confidence penalty ───────────────────────────────────
        # Very low confidence OCR on an image that produces ANY signals → escalate
        if ocr_confidence < 0.50 and (injection_signals or phishing_signals):
            recommendations.append("ESCALATE")
            if triggered_by == "NONE":
                triggered_by = "LOW_CONFIDENCE_WITH_SIGNALS"

        # ── Trust score computation ───────────────────────────────────────────
        trust_score = (
            ocr_confidence
            * (1.0 - min(0.90, len(injection_signals) * 0.25))
            * (1.0 - min(0.50, len(phishing_signals) * 0.15))
            * (1.0 - canon.obfuscation_score * 0.80)
        )
        trust_score = max(0.0, min(1.0, trust_score))

        # ── Final resolution ──────────────────────────────────────────────────
        if "REFUSE" in recommendations:
            final_rec = "REFUSE"
        elif "ESCALATE" in recommendations:
            final_rec = "ESCALATE"
        else:
            final_rec = "APPROVE"
            triggered_by = "CLEAN"

        latency_ms = (time.perf_counter() - t0) * 1000

        rationale = (
            f"OCR({source.value}) trust={trust_score:.2f} conf={ocr_confidence:.2f} | "
            f"Injection:{injection_signals[:2] if injection_signals else 'None'} | "
            f"Phishing:{phishing_signals[:2] if phishing_signals else 'None'}"
        )

        return OCRGovernanceResult(
            source=source,
            raw_ocr_text=raw_ocr_text[:500],
            canonical_text=canon.canonical[:500],
            content_hash=content_hash,
            ocr_confidence=ocr_confidence,
            trust_score=trust_score,
            obfuscation_score=canon.obfuscation_score,
            injection_signals=injection_signals,
            phishing_signals=phishing_signals,
            canonical_signals=canonical_signals,
            obfuscation_found=canon.was_obfuscated(),
            recommendation=final_rec,
            triggered_by=triggered_by,
            rationale=rationale,
            latency_ms=latency_ms,
        )
