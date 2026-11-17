"""
tests/integration/test_phase14_agent_orchestration.py
======================================================
Authoritative Phase 14 Integration Test Suite:
Agent Orchestration & Productization Kernel.

Covers the authoritative end-to-end product execution pipeline:
  USER TASK
     ↓
  PERSISTED TASK
     ↓
  AUTHORIZED REPOSITORY / WORKSPACE
     ↓
  EXECUTION CONTEXT
     ↓
  ISOLATED WORKTREE
     ↓
  PLAN
     ↓
  EXECUTE
     ↓
  HITL WHEN REQUIRED
     ↓
  VERIFY
     ↓
  REPAIR IF REQUIRED
     ↓
  FINAL VERIFY
     ↓
  DIFF + EVIDENCE + REPLAY
     ↓
  USER REVIEW
     ↓
  PROMOTE / REJECT

Test Classes:
  1. TestAuthoritativeTaskCreationAndPersistence
  2. TestWorktreePolicyAndFailClosedContract
  3. TestAuthoritativeExecutionLifecycleAndConvergence
  4. TestHITLIntegrationInCodingPipeline
  5. TestVerificationTruthAndBoundedRepair
  6. TestUnifiedDiffAndEvidenceSafety
  7. TestReviewAndPromotionContract
"""
from __future__ import annotations

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
from flask import Flask

# Path bootstrap: ensure backend and cli are accessible
BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
for p in (str(BASE_DIR), str(BACKEND_DIR), str(CLI_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from backend.auth_system import _make_access_token
from backend.execution import durable_state
from backend.execution.agent_runtime import (
    ContextImmutableError,
    DeterministicTestProvider,
    RealAgentRuntime,
    STATE_ACCEPTED,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_EXECUTING,
    STATE_FAILED,
    STATE_PLANNING,
    STATE_QUEUED,
    STATE_REJECTED,
    STATE_REPAIRING,
    STATE_REVIEW_PENDING,
    STATE_SUCCEEDED,
    STATE_VERIFYING,
    STATE_WAITING_FOR_APPROVAL,
    TaskExecutionContext,
    WorktreeRequiredError,
    attach_execution_worktree,
    cleanup_execution_worktree,
    get_execution_diff,
    promote_execution,
    reject_execution,
)
from backend.execution.hitl import global_hitl_tracker, resolve_gate
from backend.execution.providers.base import BaseModelProvider, ModelResponse, ToolCall
from backend.execution.replay import ReplayEngine
from backend.execution.verification.engine import (
    NOT_VERIFIED,
    PARTIALLY_VERIFIED,
    VERIFICATION_FAILED,
    VERIFIED,
)
from backend.routes.tasks import (
    run_task_pipeline,
    tasks_bp,
    trigger_cancellation,
)


class ScriptedProvider(BaseModelProvider):
    """Deterministic scripted provider yielding preconfigured ModelResponses."""

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
            text="Completed fallback step",
            tool_calls=[ToolCall(id="f_done", name="finish", arguments={"summary": "Completed fallback"})],
        )


class ReplayStoreAdapter:
    """Store adapter for ReplayEngine projection over runtime replay_events."""

    def __init__(self, events_by_id: Optional[Dict[str, List[Dict[str, Any]]]] = None):
        self._events: Dict[str, List[Dict[str, Any]]] = events_by_id or {}

    def get_events(self, execution_id: str) -> List[Dict[str, Any]]:
        return list(self._events.get(execution_id, []))


def _init_test_git_repo(path: Path) -> None:
    """Initialize a valid git repository with initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@aetherion.test"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Aetherion Tester"], cwd=str(path), check=True, capture_output=True)
    (path / "README.md").write_text("# Aetherion Root Project\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=str(path), check=True, capture_output=True)


# ==============================================================================
# 1. Authoritative Task Creation & Persistence
# ==============================================================================

class TestAuthoritativeTaskCreationAndPersistence(unittest.TestCase):
    """Validates authoritative task creation, payload preservation, and persistence."""

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        self.app.register_blueprint(tasks_bp)
        self.client = self.app.test_client()
        self.tenant_id = "tenant-auth-create"
        self.token = _make_access_token(user_id=self.tenant_id, email="lead@aetherion.test", role="admin")

    def test_task_create_captures_all_authoritative_fields(self):
        """POST /api/tasks captures objective, repo_root, base_ref, constraints, criteria, budget, security."""
        payload = {
            "objective": "Build high-throughput data ingestion pipeline",
            "repo_id": "repo-core-001",
            "repo_root": "/var/repos/core",
            "base_ref": "v1.2.0",
            "execution_constraints": {
                "require_review": True,
                "allow_plain_workspace": False,
                "max_iterations": 25,
            },
            "verification_criteria": {
                "expected_files": ["ingest.py", "test_ingest.py"],
                "run_tests": True,
            },
            "execution_budget": {
                "max_repair_attempts": 3,
                "max_tokens": 100000,
            },
            "security_context": {
                "role": "admin",
                "isolation_level": "worktree",
            },
        }

        resp = self.client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {self.token}"},
            json=payload,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        task_id = data["task_id"]
        self.assertTrue(task_id)

        # Check returned execution context summary
        exec_ctx = data.get("execution_context", {})
        self.assertEqual(exec_ctx.get("objective"), payload["objective"])
        self.assertEqual(exec_ctx.get("repo_root"), payload["repo_root"])
        self.assertEqual(exec_ctx.get("base_ref"), payload["base_ref"])
        self.assertEqual(exec_ctx.get("verification_criteria"), payload["verification_criteria"])
        self.assertEqual(exec_ctx.get("execution_budget"), payload["execution_budget"])

        # Check durable state persistence directly
        record = durable_state.get(task_id, self.tenant_id)
        self.assertIsNotNone(record)
        stored_payload = json.loads(record.get("payload_json") or "{}")
        self.assertEqual(stored_payload.get("objective"), payload["objective"])
        self.assertEqual(stored_payload.get("repo_id"), payload["repo_id"])
        self.assertEqual(stored_payload.get("repo_root"), payload["repo_root"])
        self.assertEqual(stored_payload.get("base_ref"), payload["base_ref"])
        self.assertEqual(stored_payload.get("execution_constraints"), payload["execution_constraints"])
        self.assertEqual(stored_payload.get("verification_criteria"), payload["verification_criteria"])
        self.assertEqual(stored_payload.get("execution_budget"), payload["execution_budget"])
        self.assertEqual(stored_payload.get("security_context"), payload["security_context"])

    def test_task_create_missing_prompt_rejected(self):
        """POST /api/tasks without prompt/objective fails with 400 Bad Request."""
        resp = self.client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"repo_id": "missing-prompt"},
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("Task prompt is required", data.get("error", ""))

    def test_task_get_and_list_endpoints(self):
        """GET /api/task/<tid> and GET /api/tasks retrieve authoritative tasks for tenant."""
        # Create task
        create_resp = self.client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"prompt": "Task for get/list test"},
        )
        self.assertEqual(create_resp.status_code, 201)
        tid = create_resp.get_json()["task_id"]

        # GET /api/task/<tid>
        get_resp = self.client.get(
            f"/api/task/{tid}",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.get_json()["task_id"], tid)

        # GET /api/tasks
        list_resp = self.client.get(
            "/api/tasks",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(list_resp.status_code, 200)
        tasks = list_resp.get_json()["tasks"]
        tids = [t["execution_id"] for t in tasks]
        self.assertIn(tid, tids)

    def test_task_idempotency_key_prevents_duplicate_creation(self):
        """Submitting with identical Idempotency-Key returns existing task."""
        idem_key = f"idem-{uuid.uuid4().hex[:8]}"
        payload = {"prompt": "Idempotent task"}

        resp1 = self.client.post(
            "/api/tasks",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Idempotency-Key": idem_key,
            },
            json=payload,
        )
        self.assertEqual(resp1.status_code, 201)
        tid1 = resp1.get_json()["task_id"]

        resp2 = self.client.post(
            "/api/tasks",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Idempotency-Key": idem_key,
            },
            json=payload,
        )
        self.assertEqual(resp2.status_code, 201)
        tid2 = resp2.get_json()["task_id"]
        self.assertEqual(tid1, tid2)


# ==============================================================================
# 2. Worktree Policy & Fail-Closed Contract
# ==============================================================================

class TestWorktreePolicyAndFailClosedContract(unittest.TestCase):
    """Validates the fail-closed worktree requirement and authorized plain workspace fallback."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeth_p14_wt_")
        self.tenant_id = "tenant-wt-policy"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_worktree_required_by_default_fails_closed_without_repo(self):
        """Autonomous coding task without git repo fails closed (WorktreeRequiredError)."""
        non_git_dir = Path(self.tmp) / "plain_dir"
        non_git_dir.mkdir()

        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={
                "objective": "Build parser",
                "repo_root": str(non_git_dir),
                "execution_constraints": {"allow_plain_workspace": False},
            },
        )
        tid = record["execution_id"]

        # Run pipeline directly
        res = run_task_pipeline(tid, self.tenant_id)
        self.assertEqual(res.get("status"), durable_state.STATUS_FAILED)
        self.assertEqual(res.get("failure_class"), durable_state.FAILURE_CLASS_QUEUE)
        self.assertIn("isolated worktree", res.get("error", "").lower())

        # Check durable state updated to FAILED
        updated_rec = durable_state.get(tid, self.tenant_id)
        self.assertEqual(updated_rec["status"], durable_state.STATUS_FAILED)

    def test_worktree_attachment_in_git_repo(self):
        """Autonomous task in git repo attaches isolated worktree and records branch in task payload."""
        git_repo = Path(self.tmp) / "git_repo"
        _init_test_git_repo(git_repo)

        provider = DeterministicTestProvider()
        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={
                "objective": "Create hello.py that prints Hello Aetherion",
                "repo_root": str(git_repo),
                "base_ref": "HEAD",
                "verification_criteria": {"expected_files": ["hello.py"]},
            },
        )
        tid = record["execution_id"]

        res = run_task_pipeline(tid, self.tenant_id, provider=provider)
        self.assertTrue(res.get("ok"))

        # Verify task payload stored worktree metadata
        rec = durable_state.get(tid, self.tenant_id)
        payload = json.loads(rec.get("payload_json") or "{}")
        self.assertIsNotNone(payload.get("worktree_id"))
        self.assertTrue(str(payload.get("branch", "")).startswith(f"aeth/{tid}-"))
        self.assertEqual(payload.get("repo_root"), str(git_repo))

    def test_authorized_plain_workspace_fallback(self):
        """Explicit allow_plain_workspace=True permits execution in non-git directory."""
        non_git_dir = Path(self.tmp) / "authorized_plain"
        non_git_dir.mkdir()

        provider = DeterministicTestProvider()
        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={
                "objective": "Create hello.py that prints Hello Aetherion",
                "repo_root": str(non_git_dir),
                "execution_constraints": {"allow_plain_workspace": True},
                "verification_criteria": {"expected_files": ["hello.py"]},
            },
        )
        tid = record["execution_id"]

        res = run_task_pipeline(tid, self.tenant_id, provider=provider)
        self.assertTrue(res.get("ok"))
        rec = durable_state.get(tid, self.tenant_id)
        self.assertEqual(rec["status"], durable_state.STATUS_SUCCEEDED)


# ==============================================================================
# 3. Authoritative Execution Lifecycle & Convergence
# ==============================================================================

class TestAuthoritativeExecutionLifecycleAndConvergence(unittest.TestCase):
    """Validates lifecycle progression and convergence across durable state, runtime, and replay."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeth_p14_life_")
        self.git_repo = Path(self.tmp) / "repo"
        _init_test_git_repo(self.git_repo)
        self.tenant_id = "tenant-life-conv"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lifecycle_convergence_pending_to_succeeded(self):
        """Durable state, runtime state, and replay events converge to SUCCEEDED."""
        provider = DeterministicTestProvider()
        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={
                "objective": "Create hello.py that prints Hello Aetherion",
                "repo_root": str(self.git_repo),
                "base_ref": "HEAD",
                "verification_criteria": {"expected_files": ["hello.py"]},
            },
        )
        tid = record["execution_id"]

        run_res = run_task_pipeline(tid, self.tenant_id, provider=provider)
        self.assertTrue(run_res["ok"])
        self.assertEqual(run_res["status"], durable_state.STATUS_SUCCEEDED)

        # Durable state verification
        rec = durable_state.get(tid, self.tenant_id)
        self.assertEqual(rec["status"], durable_state.STATUS_SUCCEEDED)
        res_json = json.loads(rec.get("result_json") or "{}")
        self.assertEqual(res_json.get("verification"), VERIFIED)
        self.assertIn("hello.py", res_json.get("files", []))

        # Replay event log verification
        adapter = ReplayStoreAdapter({tid: run_res.get("replay_events", [])})
        replay_engine = ReplayEngine(store=adapter)
        timeline = replay_engine.timeline(tid)
        self.assertEqual(timeline.terminal_status, "succeeded")
        self.assertGreater(len(timeline.frames), 0)

    def test_cancellation_stops_execution_with_cancel_wins(self):
        """Triggering cancellation stops execution thread and transitions state to stopped/cancelled."""
        # Slow provider that pauses
        class DelayProvider(BaseModelProvider):
            def name(self): return "delay_provider"
            def is_available(self): return True
            def chat(self, messages, tools):
                time.sleep(1.0)
                return ModelResponse(text="Delayed step")

        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={
                "objective": "Long running simulation",
                "repo_root": str(self.git_repo),
            },
        )
        tid = record["execution_id"]

        cancel_ev = threading.Event()
        exec_thread = threading.Thread(
            target=run_task_pipeline,
            args=(tid, self.tenant_id, cancel_ev, DelayProvider()),
            daemon=True,
        )
        exec_thread.start()
        time.sleep(0.3)

        # Cancel wins
        cancel_ev.set()
        durable_state.stop(tid, self.tenant_id)
        exec_thread.join(timeout=3.0)
        self.assertFalse(exec_thread.is_alive())

        rec = durable_state.get(tid, self.tenant_id)
        self.assertIn(rec["status"], (durable_state.STATUS_STOPPED, durable_state.STATUS_CANCELLED))

    def test_terminal_task_cannot_be_re_executed(self):
        """Attempting to execute an already terminal task via API returns 409 Conflict."""
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(tasks_bp)
        client = app.test_client()
        token = _make_access_token(user_id=self.tenant_id, email="lead@aetherion.test", role="admin")

        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={"prompt": "Already done task"},
        )
        tid = record["execution_id"]
        durable_state.succeed(tid, self.tenant_id, result={"done": True})

        resp = client.post(
            f"/api/task/{tid}/execute",
            headers={"Authorization": f"Bearer {token}"},
            json={"sync": True},
        )
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("already in terminal status", data.get("error", ""))


# ==============================================================================
# 4. HITL Integration in Coding Pipeline
# ==============================================================================

class TestHITLIntegrationInCodingPipeline(unittest.TestCase):
    """Validates blocking Human-in-the-Loop approval/rejection gates."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeth_p14_hitl_")
        self.git_repo = Path(self.tmp) / "repo"
        _init_test_git_repo(self.git_repo)
        self.tenant_id = "tenant-hitl-pipe"

        # Local SQLite for HITL tracker
        self.db_path = str(Path(self.tmp) / "hitl.db")
        global_hitl_tracker.db_path = self.db_path
        global_hitl_tracker._init_db()

        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        self.app.register_blueprint(tasks_bp)
        self.client = self.app.test_client()
        self.token = _make_access_token(user_id=self.tenant_id, email="lead@aetherion.test", role="admin")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hitl_approval_unblocks_dangerous_tool(self):
        """Dangerous command triggers approval gate; operator approve unblocks and completes."""
        marker = Path(self.tmp) / "HITL_APPROVED_MARKER.txt"
        cmd = f'python -c "import pathlib; pathlib.Path(r\'{marker}\').write_text(\'success\')" && rm -rf dummy'

        provider = ScriptedProvider([
            ModelResponse(
                text="Request dangerous cleanup",
                tool_calls=[ToolCall(id="c1", name="run_command", arguments={"command": cmd})],
            ),
            ModelResponse(
                text="Finish after approved command",
                tool_calls=[ToolCall(id="f1", name="finish", arguments={"summary": "Approved and finished"})],
            ),
        ])

        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={
                "objective": "Dangerous operation",
                "repo_root": str(self.git_repo),
                "execution_constraints": {"allow_plain_workspace": True},
            },
        )
        tid = record["execution_id"]

        is_waiting = threading.Event()

        def _runner():
            ctx = TaskExecutionContext(
                task_id=tid,
                objective="Dangerous operation",
                workspace_dir=self.git_repo,
                tenant_id=self.tenant_id,
                execution_constraints={"allow_plain_workspace": True},
            )
            runtime = RealAgentRuntime(context=ctx, provider=provider)
            for ev in runtime.run_stream(ctx.objective):
                if ev.get("type") == "approval":
                    is_waiting.set()

        th = threading.Thread(target=_runner, daemon=True)
        th.start()

        # Wait until runtime hits approval gate
        self.assertTrue(is_waiting.wait(timeout=5.0), "Did not encounter approval event")
        time.sleep(0.2)
        self.assertFalse(marker.exists(), "Command executed prior to approval!")

        # Approve gate via route API
        resolve_gate(tid, approved=True, comments="Operator approved via test")
        th.join(timeout=5.0)

        self.assertTrue(marker.exists(), "Approved command did not execute!")

    def test_hitl_rejection_denies_execution(self):
        """Dangerous command rejection blocks command from running."""
        marker = Path(self.tmp) / "HITL_REJECTED_MARKER.txt"
        cmd = f'python -c "import pathlib; pathlib.Path(r\'{marker}\').write_text(\'fail\')" && rm -rf dummy'

        provider = ScriptedProvider([
            ModelResponse(
                text="Attempt dangerous wipe",
                tool_calls=[ToolCall(id="c2", name="run_command", arguments={"command": cmd})],
            ),
            ModelResponse(
                text="Wrap up after rejection",
                tool_calls=[ToolCall(id="f2", name="finish", arguments={"summary": "Cleaned up"})],
            ),
        ])

        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={"objective": "Dangerous wipe"},
        )
        tid = record["execution_id"]

        is_waiting = threading.Event()

        def _runner():
            ctx = TaskExecutionContext(
                task_id=tid,
                objective="Dangerous wipe",
                workspace_dir=self.git_repo,
                tenant_id=self.tenant_id,
                execution_constraints={"allow_plain_workspace": True},
            )
            runtime = RealAgentRuntime(context=ctx, provider=provider)
            for ev in runtime.run_stream(ctx.objective):
                if ev.get("type") == "approval":
                    is_waiting.set()

        th = threading.Thread(target=_runner, daemon=True)
        th.start()

        self.assertTrue(is_waiting.wait(timeout=5.0))
        time.sleep(0.2)

        # Reject gate
        resolve_gate(tid, approved=False, comments="Security policy rejection")
        th.join(timeout=5.0)

        self.assertFalse(marker.exists(), "Rejected command was executed!")


# ==============================================================================
# 5. Objective Verification Truth & Bounded Repair
# ==============================================================================

class TestVerificationTruthAndBoundedRepair(unittest.TestCase):
    """Validates objective verification truth and bounded repair loops."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeth_p14_ver_")
        self.git_repo = Path(self.tmp) / "repo"
        _init_test_git_repo(self.git_repo)
        self.tenant_id = "tenant-ver-repair"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_model_unverified_claim_rejected_by_verifier_truth(self):
        """Model claim of success without meeting expected_files is rejected by verifier."""
        # Provider that claims completion but writes nothing
        provider = ScriptedProvider([
            ModelResponse(
                text="I claim that auth.py is completely written and passes all security checks!",
                tool_calls=[ToolCall(id="f_done", name="finish", arguments={"summary": "Created auth.py"})],
            ),
        ])

        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={
                "objective": "Create auth.py",
                "repo_root": str(self.git_repo),
                "verification_criteria": {"expected_files": ["auth.py"]},
                "execution_budget": {"max_repair_attempts": 0},
            },
        )
        tid = record["execution_id"]

        run_res = run_task_pipeline(tid, self.tenant_id, provider=provider)
        self.assertEqual(run_res.get("status"), durable_state.STATUS_FAILED)

        rec = durable_state.get(tid, self.tenant_id)
        self.assertEqual(rec["status"], durable_state.STATUS_FAILED)
        res_data = json.loads(rec.get("result_json") or "{}")
        self.assertIn(res_data.get("verification"), (NOT_VERIFIED, VERIFICATION_FAILED))

    def test_bounded_repair_loop_recovers_to_success(self):
        """Failed initial verification triggers repair loop that recovers to success."""
        # Attempt 1:
        #   Turn 1: writes wrong file (wrong_file.py)
        #   Turn 2: finishes attempt 1
        # Attempt 2 (Repair):
        #   Turn 3: writes expected file (required.py)
        #   Turn 4: finishes attempt 2
        provider = ScriptedProvider([
            ModelResponse(
                text="Attempt 1: write wrong file",
                tool_calls=[ToolCall(id="w1", name="write_file", arguments={"path": "wrong_file.py", "content": "x = 1\n"})],
            ),
            ModelResponse(
                text="Attempt 1: finish attempt",
                tool_calls=[ToolCall(id="f1", name="finish", arguments={"summary": "Done wrong file"})],
            ),
            ModelResponse(
                text="Attempt 2: repair by writing required file",
                tool_calls=[ToolCall(id="w2", name="write_file", arguments={"path": "required.py", "content": "print('ok')\n"})],
            ),
            ModelResponse(
                text="Attempt 2: finish repair attempt",
                tool_calls=[ToolCall(id="f2", name="finish", arguments={"summary": "Repaired and created required.py"})],
            ),
        ])

        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={
                "objective": "Create required.py",
                "repo_root": str(self.git_repo),
                "verification_criteria": {"expected_files": ["required.py"]},
                "execution_budget": {"max_repair_attempts": 2},
            },
        )
        tid = record["execution_id"]

        run_res = run_task_pipeline(tid, self.tenant_id, provider=provider)
        self.assertEqual(run_res.get("status"), durable_state.STATUS_SUCCEEDED)

        rec = durable_state.get(tid, self.tenant_id)
        self.assertEqual(rec["status"], durable_state.STATUS_SUCCEEDED)
        res_data = json.loads(rec.get("result_json") or "{}")
        self.assertEqual(res_data.get("verification"), VERIFIED)
        # Repair history must record the initial attempt failure
        repair_history = res_data.get("repair_history", [])
        self.assertTrue(len(repair_history) >= 1)
        self.assertIn(repair_history[0]["verification_status"], (NOT_VERIFIED, VERIFICATION_FAILED))

    def test_repair_budget_exhaustion_terminates_in_failure(self):
        """Repeated verification failures exhaust repair budget and terminate as FAILED."""
        # Attempt 1:
        #   Turn 1: write wrong file
        #   Turn 2: finish attempt 1
        # Attempt 2 (Repair):
        #   Turn 3: write still wrong file
        #   Turn 4: finish attempt 2
        provider = ScriptedProvider([
            ModelResponse(
                text="Attempt 1 wrong file",
                tool_calls=[ToolCall(id="w1", name="write_file", arguments={"path": "other.py", "content": "a = 1\n"})],
            ),
            ModelResponse(
                text="Attempt 1 finish",
                tool_calls=[ToolCall(id="f1", name="finish", arguments={"summary": "Attempt 1"})],
            ),
            ModelResponse(
                text="Attempt 2 still wrong file",
                tool_calls=[ToolCall(id="w2", name="write_file", arguments={"path": "other2.py", "content": "b = 2\n"})],
            ),
            ModelResponse(
                text="Attempt 2 finish",
                tool_calls=[ToolCall(id="f2", name="finish", arguments={"summary": "Attempt 2"})],
            ),
        ])

        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={
                "objective": "Create target.py",
                "repo_root": str(self.git_repo),
                "verification_criteria": {"expected_files": ["target.py"]},
                "execution_budget": {"max_repair_attempts": 1},
            },
        )
        tid = record["execution_id"]

        run_res = run_task_pipeline(tid, self.tenant_id, provider=provider)
        self.assertEqual(run_res.get("status"), durable_state.STATUS_FAILED)

        rec = durable_state.get(tid, self.tenant_id)
        self.assertEqual(rec["status"], durable_state.STATUS_FAILED)
        res_data = json.loads(rec.get("result_json") or "{}")
        self.assertIn(res_data.get("verification"), (NOT_VERIFIED, VERIFICATION_FAILED))


# ==============================================================================
# 6. Unified Diff & Evidence Safety
# ==============================================================================

class TestUnifiedDiffAndEvidenceSafety(unittest.TestCase):
    """Validates diff generation, parent git index isolation, and evidence safety."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeth_p14_diff_")
        self.git_repo = Path(self.tmp) / "parent_repo"
        _init_test_git_repo(self.git_repo)
        self.tenant_id = "tenant-diff-safety"

        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        self.app.register_blueprint(tasks_bp)
        self.client = self.app.test_client()
        self.token = _make_access_token(user_id=self.tenant_id, email="lead@aetherion.test", role="admin")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_execution_diff_generated_and_retrieved_via_api(self):
        """Unified diff is generated after task execution and returned via GET /api/task/<tid>/diff."""
        provider = DeterministicTestProvider()
        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={
                "objective": "Create hello.py that prints Hello Aetherion",
                "repo_root": str(self.git_repo),
                "base_ref": "HEAD",
                "verification_criteria": {"expected_files": ["hello.py"]},
            },
        )
        tid = record["execution_id"]

        run_res = run_task_pipeline(tid, self.tenant_id, provider=provider)
        self.assertTrue(run_res["ok"])

        # Fetch diff via API
        resp = self.client.get(
            f"/api/task/{tid}/diff",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        diff_text = data.get("diff", "")
        self.assertTrue(len(diff_text) > 0, "Diff was empty!")
        self.assertIn("hello.py", diff_text)

    def test_parent_repository_index_and_working_tree_isolation(self):
        """Parent repository working tree and git index are completely untouched by worktree diff."""
        ctx = TaskExecutionContext(
            task_id="task_diff_isolation",
            objective="Develop branch logic",
            workspace_dir=self.git_repo,
            repo_root=str(self.git_repo),
        )
        attach_execution_worktree(ctx, repo_root=self.git_repo)

        # Write new files and modify existing in isolated worktree
        wt_file = ctx.workspace_dir / "worker.py"
        wt_file.write_text("def work(): pass\n", encoding="utf-8")

        # Extract diff (which performs git add -N . inside isolated worktree)
        diff = get_execution_diff(ctx, ["worker.py"])
        self.assertIn("worker.py", diff)

        # CRITICAL SAFETY CHECK: Inspect parent repository
        parent_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(self.git_repo),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        parent_diff = subprocess.run(
            ["git", "diff"],
            cwd=str(self.git_repo),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        # Parent repository MUST have zero changes and zero staged/untracked files
        self.assertEqual(parent_status, "", f"Parent repository status polluted! Got:\n{parent_status}")
        self.assertEqual(parent_diff, "", f"Parent repository diff polluted! Got:\n{parent_diff}")

        cleanup_execution_worktree(ctx)


# ==============================================================================
# 7. Review and Promotion / Rejection Contract
# ==============================================================================

class TestReviewAndPromotionContract(unittest.TestCase):
    """Validates the explicit human review boundary, promotion metadata, and worktree pruning on rejection."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aeth_p14_rev_")
        self.git_repo = Path(self.tmp) / "repo"
        _init_test_git_repo(self.git_repo)
        self.tenant_id = "tenant-review-prom"

        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        self.app.register_blueprint(tasks_bp)
        self.client = self.app.test_client()
        self.token = _make_access_token(user_id=self.tenant_id, email="lead@aetherion.test", role="admin")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_review_pending_lifecycle_when_require_review_enabled(self):
        """When require_review is True, verified success transitions to REVIEW_PENDING."""
        provider = DeterministicTestProvider()
        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={
                "objective": "Create hello.py that prints Hello Aetherion",
                "repo_root": str(self.git_repo),
                "base_ref": "HEAD",
                "execution_constraints": {"require_review": True},
                "verification_criteria": {"expected_files": ["hello.py"]},
            },
        )
        tid = record["execution_id"]

        run_res = run_task_pipeline(tid, self.tenant_id, provider=provider)
        self.assertTrue(run_res["ok"])

        # Check task state is REVIEW_PENDING
        rec = durable_state.get(tid, self.tenant_id)
        self.assertEqual(rec["status"], durable_state.STATUS_REVIEW_PENDING)

        # GET /api/task/<tid>/review returns complete review package
        rev_resp = self.client.get(
            f"/api/task/{tid}/review",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(rev_resp.status_code, 200)
        rev_data = rev_resp.get_json()
        self.assertTrue(rev_data["ok"])
        self.assertEqual(rev_data["status"], durable_state.STATUS_REVIEW_PENDING)
        self.assertEqual(rev_data["verification"], VERIFIED)
        self.assertIn("hello.py", rev_data["diff"])

    def test_human_acceptance_and_promotion(self):
        """Human operator acceptance transitions task to ACCEPTED and preserves branch."""
        provider = DeterministicTestProvider()
        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={
                "objective": "Create hello.py that prints Hello Aetherion",
                "repo_root": str(self.git_repo),
                "base_ref": "HEAD",
                "execution_constraints": {"require_review": True},
                "verification_criteria": {"expected_files": ["hello.py"]},
            },
        )
        tid = record["execution_id"]
        run_task_pipeline(tid, self.tenant_id, provider=provider)

        # Accept / promote task
        resp = self.client.post(
            f"/api/task/{tid}/accept",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"comment": "Verified and approved for release", "target_ref": "main"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], durable_state.STATUS_ACCEPTED)

        # Promotion metadata verification
        promo = data.get("promotion_metadata", {})
        self.assertTrue(promo.get("promoted"))
        self.assertEqual(promo.get("target_ref"), "main")
        self.assertEqual(promo.get("approver"), self.tenant_id)

        # Verify durable state is ACCEPTED
        updated = durable_state.get(tid, self.tenant_id)
        self.assertEqual(updated["status"], durable_state.STATUS_ACCEPTED)

        # Branch must remain preserved in git repo
        branch_name = promo.get("branch")
        if branch_name:
            branches = subprocess.run(["git", "branch"], cwd=str(self.git_repo), capture_output=True, text=True).stdout
            self.assertIn(branch_name, branches)

    def test_human_rejection_and_worktree_discard(self):
        """Human operator rejection transitions task to REJECTED and prunes worktree/branch."""
        provider = DeterministicTestProvider()
        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={
                "objective": "Create hello.py that prints Hello Aetherion",
                "repo_root": str(self.git_repo),
                "base_ref": "HEAD",
                "execution_constraints": {"require_review": True},
                "verification_criteria": {"expected_files": ["hello.py"]},
            },
        )
        tid = record["execution_id"]
        run_task_pipeline(tid, self.tenant_id, provider=provider)

        rec_before = durable_state.get(tid, self.tenant_id)
        branch_name = json.loads(rec_before.get("payload_json") or "{}").get("branch")

        # Reject task
        resp = self.client.post(
            f"/api/task/{tid}/reject",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"reason": "Architectural deviation: rejected"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], durable_state.STATUS_REJECTED)

        # Verify durable state is REJECTED
        updated = durable_state.get(tid, self.tenant_id)
        self.assertEqual(updated["status"], durable_state.STATUS_REJECTED)

        # Branch must be deleted
        if branch_name:
            branches = subprocess.run(["git", "branch"], cwd=str(self.git_repo), capture_output=True, text=True).stdout
            self.assertNotIn(branch_name, branches)

    def test_promotion_gate_cannot_be_bypassed_on_failed_task(self):
        """Failed task cannot be accepted or promoted; returns 409 Conflict."""
        record = durable_state.create(
            kind="agent_task",
            tenant_id=self.tenant_id,
            payload={"prompt": "Flawed task"},
        )
        tid = record["execution_id"]
        durable_state.fail(tid, self.tenant_id, failure_class=durable_state.FAILURE_CLASS_VERIFIER)

        resp = self.client.post(
            f"/api/task/{tid}/accept",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"comment": "Bypass attempt"},
        )
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("Task must be SUCCEEDED or REVIEW_PENDING", data.get("error", ""))


if __name__ == "__main__":
    unittest.main()
