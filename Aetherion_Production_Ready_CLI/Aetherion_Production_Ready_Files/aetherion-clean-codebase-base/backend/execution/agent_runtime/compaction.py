"""
backend/execution/agent_runtime/compaction.py — Bounded agent conversation history compaction.

Guarantees:
- Never orphans a tool_call_id: assistant tool calls and corresponding tool results
  are always kept together or compacted together in complete turn groups.
- Preserves the system instruction (if present).
- Preserves the original user task prompt.
- Preserves the most recent active dialogue turns (tail).
- Compresses middle dialogue turns into a compact summary block.
- Fallback safe: if compaction cannot guarantee 100% paired integrity, original messages are kept.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("aetherion.agent_runtime.compaction")

DEFAULT_MAX_MESSAGES: int = 16
DEFAULT_MIN_TAIL_MESSAGES: int = 6


def _extract_assistant_call_ids(msg: Dict[str, Any]) -> Set[str]:
    """Extract all tool call IDs declared in an assistant message."""
    ids: Set[str] = set()
    if msg.get("tool_calls") and isinstance(msg["tool_calls"], list):
        for tc in msg["tool_calls"]:
            if isinstance(tc, dict) and tc.get("id"):
                ids.add(str(tc["id"]))
            elif hasattr(tc, "id") and getattr(tc, "id"):
                ids.add(str(getattr(tc, "id")))
    if msg.get("tool_call_id"):
        ids.add(str(msg["tool_call_id"]))
    return ids


def _validate_paired_integrity(messages: List[Dict[str, Any]]) -> bool:
    """Verify that every tool result matches an assistant tool call and vice-versa."""
    declared_ids: Set[str] = set()
    used_result_ids: Set[str] = set()

    for m in messages:
        role = m.get("role", "")
        if role == "assistant":
            declared_ids.update(_extract_assistant_call_ids(m))
        elif role == "tool":
            tcid = m.get("tool_call_id")
            if tcid:
                used_result_ids.add(str(tcid))

    # Every tool result must have a declared assistant call
    for rid in used_result_ids:
        if rid not in declared_ids:
            return False

    # Every declared assistant call should have a matching tool result (if already executed)
    for did in declared_ids:
        if did not in used_result_ids:
            # If the assistant message is the very last message in history, the tool is pending execution.
            # Otherwise, an assistant tool call in the middle without a result is invalid.
            last_msg = messages[-1] if messages else {}
            if last_msg.get("role") != "assistant" or did not in _extract_assistant_call_ids(last_msg):
                return False

    return True


def compact_history(
    messages: List[Dict[str, Any]],
    max_messages: int = DEFAULT_MAX_MESSAGES,
    min_tail_messages: int = DEFAULT_MIN_TAIL_MESSAGES,
) -> List[Dict[str, Any]]:
    """Compact conversation history while strictly maintaining turn pairing and message bounds.

    Returns the compacted messages list. If messages length is <= max_messages,
    it returns the messages unchanged.
    """
    if len(messages) <= max_messages:
        return list(messages)

    # 1. Identify head (system message and initial user task)
    head_indices: List[int] = []
    idx = 0
    if idx < len(messages) and messages[idx].get("role") == "system":
        head_indices.append(idx)
        idx += 1

    # First user message (initial task prompt)
    if idx < len(messages) and messages[idx].get("role") == "user":
        head_indices.append(idx)
        idx += 1

    head_end = idx

    # 2. Identify tail (most recent messages)
    # Start at len(messages) - min_tail_messages
    tail_start = max(head_end, len(messages) - min_tail_messages)

    # Invariant: Never split in the middle of a tool execution block.
    # If messages[tail_start] is a "tool" message, walk backward to include the assistant message.
    while tail_start > head_end and messages[tail_start].get("role") == "tool":
        tail_start -= 1

    # If the message at tail_start is an assistant with tool_calls, but earlier tool results belong to it,
    # ensure we have not separated paired messages.
    # Also verify that any tool result in tail has its assistant call in tail:
    tail_declared_ids: Set[str] = set()
    for m in messages[tail_start:]:
        if m.get("role") == "assistant":
            tail_declared_ids.update(_extract_assistant_call_ids(m))

    for m in messages[tail_start:]:
        if m.get("role") == "tool":
            tcid = str(m.get("tool_call_id", ""))
            if tcid and tcid not in tail_declared_ids:
                # Need to walk tail_start further backward to find the initiating assistant message
                while tail_start > head_end and tcid not in tail_declared_ids:
                    tail_start -= 1
                    if messages[tail_start].get("role") == "assistant":
                        tail_declared_ids.update(_extract_assistant_call_ids(messages[tail_start]))

    # Middle slice to compact
    middle_msgs = messages[head_end:tail_start]
    if not middle_msgs:
        return list(messages)

    # 3. Summarize the middle execution steps
    summary_lines: List[str] = []
    step_num = 1
    for m in middle_msgs:
        role = m.get("role", "")
        if role == "assistant":
            content_snippet = (m.get("content") or "").strip().replace("\n", " ")[:120]
            tcs = m.get("tool_calls") or ([m.get("tool_call")] if m.get("tool_call") else [])
            tc_names = [tc.get("name") for tc in tcs if isinstance(tc, dict) and tc.get("name")]
            if tc_names:
                summary_lines.append(f"- Step {step_num}: Called {', '.join(tc_names)}. Thought: {content_snippet or 'N/A'}")
            elif content_snippet:
                summary_lines.append(f"- Step {step_num}: Thought: {content_snippet}")
            step_num += 1
        elif role == "tool":
            t_name = m.get("name", "tool")
            res_content = (m.get("content") or "").strip().replace("\n", " ")[:100]
            summary_lines.append(f"  Result ({t_name}): {res_content}")

    omitted_count = len(middle_msgs)
    summary_text = (
        f"[CONVERSATION SUMMARY: {omitted_count} earlier intermediate message(s) compacted]\n"
        + "\n".join(summary_lines[-12:])  # keep last 12 summary points
        + "\n[End of compacted intermediate summary]"
    )

    summary_user_msg = {
        "role": "user",
        "content": summary_text,
    }

    # 4. Assemble candidate compacted messages
    compacted: List[Dict[str, Any]] = []
    for h_idx in head_indices:
        compacted.append(messages[h_idx])
    compacted.append(summary_user_msg)
    for m in messages[tail_start:]:
        compacted.append(m)

    # 5. Strict verification of paired integrity:
    if not _validate_paired_integrity(compacted):
        logger.warning("Compaction would create orphaned tool calls; falling back to original history.")
        return list(messages)

    return compacted
