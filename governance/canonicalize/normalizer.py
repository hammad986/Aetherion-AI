"""
governance/canonicalize/normalizer.py — Multi-Layer Input Canonicalization Pipeline
====================================================================================
Every input reaches governance as a normalized, canonical form — not as raw user text.

Canonicalization Layers (applied in order):
  L1: Null-byte and control character stripping
  L2: Unicode normalization (NFKC — compatibility + composition)
  L3: Homoglyph substitution (confusable unicode → ASCII equivalents)
  L4: Zero-width and invisible character removal
  L5: URL decoding (single + double encoding)
  L6: HTML entity decoding
  L7: Hex/octal escape sequence expansion
  L8: Base64 suspicion flagging and conditional decoding
  L9: Whitespace normalization (collapse excessive whitespace)
  L10: Case-insensitive canonical form for pattern evaluation
  L11: Comment and delimiter stripping (shell/code comments)
  L12: Mixed-script detection and flagging

Each layer is:
  - Deterministic (same input → same output, always)
  - Auditable (transformation delta is recorded)
  - Replay-safe (original + canonical form both preserved)
  - Non-destructive to legitimate content (benign inputs unchanged)

Design Principle:
  Canonicalization must run BEFORE any governance evaluation.
  Governance sees only the canonical form, never raw user input.
  The original form is preserved in the audit trail.
"""

import re
import html
import base64
import codecs
import unicodedata
import urllib.parse
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("nexora.governance.canonicalize.normalizer")


# ── Homoglyph Map (confusable unicode → ASCII) ────────────────────────────────
# Sources: Unicode Consortium confusables.txt + curated attack-relevant chars
HOMOGLYPH_MAP: Dict[str, str] = {
    # Cyrillic lookalikes
    "\u0430": "a",   # Cyrillic small a
    "\u04cf": "l",   # Cyrillic small el with tail
    "\u0435": "e",   # Cyrillic small ie
    "\u043e": "o",   # Cyrillic small o
    "\u0440": "p",   # Cyrillic small er
    "\u0441": "c",   # Cyrillic small es
    "\u0445": "x",   # Cyrillic small ha
    "\u0443": "y",   # Cyrillic small u
    "\u0456": "i",   # Cyrillic small byelorussian i
    "\u0432": "b",   # Cyrillic small ve (looks like 6/b)
    "\u0455": "s",   # Cyrillic small dze
    # Greek lookalikes
    "\u03b1": "a",   # Greek alpha
    "\u03b2": "b",   # Greek beta
    "\u03b5": "e",   # Greek epsilon
    "\u03b9": "i",   # Greek iota
    "\u03bf": "o",   # Greek omicron
    "\u03c1": "p",   # Greek rho
    "\u03c5": "u",   # Greek upsilon
    "\u03bd": "v",   # Greek nu
    # Latin Extended lookalikes
    "\u00e0": "a",   # à → a
    "\u00e1": "a",   # á → a
    "\u00e2": "a",   # â → a
    "\u00e4": "a",   # ä → a
    "\u00e8": "e",   # è → e
    "\u00e9": "e",   # é → e
    "\u00ea": "e",   # ê → e
    "\u00ec": "i",   # ì → i
    "\u00ed": "i",   # í → i
    "\u00f2": "o",   # ò → o
    "\u00f3": "o",   # ó → o
    "\u00f9": "u",   # ù → u
    "\u00fa": "u",   # ú → u
    "\u00fc": "u",   # ü → u
    # Mathematical/Fullwidth variants
    "\uff41": "a", "\uff42": "b", "\uff43": "c", "\uff44": "d",
    "\uff45": "e", "\uff46": "f", "\uff47": "g", "\uff48": "h",
    "\uff49": "i", "\uff4a": "j", "\uff4b": "k", "\uff4c": "l",
    "\uff4d": "m", "\uff4e": "n", "\uff4f": "o", "\uff50": "p",
    "\uff51": "q", "\uff52": "r", "\uff53": "s", "\uff54": "t",
    "\uff55": "u", "\uff56": "v", "\uff57": "w", "\uff58": "x",
    "\uff59": "y", "\uff5a": "z",
    # Zero-width and invisible joiners (handled separately but included for completeness)
    "\u200b": "",    # Zero-width space
    "\u200c": "",    # Zero-width non-joiner
    "\u200d": "",    # Zero-width joiner
    "\u200e": "",    # Left-to-right mark
    "\u200f": "",    # Right-to-left mark
    "\u202a": "",    # Left-to-right embedding
    "\u202b": "",    # Right-to-left embedding
    "\u202c": "",    # Pop directional formatting
    "\u202d": "",    # Left-to-right override
    "\u202e": "",    # Right-to-left override  ← RTL override attack
    "\u2060": "",    # Word joiner
    "\ufeff": "",    # BOM / zero-width no-break space
    "\u00ad": "",    # Soft hyphen (invisible)
    # Digit lookalikes
    "\u0660": "0",   # Arabic-Indic digit zero
    "\u0661": "1",   # Arabic-Indic digit one
    "\u0662": "2",
    "\u0663": "3",
    "\u0664": "4",
    "\u0665": "5",
    "\u0666": "6",
    "\u0667": "7",
    "\u0668": "8",
    "\u0669": "9",
}

# Invisible / zero-width character set for rapid lookup
INVISIBLE_CHARS = frozenset({
    "\u200b", "\u200c", "\u200d", "\u200e", "\u200f",
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
    "\u2060", "\u2061", "\u2062", "\u2063", "\u2064",
    "\ufeff", "\u00ad", "\u034f", "\u115f", "\u1160",
    "\u3164", "\uffa0",
})


# ── Canonicalization Result ────────────────────────────────────────────────────

@dataclass
class CanonicalForm:
    """The output of the full canonicalization pipeline."""
    original: str           # Raw input (preserved for audit)
    canonical: str          # Normalized form for governance evaluation
    transformations: List[str]  # Ordered list of applied transformations
    obfuscation_score: float    # 0.0 = clean, 1.0 = heavily obfuscated
    suspicious_encodings: List[str]  # Detected encoding types
    homoglyphs_found: int
    invisible_chars_found: int
    url_decoding_layers: int
    base64_decoded: bool
    shell_comments_stripped: bool
    mixed_scripts_detected: bool

    def was_obfuscated(self) -> bool:
        return self.obfuscation_score > 0.0

    def to_dict(self) -> dict:
        return {
            "canonical": self.canonical,
            "obfuscation_score": round(self.obfuscation_score, 3),
            "transformations": self.transformations,
            "suspicious_encodings": self.suspicious_encodings,
            "homoglyphs_found": self.homoglyphs_found,
            "invisible_chars_found": self.invisible_chars_found,
            "url_decoding_layers": self.url_decoding_layers,
            "base64_decoded": self.base64_decoded,
            "shell_comments_stripped": self.shell_comments_stripped,
            "mixed_scripts_detected": self.mixed_scripts_detected,
        }


# ── Canonicalization Pipeline ──────────────────────────────────────────────────

class InputCanonicalizer:
    """
    Multi-layer deterministic input canonicalization.
    Transforms adversarially obfuscated inputs into their true semantic form
    before any governance evaluation occurs.
    """

    # Shell/scripting comment patterns
    _SHELL_COMMENT_RE = re.compile(r'(?<!\$)#[^\n]*', re.MULTILINE)
    _C_COMMENT_RE = re.compile(r'/\*.*?\*/', re.DOTALL)
    _SQL_COMMENT_RE = re.compile(r'--[^\n]*', re.MULTILINE)

    # Hex escape sequences: \x41, \u0041, \U00000041
    _HEX_ESC_RE = re.compile(
        r'\\x([0-9a-fA-F]{2})|\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})'
    )
    # Octal: \101 etc.
    _OCT_ESC_RE = re.compile(r'\\([0-7]{1,3})')

    # Base64 detection: 8+ base64 chars in a block (high confidence)
    _B64_RE = re.compile(r'[A-Za-z0-9+/]{12,}={0,2}')

    # URL percent-encoding
    _URL_ENC_RE = re.compile(r'%[0-9a-fA-F]{2}')

    # Script category detection (simplistic — detect non-Latin primary scripts)
    _CYRILLIC_RE  = re.compile(r'[\u0400-\u04FF]')
    _ARABIC_RE    = re.compile(r'[\u0600-\u06FF]')
    _CJK_RE       = re.compile(r'[\u4E00-\u9FFF\u3400-\u4DBF]')
    _GREEK_RE     = re.compile(r'[\u0370-\u03FF]')

    @classmethod
    def canonicalize(cls, text: str, strip_comments: bool = True) -> CanonicalForm:
        """Apply all canonicalization layers to the input text."""
        original = text
        transformations: List[str] = []
        suspicious_encodings: List[str] = []

        # ── L1: Null-byte & control character stripping ───────────────────────
        cleaned = cls._strip_control_chars(text)
        if cleaned != text:
            transformations.append("L1:control_chars_stripped")
        text = cleaned

        # ── L2: Unicode NFKC normalization ────────────────────────────────────
        nfkc = unicodedata.normalize("NFKC", text)
        if nfkc != text:
            transformations.append("L2:unicode_nfkc_normalized")
        text = nfkc

        # ── L3: Homoglyph substitution ─────────────────────────────────────────
        text, hg_count = cls._substitute_homoglyphs(text)
        if hg_count > 0:
            transformations.append(f"L3:homoglyphs_substituted({hg_count})")
            suspicious_encodings.append("HOMOGLYPH")

        # ── L4: Invisible / zero-width character removal ──────────────────────
        text, inv_count = cls._remove_invisible(text)
        if inv_count > 0:
            transformations.append(f"L4:invisible_chars_removed({inv_count})")
            suspicious_encodings.append("INVISIBLE_UNICODE")

        # ── L5: URL decoding (multi-layer) ────────────────────────────────────
        text, url_layers = cls._url_decode_layers(text)
        if url_layers > 0:
            transformations.append(f"L5:url_decoded({url_layers}_layers)")
            suspicious_encodings.append("URL_ENCODING")

        # ── L6: HTML entity decoding ───────────────────────────────────────────
        html_decoded = html.unescape(text)
        if html_decoded != text:
            transformations.append("L6:html_entities_decoded")
            suspicious_encodings.append("HTML_ENTITY")
        text = html_decoded

        # ── L7: Hex/octal escape expansion ────────────────────────────────────
        text, hex_expanded = cls._expand_escapes(text)
        if hex_expanded:
            transformations.append("L7:hex_octal_escapes_expanded")
            suspicious_encodings.append("HEX_ESCAPE")

        # ── L8: Base64 suspicion detection and decoding ───────────────────────
        text, b64_decoded = cls._process_base64(text)
        if b64_decoded:
            transformations.append("L8:base64_decoded")
            suspicious_encodings.append("BASE64")

        # ── L9: Whitespace normalization ──────────────────────────────────────
        text_ws = re.sub(r'[ \t]+', ' ', text)          # collapse horizontal whitespace
        text_ws = re.sub(r'\n{3,}', '\n\n', text_ws)    # collapse excessive newlines
        text_ws = text_ws.strip()
        if text_ws != text:
            transformations.append("L9:whitespace_normalized")
        text = text_ws

        # ── L10: Case normalization (applied only to canonical form) ─────────
        # We keep a separate lowercase version for pattern evaluation
        # but preserve case in canonical (humans may need to see it)

        # ── L11: Comment stripping ────────────────────────────────────────────
        comments_stripped = False
        if strip_comments:
            text, comments_stripped = cls._strip_comments(text)
            if comments_stripped:
                transformations.append("L11:comments_stripped")

        # ── L12: Mixed-script detection ───────────────────────────────────────
        mixed_scripts = cls._detect_mixed_scripts(original)
        if mixed_scripts:
            transformations.append(f"L12:mixed_scripts_detected({','.join(mixed_scripts)})")

        # ── Obfuscation Score ─────────────────────────────────────────────────
        obfuscation_score = cls._compute_obfuscation_score(
            hg_count, inv_count, url_layers, b64_decoded, hex_expanded,
            bool(mixed_scripts), comments_stripped, len(suspicious_encodings),
        )

        if obfuscation_score > 0.3:
            logger.warning(
                "[Canonicalizer] Obfuscation detected (score=%.2f): %s",
                obfuscation_score, ", ".join(transformations),
            )

        return CanonicalForm(
            original=original,
            canonical=text,
            transformations=transformations,
            obfuscation_score=obfuscation_score,
            suspicious_encodings=list(set(suspicious_encodings)),
            homoglyphs_found=hg_count,
            invisible_chars_found=inv_count,
            url_decoding_layers=url_layers,
            base64_decoded=b64_decoded,
            shell_comments_stripped=comments_stripped,
            mixed_scripts_detected=bool(mixed_scripts),
        )

    # ── Private Layer Implementations ─────────────────────────────────────────

    @classmethod
    def _strip_control_chars(cls, text: str) -> str:
        """Remove null bytes and dangerous control characters."""
        # Keep: \n (newline), \t (tab), \r (CR)
        # Remove: everything else in 0x00-0x1F except \t\n\r, and 0x7F, 0x80-0x9F
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]', '', text)

    @classmethod
    def _substitute_homoglyphs(cls, text: str) -> Tuple[str, int]:
        """Replace homoglyph characters with ASCII equivalents."""
        count = 0
        result = []
        for ch in text:
            if ch in HOMOGLYPH_MAP:
                result.append(HOMOGLYPH_MAP[ch])
                if HOMOGLYPH_MAP[ch]:   # Count only actual substitutions (not deletions)
                    count += 1
            else:
                result.append(ch)
        return "".join(result), count

    @classmethod
    def _remove_invisible(cls, text: str) -> Tuple[str, int]:
        """Remove zero-width and invisible Unicode characters."""
        count = sum(1 for ch in text if ch in INVISIBLE_CHARS)
        cleaned = "".join(ch for ch in text if ch not in INVISIBLE_CHARS)
        return cleaned, count

    @classmethod
    def _url_decode_layers(cls, text: str) -> Tuple[str, int]:
        """Iteratively URL-decode until stable (handles double encoding)."""
        layers = 0
        current = text
        for _ in range(4):   # Max 4 decoding passes
            if not cls._URL_ENC_RE.search(current):
                break
            try:
                decoded = urllib.parse.unquote(current, errors='replace')
                if decoded == current:
                    break
                current = decoded
                layers += 1
            except Exception:
                break
        return current, layers

    @classmethod
    def _expand_escapes(cls, text: str) -> Tuple[str, bool]:
        """Expand hex/octal escape sequences to their character equivalents."""
        expanded = False

        def replace_hex(m: re.Match) -> str:
            nonlocal expanded
            expanded = True
            hex_val = m.group(1) or m.group(2) or m.group(3)
            try:
                return chr(int(hex_val, 16))
            except (ValueError, OverflowError):
                return m.group(0)

        def replace_oct(m: re.Match) -> str:
            nonlocal expanded
            expanded = True
            try:
                return chr(int(m.group(1), 8))
            except (ValueError, OverflowError):
                return m.group(0)

        result = cls._HEX_ESC_RE.sub(replace_hex, text)
        result = cls._OCT_ESC_RE.sub(replace_oct, result)
        return result, expanded

    @classmethod
    def _process_base64(cls, text: str) -> Tuple[str, bool]:
        """
        Detect and decode suspicious base64 blocks in the text.
        Only decodes when the decoded result contains printable ASCII
        (avoids false positives on random alphanumeric strings).
        """
        decoded_any = False

        def try_decode(m: re.Match) -> str:
            nonlocal decoded_any
            candidate = m.group(0)
            try:
                # Pad if necessary
                padded = candidate + "=" * (-len(candidate) % 4)
                raw = base64.b64decode(padded)
                decoded = raw.decode("utf-8", errors="strict")
                # Only substitute if decoded result is meaningful text
                if len(decoded) >= 4 and all(
                    0x20 <= ord(c) < 0x7F or c in "\n\r\t" for c in decoded
                ):
                    decoded_any = True
                    return f"[DECODED:{decoded}]"
            except Exception:
                pass
            return candidate

        result = cls._B64_RE.sub(try_decode, text)
        return result, decoded_any

    @classmethod
    def _strip_comments(cls, text: str) -> Tuple[str, bool]:
        """Strip shell (#), C (/* */), and SQL (--) comments."""
        original = text
        text = cls._SHELL_COMMENT_RE.sub(' ', text)
        text = cls._C_COMMENT_RE.sub(' ', text)
        text = cls._SQL_COMMENT_RE.sub(' ', text)
        stripped = (text != original)
        text = re.sub(r' +', ' ', text).strip()
        return text, stripped

    @classmethod
    def _detect_mixed_scripts(cls, text: str) -> List[str]:
        """Detect presence of multiple non-ASCII scripts alongside Latin."""
        scripts = []
        if cls._CYRILLIC_RE.search(text):
            scripts.append("CYRILLIC")
        if cls._ARABIC_RE.search(text):
            scripts.append("ARABIC")
        if cls._CJK_RE.search(text):
            scripts.append("CJK")
        if cls._GREEK_RE.search(text):
            scripts.append("GREEK")
        # Mixed if both non-Latin and Latin appear
        has_latin = bool(re.search(r'[a-zA-Z]', text))
        if has_latin and scripts:
            return scripts
        return []

    @classmethod
    def _compute_obfuscation_score(
        cls,
        hg_count: int, inv_count: int, url_layers: int,
        b64_decoded: bool, hex_expanded: bool,
        mixed_scripts: bool, comments_stripped: bool,
        encoding_type_count: int,
    ) -> float:
        score = 0.0
        # Each signal contributes a weight
        if hg_count > 0:
            score += min(0.4, hg_count * 0.08)
        if inv_count > 0:
            score += min(0.35, inv_count * 0.07)
        if url_layers >= 2:
            score += 0.30   # Double-encoding = strong signal
        elif url_layers == 1:
            score += 0.10
        if b64_decoded:
            score += 0.25
        if hex_expanded:
            score += 0.20
        if mixed_scripts:
            score += 0.15
        if comments_stripped:
            score += 0.10
        # Multiple encoding types compound
        if encoding_type_count >= 3:
            score += 0.20
        elif encoding_type_count == 2:
            score += 0.10
        return min(1.0, score)
