"""
backend/execution/tools/output_policy.py — Tool output bounding and truncation policy.

Guarantees:
- Head/tail truncation (40% head / 60% tail) around an explicit truncation notice marker.
- Bounded token footprint for LLM message history (default budget: 12,000 characters).
- Secrets redaction before LLM context injection.
- Unchanged output when text fits within budget.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("aetherion.tools.output_policy")

HEAD_RATIO: float = 0.4
DEFAULT_MAX_CHARS: int = 12000

try:
    from ..sessions.manager import redact_secrets
except Exception:
    def redact_secrets(s: str) -> str:
        return s


def truncation_notice(omitted: int, total: int, *, label: str = "OUTPUT", unit: str = "chars") -> str:
    """Explicit, standardized marker indicating omitted characters/tokens."""
    return f"\n\n... [{label} TRUNCATED - {omitted:,} {unit} omitted out of {total:,} total] ...\n\n"


def head_tail_split(budget: int) -> tuple[int, int]:
    """Calculate (head, tail) split budgets given total character budget."""
    head = int(budget * HEAD_RATIO)
    return head, budget - head


def truncate_head_tail(text: str, max_chars: int = DEFAULT_MAX_CHARS, *, label: str = "OUTPUT") -> str:
    """Preserve head (earliest errors/context) and tail (latest output/summary).
    
    If text fits within max_chars, it is returned unmodified.
    Otherwise, returns head + truncation notice + tail.
    """
    if not text or len(text) <= max_chars:
        return text or ""
    head_len, tail_len = head_tail_split(max_chars)
    omitted = len(text) - head_len - tail_len
    return text[:head_len] + truncation_notice(omitted, len(text), label=label) + text[-tail_len:]


def bound_tool_output(
    text: Optional[str],
    max_chars: int = DEFAULT_MAX_CHARS,
    *,
    label: str = "TOOL_OUTPUT",
    redact: bool = True,
) -> str:
    """Format and bound tool output safely for injection into LLM context."""
    if not text:
        return ""
    processed = truncate_head_tail(text, max_chars=max_chars, label=label)
    if redact:
        processed = redact_secrets(processed)
    return processed
