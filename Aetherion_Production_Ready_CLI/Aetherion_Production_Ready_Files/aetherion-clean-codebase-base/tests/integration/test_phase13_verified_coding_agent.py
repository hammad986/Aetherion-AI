"""
tests/integration/test_phase13_verified_coding_agent.py
======================================================
Authoritative Phase 13 Integration Test Suite: Verified Coding-Agent Kernel.

Covers the complete end-to-end verified autonomous coding agent workflow:
  TASK CREATE -> REPOSITORY/WORKSPACE ATTACH -> ISOLATED WORKTREE -> TASK CONTEXT
  -> PLAN -> TOOL EXECUTION -> HITL WHEN REQUIRED -> VERIFY -> REPAIR IF FAILED
  -> FINAL VERIFICATION -> DIFF + EVIDENCE + REPLAY -> VERIFIED SUCCESS / FAILURE.

Test Classes:
  1. TestTaskExecutionContext
  2. TestWorktreeAndAttachmentContract
  3. TestVerifiedExecutionLifecycle
  4. TestBoundedRepairLoop
  5. TestHITLBlockingGateInCodingTask
  6. TestReplayEngineIntegration
  7. TestDurableStatePersistence
"""
from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Ensure repository root and backend are on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
for p in (str(BASE_DIR), str(BACKEND_DIR), str(CLI_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from backend.execution.agent_runtime import (
    ContextImmutableError,
    DeterministicTestProvider,
    RealAgentRuntime,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_EXECUTING,
    STATE_FAILED,
    STATE_PLANNING,
    STATE_QUEUED,
    STATE_REPAIRING,
    STATE_SUCCEEDED,
    STATE_VERIFYING,
    STATE_WAITING_FOR_APPROVAL,
    TaskExecutionContext,
    attach_execution_worktree,
    cleanup_execution_worktree,
    get_execution_diff,
)
from backend.execution.durable_state import (
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    get as get_durable_state,
)
from backend.execution.hitl import global_hitl_tracker, resolve_gate
from backend.execution.providers.base import BaseModelProvider, ModelResponse, ToolCall
from backend.execution.replay import ReplayEngine
from backend.execution.runner_contract import RunnerDeniedError
from backend.execution.verification.engine import (
    NOT_VERIFIED,
    VERIFICATION_FAILED,
    VERIFIED,
)


class ScriptedProvider(BaseModelProvider):
    """Deterministic scripted provider yielding specific responses per step."""

    def __init__(self, responses: List[ModelResponse]):
        self._responses = list(responses)
        self._index = 0

    def name(self) -> str:
        return "scripted_provider"

    def is_available(self) -> bool:
        return True

    def chat(self, messages: List[Any], tools: List[Any]) -> ModelResponse:
        if self._index < len(self._responses):
            resp = self._responses[self._index]
            self._index += 1
            return resp
        return ModelResponse(
            text="Default completion",
            tool_calls=[ToolCall(id="fin", name="finish", arguments={"summary": "Completed"})],
        )


class ReplayStoreAdapter:
    """Store adapter for ReplayEngine projection over runtime replay_events."""

    def __init__(self, events_by_id: Optional[Dict[str, List[Dict[str, Any]]]] = None):
        self._events: Dict[str, List[Dict[str, Any]]] = events_by_id or {}

    def get_events(self, execution_id: str) -> List[Dict[str, Any]]:
        return list(self._events.get(execution_id, []))


# ==============================================================================
# 1. Task Execution Context Tests
# ==============================================================================

class TestTaskExecutionContext(unittest.TestCase):
    """Unit and boundary tests for authoritative TaskExecutionContext."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeth_p13_ctx_")
        self.ws = Path(self.tmp) / "workspace"
        self.ws.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_context_creation_and_defaults(self):
        """Context initializes with sensible defaults and valid attributes."""
        ctx = TaskExecutionContext(
            task_id="task_p13_001",
            objective="Build calculator module",
            workspace_dir=self.ws,
            tenant_id="tenant_p13",
        )
        self.assertEqual(ctx.task_id, "task_p13_001")
        self.assertEqual(ctx.objective, "Build calculator module")
        self.assertEqual(ctx.workspace_dir, self.ws.resolve())
        self.assertEqual(ctx.tenant_id, "tenant_p13")
        self.assertEqual(ctx.session_id, "sess_task_p13_001")
        self.assertEqual(ctx.base_ref, "HEAD")
        self.assertFalse(ctx.is_frozen())
        self.assertEqual(ctx.execution_constraints.get("max_iterations"), 15)
        self.assertEqual(ctx.execution_budget.get("max_repair_attempts"), 2)

    def test_context_immutability_enforcement(self):
        """Frozen context rejects mutations of critical fields with ContextImmutableError."""
        ctx = TaskExecutionContext(
            task_id="task_p13_002",
            objective="Run tests",
            workspace_dir=self.ws,
        )
        ctx.freeze()
        self.assertTrue(ctx.is_frozen())

        # Mutating frozen attributes must raise ContextImmutableError
        with self.assertRaises(ContextImmutableError):
            ctx.task_id = "mutated_task_id"

        with self.assertRaises(ContextImmutableError):
            ctx.objective = "mutated_objective"

        with self.assertRaises(ContextImmutableError):
            ctx.workspace_dir = Path("/tmp")

        with self.assertRaises(ContextImmutableError):
            ctx.tenant_id = "attacker_tenant"

        with self.assertRaises(ContextImmutableError):
            ctx.execution_constraints = {"max_iterations": 999}

        with self.assertRaises(ContextImmutableError):
            ctx.execution_budget = {"max_repair_attempts": 999}

    def test_context_serialization_roundtrip(self):
        """Context serializes to dict and reconstructs identically with from_dict."""
        ctx = TaskExecutionContext(
            task_id="task_p13_003",
            objective="Deploy artifact",
            workspace_dir=self.ws,
            tenant_id="tenant_alpha",
            execution_constraints={"timeout_sec": 45.0},
            verification_criteria={"expected_files": ["app.py"]},
            execution_budget={"max_repair_attempts": 3},
        )
        ctx.freeze()

        d = ctx.to_dict()
        self.assertEqual(d["task_id"], "task_p13_003")
        self.assertEqual(d["objective"], "Deploy artifact")
        self.assertTrue(d["is_frozen"])
        self.assertEqual(d["verification_criteria"], {"expected_files": ["app.py"]})

        reconstructed = TaskExecutionContext.from_dict(d)
        self.assertEqual(reconstructed.task_id, ctx.task_id)
        self.assertEqual(reconstructed.objective, ctx.objective)
        self.assertEqual(reconstructed.workspace_dir, ctx.workspace_dir)
        self.assertEqual(reconstructed.tenant_id, ctx.tenant_id)
        self.assertTrue(reconstructed.is_frozen())
        self.assertEqual(reconstructed.verification_criteria, ctx.verification_criteria)

    def test_context_validation_rejections(self):
        """Context rejects empty strings for required identifiers."""
        with self.assertRaises(ValueError):
            TaskExecutionContext(task_id="", objective="valid", workspace_dir=self.ws)

        with self.assertRaises(ValueError):
            TaskExecutionContext(task_id="   ", objective="valid", workspace_dir=self.ws)

        with self.assertRaises(ValueError):
            TaskExecutionContext(task_id="valid", objective="valid", workspace_dir=self.ws, tenant_id="")

    def test_context_mount_safety_guard(self):
        """Unsafe paths (e.g. system roots) fail closed via check_mount_safety."""
        root_path = Path("C:/") if os.name == "nt" else Path("/")
        with self.assertRaises(RunnerDeniedError):
            TaskExecutionContext(task_id="test_safe", objective="test", workspace_dir=root_path)


# ==============================================================================
# 2. Worktree & Repository Attachment Contract Tests
# ==============================================================================

class TestWorktreeAndAttachmentContract(unittest.TestCase):
    """Validates isolated worktree attachment, mount safety, diff extraction, and cleanup."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeth_p13_wt_")
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        # Initialize a real git repo
        subprocess.run(["git", "init"], cwd=str(self.repo), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "aetherion@test.local"], cwd=str(self.repo), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Aetherion Tester"], cwd=str(self.repo), check=True, capture_output=True)
        (self.repo / "README.md").write_text("# Aetherion Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(self.repo), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(self.repo), check=True, capture_output=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_worktree_attachment_in_git_repo(self):
        """Worktree attachment creates an isolated checkout and sets branch and workspace."""
        ctx = TaskExecutionContext(
            task_id="task_wt_01",
            objective="Develop feature",
            workspace_dir=self.repo,
            repo_root=str(self.repo),
        )
        self.assertEqual(ctx.workspace_dir, self.repo.resolve())

        attach_execution_worktree(ctx, repo_root=self.repo)

        self.assertIsNotNone(ctx.worktree_id)
        self.assertIsNotNone(ctx.branch)
        self.assertTrue(ctx.branch.startswith("aeth/task_wt_01-"))
        self.assertNotEqual(ctx.workspace_dir, self.repo.resolve())
        self.assertTrue(ctx.workspace_dir.exists())

        # Cleanup
        res = cleanup_execution_worktree(ctx)
        self.assertTrue(res["ok"])

    def test_worktree_cannot_attach_when_frozen(self):
        """Cannot attach a worktree after execution context has been frozen."""
        ctx = TaskExecutionContext(
            task_id="task_wt_frozen",
            objective="Develop feature",
            workspace_dir=self.repo,
            repo_root=str(self.repo),
        )
        ctx.freeze()

        with self.assertRaises(ContextImmutableError):
            attach_execution_worktree(ctx, repo_root=self.repo)

    def test_worktree_cleanup_removes_checkout_and_branch(self):
        """Cleanup removes worktree checkout directory and deletes branch."""
        ctx = TaskExecutionContext(
            task_id="task_wt_clean",
            objective="Temporary task",
            workspace_dir=self.repo,
            repo_root=str(self.repo),
        )
        attach_execution_worktree(ctx, repo_root=self.repo)
        wt_path = ctx.workspace_dir
        branch = ctx.branch

        res = cleanup_execution_worktree(ctx)
        self.assertTrue(res["ok"])

        # Check worktree checkout directory is removed
        self.assertFalse(wt_path.exists())

        # Check branch no longer exists in repository
        branches = subprocess.run(["git", "branch"], cwd=str(self.repo), capture_output=True, text=True).stdout
        self.assertNotIn(branch, branches)

    def test_worktree_diff_extraction(self):
        """Diff extraction against base ref returns unified diff for modified and added files."""
        ctx = TaskExecutionContext(
            task_id="task_wt_diff",
            objective="Feature diff",
            workspace_dir=self.repo,
            repo_root=str(self.repo),
        )
        attach_execution_worktree(ctx, repo_root=self.repo)

        # Create new file in worktree
        new_file = ctx.workspace_dir / "calculator.py"
        new_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

        diff = get_execution_diff(ctx, ["calculator.py"])
        self.assertIn("calculator.py", diff)
        self.assertIn("+def add(a, b):", diff)

        cleanup_execution_worktree(ctx)

    def test_plain_workspace_diff_fallback(self):
        """Non-worktree workspaces generate clean unified diff via fallback."""
        plain_ws = Path(self.tmp) / "plain_ws"
        plain_ws.mkdir()
        ctx = TaskExecutionContext(
            task_id="task_plain_diff",
            objective="Plain workspace",
            workspace_dir=plain_ws,
        )
        test_file = plain_ws / "script.py"
        test_file.write_text("x = 42\n", encoding="utf-8")

        diff = get_execution_diff(ctx, ["script.py"])
        self.assertIn("script.py", diff)
        self.assertIn("+x = 42", diff)


# ==============================================================================
# 3. Verified Execution Lifecycle Tests
# ==============================================================================

class TestVerifiedExecutionLifecycle(unittest.TestCase):
    """Validates the state progression: completed -> verifying -> succeeded."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeth_p13_life_")
        self.ws = Path(self.tmp) / "workspace"
        self.ws.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_verified_lifecycle_full_progression(self):
        """Full autonomous run verifies and reaches STATE_SUCCEEDED."""
        provider = DeterministicTestProvider()
        ctx = TaskExecutionContext(
            task_id="task_life_01",
            objective="Create hello.py that prints Hello Aetherion.",
            workspace_dir=self.ws,
            verification_criteria={"expected_files": ["hello.py"]},
        )
        runtime = RealAgentRuntime(context=ctx, provider=provider)

        events = list(runtime.run_stream(ctx.objective))

        event_types = [e.get("type") for e in events]
        self.assertIn("queued", event_types)
        self.assertIn("state_change", event_types)
        self.assertIn("tool_call", event_types)

        done_event = [e for e in events if e.get("type") == "done"][0]
        self.assertEqual(done_event["status"], "done")
        self.assertEqual(done_event["execution_status"], "succeeded")
        self.assertEqual(done_event["verification"], VERIFIED)
        self.assertIn("hello.py", done_event.get("files", []))
        self.assertTrue((self.ws / "hello.py").exists())

    def test_verifier_truth_rejects_unverified_claims(self):
        """Model claiming 'VERIFIED' without actually creating files is rejected."""
        provider = ScriptedProvider([
            ModelResponse(
                text="I have completed everything. VERIFIED. All tests pass.",
                tool_calls=[ToolCall(id="fin", name="finish", arguments={"summary": "I did it all without files."})],
            ),
        ])
        ctx = TaskExecutionContext(
            task_id="task_life_truth",
            objective="Create app.py",
            workspace_dir=self.ws,
            verification_criteria={"expected_files": ["app.py"]},
            execution_budget={"max_repair_attempts": 0},
        )
        runtime = RealAgentRuntime(context=ctx, provider=provider)

        events = list(runtime.run_stream(ctx.objective))

        done_event = [e for e in events if e.get("type") == "done"][0]
        # Must fail because app.py does not exist on disk
        self.assertEqual(done_event["execution_status"], "failed")
        self.assertIn(done_event["verification"], (NOT_VERIFIED, VERIFICATION_FAILED))

    def test_criteria_evaluation_expected_files_enforced(self):
        """Verification engine enforces criteria expected_files strictly."""
        # Provider creates a different file than expected
        provider = ScriptedProvider([
            ModelResponse(
                text="Writing wrong file",
                tool_calls=[ToolCall(id="w1", name="write_file", arguments={"path": "wrong.py", "content": "pass"})],
            ),
            ModelResponse(
                text="Finishing",
                tool_calls=[ToolCall(id="fin", name="finish", arguments={"summary": "Done"})],
            ),
        ])
        ctx = TaskExecutionContext(
            task_id="task_life_crit",
            objective="Must create correct file",
            workspace_dir=self.ws,
            verification_criteria={"expected_files": ["required.py"]},
            execution_budget={"max_repair_attempts": 0},
        )
        runtime = RealAgentRuntime(context=ctx, provider=provider)

        events = list(runtime.run_stream(ctx.objective))
        done_event = [e for e in events if e.get("type") == "done"][0]
        self.assertEqual(done_event["execution_status"], "failed")
        self.assertIn(done_event["verification"], (NOT_VERIFIED, VERIFICATION_FAILED))


# ==============================================================================
# 4. Bounded Repair Loop Tests
# ==============================================================================

class TestBoundedRepairLoop(unittest.TestCase):
    """Validates repair attempt bounding, feedback injection, and recovery."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeth_p13_repair_")
        self.ws = Path(self.tmp) / "workspace"
        self.ws.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_repair_loop_recovers_from_initial_failure(self):
        """First attempt fails criteria; repair attempt receives feedback and fixes it."""
        provider = ScriptedProvider([
            # Attempt 1: Creates broken code
            ModelResponse(
                text="Attempt 1: write wrong file",
                tool_calls=[ToolCall(id="call_1", name="write_file", arguments={"path": "wrong.py", "content": "# wrong"})],
            ),
            ModelResponse(
                text="Attempt 1: finish",
                tool_calls=[ToolCall(id="call_2", name="finish", arguments={"summary": "Attempt 1 done"})],
            ),
            # Attempt 2 (Repair): Fixes the error by creating expected file
            ModelResponse(
                text="Attempt 2: repair by creating expected file",
                tool_calls=[ToolCall(id="call_3", name="write_file", arguments={"path": "math_utils.py", "content": "def square(x):\n    return x * x\n"})],
            ),
            ModelResponse(
                text="Attempt 2: finish",
                tool_calls=[ToolCall(id="call_4", name="finish", arguments={"summary": "Repaired successfully"})],
            ),
        ])

        ctx = TaskExecutionContext(
            task_id="task_repair_recover",
            objective="Implement math_utils.py",
            workspace_dir=self.ws,
            verification_criteria={"expected_files": ["math_utils.py"]},
            execution_budget={"max_repair_attempts": 2},
        )
        runtime = RealAgentRuntime(context=ctx, provider=provider)

        events = list(runtime.run_stream(ctx.objective))

        # Check repair state transition occurred
        state_changes = [e.get("status") for e in events if e.get("type") == "state_change"]
        self.assertIn(STATE_REPAIRING, state_changes)

        done_event = [e for e in events if e.get("type") == "done"][0]
        self.assertEqual(done_event["execution_status"], "succeeded")
        self.assertEqual(done_event["verification"], VERIFIED)
        self.assertTrue((self.ws / "math_utils.py").exists())
        self.assertGreaterEqual(len(runtime.repair_history), 1)

    def test_repair_loop_exhaustion_terminates_in_failure(self):
        """When repair attempts exceed budget, runtime terminates with STATE_FAILED."""
        # Provider continually finishes without creating the expected file
        provider = ScriptedProvider([
            ModelResponse(
                text="Bad attempt 1",
                tool_calls=[ToolCall(id="f1", name="finish", arguments={"summary": "Attempt 1"})],
            ),
            ModelResponse(
                text="Bad attempt 2",
                tool_calls=[ToolCall(id="f2", name="finish", arguments={"summary": "Attempt 2"})],
            ),
            ModelResponse(
                text="Bad attempt 3",
                tool_calls=[ToolCall(id="f3", name="finish", arguments={"summary": "Attempt 3"})],
            ),
        ])

        ctx = TaskExecutionContext(
            task_id="task_repair_exhaust",
            objective="Must create needed.py",
            workspace_dir=self.ws,
            verification_criteria={"expected_files": ["needed.py"]},
            execution_budget={"max_repair_attempts": 1},  # Exactly 1 repair attempt
        )
        runtime = RealAgentRuntime(context=ctx, provider=provider)

        events = list(runtime.run_stream(ctx.objective))

        done_event = [e for e in events if e.get("type") == "done"][0]
        self.assertEqual(done_event["execution_status"], "failed")
        self.assertIn(done_event["verification"], (NOT_VERIFIED, VERIFICATION_FAILED))
        self.assertEqual(done_event.get("failure_class"), "verifier_failure")


# ==============================================================================
# 5. HITL Blocking Gate In Coding Task Tests
# ==============================================================================

class TestHITLBlockingGateInCodingTask(unittest.TestCase):
    """Validates real blocking HITL gate before dangerous commands in coding tasks."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeth_p13_hitl_")
        self.ws = Path(self.tmp) / "workspace"
        self.ws.mkdir()
        self.db_path = str(Path(self.tmp) / "hitl.db")
        global_hitl_tracker.db_path = self.db_path
        global_hitl_tracker._init_db()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hitl_approval_unblocks_and_resumes(self):
        """Dangerous command genuinely blocks awaiting approval and resumes upon approval."""
        marker = self.ws / "SIDE_EFFECT_HITL_APPROVED.txt"
        cmd = f'python -c "import pathlib; pathlib.Path(r\'{marker}\').write_text(\'ok\')" && rm -rf dummy'

        provider = ScriptedProvider([
            ModelResponse(
                text="Executing dangerous cleanup",
                tool_calls=[ToolCall(id="c1", name="run_command", arguments={"command": cmd})],
            ),
            ModelResponse(
                text="Finishing after dangerous command",
                tool_calls=[ToolCall(id="f1", name="finish", arguments={"summary": "Done"})],
            ),
        ])

        task_id = "task_hitl_approve_coding"
        ctx = TaskExecutionContext(
            task_id=task_id,
            objective="Clean environment",
            workspace_dir=self.ws,
        )
        runtime = RealAgentRuntime(context=ctx, provider=provider)

        events_seen = []
        is_blocked = threading.Event()

        def _runner():
            for ev in runtime.run_stream(ctx.objective):
                events_seen.append(ev)
                if ev.get("type") == "approval":
                    is_blocked.set()

        th = threading.Thread(target=_runner, daemon=True)
        th.start()

        # Must block on HITL approval
        self.assertTrue(is_blocked.wait(timeout=5.0), "Runtime did not block on approval event")
        time.sleep(0.3)
        self.assertTrue(th.is_alive())
        self.assertFalse(marker.exists(), "Command executed before approval!")

        # Approve the gate
        resolve_gate(task_id, approved=True, comments="Operator approved")
        th.join(timeout=5.0)

        # Command executed and task succeeded
        self.assertTrue(marker.exists(), "Approved command did not execute!")
        self.assertIn("approval", [e.get("type") for e in events_seen])
        self.assertTrue(len(runtime.hitl_decisions) >= 1)

    def test_hitl_rejection_prevents_command_execution(self):
        """Rejecting dangerous command blocks execution safely."""
        marker = self.ws / "SIDE_EFFECT_HITL_REJECTED.txt"
        cmd = f'python -c "import pathlib; pathlib.Path(r\'{marker}\').write_text(\'pwned\')" && rm -rf dummy'

        provider = ScriptedProvider([
            ModelResponse(
                text="Attempting dangerous command",
                tool_calls=[ToolCall(id="c1", name="run_command", arguments={"command": cmd})],
            ),
            ModelResponse(
                text="Clean finish after rejection",
                tool_calls=[ToolCall(id="f1", name="finish", arguments={"summary": "Safe abort"})],
            ),
        ])

        task_id = "task_hitl_reject_coding"
        ctx = TaskExecutionContext(
            task_id=task_id,
            objective="Attempt dangerous operation",
            workspace_dir=self.ws,
        )
        runtime = RealAgentRuntime(context=ctx, provider=provider)

        events_seen = []
        is_blocked = threading.Event()

        def _runner():
            for ev in runtime.run_stream(ctx.objective):
                events_seen.append(ev)
                if ev.get("type") == "approval":
                    is_blocked.set()

        th = threading.Thread(target=_runner, daemon=True)
        th.start()

        self.assertTrue(is_blocked.wait(timeout=5.0))
        time.sleep(0.3)
        self.assertFalse(marker.exists())

        # Reject the gate
        resolve_gate(task_id, approved=False, comments="Operator denied")
        th.join(timeout=5.0)

        # Side effect file must NEVER exist
        self.assertFalse(marker.exists(), "Rejected command executed anyway!")


# ==============================================================================
# 6. Replay Engine Integration Tests
# ==============================================================================

class TestReplayEngineIntegration(unittest.TestCase):
    """Validates ReplayEngine folding over real autonomous agent replay events."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeth_p13_rep_")
        self.ws = Path(self.tmp) / "workspace"
        self.ws.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_replay_engine_folds_task_events(self):
        """ReplayEngine timeline folds runtime.replay_events into verified terminal state."""
        provider = DeterministicTestProvider()
        task_id = "task_replay_fold_01"
        ctx = TaskExecutionContext(
            task_id=task_id,
            objective="Create hello.py that prints Hello Aetherion.",
            workspace_dir=self.ws,
        )
        runtime = RealAgentRuntime(context=ctx, provider=provider)
        list(runtime.run_stream(ctx.objective))

        # Feed events to ReplayEngine
        adapter = ReplayStoreAdapter({task_id: runtime.replay_events})
        engine = ReplayEngine(store=adapter)
        timeline = engine.timeline(task_id)

        self.assertEqual(timeline.terminal_status, "succeeded")
        self.assertGreater(len(timeline.frames), 0)

        kinds = [f.kind for f in timeline.frames]
        self.assertIn("lifecycle", kinds)
        self.assertIn("tool", kinds)
        self.assertIn("verifier", kinds)

        # Check summary properties
        self.assertGreaterEqual(timeline.summary.get("tools_called", 0), 1)

    def test_replay_diff_and_evidence_in_terminal_output(self):
        """Unified diff and repair metadata are exported cleanly."""
        provider = DeterministicTestProvider()
        task_id = "task_replay_diff_02"
        ctx = TaskExecutionContext(
            task_id=task_id,
            objective="Create hello.py that prints Hello Aetherion.",
            workspace_dir=self.ws,
        )
        runtime = RealAgentRuntime(context=ctx, provider=provider)
        events = list(runtime.run_stream(ctx.objective))

        done_event = [e for e in events if e.get("type") == "done"][0]
        self.assertIn("diff", done_event)
        self.assertIn("hello.py", done_event["diff"])


# ==============================================================================
# 7. Durable State Persistence Tests
# ==============================================================================

class TestDurableStatePersistence(unittest.TestCase):
    """Validates durable state persistence across task completion and failure."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeth_p13_dur_")
        self.ws = Path(self.tmp) / "workspace"
        self.ws.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_durable_state_records_success_and_diff(self):
        """Durable state records STATUS_SUCCEEDED and diff on task success."""
        provider = DeterministicTestProvider()
        task_id = "task_dur_succ_01"
        tenant_id = "tenant_dur"
        ctx = TaskExecutionContext(
            task_id=task_id,
            objective="Create hello.py that prints Hello Aetherion.",
            workspace_dir=self.ws,
            tenant_id=tenant_id,
        )
        runtime = RealAgentRuntime(context=ctx, provider=provider)
        list(runtime.run_stream(ctx.objective))

        # Check durable state
        state = get_durable_state(task_id, tenant_id=tenant_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.get("status"), STATUS_SUCCEEDED)
        self.assertIn("hello.py", state.get("result_json", "") or "")

    def test_durable_state_records_failure_on_exhaustion(self):
        """Durable state records STATUS_FAILED when task verification fails."""
        provider = ScriptedProvider([
            ModelResponse(
                text="Failing finish",
                tool_calls=[ToolCall(id="fin", name="finish", arguments={"summary": "Failed"})],
            ),
        ])
        task_id = "task_dur_fail_02"
        tenant_id = "tenant_dur"
        ctx = TaskExecutionContext(
            task_id=task_id,
            objective="Create non_existent.py",
            workspace_dir=self.ws,
            tenant_id=tenant_id,
            verification_criteria={"expected_files": ["non_existent.py"]},
            execution_budget={"max_repair_attempts": 0},
        )
        runtime = RealAgentRuntime(context=ctx, provider=provider)
        list(runtime.run_stream(ctx.objective))

        state = get_durable_state(task_id, tenant_id=tenant_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.get("status"), STATUS_FAILED)


if __name__ == "__main__":
    unittest.main()
