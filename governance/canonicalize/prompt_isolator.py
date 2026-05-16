"""
governance/canonicalize/prompt_isolator.py — Structured Prompt Isolation
=========================================================================
Prevents prompt injection by enforcing strict boundary architecture between
system instructions, user content, and tool outputs.

The fundamental problem:
  When tool output, user text, or external data is included in a prompt
  context, an adversary who controls that content can inject instructions
  that the system treats as system-level directives.

Defense strategy:
  1. PROVENANCE TAGGING — every piece of text is labeled by its source tier
  2. BOUNDARY MARKERS — explicit, immutable separators between tiers
  3. SANITIZATION — tool output and user content are stripped of
     instruction-like patterns before context assembly
  4. INJECTION SCANNING — the assembled context is checked for
     cross-tier instruction leakage

Tiers:
  TIER_0: SYSTEM     — immutable constitutional instructions (never sanitized)
  TIER_1: OPERATOR   — trusted operator commands (validated)
  TIER_2: USER       — untrusted user input (sanitized)
  TIER_3: TOOL       — tool/agent output (sandboxed)
  TIER_4: EXTERNAL   — external data, web content, files (maximally sandboxed)
"""

import re
import logging
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum, auto

logger = logging.getLogger("nexora.governance.canonicalize.prompt_isolator")


# ── Provenance Tiers ──────────────────────────────────────────────────────────

class ContentTier(str, Enum):
    SYSTEM    = "SYSTEM"     # Constitutional instructions — NEVER sanitized
    OPERATOR  = "OPERATOR"   # Trusted operator — validated but not fully sanitized
    USER      = "USER"       # Untrusted — sanitized before context assembly
    TOOL      = "TOOL"       # Tool/agent output — sandboxed
    EXTERNAL  = "EXTERNAL"   # External data — maximally restricted


# ── Injection Pattern Library ─────────────────────────────────────────────────

INJECTION_PATTERNS: List[Tuple[re.Pattern, str, float]] = [
    # (pattern, label, severity_0.0-1.0)
    (re.compile(r'ignore\s+(?:all\s+)?(?:previous|above|prior)\s+instructions?', re.I),
     "INJ:ignore_previous", 0.95),
    (re.compile(r'(?:you\s+are\s+now|from\s+now\s+on)\s+(?:an?\s+)?(?:unrestricted|unconstrained|free)', re.I),
     "INJ:persona_override", 0.90),
    (re.compile(r'system\s*(?:prompt|instruction|message)\s*[:=]', re.I),
     "INJ:system_override", 0.95),
    (re.compile(r'(?:forget|disregard|override)\s+(?:your\s+)?(?:training|instructions?|guidelines?|rules?)', re.I),
     "INJ:override_training", 0.90),
    (re.compile(r'(?:print|output|reveal|show)\s+(?:your\s+)?(?:system\s+)?prompt', re.I),
     "INJ:prompt_extraction", 0.85),
    (re.compile(r'assistant\s*:\s*(?:sure|of\s+course|yes|i\s+will)', re.I),
     "INJ:assistant_hijack", 0.80),
    (re.compile(r'(?:new|actual|real)\s+instructions?\s*[:=\n]', re.I),
     "INJ:instruction_replace", 0.85),
    (re.compile(r'<\s*(?:system|instruction|prompt)\s*>', re.I),
     "INJ:xml_tag_injection", 0.90),
    (re.compile(r'\[\s*(?:SYSTEM|INST|SYS)\s*\]', re.I),
     "INJ:bracket_injection", 0.85),
    (re.compile(r'(?:execute|run|eval)\s*(?:\(|:)\s*[\'"]{0,1}(?:os|sys|exec|shell)', re.I),
     "INJ:code_injection", 0.88),
    (re.compile(r'(?:do\s+not\s+)?(?:mention|say|reveal)\s+(?:that\s+)?(?:you|this)', re.I),
     "INJ:secrecy_instruction", 0.65),
]

# Patterns that indicate a TOOL output is trying to inject instructions
TOOL_INJECTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'(?:Note|Important|Instruction|Directive)\s*:\s*(?:Always|Never|You\s+must)', re.I),
     "TOOL_INJ:directive_note"),
    (re.compile(r'USER\s*:\s*|HUMAN\s*:\s*|ASSISTANT\s*:\s*', re.I),
     "TOOL_INJ:role_spoofing"),
    (re.compile(r'<\|(?:system|human|assistant|im_start|im_end)\|>', re.I),
     "TOOL_INJ:special_token"),
    (re.compile(r'\[INST\]|\[/INST\]|<s>|</s>', re.I),
     "TOOL_INJ:llm_format_tag"),
]


# ── Isolated Content Block ────────────────────────────────────────────────────

@dataclass
class IsolatedBlock:
    """A single piece of content with provenance metadata."""
    tier: ContentTier
    content: str
    content_hash: str    # SHA-256 of original content (for audit)
    sanitized: bool
    injection_signals: List[str]
    injection_severity: float

    @classmethod
    def create(cls, tier: ContentTier, content: str) -> "IsolatedBlock":
        content_hash = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]
        sanitized = False
        signals: List[str] = []
        severity = 0.0

        if tier in (ContentTier.USER, ContentTier.TOOL, ContentTier.EXTERNAL):
            content, signals, severity = cls._sanitize(content, tier)
            sanitized = True

        return cls(
            tier=tier,
            content=content,
            content_hash=content_hash,
            sanitized=sanitized,
            injection_signals=signals,
            injection_severity=severity,
        )

    @staticmethod
    def _sanitize(
        content: str, tier: ContentTier
    ) -> Tuple[str, List[str], float]:
        """Strip injection patterns from untrusted content tiers."""
        signals: List[str] = []
        max_severity = 0.0
        result = content

        patterns = INJECTION_PATTERNS
        if tier == ContentTier.TOOL:
            # Tools also checked for tool-specific injection
            for pat, label in TOOL_INJECTION_PATTERNS:
                if pat.search(result):
                    signals.append(label)
                    result = pat.sub("[REDACTED_INJECTION]", result)

        for pat, label, severity in patterns:
            if pat.search(result):
                signals.append(label)
                max_severity = max(max_severity, severity)
                # Replace with redacted marker — don't just delete (audit trail)
                result = pat.sub(f"[REDACTED:{label}]", result)

        return result, signals, max_severity


# ── Assembled Context ─────────────────────────────────────────────────────────

@dataclass
class IsolatedContext:
    """A fully assembled, injection-resistant prompt context."""
    blocks: List[IsolatedBlock]
    total_injection_signals: List[str]
    max_injection_severity: float
    injection_attempted: bool
    assembly_safe: bool
    rationale: str

    def assemble_text(self) -> str:
        """
        Assemble blocks with clear tier boundary markers.
        System instructions are FIRST and immutable.
        """
        parts = []
        for block in self.blocks:
            if block.tier == ContentTier.SYSTEM:
                parts.append(f"[SYSTEM]\n{block.content}\n[/SYSTEM]")
            elif block.tier == ContentTier.OPERATOR:
                parts.append(f"[OPERATOR]\n{block.content}\n[/OPERATOR]")
            elif block.tier == ContentTier.USER:
                parts.append(f"[USER_INPUT]\n{block.content}\n[/USER_INPUT]")
            elif block.tier == ContentTier.TOOL:
                parts.append(f"[TOOL_OUTPUT sandbox=true]\n{block.content}\n[/TOOL_OUTPUT]")
            elif block.tier == ContentTier.EXTERNAL:
                parts.append(f"[EXTERNAL_DATA restricted=true]\n{block.content}\n[/EXTERNAL_DATA]")
        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "injection_attempted": self.injection_attempted,
            "assembly_safe": self.assembly_safe,
            "max_severity": round(self.max_injection_severity, 3),
            "signals": self.total_injection_signals,
            "rationale": self.rationale,
        }


# ── Prompt Isolator ───────────────────────────────────────────────────────────

class PromptIsolator:
    """
    Enforces strict tier isolation and injection resistance.
    Called before any prompt is assembled for AI processing.
    """

    @classmethod
    def isolate(
        cls,
        system_text: Optional[str] = None,
        operator_text: Optional[str] = None,
        user_text: Optional[str] = None,
        tool_outputs: Optional[List[str]] = None,
        external_data: Optional[List[str]] = None,
    ) -> IsolatedContext:
        blocks: List[IsolatedBlock] = []

        # System tier — never sanitized (it IS the governance)
        if system_text:
            blocks.append(IsolatedBlock.create(ContentTier.SYSTEM, system_text))

        # Operator tier — validated
        if operator_text:
            blocks.append(IsolatedBlock.create(ContentTier.OPERATOR, operator_text))

        # User tier — sanitized
        if user_text:
            blocks.append(IsolatedBlock.create(ContentTier.USER, user_text))

        # Tool output tier — sandboxed
        for tool_out in (tool_outputs or []):
            blocks.append(IsolatedBlock.create(ContentTier.TOOL, tool_out))

        # External data tier — maximally restricted
        for ext in (external_data or []):
            blocks.append(IsolatedBlock.create(ContentTier.EXTERNAL, ext))

        # ── Aggregate injection signals ───────────────────────────────────────
        all_signals: List[str] = []
        max_severity = 0.0
        for block in blocks:
            if block.tier == ContentTier.SYSTEM:
                continue   # System is trusted — don't count its patterns
            all_signals.extend(block.injection_signals)
            max_severity = max(max_severity, block.injection_severity)

        injection_attempted = bool(all_signals)
        assembly_safe = max_severity < 0.85

        if injection_attempted:
            logger.warning(
                "[PromptIsolator] Injection attempt detected: %s (severity=%.2f)",
                all_signals[:3], max_severity,
            )

        rationale = (
            f"{'⚠ INJECTION DETECTED' if injection_attempted else 'Clean context'}. "
            f"Max severity={max_severity:.2f}. Signals: {all_signals[:3] if all_signals else 'None'}"
        )

        return IsolatedContext(
            blocks=blocks,
            total_injection_signals=all_signals,
            max_injection_severity=max_severity,
            injection_attempted=injection_attempted,
            assembly_safe=assembly_safe,
            rationale=rationale,
        )

    @classmethod
    def scan_for_injection(cls, text: str) -> Tuple[bool, List[str], float]:
        """
        Quick injection scan for a single text block.
        Returns: (detected, signals, max_severity)
        """
        signals = []
        max_sev = 0.0
        for pat, label, severity in INJECTION_PATTERNS:
            if pat.search(text):
                signals.append(label)
                max_sev = max(max_sev, severity)
        return bool(signals), signals, max_sev
