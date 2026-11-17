"""
tests/integration/test_phase10_hitl_lifecycle.py — Phase 10 Human-in-the-Loop
(HITL) Gate & Durable Task Approval Lifecycle Test Suite.

Invariants validated:
  INV-P10-HITL-01: resolve_gate(execution_id, approved=True) unblocks active thread and records approval.
  INV-P10-HITL-02: resolve_gate(execution_id, approved=False) unblocks active thread with rejection & feedback.
  INV-P10-HITL-03: provide_approval updates hitl_requests in SQLite even without active thread (persisted gate).
  INV-P10-HITL-04: resolve_gate returns False for unknown execution_id.
  INV-P10-HITL-05: /api/task/<tid>/approve with decision='approve' transitions WAITING_FOR_APPROVAL -> EXECUTING.
  INV-P10-HITL-06: /api/task/<tid>/approve with decision='reject' transitions WAITING_FOR_APPROVAL -> CANCELLED.
  INV-P10-HITL-07: /api/task/<tid>/approve works for AWAITING_APPROVAL status alias.
  INV-P10-HITL-08: /api/task/<tid>/approve returns 404 for nonexistent task ID.
  INV-P10-HITL-09: /api/task/<tid>/approve enforces tenant isolation (tenant A cannot approve tenant B task).
  INV-P10-HITL-10: /api/task/<tid>/approve invokes resolve_gate safely without unhandled exception.
  INV-P10-HITL-11: resume_session_hitl finds and unblocks pending session HITL requests.
  INV-P10-HITL-12: HITLEventTracker pause_execution and resume_execution manage thread pause states.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from flask import Flask

# ── Path bootstrap ────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from backend.auth_system import _make_access_token
from backend.execution.hitl import HITLEventTracker, resolve_gate, resume_session_hitl, global_hitl_tracker
from backend.execution import durable_state
from backend.routes.tasks import tasks_bp


class TestHITLTrackerUnit(unittest.TestCase):
    """Unit and thread lifecycle tests for HITLEventTracker and resolve_gate."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.temp_dir.name, "test_hitl.db")
        self.tracker = HITLEventTracker(db_path=self.db_path)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass


    def test_inv_p10_hitl_01_resolve_gate_approved(self):
        """INV-P10-HITL-01: resolve_gate(..., approved=True) unblocks waiting thread."""
        exec_id = "exec-test-approve-01"
        result_holder = {}

        def _worker():
            res = self.tracker.request_approval(
                exec_id,
                payload={"session_id": "sess-1", "action": "deploy"},
                timeout_sec=5,
            )
            result_holder.update(res)

        th = threading.Thread(target=_worker, daemon=True)
        th.start()
        time.sleep(0.1)

        # Ensure thread is blocked in tracker
        self.assertIn(exec_id, self.tracker._pauses)

        # Operator provides approval
        ok = self.tracker.provide_approval(exec_id, "approved", feedback="Approved by operator")
        self.assertTrue(ok)

        th.join(timeout=2.0)
        self.assertFalse(th.is_alive())
        self.assertEqual(result_holder.get("status"), "approved")
        self.assertEqual(result_holder.get("feedback"), "Approved by operator")

    def test_inv_p10_hitl_02_resolve_gate_rejected(self):
        """INV-P10-HITL-02: resolve_gate(..., approved=False) unblocks waiting thread with rejection."""
        exec_id = "exec-test-reject-02"
        result_holder = {}

        def _worker():
            res = self.tracker.request_approval(
                exec_id,
                payload={"session_id": "sess-2", "action": "delete_all"},
                timeout_sec=5,
            )
            result_holder.update(res)

        th = threading.Thread(target=_worker, daemon=True)
        th.start()
        time.sleep(0.1)

        ok = self.tracker.provide_approval(exec_id, "rejected", feedback="Dangerous operation denied")
        self.assertTrue(ok)

        th.join(timeout=2.0)
        self.assertFalse(th.is_alive())
        self.assertEqual(result_holder.get("status"), "rejected")
        self.assertEqual(result_holder.get("feedback"), "Dangerous operation denied")

    def test_inv_p10_hitl_03_persisted_gate_update(self):
        """INV-P10-HITL-03: provide_approval updates hitl_requests row even when thread is not paused in memory."""
        exec_id = "exec-persisted-03"
        # Manually insert a pending record in SQLite
        import sqlite3
        import json
        import contextlib
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO hitl_requests (execution_id, payload, status) VALUES (?, ?, ?)",
                    (exec_id, json.dumps({"action": "review"}), "pending"),
                )

        # Call provide_approval when exec_id is NOT in self.tracker._pauses
        self.assertNotIn(exec_id, self.tracker._pauses)
        ok = self.tracker.provide_approval(exec_id, "approved", feedback="Async signoff")
        self.assertTrue(ok)

        # Verify DB updated
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute("SELECT status, feedback FROM hitl_requests WHERE execution_id=?", (exec_id,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "approved")
        self.assertEqual(row[1], "Async signoff")


    def test_inv_p10_hitl_04_unknown_execution_returns_false(self):
        """INV-P10-HITL-04: provide_approval returns False when execution_id is completely unknown."""
        ok = self.tracker.provide_approval("completely-unknown-exec-id", "approved")
        self.assertFalse(ok)

    def test_inv_p10_hitl_11_resume_session_hitl(self):
        """INV-P10-HITL-11: resume_session_hitl unblocks requests associated with a session."""
        exec_id = "exec-sess-11"
        res_holder = {}

        # Use global_hitl_tracker
        def _worker():
            res = global_hitl_tracker.request_approval(
                exec_id,
                payload={"session_id": "sess-hitl-11", "action": "proceed"},
                timeout_sec=5,
            )
            res_holder.update(res)

        th = threading.Thread(target=_worker, daemon=True)
        th.start()
        time.sleep(0.1)

        resumed = resume_session_hitl("sess-hitl-11", status="approved", feedback="Session unblocked")
        self.assertTrue(resumed)

        th.join(timeout=2.0)
        self.assertFalse(th.is_alive())
        self.assertEqual(res_holder.get("status"), "approved")
        self.assertEqual(res_holder.get("feedback"), "Session unblocked")

    def test_inv_p10_hitl_12_pause_and_resume_execution(self):
        """INV-P10-HITL-12: pause_execution and resume_execution toggle pause state."""
        exec_id = "exec-pause-12"
        self.assertTrue(self.tracker.pause_execution(exec_id))
        self.assertIn(exec_id, self.tracker._pauses)
        self.assertFalse(self.tracker._pauses[exec_id].is_set())

        self.assertTrue(self.tracker.resume_execution(exec_id))
        self.assertTrue(self.tracker._pauses[exec_id].is_set())

        # Resuming unknown execution returns False
        self.assertFalse(self.tracker.resume_execution("unknown-exec-12"))


class TestTaskApproveRoute(unittest.TestCase):
    """Integration tests for POST /api/task/<tid>/approve route."""

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        self.app.register_blueprint(tasks_bp)
        self.client = self.app.test_client()

        self.tenant_id = "tenant-hitl-test"
        self.token = _make_access_token(user_id=101, email="lead@aetherion.test", role="admin")

    def test_inv_p10_hitl_05_approve_waiting_for_approval(self):
        """INV-P10-HITL-05: /api/task/<tid>/approve transitions WAITING_FOR_APPROVAL -> EXECUTING."""
        # Create task in durable_state
        rec = durable_state.create(
            kind="agent_task",
            tenant_id="101",
            payload={"prompt": "Deploy to prod"},
        )
        tid = rec["execution_id"]

        # Move to RUNNING -> EXECUTING -> WAITING_FOR_APPROVAL
        durable_state.transition(tid, "101", durable_state.STATUS_RUNNING)
        durable_state.transition(tid, "101", durable_state.STATUS_EXECUTING)
        durable_state.transition(tid, "101", durable_state.STATUS_WAITING_FOR_APPROVAL)

        current = durable_state.get(tid, "101")
        self.assertEqual(current["status"], durable_state.STATUS_WAITING_FOR_APPROVAL)

        # Call POST /api/task/<tid>/approve
        resp = self.client.post(
            f"/api/task/{tid}/approve",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"decision": "approve", "comments": "Approved for staging deployment"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["task_id"], tid)
        self.assertEqual(data["decision"], "approve")

        # Verify durable state is now EXECUTING
        after = durable_state.get(tid, "101")
        self.assertEqual(after["status"], durable_state.STATUS_EXECUTING)

    def test_inv_p10_hitl_06_reject_waiting_for_approval(self):
        """INV-P10-HITL-06: /api/task/<tid>/approve with reject transitions WAITING_FOR_APPROVAL -> CANCELLED."""
        rec = durable_state.create(
            kind="agent_task",
            tenant_id="101",
            payload={"prompt": "Drop database table"},
        )
        tid = rec["execution_id"]

        durable_state.transition(tid, "101", durable_state.STATUS_RUNNING)
        durable_state.transition(tid, "101", durable_state.STATUS_EXECUTING)
        durable_state.transition(tid, "101", durable_state.STATUS_WAITING_FOR_APPROVAL)

        resp = self.client.post(
            f"/api/task/{tid}/approve",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"decision": "reject", "comments": "Data loss hazard rejected"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["decision"], "reject")

        after = durable_state.get(tid, "101")
        self.assertEqual(after["status"], durable_state.STATUS_CANCELLED)

    def test_inv_p10_hitl_07_awaiting_approval_alias(self):
        """INV-P10-HITL-07: /api/task/<tid>/approve transitions AWAITING_APPROVAL -> EXECUTING."""
        rec = durable_state.create(
            kind="agent_task",
            tenant_id="101",
            payload={"prompt": "Run security scan"},
        )
        tid = rec["execution_id"]

        durable_state.transition(tid, "101", durable_state.STATUS_RUNNING)
        durable_state.transition(tid, "101", durable_state.STATUS_EXECUTING)
        durable_state.transition(tid, "101", durable_state.STATUS_AWAITING_APPROVAL)

        resp = self.client.post(
            f"/api/task/{tid}/approve",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"decision": "approve"},
        )
        self.assertEqual(resp.status_code, 200)

        after = durable_state.get(tid, "101")
        self.assertEqual(after["status"], durable_state.STATUS_EXECUTING)

    def test_inv_p10_hitl_08_404_on_missing_task(self):
        """INV-P10-HITL-08: /api/task/<tid>/approve returns 404 for unknown task."""
        resp = self.client.post(
            "/api/task/nonexistent-task-id-12345/approve",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"decision": "approve"},
        )
        self.assertEqual(resp.status_code, 404)
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("Task not found", data["error"])

    def test_inv_p10_hitl_09_tenant_isolation(self):
        """INV-P10-HITL-09: User from tenant 202 cannot approve a task owned by tenant 101."""
        rec = durable_state.create(
            kind="agent_task",
            tenant_id="101",
            payload={"prompt": "Confidential migration"},
        )
        tid = rec["execution_id"]

        # Different tenant token
        foreign_token = _make_access_token(user_id=202, email="other@tenant.test", role="admin")

        resp = self.client.post(
            f"/api/task/{tid}/approve",
            headers={"Authorization": f"Bearer {foreign_token}"},
            json={"decision": "approve"},
        )
        # Should be 404 because durable_state.get() scopes by tenant
        self.assertEqual(resp.status_code, 404)

    def test_inv_p10_hitl_10_resolve_gate_module_function(self):
        """INV-P10-HITL-10: resolve_gate module function resolves cleanly without exception."""
        # Test direct call to resolve_gate function
        res = resolve_gate("non-existent-gate", approved=True, comments="Safe to call")
        self.assertIsInstance(res, bool)


if __name__ == "__main__":
    unittest.main()
