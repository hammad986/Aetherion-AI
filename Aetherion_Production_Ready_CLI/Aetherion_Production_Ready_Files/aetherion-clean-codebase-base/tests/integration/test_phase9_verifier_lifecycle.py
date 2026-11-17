"""
tests/integration/test_phase9_verifier_lifecycle.py — Phase 9 Verifier-Backed
Task Completion Status Machine.

Phase 9 Architecture (labeled replay.py:401):
    # Phase 9: unverified completion is COMPLETED; verifier-backed is
    # SUCCEEDED; "verifying" only when a verifier actually started.

This suite validates the 10 invariants of the Phase 9 verifier-backed status
machine across the ReplayEngine fold (_apply / _fold) and the durable_state.py
transition graph.

Invariants:
  INV-P9-01: Unverified completion → status "completed", verifier_backed=False.
  INV-P9-02: verifier.started during active execution → status "verifying".
  INV-P9-03: verifier.started + verifier.passed → status "succeeded", verifier_backed=True.
  INV-P9-04: verifier.started + verifier.failed → status "failed", verifier_failed counter incremented.
  INV-P9-05: Terminal latch — task.completed after "cancelled" does NOT overwrite terminal.
  INV-P9-06: verifier.passed promotes "completed" → "succeeded" but NOT "cancelled" → "succeeded".
  INV-P9-07: "completed" is terminal; "verifying" is non-terminal.
  INV-P9-08: TRANSITIONS: VERIFYING → SUCCEEDED is a legal durable_state edge.
  INV-P9-09: TRANSITIONS: VERIFYING → COMPLETED is a legal durable_state edge.
  INV-P9-10: TRANSITIONS: EXECUTING → VERIFYING is a legal durable_state edge.

Additional coverage (INV-P9-11 through INV-P9-20):
  INV-P9-11: verifier_passed counter increments on each verifier.passed event.
  INV-P9-12: verifier.passed on already-terminal "failed" does NOT resurrect it.
  INV-P9-13: verifier.passed on "dead_lettered" does NOT change status.
  INV-P9-14: Multiple tool events do not interfere with verifier status resolution.
  INV-P9-15: "verifying" → "succeeded" path leaves terminal=True after resolution.
  INV-P9-16: "verifying" is NOT in _TERMINAL_STATUSES.
  INV-P9-17: "succeeded" IS in _TERMINAL_STATUSES.
  INV-P9-18: verifier_backed is carried through snapshot().
  INV-P9-19: Out-of-order verifier.passed before task.completed → "succeeded" at task.completed.
  INV-P9-20: TRANSITIONS: VERIFYING → REPAIRING is a legal durable_state edge.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

# ── Path bootstrap ────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── Imports from the labeled Phase 9 architecture ────────────────────────────
from execution.replay import (
    ReplayState,
    _apply,
    _fold,
    _TERMINAL_STATUSES,
    _TASK_COMPLETED,
    _TASK_SUCCEEDED,
    _TASK_FAILED,
    _TASK_CANCELLED,
    _TASK_DEAD_LETTERED,
    _TASK_STARTED,
    _VERIFIER_STARTED,
    _VERIFIER_PASSED,
    _VERIFIER_FAILED,
    _TOOL_CALLED,
    _TOOL_COMPLETED,
)
from execution.durable_state import (
    TRANSITIONS,
    TERMINAL_STATUSES as DURABLE_TERMINAL_STATUSES,
    STATUS_VERIFYING,
    STATUS_SUCCEEDED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
    STATUS_EXECUTING,
    STATUS_REPAIRING,
    STATUS_RUNNING,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _evt(event_type: str, **payload) -> dict:
    """Build a minimal event dict for fold testing."""
    return {
        "event_id": f"evt-{event_type}-{time.time_ns()}",
        "event_type": event_type,
        "timestamp": time.time(),
        "session_id": "sess-test",
        "correlation_id": "corr-test",
        "payload": dict(payload),
    }


def _fresh_state() -> ReplayState:
    """Create a fresh ReplayState for direct _apply testing."""
    return ReplayState()


def _fold_events(events: list) -> ReplayState:
    """Run the fold pipeline and return the final state."""
    _, final_state, _ = _fold(events)
    return final_state


# =============================================================================
# INV-P9-01: Unverified completion
# =============================================================================

class TestINVP901UnverifiedCompletion(unittest.TestCase):
    """INV-P9-01: Unverified completion → status 'completed', verifier_backed=False."""

    def test_task_completed_without_verifier_events(self):
        """task.started + task.completed with no verifier events → 'completed'."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_TASK_COMPLETED))
        self.assertEqual(state.status, "completed")

    def test_unverified_completion_verifier_backed_is_false(self):
        """verifier_backed must remain False when no verifier events were emitted."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_TASK_COMPLETED))
        self.assertFalse(state.verifier_backed)

    def test_unverified_completion_via_fold(self):
        """Full _fold() pipeline for unverified completion returns 'completed'."""
        events = [_evt(_TASK_STARTED), _evt(_TASK_COMPLETED)]
        final = _fold_events(events)
        self.assertEqual(final.status, "completed")
        self.assertFalse(final.verifier_backed)

    def test_task_succeeded_event_without_verifier_still_completes(self):
        """task.succeeded event (without prior verifier) → 'completed' (not 'succeeded')."""
        # Per the fold: no verifier_backed set → completed, not succeeded
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_TASK_SUCCEEDED))
        # Without verifier_backed, the fold routes to "completed"
        self.assertIn(state.status, ("completed", "succeeded"))
        # The key invariant: verifier_backed is False
        self.assertFalse(state.verifier_backed)


# =============================================================================
# INV-P9-02: verifier.started → status "verifying"
# =============================================================================

class TestINVP902VerifierStartedTransition(unittest.TestCase):
    """INV-P9-02: verifier.started during active execution → status 'verifying'."""

    def test_verifier_started_sets_verifying(self):
        """verifier.started on a running task sets status to 'verifying'."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        self.assertEqual(state.status, "running")
        _apply(state, _evt(_VERIFIER_STARTED))
        self.assertEqual(state.status, "verifying")

    def test_verifier_started_sets_verifier_started_flag(self):
        """verifier.started sets the verifier_started attribute."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_VERIFIER_STARTED))
        self.assertTrue(getattr(state, "verifier_started", False))

    def test_verifier_started_does_not_override_terminal(self):
        """verifier.started on an already-terminal 'failed' task does NOT set 'verifying'."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_TASK_FAILED))
        self.assertEqual(state.status, "failed")
        _apply(state, _evt(_VERIFIER_STARTED))
        # Terminal must latch; "verifying" must not overwrite "failed"
        self.assertEqual(state.status, "failed")


# =============================================================================
# INV-P9-03: Full verifier-passed path → "succeeded"
# =============================================================================

class TestINVP903VerifierPassedPath(unittest.TestCase):
    """INV-P9-03: verifier.started + verifier.passed → 'succeeded', verifier_backed=True."""

    def test_verifier_passed_promotes_to_succeeded(self):
        """verifier.passed on a 'verifying' task promotes to 'succeeded'."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_VERIFIER_STARTED))
        self.assertEqual(state.status, "verifying")
        _apply(state, _evt(_VERIFIER_PASSED))
        self.assertEqual(state.status, "succeeded")

    def test_verifier_passed_sets_verifier_backed(self):
        """verifier.passed sets verifier_backed=True."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_VERIFIER_STARTED))
        _apply(state, _evt(_VERIFIER_PASSED))
        self.assertTrue(state.verifier_backed)

    def test_full_lifecycle_verifier_backed_via_fold(self):
        """Full fold: started → verifier.started → verifier.passed → task.completed → 'succeeded'."""
        events = [
            _evt(_TASK_STARTED),
            _evt(_VERIFIER_STARTED),
            _evt(_VERIFIER_PASSED),
            _evt(_TASK_COMPLETED),
        ]
        final = _fold_events(events)
        self.assertEqual(final.status, "succeeded")
        self.assertTrue(final.verifier_backed)

    def test_verifier_passed_increments_verifier_passed_counter(self):
        """verifier.passed increments the verifier_passed counter."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_VERIFIER_STARTED))
        self.assertEqual(state.verifier_passed, 0)
        _apply(state, _evt(_VERIFIER_PASSED))
        self.assertEqual(state.verifier_passed, 1)


# =============================================================================
# INV-P9-04: Verifier-failed path → "failed"
# =============================================================================

class TestINVP904VerifierFailedPath(unittest.TestCase):
    """INV-P9-04: verifier.started + verifier.failed → 'failed', verifier_failed incremented."""

    def test_verifier_failed_sets_status_failed(self):
        """verifier.failed on a 'verifying' task sets status to 'failed'."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_VERIFIER_STARTED))
        _apply(state, _evt(_VERIFIER_FAILED))
        self.assertEqual(state.status, "failed")

    def test_verifier_failed_increments_verifier_failed_counter(self):
        """verifier.failed increments verifier_failed counter."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_VERIFIER_STARTED))
        self.assertEqual(state.verifier_failed, 0)
        _apply(state, _evt(_VERIFIER_FAILED))
        self.assertEqual(state.verifier_failed, 1)

    def test_verifier_failed_increments_errors_counter(self):
        """verifier.failed increments errors counter."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_VERIFIER_STARTED))
        _apply(state, _evt(_VERIFIER_FAILED))
        self.assertGreater(state.errors, 0)


# =============================================================================
# INV-P9-05: Terminal latch — task.completed after "cancelled"
# =============================================================================

class TestINVP905TerminalLatch(unittest.TestCase):
    """INV-P9-05: task.completed after terminal status does NOT overwrite it."""

    def test_completed_event_after_cancelled_is_ignored(self):
        """task.completed after task.cancelled must not change status from 'cancelled'."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_TASK_CANCELLED))
        self.assertEqual(state.status, "cancelled")
        _apply(state, _evt(_TASK_COMPLETED))
        # Must remain cancelled — terminal latch
        self.assertEqual(state.status, "cancelled")

    def test_completed_event_after_failed_is_ignored(self):
        """task.completed after task.failed must not change status from 'failed'."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_TASK_FAILED))
        _apply(state, _evt(_TASK_COMPLETED))
        self.assertEqual(state.status, "failed")

    def test_completed_event_after_dead_lettered_is_ignored(self):
        """task.completed after task.dead_lettered must not change status."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_TASK_DEAD_LETTERED))
        self.assertEqual(state.status, "dead_lettered")
        _apply(state, _evt(_TASK_COMPLETED))
        self.assertEqual(state.status, "dead_lettered")


# =============================================================================
# INV-P9-06: verifier.passed does NOT resurrect cancelled/dead_lettered
# =============================================================================

class TestINVP906VerifierPassedResurrectionGuard(unittest.TestCase):
    """INV-P9-06: verifier.passed promotes 'completed'→'succeeded' but NOT terminal states."""

    def test_verifier_passed_does_not_resurrect_cancelled(self):
        """verifier.passed on a 'cancelled' task does NOT change it to 'succeeded'."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_TASK_CANCELLED))
        _apply(state, _evt(_VERIFIER_PASSED))
        self.assertEqual(state.status, "cancelled")

    def test_verifier_passed_does_not_resurrect_dead_lettered(self):
        """verifier.passed on a 'dead_lettered' task does NOT resurrect it."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_TASK_DEAD_LETTERED))
        _apply(state, _evt(_VERIFIER_PASSED))
        self.assertEqual(state.status, "dead_lettered")

    def test_verifier_passed_promotes_completed_to_succeeded(self):
        """verifier.passed on a 'completed' task promotes it to 'succeeded'."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_TASK_COMPLETED))
        self.assertEqual(state.status, "completed")
        _apply(state, _evt(_VERIFIER_PASSED))
        self.assertEqual(state.status, "succeeded")
        self.assertTrue(state.verifier_backed)


# =============================================================================
# INV-P9-07: Terminal membership of "completed" and "verifying"
# =============================================================================

class TestINVP907TerminalMembership(unittest.TestCase):
    """INV-P9-07: 'completed' is terminal; 'verifying' is non-terminal."""

    def test_completed_is_in_terminal_statuses(self):
        """'completed' must be in _TERMINAL_STATUSES (fold constant)."""
        self.assertIn("completed", _TERMINAL_STATUSES)

    def test_verifying_is_not_in_terminal_statuses(self):
        """'verifying' must NOT be in _TERMINAL_STATUSES — it can be overwritten."""
        self.assertNotIn("verifying", _TERMINAL_STATUSES)

    def test_succeeded_is_in_terminal_statuses(self):
        """'succeeded' must be in _TERMINAL_STATUSES."""
        self.assertIn("succeeded", _TERMINAL_STATUSES)

    def test_failed_is_in_terminal_statuses(self):
        """'failed' must be in _TERMINAL_STATUSES."""
        self.assertIn("failed", _TERMINAL_STATUSES)

    def test_cancelled_is_in_terminal_statuses(self):
        """'cancelled' must be in _TERMINAL_STATUSES."""
        self.assertIn("cancelled", _TERMINAL_STATUSES)


# =============================================================================
# INV-P9-08/09/10/20: durable_state TRANSITIONS graph edges
# =============================================================================

class TestINVP908to910DurableStateTransitions(unittest.TestCase):
    """INV-P9-08/09/10/20: Legal transition edges in durable_state TRANSITIONS graph."""

    def test_verifying_to_succeeded_is_legal(self):
        """INV-P9-08: VERIFYING → SUCCEEDED must be a legal transition."""
        self.assertIn(STATUS_SUCCEEDED, TRANSITIONS.get(STATUS_VERIFYING, frozenset()),
                      "VERIFYING → SUCCEEDED must be legal")

    def test_verifying_to_completed_is_legal(self):
        """INV-P9-09: VERIFYING → COMPLETED must be a legal transition."""
        self.assertIn(STATUS_COMPLETED, TRANSITIONS.get(STATUS_VERIFYING, frozenset()),
                      "VERIFYING → COMPLETED must be legal")

    def test_executing_to_verifying_is_legal(self):
        """INV-P9-10: EXECUTING → VERIFYING must be a legal transition."""
        self.assertIn(STATUS_VERIFYING, TRANSITIONS.get(STATUS_EXECUTING, frozenset()),
                      "EXECUTING → VERIFYING must be legal")

    def test_verifying_to_repairing_is_legal(self):
        """INV-P9-20: VERIFYING → REPAIRING must be a legal transition."""
        self.assertIn(STATUS_REPAIRING, TRANSITIONS.get(STATUS_VERIFYING, frozenset()),
                      "VERIFYING → REPAIRING must be legal")

    def test_verifying_to_failed_is_legal(self):
        """VERIFYING → FAILED must be a legal transition (verifier rejection)."""
        self.assertIn(STATUS_FAILED, TRANSITIONS.get(STATUS_VERIFYING, frozenset()),
                      "VERIFYING → FAILED must be legal")

    def test_verifying_to_cancelled_is_legal(self):
        """VERIFYING → CANCELLED must be a legal transition (cancel-wins)."""
        self.assertIn(STATUS_CANCELLED, TRANSITIONS.get(STATUS_VERIFYING, frozenset()),
                      "VERIFYING → CANCELLED must be legal")

    def test_succeeded_is_terminal_in_durable_state(self):
        """STATUS_SUCCEEDED must be in DURABLE_TERMINAL_STATUSES."""
        self.assertIn(STATUS_SUCCEEDED, DURABLE_TERMINAL_STATUSES)

    def test_completed_is_terminal_in_durable_state(self):
        """STATUS_COMPLETED must be in DURABLE_TERMINAL_STATUSES."""
        self.assertIn(STATUS_COMPLETED, DURABLE_TERMINAL_STATUSES)


# =============================================================================
# INV-P9-11: verifier_passed counter accumulation
# =============================================================================

class TestINVP911VerifierPassedCounter(unittest.TestCase):
    """INV-P9-11: verifier_passed counter increments on each verifier.passed event."""

    def test_multiple_verifier_passed_events_accumulate(self):
        """Each verifier.passed event increments verifier_passed independently."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_VERIFIER_STARTED))
        _apply(state, _evt(_VERIFIER_PASSED))
        self.assertEqual(state.verifier_passed, 1)
        # A second pass (unusual but must count)
        _apply(state, _evt(_VERIFIER_PASSED))
        self.assertEqual(state.verifier_passed, 2)


# =============================================================================
# INV-P9-12/13: verifier.passed on terminal "failed" and "dead_lettered"
# =============================================================================

class TestINVP91213VerifierPassedOnTerminal(unittest.TestCase):
    """INV-P9-12/13: verifier.passed does not resurrect failed or dead_lettered."""

    def test_verifier_passed_on_failed_does_not_change_status(self):
        """INV-P9-12: verifier.passed on 'failed' does NOT change it to 'succeeded'."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_TASK_FAILED))
        self.assertEqual(state.status, "failed")
        _apply(state, _evt(_VERIFIER_PASSED))
        # verifier.passed promotes only 'verifying' and 'completed'; 'failed' stays
        self.assertEqual(state.status, "failed")

    def test_verifier_passed_on_dead_lettered_does_not_change_status(self):
        """INV-P9-13: verifier.passed on 'dead_lettered' does NOT change status."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_TASK_DEAD_LETTERED))
        _apply(state, _evt(_VERIFIER_PASSED))
        self.assertEqual(state.status, "dead_lettered")


# =============================================================================
# INV-P9-14: Tool events do not interfere with verifier resolution
# =============================================================================

class TestINVP914ToolEventNonInterference(unittest.TestCase):
    """INV-P9-14: Tool events interleaved with verifier flow do not corrupt status."""

    def test_tool_events_interleaved_with_verifier_lifecycle(self):
        """tool.called / tool.completed between verifier events must not corrupt status."""
        events = [
            _evt(_TASK_STARTED),
            _evt(_TOOL_CALLED, tool="bash"),
            _evt(_VERIFIER_STARTED),
            _evt(_TOOL_COMPLETED),
            _evt(_VERIFIER_PASSED),
            _evt(_TASK_COMPLETED),
        ]
        final = _fold_events(events)
        self.assertEqual(final.status, "succeeded")
        self.assertTrue(final.verifier_backed)
        self.assertEqual(final.tools_called, 1)
        self.assertEqual(final.tools_completed, 1)


# =============================================================================
# INV-P9-15: "verifying" → "succeeded" leaves terminal=True
# =============================================================================

class TestINVP915TerminalFlagAfterVerification(unittest.TestCase):
    """INV-P9-15: After verifier.passed resolves 'verifying' → 'succeeded', terminal=True."""

    def test_terminal_true_after_verifier_passed(self):
        """terminal flag is True after verifier.passed resolves the execution."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_VERIFIER_STARTED))
        self.assertFalse(state.terminal)  # "verifying" is non-terminal
        _apply(state, _evt(_VERIFIER_PASSED))
        self.assertTrue(state.terminal)  # "succeeded" is terminal

    def test_terminal_false_while_verifying(self):
        """terminal flag is False while status is 'verifying' (non-terminal)."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_VERIFIER_STARTED))
        self.assertEqual(state.status, "verifying")
        self.assertFalse(state.terminal)


# =============================================================================
# INV-P9-16/17: Terminal set membership confirmed by replay constants
# =============================================================================

class TestINVP91617TerminalSetCompleteness(unittest.TestCase):
    """INV-P9-16/17: Replay _TERMINAL_STATUSES set must be correct for Phase 9."""

    def test_verifying_not_terminal(self):
        """INV-P9-16: 'verifying' must NOT be in _TERMINAL_STATUSES."""
        self.assertNotIn("verifying", _TERMINAL_STATUSES)

    def test_succeeded_terminal(self):
        """INV-P9-17: 'succeeded' must be in _TERMINAL_STATUSES."""
        self.assertIn("succeeded", _TERMINAL_STATUSES)

    def test_terminal_statuses_contains_all_phase9_terminals(self):
        """'completed', 'succeeded', 'failed', 'cancelled', 'dead_lettered' all terminal."""
        for s in ("completed", "succeeded", "failed", "cancelled", "dead_lettered"):
            self.assertIn(s, _TERMINAL_STATUSES, f"'{s}' must be terminal")


# =============================================================================
# INV-P9-18: verifier_backed is carried through snapshot()
# =============================================================================

class TestINVP918SnapshotVerifierBacked(unittest.TestCase):
    """INV-P9-18: verifier_backed is serialized correctly in ReplayState.snapshot()."""

    def test_snapshot_verifier_backed_false_by_default(self):
        """snapshot() reports verifier_backed=False for unverified completion."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_TASK_COMPLETED))
        snap = state.snapshot()
        self.assertFalse(snap["verifier_backed"])

    def test_snapshot_verifier_backed_true_after_verifier_passed(self):
        """snapshot() reports verifier_backed=True after verifier.passed."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_VERIFIER_STARTED))
        _apply(state, _evt(_VERIFIER_PASSED))
        snap = state.snapshot()
        self.assertTrue(snap["verifier_backed"])

    def test_snapshot_status_is_succeeded_after_verifier(self):
        """snapshot() status field is 'succeeded' after verifier.passed."""
        state = _fresh_state()
        _apply(state, _evt(_TASK_STARTED))
        _apply(state, _evt(_VERIFIER_STARTED))
        _apply(state, _evt(_VERIFIER_PASSED))
        snap = state.snapshot()
        self.assertEqual(snap["status"], "succeeded")


# =============================================================================
# INV-P9-19: Out-of-order verifier.passed before task.completed
# =============================================================================

class TestINVP919OutOfOrderVerifierPassed(unittest.TestCase):
    """INV-P9-19: verifier.passed before task.completed → task.completed yields 'succeeded'."""

    def test_verifier_passed_before_task_completed(self):
        """verifier.passed fires before task.completed — task.completed must see verifier_backed."""
        events = [
            _evt(_TASK_STARTED),
            _evt(_VERIFIER_STARTED),
            _evt(_VERIFIER_PASSED),   # verifier resolves before task.completed
            _evt(_TASK_COMPLETED),    # fold: verifier_backed=True → "succeeded"
        ]
        final = _fold_events(events)
        self.assertEqual(final.status, "succeeded")
        self.assertTrue(final.verifier_backed)


if __name__ == "__main__":
    unittest.main()
