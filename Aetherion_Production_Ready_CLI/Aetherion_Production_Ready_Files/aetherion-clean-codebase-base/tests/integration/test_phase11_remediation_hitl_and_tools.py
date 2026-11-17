"""
tests/integration/test_phase11_remediation_hitl_and_tools.py
Authoritative Integration and Adversarial Verification Suite for Phase 11 Blockers:
- FINDING-P11-05: Real Blocking Human-in-the-Loop (HITL) Execution Gating
- FINDING-P11-06: Strict Tool Argument Validation Boundary
"""
import contextlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Ensure paths
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "backend"))

from backend.execution.agent_runtime.runtime import RealAgentRuntime
from backend.execution.agent_runtime.state import (
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_EXECUTING,
    STATE_WAITING_FOR_APPROVAL,
)
from backend.execution.hitl import HITLEventTracker, global_hitl_tracker, resolve_gate
from backend.execution.providers.base import BaseModelProvider, ModelResponse, ToolCall
from backend.execution.sandbox.boundary import WorkspaceBoundary
from backend.execution.tools.registry import AgentToolRegistry
from backend.execution.tools.schemas import ToolRequest, ToolResult
from backend.execution import durable_state


class MockScriptedProvider(BaseModelProvider):
    """Mock provider returning scripted responses."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.call_idx = 0

    def name(self) -> str:
        return "mock_scripted"

    def is_available(self) -> bool:
        return True

    def chat(self, messages, tools):
        if self.call_idx < len(self.responses):
            resp = self.responses[self.call_idx]
            self.call_idx += 1
            return resp
        return ModelResponse(
            text="Finished",
            tool_calls=[ToolCall(id="f", name="finish", arguments={"summary": "Done"})],
        )


class TestPhase11HITLRemediation(unittest.TestCase):
    """Comprehensive adversarial tests for FINDING-P11-05 (Real Blocking HITL)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ws = Path(self.temp_dir.name) / "workspace"
        self.ws.mkdir()
        # Isolated sessions db for testing
        self.db_path = str(Path(self.temp_dir.name) / "sessions.db")
        global_hitl_tracker.db_path = self.db_path
        global_hitl_tracker._init_db()

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_inv_p11_hitl_01_dangerous_command_blocks_before_execution(self):
        """A: Dangerous command genuinely blocks before any execution side effect occurs."""
        marker_file = self.ws / "UNAPPROVED_SIDE_EFFECT.txt"
        dangerous_cmd = f'python -c "import pathlib; pathlib.Path(r\'{marker_file}\').write_text(\'pwned\')" && rm -rf dummy'

        provider = MockScriptedProvider([
            ModelResponse(
                text="Running dangerous cleanup",
                tool_calls=[ToolCall(id="call_1", name="run_command", arguments={"command": dangerous_cmd})],
            ),
        ])

        task_id = "task_hitl_block_01"
        runtime = RealAgentRuntime(
            workspace_dir=str(self.ws),
            session_id="sess_block_01",
            task_id=task_id,
            tenant_id="tenant_1",
            provider=provider,
        )

        events_seen = []
        is_blocked = threading.Event()

        def _runner():
            for ev in runtime.run_stream("Clean up files"):
                events_seen.append(ev)
                if ev.get("type") == "approval":
                    is_blocked.set()

        th = threading.Thread(target=_runner, daemon=True)
        th.start()

        # Wait until runtime emits approval event and blocks
        self.assertTrue(is_blocked.wait(timeout=5.0), "Runtime did not emit approval event")
        time.sleep(0.5)

        # Thread must still be alive (blocked awaiting operator approval)
        self.assertTrue(th.is_alive(), "Runtime did not block; thread terminated prematurely")
        # Hard invariant: side effect file must NOT exist!
        self.assertFalse(marker_file.exists(), "Dangerous command executed before approval was granted!")

        # Cleanly unblock by rejecting
        resolve_gate(task_id, approved=False, comments="Audit unblock")
        th.join(timeout=3.0)
        self.assertFalse(marker_file.exists(), "Dangerous command executed even after rejection!")

    def test_inv_p11_hitl_02_approved_command_executes_exactly_once(self):
        """B: Approved command unblocks and executes exactly once."""
        marker_file = self.ws / "APPROVED_EXECUTION.txt"
        dangerous_cmd = f'python -c "import pathlib; p = pathlib.Path(r\'{marker_file}\'); p.write_text(str(int(p.read_text()) + 1 if p.exists() else 1))" && rm -rf dummy'

        provider = MockScriptedProvider([
            ModelResponse(
                text="Dangerous run",
                tool_calls=[ToolCall(id="call_1", name="run_command", arguments={"command": dangerous_cmd})],
            ),
            ModelResponse(
                text="Finish up",
                tool_calls=[ToolCall(id="call_2", name="finish", arguments={"summary": "Complete"})],
            ),
        ])

        task_id = "task_hitl_approve_02"
        runtime = RealAgentRuntime(
            workspace_dir=str(self.ws),
            session_id="sess_approve_02",
            task_id=task_id,
            tenant_id="tenant_1",
            provider=provider,
        )

        events_seen = []
        is_blocked = threading.Event()

        def _runner():
            for ev in runtime.run_stream("Run maintenance"):
                events_seen.append(ev)
                if ev.get("type") == "approval":
                    is_blocked.set()

        th = threading.Thread(target=_runner, daemon=True)
        th.start()

        self.assertTrue(is_blocked.wait(timeout=5.0))
        time.sleep(0.3)
        self.assertFalse(marker_file.exists(), "Pre-approval execution breach")

        # Approve
        ok = resolve_gate(task_id, approved=True, comments="Operator verified")
        self.assertTrue(ok)

        th.join(timeout=5.0)
        self.assertFalse(th.is_alive())

        # Must have executed exactly once (marker content == '1')
        self.assertTrue(marker_file.exists())
        self.assertEqual(marker_file.read_text().strip(), "1")

        # Verify approval_resolved event in stream
        resolved_evs = [e for e in events_seen if e.get("type") == "approval_resolved"]
        self.assertEqual(len(resolved_evs), 1)
        self.assertEqual(resolved_evs[0].get("decision"), "approved")

    def test_inv_p11_hitl_03_rejected_command_never_executes(self):
        """C: Rejected command is denied and never executed."""
        marker_file = self.ws / "REJECTED_MUST_NOT_EXIST.txt"
        dangerous_cmd = f'python -c "import pathlib; pathlib.Path(r\'{marker_file}\').write_text(\'bad\')" && rm -rf dummy'

        provider = MockScriptedProvider([
            ModelResponse(
                text="Dangerous run",
                tool_calls=[ToolCall(id="call_1", name="run_command", arguments={"command": dangerous_cmd})],
            ),
            ModelResponse(
                text="Acknowledged rejection",
                tool_calls=[ToolCall(id="call_2", name="finish", arguments={"summary": "Handled rejection"})],
            ),
        ])

        task_id = "task_hitl_reject_03"
        runtime = RealAgentRuntime(
            workspace_dir=str(self.ws),
            session_id="sess_reject_03",
            task_id=task_id,
            tenant_id="tenant_1",
            provider=provider,
        )

        events_seen = []
        is_blocked = threading.Event()

        def _runner():
            for ev in runtime.run_stream("Run dangerous stuff"):
                events_seen.append(ev)
                if ev.get("type") == "approval":
                    is_blocked.set()

        th = threading.Thread(target=_runner, daemon=True)
        th.start()

        self.assertTrue(is_blocked.wait(timeout=5.0))
        time.sleep(0.3)

        # Reject
        ok = resolve_gate(task_id, approved=False, comments="Operation too dangerous")
        self.assertTrue(ok)

        th.join(timeout=5.0)
        self.assertFalse(th.is_alive())

        # Hard invariant: Side effect file must never exist
        self.assertFalse(marker_file.exists(), "Command executed despite rejection!")

        # Verify rejection event
        resolved_evs = [e for e in events_seen if e.get("type") == "approval_resolved"]
        self.assertEqual(len(resolved_evs), 1)
        self.assertEqual(resolved_evs[0].get("decision"), "rejected")

    def test_inv_p11_hitl_04_expired_approval_never_executes(self):
        """D: Expired approval times out deterministically and does not execute."""
        marker_file = self.ws / "TIMEOUT_MUST_NOT_EXIST.txt"
        dangerous_cmd = f'python -c "import pathlib; pathlib.Path(r\'{marker_file}\').write_text(\'timeout\')" && rm -rf dummy'

        provider = MockScriptedProvider([
            ModelResponse(
                text="Dangerous run",
                tool_calls=[ToolCall(id="call_1", name="run_command", arguments={"command": dangerous_cmd})],
            ),
        ])

        task_id = "task_hitl_timeout_04"
        runtime = RealAgentRuntime(
            workspace_dir=str(self.ws),
            session_id="sess_timeout_04",
            task_id=task_id,
            tenant_id="tenant_1",
            provider=provider,
        )

        events_seen = []
        # Run directly with a patched fast timeout in global_hitl_tracker
        orig_req = global_hitl_tracker.request_approval

        def _fast_timeout(execution_id, payload, timeout_sec=300, cancel_event=None):
            return orig_req(execution_id, payload, timeout_sec=1, cancel_event=cancel_event)

        global_hitl_tracker.request_approval = _fast_timeout
        try:
            for ev in runtime.run_stream("Run and let expire"):
                events_seen.append(ev)
        finally:
            global_hitl_tracker.request_approval = orig_req

        self.assertFalse(marker_file.exists(), "Command executed after approval timeout!")
        timeout_evs = [e for e in events_seen if e.get("type") == "approval_resolved" and e.get("decision") == "timeout"]
        self.assertEqual(len(timeout_evs), 1)

    def test_inv_p11_hitl_05_cancelled_execution_never_executes(self):
        """E: Cancellation during approval wait unblocks and prevents execution."""
        marker_file = self.ws / "CANCEL_MUST_NOT_EXIST.txt"
        dangerous_cmd = f'python -c "import pathlib; pathlib.Path(r\'{marker_file}\').write_text(\'cancelled\')" && rm -rf dummy'

        provider = MockScriptedProvider([
            ModelResponse(
                text="Dangerous run",
                tool_calls=[ToolCall(id="call_1", name="run_command", arguments={"command": dangerous_cmd})],
            ),
        ])

        task_id = "task_hitl_cancel_05"
        runtime = RealAgentRuntime(
            workspace_dir=str(self.ws),
            session_id="sess_cancel_05",
            task_id=task_id,
            tenant_id="tenant_1",
            provider=provider,
        )

        cancel_evt = threading.Event()
        events_seen = []
        is_blocked = threading.Event()

        def _runner():
            for ev in runtime.run_stream("Run cancel test", cancel_event=cancel_evt):
                events_seen.append(ev)
                if ev.get("type") == "approval":
                    is_blocked.set()

        th = threading.Thread(target=_runner, daemon=True)
        th.start()

        self.assertTrue(is_blocked.wait(timeout=5.0))
        time.sleep(0.3)

        # Trigger cancellation
        cancel_evt.set()

        th.join(timeout=3.0)
        self.assertFalse(th.is_alive())
        self.assertFalse(marker_file.exists(), "Command executed despite cancellation!")

        cancelled_evs = [e for e in events_seen if e.get("type") == "approval_resolved" and e.get("decision") == "cancelled"]
        self.assertEqual(len(cancelled_evs), 1)

    def test_inv_p11_hitl_06_duplicate_decisions_are_idempotent(self):
        """F & G: Duplicate approve or reject calls are safe and return False on redundant attempts."""
        exec_id = "task_duplicate_06"
        th_ready = threading.Event()

        def _fake_worker():
            global_hitl_tracker.request_approval(exec_id, payload={"cmd": "test"}, timeout_sec=5)
            th_ready.set()

        th = threading.Thread(target=_fake_worker, daemon=True)
        th.start()
        time.sleep(0.2)

        # First approval must succeed
        ok1 = resolve_gate(exec_id, approved=True, comments="first")
        self.assertTrue(ok1)

        # Duplicate approval must return False (already resolved)
        ok2 = resolve_gate(exec_id, approved=True, comments="second")
        self.assertFalse(ok2)

        # Opposing decision after approval must return False
        ok3 = resolve_gate(exec_id, approved=False, comments="third")
        self.assertFalse(ok3)

        th.join(timeout=2.0)

    def test_inv_p11_hitl_07_tenant_isolation_enforced(self):
        """H: Tenant A cannot approve or resolve Tenant B's task in tasks route."""
        from backend.app import app
        from backend.routes.tasks import tasks_bp
        from backend.auth_system import _make_access_token

        # Create task under tenant_B
        rec = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_B",
            payload={"prompt": "Tenant B work"},
        )
        tid = rec["execution_id"]
        durable_state.transition(tid, "tenant_B", durable_state.STATUS_RUNNING)
        durable_state.transition(tid, "tenant_B", durable_state.STATUS_EXECUTING)
        durable_state.transition(tid, "tenant_B", durable_state.STATUS_WAITING_FOR_APPROVAL)

        with app.test_client() as client:
            # Attempt to approve as tenant_A
            token_a = _make_access_token(user_id=999, email="tenant_A@test.com", role="operator")
            res = client.post(
                f"/api/task/{tid}/approve",
                headers={"Authorization": f"Bearer {token_a}"},
                json={"decision": "approve"},
            )
            # Tenant isolation must return 404
            self.assertEqual(res.status_code, 404)
            self.assertFalse(res.get_json()["ok"])

    def test_inv_p11_hitl_08_terminal_task_approval_rejected(self):
        """Terminal task cannot be approved."""
        from backend.app import app
        from backend.auth_system import _make_access_token

        rec = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_term",
            payload={"prompt": "Terminal task"},
        )
        tid = rec["execution_id"]
        durable_state.transition(tid, "tenant_term", durable_state.STATUS_RUNNING)
        durable_state.transition(tid, "tenant_term", durable_state.STATUS_FAILED)

        with app.test_client() as client:
            token = _make_access_token(user_id="tenant_term", email="term@test.com", role="operator")
            res = client.post(
                f"/api/task/{tid}/approve",
                headers={"Authorization": f"Bearer {token}"},
                json={"decision": "approve"},
            )
            self.assertEqual(res.status_code, 409)


class TestPhase11ToolValidationRemediation(unittest.TestCase):
    """Comprehensive adversarial tests for FINDING-P11-06 (Strict Tool Argument Validation Boundary)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ws = Path(self.temp_dir.name) / "workspace"
        self.ws.mkdir()
        self.boundary = WorkspaceBoundary(self.ws)
        self.registry = AgentToolRegistry(self.boundary)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_inv_p11_tool_01_none_arguments_fails_gracefully(self):
        """None arguments fail gracefully without raising AttributeError."""
        req = ToolRequest(tool="read_file", arguments=None, task_id="t1", session_id="s1")
        res = self.registry.execute_tool(req)
        self.assertFalse(res.ok)
        self.assertIn("expected a JSON object (dict)", res.error)

    def test_inv_p11_tool_02_list_arguments_fails_gracefully(self):
        """List arguments fail gracefully without raising AttributeError."""
        req = ToolRequest(tool="write_file", arguments=["app.py", "code"], task_id="t1", session_id="s1")
        res = self.registry.execute_tool(req)
        self.assertFalse(res.ok)
        self.assertIn("expected a JSON object (dict)", res.error)

    def test_inv_p11_tool_03_scalar_arguments_fails_gracefully(self):
        """Scalar arguments (string, int) fail gracefully."""
        req = ToolRequest(tool="run_command", arguments="ls -la", task_id="t1", session_id="s1")
        res = self.registry.execute_tool(req)
        self.assertFalse(res.ok)
        self.assertIn("expected a JSON object (dict)", res.error)

    def test_inv_p11_tool_04_write_file_validation_matrix(self):
        """Strict matrix for write_file: required path and content."""
        # 1. Empty dict
        r1 = self.registry.execute_tool(ToolRequest(tool="write_file", arguments={}, task_id="t1", session_id="s1"))
        self.assertFalse(r1.ok)
        self.assertIn("Missing required parameter", r1.error)

        # 2. Missing content
        r2 = self.registry.execute_tool(ToolRequest(tool="write_file", arguments={"path": "hello.txt"}, task_id="t1", session_id="s1"))
        self.assertFalse(r2.ok)
        self.assertIn("content", r2.error)
        self.assertFalse((self.ws / "hello.txt").exists(), "Empty file created despite missing content")

        # 3. Missing path
        r3 = self.registry.execute_tool(ToolRequest(tool="write_file", arguments={"content": "data"}, task_id="t1", session_id="s1"))
        self.assertFalse(r3.ok)
        self.assertIn("path", r3.error)

        # 4. Empty path string
        r4 = self.registry.execute_tool(ToolRequest(tool="write_file", arguments={"path": "", "content": "data"}, task_id="t1", session_id="s1"))
        self.assertFalse(r4.ok)
        self.assertIn("cannot be empty", r4.error)

        # 5. Whitespace-only path
        r5 = self.registry.execute_tool(ToolRequest(tool="write_file", arguments={"path": "   ", "content": "data"}, task_id="t1", session_id="s1"))
        self.assertFalse(r5.ok)
        self.assertIn("cannot be empty", r5.error)

        # 6. Type mismatch (content is int)
        r6 = self.registry.execute_tool(ToolRequest(tool="write_file", arguments={"path": "valid.txt", "content": 12345}, task_id="t1", session_id="s1"))
        self.assertFalse(r6.ok)
        self.assertIn("expected string", r6.error)

        # 7. Valid request
        r7 = self.registry.execute_tool(ToolRequest(tool="write_file", arguments={"path": "valid.txt", "content": "hello world"}, task_id="t1", session_id="s1"))
        self.assertTrue(r7.ok)
        self.assertTrue((self.ws / "valid.txt").exists())
        self.assertEqual((self.ws / "valid.txt").read_text(), "hello world")

    def test_inv_p11_tool_05_read_file_empty_and_directory_guarded(self):
        """read_file rejects empty paths and directory paths."""
        r1 = self.registry.execute_tool(ToolRequest(tool="read_file", arguments={"path": ""}, task_id="t1", session_id="s1"))
        self.assertFalse(r1.ok)

        # Create a directory
        sub_dir = self.ws / "sub_dir"
        sub_dir.mkdir()
        r2 = self.registry.execute_tool(ToolRequest(tool="read_file", arguments={"path": "sub_dir"}, task_id="t1", session_id="s1"))
        self.assertFalse(r2.ok)
        self.assertIn("Cannot read directory", r2.error)

    def test_inv_p11_tool_06_delete_file_guards_root(self):
        """delete_file refuses to delete workspace root."""
        r1 = self.registry.execute_tool(ToolRequest(tool="delete_file", arguments={"path": ""}, task_id="t1", session_id="s1"))
        self.assertFalse(r1.ok)

        r2 = self.registry.execute_tool(ToolRequest(tool="delete_file", arguments={"path": "."}, task_id="t1", session_id="s1"))
        self.assertFalse(r2.ok)
        self.assertTrue(self.ws.exists())

    def test_inv_p11_tool_07_run_command_validation(self):
        """run_command rejects empty or non-string commands."""
        r1 = self.registry.execute_tool(ToolRequest(tool="run_command", arguments={}, task_id="t1", session_id="s1"))
        self.assertFalse(r1.ok)

        r2 = self.registry.execute_tool(ToolRequest(tool="run_command", arguments={"command": ""}, task_id="t1", session_id="s1"))
        self.assertFalse(r2.ok)

        r3 = self.registry.execute_tool(ToolRequest(tool="run_command", arguments={"command": 42}, task_id="t1", session_id="s1"))
        self.assertFalse(r3.ok)

        r4 = self.registry.execute_tool(ToolRequest(tool="run_command", arguments={"command": "echo test_ok"}, task_id="t1", session_id="s1"))
        self.assertTrue(r4.ok)
        self.assertIn("test_ok", r4.stdout)

    def test_inv_p11_tool_08_finish_tool_enum_validation(self):
        """finish tool validates verification enum."""
        r1 = self.registry.execute_tool(ToolRequest(tool="finish", arguments={"summary": "done", "verification": "INVALID_STATUS"}, task_id="t1", session_id="s1"))
        self.assertFalse(r1.ok)
        self.assertIn("expected one of", r1.error)

        r2 = self.registry.execute_tool(ToolRequest(tool="finish", arguments={"summary": "done", "verification": "VERIFIED"}, task_id="t1", session_id="s1"))
        self.assertTrue(r2.ok)

    def test_inv_p11_tool_09_runtime_stays_alive_on_malformed_model_calls(self):
        """RealAgentRuntime stays alive and handles malformed model output without crashing generator."""
        provider = MockScriptedProvider([
            ModelResponse(
                text="Malformed tool call 1",
                tool_calls=[ToolCall(id="c1", name="write_file", arguments=None)],
            ),
            ModelResponse(
                text="Malformed tool call 2",
                tool_calls=[ToolCall(id="c2", name="read_file", arguments=["not_a_dict"])],
            ),
            ModelResponse(
                text="Valid finish",
                tool_calls=[ToolCall(id="c3", name="finish", arguments={"summary": "Recovered successfully"})],
            ),
        ])

        runtime = RealAgentRuntime(
            workspace_dir=str(self.ws),
            session_id="sess_malformed_09",
            task_id="task_malformed_09",
            tenant_id="tenant_1",
            provider=provider,
        )

        events = list(runtime.run_stream("Test prompt with malformed output"))
        # Generator completed without exception
        self.assertTrue(len(events) > 0)
        done_evs = [e for e in events if e.get("type") == "done"]
        self.assertEqual(len(done_evs), 1)
        self.assertIn(done_evs[0].get("status"), ("completed", "failed"))


if __name__ == "__main__":
    unittest.main()
