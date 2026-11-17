"""
aetherion_cli/rendering.py
Content rendering utilities for the Aetherion CLI + TUI.

Addresses the following gaps identified in the Hermes gap analysis:
  1. Reasoning tag stripping — <think>, <thinking>, <reasoning>, etc.
  2. Tool call XML leak cleanup — <tool_call>, <function_calls>, etc.
  3. Diff rendering — colored unified diff output
  4. Token/duration compact formatting
  5. Streaming-safe text extraction from mixed content
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# 1. Reasoning tag stripping
# ---------------------------------------------------------------------------
# Source-evidence from Hermes cli.py lines 185-263:
# Strips: <think>, <thinking>, <reasoning>, <REASONING_SCRATCHPAD>, <thought>
# Also strips: <tool_call>, <tool_calls>, <function_calls>, <function_calls>

_REASONING_TAGS = (
    "REASONING_SCRATCHPAD",
    "think",
    "thinking",
    "reasoning",
    "thought",
)

_TOOL_CALL_TAGS = (
    "tool_call",
    "tool_calls",
    "tool_result",
    "function_call",
    "function_calls",
)


def strip_reasoning_tags(text: str) -> str:
    """
    Remove reasoning/thinking blocks from displayed text.

    Handles every case:
      * Closed pairs ``<tag>…</tag>`` (case-insensitive, multi-line).
      * Unterminated open tags that run to end-of-text (truncated generations).
      * Stray orphan close tags (``stuff</think>answer``).
      * Tool-call XML blocks that open models leak into visible content.
      * ``<function name="…">`` blocks (Gemma-style).

    Keeps sync with Hermes cli.py::_strip_reasoning_tags.
    """
    cleaned = text
    for tag in _REASONING_TAGS:
        # Closed pair — case-insensitive
        cleaned = re.sub(
            rf"<{tag}>.*?</{tag}>\s*",
            "",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Unterminated open tag — strip from tag to end of text
        cleaned = re.sub(
            rf"<{tag}>.*$",
            "",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Stray orphan close tag
        cleaned = re.sub(
            rf"</{tag}>\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

    # Tool-call XML blocks
    for tc_tag in _TOOL_CALL_TAGS:
        cleaned = re.sub(
            rf"<{tc_tag}\b[^>]*>.*?</{tc_tag}>\s*",
            "",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )

    # <function name="...">…</function> — boundary + attribute gated
    cleaned = re.sub(
        r'(?:(?<=^)|(?<=[\\n\\r.!?:]))[ \\t]*'
        r'<function\\b[^>]*\\bname\\s*=[^>]*>'
        r'(?:(?:(?!</function>).)*)</function>\\s*',
        '',
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Stray tool-call close tags
    cleaned = re.sub(
        r'</(?:tool_call|tool_calls|tool_result|function_call|function_calls|function)>\s*',
        '',
        cleaned,
        flags=re.IGNORECASE,
    )

    return cleaned.strip()


def extract_text_content(content: Any) -> str:
    """
    Extract plain text from mixed content (string | list[{type, text}]).
    Used when the API returns structured assistant content.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return str(content)


def clean_assistant_text(content: Any) -> str:
    """Extract text then strip all reasoning/tool-call tags."""
    return strip_reasoning_tags(extract_text_content(content))


def format_user_facing_response(content: Any) -> str:
    """
    Format assistant response into clean, human-readable user-facing text.
    Strips raw transport JSON envelopes (e.g. {"thought": ..., "response": ...},
    {"choices": [...]}, {"content": ...}) unless the user explicitly requested
    structured output (e.g. formatted inside a ```json markdown block).
    """
    if content is None:
        return ""

    # 1. Extract text and strip reasoning / tool-call XML tags
    text = clean_assistant_text(content)
    if not text:
        return ""

    # If it's already an explicit markdown code block (e.g. ```json ... ```), keep it
    trimmed = text.strip()
    if trimmed.startswith("```"):
        return text

    # Handle bulleted prefix like "* {json}" or "* Thought"
    candidate_json = trimmed
    bullet_prefix = ""
    if trimmed.startswith("* ") and ("{" in trimmed and "}" in trimmed):
        candidate_json = trimmed[2:].strip()
        bullet_prefix = "* "

    # Check if candidate_json is a JSON string or dict
    parsed = None
    if (candidate_json.startswith("{") and candidate_json.endswith("}")) or (
        candidate_json.startswith("[") and candidate_json.endswith("]")
    ):
        try:
            import json
            parsed = json.loads(candidate_json)
        except Exception:
            try:
                import ast
                parsed = ast.literal_eval(candidate_json)
            except Exception:
                parsed = None

    if isinstance(parsed, dict):
        # Extract user-facing message from known transport envelopes
        # 1. Standard OpenAI response envelope: {"choices": [{"message": {"content": ...}}]}
        if "choices" in parsed and isinstance(parsed["choices"], list) and len(parsed["choices"]) > 0:
            c0 = parsed["choices"][0]
            if isinstance(c0, dict):
                msg = c0.get("message")
                if isinstance(msg, dict) and "content" in msg and msg["content"]:
                    return format_user_facing_response(msg["content"])
                if "text" in c0 and c0["text"]:
                    return format_user_facing_response(c0["text"])

        # 2. Provider response / answer fields
        for key in ("response", "answer", "content", "output", "result", "summary", "text", "message"):
            val = parsed.get(key)
            if val and isinstance(val, str) and val.strip():
                return strip_reasoning_tags(val.strip())

        # 3. If only "thought" is present in the dict
        if "thought" in parsed and isinstance(parsed["thought"], str) and parsed["thought"].strip():
            return strip_reasoning_tags(parsed["thought"].strip())

        # 4. Error envelopes: {"error": {"message": ...}} or {"error": "..."}
        if "error" in parsed:
            err = parsed["error"]
            if isinstance(err, dict):
                err_msg = err.get("message") or err.get("detail") or str(err)
                code = err.get("code") or err.get("status")
                return f"Error: {err_msg}" + (f" (code: {code})" if code else "")
            elif isinstance(err, str):
                return f"Error: {err}"

    # If lines within text start with '* {"thought": ...}', extract them line by line
    if "\n" in text and ("{" in text and "}" in text):
        lines = text.split("\n")
        reconstructed = []
        modified = False
        for line in lines:
            s_line = line.strip()
            if s_line.startswith("* {") and s_line.endswith("}"):
                try:
                    import json
                    obj = json.loads(s_line[2:])
                    if isinstance(obj, dict):
                        inner = obj.get("response") or obj.get("thought") or obj.get("content") or obj.get("summary")
                        if inner:
                            reconstructed.append(f"* {inner}")
                            modified = True
                            continue
                except Exception:
                    pass
            reconstructed.append(line)
        if modified:
            return "\n".join(reconstructed)

    return text



# ---------------------------------------------------------------------------
# 2. Diff rendering (colored unified diff)
# ---------------------------------------------------------------------------

_DIFF_ANSI = {
    "added":   "\033[32m",   # green
    "removed": "\033[31m",   # red
    "header":  "\033[36m",   # cyan
    "hunk":    "\033[35m",   # magenta
    "reset":   "\033[0m",
}


def render_diff(text: str, use_color: bool = True) -> str:
    """
    Render a unified diff with ANSI colors.
    Returns the colorized string for display in terminals / Textual Static widgets.
    Falls back to plain text if the input is not a recognizable diff.
    """
    if not text or not text.strip():
        return text or ""

    lines = text.splitlines(keepends=True)
    out: list[str] = []

    is_diff = any(
        ln.startswith(("---", "+++", "@@", "diff --git"))
        for ln in lines[:20]
    )
    if not is_diff or not use_color:
        return text

    for line in lines:
        stripped = line.rstrip("\n")
        if stripped.startswith(("---", "+++")):
            out.append(f"{_DIFF_ANSI['header']}{stripped}{_DIFF_ANSI['reset']}")
        elif stripped.startswith("@@"):
            out.append(f"{_DIFF_ANSI['hunk']}{stripped}{_DIFF_ANSI['reset']}")
        elif stripped.startswith("+"):
            out.append(f"{_DIFF_ANSI['added']}{stripped}{_DIFF_ANSI['reset']}")
        elif stripped.startswith("-"):
            out.append(f"{_DIFF_ANSI['removed']}{stripped}{_DIFF_ANSI['reset']}")
        elif stripped.startswith("diff "):
            out.append(f"{_DIFF_ANSI['header']}{stripped}{_DIFF_ANSI['reset']}")
        else:
            out.append(stripped)

    return "\n".join(out)


def render_diff_rich(text: str) -> str:
    """
    Render a diff for Textual RichLog / Markdown widgets.
    Returns Rich markup string.
    """
    if not text or not text.strip():
        return text or ""

    lines = text.splitlines()
    out: list[str] = []

    is_diff = any(
        ln.startswith(("---", "+++", "@@", "diff --git"))
        for ln in lines[:20]
    )
    if not is_diff:
        return text

    for line in lines:
        if line.startswith(("---", "+++")):
            out.append(f"[bold cyan]{line}[/bold cyan]")
        elif line.startswith("@@"):
            out.append(f"[bold magenta]{line}[/bold magenta]")
        elif line.startswith("+"):
            out.append(f"[green]{line}[/green]")
        elif line.startswith("-"):
            out.append(f"[red]{line}[/red]")
        elif line.startswith("diff "):
            out.append(f"[bold cyan]{line}[/bold cyan]")
        else:
            out.append(line)

    return "\n".join(out)


def detect_diff(text: str) -> bool:
    """Return True if the text appears to be a unified diff."""
    if not text:
        return False
    lines = text.splitlines()
    markers = sum(
        1 for ln in lines[:10]
        if ln.startswith(("---", "+++", "@@", "diff --git"))
    )
    return markers >= 2


# ---------------------------------------------------------------------------
# 3. Token/duration compact formatting (from Hermes cli.py lines 120-141)
# ---------------------------------------------------------------------------

def format_token_count(value: int) -> str:
    """Format a token count compactly: 1234 → '1.23K', 1500000 → '1.5M'."""
    abs_value = abs(value)
    sign = "-" if value < 0 else ""
    if abs_value < 1_000:
        return str(value)
    units = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K"))
    for threshold, suffix in units:
        if abs_value >= threshold:
            scaled = abs_value / threshold
            if scaled < 10:
                text = f"{scaled:.2f}"
            elif scaled < 100:
                text = f"{scaled:.1f}"
            else:
                text = f"{scaled:.0f}"
            text = text.rstrip("0").rstrip(".")
            return f"{sign}{text}{suffix}"
    return f"{value:,}"


def format_duration(seconds: float) -> str:
    """Format elapsed seconds compactly: 90 → '1m 30s', 3700 → '1h 1m'."""
    if seconds < 0:
        seconds = 0.0
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 60:
        secs = int(seconds % 60)
        return f"{int(minutes)}m {secs}s" if secs else f"{int(minutes)}m"
    hours = minutes / 60
    if hours < 24:
        remaining_min = int(minutes % 60)
        return (
            f"{int(hours)}h {remaining_min}m"
            if remaining_min
            else f"{int(hours)}h"
        )
    days = hours / 24
    return f"{days:.1f}d"


def format_context_pct(used: int, total: int) -> str:
    """Format context usage as 'XX%' with colour hint key."""
    if total <= 0:
        return "0%"
    pct = min(100, int(used * 100 / total))
    return f"{pct}%"


def context_pct_colour(used: int, total: int) -> str:
    """Return a CSS/Rich colour string for context usage level."""
    if total <= 0:
        return "green"
    pct = used * 100 / total
    if pct < 50:
        return "green"
    if pct < 75:
        return "yellow"
    if pct < 90:
        return "orange1"
    return "red"


# ---------------------------------------------------------------------------
# 4. Streaming-safe markdown split
# ---------------------------------------------------------------------------

def split_stable_prefix(text: str) -> tuple[str, str]:
    """
    Split streamed text into a stable (renderable) prefix and a
    potentially-incomplete suffix.

    A prefix is stable when:
      - It ends at a double-newline boundary outside a code fence.
      - The last character is \\n\\n.

    Returns (stable_prefix, pending_suffix).
    """
    if not text:
        return "", ""

    # Track code-fence state
    in_fence = False
    fence_marker = ""
    lines = text.split("\n")
    last_stable_pos = 0
    pos = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not in_fence:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = True
                fence_marker = stripped[:3]
            elif i > 0 and lines[i - 1] == "" and stripped == "":
                # Double blank line outside fence → stable boundary
                last_stable_pos = pos + len(line) + 1
        else:
            if stripped == fence_marker or stripped.startswith(fence_marker + " "):
                in_fence = False
        pos += len(line) + 1  # +1 for the \n we split on

    if last_stable_pos == 0 or last_stable_pos >= len(text):
        return "", text

    return text[:last_stable_pos], text[last_stable_pos:]
