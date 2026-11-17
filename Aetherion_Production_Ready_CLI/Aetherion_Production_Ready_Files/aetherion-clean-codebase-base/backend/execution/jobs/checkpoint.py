"""
backend/execution/jobs/checkpoint.py — Checkpoint Schema Validation, Normalization, and Secret Scrubbing.
Treats all persisted and resumed checkpoint metadata as untrusted structured input.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from ..sessions.manager import redact_secrets

logger = logging.getLogger("aetherion.jobs.checkpoint")

MAX_CHECKPOINT_SUMMARY_CHARS = 3000
MAX_CHECKPOINT_THOUGHT_CHARS = 2000
MAX_CHECKPOINT_TOOL_CHARS = 256
MAX_COMPLETED_ACTIONS = 15
MAX_ACTION_SUMMARY_CHARS = 300


CHECKPOINT_VERSION_1 = 1
CHECKPOINT_VERSION_2 = 2
CURRENT_CHECKPOINT_VERSION = 2

ALLOWED_TOOL_STATES = {"COMPLETED", "FAILED", "PENDING", "APPROVED", "SKIPPED"}
MAX_TOOL_STATES = 50


def migrate_checkpoint_to_v2(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Migrate a v1 (or unversioned) checkpoint into the v2 schema with structured
    tool execution states, replay protection, and resumability directives.
    Guarantees idempotency and safe transformation.
    """
    if not isinstance(raw, dict):
        raw = {}

    res = dict(raw)
    res["schema_version"] = CHECKPOINT_VERSION_2

    tool_states: List[Dict[str, Any]] = []
    existing_states = res.get("tool_execution_states")
    if isinstance(existing_states, list) and existing_states:
        # Already has structured states
        tool_states = list(existing_states)
    else:
        # Synthesize from completed_actions
        raw_actions = res.get("completed_actions")
        if isinstance(raw_actions, list):
            for i, item in enumerate(raw_actions):
                if isinstance(item, dict):
                    t_tool = str(item.get("tool", "unknown"))[:MAX_CHECKPOINT_TOOL_CHARS]
                    t_ok = bool(item.get("ok", True))
                    t_sum = redact_secrets(str(item.get("summary", ""))[:MAX_ACTION_SUMMARY_CHARS])
                    t_state = "COMPLETED" if t_ok else "FAILED"
                    tool_states.append({
                        "tool_name": t_tool,
                        "call_id": f"call_{i}_{t_tool.split()[0][:32]}",
                        "state": t_state,
                        "idempotency_key": f"idemp_{i}_{hash(t_tool) & 0xFFFFFFFF}",
                        "result_summary": t_sum,
                        "timestamp": 0.0,
                    })
                elif isinstance(item, str) and item.strip():
                    tool_states.append({
                        "tool_name": item.strip()[:MAX_CHECKPOINT_TOOL_CHARS],
                        "call_id": f"call_{i}_action",
                        "state": "COMPLETED",
                        "idempotency_key": f"idemp_{i}",
                        "result_summary": redact_secrets(item.strip()[:MAX_ACTION_SUMMARY_CHARS]),
                        "timestamp": 0.0,
                    })

    res["tool_execution_states"] = tool_states

    # Build resumability directives
    skip_tools = []
    retry_tools = []
    for ts in tool_states:
        tname = ts.get("tool_name", "")
        if not tname:
            continue
        base_name = tname.split()[0].split("(")[0]
        if ts.get("state") == "COMPLETED":
            if base_name not in skip_tools:
                skip_tools.append(base_name)
        elif ts.get("state") == "FAILED":
            if base_name not in retry_tools:
                retry_tools.append(base_name)

    res["resumability"] = {
        "skip_tools": skip_tools,
        "retry_tools": retry_tools,
        "requires_approval": [],
    }
    return res


def normalize_checkpoint(raw: Any) -> Dict[str, Any]:
    """
    Validate, sanitize, bound, and normalize an untrusted checkpoint dictionary.
    Supports Checkpoint v2 with backward-compatible migration from v1.
    Guarantees safe schema adherence and immunity against malformed/tampered structures.
    """
    if not isinstance(raw, dict):
        return {
            "schema_version": CHECKPOINT_VERSION_2,
            "iteration": 0,
            "summary": "Resumed execution.",
            "completed_actions": [],
            "tool_execution_states": [],
            "resumability": {
                "skip_tools": [],
                "retry_tools": [],
                "requires_approval": [],
            },
            "last_tool": None,
            "last_args": None,
            "last_thought": None,
            "verification": "NOT_VERIFIED",
            "created_or_edited_files": [],
            "elapsed_sec": 0.0,
        }

    # If version is missing or v1, migrate to v2 first
    raw_version = raw.get("schema_version")
    try:
        ver_int = int(raw_version) if raw_version is not None else 1
    except (ValueError, TypeError):
        ver_int = 1

    if ver_int < CHECKPOINT_VERSION_2 or "tool_execution_states" not in raw or "resumability" not in raw:
        raw = migrate_checkpoint_to_v2(raw)

    # 1. Iteration: integer >= 0, bounded to 10000
    try:
        raw_iter = raw.get("iteration", 0)
        iter_val = int(raw_iter) if raw_iter is not None else 0
        iteration = max(0, min(iter_val, 10000))
    except (ValueError, TypeError):
        iteration = 0

    # 2. Summary: string <= 3000 chars, secret redacted
    raw_summary = raw.get("summary")
    if not isinstance(raw_summary, str):
        summary_str = "Execution checkpoint."
    else:
        summary_str = raw_summary[:MAX_CHECKPOINT_SUMMARY_CHARS]
    summary = redact_secrets(summary_str)

    # 3. Last thought: string <= 2000 chars or None, secret redacted
    raw_thought = raw.get("last_thought")
    if isinstance(raw_thought, str):
        last_thought = redact_secrets(raw_thought[:MAX_CHECKPOINT_THOUGHT_CHARS])
    else:
        last_thought = None

    # 4. Last tool: string <= 256 chars or None
    raw_tool = raw.get("last_tool")
    last_tool = str(raw_tool)[:MAX_CHECKPOINT_TOOL_CHARS] if raw_tool is not None else None

    # 5. Last args: dict or None, bounded and values scrubbed
    raw_args = raw.get("last_args")
    if isinstance(raw_args, dict):
        last_args = {}
        for k, v in list(raw_args.items())[:20]:
            k_clean = str(k)[:64]
            if isinstance(v, str):
                last_args[k_clean] = redact_secrets(v[:500])
            elif isinstance(v, (int, float, bool)):
                last_args[k_clean] = v
            else:
                last_args[k_clean] = redact_secrets(str(v)[:500])
    else:
        last_args = None

    # 6. Completed actions: list of dicts only, max 15 entries (backward-compat)
    raw_actions = raw.get("completed_actions")
    valid_actions: List[Dict[str, Any]] = []
    if isinstance(raw_actions, list):
        for item in raw_actions:
            if not isinstance(item, dict):
                continue
            try:
                a_iter = int(item.get("iteration", 0)) if item.get("iteration") is not None else 0
            except (ValueError, TypeError):
                a_iter = 0

            a_tool = str(item.get("tool", "unknown"))[:MAX_CHECKPOINT_TOOL_CHARS]
            a_ok = bool(item.get("ok", True))
            raw_act_sum = item.get("summary", "")
            a_sum = redact_secrets(str(raw_act_sum)[:MAX_ACTION_SUMMARY_CHARS])

            valid_actions.append({
                "iteration": a_iter,
                "tool": a_tool,
                "ok": a_ok,
                "summary": a_sum,
            })

    if len(valid_actions) > MAX_COMPLETED_ACTIONS:
        valid_actions = valid_actions[-MAX_COMPLETED_ACTIONS:]

    # 7. Verification: bounded string
    raw_ver = raw.get("verification")
    verification = str(raw_ver)[:64] if raw_ver is not None else "NOT_VERIFIED"

    # 8. Created or edited files: list of strings
    raw_files = raw.get("created_or_edited_files")
    valid_files: List[str] = []
    if isinstance(raw_files, list):
        for f in raw_files[:50]:
            if isinstance(f, str) and len(f) < 1024:
                valid_files.append(f)

    # 9. Elapsed seconds: float >= 0.0
    try:
        raw_elapsed = raw.get("elapsed_sec", 0.0)
        elapsed_sec = max(0.0, float(raw_elapsed))
    except (ValueError, TypeError):
        elapsed_sec = 0.0

    # 10. Checkpoint v2 Tool Execution States: structured list of dicts, max 50
    raw_states = raw.get("tool_execution_states")
    valid_tool_states: List[Dict[str, Any]] = []
    if isinstance(raw_states, list):
        for ts in raw_states:
            if not isinstance(ts, dict):
                continue
            t_name = str(ts.get("tool_name", "unknown"))[:MAX_CHECKPOINT_TOOL_CHARS]
            t_cid = str(ts.get("call_id", f"call_{len(valid_tool_states)}"))[:64]
            raw_st = str(ts.get("state", "COMPLETED")).upper().strip()
            t_state = raw_st if raw_st in ALLOWED_TOOL_STATES else "COMPLETED"
            t_idemp = str(ts.get("idempotency_key", ""))[:128]
            t_sum = redact_secrets(str(ts.get("result_summary", ""))[:MAX_ACTION_SUMMARY_CHARS])
            try:
                t_ts = float(ts.get("timestamp", 0.0))
            except (ValueError, TypeError):
                t_ts = 0.0

            valid_tool_states.append({
                "tool_name": t_name,
                "call_id": t_cid,
                "state": t_state,
                "idempotency_key": t_idemp,
                "result_summary": t_sum,
                "timestamp": t_ts,
            })

    if len(valid_tool_states) > MAX_TOOL_STATES:
        valid_tool_states = valid_tool_states[-MAX_TOOL_STATES:]

    # 11. Checkpoint v2 Resumability Directives
    raw_resumability = raw.get("resumability")
    if not isinstance(raw_resumability, dict):
        raw_resumability = {}

    skip_tools_list: List[str] = []
    raw_skip = raw_resumability.get("skip_tools", [])
    if isinstance(raw_skip, list):
        for s in raw_skip:
            if isinstance(s, str) and s.strip():
                skip_tools_list.append(s.strip()[:MAX_CHECKPOINT_TOOL_CHARS])

    retry_tools_list: List[str] = []
    raw_retry = raw_resumability.get("retry_tools", [])
    if isinstance(raw_retry, list):
        for r in raw_retry:
            if isinstance(r, str) and r.strip():
                retry_tools_list.append(r.strip()[:MAX_CHECKPOINT_TOOL_CHARS])

    approval_tools_list: List[str] = []
    raw_approval = raw_resumability.get("requires_approval", [])
    if isinstance(raw_approval, list):
        for a in raw_approval:
            if isinstance(a, str) and a.strip():
                approval_tools_list.append(a.strip()[:MAX_CHECKPOINT_TOOL_CHARS])
    elif isinstance(raw_approval, bool) and raw_approval:
        approval_tools_list = ["*"]

    resumability = {
        "skip_tools": skip_tools_list[:50],
        "retry_tools": retry_tools_list[:50],
        "requires_approval": approval_tools_list[:50],
    }

    return {
        "schema_version": CHECKPOINT_VERSION_2,
        "iteration": iteration,
        "summary": summary,
        "last_thought": last_thought,
        "last_tool": last_tool,
        "last_args": last_args,
        "completed_actions": valid_actions,
        "tool_execution_states": valid_tool_states,
        "resumability": resumability,
        "verification": verification,
        "created_or_edited_files": valid_files,
        "elapsed_sec": elapsed_sec,
    }

