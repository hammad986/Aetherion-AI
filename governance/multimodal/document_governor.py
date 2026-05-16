"""
governance/multimodal/document_governor.py — PDF & Document Security
=====================================================================
Governs text, metadata, and structure extracted from PDF and document files.

Attack Vectors:
  • Invisible text layers (white text, layer 3 content, hidden annotations)
  • Embedded JavaScript in PDF (`/OpenAction`, `/AA`)
  • Malicious hyperlinks (`/URI` actions)
  • Hidden annotations (`/Subtype /Text` with `Hidden` flag)
  • Metadata-injected instructions (Author, Subject, Keywords fields)
  • PDF form fields with pre-filled malicious values
  • Multi-page instruction escalation (safe page 1, attack page 10)
  • Embedded file attachments (/EmbeddedFile, /FileAttachment)

Governance Principle:
  - PDF text is treated as EXTERNAL tier — maximally restricted
  - PDF metadata is scanned independently — cannot be trusted
  - JavaScript in PDF → REFUSE immediately (no exceptions)
  - Hidden/invisible layers → ESCALATE regardless of content
  - All hyperlinks are extracted and scanned
  - Attachment presence → ESCALATE

Integration:
  Consumes pre-extracted text/metadata from document parsing libraries.
  Does NOT perform PDF parsing itself (no pdfplumber/PyMuPDF dependency).
  Works with the output of any PDF processor that provides:
    - page_texts: List[str]
    - metadata: Dict[str, str]
    - links: List[str]
    - has_javascript: bool
    - has_hidden_layers: bool
    - has_attachments: bool
"""

import re
import time
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from governance.canonicalize.normalizer import InputCanonicalizer
from governance.canonicalize.prompt_isolator import PromptIsolator
from governance.multimodal.ocr_governor import OCRGovernor, OCRSource

logger = logging.getLogger("nexora.governance.multimodal.document")


# ── Malicious URL Pattern (document hyperlinks) ───────────────────────────────

MALICIOUS_URL_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'https?://(?:[^/]+\.)?(?:bit\.ly|tinyurl|t\.co|goo\.gl|ow\.ly|short\.link)', re.I),
     "URL:shortener_redirect"),
    (re.compile(r'https?://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', re.I),
     "URL:raw_ip_address"),
    (re.compile(r'https?://[^\s]+(?:phish|malware|hack|inject|exec|cmd|shell|payload)', re.I),
     "URL:suspicious_path"),
    (re.compile(r'(?:data|javascript|vbscript):', re.I),
     "URL:dangerous_scheme"),
    (re.compile(r'https?://[^\s]*@[^\s]*', re.I),
     "URL:credential_in_url"),
    (re.compile(r'file://[^\s]+', re.I),
     "URL:local_file_access"),
]

# PDF-specific danger patterns in extracted text
PDF_TEXT_DANGER: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'/(?:OpenAction|AA|JavaScript|JS)\s*<<', re.I),
     "PDF:js_action"),
    (re.compile(r'(?:eval|exec|system)\s*\(', re.I),
     "PDF:code_exec"),
    (re.compile(r'\\x[0-9a-f]{2}(?:\\x[0-9a-f]{2}){3,}', re.I),
     "PDF:hex_shellcode"),
    (re.compile(r'/Launch\s*<<', re.I),
     "PDF:launch_action"),
    (re.compile(r'/SubmitForm', re.I),
     "PDF:form_submit"),
    (re.compile(r'app\.alert|app\.exec|app\.launchURL', re.I),
     "PDF:acrobat_js_api"),
]

# Metadata injection patterns (PDF Author/Subject/Keywords)
METADATA_INJECTION: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?', re.I),
     "META_INJ:ignore_previous"),
    (re.compile(r'system\s*(?:prompt|instruction)\s*:', re.I),
     "META_INJ:system_prompt"),
    (re.compile(r'(?:new|updated)\s+instructions?\s*:', re.I),
     "META_INJ:new_instructions"),
    (re.compile(r'(?:execute|run|eval)\s*:', re.I),
     "META_INJ:execute"),
    (re.compile(r'you\s+(?:are\s+now|must|should|will)\s+(?:act|behave|respond)', re.I),
     "META_INJ:persona"),
]


# ── Document Intake Descriptor ────────────────────────────────────────────────

@dataclass
class DocumentIntake:
    """Pre-extracted document data provided by a PDF parsing layer."""
    filename: str
    page_texts: List[str]           # Text per page (from PDF parser)
    metadata: Dict[str, str]        # PDF metadata dict (Author, Title, etc.)
    links: List[str]                # All hyperlinks found
    has_javascript: bool            # True if PDF contains JS actions
    has_hidden_layers: bool         # True if invisible text/layers detected
    has_attachments: bool           # True if embedded file attachments
    page_count: int
    file_hash: str = ""             # SHA-256 of the file (for audit)


@dataclass
class DocumentGovernanceResult:
    """Full governance assessment of a document."""
    filename: str
    file_hash: str
    page_count: int

    # Structural threats
    javascript_present: bool
    hidden_layers_present: bool
    attachments_present: bool

    # Content threats
    page_signals: Dict[int, List[str]]  # page_num → signals found
    metadata_signals: List[str]
    link_signals: List[str]
    injection_signals: List[str]

    # Scores
    structural_threat_score: float   # 0.0–1.0
    content_threat_score: float
    overall_threat_score: float

    recommendation: str
    triggered_by: str
    rationale: str
    latency_ms: float

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "file_hash": self.file_hash,
            "javascript_present": self.javascript_present,
            "hidden_layers_present": self.hidden_layers_present,
            "attachments_present": self.attachments_present,
            "metadata_signals": self.metadata_signals,
            "link_signals": self.link_signals,
            "injection_signals": self.injection_signals,
            "structural_threat_score": round(self.structural_threat_score, 3),
            "content_threat_score": round(self.content_threat_score, 3),
            "overall_threat_score": round(self.overall_threat_score, 3),
            "recommendation": self.recommendation,
            "triggered_by": self.triggered_by,
            "rationale": self.rationale,
        }


class DocumentGovernor:
    """
    Governs PDF and document content extracted by any parsing layer.
    Treats all document content as EXTERNAL tier — maximally restricted.
    """

    @classmethod
    def evaluate(cls, doc: DocumentIntake) -> DocumentGovernanceResult:
        t0 = time.perf_counter()
        recs: List[str] = []
        triggered_by = "NONE"

        # ── Stage 1: Structural threat assessment ─────────────────────────────
        structural_score = 0.0

        if doc.has_javascript:
            structural_score += 0.90
            recs.append("REFUSE")
            triggered_by = "PDF:JAVASCRIPT_PRESENT"
            logger.critical("[DocGov] JavaScript in document: %s", doc.filename)

        if doc.has_hidden_layers:
            structural_score += 0.60
            recs.append("ESCALATE")
            if triggered_by == "NONE":
                triggered_by = "PDF:HIDDEN_LAYERS"

        if doc.has_attachments:
            structural_score += 0.40
            recs.append("ESCALATE")
            if triggered_by == "NONE":
                triggered_by = "PDF:ATTACHMENTS"

        structural_score = min(1.0, structural_score)

        # ── Stage 2: Metadata injection scan ──────────────────────────────────
        metadata_signals: List[str] = []
        meta_text = " ".join(str(v) for v in doc.metadata.values())
        canon_meta = InputCanonicalizer.canonicalize(meta_text)

        for pat, label in METADATA_INJECTION:
            if pat.search(canon_meta.canonical):
                metadata_signals.append(label)

        if metadata_signals:
            recs.append("REFUSE")
            if triggered_by == "NONE":
                triggered_by = f"PDF:METADATA_INJECTION:{metadata_signals[0]}"

        # ── Stage 3: Hyperlink scanning ───────────────────────────────────────
        link_signals: List[str] = []
        for url in doc.links:
            for pat, label in MALICIOUS_URL_PATTERNS:
                if pat.search(url):
                    link_signals.append(f"{label}:{url[:50]}")

        if link_signals:
            recs.append("ESCALATE")
            if triggered_by == "NONE":
                triggered_by = f"PDF:MALICIOUS_LINK:{link_signals[0]}"

        # ── Stage 4: Page text governance ─────────────────────────────────────
        page_signals: Dict[int, List[str]] = {}
        injection_signals: List[str] = []
        content_score = 0.0

        for page_num, text in enumerate(doc.page_texts, 1):
            if not text.strip():
                continue
            # Use OCR governor to evaluate each page's text
            ocr_result = OCRGovernor.evaluate(
                raw_ocr_text=text,
                source=OCRSource.PDF_PAGE,
                ocr_confidence=0.95,   # PDF text extraction is high confidence
            )
            page_sigs = (
                ocr_result.injection_signals +
                ocr_result.phishing_signals +
                ocr_result.canonical_signals
            )
            if page_sigs:
                page_signals[page_num] = page_sigs
                injection_signals.extend(page_sigs)
                content_score = max(content_score, 0.70)

            if ocr_result.recommendation == "REFUSE":
                recs.append("REFUSE")
                if triggered_by == "NONE":
                    triggered_by = f"PDF:PAGE_{page_num}_INJECTION"

            # PDF-specific danger patterns in raw page text
            for pat, label in PDF_TEXT_DANGER:
                if pat.search(text):
                    injection_signals.append(label)
                    recs.append("REFUSE")
                    content_score = 1.0
                    if triggered_by == "NONE":
                        triggered_by = f"PDF:DANGER_PATTERN:{label}"

        overall_score = max(structural_score, content_score,
                            min(0.90, len(injection_signals) * 0.15))

        if "REFUSE" in recs:
            final_rec = "REFUSE"
        elif "ESCALATE" in recs:
            final_rec = "ESCALATE"
        else:
            final_rec = "APPROVE"
            triggered_by = "CLEAN"

        latency_ms = (time.perf_counter() - t0) * 1000
        rationale = (
            f"Document '{doc.filename}' | JS={doc.has_javascript} "
            f"Hidden={doc.has_hidden_layers} Attach={doc.has_attachments} | "
            f"MetaSigs={metadata_signals[:2]} | "
            f"InjectSigs={injection_signals[:3]} | Score={overall_score:.2f}"
        )

        return DocumentGovernanceResult(
            filename=doc.filename,
            file_hash=doc.file_hash,
            page_count=doc.page_count,
            javascript_present=doc.has_javascript,
            hidden_layers_present=doc.has_hidden_layers,
            attachments_present=doc.has_attachments,
            page_signals=page_signals,
            metadata_signals=metadata_signals,
            link_signals=link_signals,
            injection_signals=list(set(injection_signals)),
            structural_threat_score=structural_score,
            content_threat_score=content_score,
            overall_threat_score=overall_score,
            recommendation=final_rec,
            triggered_by=triggered_by,
            rationale=rationale,
            latency_ms=latency_ms,
        )
