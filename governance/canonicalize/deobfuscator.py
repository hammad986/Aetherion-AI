"""
governance/canonicalize/deobfuscator.py — Adversarial Deobfuscation Engine
===========================================================================
Detects hidden malicious semantics after canonicalization.

While the normalizer handles KNOWN encoding schemes deterministically,
the deobfuscator reasons about SUSPICIOUS PATTERNS that suggest an
adversary is attempting to hide their payload's true meaning.

Detects:
  • Fragmented commands (split across tokens to avoid matching)
  • Concatenated payload reconstruction (reassembled from parts)
  • Obfuscation scoring of the overall transformation graph
  • Layered encoding detection (encode-encode-encode patterns)
  • Payload laundering via variable substitution
  • Hidden delimiter patterns
  • Semantic camouflage: legitimate-looking text with embedded control

Architecture:
  TransformationGraph records what the normalizer did.
  ObfuscationScorer evaluates the graph's adversarial intent.
  PayloadReconstructor attempts to reassemble fragmented commands.
  DeobfuscationResult packages the findings for governance integration.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum

from governance.canonicalize.normalizer import CanonicalForm

logger = logging.getLogger("nexora.governance.canonicalize.deobfuscator")


# ── Obfuscation Intent Classes ────────────────────────────────────────────────

class ObfuscationIntent(str, Enum):
    NONE            = "NONE"
    ENCODING_BYPASS = "ENCODING_BYPASS"     # Tried to hide payload via encoding
    FRAGMENTATION   = "FRAGMENTATION"       # Split command to defeat matching
    CAMOUFLAGE      = "CAMOUFLAGE"          # Embedded harmful payload in benign text
    HOMOGLYPH_SPOOF = "HOMOGLYPH_SPOOF"     # Used lookalike chars
    INVISIBLE_EMBED = "INVISIBLE_EMBED"     # Hidden chars for channel creation
    MULTILAYER_ENCODE = "MULTILAYER_ENCODE" # Multiple nested encodings
    RTLO_ATTACK     = "RTLO_ATTACK"         # Right-to-left override
    VARIABLE_SUBST  = "VARIABLE_SUBST"      # Shell variable injection


@dataclass
class DeobfuscationResult:
    """Complete deobfuscation analysis with reconstruction attempt."""
    original: str
    canonical: str
    reconstructed_payload: str    # Best-effort reassembly of fragmented commands
    obfuscation_intent: ObfuscationIntent
    obfuscation_confidence: float  # 0.0–1.0
    detected_techniques: List[str]
    canonical_signals: List[str]   # Dangerous patterns found IN the canonical form
    requires_escalation: bool
    rationale: str

    def to_dict(self) -> dict:
        return {
            "obfuscation_intent": self.obfuscation_intent.value,
            "obfuscation_confidence": round(self.obfuscation_confidence, 3),
            "detected_techniques": self.detected_techniques,
            "canonical_signals": self.canonical_signals,
            "reconstructed_payload": self.reconstructed_payload[:200],
            "requires_escalation": self.requires_escalation,
            "rationale": self.rationale,
        }


# ── Payload Fragment Patterns (post-canonicalization) ─────────────────────────

# Dangerous patterns we look for IN the canonical form (after decoding)
CANONICAL_DANGER_SIGS: List[Tuple[re.Pattern, str]] = [
    # Shell execution
    (re.compile(r'\brm\s+-[rf]{1,2}\b', re.I),             "SHELL:rm_recursive"),
    (re.compile(r'/dev/tcp/', re.I),                         "SHELL:tcp_redirect"),
    (re.compile(r'\bnc\b.*-[el]\b', re.I),                  "SHELL:netcat_listen"),
    (re.compile(r'(bash|sh|zsh)\s+-[ci]\b', re.I),          "SHELL:bash_exec"),
    (re.compile(r'chmod\s+[0-7]{3,4}|chmod\s+\+[xs]', re.I),"SHELL:chmod_perm"),
    (re.compile(r'\bcurl\b.*\|\s*(bash|sh)\b', re.I),       "SHELL:curl_pipe_exec"),
    (re.compile(r'\bwget\b.*-[qO].*\|', re.I),              "SHELL:wget_pipe"),
    (re.compile(r'>\s*/etc/passwd|>>\s*/etc/cron', re.I),   "SHELL:crit_file_write"),
    # Credential access
    (re.compile(r'cat\s+.*\.aws/credentials', re.I),         "CRED:aws_creds"),
    (re.compile(r'cat\s+.*\.ssh/id_', re.I),                 "CRED:ssh_key"),
    (re.compile(r'printenv.*\|\s*(grep|curl)\b', re.I),      "CRED:env_exfil"),
    (re.compile(r'grep\s+-[ri].*\b(pass|secret|key|token)\b', re.I), "CRED:grep_secrets"),
    # Network abuse
    (re.compile(r'nmap\s+.*-[sSfP]', re.I),                 "NET:nmap_scan"),
    (re.compile(r'masscan\b.*-p', re.I),                     "NET:masscan_ports"),
    (re.compile(r'python.*http\.server|SimpleHTTPServer', re.I), "NET:http_server"),
    # Governance bypass
    (re.compile(r'ignore\s+all\s+previous', re.I),          "INJECT:ignore_prev"),
    (re.compile(r'(disable|bypass|remove)\s+(filter|governance|safeguard)', re.I),
                                                             "INJECT:bypass_gov"),
    (re.compile(r'system\s+prompt\s*[:=]', re.I),           "INJECT:sys_prompt_override"),
    # Persistence
    (re.compile(r'crontab\s+-[el]|/etc/cron\.(d|daily)', re.I), "PERSIST:cron"),
    (re.compile(r'systemctl\s+enable\b', re.I),              "PERSIST:systemctl"),
    # Exfiltration
    (re.compile(r'(curl|wget).*http[s]?://.*-[dTf]', re.I), "EXFIL:http_upload"),
    (re.compile(r'scp\s+.*@', re.I),                         "EXFIL:scp_remote"),
]

# Fragmentation patterns — commands split across lines or tokens
_FRAGMENT_INDICATORS = [
    re.compile(r'`[^`]{1,30}`', re.S),          # command substitution fragments
    re.compile(r'\$\{[^}]{1,30}\}', re.S),       # variable expansion
    re.compile(r'\\\s*\n\s*', re.M),             # line continuation
    re.compile(r';\s*\n\s*', re.M),              # semicolon chaining
    re.compile(r'\|\s*\n\s*', re.M),             # pipe chaining across lines
]

# RTLO (Right-to-Left Override) patterns
_RTLO_CHARS = frozenset({"\u202e", "\u202d", "\u200f", "\u061c"})

# Variable substitution in shell
_VAR_SUBST_RE = re.compile(r'\$[A-Z_][A-Z0-9_]*|\$\{[^}]+\}', re.I)


class AdversarialDeobfuscator:
    """
    Analyzes a CanonicalForm to detect obfuscation intent and reconstruct
    the true payload from any fragmentation.
    """

    @classmethod
    def analyze(cls, canon: CanonicalForm) -> DeobfuscationResult:
        """
        Full deobfuscation analysis.
        Input: CanonicalForm from InputCanonicalizer
        Output: DeobfuscationResult with intent, techniques, and signals
        """
        techniques: List[str] = []
        intent = ObfuscationIntent.NONE
        confidence = 0.0

        # ── Encoding bypass detection ─────────────────────────────────────────
        if len(canon.suspicious_encodings) >= 2:
            techniques.append(f"MULTILAYER_ENCODE: {'+'.join(canon.suspicious_encodings)}")
            intent = ObfuscationIntent.MULTILAYER_ENCODE
            confidence = max(confidence, 0.85)
        elif canon.suspicious_encodings:
            enc = canon.suspicious_encodings[0]
            techniques.append(f"ENCODING_BYPASS: {enc}")
            intent = ObfuscationIntent.ENCODING_BYPASS
            confidence = max(confidence, 0.55)

        # ── Homoglyph spoofing ────────────────────────────────────────────────
        if canon.homoglyphs_found > 0:
            techniques.append(f"HOMOGLYPH_SPOOF: {canon.homoglyphs_found} chars substituted")
            intent = ObfuscationIntent.HOMOGLYPH_SPOOF
            confidence = max(confidence, 0.70 + min(0.25, canon.homoglyphs_found * 0.05))

        # ── Invisible character embedding ─────────────────────────────────────
        if canon.invisible_chars_found > 0:
            techniques.append(f"INVISIBLE_EMBED: {canon.invisible_chars_found} chars removed")
            intent = ObfuscationIntent.INVISIBLE_EMBED
            # Multiple invisible chars = deliberate covert channel
            confidence = max(confidence, 0.60 + min(0.35, canon.invisible_chars_found * 0.07))

        # ── RTLO attack detection (in original) ───────────────────────────────
        rtlo_found = sum(1 for ch in canon.original if ch in _RTLO_CHARS)
        if rtlo_found > 0:
            techniques.append(f"RTLO_ATTACK: {rtlo_found} RTL override chars")
            intent = ObfuscationIntent.RTLO_ATTACK
            confidence = max(confidence, 0.90)

        # ── Fragment reconstruction ───────────────────────────────────────────
        fragmented, reconstructed = cls._reconstruct_payload(canon.canonical)
        if fragmented:
            techniques.append("FRAGMENTATION: command reconstructed from fragments")
            if intent == ObfuscationIntent.NONE:
                intent = ObfuscationIntent.FRAGMENTATION
            confidence = max(confidence, 0.50)
        else:
            reconstructed = canon.canonical

        # ── Variable substitution ─────────────────────────────────────────────
        var_matches = _VAR_SUBST_RE.findall(canon.original)
        if len(var_matches) >= 3:
            techniques.append(f"VARIABLE_SUBST: {len(var_matches)} shell variables")
            if intent == ObfuscationIntent.NONE:
                intent = ObfuscationIntent.VARIABLE_SUBST
            confidence = max(confidence, 0.40)

        # ── Canonical danger signal detection ─────────────────────────────────
        canonical_signals = cls._scan_canonical(reconstructed)

        # Signals found in canonical form after decoding raise confidence
        if canonical_signals and (canon.was_obfuscated() or fragmented):
            confidence = min(1.0, confidence + len(canonical_signals) * 0.10)
            techniques.append(
                f"PAYLOAD_REVEALED: {len(canonical_signals)} danger signals in decoded form"
            )

        # ── Camouflage detection ──────────────────────────────────────────────
        # Camouflage: canonical form is much shorter than original (hidden content was stripped)
        compression_ratio = len(canon.canonical) / max(1, len(canon.original))
        if compression_ratio < 0.70 and canon.was_obfuscated():
            techniques.append(f"CAMOUFLAGE: {(1-compression_ratio)*100:.0f}% content removed")
            intent = ObfuscationIntent.CAMOUFLAGE
            confidence = max(confidence, 0.65)

        # ── Escalation requirement ────────────────────────────────────────────
        requires_escalation = (
            confidence >= 0.45 or
            bool(canonical_signals) or
            rtlo_found > 0
        )

        # ── Rationale ─────────────────────────────────────────────────────────
        rationale = (
            f"Obfuscation intent: {intent.value} (confidence={confidence:.2f}). "
            f"Techniques: {'; '.join(techniques[:3]) if techniques else 'None'}. "
            f"Canonical signals: {', '.join(canonical_signals[:3]) if canonical_signals else 'None'}."
        )

        return DeobfuscationResult(
            original=canon.original,
            canonical=canon.canonical,
            reconstructed_payload=reconstructed,
            obfuscation_intent=intent,
            obfuscation_confidence=confidence,
            detected_techniques=techniques,
            canonical_signals=canonical_signals,
            requires_escalation=requires_escalation,
            rationale=rationale,
        )

    @classmethod
    def _reconstruct_payload(cls, text: str) -> Tuple[bool, str]:
        """
        Attempt to reconstruct a fragmented command by collapsing
        line continuations, semicolons, and pipe chains.
        """
        fragmented = False
        result = text

        # Collapse line continuations: "rm -r\ \nf /" → "rm -rf /"
        if re.search(r'\\\s*\n', text):
            result = re.sub(r'\\\s*\n\s*', '', result)
            fragmented = True

        # Collapse semicolons used as inline separators
        if ';' in result and '\n' in result:
            result = re.sub(r';\s*\n\s*', '; ', result)
            fragmented = True

        # Collapse pipe-newline
        if re.search(r'\|\s*\n', text):
            result = re.sub(r'\|\s*\n\s*', '| ', result)
            fragmented = True

        return fragmented, result

    @classmethod
    def _scan_canonical(cls, text: str) -> List[str]:
        """Scan the canonical/reconstructed form for danger signals."""
        signals = []
        for pattern, label in CANONICAL_DANGER_SIGS:
            if pattern.search(text):
                signals.append(label)
        return signals
