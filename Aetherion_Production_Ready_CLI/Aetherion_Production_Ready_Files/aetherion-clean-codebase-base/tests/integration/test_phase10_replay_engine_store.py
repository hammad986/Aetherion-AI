"""
tests/integration/test_phase10_replay_engine_store.py — Phase 10 ReplayEngine
Store Integration Test Suite.

Validates Phase 9 audit residual finding FINDING-P9-02:
Exercising ReplayEngine projections over a concrete ExecutionStore adapter.

Invariants validated:
  INV-P10-REP-01: ReplayEngine initialized with store adapter reads events via store.get_events().
  INV-P10-REP-02: timeline() projects events into ReplayTimeline with full frame sequence and summary.
  INV-P10-REP-03: timeline() pagination with limit and offset windows frames and flags truncated=True.
  INV-P10-REP-04: timeline() kind filtering (kinds=['tool']) filters frames without distorting folded state.
  INV-P10-REP-05: frame() returns frame at seq index, and None for out-of-bounds indices.
  INV-P10-REP-06: seek() by seq reconstructs exact state snapshot at sequence index.
  INV-P10-REP-07: seek() by timestamp resolves to the latest frame at-or-before target timestamp.
  INV-P10-REP-08: diff() computes monotonic deltas (tools, files, errors) between two sequence points.
  INV-P10-REP-09: diff() normalizes reversed sequence order seamlessly.
  INV-P10-REP-10: transcript() aggregates stream_chunk events into full transcript text.
  INV-P10-REP-11: bundle() exports self-describing, schema-valid, redacted projection bundle.
  INV-P10-REP-12: ReplayEngine degrades gracefully when store.get_events() raises an exception.
  INV-P10-REP-13: ReplayEngine with store=None handles missing store safely without crashing.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Path bootstrap ────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from backend.execution.replay import (
    ReplayEngine,
    ReplayFrame,
    ReplayTimeline,
    _TASK_COMPLETED,
    _TASK_STARTED,
    _TASK_SUCCEEDED,
    _TOOL_COMPLETED,
    _TOOL_CALLED,
    _FILE_MODIFIED,
    _VERIFIER_PASSED,
    _VERIFIER_STARTED,
    _STREAM_CHUNK,
)



class InMemoryStoreAdapter:
    """Store adapter implementing the get_events contract for ReplayEngine."""

    def __init__(self, events_by_exec_id: Optional[Dict[str, List[Dict[str, Any]]]] = None):
        self._events: Dict[str, List[Dict[str, Any]]] = events_by_exec_id or {}

    def get_events(self, execution_id: str) -> List[Dict[str, Any]]:
        return list(self._events.get(execution_id, []))

    def add_event(self, execution_id: str, event: Dict[str, Any]) -> None:
        self._events.setdefault(execution_id, []).append(event)


class FailingStoreAdapter:
    """Simulates a database failure on get_events."""

    def get_events(self, execution_id: str) -> List[Dict[str, Any]]:
        raise RuntimeError("Database connection pool exhausted")


class TestPhase10ReplayEngineStore(unittest.TestCase):
    """Integration test suite for ReplayEngine backed by a store adapter."""

    def setUp(self):
        self.exec_id = "exec-replay-store-001"
        self.session_id = "session-test-001"
        self.t0 = 1700000000.0

        # Build an authentic event trajectory
        self.sample_events = [
            {
                "event_id": "evt-001",
                "event_type": _TASK_STARTED,
                "session_id": self.session_id,
                "correlation_id": "corr-1",
                "timestamp": self.t0 + 0.1,
                "payload": {"prompt": "Write test cases"},
            },
            {
                "event_id": "evt-002",
                "event_type": _STREAM_CHUNK,
                "session_id": self.session_id,
                "correlation_id": "corr-1",
                "timestamp": self.t0 + 0.2,
                "payload": {"content": "Planning implementation... "},
            },
            {
                "event_id": "evt-003",
                "event_type": _TOOL_CALLED,
                "session_id": self.session_id,
                "correlation_id": "corr-1",
                "timestamp": self.t0 + 0.3,
                "payload": {"tool_name": "file_writer", "path": "tests/test_foo.py"},
            },
            {
                "event_id": "evt-004",
                "event_type": _TOOL_COMPLETED,
                "session_id": self.session_id,
                "correlation_id": "corr-1",
                "timestamp": self.t0 + 0.5,
                "payload": {"tool_name": "file_writer", "file_path": "tests/test_foo.py", "lines": 42},
            },
            {
                "event_id": "evt-005",
                "event_type": _FILE_MODIFIED,
                "session_id": self.session_id,
                "correlation_id": "corr-1",
                "timestamp": self.t0 + 0.55,
                "payload": {"file_path": "tests/test_foo.py"},
            },
            {
                "event_id": "evt-006",
                "event_type": _STREAM_CHUNK,
                "session_id": self.session_id,
                "correlation_id": "corr-1",
                "timestamp": self.t0 + 0.6,
                "payload": {"content": "Running test verification..."},
            },
            {
                "event_id": "evt-007",
                "event_type": _VERIFIER_STARTED,
                "session_id": self.session_id,
                "correlation_id": "corr-1",
                "timestamp": self.t0 + 0.7,
                "payload": {"verifier_type": "unit_test"},
            },
            {
                "event_id": "evt-008",
                "event_type": _VERIFIER_PASSED,
                "session_id": self.session_id,
                "correlation_id": "corr-1",
                "timestamp": self.t0 + 0.9,
                "payload": {"score": 1.0, "details": "All unit tests pass"},
            },
            {
                "event_id": "evt-009",
                "event_type": _TASK_COMPLETED,
                "session_id": self.session_id,
                "correlation_id": "corr-1",
                "timestamp": self.t0 + 1.0,
                "payload": {"result": "success", "token": "sensitive_jwt_token_12345"},
            },
        ]


        self.store = InMemoryStoreAdapter({self.exec_id: self.sample_events})
        self.engine = ReplayEngine(store=self.store)

    def test_inv_p10_rep_01_store_adapter_initialization(self):
        """INV-P10-REP-01: ReplayEngine binds to custom store adapter."""
        self.assertIs(self.engine.store, self.store)
        events = self.engine._events(self.exec_id)
        self.assertEqual(len(events), 9)

    def test_inv_p10_rep_02_timeline_full_projection(self):
        """INV-P10-REP-02: timeline() generates ReplayTimeline with accurate fold summary."""
        tl = self.engine.timeline(self.exec_id)
        self.assertIsInstance(tl, ReplayTimeline)
        self.assertEqual(tl.execution_id, self.exec_id)
        self.assertEqual(tl.session_id, self.session_id)
        self.assertEqual(tl.total_frames, 9)
        self.assertFalse(tl.truncated)

        summary = tl.summary
        self.assertEqual(summary["event_count"], 9)
        self.assertEqual(summary["frame_count"], 9)
        self.assertEqual(summary["final_status"], "succeeded")
        self.assertTrue(summary["verifier_backed"])
        self.assertTrue(summary["terminal"])
        self.assertEqual(summary["tools_called"], 1)
        self.assertEqual(summary["tools_completed"], 1)
        self.assertEqual(summary["files_modified"], 1)
        self.assertIn("tests/test_foo.py", summary["files_touched"])

    def test_inv_p10_rep_03_timeline_pagination(self):
        """INV-P10-REP-03: timeline() with limit and offset windows frames and flags truncated=True."""
        tl = self.engine.timeline(self.exec_id, limit=3, offset=2)
        self.assertEqual(len(tl.frames), 3)
        self.assertEqual(tl.frames[0].seq, 2)
        self.assertEqual(tl.frames[1].seq, 3)
        self.assertEqual(tl.frames[2].seq, 4)
        self.assertTrue(tl.truncated)
        self.assertEqual(tl.total_frames, 9)

    def test_inv_p10_rep_04_timeline_kind_filtering(self):
        """INV-P10-REP-04: timeline(kinds=['tool']) filters output frames but retains cumulative state."""
        tl = self.engine.timeline(self.exec_id, kinds=["tool"])
        self.assertEqual(len(tl.frames), 2)
        for f in tl.frames:
            self.assertEqual(f.kind, "tool")
        # Ensure state on the second tool frame includes prior events
        self.assertEqual(tl.frames[1].state["tools_called"], 1)
        self.assertEqual(tl.frames[1].state["tools_completed"], 1)

    def test_inv_p10_rep_05_frame_by_seq(self):
        """INV-P10-REP-05: frame() returns frame at index, or None if out-of-bounds."""
        f0 = self.engine.frame(self.exec_id, 0)
        self.assertIsNotNone(f0)
        self.assertEqual(f0.seq, 0)
        self.assertEqual(f0.event_type, _TASK_STARTED)

        f_last = self.engine.frame(self.exec_id, 8)
        self.assertIsNotNone(f_last)
        self.assertEqual(f_last.seq, 8)

        self.assertIsNone(self.engine.frame(self.exec_id, -1))
        self.assertIsNone(self.engine.frame(self.exec_id, 99))
        self.assertIsNone(self.engine.frame("nonexistent-exec", 0))

    def test_inv_p10_rep_06_seek_by_seq(self):
        """INV-P10-REP-06: seek(seq=N) reconstructs state at sequence N."""
        s3 = self.engine.seek(self.exec_id, seq=3)
        self.assertTrue(s3["found"])
        self.assertEqual(s3["seq"], 3)
        self.assertEqual(s3["state"]["tools_called"], 1)
        self.assertEqual(s3["state"]["tools_completed"], 1)
        self.assertFalse(s3["state"]["verifier_backed"])

        # Seek to frame 8 (after verifier passed and completed)
        s8 = self.engine.seek(self.exec_id, seq=8)
        self.assertTrue(s8["found"])
        self.assertEqual(s8["state"]["status"], "succeeded")
        self.assertTrue(s8["state"]["verifier_backed"])

    def test_inv_p10_rep_07_seek_by_timestamp(self):
        """INV-P10-REP-07: seek(timestamp=T) resolves to the last frame at-or-before T."""
        # Query instant between evt-004 (t0+0.5) and evt-005 (t0+0.55)
        res = self.engine.seek(self.exec_id, timestamp=self.t0 + 0.52)
        self.assertTrue(res["found"])
        self.assertEqual(res["seq"], 3)  # evt-004 is at seq 3

        # Query instant before any events
        res_early = self.engine.seek(self.exec_id, timestamp=self.t0 - 10.0)
        self.assertTrue(res_early["found"])
        self.assertEqual(res_early["seq"], -1)

    def test_inv_p10_rep_08_diff_between_frames(self):
        """INV-P10-REP-08: diff() computes monotonic deltas between two sequence frames."""
        d = self.engine.diff(self.exec_id, from_seq=0, to_seq=8)
        self.assertTrue(d["found"])
        self.assertEqual(d["from_seq"], 0)
        self.assertEqual(d["to_seq"], 8)
        self.assertEqual(d["from_status"], "running")
        self.assertEqual(d["to_status"], "succeeded")
        self.assertTrue(d["status_changed"])

        changes = d["changes"]
        self.assertEqual(changes.get("tools_called"), 1)
        self.assertEqual(changes.get("tools_completed"), 1)
        self.assertEqual(changes.get("verifier_passed"), 1)
        self.assertEqual(changes.get("files_modified"), 1)
        self.assertIn("tests/test_foo.py", d["files_added"])

    def test_inv_p10_rep_09_diff_order_normalization(self):
        """INV-P10-REP-09: diff(8, 0) normalizes to diff(0, 8)."""
        d_forward = self.engine.diff(self.exec_id, from_seq=0, to_seq=8)
        d_reverse = self.engine.diff(self.exec_id, from_seq=8, to_seq=0)
        self.assertEqual(d_forward["from_seq"], d_reverse["from_seq"])
        self.assertEqual(d_forward["to_seq"], d_reverse["to_seq"])
        self.assertEqual(d_forward["changes"], d_reverse["changes"])

    def test_inv_p10_rep_10_transcript_aggregation(self):
        """INV-P10-REP-10: transcript() aggregates stream chunks in order."""
        t = self.engine.transcript(self.exec_id)
        self.assertEqual(t["execution_id"], self.exec_id)
        self.assertEqual(t["transcript"], "Planning implementation... Running test verification...")
        self.assertFalse(t["truncated"])

    def test_inv_p10_rep_11_bundle_export_and_redaction(self):
        """INV-P10-REP-11: bundle() exports schema-valid redacted bundle."""
        b = self.engine.bundle(self.exec_id, include_frames=True)
        self.assertEqual(b["schema_version"], "replay/v1")
        self.assertEqual(b["kind"], "aetherion.replay.bundle")
        self.assertEqual(b["execution_id"], self.exec_id)
        self.assertEqual(b["session_id"], self.session_id)
        self.assertEqual(b["frame_count"], 9)

        # Check payload redaction in completed frame (frame 8)
        frame_8 = b["frames"][8]
        self.assertNotIn("sensitive_jwt_token_12345", str(frame_8["payload"]))
        self.assertEqual(frame_8["payload"]["token"], "***")


    def test_inv_p10_rep_12_failing_store_graceful_degradation(self):
        """INV-P10-REP-12: Engine gracefully handles exceptions from store without crashing."""
        failing_engine = ReplayEngine(store=FailingStoreAdapter())
        tl = failing_engine.timeline("failing-exec-id")
        self.assertEqual(tl.total_frames, 0)
        self.assertEqual(len(tl.frames), 0)
        self.assertEqual(tl.summary["event_count"], 0)

    def test_inv_p10_rep_13_none_store_graceful_degradation(self):
        """INV-P10-REP-13: ReplayEngine with store=None returns empty projection."""
        empty_engine = ReplayEngine(store=None)
        # Explicitly ensure store is None
        empty_engine.store = None
        tl = empty_engine.timeline("any-exec")
        self.assertEqual(tl.total_frames, 0)
        self.assertEqual(len(tl.frames), 0)


if __name__ == "__main__":
    unittest.main()
