"""
tests/integration/test_remediation_p1_p2.py — Adversarial Verification Suite
for Production Blocker Remediations:
  P1: Verification Engine Fail-Closed Contract & CLI Exit Code Integrity
  P2: Cross-Tenant Mutation Security & HTTP 403 Semantics
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from backend.execution import durable_state
from backend.execution.agent_runtime import (
    AgentToolRegistry,
    DeterministicTestProvider,
    NOT_VERIFIED,
    PARTIALLY_VERIFIED,
    RealAgentRuntime,
    STATE_FAILED,
    STATE_REPAIRING,
    STATE_SUCCEEDED,
    ToolRequest,
    VERIFICATION_FAILED,
    VERIFIED,
    VerificationEngine,
    WorkspaceBoundary,
)
from backend.auth_system import _make_access_token
from backend.app import app


# ==============================================================================
# P1: VERIFICATION ENGINE FAIL-CLOSED ADVERSARIAL TESTS
# ==============================================================================

class TestVerificationEngineFailClosed:
    """Verifies that VerificationEngine rejects empty mutations and never treats
    pre-existing repository files as verification success."""

    @pytest.fixture
    def workspace(self, tmp_path: Path):
        """Create a workspace with pre-existing unrelated python files."""
        ws = tmp_path / "ws"
        ws.mkdir()
        # Seed pre-existing files to simulate repository worktree
        (ws / "pre_existing_app.py").write_text("print('pre-existing')", encoding="utf-8")
        (ws / "utils.py").write_text("def util(): return 42", encoding="utf-8")
        nested = ws / "subpkg"
        nested.mkdir()
        (nested / "module.py").write_text("X = 1", encoding="utf-8")
        return ws

    def test_p1_zero_mutation_with_preexisting_files_returns_not_verified(self, workspace: Path):
        """DEFECT P1 CORE: When task requested code creation and agent made no mutations,
        pre-existing .py files in worktree MUST NOT satisfy verification."""
        boundary = WorkspaceBoundary(workspace)
        tools = AgentToolRegistry(boundary)
        verifier = VerificationEngine(boundary, tools)

        # Task requests creation of math_utils.py, but files_created_or_edited is empty
        status, details, meta = verifier.verify(
            task="Create math_utils.py with add and subtract functions",
            files_created_or_edited=[],
        )

        assert status == NOT_VERIFIED
        assert "pre_existing_app.py" not in meta["verified_files"]
        assert "utils.py" not in meta["verified_files"]
        assert len(meta["verified_files"]) == 0
        assert "No files were created or modified" in details

    def test_p1_expected_file_absent_returns_not_verified(self, workspace: Path):
        """Case A: Criteria specified expected_files, but the expected file was not created."""
        boundary = WorkspaceBoundary(workspace)
        tools = AgentToolRegistry(boundary)
        verifier = VerificationEngine(boundary, tools)

        status, details, meta = verifier.verify(
            task="Implement sorting algorithms",
            files_created_or_edited=[],
            criteria={"expected_files": ["sorters.py"]},
        )

        assert status == NOT_VERIFIED
        assert "sorters.py" in details
        assert len(meta["verified_files"]) == 0

    def test_p1_preexisting_file_matching_expected_name_not_accepted_if_unmodified(self, workspace: Path):
        """Case F: An expected file already existed in the repository, but was NOT touched
        by the agent in this run. Pre-existing files must NEVER satisfy mutation expectation."""
        # Create a pre-existing math_utils.py before verification
        pre_existing = workspace / "math_utils.py"
        pre_existing.write_text("def old_math(): pass\n", encoding="utf-8")

        boundary = WorkspaceBoundary(workspace)
        tools = AgentToolRegistry(boundary)
        verifier = VerificationEngine(boundary, tools)

        # Agent did not mutate anything: files_created_or_edited == []
        status, details, meta = verifier.verify(
            task="Create math_utils.py with vector math functions",
            files_created_or_edited=[],
            criteria={"expected_files": ["math_utils.py"]},
        )

        assert status == NOT_VERIFIED
        assert "math_utils.py" not in meta["verified_files"]

        # Even if files_created_or_edited contains other file, but math_utils.py was not in it
        (workspace / "other.py").write_text("X = 1\n", encoding="utf-8")
        status2, details2, meta2 = verifier.verify(
            task="Create math_utils.py and other.py",
            files_created_or_edited=["other.py"],
            criteria={"expected_files": ["math_utils.py", "other.py"]},
        )
        assert status2 == NOT_VERIFIED
        assert "math_utils.py" in details2
        assert "pre-existing files cannot satisfy mutation" in details2

    def test_p1_edit_task_without_mutation_returns_not_verified(self, workspace: Path):
        """Case C: Task requested editing / fixing, but agent produced no edits."""
        boundary = WorkspaceBoundary(workspace)
        tools = AgentToolRegistry(boundary)
        verifier = VerificationEngine(boundary, tools)

        status, details, meta = verifier.verify(
            task="Fix bug in utils.py where return value was wrong",
            files_created_or_edited=[],
        )

        assert status == NOT_VERIFIED
        assert len(meta["verified_files"]) == 0
        assert "No files were created or modified" in details

    def test_p1_partial_multifile_task_returns_not_verified(self, workspace: Path):
        """Case I: Multiple expected files required, but agent only produced a subset."""
        boundary = WorkspaceBoundary(workspace)
        tools = AgentToolRegistry(boundary)
        verifier = VerificationEngine(boundary, tools)

        (workspace / "file_a.py").write_text("A = 1\n", encoding="utf-8")

        status, details, meta = verifier.verify(
            task="Create file_a.py and file_b.py",
            files_created_or_edited=["file_a.py"],
            criteria={"expected_files": ["file_a.py", "file_b.py"]},
        )

        assert status == NOT_VERIFIED
        assert "file_b.py" in details

    def test_p1_positive_control_genuine_creation_passes(self, workspace: Path):
        """Case G: Agent genuinely creates expected file with valid syntax -> VERIFIED."""
        boundary = WorkspaceBoundary(workspace)
        tools = AgentToolRegistry(boundary)
        verifier = VerificationEngine(boundary, tools)

        (workspace / "math_utils.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

        status, details, meta = verifier.verify(
            task="Create math_utils.py with add function",
            files_created_or_edited=["math_utils.py"],
            criteria={"expected_files": ["math_utils.py"]},
        )

        assert status == VERIFIED
        assert "math_utils.py" in meta["verified_files"]
        assert "Files verified successfully" in details

    def test_p1_positive_control_genuine_edit_passes(self, workspace: Path):
        """Case H: Agent genuinely edits tracked file -> VERIFIED."""
        boundary = WorkspaceBoundary(workspace)
        tools = AgentToolRegistry(boundary)
        verifier = VerificationEngine(boundary, tools)

        # Simulate genuine edit by appending to utils.py
        utils = workspace / "utils.py"
        utils.write_text("def util(): return 42\ndef new_util(): return 100\n", encoding="utf-8")

        status, details, meta = verifier.verify(
            task="Update utils.py with new_util function",
            files_created_or_edited=["utils.py"],
        )

        assert status == VERIFIED
        assert "utils.py" in meta["verified_files"]

    def test_p1_read_only_task_completes_without_mutation(self, workspace: Path):
        """Informational / read-only query requires no file creation."""
        boundary = WorkspaceBoundary(workspace)
        tools = AgentToolRegistry(boundary)
        verifier = VerificationEngine(boundary, tools)

        status, details, meta = verifier.verify(
            task="What is the architecture of this project?",
            files_created_or_edited=[],
        )

        assert status == VERIFIED
        assert "no file modifications required" in details


# ==============================================================================
# P1: CLI INTEGRITY & EXIT CODE TEST
# ==============================================================================

class TestCliExitCodeOnFailedTask:
    """Verifies that the CLI returns non-zero exit code when a task fails verification."""

    def test_cli_handles_status_failed_with_exit_1(self):
        """Ensure that done event with status='failed' exits with code 1."""
        from click.testing import CliRunner
        from cli.aetherion_cli.main import cli

        class MockEventStream:
            def __iter__(self):
                yield {"type": "progress", "state": "executing"}
                yield {
                    "type": "done",
                    "status": "failed",
                    "error": "Verification failed: No files were created",
                    "verification": "NOT VERIFIED",
                }

        class MockClient:
            def is_authenticated(self):
                return True
            def stream(self, *args, **kwargs):
                return MockEventStream()

        with patch("cli.aetherion_cli.main._get_client", return_value=MockClient()), \
             patch("cli.aetherion_cli.main._require_auth", return_value=None):
            runner = CliRunner()
            result = runner.invoke(cli, ["run", "Create math_utils.py"])
            assert result.exit_code == 1
            assert "Task failed" in result.output


# ==============================================================================
# P2: CROSS-TENANT MUTATION SECURITY & HTTP 403 TESTS
# ==============================================================================

class TestCrossTenantMutationSecurity:
    """Verifies tenant isolation on task mutations, deterministic HTTP 403, and safety."""

    def test_p2_authorization_error_is_permission_error(self):
        """Invariants: AuthorizationError is a standard PermissionError subclass."""
        assert issubclass(durable_state.AuthorizationError, PermissionError)
        err = durable_state.AuthorizationError("Forbidden")
        assert isinstance(err, PermissionError)
        assert isinstance(err, durable_state.ExecutionStateError)

    def test_p2_owner_can_stop_own_task(self):
        """Owner of a task can stop their own task with HTTP 200."""
        # Create task under tenant_101
        rec = durable_state.create(
            kind="agent_task",
            tenant_id="101",
            payload={"prompt": "Tenant 101 Task"},
        )
        tid = rec["execution_id"]
        durable_state.transition(tid, "101", durable_state.STATUS_RUNNING)

        with app.test_client() as client:
            token_101 = _make_access_token(user_id=101, email="t101@test.com", role="user")
            res = client.post(
                f"/api/task/{tid}/stop",
                headers={"Authorization": f"Bearer {token_101}"},
            )
            assert res.status_code == 200
            data = res.get_json()
            assert data["ok"] is True
            assert data["status"] == "stopped"

        # Verify durable state is now STOPPED
        updated = durable_state.get(tid, "101")
        assert updated["status"] == durable_state.STATUS_STOPPED

    def test_p2_foreign_tenant_cannot_stop_task_returns_403(self):
        """Cross-tenant mutation returns deterministic HTTP 403 and leaves task state untouched."""
        rec = durable_state.create(
            kind="agent_task",
            tenant_id="101",
            payload={"prompt": "Confidential Tenant 101 Task"},
        )
        tid = rec["execution_id"]
        durable_state.transition(tid, "101", durable_state.STATUS_RUNNING)

        with app.test_client() as client:
            # Foreign tenant 202 tries to stop tenant 101's task
            token_202 = _make_access_token(user_id=202, email="t202@test.com", role="user")
            res = client.post(
                f"/api/task/{tid}/stop",
                headers={"Authorization": f"Bearer {token_202}"},
            )

            # Must return HTTP 403 Forbidden
            assert res.status_code == 403
            data = res.get_json()
            assert data["ok"] is False
            assert "Forbidden" in data["error"] or "does not belong" in data["error"]
            # No traceback leakage
            assert "Traceback" not in res.get_data(as_text=True)

        # Task state MUST remain unchanged (still RUNNING)
        owner_view = durable_state.get(tid, "101")
        assert owner_view["status"] == durable_state.STATUS_RUNNING

    def test_p2_repeated_unauthorized_attempts_remain_safe(self):
        """Repeated unauthorized stop attempts return 403 and never alter state."""
        rec = durable_state.create(
            kind="agent_task",
            tenant_id="303",
            payload={"prompt": "Mission critical task"},
        )
        tid = rec["execution_id"]
        durable_state.transition(tid, "303", durable_state.STATUS_RUNNING)

        with app.test_client() as client:
            token_attacker = _make_access_token(user_id=999, email="attacker@test.com", role="user")
            for _ in range(5):
                res = client.post(
                    f"/api/task/{tid}/stop",
                    headers={"Authorization": f"Bearer {token_attacker}"},
                )
                assert res.status_code == 403

        # Task remains unmodified
        assert durable_state.get(tid, "303")["status"] == durable_state.STATUS_RUNNING

    def test_p2_stop_nonexistent_task_returns_404(self):
        """Stopping an unknown execution returns 404 (resource non-disclosure)."""
        with app.test_client() as client:
            token = _make_access_token(user_id=101, email="t101@test.com", role="user")
            res = client.post(
                "/api/task/task_non_existent_999999/stop",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert res.status_code == 404
            data = res.get_json()
            assert data["ok"] is False
            assert "not found" in data["error"].lower()


# ==============================================================================
# P1: RUNTIME INTEGRATION & REPAIR LOOP ADVERSARIAL TESTS
# ==============================================================================

class TestRuntimeAdversarialVerification:
    """Verifies that RealAgentRuntime enforces the fail-closed verification contract
    end-to-end through event streaming and durable checkpoints."""

    def test_p1_runtime_no_op_agent_with_preexisting_files_fails(self, tmp_path: Path):
        """End-to-end: If agent creates no files on a mutation prompt, even with
        pre-existing files, the runtime marks the task as failed."""
        ws = tmp_path / "runtime_ws"
        ws.mkdir()
        # Pre-existing repository file
        (ws / "pre_existing_repo_file.py").write_text("def existing(): pass\n", encoding="utf-8")

        class NoOpProvider(DeterministicTestProvider):
            def chat(self, messages, tools):
                return {"thought": "I took no action and created no files.", "tool": None, "arguments": {}}

        runtime = RealAgentRuntime(
            workspace_dir=ws,
            session_id="test_remediation_s01",
            provider=NoOpProvider(),
            max_iterations=2,
        )

        events = list(runtime.run_stream("Create math_utils.py with add function"))
        done_events = [e for e in events if e.get("type") == "done"]
        assert len(done_events) == 1
        done = done_events[0]

        # Must report terminal failure
        assert done["status"] == "failed"
        assert done["verification"] == NOT_VERIFIED
        assert "pre_existing_repo_file.py" not in done.get("files", [])

    def test_p1_runtime_repair_loop_retry_after_failure(self, tmp_path: Path):
        """Case J: Verifier fails on injected bug, repair loop triggers,
        and agent repairs it in next attempt -> SUCCEEDED."""
        ws = tmp_path / "repair_ws"
        ws.mkdir()

        # DeterministicTestProvider with inject_failure_once=True simulates
        # an injected bug in fibonacci.py that fails tests on attempt 1,
        # triggers repair, and succeeds on attempt 2.
        provider = DeterministicTestProvider(inject_failure_once=True)

        runtime = RealAgentRuntime(
            workspace_dir=ws,
            session_id="test_remediation_repair",
            provider=provider,
            max_iterations=10,
        )

        events = list(runtime.run_stream(
            "Create a Python file called fibonacci.py with a function fibonacci(n) and tests for n=0, 1, and 10."
        ))

        # Check that repair state was entered
        states = [e.get("status") for e in events if e.get("type") == "state_change"]
        assert STATE_REPAIRING in states

        # Verify file on disk is corrected
        fib_content = (ws / "fibonacci.py").read_text(encoding="utf-8")
        assert "return 1" in fib_content
        assert "999" not in fib_content

        done_events = [e for e in events if e.get("type") == "done"]
        assert len(done_events) == 1
        done = done_events[0]

        assert done["status"] == "done"
        assert done["verification"] == VERIFIED

    def test_p1_worktree_isolation_prevents_cross_workspace_leak(self, tmp_path: Path):
        """Changes in worktree A must never satisfy verification in worktree B."""
        ws_a = tmp_path / "ws_a"
        ws_b = tmp_path / "ws_b"
        ws_a.mkdir()
        ws_b.mkdir()

        (ws_a / "generated_in_a.py").write_text("A = 1\n", encoding="utf-8")

        boundary_b = WorkspaceBoundary(ws_b)
        tools_b = AgentToolRegistry(boundary_b)
        verifier_b = VerificationEngine(boundary_b, tools_b)

        status, details, meta = verifier_b.verify(
            task="Create generated_in_a.py",
            files_created_or_edited=[],
        )
        assert status == NOT_VERIFIED
        assert "generated_in_a.py" not in meta["verified_files"]


# ==============================================================================
# P2: SENSITIVE STATE NON-DISCLOSURE TESTS
# ==============================================================================

class TestTenantSensitiveStateNonDisclosure:
    """Verifies that cross-tenant operations never disclose task contents or state."""

    def test_p2_unauthorized_tenant_cannot_read_or_mutate_sensitive_state(self):
        """Tenant B cannot read (404) or stop (403) Tenant A's sensitive task."""
        sensitive_payload = {
            "prompt": "Top Secret Architecture Refactor",
            "api_key": "sk-classified-token-9999",
            "internal_notes": "Do not disclose",
        }
        rec = durable_state.create(
            kind="agent_task",
            tenant_id="alpha_corp",
            payload=sensitive_payload,
        )
        tid = rec["execution_id"]
        durable_state.transition(tid, "alpha_corp", durable_state.STATUS_RUNNING)

        with app.test_client() as client:
            token_beta = _make_access_token(user_id=888, email="beta@corp.com", role="user")

            # 1. Read attempt -> 404 (non-disclosure)
            get_res = client.get(
                f"/api/task/{tid}",
                headers={"Authorization": f"Bearer {token_beta}"},
            )
            assert get_res.status_code == 404
            assert "classified" not in get_res.get_data(as_text=True)

            # 2. Stop attempt -> 403 (forbidden)
            stop_res = client.post(
                f"/api/task/{tid}/stop",
                headers={"Authorization": f"Bearer {token_beta}"},
            )
            assert stop_res.status_code == 403
            assert "classified" not in stop_res.get_data(as_text=True)

            # 3. Owner read -> 200
            token_alpha = _make_access_token(user_id=1, email="alpha@corp.com", role="user")
            # In route tests, user_id=1 maps to tenant "1" via _get_tenant()
            # Let's test with tenant_id matching user_id
            rec_owner = durable_state.create(
                kind="agent_task",
                tenant_id="777",
                payload={"sensitive": "data_xyz"},
            )
            tid_owner = rec_owner["execution_id"]
            token_owner = _make_access_token(user_id=777, email="owner777@corp.com", role="user")

            owner_res = client.get(
                f"/api/task/{tid_owner}",
                headers={"Authorization": f"Bearer {token_owner}"},
            )
            assert owner_res.status_code == 200
            assert owner_res.get_json()["ok"] is True
