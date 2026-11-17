"""
scripts/audit_gate5_agentic_task.py
Independent Adversarial Gate 5 Audit: Challenging Multi-file Coding Task & Verifier Proof
=========================================================================================
Requirements:
1. Real Git repository workspace with isolated worktree attachment.
2. Multi-file coding task (structured logging with request ID propagation, middleware, tests).
3. Real tool calls (inspect, write, run_tests, finish).
4. Verifier truthfulness: fail-closed rejection when tests fail.
5. Autonomous repair loop: handles failure feedback and fixes defect.
6. Durable state persistence and replay recording.
7. Zero false passes.
"""
from __future__ import annotations

import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path

# Add project root and backend to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
CLI_DIR = ROOT_DIR / "cli"
for p in (str(ROOT_DIR), str(BACKEND_DIR), str(CLI_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from backend.execution.agent_runtime import (
    RealAgentRuntime,
    TaskExecutionContext,
    attach_execution_worktree,
    cleanup_execution_worktree,
    get_execution_diff,
    STATE_SUCCEEDED,
    STATE_FAILED,
    STATE_REPAIRING,
    STATE_VERIFYING,
)
from backend.execution.durable_state import get as get_durable_state
from backend.execution.providers.base import BaseModelProvider, ModelResponse, ToolCall
from backend.execution.verification.engine import (
    VERIFIED,
    VERIFICATION_FAILED,
    NOT_VERIFIED,
)


class ScriptedAuditAgent(BaseModelProvider):
    """Adversarial scripted agent executing multi-file operations and repair cycles."""

    def __init__(self, mode: str):
        self.mode = mode
        self._step = 0

    def name(self) -> str:
        return f"scripted_audit_agent_{self.mode}"

    def is_available(self) -> bool:
        return True

    def chat(self, messages, tools):
        self._step += 1

        if self.mode == "fail_closed_test":
            # Introduces a broken implementation and claims SUCCESS
            if self._step == 1:
                broken_code = "def get_request_id():\n    raise RuntimeError('Simulated fatal fault')\n"
                return ModelResponse(
                    text="Writing broken context implementation",
                    tool_calls=[ToolCall(
                        id="call_w1",
                        name="write_file",
                        arguments={"path": "logging_utils/context.py", "content": broken_code}
                    )]
                )
            elif self._step == 2:
                # Falsely claims verified success without running or passing tests
                return ModelResponse(
                    text="Claiming success without valid tests",
                    tool_calls=[ToolCall(
                        id="call_f1",
                        name="finish",
                        arguments={"summary": "I am done and totally verified.", "verification": VERIFIED}
                    )]
                )

        elif self.mode == "multi_file_repair_cycle":
            # Step 1: Inspect environment
            if self._step == 1:
                return ModelResponse(
                    text="Inspecting workspace files",
                    tool_calls=[ToolCall(id="call_i1", name="inspect_environment", arguments={})]
                )
            # Step 2: Write initial broken implementation (causes test failure)
            elif self._step == 2:
                buggy_context = (
                    "import uuid\n"
                    "_rid = '-'\n"
                    "def get_request_id():\n"
                    "    return 'WRONG_ID'\n"
                    "def new_request_id():\n"
                    "    return uuid.uuid4().hex\n"
                    "def set_request_id(rid):\n"
                    "    global _rid\n"
                    "    _rid = rid\n"
                )
                return ModelResponse(
                    text="Writing buggy context implementation",
                    tool_calls=[ToolCall(
                        id="call_w1",
                        name="write_file",
                        arguments={"path": "logging_utils/context.py", "content": buggy_context}
                    )]
                )
            # Step 3: Run test suite to see failure
            elif self._step == 3:
                return ModelResponse(
                    text="Executing test suite",
                    tool_calls=[ToolCall(
                        id="call_t1",
                        name="run_command",
                        arguments={"command": f"{sys.executable} -m pytest tests/test_context.py"}
                    )]
                )
            # Step 4: Finish attempt 1 (fails criteria verification)
            elif self._step == 4:
                return ModelResponse(
                    text="Attempting finish attempt 1",
                    tool_calls=[ToolCall(
                        id="call_f1",
                        name="finish",
                        arguments={"summary": "Attempt 1 completed"}
                    )]
                )
            # Step 5 (Repair cycle): Fix context.py with correct implementation
            elif self._step == 5:
                fixed_context = (
                    "import uuid, contextvars\n"
                    "_req_id_ctx = contextvars.ContextVar('request_id', default='-')\n"
                    "def get_request_id():\n"
                    "    return _req_id_ctx.get()\n"
                    "def new_request_id():\n"
                    "    return uuid.uuid4().hex\n"
                    "def set_request_id(rid):\n"
                    "    token = _req_id_ctx.set(rid)\n"
                    "    return token\n"
                    "def reset_request_id(token):\n"
                    "    if token:\n"
                    "        _req_id_ctx.reset(token)\n"
                )
                return ModelResponse(
                    text="Repairing context.py with contextvars",
                    tool_calls=[ToolCall(
                        id="call_w2",
                        name="write_file",
                        arguments={"path": "logging_utils/context.py", "content": fixed_context}
                    )]
                )
            # Step 6: Also create middleware.py
            elif self._step == 6:
                middleware_code = (
                    "from logging_utils.context import new_request_id, set_request_id, reset_request_id\n"
                    "class RequestIdMiddleware:\n"
                    "    def __init__(self, app):\n"
                    "        self.app = app\n"
                    "    def __call__(self, environ, start_response):\n"
                    "        rid = environ.get('HTTP_X_REQUEST_ID') or new_request_id()\n"
                    "        tok = set_request_id(rid)\n"
                    "        try:\n"
                    "            return self.app(environ, start_response)\n"
                    "        finally:\n"
                    "            reset_request_id(tok)\n"
                )
                return ModelResponse(
                    text="Implementing RequestIdMiddleware in middleware.py",
                    tool_calls=[ToolCall(
                        id="call_w3",
                        name="write_file",
                        arguments={"path": "logging_utils/middleware.py", "content": middleware_code}
                    )]
                )
            # Step 7: Re-run tests to verify repair
            elif self._step == 7:
                return ModelResponse(
                    text="Re-running tests",
                    tool_calls=[ToolCall(
                        id="call_t2",
                        name="run_command",
                        arguments={"command": f"{sys.executable} -m pytest tests/test_context.py"}
                    )]
                )
            # Step 8: Finish repair attempt
            elif self._step == 8:
                return ModelResponse(
                    text="Finishing task after verified repair",
                    tool_calls=[ToolCall(
                        id="call_f2",
                        name="finish",
                        arguments={"summary": "Multi-file structured logging successfully implemented and verified."}
                    )]
                )

        return ModelResponse(text="No further actions.")


def setup_test_git_repo(repo_path: Path):
    """Initialize a real Git repository with test files."""
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "AuditBot"], cwd=str(repo_path), check=True)
    subprocess.run(["git", "config", "user.email", "audit@aetherion.ai"], cwd=str(repo_path), check=True)

    (repo_path / "logging_utils").mkdir(exist_ok=True)
    (repo_path / "logging_utils" / "__init__.py").write_text("# logging_utils package\n", encoding="utf-8")
    (repo_path / "tests").mkdir(exist_ok=True)

    test_file_content = (
        "import pytest\n"
        "from logging_utils.context import get_request_id, set_request_id, reset_request_id\n"
        "\n"
        "def test_default_request_id():\n"
        "    assert get_request_id() == '-'\n"
        "\n"
        "def test_set_and_reset_request_id():\n"
        "    tok = set_request_id('req-audit-999')\n"
        "    assert get_request_id() == 'req-audit-999'\n"
        "    reset_request_id(tok)\n"
        "    assert get_request_id() == '-'\n"
    )
    (repo_path / "tests" / "test_context.py").write_text(test_file_content, encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit with skeleton"], cwd=str(repo_path), check=True, capture_output=True)


def test_gate5():
    print("=" * 70)
    print("GATE 5: INDEPENDENT ADVERSARIAL AGENTIC CODING TASK AUDIT")
    print("=" * 70)

    tmp_dir = Path(tempfile.mkdtemp(prefix="aeth_gate5_audit_"))
    try:
        repo_dir = tmp_dir / "target_repo"
        setup_test_git_repo(repo_dir)
        print(f"[OK] Initialized real Git repository at {repo_dir}")

        # ---------------------------------------------------------------------
        # TEST 1: Adversarial Deliberate Failure -> Runtime MUST NOT Report SUCCESS
        # ---------------------------------------------------------------------
        print("\n--- Test 1: Deliberate Failure Verifier Gate (Fail-Closed) ---")
        fail_provider = ScriptedAuditAgent(mode="fail_closed_test")
        ctx_fail = TaskExecutionContext(
            task_id="audit_task_fail_closed",
            objective="Implement request-ID context and verify with tests",
            workspace_dir=repo_dir,
            verification_criteria={
                "expected_files": ["logging_utils/context.py"],
                "test_command": f"{sys.executable} -m pytest tests/test_context.py",
                "tests_required": True,
            },
            execution_budget={"max_repair_attempts": 0},
        )
        runtime_fail = RealAgentRuntime(context=ctx_fail, provider=fail_provider)
        events_fail = list(runtime_fail.run_stream(ctx_fail.objective))
        done_fail = next((e for e in events_fail if e.get("type") == "done"), None)
        assert done_fail is not None, "Missing 'done' event from runtime"

        print(f"Deliberate failure execution_status: {done_fail.get('execution_status')}")
        print(f"Deliberate failure verification: {done_fail.get('verification')}")

        assert done_fail["execution_status"] == "failed", (
            f"SECURITY DEFECT: Agent claimed SUCCESS despite broken tests! status={done_fail.get('execution_status')}"
        )
        assert done_fail["verification"] in (VERIFICATION_FAILED, NOT_VERIFIED), (
            f"SECURITY DEFECT: Verifier approved broken state! verification={done_fail.get('verification')}"
        )
        print("[PASS] Test 1: Verifier correctly failed closed on deliberate failure.")

        # ---------------------------------------------------------------------
        # TEST 2: Real Multi-file Task with Automated Failure & Repair Loop
        # ---------------------------------------------------------------------
        print("\n--- Test 2: Multi-File Task with Real Repair & Verification ---")
        repair_provider = ScriptedAuditAgent(mode="multi_file_repair_cycle")
        ctx_repair = TaskExecutionContext(
            task_id="audit_task_repair_success",
            objective="Implement multi-file request ID tracking and middleware",
            workspace_dir=repo_dir,
            verification_criteria={
                "expected_files": ["logging_utils/context.py", "logging_utils/middleware.py"],
                "test_command": f"{sys.executable} -m pytest tests/test_context.py",
                "tests_required": True,
            },
            execution_budget={"max_repair_attempts": 2},
        )
        runtime_repair = RealAgentRuntime(context=ctx_repair, provider=repair_provider)
        events_repair = list(runtime_repair.run_stream(ctx_repair.objective))

        # Validate state transitions
        states = [e.get("status") for e in events_repair if e.get("type") == "state_change"]
        print(f"Observed state machine transitions: {states}")
        assert STATE_REPAIRING in states, f"Expected {STATE_REPAIRING} in state transitions"
        assert STATE_VERIFYING in states, f"Expected {STATE_VERIFYING} in state transitions"

        done_repair = next((e for e in events_repair if e.get("type") == "done"), None)
        assert done_repair is not None, "Missing 'done' event from repair runtime"
        print(f"Repair cycle execution_status: {done_repair.get('execution_status')}")
        print(f"Repair cycle verification: {done_repair.get('verification')}")

        assert done_repair["execution_status"] == "succeeded"
        assert done_repair["verification"] == VERIFIED
        print("[PASS] Repair cycle completed with genuine VERIFIED state.")

        # ---------------------------------------------------------------------
        # TEST 3: Verify Durable State & Git Diff Extraction
        # ---------------------------------------------------------------------
        print("\n--- Test 3: Durable State & Git Diff Verification ---")
        durable = get_durable_state(ctx_repair.task_id, tenant_id=ctx_repair.tenant_id)
        assert durable is not None, "Durable state not found in database"
        assert durable["status"] == "SUCCEEDED"
        print(f"Durable state recorded: status={durable['status']}")

        # Verify real diff was extracted
        diff = get_execution_diff(ctx_repair)
        print("Extracted Git Diff preview:")
        for line in diff.splitlines()[:15]:
            print(f"  {line}")
        assert (repo_dir / "logging_utils" / "context.py").exists()
        assert (repo_dir / "logging_utils" / "middleware.py").exists()
        print("[PASS] Multi-file mutations and durable state confirmed on disk.")

        print("\n" + "=" * 70)
        print("GATE 5: ALL 3 AGENTIC TASK AUDIT ASSERTIONS PASSED (100% VERIFIED)")
        print("=" * 70)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_gate5()
