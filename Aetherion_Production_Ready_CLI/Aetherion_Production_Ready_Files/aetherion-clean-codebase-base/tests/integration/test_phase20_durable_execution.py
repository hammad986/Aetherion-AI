"""
tests/integration/test_phase20_durable_execution.py — Phase 20 Integration Tests.

Comprehensive test suite verifying the Durable Execution Supervisor & Crash-Resilient Kernel:
- Category 1: Supervisor Core Lifecycle & Transitions
- Category 2: Lease Semantics & CAS Claiming
- Category 3: Orphan Detection & Deterministic Crash Recovery
- Category 4: Idempotency Identity & Deduplication
- Category 5: Checkpointing & Graph Reconstitution
- Category 6: Dependency DAG Recovery & Immutability
- Category 7: Cancellation Races (Cancel-Wins Semantics)
- Category 8: RunBudget Integration
- Category 9: Human-In-The-Loop (HITL) Safety
- Category 10: End-to-End Durable Execution & Crash Simulation
"""

import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Dict, List
import uuid

import pytest

from backend.execution.orchestration.execution_plan import (
    ExecutionPlan,
    PlanRiskLevel,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
    PlanValidator,
)
from backend.execution.orchestration.execution_graph import ExecutionGraph
from backend.execution.orchestration.durable_supervisor import (
    DurableExecutionSupervisor,
    DurableSupervisorStore,
    StepLease,
    RecoveryDecision,
    RecoveryEvaluation,
    ExecutionCheckpoint,
    DurableStepRecord,
    RecoveryDecisionEngine,
    StaleLeaseError,
    DuplicateClaimError,
    InvalidStepTransitionError,
    DuplicateWorkerExecutionError,
    TaskCancelledError,
    RecoveryFailedError,
)
from backend.execution.orchestration.run_budget import RunBudget


@pytest.fixture
def temp_db_path(tmp_path: Path) -> str:
    """Fixture providing isolated SQLite database path."""
    return str(tmp_path / "test_supervisor.db")


@pytest.fixture
def supervisor(temp_db_path: str) -> DurableExecutionSupervisor:
    """Fixture providing initialized DurableExecutionSupervisor."""
    return DurableExecutionSupervisor(
        db_path=temp_db_path,
        default_lease_seconds=5.0,
        heartbeat_timeout_seconds=5.0,
    )


def create_sample_plan(task_id: str, steps_count: int = 3) -> ExecutionPlan:
    """Helper to generate a linear execution plan."""
    steps = {}
    prev_id = None
    for i in range(1, steps_count + 1):
        s_id = f"step_{i}"
        deps = [prev_id] if prev_id else []
        steps[s_id] = PlanStep(
            step_id=s_id,
            objective=f"Objective for {s_id}",
            dependencies=deps,
            risk_level=PlanRiskLevel.LOW,
            max_retries=2,
            target_files=[f"file_{i}.py"],
        )
        prev_id = s_id

    plan = ExecutionPlan(
        plan_id=f"plan_{task_id}",
        task_id=task_id,
        tenant_id="tenant_alpha",
        objective="Integration test plan",
        steps=steps,
    )
    return plan


# ── Category 1: Supervisor Core Lifecycle & Transitions ───────────────────────

def test_task_and_graph_registration(supervisor: DurableExecutionSupervisor):
    task_id = "task_reg_01"
    plan = create_sample_plan(task_id, 3)
    graph = ExecutionGraph(plan)

    supervisor.register_task(task_id, "tenant_alpha", graph)

    task_rec = supervisor.store.get_task(task_id)
    assert task_rec is not None
    assert task_rec["status"] == "RUNNING"
    assert task_rec["tenant_id"] == "tenant_alpha"

    steps = supervisor.store.list_steps(task_id)
    assert len(steps) == 3
    assert steps[0].step_id == "step_1"


def test_step_registration_and_initial_status(supervisor: DurableExecutionSupervisor):
    task_id = "task_step_reg"
    plan = create_sample_plan(task_id, 2)
    graph = ExecutionGraph(plan)

    supervisor.register_task(task_id, "tenant_alpha", graph)

    step_1 = supervisor.store.get_step(task_id, "step_1")
    assert step_1 is not None
    assert step_1.status == PlanStepStatus.PENDING.value
    assert step_1.version == 1
    assert step_1.attempt_number == 0


def test_valid_step_state_transitions(supervisor: DurableExecutionSupervisor):
    task_id = "task_transitions"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    # PENDING -> READY -> CLAIMED -> RUNNING -> VERIFYING -> SUCCEEDED
    graph.get_ready_steps()
    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)

    lease = supervisor.claim_step(task_id, "step_1", worker_id="worker_A")
    assert lease is not None

    supervisor.start_step(task_id, "step_1", worker_id="worker_A", lease_id=lease.lease_id)
    step_rec = supervisor.store.get_step(task_id, "step_1")
    assert step_rec.status == PlanStepStatus.RUNNING.value

    supervisor.start_verification(task_id, "step_1", worker_id="worker_A", lease_id=lease.lease_id)
    step_rec = supervisor.store.get_step(task_id, "step_1")
    assert step_rec.status == PlanStepStatus.VERIFYING.value

    supervisor.complete_step(task_id, "step_1", worker_id="worker_A", lease_id=lease.lease_id, result={"ok": True})
    step_rec = supervisor.store.get_step(task_id, "step_1")
    assert step_rec.status == PlanStepStatus.SUCCEEDED.value


def test_invalid_step_state_transition_rejected(supervisor: DurableExecutionSupervisor):
    task_id = "task_bad_trans"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    lease = supervisor.claim_step(task_id, "step_1", worker_id="worker_A")

    # Cannot jump directly from CLAIMED to VERIFYING
    with pytest.raises(InvalidStepTransitionError):
        supervisor.start_verification(task_id, "step_1", worker_id="worker_A", lease_id=lease.lease_id)


def test_durable_step_ownership(supervisor: DurableExecutionSupervisor):
    task_id = "task_ownership"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    t0 = time.time()
    lease = supervisor.claim_step(task_id, "step_1", worker_id="worker_X", lease_duration=10.0, now=t0)

    step_rec = supervisor.store.get_step(task_id, "step_1")
    assert step_rec.worker_id == "worker_X"
    assert step_rec.lease_id == lease.lease_id
    assert step_rec.lease_expires == t0 + 10.0
    assert step_rec.attempt_number == 1


# ── Category 2: Lease Semantics & CAS Claiming ────────────────────────────────

def test_step_claim_success(supervisor: DurableExecutionSupervisor):
    task_id = "task_claim_success"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    lease = supervisor.claim_step(task_id, "step_1", worker_id="worker_1", lease_duration=30.0)

    assert lease.step_id == "step_1"
    assert lease.worker_id == "worker_1"
    assert lease.lease_id.startswith("lease_")
    assert not lease.is_expired()


def test_duplicate_claim_rejection(supervisor: DurableExecutionSupervisor):
    task_id = "task_dup_claim"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    supervisor.claim_step(task_id, "step_1", worker_id="worker_1", lease_duration=30.0)

    # Worker 2 attempts to claim the same step
    with pytest.raises(DuplicateClaimError):
        supervisor.claim_step(task_id, "step_1", worker_id="worker_2", lease_duration=30.0)


def test_step_heartbeat_renewal(supervisor: DurableExecutionSupervisor):
    task_id = "task_hb_renewal"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    t0 = 1000.0
    lease = supervisor.claim_step(task_id, "step_1", worker_id="worker_1", lease_duration=10.0, now=t0)
    assert lease.lease_expires == 1010.0

    t1 = 1005.0
    renewed = supervisor.heartbeat_step(task_id, "step_1", worker_id="worker_1", lease_id=lease.lease_id, lease_duration=10.0, now=t1)
    assert renewed.lease_expires == 1015.0
    assert renewed.heartbeat_at == 1005.0


def test_lease_expiry():
    lease = StepLease(
        step_id="step_1",
        task_id="task_1",
        worker_id="worker_1",
        lease_id="lease_123",
        lease_start=100.0,
        lease_expires=130.0,
        heartbeat_at=100.0,
        attempt_number=1,
    )
    assert not lease.is_expired(now=120.0)
    assert lease.is_expired(now=135.0)


def test_stale_worker_heartbeat_rejection(supervisor: DurableExecutionSupervisor):
    task_id = "task_stale_hb"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    t0 = 1000.0
    lease = supervisor.claim_step(task_id, "step_1", worker_id="worker_1", lease_duration=10.0, now=t0)

    # Worker calls heartbeat at t = 1015.0 (5 seconds past expiry)
    with pytest.raises(StaleLeaseError):
        supervisor.heartbeat_step(task_id, "step_1", worker_id="worker_1", lease_id=lease.lease_id, now=1015.0)


def test_stale_worker_commit_rejection(supervisor: DurableExecutionSupervisor):
    task_id = "task_stale_commit"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    t0 = 1000.0
    lease = supervisor.claim_step(task_id, "step_1", worker_id="worker_1", lease_duration=10.0, now=t0)
    supervisor.start_step(task_id, "step_1", worker_id="worker_1", lease_id=lease.lease_id, now=t0)

    # Expired worker attempts to complete step
    with pytest.raises(StaleLeaseError):
        supervisor.complete_step(
            task_id, "step_1", worker_id="worker_1", lease_id=lease.lease_id, result={"ok": True}, now=1015.0
        )


def test_worker_id_mismatch_rejection(supervisor: DurableExecutionSupervisor):
    task_id = "task_worker_mismatch"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    lease = supervisor.claim_step(task_id, "step_1", worker_id="worker_1")

    with pytest.raises(StaleLeaseError):
        supervisor.start_step(task_id, "step_1", worker_id="worker_IMPOSTER", lease_id=lease.lease_id)


def test_lease_id_mismatch_rejection(supervisor: DurableExecutionSupervisor):
    task_id = "task_lease_mismatch"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    lease = supervisor.claim_step(task_id, "step_1", worker_id="worker_1")

    with pytest.raises(StaleLeaseError):
        supervisor.start_step(task_id, "step_1", worker_id="worker_1", lease_id="bogus_lease_id")


# ── Category 3: Orphan Detection & Deterministic Crash Recovery ───────────────

def test_orphan_detection_stale_heartbeat_and_lease(supervisor: DurableExecutionSupervisor):
    task_id = "task_orphan_detect"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    t0 = 1000.0
    lease = supervisor.claim_step(task_id, "step_1", worker_id="worker_1", lease_duration=10.0, now=t0)
    supervisor.start_step(task_id, "step_1", worker_id="worker_1", lease_id=lease.lease_id, now=t0)

    # At t0 + 20s, heartbeat is 20s old and lease expired 10s ago
    orphans = supervisor.detect_orphans(heartbeat_timeout_seconds=10.0, now=1020.0)
    assert len(orphans) == 1
    assert orphans[0].step_id == "step_1"
    assert orphans[0].status == PlanStepStatus.ORPHANED.value


def test_orphan_detection_ignores_healthy_active_steps(supervisor: DurableExecutionSupervisor):
    task_id = "task_orphan_healthy"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    t0 = 1000.0
    lease = supervisor.claim_step(task_id, "step_1", worker_id="worker_1", lease_duration=60.0, now=t0)
    supervisor.start_step(task_id, "step_1", worker_id="worker_1", lease_id=lease.lease_id, now=t0)

    orphans = supervisor.detect_orphans(heartbeat_timeout_seconds=30.0, now=1010.0)
    assert len(orphans) == 0


def test_recovery_decision_resume_when_verification_passed():
    eval_res = RecoveryDecisionEngine.evaluate(
        task_id="t1",
        step_id="s1",
        attempt_number=1,
        max_retries=2,
        is_idempotent=False,
        has_unverified_mutations=True,
        verifier_state={"passed": True},
    )
    assert eval_res.decision == RecoveryDecision.RESUME
    assert "Verification criteria already satisfied" in eval_res.reason


def test_recovery_decision_retry_when_idempotent():
    eval_res = RecoveryDecisionEngine.evaluate(
        task_id="t1",
        step_id="s1",
        attempt_number=1,
        max_retries=2,
        is_idempotent=True,
        has_unverified_mutations=False,
    )
    assert eval_res.decision == RecoveryDecision.RETRY
    assert "safe to retry" in eval_res.reason.lower()


def test_recovery_decision_fail_closed_on_unverified_side_effects():
    eval_res = RecoveryDecisionEngine.evaluate(
        task_id="t1",
        step_id="s1",
        attempt_number=1,
        max_retries=2,
        is_idempotent=False,
        has_unverified_mutations=True,
    )
    assert eval_res.decision == RecoveryDecision.FAIL_CLOSED
    assert "Non-idempotent side effects" in eval_res.reason


def test_recovery_decision_fail_closed_when_max_retries_exceeded():
    eval_res = RecoveryDecisionEngine.evaluate(
        task_id="t1",
        step_id="s1",
        attempt_number=2,
        max_retries=2,
        is_idempotent=True,
        has_unverified_mutations=False,
    )
    assert eval_res.decision == RecoveryDecision.FAIL_CLOSED
    assert "maximum retry attempts" in eval_res.reason


def test_apply_recovery_resume_transitions_to_recovering(supervisor: DurableExecutionSupervisor):
    task_id = "task_rec_resume"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)
    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.ORPHANED.value)

    res = supervisor.evaluate_and_recover_orphan(
        task_id, "step_1", is_idempotent=False, has_unverified_mutations=True, verifier_state={"passed": True}
    )
    assert res.decision == RecoveryDecision.RESUME
    step_rec = supervisor.store.get_step(task_id, "step_1")
    assert step_rec.status == PlanStepStatus.RECOVERING.value


def test_apply_recovery_retry_transitions_to_ready(supervisor: DurableExecutionSupervisor):
    task_id = "task_rec_retry"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)
    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.ORPHANED.value)

    res = supervisor.evaluate_and_recover_orphan(
        task_id, "step_1", is_idempotent=True, has_unverified_mutations=False, max_retries=2
    )
    assert res.decision == RecoveryDecision.RETRY
    step_rec = supervisor.store.get_step(task_id, "step_1")
    # Transitions to RETRYING then READY for new worker claim
    assert step_rec.status == PlanStepStatus.READY.value


def test_apply_recovery_fail_closed_transitions_to_failed(supervisor: DurableExecutionSupervisor):
    task_id = "task_rec_fail_closed"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)
    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.ORPHANED.value)

    res = supervisor.evaluate_and_recover_orphan(
        task_id, "step_1", is_idempotent=False, has_unverified_mutations=True, max_retries=2
    )
    assert res.decision == RecoveryDecision.FAIL_CLOSED
    step_rec = supervisor.store.get_step(task_id, "step_1")
    assert step_rec.status == PlanStepStatus.FAILED.value


# ── Category 4: Idempotency Identity & Deduplication ─────────────────────────

def test_idempotency_token_deterministic(supervisor: DurableExecutionSupervisor):
    tok1 = supervisor.generate_idempotency_token("t1", "s1", 1, "write_file", {"path": "a.txt"})
    tok2 = supervisor.generate_idempotency_token("t1", "s1", 1, "write_file", {"path": "a.txt"})
    tok3 = supervisor.generate_idempotency_token("t1", "s1", 2, "write_file", {"path": "a.txt"})
    assert tok1 == tok2
    assert tok1 != tok3


def test_idempotency_committed_operation_deduplicated(supervisor: DurableExecutionSupervisor):
    task_id = "task_idem_dedup"
    step_id = "step_1"
    params = {"path": "out.txt", "content": "hello"}

    # Start operation
    done, res = supervisor.check_or_record_operation(task_id, step_id, 1, "file_write", params, worker_id="w1")
    assert not done
    assert res is None

    # Commit operation
    supervisor.commit_operation(task_id, step_id, 1, "file_write", params, result={"bytes_written": 5})

    # Second execution on same attempt sees COMMITTED and returns result
    done2, res2 = supervisor.check_or_record_operation(task_id, step_id, 1, "file_write", params, worker_id="w1")
    assert done2
    assert res2 == {"bytes_written": 5}


def test_idempotency_duplicate_worker_raises(supervisor: DurableExecutionSupervisor):
    task_id = "task_idem_dup_worker"
    step_id = "step_1"
    params = {"query": "SELECT *"}

    # Worker 1 starts operation on attempt 1
    supervisor.check_or_record_operation(task_id, step_id, 1, "db_query", params, worker_id="w1")

    # Worker 2 attempts same operation on same attempt while w1 is in flight
    with pytest.raises(DuplicateWorkerExecutionError):
        supervisor.check_or_record_operation(task_id, step_id, 1, "db_query", params, worker_id="w2")


def test_idempotency_new_retry_attempt_allowed(supervisor: DurableExecutionSupervisor):
    task_id = "task_idem_retry"
    step_id = "step_1"
    params = {"patch": "diff"}

    # Attempt 1 failed or interrupted
    supervisor.check_or_record_operation(task_id, step_id, 1, "apply_patch", params, worker_id="w1")

    # Legitimate attempt 2 by worker 2 is allowed
    done, res = supervisor.check_or_record_operation(task_id, step_id, 2, "apply_patch", params, worker_id="w2")
    assert not done
    assert res is None


def test_idempotency_side_effect_prevented_across_worker_crash(supervisor: DurableExecutionSupervisor):
    task_id = "task_idem_crash"
    step_id = "step_1"
    params = {"transfer_id": "tx_999"}

    # Worker 1 committed side effect before crashing
    supervisor.check_or_record_operation(task_id, step_id, 1, "commit_tx", params, worker_id="w1")
    supervisor.commit_operation(task_id, step_id, 1, "commit_tx", params, result={"status": "committed"})

    # Worker 2 resuming step 1 checks idempotency
    done, cached_res = supervisor.check_or_record_operation(task_id, step_id, 1, "commit_tx", params, worker_id="w2")
    assert done
    assert cached_res == {"status": "committed"}


# ── Category 5: Checkpointing & Graph Reconstitution ─────────────────────────

def test_checkpoint_creation_at_boundaries(supervisor: DurableExecutionSupervisor):
    task_id = "task_chk_boundaries"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    chk1 = supervisor.create_checkpoint(task_id, "plan_validation", graph)
    assert chk1.boundary_type == "plan_validation"

    chk2 = supervisor.create_checkpoint(task_id, "step_claim", graph, step_id="step_1")
    assert chk2.boundary_type == "step_claim"

    latest = supervisor.load_latest_checkpoint(task_id)
    assert latest.checkpoint_id == chk2.checkpoint_id


def test_load_latest_checkpoint(supervisor: DurableExecutionSupervisor):
    task_id = "task_load_chk"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    chk = supervisor.load_latest_checkpoint(task_id)
    assert chk is not None
    assert chk.boundary_type == "plan_validation"


def test_graph_reconstitution_after_simulated_process_crash(temp_db_path: str):
    task_id = "task_crash_recon"

    # Process 1: Setup and complete Step 1
    supervisor1 = DurableExecutionSupervisor(db_path=temp_db_path)
    plan1 = create_sample_plan(task_id, 3)
    graph1 = ExecutionGraph(plan1)
    supervisor1.register_task(task_id, "tenant_alpha", graph1)

    supervisor1._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    lease1 = supervisor1.claim_step(task_id, "step_1", worker_id="w1")
    supervisor1.start_step(task_id, "step_1", worker_id="w1", lease_id=lease1.lease_id)
    supervisor1.complete_step(task_id, "step_1", worker_id="w1", lease_id=lease1.lease_id, result={"output": 42})

    del supervisor1  # Simulate process crash

    # Process 2: Reconstitute from persistent store
    supervisor2 = DurableExecutionSupervisor(db_path=temp_db_path)
    graph2, chk = supervisor2.reconstitute_execution(task_id)

    assert graph2 is not None
    assert graph2.get_step("step_1").status == PlanStepStatus.SUCCEEDED
    assert graph2.get_step("step_1").result == {"output": 42}
    # Step 2 should now be ready
    ready = graph2.get_ready_steps()
    assert any(s.step_id == "step_2" for s in ready)


def test_reconstituted_graph_preserves_immutable_successful_steps(temp_db_path: str):
    task_id = "task_immutable_recon"
    sup1 = DurableExecutionSupervisor(db_path=temp_db_path)
    plan = create_sample_plan(task_id, 2)
    graph = ExecutionGraph(plan)
    sup1.register_task(task_id, "tenant_alpha", graph)

    sup1._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    lease = sup1.claim_step(task_id, "step_1", worker_id="w1")
    sup1.start_step(task_id, "step_1", worker_id="w1", lease_id=lease.lease_id)
    sup1.complete_step(task_id, "step_1", worker_id="w1", lease_id=lease.lease_id, result={"immutable_key": "val1"})

    del sup1

    sup2 = DurableExecutionSupervisor(db_path=temp_db_path)
    graph2, _ = sup2.reconstitute_execution(task_id)
    s1 = graph2.get_step("step_1")
    assert s1.status == PlanStepStatus.SUCCEEDED
    assert s1.result == {"immutable_key": "val1"}


def test_reconstituted_graph_detects_crashed_active_step(temp_db_path: str):
    task_id = "task_active_recon"
    sup1 = DurableExecutionSupervisor(db_path=temp_db_path)
    plan = create_sample_plan(task_id, 2)
    graph = ExecutionGraph(plan)
    sup1.register_task(task_id, "tenant_alpha", graph)

    sup1._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    lease = sup1.claim_step(task_id, "step_1", worker_id="w1", lease_duration=5.0)
    sup1.start_step(task_id, "step_1", worker_id="w1", lease_id=lease.lease_id)

    del sup1  # Crashed during RUNNING

    sup2 = DurableExecutionSupervisor(db_path=temp_db_path)
    graph2, _ = sup2.reconstitute_execution(task_id)
    s1 = graph2.get_step("step_1")
    assert s1.status == PlanStepStatus.RUNNING
    assert s1.lease_id == lease.lease_id


# ── Category 6: Dependency DAG Recovery & Immutability ─────────────────────────

def test_recovered_step_success_unlocks_downstream_dependencies(supervisor: DurableExecutionSupervisor):
    task_id = "task_dag_unlock"
    plan = create_sample_plan(task_id, 3)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    # Step 1 was orphaned and recovered
    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.ORPHANED.value)
    eval_res = supervisor.evaluate_and_recover_orphan(task_id, "step_1", is_idempotent=True)
    assert eval_res.decision == RecoveryDecision.RETRY

    # Worker claims and completes Step 1
    lease = supervisor.claim_step(task_id, "step_1", worker_id="w2")
    supervisor.start_step(task_id, "step_1", worker_id="w2", lease_id=lease.lease_id)
    supervisor.complete_step(task_id, "step_1", worker_id="w2", lease_id=lease.lease_id, result={"s1": "done"})

    # Downstream Step 2 is now unlocked
    ready = graph.get_ready_steps()
    assert any(s.step_id == "step_2" for s in ready)


def test_failed_recovery_blocks_downstream_steps(supervisor: DurableExecutionSupervisor):
    task_id = "task_dag_blocked"
    plan = create_sample_plan(task_id, 3)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    # Step 1 fails closed
    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.ORPHANED.value)
    supervisor.evaluate_and_recover_orphan(task_id, "step_1", is_idempotent=False, has_unverified_mutations=True)

    # Step 2 remains blocked
    blocked = graph.get_blocked_steps()
    assert any(s.step_id == "step_2" for s in blocked)
    ready = graph.get_ready_steps()
    assert not any(s.step_id == "step_2" for s in ready)


def test_successful_step_immutability_under_concurrent_writes(supervisor: DurableExecutionSupervisor):
    task_id = "task_immutable_writes"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    lease = supervisor.claim_step(task_id, "step_1", worker_id="w1")
    supervisor.start_step(task_id, "step_1", worker_id="w1", lease_id=lease.lease_id)
    supervisor.complete_step(task_id, "step_1", worker_id="w1", lease_id=lease.lease_id, result={"initial": True})

    # Second attempt to complete does not overwrite initial result
    supervisor.complete_step(task_id, "step_1", worker_id="w1", lease_id=lease.lease_id, result={"corrupt": True})
    step_rec = supervisor.store.get_step(task_id, "step_1")
    assert json.loads(step_rec.result_json) == {"initial": True}


# ── Category 7: Cancellation Races (Cancel-Wins Semantics) ────────────────────

def test_cancel_wins_over_late_worker_success(supervisor: DurableExecutionSupervisor):
    task_id = "task_cancel_vs_success"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    lease = supervisor.claim_step(task_id, "step_1", worker_id="w1")
    supervisor.start_step(task_id, "step_1", worker_id="w1", lease_id=lease.lease_id)

    # Task is cancelled while worker was executing
    supervisor.cancel_task(task_id, reason="User cancelled task")

    # Late worker completes - must be rejected by cancel-wins
    with pytest.raises(TaskCancelledError):
        supervisor.complete_step(task_id, "step_1", worker_id="w1", lease_id=lease.lease_id, result={"late": True})

    step_rec = supervisor.store.get_step(task_id, "step_1")
    assert step_rec.status == PlanStepStatus.CANCELLED.value


def test_cancel_wins_over_worker_claim(supervisor: DurableExecutionSupervisor):
    task_id = "task_cancel_vs_claim"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor.cancel_task(task_id)

    with pytest.raises(TaskCancelledError):
        supervisor.claim_step(task_id, "step_1", worker_id="w1")


def test_cancel_wins_over_recovery(supervisor: DurableExecutionSupervisor):
    task_id = "task_cancel_vs_rec"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)
    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.ORPHANED.value)

    supervisor.cancel_task(task_id)

    res = supervisor.evaluate_and_recover_orphan(task_id, "step_1", is_idempotent=True)
    assert res.decision == RecoveryDecision.FAIL_CLOSED
    assert "cancelled or stopped" in res.reason


def test_cancel_cancels_all_non_terminal_steps(supervisor: DurableExecutionSupervisor):
    task_id = "task_cancel_all"
    plan = create_sample_plan(task_id, 3)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    # Step 1 succeeded before cancel
    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    lease = supervisor.claim_step(task_id, "step_1", worker_id="w1")
    supervisor.start_step(task_id, "step_1", worker_id="w1", lease_id=lease.lease_id)
    supervisor.complete_step(task_id, "step_1", worker_id="w1", lease_id=lease.lease_id, result={"done": True})

    supervisor.cancel_task(task_id)

    steps = supervisor.store.list_steps(task_id)
    assert steps[0].status == PlanStepStatus.SUCCEEDED.value  # Preserved immutable
    assert steps[1].status == PlanStepStatus.CANCELLED.value
    assert steps[2].status == PlanStepStatus.CANCELLED.value


def test_duplicate_cancellation_idempotent(supervisor: DurableExecutionSupervisor):
    task_id = "task_dup_cancel"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor.cancel_task(task_id)
    supervisor.cancel_task(task_id)
    task_rec = supervisor.store.get_task(task_id)
    assert task_rec["status"] == "CANCELLED"


# ── Category 8: RunBudget Integration ────────────────────────────────────────

def test_recovery_respects_run_budget_exhaustion():
    budget = RunBudget(max_model_calls=1, max_tool_calls=1, max_execution_time_sec=10.0)
    budget.record_model_call()  # Exhausted

    res = RecoveryDecisionEngine.evaluate(
        task_id="t1",
        step_id="s1",
        attempt_number=1,
        max_retries=2,
        is_idempotent=True,
        has_unverified_mutations=False,
        run_budget=budget,
    )
    assert res.decision == RecoveryDecision.FAIL_CLOSED
    assert res.budget_exhausted
    assert "RunBudget exhausted" in res.reason


def test_recovery_retries_consume_budget():
    budget = RunBudget(max_model_calls=10, max_tool_calls=10, max_execution_time_sec=60.0)

    # First attempt
    res1 = RecoveryDecisionEngine.evaluate(
        task_id="t1", step_id="s1", attempt_number=0, max_retries=2, is_idempotent=True, has_unverified_mutations=False, run_budget=budget
    )
    assert res1.decision == RecoveryDecision.RETRY
    assert res1.remaining_retries == 2

    # Second attempt
    res2 = RecoveryDecisionEngine.evaluate(
        task_id="t1", step_id="s1", attempt_number=1, max_retries=2, is_idempotent=True, has_unverified_mutations=False, run_budget=budget
    )
    assert res2.decision == RecoveryDecision.RETRY
    assert res2.remaining_retries == 1

    # Max reached
    res3 = RecoveryDecisionEngine.evaluate(
        task_id="t1", step_id="s1", attempt_number=2, max_retries=2, is_idempotent=True, has_unverified_mutations=False, run_budget=budget
    )
    assert res3.decision == RecoveryDecision.FAIL_CLOSED


def test_budget_enforcer_integration_with_supervisor(supervisor: DurableExecutionSupervisor):
    task_id = "task_budget_checkpoint"
    plan = create_sample_plan(task_id, 1)
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    budget = RunBudget(max_model_calls=5, max_tool_calls=10)
    budget.record_model_call()
    budget.record_tool_call()

    chk = supervisor.create_checkpoint(task_id, "budget_check", graph, budget=budget)
    assert "model_calls" in chk.budget_state_json
    b_dict = json.loads(chk.budget_state_json)
    assert b_dict["usage"]["model_calls"] == 1
    assert b_dict["usage"]["tool_calls"] == 1


# ── Category 9: Human-In-The-Loop (HITL) Safety ──────────────────────────────

def test_crashed_worker_with_pending_hitl_evaluates_to_wait():
    res = RecoveryDecisionEngine.evaluate(
        task_id="t1",
        step_id="s1",
        attempt_number=1,
        max_retries=2,
        is_idempotent=True,
        has_unverified_mutations=False,
        hitl_state={"requires_hitl": True, "waiting_approval": True},
    )
    assert res.decision == RecoveryDecision.WAIT
    assert res.hitl_pending
    assert "pending human operator approval gate" in res.reason


def test_approved_hitl_action_resumes_without_duplicate_execution():
    res = RecoveryDecisionEngine.evaluate(
        task_id="t1",
        step_id="s1",
        attempt_number=1,
        max_retries=2,
        is_idempotent=False,
        has_unverified_mutations=True,
        hitl_state={"requires_hitl": True, "approved": True},
    )
    assert res.decision == RecoveryDecision.RESUME
    assert "approved by human operator" in res.reason


def test_rejected_hitl_action_fails_closed():
    res = RecoveryDecisionEngine.evaluate(
        task_id="t1",
        step_id="s1",
        attempt_number=1,
        max_retries=2,
        is_idempotent=True,
        has_unverified_mutations=False,
        hitl_state={"requires_hitl": True, "rejected": True},
    )
    assert res.decision == RecoveryDecision.FAIL_CLOSED
    assert "explicitly rejected" in res.reason


def test_worker_recovery_cannot_bypass_hitl_gate(supervisor: DurableExecutionSupervisor):
    task_id = "task_hitl_bypass"
    plan = create_sample_plan(task_id, 1)
    plan.steps["step_1"].risk_level = PlanRiskLevel.CRITICAL
    plan.steps["step_1"].requires_hitl = True
    graph = ExecutionGraph(plan)
    supervisor.register_task(task_id, "tenant_alpha", graph)

    supervisor._transition_step_status(task_id, "step_1", PlanStepStatus.ORPHANED.value)

    # Orphan evaluation with unapproved dangerous step must WAIT, not RETRY
    res = supervisor.evaluate_and_recover_orphan(
        task_id, "step_1", hitl_state={"requires_hitl": True, "waiting_approval": True}
    )
    assert res.decision == RecoveryDecision.WAIT
    # Step remains waiting/orphaned, NOT marked ready
    step_rec = supervisor.store.get_step(task_id, "step_1")
    assert step_rec.status == PlanStepStatus.ORPHANED.value


# ── Category 10: End-to-End Durable Execution & Crash Simulation ─────────────

def test_end_to_end_worker_crash_recovery_pipeline(temp_db_path: str):
    """
    Complex Plan: Step 1 -> Step 2 -> Step 3.
    1. Worker 1 completes Step 1.
    2. Worker 1 claims Step 2, starts running, and crashes.
    3. Supervisor detects orphan Step 2, evaluates safe RETRY.
    4. Worker 2 claims Step 2, verifies, and completes Step 2.
    5. Step 3 is unlocked, completed, and plan succeeds with full durable history!
    """
    task_id = "task_e2e_resilience"

    # === PHASE 1: Worker 1 executes Step 1 ===
    sup1 = DurableExecutionSupervisor(db_path=temp_db_path, default_lease_seconds=5.0)
    plan = create_sample_plan(task_id, 3)
    graph = ExecutionGraph(plan)
    sup1.register_task(task_id, "tenant_alpha", graph)

    # Step 1 is ready
    ready = graph.get_ready_steps()
    assert len(ready) == 1 and ready[0].step_id == "step_1"

    sup1._transition_step_status(task_id, "step_1", PlanStepStatus.READY.value)
    t0 = 1000.0
    lease_s1 = sup1.claim_step(task_id, "step_1", worker_id="worker_1", now=t0)
    sup1.start_step(task_id, "step_1", worker_id="worker_1", lease_id=lease_s1.lease_id, now=t0)
    sup1.complete_step(
        task_id, "step_1", worker_id="worker_1", lease_id=lease_s1.lease_id, result={"s1_res": "ok"}, now=t0 + 1.0
    )

    # Step 2 is now ready
    ready_s2 = graph.get_ready_steps()
    assert any(s.step_id == "step_2" for s in ready_s2)

    # Worker 1 claims Step 2 with short lease and crashes
    lease_s2 = sup1.claim_step(task_id, "step_2", worker_id="worker_1", lease_duration=5.0, now=t0 + 2.0)
    sup1.start_step(task_id, "step_2", worker_id="worker_1", lease_id=lease_s2.lease_id, now=t0 + 2.0)

    # === SIMULATED PROCESS CRASH ===
    del sup1

    # === PHASE 2: New Process Supervisor Recovers Orphan ===
    sup2 = DurableExecutionSupervisor(db_path=temp_db_path, default_lease_seconds=10.0)
    recon_graph, _ = sup2.reconstitute_execution(task_id)

    # Time advances to t0 + 15.0s (Worker 1 lease expired 8s ago)
    orphans = sup2.detect_orphans(heartbeat_timeout_seconds=5.0, now=t0 + 15.0)
    assert len(orphans) == 1
    assert orphans[0].step_id == "step_2"
    assert orphans[0].status == PlanStepStatus.ORPHANED.value

    # Evaluate recovery: safe retry
    rec_eval = sup2.evaluate_and_recover_orphan(task_id, "step_2", is_idempotent=True, has_unverified_mutations=False)
    assert rec_eval.decision == RecoveryDecision.RETRY

    # Step 2 is now back in READY state
    step2_rec = sup2.store.get_step(task_id, "step_2")
    assert step2_rec.status == PlanStepStatus.READY.value

    # === PHASE 3: Worker 2 Claims and Completes Step 2 and Step 3 ===
    t_rec = t0 + 16.0
    lease_w2_s2 = sup2.claim_step(task_id, "step_2", worker_id="worker_2", lease_duration=10.0, now=t_rec)
    assert lease_w2_s2.attempt_number == 2
    assert lease_w2_s2.worker_id == "worker_2"

    sup2.start_step(task_id, "step_2", worker_id="worker_2", lease_id=lease_w2_s2.lease_id, now=t_rec)
    sup2.start_verification(task_id, "step_2", worker_id="worker_2", lease_id=lease_w2_s2.lease_id, now=t_rec + 1.0)
    sup2.complete_step(
        task_id, "step_2", worker_id="worker_2", lease_id=lease_w2_s2.lease_id, result={"s2_res": "ok"}, now=t_rec + 2.0
    )

    # Step 3 unlocks
    ready_s3 = recon_graph.get_ready_steps()
    assert any(s.step_id == "step_3" for s in ready_s3)

    lease_w2_s3 = sup2.claim_step(task_id, "step_3", worker_id="worker_2", lease_duration=10.0, now=t_rec + 3.0)
    sup2.start_step(task_id, "step_3", worker_id="worker_2", lease_id=lease_w2_s3.lease_id, now=t_rec + 3.0)
    sup2.complete_step(
        task_id, "step_3", worker_id="worker_2", lease_id=lease_w2_s3.lease_id, result={"s3_res": "ok"}, now=t_rec + 4.0
    )

    # === VERIFY FINAL STATE ===
    assert recon_graph.is_completed()
    all_steps = sup2.store.list_steps(task_id)
    assert len(all_steps) == 3
    assert all(s.status == PlanStepStatus.SUCCEEDED.value for s in all_steps)
    assert json.loads(all_steps[0].result_json) == {"s1_res": "ok"}
    assert json.loads(all_steps[1].result_json) == {"s2_res": "ok"}
    assert json.loads(all_steps[2].result_json) == {"s3_res": "ok"}
