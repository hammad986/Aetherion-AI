"""
security/injection_guard.py — Active Prompt Injection Defense System
====================================================================
Detects and contains prompt injection attacks across all input vectors:

  Vector 1: Tool output injections     (shell stdout, API responses)
  Vector 2: Browser content injections (scraped web page content)
  Vector 3: File content injections    (README.md, config files)
  Vector 4: Delegation hijacking       (crafted task descriptions)
  Vector 5: Memory poisoning           (injected via shared context)
  Vector 6: Indirect injection         (third-party API responses)

Design principles:
  - Content-level scanning (not prompt-level only)
  - Instruction provenance tracking (every instruction tagged with source)
  - Trust tier scoring (USER > SYSTEM > TOOL_OUTPUT > BROWSER > UNTRUSTED)
  - Suspicious content quarantine before LLM ingestion
  - Delegation sanitization strips embedded system-prompt patterns
  - Detection does NOT rely on LLM self-reporting

Known injection patterns (examples of real attack categories):
  - "Ignore previous instructions and..."
  - "### NEW SYSTEM PROMPT ###"
  - "[INST] ... [/INST]" (Llama instruction tokens in content)
  - "You are now DAN..."
  - Hidden Unicode direction overrides (RTL spoofing)
  - HTML comment encoded instructions
"""

import hashlib
import logging
import re
import time
import threading
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("nexora.security.injection")


# ─────────────────────────────────────────────────────────────────────────────
# Trust tiers — determines how aggressively content is scanned
# ─────────────────────────────────────────────────────────────────────────────

class ContentTrust(int, Enum):
    USER      = 5     # Direct user message (trusted — but still scanned)
    SYSTEM    = 4     # System-generated (high trust)
    TOOL      = 3     # Tool output (medium trust — runs in agent env)
    BROWSER   = 2     # Browser page content (low trust — external)
    FILE      = 2     # File read from workspace (low trust)
    EXTERNAL  = 1     # Third-party API / network response (untrusted)
    UNTRUSTED = 0     # Unknown provenance (maximum scrutiny)


class InjectionVerdict(str, Enum):
    CLEAN       = "CLEAN"        # No injection detected
    SUSPICIOUS  = "SUSPICIOUS"   # Possible injection — flag but allow with warning
    INJECTED    = "INJECTED"     # High-confidence injection — quarantine
    SANITIZED   = "SANITIZED"    # Injection detected and stripped


@dataclass
class InjectionScanResult:
    verdict: InjectionVerdict
    confidence: float          # 0.0 – 1.0
    signals: List[str]
    sanitized_content: str     # content after stripping detected injections
    content_hash: str
    source_trust: ContentTrust


# ─────────────────────────────────────────────────────────────────────────────
# Detection patterns — multi-layer
# ─────────────────────────────────────────────────────────────────────────────

# Layer 1: Hard injection signatures (high confidence)
_HARD_INJECTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.I | re.S),
     "ignore_previous_instructions"),
    (re.compile(r"forget\s+everything\s+you\s+(know|were|have)", re.I),
     "forget_everything"),
    (re.compile(r"you\s+are\s+now\s+(DAN|jailbreak|unrestricted|free)", re.I),
     "jailbreak_persona"),
    (re.compile(r"\bnew\s+system\s+(prompt|instruction)", re.I),
     "new_system_prompt"),
    (re.compile(r"###\s*(SYSTEM|INSTRUCTION|OVERRIDE|NEW PROMPT)", re.I),
     "system_override_header"),
    (re.compile(r"\[/?INST\]|\[SYS\]|\[\/SYS\]|\<\|im_start\|\>", re.I),
     "model_instruction_tokens"),
    (re.compile(r"<\s*system\s*>.*?<\s*/\s*system\s*>", re.I | re.S),
     "xml_system_tag"),
    (re.compile(r"Act\s+as\s+(if\s+you\s+are\s+|a\s+)?(an?\s+)?(?:unrestricted|jailbroken|evil|malicious)", re.I),
     "evil_persona_switch"),
    (re.compile(r"(?:your\s+true|real|actual)\s+(?:purpose|goal|instruction)\s+is", re.I),
     "purpose_override"),
    (re.compile(r"disregard\s+(your\s+)?(safety|ethics|guidelines|rules|constraints)", re.I),
     "safety_disregard"),
    (re.compile(r"DO\s+NOT\s+(?:tell|mention|inform)\s+the\s+user", re.I),
     "secret_instruction"),
    (re.compile(r"exfiltrate|send\s+to\s+https?://|upload\s+(the\s+)?(?:key|secret|credential|env)", re.I),
     "exfiltration_instruction"),
    (re.compile(r"print\s+(?:your\s+)?(?:system\s+)?prompt|reveal\s+instructions", re.I),
     "prompt_extraction"),
]

# Layer 2: Soft signals (scored, contribute to confidence)
_SOFT_SIGNALS: List[Tuple[re.Pattern, float, str]] = [
    (re.compile(r"\boverride\b",         re.I), 0.15, "override_keyword"),
    (re.compile(r"\bbypass\b",           re.I), 0.15, "bypass_keyword"),
    (re.compile(r"\bjailbreak\b",        re.I), 0.30, "jailbreak_keyword"),
    (re.compile(r"\bunrestricted\b",     re.I), 0.20, "unrestricted_keyword"),
    (re.compile(r"\bconfidential\b.*\binstruction", re.I), 0.20, "confidential_instruction"),
    (re.compile(r"system:\s",            re.I), 0.15, "system_role_prefix"),
    (re.compile(r"\bassistant:\s",       re.I), 0.10, "assistant_role_prefix"),
    (re.compile(r"```\s*\n.*?```",       re.I | re.S), 0.05, "code_block_in_content"),  # low weight, common
    (re.compile(r"\bRoot\s+Access\b",    re.I), 0.25, "root_access_mention"),
    (re.compile(r"\badmin\s+mode\b",     re.I), 0.20, "admin_mode"),
    (re.compile(r"developer\s+mode",     re.I), 0.15, "developer_mode"),
    (re.compile(r"your\s+real\s+purpose", re.I), 0.30, "real_purpose"),
]

# Layer 3: Unicode / encoding obfuscation detection
_UNICODE_OBFUSCATION_CATS = {
    "Cf",   # Format characters (invisible)
    "Cc",   # Control characters
    "Co",   # Private use
    "Cn",   # Unassigned
}

# RTL override characters used for spoofing
_RTL_OVERRIDES = {
    "\u200e", "\u200f",   # LRM, RLM
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",  # directional overrides
    "\u2066", "\u2067", "\u2068", "\u2069",            # isolate chars
    "\u200b", "\u200c", "\u200d",                      # zero-width characters
}

# ─────────────────────────────────────────────────────────────────────────────
# Provenance tracking
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InstructionProvenance:
    """Tracks where an instruction originated."""
    instruction_id: str
    source:         str             # "user", "tool:bash", "browser:github.com", etc.
    trust:          ContentTrust
    ts:             float = field(default_factory=time.time)
    session_id:     str  = ""
    content_hash:   str  = ""


class ProvenanceRegistry:
    """Thread-safe registry of all instructions with their provenance."""

    def __init__(self):
        self._registry: Dict[str, InstructionProvenance] = {}
        self._lock = threading.RLock()

    def register(self, content: str, source: str, trust: ContentTrust,
                 session_id: str = "") -> str:
        """Registers content and returns its instruction_id."""
        iid = hashlib.sha256(f"{source}:{content[:200]}:{time.time()}".encode()).hexdigest()[:16]
        prov = InstructionProvenance(
            instruction_id=iid,
            source=source,
            trust=trust,
            session_id=session_id,
            content_hash=hashlib.sha256(content.encode()).hexdigest()[:12],
        )
        with self._lock:
            self._registry[iid] = prov
            if len(self._registry) > 5000:
                # Prune oldest 10%
                oldest = sorted(self._registry.items(), key=lambda x: x[1].ts)[:500]
                for k, _ in oldest:
                    del self._registry[k]
        return iid

    def get(self, instruction_id: str) -> Optional[InstructionProvenance]:
        with self._lock:
            return self._registry.get(instruction_id)

    def get_by_session(self, session_id: str) -> List[InstructionProvenance]:
        with self._lock:
            return [p for p in self._registry.values() if p.session_id == session_id]


# ─────────────────────────────────────────────────────────────────────────────
# PromptInjectionGuard
# ─────────────────────────────────────────────────────────────────────────────

class PromptInjectionGuard:
    """
    Active prompt injection defense system.

    Every piece of external content (tool output, browser, files, API)
    MUST be scanned before being added to the LLM context window.
    """

    # Trust-tier thresholds: content must score below these to be CLEAN
    _VERDICT_THRESHOLDS = {
        ContentTrust.USER:      0.35,   # Users get more benefit of doubt
        ContentTrust.SYSTEM:    0.20,
        ContentTrust.TOOL:      0.30,
        ContentTrust.BROWSER:   0.25,
        ContentTrust.FILE:      0.30,
        ContentTrust.EXTERNAL:  0.20,
        ContentTrust.UNTRUSTED: 0.10,   # Untrusted: almost anything suspicious = flagged
    }

    def __init__(self):
        self._lock    = threading.RLock()
        self._audit:  List[dict] = []
        self.provenance = ProvenanceRegistry()

    # ── Main scan interface ───────────────────────────────────────────────────

    def scan(
        self,
        content: str,
        source: str,
        trust: ContentTrust,
        session_id: str = "",
        auto_sanitize: bool = True,
    ) -> InjectionScanResult:
        """
        Scans content for injection attempts.

        Parameters:
          content       — raw content to scan (tool output, browser text, etc.)
          source        — origin label ("tool:bash", "browser:github.com", etc.)
          trust         — ContentTrust tier
          session_id    — for audit logging
          auto_sanitize — if True, attempts to strip injection patterns

        Returns InjectionScanResult — caller MUST check .verdict.
        """
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:12]
        signals: List[str] = []
        confidence = 0.0

        # Layer 1: Unicode obfuscation check
        unicode_score = self._scan_unicode_obfuscation(content)
        if unicode_score > 0:
            signals.append(f"unicode_obfuscation:{unicode_score}")
            confidence += unicode_score * 0.4

        # Layer 2: Hard injection signatures
        for pattern, label in _HARD_INJECTION_PATTERNS:
            if pattern.search(content):
                signals.append(label)
                confidence = min(1.0, confidence + 0.60)  # Hard signatures are high weight

        # Layer 3: Soft signals
        for pattern, weight, label in _SOFT_SIGNALS:
            if pattern.search(content):
                signals.append(label)
                confidence = min(1.0, confidence + weight)

        # Clamp confidence
        confidence = min(1.0, confidence)

        # Determine threshold for this trust tier
        threshold = self._VERDICT_THRESHOLDS.get(trust, 0.25)

        if confidence >= 0.80:
            verdict = InjectionVerdict.INJECTED
        elif confidence >= threshold:
            verdict = InjectionVerdict.SUSPICIOUS
        else:
            verdict = InjectionVerdict.CLEAN

        # Sanitize if flagged and auto_sanitize=True
        sanitized = content
        if verdict in (InjectionVerdict.INJECTED, InjectionVerdict.SUSPICIOUS) and auto_sanitize:
            sanitized = self._sanitize(content)
            if sanitized != content:
                verdict = InjectionVerdict.SANITIZED

        result = InjectionScanResult(
            verdict=verdict,
            confidence=confidence,
            signals=signals,
            sanitized_content=sanitized,
            content_hash=content_hash,
            source_trust=trust,
        )

        self._audit_log(result, source, session_id, content[:200])
        return result

    # ── Convenience wrappers ──────────────────────────────────────────────────

    def scan_tool_output(self, output: str, tool_name: str = "tool",
                         session_id: str = "") -> InjectionScanResult:
        return self.scan(output, f"tool:{tool_name}", ContentTrust.TOOL, session_id)

    def scan_browser_content(self, html_text: str, url: str = "",
                              session_id: str = "") -> InjectionScanResult:
        domain = url.split("/")[2] if url.startswith("http") else "unknown"
        return self.scan(html_text, f"browser:{domain}", ContentTrust.BROWSER, session_id)

    def scan_file_content(self, content: str, path: str = "",
                          session_id: str = "") -> InjectionScanResult:
        fname = path.split("/")[-1] if "/" in path else path
        return self.scan(content, f"file:{fname}", ContentTrust.FILE, session_id)

    def scan_user_message(self, message: str, session_id: str = "") -> InjectionScanResult:
        return self.scan(message, "user", ContentTrust.USER, session_id, auto_sanitize=False)

    def sanitize_delegation(self, task_description: str, session_id: str = "") -> str:
        """
        Sanitizes a delegation task description.
        Strips any embedded system-prompt patterns while preserving task intent.
        """
        result = self.scan(task_description, "delegation", ContentTrust.TOOL, session_id)
        if result.verdict in (InjectionVerdict.INJECTED, InjectionVerdict.SUSPICIOUS):
            logger.warning(
                f"[InjectionGuard] Delegation injection stripped: "
                f"signals={result.signals} session={session_id}"
            )
        return result.sanitized_content

    # ── Sanitization ──────────────────────────────────────────────────────────

    def _sanitize(self, content: str) -> str:
        """
        Removes high-confidence injection patterns from content.
        Does NOT use LLM for sanitization (would itself be an injection vector).
        Uses regex stripping only.
        """
        sanitized = content
        for pattern, _ in _HARD_INJECTION_PATTERNS:
            sanitized = pattern.sub("[CONTENT_REDACTED_BY_SECURITY_GUARD]", sanitized)
        # Strip RTL overrides
        sanitized = "".join(c for c in sanitized if c not in _RTL_OVERRIDES)
        # Normalize unicode (NFC)
        sanitized = unicodedata.normalize("NFC", sanitized)
        return sanitized

    # ── Unicode obfuscation scoring ───────────────────────────────────────────

    def _scan_unicode_obfuscation(self, content: str) -> float:
        """Returns a 0.0-1.0 obfuscation score based on suspicious unicode density."""
        if not content:
            return 0.0
        suspicious = sum(
            1 for c in content
            if unicodedata.category(c) in _UNICODE_OBFUSCATION_CATS
            or c in _RTL_OVERRIDES
        )
        density = suspicious / max(len(content), 1)
        return min(1.0, density * 100)   # 1% density → score 1.0

    # ── Audit ─────────────────────────────────────────────────────────────────

    def _audit_log(self, result: InjectionScanResult, source: str,
                   session_id: str, preview: str) -> None:
        from infra.telemetry import get_telemetry
        entry = {
            "ts":         time.time(),
            "session_id": session_id,
            "verdict":    result.verdict.value,
            "confidence": round(result.confidence, 3),
            "signals":    result.signals,
            "source":     source,
            "content_hash": result.content_hash,
            "preview":    preview[:100],
        }
        with self._lock:
            self._audit.append(entry)
            if len(self._audit) > 1000:
                self._audit.pop(0)

        if result.verdict != InjectionVerdict.CLEAN:
            logger.warning(
                f"[InjectionGuard] {result.verdict.value} confidence={result.confidence:.2f} "
                f"signals={result.signals} source={source} session={session_id}"
            )
            try:
                get_telemetry().record(
                    "security", f"injection_{result.verdict.value.lower()}",
                    {"confidence": result.confidence, "signals": result.signals,
                     "source": source},
                    session_id=session_id
                )
            except Exception:
                pass

    def recent_incidents(self, n: int = 20) -> List[dict]:
        with self._lock:
            flagged = [e for e in self._audit if e["verdict"] != "CLEAN"]
            return list(flagged[-n:])

    def snapshot(self) -> dict:
        with self._lock:
            total = len(self._audit)
            injected   = sum(1 for e in self._audit if e["verdict"] == "INJECTED")
            suspicious = sum(1 for e in self._audit if e["verdict"] == "SUSPICIOUS")
            sanitized  = sum(1 for e in self._audit if e["verdict"] == "SANITIZED")
        return {
            "scans_total":  total,
            "injected":     injected,
            "suspicious":   suspicious,
            "sanitized":    sanitized,
            "clean":        total - injected - suspicious - sanitized,
            "patterns":     len(_HARD_INJECTION_PATTERNS),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Global singleton
# ─────────────────────────────────────────────────────────────────────────────

global_injection_guard = PromptInjectionGuard()
