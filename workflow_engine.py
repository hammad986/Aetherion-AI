"""
workflow_engine.py — Minimal Contract Restoration
==================================================
Gate B: Restores the missing workflow_engine.py that was deleted during the
Phase 43 consolidation. Satisfies the import contract used by:

  - web_app.py  (4 import sites)
  - orchestrator.py (1 import site)

Interface contract (from forensic analysis of all call sites):
  get_workflow_engine()              → WorkflowEngine singleton
  engine.list_workflows()            → list[str]
  engine.run(task, language?,        → dict with ok, output, workflow_id
             emit_fn?, force_id?,
             chaos_flags?, user_id?)
  engine.get_result(workflow_id)     → dict | None

Design decisions:
  - run() delegates to the existing Agent pipeline (already realtime-wired)
  - Results stored in a thread-safe in-process dict (sufficient for workers=1)
  - emit_fn forwarded to Agent so SSE events still flow during workflow execution
  - chaos_flags supported for the existing /api/system/chaos-test endpoint
  - This is NOT a new orchestration system — it is restoring a broken import.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

import os

logger = logging.getLogger("nexora.workflow_engine")


# ── Result store ──────────────────────────────────────────────────────────────
# Keyed by workflow_id. In-process is sufficient for workers=1 deployment.
# Results are retained for RESULT_TTL_SECONDS then evicted by _reaper().
_results: Dict[str, Dict[str, Any]] = {}
_results_lock = threading.Lock()
RESULT_TTL_SECONDS = 3600   # 1 hour


def _store_result(wid: str, data: dict) -> None:
    with _results_lock:
        _results[wid] = {"data": data, "ts": time.time()}


def _get_result(wid: str) -> Optional[dict]:
    with _results_lock:
        entry = _results.get(wid)
    if not entry:
        return None
    return entry["data"]


def _start_reaper_once():
    """Background thread that evicts stale results."""
    if getattr(_start_reaper_once, "_started", False):
        return
    _start_reaper_once._started = True

    def _reap():
        while True:
            time.sleep(300)
            cutoff = time.time() - RESULT_TTL_SECONDS
            with _results_lock:
                stale = [wid for wid, v in _results.items() if v["ts"] < cutoff]
                for wid in stale:
                    _results.pop(wid, None)
                    logger.debug(f"[WorkflowEngine] Reaped stale result {wid}")

    t = threading.Thread(target=_reap, daemon=True, name="workflow-result-reaper")
    t.start()


# ── Supported workflow catalogue ───────────────────────────────────────────────
_WORKFLOWS = [
    "generate_and_test",
    "code_review",
    "refactor",
    "debug_and_fix",
    "explain_code",
    "write_tests",
    "full_stack_feature",
    "free_form",
]


# ── WorkflowEngine ─────────────────────────────────────────────────────────────
class WorkflowEngine:
    """
    Minimal workflow execution engine.

    Delegates all task execution to the Agent pipeline so that:
      1. SSE events (agent.think, agent.action, etc.) still flow via emit_fn
      2. The AETHERION_REALTIME_V1 wiring in agent.py is exercised correctly
      3. No duplicate orchestration logic is introduced
    """

    def list_workflows(self) -> list[str]:
        return list(_WORKFLOWS)

    def run(
        self,
        task: str,
        language: str = "python",
        emit_fn: Optional[Callable] = None,
        force_id: Optional[str] = None,
        chaos_flags: Optional[dict] = None,
        user_id: Optional[str] = None,
        **kwargs,
    ) -> dict:
        """
        Execute a task via the Agent pipeline.

        Args:
            task      : Natural-language task description
            language  : Hint for the agent (injected into prompt context)
            emit_fn   : Optional SSE callback — forwarded to Agent
            force_id  : Caller-supplied workflow_id (used by /api/run_workflow)
            chaos_flags: Dict of {flag: bool} for chaos testing endpoints
            user_id   : Authenticated user (stored in result, not used by agent)

        Returns:
            dict with: ok, workflow_id, output, events (if emit_fn collected any)
        """
        wid = force_id or uuid.uuid4().hex[:12]
        _start_reaper_once()

        # ── Chaos flag injection ──────────────────────────────────────────────
        # /api/system/chaos-test sets these flags to simulate failures for testing.
        # We surface them in the result rather than crashing the agent.
        chaos = chaos_flags or {}
        if chaos.get("timeout"):
            logger.info(f"[WorkflowEngine] CHAOS: timeout flag active for {wid}")
            result = {"ok": False, "workflow_id": wid, "error": "chaos_timeout",
                      "output": "", "chaos": True}
            _store_result(wid, result)
            return result

        # ── Language hint injected into prompt ────────────────────────────────
        full_task = task
        if language and language.lower() not in ("any", "auto", ""):
            full_task = f"[Language: {language}]\n{task}"

        # ── Chaos: model_failure ──────────────────────────────────────────────
        if chaos.get("model_failure"):
            result = {"ok": False, "workflow_id": wid, "error": "chaos_model_failure",
                      "output": "", "chaos": True}
            _store_result(wid, result)
            return result

        # ── Delegate to Agent ─────────────────────────────────────────────────
        try:
            from agent import Agent
            from config import Config
            from memory import Memory

            # Collect events locally if emit_fn not provided (for /api/workflows/run)
            _local_events: list = []
            def _combined_emit(kind: str, payload: dict) -> None:
                _local_events.append({"kind": kind, **payload})
                if emit_fn:
                    try:
                        emit_fn(kind, payload)
                    except Exception as _e:
                        logger.warning(f"[WorkflowEngine] emit_fn error: {_e}")

            agent = Agent(
                config=Config(),
                memory=Memory(),
                emit_fn=_combined_emit,
                session_id=f"wf_{wid}",
            )
            output = agent.run(full_task)

            # ── Chaos: tool_failure (post-run, for result poisoning test) ─────
            if chaos.get("tool_failure"):
                result = {"ok": False, "workflow_id": wid,
                          "error": "chaos_tool_failure", "output": str(output),
                          "chaos": True}
            else:
                result = {
                    "ok": True,
                    "workflow_id": wid,
                    "output": str(output) if output else "",
                    "events": _local_events[-50:],   # last 50 events for caller
                    "user_id": user_id,
                    "language": language,
                }

        except Exception as e:
            logger.exception(f"[WorkflowEngine] run({wid}) failed: {e}")
            result = {"ok": False, "workflow_id": wid, "error": str(e), "output": ""}

        _store_result(wid, result)

        # ── Signal SSE stream consumer that workflow is done ──────────────────
        if emit_fn:
            try:
                emit_fn("workflow_done", {"workflow_id": wid, "ok": result["ok"]})
            except Exception:
                pass

        return result

    def get_result(self, workflow_id: str) -> Optional[dict]:
        """Retrieve a stored workflow result by ID."""
        return _get_result(workflow_id)


# ── Singleton ──────────────────────────────────────────────────────────────────
_engine: Optional[WorkflowEngine] = None
_engine_lock = threading.Lock()


def get_workflow_engine() -> WorkflowEngine:
    """Return the process-wide WorkflowEngine singleton."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = WorkflowEngine()
                logger.info("[WorkflowEngine] Singleton initialised.")
    return _engine
