"""
tests/integration/test_phase22_control_plane_worker_runtime.py — Phase 22 Integration Suite.

Comprehensive test suite verifying:
- Persistence Adapters (SqliteFabricStore, PostgresFabricStore, get_fabric_store)
- Atomic CAS Queue Claiming and Capacity Updates
- Transient Fabric Coordination (FabricCoordinator)
- Multi-Process Worker Runtime Lifecycle & Graceful Drain
- Multi-Worker Concurrency, Load Balancing, and Capability Routing
- Operator CLI Commands (aetherion worker start, status, drain)
- Production Preflight (check_control_plane) & Health Probes (/api/health/control-plane, /readyz)
- Security Boundaries and Secret Sanitization
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from unittest.mock import MagicMock, patch
import uuid

from click.testing import CliRunner
import pytest

from backend.core.preflight import (
    CheckSeverity,
    CheckStatus,
    check_control_plane,
    run_preflight,
)
from backend.execution.fabric.coordination import FabricCoordinator
from backend.execution.fabric.models import (
    QueueItem,
    QueueItemStatus,
    SchedulingVerdict,
    WorkerRecord,
    WorkerStatus,
)
from backend.execution.fabric.postgres_store import PostgresFabricStore
from backend.execution.fabric.queue import DurableTaskQueue
from backend.execution.fabric.scheduler import ExecutionScheduler, SchedulerQuotaPolicy
from backend.execution.fabric.service import ExecutionFabricService
from backend.execution.fabric.store import (
    BaseFabricStore,
    SqliteFabricStore,
    get_fabric_store,
    sanitize_dsn,
)
from backend.execution.fabric.worker_registry import WorkerRegistry
from backend.execution.fabric.worker_runtime import WorkerRuntime
from cli.aetherion_cli.commands.worker import worker_group
from cli.aetherion_cli.main import cli


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path):
    """Isolated SQLite store for tests."""
    db_file = tmp_path / f"fabric_{uuid.uuid4().hex[:8]}.db"
    store = SqliteFabricStore(db_path=db_file)
    return store


@pytest.fixture
def coordinator():
    """Isolated coordinator with in-process fallback."""
    coord = FabricCoordinator(fallback_to_local=True)
    yield coord
    coord.close()


@pytest.fixture
def fabric_service(temp_db):
    """Fabric service configured with isolated temp DB."""
    return ExecutionFabricService(store=temp_db)


# ── 1. Persistence Adapters & Atomic CAS ──────────────────────────────────────

class TestPersistenceAdaptersAndFactory:
    def test_sqlite_fabric_store_wal_and_crud(self, temp_db):
        """Verify SqliteFabricStore initializes with WAL and supports standard CRUD."""
        conn = temp_db.get_connection()
        try:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            assert mode.upper() == "WAL"
        finally:
            conn.close()

        item = QueueItem(
            item_id="item_001",
            task_id="task_100",
            tenant_id="tenant_alpha",
            priority=80,
            execution_class="HIGH",
        )
        temp_db.insert_queue_item(item)

        fetched = temp_db.get_queue_item("item_001")
        assert fetched is not None
        assert fetched.item_id == "item_001"
        assert fetched.priority == 80
        assert fetched.status == QueueItemStatus.QUEUED

    def test_atomic_cas_claim_item(self, temp_db):
        """Verify atomic CAS item claiming prevents double-claiming race condition."""
        item = QueueItem(
            item_id="race_item_1",
            task_id="task_race",
            tenant_id="tenant_race",
            status=QueueItemStatus.QUEUED,
        )
        temp_db.insert_queue_item(item)

        # Worker 1 claims item
        won_1 = temp_db.claim_item_atomic(
            item_id="race_item_1",
            worker_id="worker_1",
            lease_id="lease_1",
        )
        assert won_1 is True

        # Worker 2 attempts to claim the same item (CAS must fail)
        won_2 = temp_db.claim_item_atomic(
            item_id="race_item_1",
            worker_id="worker_2",
            lease_id="lease_2",
        )
        assert won_2 is False

        # Verify state in DB
        current = temp_db.get_queue_item("race_item_1")
        assert current.status == QueueItemStatus.CLAIMED
        assert current.assigned_worker_id == "worker_1"
        assert current.lease_id == "lease_1"

    def test_atomic_worker_capacity_updates(self, temp_db):
        """Verify update_worker_capacity_atomic adheres to concurrency bounds."""
        worker = WorkerRecord(
            worker_id="worker_cap_test",
            session_id="sess_1",
            process_id=1234,
            max_concurrency=2,
            active_concurrency=0,
            status=WorkerStatus.READY,
        )
        temp_db.upsert_worker(worker)

        # +1 slot -> active = 1
        ok = temp_db.update_worker_capacity_atomic("worker_cap_test", delta=1, max_concurrency=2, lease_id="l1")
        assert ok is True
        w = temp_db.get_worker("worker_cap_test")
        assert w.active_concurrency == 1
        assert w.status == WorkerStatus.READY

        # +1 slot -> active = 2 (BUSY)
        ok = temp_db.update_worker_capacity_atomic("worker_cap_test", delta=1, max_concurrency=2, lease_id="l2")
        assert ok is True
        w = temp_db.get_worker("worker_cap_test")
        assert w.active_concurrency == 2
        assert w.status == WorkerStatus.BUSY

        # Exceeding max concurrency (+1) must fail
        ok = temp_db.update_worker_capacity_atomic("worker_cap_test", delta=1, max_concurrency=2, lease_id="l3")
        assert ok is False

        # Release 1 slot -> active = 1 (READY)
        ok = temp_db.update_worker_capacity_atomic("worker_cap_test", delta=-1, max_concurrency=2, lease_id="l1", add_lease=False)
        assert ok is True
        w = temp_db.get_worker("worker_cap_test")
        assert w.active_concurrency == 1
        assert w.status == WorkerStatus.READY

    def test_get_fabric_store_factory_local_vs_production(self, monkeypatch, tmp_path):
        """Verify store factory selects SQLite in dev/test and fails closed in production."""
        # Dev / test environment: returns SqliteFabricStore
        monkeypatch.setenv("AETHERION_ENV", "local-development")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        store = get_fabric_store(db_path=tmp_path / "dev.db")
        assert isinstance(store, SqliteFabricStore)

        # Production environment without PostgreSQL must fail closed
        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(RuntimeError) as exc_info:
            get_fabric_store()
        assert "Production and staging environments require PostgreSQL and fail closed" in str(exc_info.value)

    def test_postgres_store_dsn_sanitization(self):
        """Verify DSN masking function hides cleartext credentials."""
        secret_dsn = "postgresql://fabric_user:super_secret_password_123@db.prod.internal:5432/fabric"
        sanitized = sanitize_dsn(secret_dsn)
        assert "super_secret_password_123" not in sanitized
        assert "postgresql://fabric_user:***@db.prod.internal:5432/fabric" == sanitized


# ── 2. Transient Coordination Layer ──────────────────────────────────────────

class TestFabricCoordinator:
    def test_work_notification_wakeup_and_timeout(self, coordinator):
        """Verify notify_work_available immediately wakes up a waiting worker."""
        # Timeout with no work
        t0 = time.monotonic()
        woke_up = coordinator.wait_for_work(timeout=0.1)
        elapsed = time.monotonic() - t0
        assert woke_up is False
        assert elapsed >= 0.09

        # Notification wake up
        results = []
        def _waiter():
            got = coordinator.wait_for_work(timeout=1.0)
            results.append(got)

        t = threading.Thread(target=_waiter)
        t.start()
        time.sleep(0.05)
        coordinator.notify_work_available()
        t.join(timeout=1.0)

        assert results == [True]

    def test_transient_heartbeats_and_stop_signals(self, coordinator):
        """Verify heartbeats and stop signals function across the coordinator."""
        worker_id = f"w_{uuid.uuid4().hex[:6]}"

        coordinator.publish_worker_heartbeat(worker_id, metadata={"load": 0.5})
        hb = coordinator.get_worker_heartbeat(worker_id)
        assert hb is not None
        assert hb["worker_id"] == worker_id
        assert hb["metadata"]["load"] == 0.5

        # Stop signal
        assert coordinator.should_stop(worker_id) is False
        coordinator.request_stop(worker_id)
        assert coordinator.should_stop(worker_id) is True

        coordinator.clear_stop(worker_id)
        assert coordinator.should_stop(worker_id) is False


# ── 3. WorkerRuntime Lifecycle & Graceful Drain ──────────────────────────────

class TestWorkerRuntimeLifecycle:
    def test_worker_registration_and_background_heartbeat(self, temp_db, coordinator):
        """Verify WorkerRuntime registers itself and sends background heartbeats."""
        worker_id = f"worker_hb_{uuid.uuid4().hex[:6]}"
        runtime = WorkerRuntime(
            worker_id=worker_id,
            max_concurrency=2,
            store=temp_db,
            coordinator=coordinator,
            heartbeat_interval_sec=0.2,
        )
        runtime.start()
        try:
            assert runtime.status in (WorkerStatus.READY, WorkerStatus.STARTING)
            time.sleep(0.5)

            rec = temp_db.get_worker(worker_id)
            assert rec is not None
            assert rec.status == WorkerStatus.READY
            assert rec.last_heartbeat > 0
            assert rec.max_concurrency == 2
        finally:
            runtime.stop()

        assert runtime.status == WorkerStatus.STOPPED

    def test_worker_executes_claimed_item(self, temp_db, coordinator):
        """Verify WorkerRuntime picks up claimed work and executes it to completion."""
        worker_id = f"worker_exec_{uuid.uuid4().hex[:6]}"
        executed_event = threading.Event()
        executed_items = []

        def mock_executor(item: QueueItem):
            executed_items.append(item.item_id)
            executed_event.set()
            return {"status": "SUCCESS"}


        service = ExecutionFabricService(store=temp_db)
        runtime = WorkerRuntime(
            worker_id=worker_id,
            service=service,
            store=temp_db,
            coordinator=coordinator,
            executor_fn=mock_executor,
            min_poll_interval_sec=0.1,
        )
        runtime.start()

        try:
            # Enqueue an item
            item = service.submit_task(
                task_id="task_exec_1",
                tenant_id="tenant_test",
                priority=100,
            )
            # Notify coordinator
            coordinator.notify_work_available()

            # Wait for execution deterministically via event
            assert executed_event.wait(timeout=10.0) is True
            assert item.item_id in executed_items

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and runtime.items_succeeded == 0:
                time.sleep(0.05)

            assert runtime.items_succeeded >= 1

            # Check queue item status in store
            updated = temp_db.get_queue_item(item.item_id)
            assert updated.status == QueueItemStatus.COMPLETED


        finally:
            runtime.stop()

    def test_graceful_drain_waits_for_inflight_execution(self, temp_db, coordinator):
        """Verify graceful drain does not abort in-flight work and stops cleanly."""
        worker_id = f"worker_drain_{uuid.uuid4().hex[:6]}"
        executing = threading.Event()
        can_finish = threading.Event()
        completed = []

        def slow_executor(item: QueueItem):
            executing.set()
            can_finish.wait(timeout=5.0)
            completed.append(item.item_id)
            return {"status": "SUCCESS"}

        service = ExecutionFabricService(store=temp_db)
        runtime = WorkerRuntime(
            worker_id=worker_id,
            service=service,
            store=temp_db,
            coordinator=coordinator,
            executor_fn=slow_executor,
            min_poll_interval_sec=0.1,
        )
        runtime.start()

        try:
            # Submit task
            item = service.submit_task(task_id="task_slow", tenant_id="tenant_1")
            coordinator.notify_work_available()

            # Wait until execution has started
            assert executing.wait(timeout=3.0) is True
            assert runtime.active_concurrency == 1

            # Initiate drain asynchronously
            drain_thread = threading.Thread(target=lambda: runtime.drain(wait=True, timeout=5.0))
            drain_thread.start()
            time.sleep(0.2)

            assert runtime.is_draining is True
            assert runtime.status == WorkerStatus.DRAINING

            # Release the execution
            can_finish.set()
            drain_thread.join(timeout=3.0)

            assert item.item_id in completed
            assert runtime.status == WorkerStatus.STOPPED
            assert runtime.active_concurrency == 0
        finally:
            runtime.stop()


# ── 4. Multi-Worker Control Plane ────────────────────────────────────────────

class TestMultiWorkerControlPlane:
    def test_multi_worker_load_balancing_and_capacity(self, temp_db, coordinator):
        """Verify scheduler balances load across multiple active workers."""
        service = ExecutionFabricService(store=temp_db)

        # Register two workers with capacity 2 each
        w1 = WorkerRuntime(worker_id="w1", max_concurrency=2, service=service, store=temp_db, coordinator=coordinator)
        w2 = WorkerRuntime(worker_id="w2", max_concurrency=2, service=service, store=temp_db, coordinator=coordinator)
        w1.register()
        w2.register()

        # Submit 3 tasks
        i1 = service.submit_task(task_id="t1", tenant_id="tenant_a", priority=90)
        i2 = service.submit_task(task_id="t2", tenant_id="tenant_a", priority=80)
        i3 = service.submit_task(task_id="t3", tenant_id="tenant_a", priority=70)

        # Tick scheduler
        decisions = service.tick_scheduler(limit=10)
        dispatched = [d for d in decisions if d.verdict == SchedulingVerdict.DISPATCHED]
        assert len(dispatched) == 3

        assigned_workers = {d.assigned_worker_id for d in dispatched}
        # Both workers should have received items (load-balanced)
        assert "w1" in assigned_workers
        assert "w2" in assigned_workers

    def test_capability_routing_to_specialized_worker(self, temp_db, coordinator):
        """Verify items with required capabilities route only to capable workers."""
        service = ExecutionFabricService(store=temp_db)

        w_cpu = WorkerRuntime(
            worker_id="worker_cpu",
            max_concurrency=2,
            supported_capabilities={"CPU"},
            service=service,
            store=temp_db,
            coordinator=coordinator,
        )
        w_gpu = WorkerRuntime(
            worker_id="worker_gpu",
            max_concurrency=2,
            supported_capabilities={"GPU", "CUDA"},
            service=service,
            store=temp_db,
            coordinator=coordinator,
        )
        w_cpu.register()
        w_gpu.register()

        # Submit item requiring GPU
        item_gpu = service.submit_task(
            task_id="task_cuda",
            tenant_id="tenant_gpu",
            required_capabilities=["GPU"],
        )

        decisions = service.tick_scheduler()
        dispatched = [d for d in decisions if d.item_id == item_gpu.item_id]
        assert len(dispatched) == 1
        assert dispatched[0].verdict == SchedulingVerdict.DISPATCHED
        assert dispatched[0].assigned_worker_id == "worker_gpu"


# ── 5. Operator CLI Commands ─────────────────────────────────────────────────

class TestOperatorCLI:
    def test_cli_worker_status_json(self, temp_db, monkeypatch):
        """Verify 'aetherion worker status --json' outputs structured worker list."""
        monkeypatch.setattr("backend.execution.fabric.store.get_fabric_store", lambda: temp_db)

        temp_db.upsert_worker(
            WorkerRecord(
                worker_id="cli_w_1",
                session_id="s1",
                process_id=999,
                max_concurrency=4,
                active_concurrency=1,
                status=WorkerStatus.READY,
            )
        )

        runner = CliRunner()
        result = runner.invoke(worker_group, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        workers_list = data["data"] if isinstance(data, dict) and "data" in data else data
        assert isinstance(workers_list, list)
        w = next(item for item in workers_list if item["worker_id"] == "cli_w_1")
        assert w["concurrency"] == "1/4"
        assert w["status"] == "READY"

    def test_cli_worker_drain_command(self, temp_db, monkeypatch):
        """Verify 'aetherion worker drain <worker-id>' initiates graceful drain."""
        monkeypatch.setattr("backend.execution.fabric.store.get_fabric_store", lambda: temp_db)

        temp_db.upsert_worker(
            WorkerRecord(
                worker_id="cli_w_drain",
                session_id="s2",
                process_id=1000,
                max_concurrency=4,
                active_concurrency=0,
                status=WorkerStatus.READY,
            )
        )

        runner = CliRunner()
        result = runner.invoke(worker_group, ["drain", "cli_w_drain", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        payload = data["data"] if isinstance(data, dict) and "data" in data else data
        assert payload["worker_id"] == "cli_w_drain"
        assert payload["status"] in ("DRAINING", "STOPPED")


    def test_cli_worker_drain_nonexistent_fails(self, temp_db, monkeypatch):
        """Verify draining a nonexistent worker returns an error exit code."""
        monkeypatch.setattr("backend.execution.fabric.store.get_fabric_store", lambda: temp_db)

        runner = CliRunner()
        result = runner.invoke(worker_group, ["drain", "nonexistent_worker_xyz", "--json"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_cli_worker_start_with_timeout(self, temp_db, monkeypatch):
        """Verify 'aetherion worker start --timeout' starts and cleanly stops."""
        monkeypatch.setattr("backend.execution.fabric.store.get_fabric_store", lambda: temp_db)

        runner = CliRunner()
        result = runner.invoke(
            worker_group,
            ["start", "--worker-id", "cli_test_timed", "--timeout", "0.2", "--json"],
        )
        assert result.exit_code == 0


# ── 6. Production Preflight & Health Integrations ────────────────────────────

class TestPreflightAndHealthIntegrations:
    def test_preflight_check_control_plane_pass(self):
        """Verify check_control_plane passes in valid test environment."""
        chk = check_control_plane(probe_live=True)
        assert chk.check_id == "control_plane.fabric"
        assert chk.status == CheckStatus.PASS
        assert chk.severity == CheckSeverity.INFO
        assert "Execution Fabric store verified" in chk.detail or "configured" in chk.detail

    def test_preflight_check_control_plane_fails_closed_in_production(self, monkeypatch):
        """Verify check_control_plane fails closed when PostgreSQL is missing in production."""
        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.delenv("DATABASE_URL", raising=False)

        chk = check_control_plane(probe_live=False)
        assert chk.status == CheckStatus.FAIL
        assert chk.severity == CheckSeverity.CRITICAL
        assert chk.required is True
        assert "require a PostgreSQL-backed Execution Fabric store" in chk.detail

    def test_health_control_plane_endpoint(self, temp_db, monkeypatch):
        """Verify GET /api/health/control-plane exposes structured health payload."""
        monkeypatch.setattr("backend.execution.fabric.store.get_fabric_store", lambda: temp_db)

        from flask import Flask
        from backend.routes.health import health_bp

        app = Flask(__name__)
        app.register_blueprint(health_bp)

        client = app.test_client()
        resp = client.get("/api/health/control-plane")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data["ok"] is True
        assert "store" in data
        assert "coordinator" in data
        assert "check" in data
        assert data["store"]["type"] == "SqliteFabricStore"

    def test_readyz_includes_control_plane(self):
        """Verify Kubernetes readiness probe /readyz includes control_plane.fabric."""
        from flask import Flask
        from backend.routes.health import health_bp

        app = Flask(__name__)
        app.register_blueprint(health_bp)

        client = app.test_client()
        resp = client.get("/readyz")
        data = resp.get_json()

        assert "checks" in data
        assert "control_plane.fabric" in data["checks"]
        assert data["checks"]["control_plane.fabric"]["status"] in ("PASS", "WARN")


# ── 7. Security Boundaries & Isolation ───────────────────────────────────────

class TestSecurityAndIsolation:
    def test_preflight_audit_scrubs_secrets_from_control_plane_diagnostics(self, monkeypatch):
        """Verify preflight audit strips DB password from any control plane messages."""
        secret_password = "very_secret_pg_password_999"
        monkeypatch.setenv("DATABASE_URL", f"postgresql://usr:{secret_password}@pg.host:5432/db")

        report = run_preflight(probe_live=False)
        for check in report.checks:
            assert secret_password not in check.detail
            if check.remediation:
                assert secret_password not in check.remediation

    def test_multi_tenant_concurrency_isolation(self, temp_db, coordinator):
        """Verify scheduler enforces per-tenant concurrency quotas independently."""
        policy = SchedulerQuotaPolicy(max_concurrency_per_tenant=1)
        service = ExecutionFabricService(store=temp_db, quota_policy=policy)

        # Worker with capacity 4
        w = WorkerRuntime(worker_id="w_iso", max_concurrency=4, service=service, store=temp_db, coordinator=coordinator)
        w.register()

        # Tenant 1 submits 2 items
        service.submit_task(task_id="t1_1", tenant_id="tenant_1")
        service.submit_task(task_id="t1_2", tenant_id="tenant_1")

        # Tenant 2 submits 1 item
        service.submit_task(task_id="t2_1", tenant_id="tenant_2")

        # Scheduler evaluation
        decisions = service.tick_scheduler()

        # Tenant 1 should have 1 DISPATCHED and 1 TENANT_QUOTA_EXCEEDED
        t1_decs = [d for d in decisions if d.tenant_id == "tenant_1"]
        t1_verdicts = {d.verdict for d in t1_decs}
        assert SchedulingVerdict.DISPATCHED in t1_verdicts
        assert SchedulingVerdict.TENANT_QUOTA_EXCEEDED in t1_verdicts

        # Tenant 2 should have 1 DISPATCHED (not blocked by Tenant 1)
        t2_decs = [d for d in decisions if d.tenant_id == "tenant_2"]
        assert len(t2_decs) == 1
        assert t2_decs[0].verdict == SchedulingVerdict.DISPATCHED
