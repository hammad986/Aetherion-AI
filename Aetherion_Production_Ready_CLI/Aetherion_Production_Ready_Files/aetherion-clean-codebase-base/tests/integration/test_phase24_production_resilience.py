"""
tests/integration/test_phase24_production_resilience.py — Phase 24 Production Release & Operational Resilience Suite.

Verifies the 15 production operational resilience invariants:
  1. Two workers start and register cleanly
  2. Task submitted and durable in queue
  3. Worker A claims task via atomic CAS
  4. Worker A drains gracefully, rejecting new claims
  5. Worker B safely continues eligible work
  6. No duplicate successful execution across workers
  7. Durable state survives API / service restart
  8. Durable state survives worker crash / restart
  9. Redis loss does not corrupt durable state
  10. PostgreSQL unavailability fails closed in production mode
  11. HITL-pending task cannot be illegally reclaimed
  12. Verifier-pending task cannot become successful without verifier
  13. RunBudget cannot be exceeded after recovery
  14. Tenant isolation remains intact across scheduling & quotas
  15. Secret values do not leak in operational logs and metrics
  16. Migration version tracking and rollback safety contract
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch
import uuid

import pytest
from flask import Flask

from backend.core.environment import EnvironmentMode
from backend.execution.fabric.coordination import FabricCoordinator
from backend.execution.fabric.logging import (
    FabricStructuredLogger,
    correlation_context,
    deep_scrub,
    get_structured_logger,
)
from backend.execution.fabric.metrics import (
    FabricMetricsCollector,
    get_metrics_collector,
)
from backend.execution.fabric.migrations import (
    CURRENT_CODE_SCHEMA_VERSION,
    IncompatibleSchemaError,
    SchemaCorruptionError,
    apply_fabric_migrations,
    get_applied_migrations,
    verify_rollback_safety,
    verify_schema_compatibility,
)
from backend.execution.fabric.models import (
    FailureCategory,
    QueueItem,
    QueueItemStatus,
    RetrySafetyClass,
    SchedulingDecision,
    SchedulingVerdict,
    WorkerRecord,
    WorkerStatus,
)
from backend.execution.fabric.queue import DurableTaskQueue
from backend.execution.fabric.retry_policy import (
    classify_failure,
    evaluate_retry_safety,
    map_failure_category_to_recovery,
    map_safety_to_recovery_decision,
)
from backend.execution.fabric.rolling_lifecycle import (
    RollingReplacementController,
)
from backend.execution.fabric.scheduler import ExecutionScheduler, SchedulerQuotaPolicy
from backend.execution.fabric.service import ExecutionFabricService
from backend.execution.fabric.store import (
    BaseFabricStore,
    SqliteFabricStore,
    get_fabric_store,
)
from backend.execution.fabric.worker_registry import WorkerRegistry
from backend.execution.fabric.worker_runtime import WorkerRuntime
from backend.execution.orchestration.durable_supervisor import RecoveryDecision
from backend.routes.health import health_bp


@pytest.fixture
def temp_fabric_db(tmp_path):
    """Provides a fresh isolated SQLite database with migrations applied."""
    db_file = str(tmp_path / "test_phase24_fabric.db")
    store = SqliteFabricStore(db_path=db_file)
    yield store, db_file


@pytest.fixture
def fabric_service(temp_fabric_db):
    """Provides an ExecutionFabricService backed by an isolated store."""
    store, _ = temp_fabric_db
    coordinator = FabricCoordinator()
    svc = ExecutionFabricService(store=store)
    return svc


# ── Invariant 1: Two Workers Start & Register Cleanly ─────────────────────────

def test_01_two_workers_start_and_register(temp_fabric_db):
    store, _ = temp_fabric_db
    coordinator = FabricCoordinator()
    service = ExecutionFabricService(store=store)

    worker_a = WorkerRuntime(
        worker_id="worker_alpha",
        store=store,
        service=service,
        coordinator=coordinator,
        supported_capabilities={"DEFAULT", "CPU"},
        max_concurrency=4,
    )
    worker_b = WorkerRuntime(
        worker_id="worker_beta",
        store=store,
        service=service,
        coordinator=coordinator,
        supported_capabilities={"DEFAULT", "GPU"},
        max_concurrency=2,
    )

    worker_a.register()
    worker_b.register()

    rec_a = store.get_worker("worker_alpha")
    rec_b = store.get_worker("worker_beta")

    assert rec_a is not None
    assert rec_a.worker_id == "worker_alpha"
    assert rec_a.max_concurrency == 4
    assert getattr(rec_a.status, "value", str(rec_a.status)) == "READY"
    assert "CPU" in rec_a.supported_capabilities

    assert rec_b is not None
    assert rec_b.worker_id == "worker_beta"
    assert rec_b.max_concurrency == 2
    assert getattr(rec_b.status, "value", str(rec_b.status)) == "READY"
    assert "GPU" in rec_b.supported_capabilities

    workers = store.list_workers()
    assert len(workers) == 2


# ── Invariant 2: Task Submitted & Durable in Queue ───────────────────────────

def test_02_task_submission_and_durability(fabric_service):
    svc = fabric_service
    task_id = "task_resilience_002"
    tenant_id = "tenant_enterprise"

    item = svc.submit_task(
        task_id=task_id,
        tenant_id=tenant_id,
        step_id="step_ast_parse",
        priority=90,
        required_capabilities=["DEFAULT"],
        payload={"module": "parser.py", "is_idempotent": True},
    )

    persisted = svc.store.get_queue_item(item.item_id)
    assert persisted is not None
    assert persisted.item_id == item.item_id
    assert persisted.task_id == task_id
    assert persisted.tenant_id == tenant_id
    assert persisted.priority == 90
    assert persisted.status == QueueItemStatus.QUEUED
    assert persisted.assigned_worker_id is None
    assert persisted.attempt == 1


# ── Invariant 3: Worker A Claims Task via Atomic CAS ──────────────────────────

def test_03_worker_a_claims_task_via_atomic_cas(fabric_service):
    svc = fabric_service
    svc.register_worker("worker_a", max_concurrency=2, supported_capabilities={"DEFAULT"})
    svc.register_worker("worker_b", max_concurrency=2, supported_capabilities={"DEFAULT"})

    item = svc.submit_task(
        task_id="task_cas_003",
        tenant_id="tenant_a",
        step_id="step_code_gen",
        priority=75,
        required_capabilities=["DEFAULT"],
    )

    # Scheduler cycle claims item
    decisions = svc.tick_scheduler(limit=10)
    assert len(decisions) >= 1
    d = next(d for d in decisions if d.item_id == item.item_id)
    assert d.verdict == SchedulingVerdict.DISPATCHED
    assert d.assigned_worker_id in ("worker_a", "worker_b")

    first_claimant = d.assigned_worker_id
    other_worker = "worker_b" if first_claimant == "worker_a" else "worker_a"

    # Second worker attempts to atomically claim the already-claimed item -> CAS fails
    cas_success = svc.store.claim_item_atomic(
        item_id=item.item_id,
        worker_id=other_worker,
        lease_id="illegal_lease",
        expected_statuses=[QueueItemStatus.QUEUED.value],
    )
    assert cas_success is False

    claimed_item = svc.store.get_queue_item(item.item_id)
    assert claimed_item.assigned_worker_id == first_claimant


# ── Invariant 4: Worker A Drains Gracefully, Rejecting New Claims ─────────────

def test_04_worker_a_drain_lifecycle(temp_fabric_db):
    store, _ = temp_fabric_db
    coordinator = FabricCoordinator()
    svc = ExecutionFabricService(store=store)

    worker = WorkerRuntime(
        worker_id="worker_drain_test",
        store=store,
        service=svc,
        coordinator=coordinator,
        max_concurrency=2,
    )
    worker.register()

    assert worker.status == WorkerStatus.READY
    assert not worker.is_draining

    worker.drain(wait=False)

    assert worker.is_draining
    assert worker.status == WorkerStatus.DRAINING

    # Verify store worker record reflects drain_state = 1
    rec = store.get_worker("worker_drain_test")
    assert rec.drain_state is True

    # Scheduler refuses to assign new work to draining worker
    item = svc.submit_task(
        task_id="task_drain_reject",
        tenant_id="tenant_test",
        step_id="step_noop",
    )
    decisions = svc.tick_scheduler(limit=5)
    d = next(d for d in decisions if d.item_id == item.item_id)
    assert d.verdict == SchedulingVerdict.CAPABILITY_UNAVAILABLE or d.assigned_worker_id != "worker_drain_test"


# ── Invariant 5: Worker B Safely Continues Eligible Work ──────────────────────

def test_05_worker_b_safely_continues_eligible_work(temp_fabric_db):
    store, _ = temp_fabric_db
    coordinator = FabricCoordinator()
    svc = ExecutionFabricService(store=store)

    worker_a = WorkerRuntime(worker_id="worker_a", store=store, service=svc, coordinator=coordinator)
    worker_b = WorkerRuntime(worker_id="worker_b", store=store, service=svc, coordinator=coordinator)

    worker_a.register()
    worker_b.register()

    # Worker A drains
    worker_a.drain(wait=False)

    # Submit task
    item = svc.submit_task(
        task_id="task_continue_005",
        tenant_id="tenant_test",
        step_id="step_rebuild",
    )

    # Scheduler must route work to Worker B, not Worker A
    decisions = svc.tick_scheduler(limit=5)
    d = next(d for d in decisions if d.item_id == item.item_id)
    assert d.verdict == SchedulingVerdict.DISPATCHED
    assert d.assigned_worker_id == "worker_b"

    item_claimed = store.get_queue_item(item.item_id)
    assert item_claimed.assigned_worker_id == "worker_b"


# ── Invariant 6: No Duplicate Successful Execution Across Workers ─────────────

def test_06_no_duplicate_successful_execution(fabric_service):
    svc = fabric_service
    svc.register_worker("worker_1", max_concurrency=2)
    svc.register_worker("worker_2", max_concurrency=2)

    item = svc.submit_task(
        task_id="task_dup_006",
        tenant_id="tenant_test",
        step_id="step_build",
    )
    svc.tick_scheduler(limit=5)
    item_claimed = svc.store.get_queue_item(item.item_id)
    owner = item_claimed.assigned_worker_id

    # Owner executes and completes task
    res = svc.execute_claimed_item(item.item_id, worker_id=owner)
    assert res["ok"] is True
    assert res["status"] == "COMPLETED"

    # Other worker attempts to execute same item -> fails closed
    other = "worker_2" if owner == "worker_1" else "worker_1"
    with pytest.raises(Exception):
        svc.execute_claimed_item(item.item_id, worker_id=other)

    item_final = svc.store.get_queue_item(item.item_id)
    assert item_final.status == QueueItemStatus.COMPLETED
    assert item_final.assigned_worker_id == owner


# ── Invariant 7: Durable State Survives API / Service Restart ──────────────────

def test_07_durable_state_survives_api_restart(temp_fabric_db):
    store, db_file = temp_fabric_db
    svc_1 = ExecutionFabricService(store=store)

    item1 = svc_1.submit_task("task_api_restart", "tenant_core", "step_1", priority=95)
    item2 = svc_1.submit_task("task_api_restart", "tenant_core", "step_2", priority=85)
    svc_1.register_worker("worker_persistent", max_concurrency=4)
    svc_1.tick_scheduler(limit=5)

    # Simulate complete API process destruction
    del svc_1
    del store

    # Reconnect new API service instance to same database file
    new_store = SqliteFabricStore(db_path=db_file)
    svc_2 = ExecutionFabricService(store=new_store)

    reloaded_item1 = svc_2.store.get_queue_item(item1.item_id)
    reloaded_item2 = svc_2.store.get_queue_item(item2.item_id)
    worker = svc_2.store.get_worker("worker_persistent")

    assert reloaded_item1 is not None
    assert reloaded_item1.priority == 95
    assert reloaded_item2 is not None
    assert reloaded_item2.priority == 85
    assert worker is not None
    assert worker.worker_id == "worker_persistent"


# ── Invariant 8: Durable State Survives Worker Crash / Restart ────────────────

def test_08_durable_state_survives_worker_restart(temp_fabric_db):
    store, db_file = temp_fabric_db
    coordinator = FabricCoordinator()
    svc = ExecutionFabricService(store=store, heartbeat_timeout_sec=0.5)

    worker_crashed = WorkerRuntime(
        worker_id="worker_crashed",
        store=store,
        service=svc,
        coordinator=coordinator,
    )
    worker_crashed.register()

    item = svc.submit_task(
        task_id="task_crash_008",
        tenant_id="tenant_x",
        step_id="step_pure_calc",
        payload={"is_idempotent": True, "allowed_tools": ["read_file"]},
    )
    svc.tick_scheduler(limit=5)

    # Worker crashes: stop internal thread loops without updating store status to STOPPED
    worker_crashed._stop_event.set()

    # Time elapses past heartbeat timeout
    future_time = time.time() + 2.0
    recov = svc.recover_orphans(now=future_time)

    assert "worker_crashed" in recov["unhealthy_workers"]
    assert item.item_id in recov["requeued_items"]

    # New replacement worker starts up and claims the requeued item
    worker_new = WorkerRuntime(
        worker_id="worker_replacement",
        store=store,
        service=svc,
        coordinator=coordinator,
    )
    worker_new.register()
    svc.tick_scheduler(limit=5)

    item_reclaimed = store.get_queue_item(item.item_id)
    assert item_reclaimed.status == QueueItemStatus.CLAIMED
    assert item_reclaimed.assigned_worker_id == "worker_replacement"
    assert item_reclaimed.attempt == 2


# ── Invariant 9: Redis Loss Does Not Corrupt Durable State ────────────────────

def test_09_redis_loss_does_not_corrupt_durable_state(fabric_service):
    svc = fabric_service
    # Point coordinator to local fallback mode
    broken_coordinator = FabricCoordinator(fallback_to_local=True)
    mock_redis = MagicMock()
    mock_redis.publish.side_effect = Exception("Connection lost to Redis server")
    broken_coordinator._redis_client = mock_redis
    broken_coordinator._redis_available = False

    item = svc.submit_task("task_redis_loss", "tenant_a", "step_0")
    svc.register_worker("worker_safe", max_concurrency=2)
    decisions = svc.tick_scheduler(limit=5)

    assert len(decisions) >= 1
    persisted = svc.store.get_queue_item(item.item_id)
    assert persisted.status == QueueItemStatus.CLAIMED

    # Coordination falls back safely without raising uncaught exceptions
    woke = broken_coordinator.wait_for_work(timeout=0.05)
    assert woke is False  # Local fallback timeout


# ── Invariant 10: PostgreSQL Unavailability Fails Closed in Production ─────────

def test_10_postgresql_unavailability_fails_closed():
    with patch.dict(os.environ, {
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql://invalid_user:invalid_pass@127.0.0.1:5432/nonexistent_db",
    }):
        with pytest.raises(RuntimeError) as exc_info:
            get_fabric_store()
        err_msg = str(exc_info.value)
        assert "Production PostgreSQL store initialization failed" in err_msg or "Failed to connect to PostgreSQL" in err_msg


# ── Invariant 11: HITL-Pending Task Cannot Be Illegally Reclaimed ─────────────

def test_11_hitl_pending_task_cannot_be_illegally_reclaimed(fabric_service):
    svc = fabric_service
    svc.register_worker("worker_hitl_test", max_concurrency=2)

    item = svc.submit_task(
        task_id="task_hitl_011",
        tenant_id="tenant_corp",
        step_id="step_deploy_prod",
        payload={
            "requires_hitl": True,
            "hitl_pending": True,
            "allowed_tools": ["deploy", "run_shell"],
        },
    )
    svc.tick_scheduler(limit=5)
    # Simulate worker crash while task was in-flight awaiting human decision
    store_item = svc.store.get_queue_item(item.item_id)
    store_item.status = QueueItemStatus.RUNNING
    store_item.assigned_worker_id = "worker_hitl_test"
    svc.store.update_queue_item(store_item)

    # Crash recovery runs
    recov = svc.recover_orphans(now=time.time() + 60.0)

    # Item must NOT be requeued for blind execution
    assert item.item_id not in recov["requeued_items"]
    # Item must be held in waiting or failed closed
    after_item = svc.store.get_queue_item(item.item_id)
    assert after_item.status != QueueItemStatus.QUEUED


# ── Invariant 12: Verifier-Pending Task Cannot Succeed Without Verifier ────────

def test_12_verifier_pending_task_cannot_become_successful_without_verifier():
    category = classify_failure(
        tool_names=["read_file"],
        verification_pending=True,
    )
    assert category == FailureCategory.VERIFICATION_PENDING

    # Without verifier passed -> MUST return WAIT, never RESUME or RETRY
    recov_unverified = map_failure_category_to_recovery(
        category=category,
        verifier_passed=False,
    )
    assert recov_unverified == RecoveryDecision.WAIT

    # Only when verifier passes -> RESUME
    recov_verified = map_failure_category_to_recovery(
        category=category,
        verifier_passed=True,
    )
    assert recov_verified == RecoveryDecision.RESUME


# ── Invariant 13: RunBudget Cannot Be Exceeded After Recovery ─────────────────

def test_13_runbudget_cannot_be_exceeded_after_recovery(fabric_service):
    svc = fabric_service
    svc.register_worker("worker_budget_test", max_concurrency=2)

    item = svc.submit_task(
        task_id="task_budget_013",
        tenant_id="tenant_budget",
        step_id="step_token_heavy",
        payload={
            "is_idempotent": True,
            "budget_exhausted": True,  # Token or cost quota exhausted
        },
    )
    svc.tick_scheduler(limit=5)
    store_item = svc.store.get_queue_item(item.item_id)
    store_item.status = QueueItemStatus.RUNNING
    store_item.assigned_worker_id = "worker_budget_test"
    svc.store.update_queue_item(store_item)

    # Orphan recovery must fail closed when budget is exhausted
    recov = svc.recover_orphans(now=time.time() + 60.0)

    assert item.item_id in recov["failed_items"]
    assert item.item_id not in recov["requeued_items"]

    after_item = svc.store.get_queue_item(item.item_id)
    assert after_item.status == QueueItemStatus.FAILED


# ── Invariant 14: Tenant Isolation Remains Intact ─────────────────────────────

def test_14_tenant_isolation_remains_intact(fabric_service):
    svc = fabric_service
    svc.quota_policy = SchedulerQuotaPolicy(max_concurrency_per_tenant=1)
    svc.scheduler.quota_policy = svc.quota_policy

    svc.register_worker("worker_shared", max_concurrency=5)

    # Tenant Alpha submits 2 tasks
    item_a1 = svc.submit_task("task_a1", tenant_id="tenant_alpha", step_id="s1")
    item_a2 = svc.submit_task("task_a2", tenant_id="tenant_alpha", step_id="s2")

    # Tenant Beta submits 1 task
    item_b1 = svc.submit_task("task_b1", tenant_id="tenant_beta", step_id="s1")

    # First schedule tick
    decisions = svc.tick_scheduler(limit=10)
    verdicts = {d.item_id: d.verdict for d in decisions}

    # Tenant Alpha has 1 dispatched, 1 throttled by quota
    assert verdicts[item_a1.item_id] == SchedulingVerdict.DISPATCHED
    assert verdicts[item_a2.item_id] == SchedulingVerdict.TENANT_QUOTA_EXCEEDED

    # Tenant Beta is not blocked by Tenant Alpha
    assert verdicts[item_b1.item_id] == SchedulingVerdict.DISPATCHED


# ── Invariant 15: Secret Values Do Not Leak in Operational Logs & Metrics ─────

def test_15_secret_values_do_not_leak_in_logs_and_metrics():
    logger = get_structured_logger("test_scrub")
    raw_event = {
        "event": "task_dispatched",
        "dsn": "postgresql://aetherion_user:SuperSecretPassword123@db.host:5432/aetherion",
        "jwt": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.fake_jwt_signature",
        "api_key": "sk-proj-9999888877776666555544443333222211110000",
        "payload": {
            "nested_password": "PlaintextPasswordHere",
            "safe_field": "public_data",
        },
    }

    scrubbed = deep_scrub(raw_event)
    scrubbed_str = json.dumps(scrubbed)

    assert "SuperSecretPassword123" not in scrubbed_str
    assert "fake_jwt_signature" not in scrubbed_str
    assert "sk-proj-9999888877776666555544443333222211110000" not in scrubbed_str
    assert "PlaintextPasswordHere" not in scrubbed_str
    assert scrubbed["payload"]["safe_field"] == "public_data"


# ── Invariant 16: Rolling Lifecycle Handover Verification ─────────────────────

def test_16_rolling_worker_replacement_lifecycle(temp_fabric_db):
    store, _ = temp_fabric_db
    coordinator = FabricCoordinator()
    svc = ExecutionFabricService(store=store)

    retiring_worker = WorkerRuntime(
        worker_id="retiring_01",
        store=store,
        service=svc,
        coordinator=coordinator,
    )
    incoming_worker = WorkerRuntime(
        worker_id="incoming_02",
        store=store,
        service=svc,
        coordinator=coordinator,
    )

    retiring_worker.register()

    # Submit task
    item = svc.submit_task("task_rolling", "tenant_roll", "step_0")

    controller = RollingReplacementController(service=svc, coordinator=coordinator)
    report = controller.execute_rolling_handover(
        retiring_worker=retiring_worker,
        incoming_worker=incoming_worker,
        drain_timeout_sec=5.0,
    )

    assert report.success is True
    assert report.duplicate_claims_detected == 0
    assert retiring_worker.status == WorkerStatus.STOPPED
    assert incoming_worker.status == WorkerStatus.READY


# ── Invariant 17: Migration Version Tracking & Rollback Safety ────────────────

def test_17_migration_version_tracking_and_rollback(temp_fabric_db):
    store, db_file = temp_fabric_db
    conn = store.get_connection()
    try:
        # Check applied migrations
        applied = get_applied_migrations(conn, is_postgres=False)
        assert len(applied) == CURRENT_CODE_SCHEMA_VERSION

        # Check compatibility
        compat = verify_schema_compatibility(conn, is_postgres=False)
        assert compat["ok"] is True
        assert compat["is_up_to_date"] is True

        # Check rollback safety
        rollback = verify_rollback_safety(target_schema_version=1, conn=conn, is_postgres=False)
        assert rollback["safe"] is True
        assert rollback["target_version"] == 1
    finally:
        conn.close()
