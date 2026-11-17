"""
tests/integration/test_autonomous_runtime.py
Comprehensive automated test suite for Phase 1 Real Autonomous Agent Runtime.

Covers all 10 required specification test cases + CLI & TUI integration:
  Test 1  — Simple file creation
  Test 2  — Multi-step coding task
  Test 3  — Failure and repair loop
  Test 4  — Command failure & non-zero exit
  Test 5  — Cancellation & cancel-wins semantics
  Test 6  — Workspace boundary & sandbox traversal blocking
  Test 7  — Tool result propagation into next agent turn
  Test 8  — Verification truth (no false success)
  Test 9  — Provider failure & error reporting
  Test 10 — Session continuity
  Test 11 — End-to-end CLI execution
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest
from click.testing import CliRunner

# Ensure backend and cli are on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from execution.agent_runtime import (
    AgentToolRegistry,
    DeterministicTestProvider,
    ExecutionCancelledException,
    NOT_VERIFIED,
    OfflineFallbackProvider,
    PolicyViolationException,
    RealAgentRuntime,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_REPAIRING,
    SandboxBoundaryException,
    ToolRequest,
    VERIFICATION_FAILED,
    VERIFIED,
    VerificationEngine,
    WorkspaceBoundary,
)


@pytest.fixture
def test_workspace():
    """Create a temporary isolated workspace for agent execution tests."""
    temp_dir = tempfile.mkdtemp(prefix="aetherion_test_ws_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


# ── TEST 1: Simple File Creation ──────────────────────────────────────────────
def test_01_simple_file_creation(test_workspace: Path):
    """Prompt: 'Create hello.py that prints Hello Aetherion.'

    Expected:
      - Actual file created in workspace
      - Actual content correct
      - Verification succeeds (VERIFIED)
      - Final status is 'done'
    """
    session_id = "test_s01"
    provider = DeterministicTestProvider()
    runtime = RealAgentRuntime(
        workspace_dir=test_workspace,
        session_id=session_id,
        provider=provider,
    )

    events = list(runtime.run_stream("Create hello.py that prints Hello Aetherion."))

    target_file = test_workspace / "hello.py"
    assert target_file.exists(), "hello.py was not created on disk"
    content = target_file.read_text(encoding="utf-8").strip()
    assert 'Hello Aetherion' in content, f"File content incorrect: {content}"

    # Verify stream events
    event_types = [e.get("type") for e in events]
    assert "queued" in event_types
    assert "state_change" in event_types
    assert "tool_call" in event_types

    done_event = [e for e in events if e.get("type") == "done"][0]
    assert done_event["status"] == "done"
    assert done_event["verification"] == VERIFIED
    assert "hello.py" in done_event.get("files", [])


# ── TEST 2: Multi-Step Coding Task ────────────────────────────────────────────
def test_02_multistep_coding_task(test_workspace: Path):
    """Prompt: 'Create a Python module implementing factorial and add tests.'

    Expected:
      - inspects workspace
      - creates factorial.py
      - creates test_factorial.py
      - runs tests via runner
      - final verification succeeds
    """
    session_id = "test_s02"
    provider = DeterministicTestProvider()
    runtime = RealAgentRuntime(
        workspace_dir=test_workspace,
        session_id=session_id,
        provider=provider,
    )

    events = list(runtime.run_stream("Create a Python module implementing factorial and add tests."))

    fact_file = test_workspace / "factorial.py"
    test_file = test_workspace / "test_factorial.py"
    assert fact_file.exists(), "factorial.py was not created"
    assert test_file.exists(), "test_factorial.py was not created"

    # Verify tool calls included write_file and run_command
    tool_calls = [e.get("tool") for e in events if e.get("type") == "tool_call"]
    assert "inspect_environment" in tool_calls
    assert "write_file" in tool_calls
    assert "run_command" in tool_calls

    done_event = [e for e in events if e.get("type") == "done"][0]
    assert done_event["status"] == "done"
    assert done_event["verification"] == VERIFIED


# ── TEST 3: Failure and Automated Repair Loop ─────────────────────────────────
def test_03_failure_and_repair(test_workspace: Path):
    """Injected failure scenario:

    - Agent initially creates code with a bug (returns 999 for fib(1))
    - Test execution fails
    - Agent observes failure, transitions to REPAIRING state
    - Agent repairs code via edit_file
    - Reruns tests and verifies
    - Final verification passes
    """
    session_id = "test_s03"
    provider = DeterministicTestProvider(inject_failure_once=True)
    runtime = RealAgentRuntime(
        workspace_dir=test_workspace,
        session_id=session_id,
        provider=provider,
    )

    prompt = "Create a Python file called fibonacci.py with a function fibonacci(n) and tests for n=0, 1, and 10."
    events = list(runtime.run_stream(prompt))

    # Check that repair state was entered
    states = [e.get("status") for e in events if e.get("type") == "state_change"]
    assert STATE_REPAIRING in states, f"Expected STATE_REPAIRING in {states}"

    # Check that edit_file tool was invoked
    tool_calls = [e.get("tool") for e in events if e.get("type") == "tool_call"]
    assert "edit_file" in tool_calls, f"Expected edit_file in {tool_calls}"

    # Verify file on disk is corrected
    fib_content = (test_workspace / "fibonacci.py").read_text(encoding="utf-8")
    assert "return 1" in fib_content
    assert "999" not in fib_content

    done_event = [e for e in events if e.get("type") == "done"][0]
    assert done_event["status"] == "done"
    assert done_event["verification"] == VERIFIED


# ── TEST 4: Command Failure and Non-Zero Exit ─────────────────────────────────
def test_04_command_failure(test_workspace: Path):
    """Force a known failing command and ensure system captures non-zero exit

    and reports failure without fabricating success.
    """
    boundary = WorkspaceBoundary(test_workspace)
    res = boundary.run_command(f'{sys.executable} -c "import sys; sys.exit(42)"')

    assert not res.ok
    assert res.exit_code == 42
    assert res.error is not None

    # Now run via runtime with forced failing command prompt
    session_id = "test_s04"
    provider = DeterministicTestProvider()
    runtime = RealAgentRuntime(
        workspace_dir=test_workspace,
        session_id=session_id,
        provider=provider,
    )

    events = list(runtime.run_stream("force_failing_command"))
    done_event = [e for e in events if e.get("type") == "done"][0]
    assert done_event["status"] == "failed"
    assert done_event["verification"] == VERIFICATION_FAILED


# ── TEST 5: Cancellation ──────────────────────────────────────────────────────
def test_05_cancellation(test_workspace: Path):
    """Cancel a running task and ensure:

    - Task transitions to CANCELLED
    - Active process terminates
    - Task is never marked COMPLETED
    """
    session_id = "test_s05"
    cancel_event = threading.Event()
    cancel_event.set()  # Cancel immediately

    provider = DeterministicTestProvider()
    runtime = RealAgentRuntime(
        workspace_dir=test_workspace,
        session_id=session_id,
        provider=provider,
    )

    events = list(runtime.run_stream("Create hello.py", cancel_event=cancel_event))

    states = [e.get("status") for e in events if e.get("type") == "state_change"]
    assert STATE_CANCELLED in states
    assert STATE_COMPLETED not in states

    done_event = [e for e in events if e.get("type") == "done"][0]
    assert done_event["status"] == "cancelled"

    # Confirm hello.py was not created because task was cancelled
    assert not (test_workspace / "hello.py").exists()


# ── TEST 6: Workspace Boundary & Traversal Protection ─────────────────────────
def test_06_workspace_boundary_protection(test_workspace: Path):
    """Attempt access outside the permitted workspace:

    - Traversal via '../'
    - Absolute path outside workspace
    - Policy block on forbidden command 'rm -rf /'
    """
    boundary = WorkspaceBoundary(test_workspace)

    # 1. Directory traversal attempt
    with pytest.raises(SandboxBoundaryException):
        boundary.resolve_safe_path("../../../outside_file.txt")

    # 2. Absolute path outside workspace
    outside = Path(tempfile.gettempdir()) / "malicious.txt"
    if outside.resolve() != test_workspace.resolve():
        with pytest.raises(SandboxBoundaryException):
            boundary.resolve_safe_path(str(outside))

    # 3. Forbidden command blocked
    with pytest.raises(PolicyViolationException):
        boundary.run_command("rm -rf /")


# ── TEST 7: Tool Result Propagation ───────────────────────────────────────────
def test_07_tool_result_propagation(test_workspace: Path):
    """Ensure tool execution output is actually captured and propagated to agent context."""
    boundary = WorkspaceBoundary(test_workspace)
    tools = AgentToolRegistry(boundary)

    # Write a test file
    tools.write_file("data.txt", "alpha_bravo_charlie")

    # Search for it
    search_res = tools.search_files("bravo")
    assert search_res.ok
    assert "alpha_bravo_charlie" in search_res.stdout
    assert len(search_res.data.get("matches", [])) == 1

    # Edit file
    edit_res = tools.edit_file("data.txt", "bravo", "delta")
    assert edit_res.ok

    # Read file and confirm propagation
    read_res = tools.read_file("data.txt")
    assert read_res.ok
    assert "alpha_delta_charlie" in read_res.stdout


# ── TEST 8: Verification Truth (No False Success) ─────────────────────────────
def test_08_verification_truth(test_workspace: Path):
    """Force a task where model claims success but verification fails:

    - Expected files missing or tests failed
    - System must report NOT VERIFIED or FAILED, NEVER fake success.
    """
    boundary = WorkspaceBoundary(test_workspace)
    tools = AgentToolRegistry(boundary)
    verifier = VerificationEngine(boundary, tools)

    # Verify when non-existent files are claimed
    status, details, _ = verifier.verify("Build app", files_created_or_edited=["non_existent_app.py"])
    assert status == NOT_VERIFIED
    assert "does not exist on disk" in details

    # Verify when code has syntax errors
    bad_syntax_file = test_workspace / "bad_syntax.py"
    bad_syntax_file.write_text("def broken_syntax(:\n    pass\n", encoding="utf-8")
    status, details, _ = verifier.verify("Build app", files_created_or_edited=["bad_syntax.py"])
    assert status == VERIFICATION_FAILED
    assert "Syntax verification failed" in details


# ── TEST 9: Provider Failure Handling ─────────────────────────────────────────
def test_09_provider_failure(test_workspace: Path):
    """Simulate model/provider failure and confirm:

    - Error state emitted
    - Task fails cleanly
    - No fabricated execution success
    """
    class CrashingProvider(DeterministicTestProvider):
        def chat(self, messages, tools):
            raise RuntimeError("API quota exhausted: simulated provider failure")

    runtime = RealAgentRuntime(
        workspace_dir=test_workspace,
        session_id="test_s09",
        provider=CrashingProvider(),
    )

    events = list(runtime.run_stream("Create hello.py"))
    err_events = [e for e in events if e.get("type") == "error"]
    assert len(err_events) > 0
    assert "API quota exhausted" in err_events[0]["error"]

    done_event = [e for e in events if e.get("type") == "done"][0]
    assert done_event["status"] == "failed"


# ── TEST 10: Session Continuity ───────────────────────────────────────────────
def test_10_session_continuity(test_workspace: Path):
    """Verify context preservation across multiple interactions in the same workspace."""
    session_id = "test_s10_continuous"

    # Turn 1: Create a base library
    runtime1 = RealAgentRuntime(
        workspace_dir=test_workspace,
        session_id=session_id,
        provider=DeterministicTestProvider(),
    )
    list(runtime1.run_stream("Create hello.py that prints Hello Aetherion."))
    assert (test_workspace / "hello.py").exists()

    # Turn 2: Verify environment inspection in same workspace sees previously created file
    tools = AgentToolRegistry(WorkspaceBoundary(test_workspace))
    env_res = tools.inspect_environment()
    assert env_res.ok
    assert "hello.py" in env_res.data.get("files", [])


# ── TEST 11: End-to-End Real CLI Flow ─────────────────────────────────────────
def test_11_cli_runner_e2e(test_workspace: Path, monkeypatch):
    """Execute end-to-end CLI prompt via click CliRunner."""
    from aetherion_cli.main import cli

    # Mock client stream to return events directly from RealAgentRuntime
    runtime = RealAgentRuntime(
        workspace_dir=test_workspace,
        session_id="test_cli_e2e",
        provider=DeterministicTestProvider(),
    )

    def mock_stream(self, path, payload=None):
        task_prompt = payload.get("task", "")
        yield from runtime.run_stream(task_prompt)

    monkeypatch.setattr("aetherion_cli.client.Client.stream", mock_stream)
    monkeypatch.setattr("aetherion_cli.main._require_auth", lambda client: None)

    runner = CliRunner()
    result = runner.invoke(cli, ["Create hello.py that prints Hello Aetherion."])
    assert result.exit_code == 0
    assert "Done" in result.output or "Task queued" in result.output
