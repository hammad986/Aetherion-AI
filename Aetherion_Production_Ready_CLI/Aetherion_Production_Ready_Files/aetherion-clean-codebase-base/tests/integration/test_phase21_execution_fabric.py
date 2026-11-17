"""
tests/integration/test_phase21_execution_fabric.py — Phase 21 Integration Tests.

Comprehensive test suite verifying the Execution Fabric & Worker Control Plane:
- Category 1: Durable Task Queue Operations
- Category 2: Worker Control Plane & Capacity Management
- Category 3: Worker Draining & Graceful Lifecycle
- Category 4: Worker Unhealthy Detection & Stale Worker Protection
- Category 5: Scheduler Multi-Tenant Fairness & Concurrency Quotas
- Category 6: Scheduler Dependency DAG & Budget Awareness
- Category 7: Atomic Claiming & Phase 20 CAS Lease Integration
- Category 8: Side-Effect Retry Safety Policy & Categorization
- Category 9: Crash, Lease Expiry & Multi-Worker Orphan Recovery
- Category 10: End-to-End Execution Fabric Pipeline
"""

import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Dict, List
import uuid

import pytest

from backend.execution.fabric.models import (
    CapabilityMismatchError,
    FabricError,
    QueueEmptyError,
    QueueItem,
    QueueItemStatus,
    QuotaExceededError,
    RetrySafetyClass,
    SchedulerError,
    SchedulingDecision,
    SchedulingVerdict,
    StaleWorkerError,
    WorkerCapacityExceededError,
    WorkerNotFoundError,
    WorkerRecord,
    WorkerStatus,
)
from backend.execution.fabric.store import FabricStore
from backend.execution.fabric.queue import DurableTaskQueue
from backend.execution.fabric.worker_registry import WorkerRegistry
from backend.execution.fabric.scheduler import ExecutionScheduler, SchedulerQuotaPolicy
from backend.execution.fabric.retry_policy import (
    evaluate_retry_safety,
    map_safety_to_recovery_decision,
    SAFE_READ_TOOLS,
    DANGEROUS_SIDE_EFFECT_TOOLS,
)
from backend.execution.fabric.service import ExecutionFabricService
from backend.execution.orchestration.execution_plan import (
    ExecutionPlan,
    PlanRiskLevel,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
)
from backend.execution.orchestration.execution_graph import ExecutionGraph
from backend.execution.orchestration.durable_supervisor import (
    DurableExecutionSupervisor,
    DurableSupervisorStore,
    RecoveryDecision,
    StepLease,
)


@pytest.fixture
def temp_fabric_db(tmp_path: Path) -> str:
    """Fixture providing isolated SQLite database path for FabricStore."""
    return str(tmp_path / "test_fabric.db")


@pytest.fixture
def temp_supervisor_db(tmp_path: Path) -> str:
    """Fixture providing isolated SQLite database path for DurableSupervisorStore."""
    return str(tmp_path / "test_supervisor.db")


@pytest.fixture
def fabric_service(temp_fabric_db: str, temp_supervisor_db: str) -> ExecutionFabricService:
    """Fixture providing integrated ExecutionFabricService with Phase 20 supervisor."""
    store = FabricStore(db_path=temp_fabric_db)
    sup_store = DurableSupervisorStore(db_path=temp_supervisor_db)
    supervisor = DurableExecutionSupervisor(store=sup_store)
    return ExecutionFabricService(
        store=store,
        supervisor=supervisor,
        quota_policy=SchedulerQuotaPolicy(
            max_concurrency_per_tenant=5,
            max_global_concurrency=20,
            max_concurrency_per_task=2,
        ),
        heartbeat_timeout_sec=5.0,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Category 1: Durable Task Queue Operations
# ═════════════════════════════════════════════════════════════════════════════

class TestDurableTaskQueue:

    def test_enqueue_and_priority_ordering(self, temp_fabric_db: str) -> None:
        """Verify queue orders items by priority descending then enqueued_at ascending."""
        store = FabricStore(db_path=temp_fabric_db)
        queue = DurableTaskQueue(store=store)

        q1 = queue.enqueue(task_id="t1", tenant_id="tenant_a", priority=30, enqueued_at=100.0)
        q2 = queue.enqueue(task_id="t1", tenant_id="tenant_a", priority=80, enqueued_at=105.0)
        q3 = queue.enqueue(task_id="t1", tenant_id="tenant_a", priority=80, enqueued_at=102.0)
        q4 = queue.enqueue(task_id="t1", tenant_id="tenant_a", priority=50, enqueued_at=101.0)

        runnable = queue.peek_runnable(limit=10)
        assert len(runnable) == 4
        # Priority 80 with earlier timestamp comes first
        assert runnable[0].item_id == q3.item_id
        assert runnable[1].item_id == q2.item_id
        assert runnable[2].item_id == q4.item_id
        assert runnable[3].item_id == q1.item_id

    def test_queue_persistence_across_reloads(self, temp_fabric_db: str) -> None:
        """Verify queue state persists across store instance reloads."""
        store1 = FabricStore(db_path=temp_fabric_db)
        queue1 = DurableTaskQueue(store=store1)
        item = queue1.enqueue(task_id="t_persist", tenant_id="tenant_x", priority=90, payload={"step": 1})

        # Reload store from same SQLite database file
        store2 = FabricStore(db_path=temp_fabric_db)
        queue2 = DurableTaskQueue(store=store2)
        loaded = queue2.get_item(item.item_id)
        assert loaded is not None
        assert loaded.task_id == "t_persist"
        assert loaded.tenant_id == "tenant_x"
        assert loaded.priority == 90
        assert loaded.status == QueueItemStatus.QUEUED
        assert loaded.payload == {"step": 1}

    def test_queue_lifecycle_state_transitions(self, temp_fabric_db: str) -> None:
        """Verify progression: QUEUED -> DISPATCHING -> CLAIMED -> RUNNING -> COMPLETED."""
        store = FabricStore(db_path=temp_fabric_db)
        queue = DurableTaskQueue(store=store)
        item = queue.enqueue(task_id="t_life", tenant_id="tenant_y", priority=50)

        # Dispatching
        d_item = queue.mark_dispatching(item.item_id, worker_id="worker_1")
        assert d_item.status == QueueItemStatus.DISPATCHING
        assert d_item.assigned_worker_id == "worker_1"

        # Claimed with lease
        c_item = queue.mark_claimed(item.item_id, worker_id="worker_1", lease_id="lease_xyz")
        assert c_item.status == QueueItemStatus.CLAIMED
        assert c_item.lease_id == "lease_xyz"

        # Running
        r_item = queue.mark_running(item.item_id)
        assert r_item.status == QueueItemStatus.RUNNING

        # Completed
        comp_item = queue.mark_completed(item.item_id)
        assert comp_item.status == QueueItemStatus.COMPLETED
        assert comp_item.completed_at is not None

    def test_requeue_increments_attempt_and_clears_worker(self, temp_fabric_db: str) -> None:
        """Verify requeue clears worker assignment and increments attempt counter."""
        store = FabricStore(db_path=temp_fabric_db)
        queue = DurableTaskQueue(store=store)
        item = queue.enqueue(task_id="t_req", tenant_id="tenant_z", priority=50)
        queue.mark_dispatching(item.item_id, worker_id="worker_failed")
        queue.mark_claimed(item.item_id, worker_id="worker_failed", lease_id="lease_bad")

        requeued = queue.requeue(item.item_id, reason="Worker crash simulation")
        assert requeued.status == QueueItemStatus.REQUEUED
        assert requeued.attempt == 2
        assert requeued.assigned_worker_id is None
        assert requeued.lease_id is None
        assert "Worker crash simulation" in (requeued.error_message or "")

    def test_deadline_expiration(self, temp_fabric_db: str) -> None:
        """Verify items past deadline are marked EXPIRED."""
        store = FabricStore(db_path=temp_fabric_db)
        queue = DurableTaskQueue(store=store)
        now = 1000.0
        # Expired item
        item_exp = queue.enqueue(task_id="t_exp", tenant_id="tenant_a", deadline=950.0, enqueued_at=900.0)
        # Valid item
        item_val = queue.enqueue(task_id="t_val", tenant_id="tenant_a", deadline=1100.0, enqueued_at=900.0)

        expired = queue.expire_deadlines(now=now)
        assert len(expired) == 1
        assert expired[0].item_id == item_exp.item_id
        assert expired[0].status == QueueItemStatus.EXPIRED

        # Only valid item remains runnable
        runnable = queue.peek_runnable()
        assert len(runnable) == 1
        assert runnable[0].item_id == item_val.item_id


# ═════════════════════════════════════════════════════════════════════════════
# Category 2: Worker Control Plane & Capacity Management
# ═════════════════════════════════════════════════════════════════════════════

class TestWorkerControlPlane:

    def test_worker_registration_and_heartbeat(self, temp_fabric_db: str) -> None:
        """Verify worker registration, capability sets, and heartbeat renewal."""
        store = FabricStore(db_path=temp_fabric_db)
        registry = WorkerRegistry(store=store, heartbeat_timeout_sec=10.0)

        record = registry.register_worker(
            worker_id="worker_node_1",
            max_concurrency=4,
            supported_capabilities=["tool_execution", "sandbox", "python"],
            metadata={"host": "worker-1.internal"},
            now=100.0,
        )
        assert record.worker_id == "worker_node_1"
        assert record.max_concurrency == 4
        assert record.active_concurrency == 0
        assert record.status == WorkerStatus.READY
        assert "python" in record.supported_capabilities

        # Update heartbeat
        updated = registry.heartbeat("worker_node_1", now=105.0)
        assert updated.last_heartbeat == 105.0
        assert updated.is_healthy(timeout_sec=10.0, now=108.0) is True

    def test_capacity_acquisition_and_release(self, temp_fabric_db: str) -> None:
        """Verify capacity slot allocation up to max_concurrency and release."""
        store = FabricStore(db_path=temp_fabric_db)
        registry = WorkerRegistry(store=store)
        registry.register_worker(worker_id="w_cap", max_concurrency=2, now=100.0)

        # Slot 1
        w1 = registry.acquire_capacity("w_cap", lease_id="lease_1", now=100.0)
        assert w1.active_concurrency == 1
        assert w1.status == WorkerStatus.READY
        assert "lease_1" in w1.active_leases

        # Slot 2 (becomes BUSY)
        w2 = registry.acquire_capacity("w_cap", lease_id="lease_2", now=100.0)
        assert w2.active_concurrency == 2
        assert w2.status == WorkerStatus.BUSY
        assert w2.has_capacity() is False

        # Attempt Slot 3 -> WorkerCapacityExceededError
        with pytest.raises(WorkerCapacityExceededError):
            registry.acquire_capacity("w_cap", lease_id="lease_3", now=100.0)

        # Release Slot 1 -> Returns to READY
        w_rel = registry.release_capacity("w_cap", lease_id="lease_1")
        assert w_rel.active_concurrency == 1
        assert w_rel.status == WorkerStatus.READY
        assert w_rel.has_capacity() is True

    def test_capability_mismatch_rejection(self, temp_fabric_db: str) -> None:
        """Verify assignment is rejected if worker lacks required capability."""
        store = FabricStore(db_path=temp_fabric_db)
        registry = WorkerRegistry(store=store)
        registry.register_worker(
            worker_id="w_basic",
            supported_capabilities=["read_file"],
            now=100.0,
        )

        with pytest.raises(CapabilityMismatchError):
            registry.acquire_capacity("w_basic", lease_id="lease_gpu", required_capabilities=["gpu_cuda"], now=100.0)


# ═════════════════════════════════════════════════════════════════════════════
# Category 3: Worker Draining & Graceful Lifecycle
# ═════════════════════════════════════════════════════════════════════════════

class TestWorkerDraining:

    def test_drain_idle_worker_transitions_directly_to_stopped(self, temp_fabric_db: str) -> None:
        """Draining an idle worker with 0 active tasks stops immediately."""
        store = FabricStore(db_path=temp_fabric_db)
        registry = WorkerRegistry(store=store)
        registry.register_worker(worker_id="w_idle", max_concurrency=4, now=100.0)

        drained = registry.drain_worker("w_idle")
        assert drained.drain_state is True
        assert drained.status == WorkerStatus.STOPPED
        assert drained.has_capacity() is False

    def test_drain_busy_worker_transitions_draining_then_stopped(self, temp_fabric_db: str) -> None:
        """Draining a busy worker transitions to DRAINING, rejects new work, and stops on last release."""
        store = FabricStore(db_path=temp_fabric_db)
        registry = WorkerRegistry(store=store)
        registry.register_worker(worker_id="w_busy", max_concurrency=2, now=100.0)

        # Occupy both slots
        registry.acquire_capacity("w_busy", lease_id="l1", now=100.0)
        registry.acquire_capacity("w_busy", lease_id="l2", now=100.0)

        # Initiate drain
        draining = registry.drain_worker("w_busy")
        assert draining.status == WorkerStatus.DRAINING
        assert draining.drain_state is True

        # New capacity request must be rejected
        with pytest.raises(WorkerCapacityExceededError):
            registry.acquire_capacity("w_busy", lease_id="l3", now=100.0)

        # Release first task: still DRAINING
        mid = registry.release_capacity("w_busy", lease_id="l1")
        assert mid.status == WorkerStatus.DRAINING
        assert mid.active_concurrency == 1

        # Release second task: completes transition to STOPPED
        final = registry.release_capacity("w_busy", lease_id="l2")
        assert final.status == WorkerStatus.STOPPED
        assert final.active_concurrency == 0


# ═════════════════════════════════════════════════════════════════════════════
# Category 4: Worker Unhealthy Detection & Stale Worker Protection
# ═════════════════════════════════════════════════════════════════════════════

class TestWorkerHealthDetection:

    def test_unhealthy_worker_detection_and_rejection(self, temp_fabric_db: str) -> None:
        """Verify worker with expired heartbeat is marked UNHEALTHY and rejected."""
        store = FabricStore(db_path=temp_fabric_db)
        registry = WorkerRegistry(store=store, heartbeat_timeout_sec=10.0)
        registry.register_worker(worker_id="w_stale", max_concurrency=4, now=100.0)

        # At now=115.0, heartbeat elapsed 15s > 10s
        unhealthy = registry.detect_unhealthy_workers(now=115.0)
        assert len(unhealthy) == 1
        assert unhealthy[0].worker_id == "w_stale"
        assert unhealthy[0].status == WorkerStatus.UNHEALTHY

        # Trying to acquire capacity from stale/unhealthy worker raises StaleWorkerError
        with pytest.raises(StaleWorkerError):
            registry.acquire_capacity("w_stale", lease_id="l_stale", now=115.0)

    def test_heartbeat_revival_of_unhealthy_worker(self, temp_fabric_db: str) -> None:
        """Verify unhealthy worker becomes READY upon sending a fresh heartbeat."""
        store = FabricStore(db_path=temp_fabric_db)
        registry = WorkerRegistry(store=store, heartbeat_timeout_sec=10.0)
        registry.register_worker(worker_id="w_revive", now=100.0)
        registry.detect_unhealthy_workers(now=120.0)
        assert registry.get_worker("w_revive").status == WorkerStatus.UNHEALTHY

        # Heartbeat restores READY status
        revived = registry.heartbeat("w_revive", now=121.0)
        assert revived.status == WorkerStatus.READY


# ═════════════════════════════════════════════════════════════════════════════
# Category 5: Scheduler Multi-Tenant Fairness & Concurrency Quotas
# ═════════════════════════════════════════════════════════════════════════════

class TestSchedulerFairnessAndQuotas:

    def test_multi_tenant_fair_share_interleaving(self, temp_fabric_db: str) -> None:
        """Verify scheduler round-robins across tenants so one tenant cannot starve another."""
        store = FabricStore(db_path=temp_fabric_db)
        queue = DurableTaskQueue(store=store)
        registry = WorkerRegistry(store=store)
        # Register worker with 10 slots
        registry.register_worker(worker_id="w_pool", max_concurrency=10, now=100.0)

        scheduler = ExecutionScheduler(
            queue=queue,
            worker_registry=registry,
            quota_policy=SchedulerQuotaPolicy(max_concurrency_per_tenant=5, max_global_concurrency=20),
        )

        # Tenant Alpha enqueues 6 items; Tenant Beta enqueues 2 items
        for i in range(6):
            queue.enqueue(task_id=f"t_alpha_{i}", tenant_id="tenant_alpha", priority=50, enqueued_at=100.0 + i)
        for i in range(2):
            queue.enqueue(task_id=f"t_beta_{i}", tenant_id="tenant_beta", priority=50, enqueued_at=100.0 + i)

        decisions = scheduler.schedule_cycle(limit=10, now=105.0)

        # Both tenants get dispatched fairly
        dispatched = [d for d in decisions if d.verdict == SchedulingVerdict.DISPATCHED]
        alpha_dispatched = [d for d in dispatched if d.tenant_id == "tenant_alpha"]
        beta_dispatched = [d for d in dispatched if d.tenant_id == "tenant_beta"]

        assert len(beta_dispatched) == 2, "Tenant Beta should not be starved by Tenant Alpha"
        assert len(alpha_dispatched) <= 5, "Tenant Alpha must respect max_concurrency_per_tenant"

    def test_tenant_quota_exceeded_verdict(self, temp_fabric_db: str) -> None:
        """Verify excess tenant items receive TENANT_QUOTA_EXCEEDED without dropping."""
        store = FabricStore(db_path=temp_fabric_db)
        queue = DurableTaskQueue(store=store)
        registry = WorkerRegistry(store=store)
        registry.register_worker(worker_id="w_big", max_concurrency=10, now=100.0)

        scheduler = ExecutionScheduler(
            queue=queue,
            worker_registry=registry,
            quota_policy=SchedulerQuotaPolicy(max_concurrency_per_tenant=2, max_global_concurrency=20),
        )

        # 4 items for tenant_limited
        for i in range(4):
            queue.enqueue(task_id=f"t_lim_{i}", tenant_id="tenant_limited", priority=50, enqueued_at=100.0 + i)

        decisions = scheduler.schedule_cycle(limit=10, now=105.0)
        dispatched = [d for d in decisions if d.verdict == SchedulingVerdict.DISPATCHED]
        quota_exceeded = [d for d in decisions if d.verdict == SchedulingVerdict.TENANT_QUOTA_EXCEEDED]

        assert len(dispatched) == 2
        assert len(quota_exceeded) == 2

    def test_global_concurrency_backpressure(self, temp_fabric_db: str) -> None:
        """Verify scheduler halts dispatches when global concurrency limit is reached."""
        store = FabricStore(db_path=temp_fabric_db)
        queue = DurableTaskQueue(store=store)
        registry = WorkerRegistry(store=store)
        registry.register_worker(worker_id="w_large", max_concurrency=20, now=100.0)

        scheduler = ExecutionScheduler(
            queue=queue,
            worker_registry=registry,
            quota_policy=SchedulerQuotaPolicy(
                max_concurrency_per_tenant=10,
                max_global_concurrency=3,  # Global cap is 3
            ),
        )

        for i in range(5):
            queue.enqueue(task_id=f"t_glob_{i}", tenant_id=f"tenant_{i}", priority=50, enqueued_at=100.0 + i)

        decisions = scheduler.schedule_cycle(limit=10, now=105.0)
        dispatched = [d for d in decisions if d.verdict == SchedulingVerdict.DISPATCHED]
        backpressure = [d for d in decisions if d.verdict == SchedulingVerdict.QUEUED_BACKPRESSURE]

        assert len(dispatched) == 3
        assert len(backpressure) >= 1


# ═════════════════════════════════════════════════════════════════════════════
# Category 6: Scheduler Dependency DAG & Budget Awareness
# ═════════════════════════════════════════════════════════════════════════════

class TestSchedulerDependenciesAndBudget:

    def test_blocked_step_dependencies_held_in_queue(
        self,
        temp_fabric_db: str,
        temp_supervisor_db: str,
    ) -> None:
        """Verify step with unsatisfied dependencies receives BLOCKED_DEPENDENCIES."""
        store = FabricStore(db_path=temp_fabric_db)
        queue = DurableTaskQueue(store=store)
        registry = WorkerRegistry(store=store)
        registry.register_worker(worker_id="w_dag", max_concurrency=4, now=100.0)

        # Setup 2-step plan: step_2 depends on step_1
        sup_store = DurableSupervisorStore(db_path=temp_supervisor_db)
        supervisor = DurableExecutionSupervisor(store=sup_store)

        plan = ExecutionPlan(
            plan_id="plan_dag",
            task_id="task_dag",
            tenant_id="tenant_dag",
            objective="test dag dependencies",
            steps={
                "step_1": PlanStep(step_id="step_1", objective="step 1", status=PlanStepStatus.PENDING),
                "step_2": PlanStep(step_id="step_2", objective="step 2", dependencies=["step_1"], status=PlanStepStatus.PENDING),
            },
        )
        graph = ExecutionGraph(plan=plan)
        supervisor.register_task(task_id="task_dag", tenant_id="tenant_dag", graph=graph)

        scheduler = ExecutionScheduler(
            queue=queue,
            worker_registry=registry,
            supervisor=supervisor,
        )

        q1 = queue.enqueue(task_id="task_dag", tenant_id="tenant_dag", step_id="step_1", priority=50)
        q2 = queue.enqueue(task_id="task_dag", tenant_id="tenant_dag", step_id="step_2", priority=50)

        decisions = scheduler.schedule_cycle(limit=10, now=100.0)
        d1 = next(d for d in decisions if d.item_id == q1.item_id)
        d2 = next(d for d in decisions if d.item_id == q2.item_id)

        assert d1.verdict == SchedulingVerdict.DISPATCHED
        assert d2.verdict == SchedulingVerdict.BLOCKED_DEPENDENCIES

    def test_budget_exhaustion_verdict(self, temp_fabric_db: str) -> None:
        """Verify item with budget_exhausted flag in payload receives BUDGET_EXHAUSTED."""
        store = FabricStore(db_path=temp_fabric_db)
        queue = DurableTaskQueue(store=store)
        registry = WorkerRegistry(store=store)
        registry.register_worker(worker_id="w_bgt", max_concurrency=4, now=100.0)

        scheduler = ExecutionScheduler(queue=queue, worker_registry=registry)
        item = queue.enqueue(
            task_id="t_budget",
            tenant_id="tenant_bgt",
            priority=50,
            payload={"budget_exhausted": True},
        )

        decisions = scheduler.schedule_cycle(limit=5, now=100.0)
        assert len(decisions) == 1
        assert decisions[0].verdict == SchedulingVerdict.BUDGET_EXHAUSTED


# ═════════════════════════════════════════════════════════════════════════════
# Category 7: Atomic Claiming & Phase 20 CAS Lease Integration
# ═════════════════════════════════════════════════════════════════════════════

class TestAtomicClaimingWithPhase20Lease:

    def test_atomic_claim_acquires_supervisor_lease(
        self,
        temp_fabric_db: str,
        temp_supervisor_db: str,
    ) -> None:
        """Verify dispatching a step item binds to an authoritative Phase 20 StepLease."""
        store = FabricStore(db_path=temp_fabric_db)
        queue = DurableTaskQueue(store=store)
        registry = WorkerRegistry(store=store)
        registry.register_worker(worker_id="w_lease", max_concurrency=2, now=100.0)

        sup_store = DurableSupervisorStore(db_path=temp_supervisor_db)
        supervisor = DurableExecutionSupervisor(store=sup_store)

        plan = ExecutionPlan(
            plan_id="p_lease",
            task_id="t_lease",
            tenant_id="tenant_l",
            objective="lease binding",
            steps={"s1": PlanStep(step_id="s1", objective="do work", status=PlanStepStatus.READY)},
        )
        graph = ExecutionGraph(plan=plan)
        supervisor.register_task(task_id="t_lease", tenant_id="tenant_l", graph=graph)

        scheduler = ExecutionScheduler(queue=queue, worker_registry=registry, supervisor=supervisor)
        item = queue.enqueue(task_id="t_lease", tenant_id="tenant_l", step_id="s1")

        decisions = scheduler.schedule_cycle(limit=5, now=100.0)
        assert len(decisions) == 1
        assert decisions[0].verdict == SchedulingVerdict.DISPATCHED

        # Queue item must have lease_id
        q_item = queue.get_item(item.item_id)
        assert q_item.status == QueueItemStatus.CLAIMED
        assert q_item.lease_id is not None
        assert q_item.lease_id.startswith("lease_")

        # Step in supervisor must also be CLAIMED with matching lease_id
        step_rec = sup_store.get_step("t_lease", "s1")
        assert step_rec is not None
        assert step_rec.status == PlanStepStatus.CLAIMED.value
        assert step_rec.lease_id == q_item.lease_id
        assert step_rec.worker_id == "w_lease"


# ═════════════════════════════════════════════════════════════════════════════
# Category 8: Side-Effect Retry Safety Policy & Categorization
# ═════════════════════════════════════════════════════════════════════════════

class TestRetrySafetyPolicy:

    def test_safe_read_tools_evaluate_to_safe_retry(self) -> None:
        """Pure read-only tools are categorized as SAFE_RETRY."""
        safety = evaluate_retry_safety(
            tool_names=["read_file", "grep_search", "git_status"],
            has_unverified_mutations=False,
            is_idempotent=False,
            risk_level="LOW",
        )
        assert safety == RetrySafetyClass.SAFE_RETRY

        decision = map_safety_to_recovery_decision(safety, remaining_retries=2)
        assert decision == RecoveryDecision.RETRY

    def test_dangerous_tools_fail_closed_unless_idempotent(self) -> None:
        """Dangerous side-effect tools fail closed unless explicitly marked idempotent."""
        # Non-idempotent execute_command
        danger_safety = evaluate_retry_safety(
            tool_names=["execute_command"],
            has_unverified_mutations=True,
            is_idempotent=False,
            risk_level="HIGH",
        )
        assert danger_safety == RetrySafetyClass.FAIL_CLOSED

        danger_decision = map_safety_to_recovery_decision(danger_safety, remaining_retries=2)
        assert danger_decision == RecoveryDecision.FAIL_CLOSED

        # Explicitly idempotent tool
        idempotent_safety = evaluate_retry_safety(
            tool_names=["execute_command"],
            has_unverified_mutations=True,
            is_idempotent=True,
            risk_level="HIGH",
        )
        assert idempotent_safety == RetrySafetyClass.IDEMPOTENT

        idempotent_decision = map_safety_to_recovery_decision(idempotent_safety, remaining_retries=2)
        assert idempotent_decision == RecoveryDecision.RETRY

    def test_hitl_and_unverified_mutations_fail_closed(self) -> None:
        """HITL requirements or critical unverified mutations fail closed on retry."""
        safety = evaluate_retry_safety(
            tool_names=["edit_file"],
            has_unverified_mutations=True,
            requires_hitl=True,
            risk_level="CRITICAL",
        )
        assert safety == RetrySafetyClass.FAIL_CLOSED


# ═════════════════════════════════════════════════════════════════════════════
# Category 9: Crash, Lease Expiry & Multi-Worker Orphan Recovery
# ═════════════════════════════════════════════════════════════════════════════

class TestCrashAndOrphanRecovery:

    def test_worker_crash_safe_work_requeued_and_claimed_by_second_worker(
        self,
        fabric_service: ExecutionFabricService,
    ) -> None:
        """
        Verify crash resilience:
        1. Worker A claims safe read work.
        2. Worker A crashes (heartbeat expires).
        3. Recovery detects Worker A unhealthy and requeues safe work.
        4. Healthy Worker B claims and finishes the work.
        """
        # Register Worker A and Worker B
        fabric_service.register_worker("worker_a", max_concurrency=2, now=100.0)
        fabric_service.register_worker("worker_b", max_concurrency=2, now=100.0)

        # Enqueue item
        item = fabric_service.submit_task(
            task_id="task_crash_sim",
            tenant_id="tenant_crash",
            payload={"allowed_tools": ["read_file", "grep_search"], "max_retries": 3},
            enqueued_at=100.0,
        )

        # Dispatch: Worker A claims it
        decisions1 = fabric_service.tick_scheduler(limit=1, now=100.0)
        assert len(decisions1) == 1
        assert decisions1[0].verdict == SchedulingVerdict.DISPATCHED
        assigned_worker = decisions1[0].assigned_worker_id

        # Simulate Worker A crashing: time advances past heartbeat timeout (now=120.0 > 105.0)
        # Worker B continues heartbeating
        fabric_service.worker_heartbeat("worker_b", now=120.0)

        # Run recovery
        summary = fabric_service.recover_orphans(now=120.0)
        assert "worker_a" in summary["unhealthy_workers"]
        assert item.item_id in summary["requeued_items"]

        # Requeued item is runnable again with attempt=2
        requeued_item = fabric_service.queue.get_item(item.item_id)
        assert requeued_item.status == QueueItemStatus.REQUEUED
        assert requeued_item.attempt == 2

        # Scheduler tick: Worker B claims the requeued item
        decisions2 = fabric_service.tick_scheduler(limit=1, now=120.0)
        assert len(decisions2) == 1
        assert decisions2[0].verdict == SchedulingVerdict.DISPATCHED
        assert decisions2[0].assigned_worker_id == "worker_b"

        # Worker B executes to completion
        exec_res = fabric_service.execute_claimed_item(item.item_id, worker_id="worker_b", now=120.0)
        assert exec_res["status"] == "COMPLETED"

    def test_worker_crash_dangerous_work_fails_closed(
        self,
        fabric_service: ExecutionFabricService,
    ) -> None:
        """Verify crash during non-idempotent dangerous operation fails closed."""
        fabric_service.register_worker("worker_crit", max_concurrency=2, now=100.0)
        item = fabric_service.submit_task(
            task_id="task_danger_crash",
            tenant_id="tenant_danger",
            payload={
                "allowed_tools": ["execute_command", "deploy"],
                "has_unverified_mutations": True,
                "is_idempotent": False,
                "risk_level": "HIGH",
            },
            enqueued_at=100.0,
        )

        fabric_service.tick_scheduler(limit=1, now=100.0)

        # Worker dies
        summary = fabric_service.recover_orphans(now=150.0)
        assert item.item_id in summary["failed_items"]

        failed_item = fabric_service.queue.get_item(item.item_id)
        assert failed_item.status == QueueItemStatus.FAILED
        assert "non-idempotent" in (failed_item.error_message or "").lower()


# ═════════════════════════════════════════════════════════════════════════════
# Category 10: End-to-End Execution Fabric Pipeline
# ═════════════════════════════════════════════════════════════════════════════

class TestEndToEndExecutionFabricPipeline:

    def test_e2e_full_task_submission_and_execution_lifecycle(
        self,
        fabric_service: ExecutionFabricService,
    ) -> None:
        """Verify full lifecycle: submit -> schedule -> execute -> release."""
        fabric_service.register_worker("worker_e2e", max_concurrency=4, now=100.0)

        item = fabric_service.submit_task(
            task_id="t_e2e",
            tenant_id="tenant_e2e",
            priority=70,
            payload={"action": "build_package"},
            enqueued_at=100.0,
        )
        assert fabric_service.queue.get_queue_depth() == 1

        # Schedule
        decisions = fabric_service.tick_scheduler(limit=5, now=100.0)
        assert len(decisions) == 1
        assert decisions[0].verdict == SchedulingVerdict.DISPATCHED

        metrics_during = fabric_service.get_metrics()
        assert metrics_during["active_concurrency"] == 1

        # Worker executes
        def custom_executor(q_item: QueueItem) -> Dict[str, Any]:
            return {"built": True, "artifact": "package.tar.gz"}

        result = fabric_service.execute_claimed_item(
            item_id=item.item_id,
            worker_id="worker_e2e",
            executor_fn=custom_executor,
            now=101.0,
        )
        assert result["ok"] is True
        assert result["status"] == "COMPLETED"
        assert result["result"]["artifact"] == "package.tar.gz"

        metrics_after = fabric_service.get_metrics()
        assert metrics_after["active_concurrency"] == 0
        assert metrics_after["queue_depth"] == 0

    def test_e2e_multi_step_dag_graph_execution(
        self,
        fabric_service: ExecutionFabricService,
    ) -> None:
        """Verify sequential multi-step execution where step 2 is scheduled after step 1 completes."""
        fabric_service.register_worker("worker_dag", max_concurrency=4, now=100.0)

        plan = ExecutionPlan(
            plan_id="plan_seq",
            task_id="t_seq",
            tenant_id="tenant_seq",
            objective="sequential pipeline",
            steps={
                "step_fetch": PlanStep(step_id="step_fetch", objective="fetch repo", status=PlanStepStatus.PENDING),
                "step_test": PlanStep(step_id="step_test", objective="run tests", dependencies=["step_fetch"], status=PlanStepStatus.PENDING),
            },
        )
        graph = ExecutionGraph(plan=plan)
        fabric_service.supervisor.register_task(task_id="t_seq", tenant_id="tenant_seq", graph=graph)

        enqueued = fabric_service.enqueue_graph_steps("t_seq", "tenant_seq", graph=graph)
        assert len(enqueued) == 2

        # Cycle 1: only step_fetch can be dispatched; step_test is BLOCKED
        d_cycle1 = fabric_service.tick_scheduler(limit=10, now=100.0)
        fetch_decision = next(d for d in d_cycle1 if d.step_id == "step_fetch")
        test_decision = next(d for d in d_cycle1 if d.step_id == "step_test")

        assert fetch_decision.verdict == SchedulingVerdict.DISPATCHED
        assert test_decision.verdict == SchedulingVerdict.BLOCKED_DEPENDENCIES

        # Execute step_fetch to completion
        fabric_service.execute_claimed_item(
            item_id=fetch_decision.item_id,
            worker_id="worker_dag",
            executor_fn=lambda it: {"fetched": True},
            now=101.0,
        )

        # In supervisor, step_fetch is now SUCCEEDED
        assert fabric_service.supervisor.store.get_step("t_seq", "step_fetch").status == PlanStepStatus.SUCCEEDED.value

        # Cycle 2: step_test is now unblocked and DISPATCHED
        d_cycle2 = fabric_service.tick_scheduler(limit=10, now=102.0)
        test_decision2 = next(d for d in d_cycle2 if d.step_id == "step_test")
        assert test_decision2.verdict == SchedulingVerdict.DISPATCHED

        # Execute step_test to completion
        test_res = fabric_service.execute_claimed_item(
            item_id=test_decision2.item_id,
            worker_id="worker_dag",
            executor_fn=lambda it: {"all_tests_passed": True},
            now=103.0,
        )
        assert test_res["ok"] is True
        assert test_res["status"] == "COMPLETED"

    def test_e2e_worker_drain_and_standby_migration(
        self,
        fabric_service: ExecutionFabricService,
    ) -> None:
        """Verify draining active worker allows current task to finish while new tasks route to standby worker."""
        fabric_service.register_worker("worker_primary", max_concurrency=1, now=100.0)
        fabric_service.register_worker("worker_standby", max_concurrency=1, now=100.0)

        # Task 1 dispatched to primary
        t1 = fabric_service.submit_task("task_1", "tenant_drain", enqueued_at=100.0)
        dec1 = fabric_service.tick_scheduler(limit=1, now=100.0)
        assert dec1[0].assigned_worker_id == "worker_primary"

        # Drain primary while task 1 is in-flight
        drained_prim = fabric_service.drain_worker("worker_primary")
        assert drained_prim.status == WorkerStatus.DRAINING

        # Task 2 submitted: must route to standby because primary is draining
        t2 = fabric_service.submit_task("task_2", "tenant_drain", enqueued_at=101.0)
        dec2 = fabric_service.tick_scheduler(limit=1, now=101.0)
        assert dec2[0].assigned_worker_id == "worker_standby"

        # Primary finishes task 1 -> transitions to STOPPED
        fabric_service.execute_claimed_item(t1.item_id, worker_id="worker_primary", now=102.0)
        assert fabric_service.get_worker("worker_primary").status == WorkerStatus.STOPPED

        # Standby finishes task 2 -> remains READY
        fabric_service.execute_claimed_item(t2.item_id, worker_id="worker_standby", now=103.0)
        assert fabric_service.get_worker("worker_standby").status == WorkerStatus.READY


# ═════════════════════════════════════════════════════════════════════════════
# Category 11: Edge Cases, Security & Advanced Control Plane Verification
# ═════════════════════════════════════════════════════════════════════════════

class TestAdvancedControlPlaneAndSecurity:

    def test_multi_worker_load_balancing_selects_least_loaded(
        self,
        fabric_service: ExecutionFabricService,
    ) -> None:
        """Verify scheduler allocates to the worker with the highest available slots."""
        # Worker 1 has 4 slots, 1 used (3 available)
        fabric_service.register_worker("w_loaded", max_concurrency=4, now=100.0)
        fabric_service.worker_registry.acquire_capacity("w_loaded", lease_id="l_prior", now=100.0)

        # Worker 2 has 4 slots, 0 used (4 available)
        fabric_service.register_worker("w_fresh", max_concurrency=4, now=100.0)

        item = fabric_service.submit_task("t_bal", "tenant_bal", enqueued_at=100.0)
        decisions = fabric_service.tick_scheduler(limit=1, now=100.0)

        assert len(decisions) == 1
        assert decisions[0].assigned_worker_id == "w_fresh", "Must pick worker with most open slots (4 > 3)"

    def test_empty_queue_tick_returns_empty_decisions(
        self,
        fabric_service: ExecutionFabricService,
    ) -> None:
        """Verify tick on empty queue returns empty list gracefully."""
        decisions = fabric_service.tick_scheduler(limit=10, now=100.0)
        assert decisions == []

    def test_invalid_queue_transitions_raise_error(self, temp_fabric_db: str) -> None:
        """Verify invalid queue state transitions raise FabricError."""
        store = FabricStore(db_path=temp_fabric_db)
        queue = DurableTaskQueue(store=store)
        item = queue.enqueue(task_id="t_inv", tenant_id="tenant_inv")

        queue.mark_dispatching(item.item_id, worker_id="w1")
        queue.mark_claimed(item.item_id, worker_id="w1", lease_id="l1")
        queue.mark_completed(item.item_id)

        # Cannot dispatch completed item
        with pytest.raises(FabricError):
            queue.mark_dispatching(item.item_id, worker_id="w2")

    def test_worker_not_found_raises_exception(self, temp_fabric_db: str) -> None:
        """Verify operations on non-existent worker raise WorkerNotFoundError."""
        store = FabricStore(db_path=temp_fabric_db)
        registry = WorkerRegistry(store=store)

        with pytest.raises(WorkerNotFoundError):
            registry.acquire_capacity("non_existent_worker", lease_id="l_x")

        with pytest.raises(WorkerNotFoundError):
            registry.drain_worker("non_existent_worker")

    def test_replay_events_and_secret_scrubbing(
        self,
        fabric_service: ExecutionFabricService,
    ) -> None:
        """Verify events emitted during queue and worker operations scrub sensitive tokens."""
        item = fabric_service.submit_task(
            task_id="t_sec",
            tenant_id="tenant_sec",
            payload={"secret": "sk-ant-api03-secret12345678901234567890"},
        )
        fabric_service.register_worker("w_sec", max_concurrency=2)
        fabric_service.tick_scheduler(limit=1)

        # Execute with an error containing an API key
        def leaky_executor(it: QueueItem) -> None:
            raise ValueError("Failed connecting with bearer token: ghp_123456789012345678901234567890123456")

        fabric_service.execute_claimed_item(item.item_id, worker_id="w_sec", executor_fn=leaky_executor)

        # Verify failed item error message was scrubbed
        failed_item = fabric_service.queue.get_item(item.item_id)
        assert failed_item is not None
        assert "ghp_" not in (failed_item.error_message or "")
        assert "[REDACTED" in (failed_item.error_message or "")

    def test_tenant_isolation_concurrency_accounting(
        self,
        fabric_service: ExecutionFabricService,
    ) -> None:
        """Verify concurrency counters are strictly partitioned by tenant."""
        fabric_service.register_worker("w_iso", max_concurrency=10, now=100.0)

        # Tenant 1 occupies 2 slots
        t1_a = fabric_service.submit_task("t1_a", "tenant_one", enqueued_at=100.0)
        t1_b = fabric_service.submit_task("t1_b", "tenant_one", enqueued_at=100.0)
        fabric_service.tick_scheduler(limit=2, now=100.0)

        # Tenant 2 has 0 slots
        assert fabric_service.queue.count_active_tenant_items("tenant_one") == 2
        assert fabric_service.queue.count_active_tenant_items("tenant_two") == 0

        # Tenant 2 submits an item and gets scheduled independently
        t2_a = fabric_service.submit_task("t2_a", "tenant_two", enqueued_at=100.0)
        dec = fabric_service.tick_scheduler(limit=1, now=100.0)
        assert len(dec) == 1
        assert dec[0].tenant_id == "tenant_two"
        assert fabric_service.queue.count_active_tenant_items("tenant_two") == 1

    def test_capability_unavailable_verdict(
        self,
        fabric_service: ExecutionFabricService,
    ) -> None:
        """Verify asking for an unregistered capability produces CAPABILITY_UNAVAILABLE."""
        fabric_service.register_worker(
            "w_cpu_only",
            supported_capabilities=["cpu_compute", "read_file"],
            now=100.0,
        )

        item = fabric_service.submit_task(
            task_id="t_special",
            tenant_id="tenant_special",
            required_capabilities=["quantum_simulator"],
            enqueued_at=100.0,
        )

        decisions = fabric_service.tick_scheduler(limit=1, now=100.0)
        assert len(decisions) == 1
        assert decisions[0].verdict == SchedulingVerdict.CAPABILITY_UNAVAILABLE

    def test_worker_explicit_stop_cancels_work(
        self,
        fabric_service: ExecutionFabricService,
    ) -> None:
        """Verify explicit stop marks worker STOPPED and prevents new claims."""
        w = fabric_service.register_worker("w_stop_test", max_concurrency=4, now=100.0)
        stopped = fabric_service.stop_worker("w_stop_test", reason="Maintenance shutdown")

        assert stopped.status == WorkerStatus.STOPPED
        assert stopped.has_capacity() is False

        # Attempt to claim with stopped worker raises WorkerCapacityExceededError
        with pytest.raises(WorkerCapacityExceededError):
            fabric_service.worker_registry.acquire_capacity("w_stop_test", lease_id="l_stop")

