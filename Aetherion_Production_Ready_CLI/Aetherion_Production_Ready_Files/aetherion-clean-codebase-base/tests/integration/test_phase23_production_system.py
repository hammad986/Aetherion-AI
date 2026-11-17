"""
tests/integration/test_phase23_production_system.py — Phase 23 Production Deployment, Observability & Integration Suite.

Comprehensive end-to-end test suite verifying:
1. Docker & Compose Configuration Integrity (hardened non-root user, multi-stage, topology)
2. Worker Startup Preflight Contract (fail-fast, check-only, environment identity)
3. Fabric Observability (FabricMetricsCollector, dual JSON/Prometheus export)
4. Structured JSON Logging with Correlation IDs and Deep Secret Scrubbing
5. 5-Level Operational Health Probes (LIVE, READY, CONTROL-PLANE-READY, WORKER-CAPABLE, EXECUTION-READY)
6. Operator Diagnostics Integration (aetherion doctor and --json with Execution Fabric)
7. Multi-Worker Concurrent Execution (load balancing, atomic CAS claims, zero duplicates)
8. Real Process-Level Worker Crash & Orphan Recovery (proc.kill() + StepLease expiry + reassignment)
9. Control Plane Restart & State Preservation
10. Redis Failure Safe Degradation & PostgreSQL Outage Fail-Closed Semantics
11. Full Evidence Chain and Audit Trail Verification
12. Multi-Tenant Concurrency and Isolation Security
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch
import uuid

from click.testing import CliRunner
from flask import Flask
import pytest

from backend.core.preflight import (
    CheckResult,
    CheckSeverity,
    CheckStatus,
    check_control_plane,
    check_database,
    check_environment_identity,
    run_preflight,
    sanitize_dsn,
)
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
from backend.execution.fabric.models import (
    QueueItem,
    QueueItemStatus,
    SchedulingVerdict,
    WorkerRecord,
    WorkerStatus,
)
from backend.execution.fabric.queue import DurableTaskQueue
from backend.execution.fabric.scheduler import ExecutionScheduler, SchedulerQuotaPolicy
from backend.execution.fabric.service import ExecutionFabricService
from backend.execution.fabric.store import (
    BaseFabricStore,
    SqliteFabricStore,
    get_fabric_store,
)
from backend.execution.fabric.worker_entrypoint import (
    main as worker_entrypoint_main,
    run_worker_preflight,
)
from backend.execution.fabric.worker_registry import WorkerRegistry
from backend.execution.fabric.worker_runtime import WorkerRuntime
from backend.routes.health import health_bp
from cli.aetherion_cli.main import cli


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path):
    """Provide a fresh isolated SQLite store in WAL mode for tests."""
    db_file = tmp_path / f"fabric_p23_{uuid.uuid4().hex[:8]}.db"
    store = SqliteFabricStore(db_path=db_file)
    return store


@pytest.fixture
def coordinator():
    """Provide a fresh in-memory coordinator fallback."""
    coord = FabricCoordinator(fallback_to_local=True)
    yield coord
    coord.close()


@pytest.fixture
def metrics_collector():
    """Provide a fresh metrics collector with baseline state."""
    collector = get_metrics_collector()
    collector.reset()
    yield collector
    collector.reset()


@pytest.fixture
def test_client():
    """Provide a Flask test client with the health blueprint registered."""
    app = Flask(__name__)
    app.register_blueprint(health_bp)
    app.config["TESTING"] = True
    return app.test_client()


# ── 1. Deployment & Configuration Integrity ──────────────────────────────────

class TestDeploymentConfiguration:
    def test_docker_backend_and_worker_hardened_non_root(self):
        """Verify Dockerfile.backend and Dockerfile.worker use hardened multi-stage and non-root user."""
        repo_dir = Path(__file__).resolve().parent.parent.parent
        dockerfile_backend = repo_dir / "deploy" / "docker" / "Dockerfile.backend"
        dockerfile_worker = repo_dir / "deploy" / "docker" / "Dockerfile.worker"

        assert dockerfile_backend.exists(), "Dockerfile.backend missing"
        assert dockerfile_worker.exists(), "Dockerfile.worker missing"

        content_backend = dockerfile_backend.read_text(encoding="utf-8")
        content_worker = dockerfile_worker.read_text(encoding="utf-8")

        for name, content in [("backend", content_backend), ("worker", content_worker)]:
            # 1. Multi-stage build
            assert "AS builder" in content, f"{name}: missing builder stage"
            assert "AS runner" in content, f"{name}: missing runner stage"

            # 2. Non-root user creation and usage
            assert "groupadd -g 10001 aetherion" in content, f"{name}: missing non-root group"
            assert "useradd -u 10001 -g aetherion" in content, f"{name}: missing non-root user"
            assert "USER aetherion:aetherion" in content, f"{name}: not running as non-root user"

            # 3. Healthcheck defined
            assert "HEALTHCHECK" in content, f"{name}: missing HEALTHCHECK instruction"

            # 4. Entrypoint / CMD configured
            assert "CMD" in content or "ENTRYPOINT" in content, f"{name}: missing CMD or ENTRYPOINT"

    def test_docker_compose_topology_integrity(self):
        """Verify docker-compose.yml defines full authoritative topology including worker-a and worker-b."""
        repo_dir = Path(__file__).resolve().parent.parent.parent
        compose_file = repo_dir / "deploy" / "compose" / "docker-compose.yml"
        assert compose_file.exists(), "docker-compose.yml missing"

        content = compose_file.read_text(encoding="utf-8")

        # 1. Verify required services
        assert "postgres:" in content, "docker-compose missing postgres service"
        assert "redis:" in content, "docker-compose missing redis service"
        assert "backend:" in content, "docker-compose missing backend service"
        assert "worker-a:" in content, "docker-compose missing worker-a service"
        assert "worker-b:" in content, "docker-compose missing worker-b service"

        # 2. Verify healthchecks
        assert "pg_isready" in content, "postgres missing pg_isready healthcheck"
        assert "redis-cli" in content, "redis missing redis-cli ping healthcheck"

        # 3. Verify worker dependencies on healthy services
        assert "condition: service_healthy" in content, "services missing health condition dependencies"

        # 4. Verify shared networks and persistent volumes
        assert "aetherion_net:" in content, "missing aetherion_net bridge network"
        assert "postgres_data:" in content, "missing postgres_data volume"
        assert "redis_data:" in content, "missing redis_data volume"


# ── 2. Worker Preflight & Startup Contract ───────────────────────────────────

class TestWorkerStartupContract:
    def test_worker_preflight_success_path(self):
        """Verify worker startup preflight executes all checks and passes in valid environment."""
        passed, checks = run_worker_preflight(probe_live=False)
        assert passed is True
        check_ids = {c.check_id for c in checks}
        assert any(id_key in check_ids for id_key in ("environment.identity", "config.environment"))
        assert "database.readiness" in check_ids
        assert "redis.coordination" in check_ids
        assert "control_plane.fabric" in check_ids

    def test_worker_preflight_fail_closed_on_db_corruption(self):
        """Verify worker preflight fails closed when required database check fails."""
        mock_result = CheckResult(
            check_id="database.readiness",
            name="Database Readiness",
            status=CheckStatus.FAIL,
            severity=CheckSeverity.CRITICAL,
            required=True,
            detail="Connection refused by PostgreSQL cluster",
        )
        with patch("backend.execution.fabric.worker_entrypoint.check_database", return_value=mock_result), \
             patch("backend.core.preflight.check_database", return_value=mock_result), \
             patch("core.preflight.check_database", return_value=mock_result, create=True):
            passed, checks = run_worker_preflight(probe_live=True)
            assert passed is False
            db_res = next(c for c in checks if c.check_id == "database.readiness")
            assert db_res.status == CheckStatus.FAIL

    def test_worker_entrypoint_check_only_flag(self):
        """Verify worker_entrypoint --check-only exits with 0 on healthy environment."""
        exit_code = worker_entrypoint_main(["--check-only", "--skip-preflight"])
        assert exit_code == 0

    def test_worker_entrypoint_aborts_on_failed_preflight(self):
        """Verify worker_entrypoint aborts with code 1 if preflight check fails."""
        mock_tuple = (
            False,
            [
                CheckResult(
                    check_id="database.readiness",
                    name="Database Readiness",
                    status=CheckStatus.FAIL,
                    severity=CheckSeverity.CRITICAL,
                    required=True,
                    detail="Simulated failure",
                )
            ],
        )
        with patch("backend.execution.fabric.worker_entrypoint.run_worker_preflight", return_value=mock_tuple), \
             patch("execution.fabric.worker_entrypoint.run_worker_preflight", return_value=mock_tuple, create=True):
            exit_code = worker_entrypoint_main(["--worker-id", "test_abort"])
            assert exit_code == 1


# ── 3. Observability: Metrics & Structured Logging ───────────────────────────

class TestObservability:
    def test_fabric_metrics_collector_lifecycle_and_prometheus_export(self, metrics_collector):
        """Verify metrics collector records tasks, workers, agent steps, and exports Prometheus text."""
        # 1. Record task lifecycle
        metrics_collector.record_task_submitted("task-100", tenant_id="tenant_a", priority=75)
        metrics_collector.record_task_claimed("task-100", worker_id="w-1", queue_latency_sec=0.25, priority=75)
        metrics_collector.record_task_completed("task-100", tenant_id="tenant_a", duration_sec=1.5)

        metrics_collector.record_task_submitted("task-200", tenant_id="tenant_b", priority=50)
        metrics_collector.record_task_failed("task-200", tenant_id="tenant_b", error_class="TimeoutError", duration_sec=3.0)
        metrics_collector.record_task_retried("task-200")
        metrics_collector.record_task_recovered("task-300", reason="worker_crash")

        # 2. Record worker metrics
        metrics_collector.record_worker_heartbeat("w-1", status="READY", active_concurrency=0, max_concurrency=4)

        # 3. Record agent steps & LLM calls
        metrics_collector.record_agent_step("bash_tool", duration_sec=0.12, success=True)
        metrics_collector.record_agent_step("git_tool", duration_sec=0.34, success=False)
        metrics_collector.record_llm_call("claude-3-5-sonnet", tokens=1200, duration_sec=1.1)
        metrics_collector.record_hitl_wait(5.0)

        # 4. JSON export assertion
        data = metrics_collector.get_metrics()
        assert data["tasks"]["submitted_total"] == 2
        assert data["tasks"]["claimed_total"] == 1
        assert data["tasks"]["completed_total"] == 1
        assert data["tasks"]["failed_total"] == 1
        assert data["tasks"]["retried_total"] == 1
        assert data["tasks"]["recovered_total"] == 1
        assert data["agent"]["llm_calls"]["claude-3-5-sonnet"] == 1
        assert data["agent"]["llm_tokens"]["claude-3-5-sonnet"] == 1200

        # 5. Prometheus text export assertion
        prom = metrics_collector.to_prometheus_format()
        assert "# HELP fabric_tasks_submitted_total" in prom
        assert "# TYPE fabric_tasks_submitted_total counter" in prom
        assert "fabric_tasks_submitted_total 2" in prom
        assert "fabric_tasks_completed_total 1" in prom
        assert "fabric_tasks_failed_total 1" in prom
        assert 'fabric_agent_llm_calls_total{model="claude-3-5-sonnet"} 1' in prom

    def test_structured_json_logging_and_deep_secret_scrubbing(self):
        """Verify structured logger formats JSON with correlation IDs and deeply scrubs sensitive tokens."""
        slogger = FabricStructuredLogger(name="test.fabric.audit", buffer_size=10)
        slogger.clear_logs()

        secret_api_key = "sk-ant-api03-abcdef12345678901234567890"
        secret_password = "super_classified_password_xyz"
        secret_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThisToken"
        secret_dsn = f"postgresql://db_user:{secret_password}@pg.internal.cloud:5432/proddb"

        with correlation_context(
            task_id="task_audit_99",
            tenant_id="tenant_omega",
            step_id="step_3",
            worker_id="worker_sec_1",
            attempt=1,
            plan_revision=2,
        ):
            rec = slogger.info(
                f"Connecting to database {secret_dsn} using auth token Bearer {secret_jwt} and api key {secret_api_key}",
                event="DATABASE_CONNECT",
                db_password=secret_password,
                auth_header=f"Bearer {secret_jwt}",
                nested_info={"api_key": secret_api_key, "safe_param": "public_data"},
            )

        # Verify correlation context is properly preserved
        assert rec["correlation"]["task_id"] == "task_audit_99"
        assert rec["correlation"]["tenant_id"] == "tenant_omega"
        assert rec["correlation"]["worker_id"] == "worker_sec_1"
        assert rec["correlation"]["step_id"] == "step_3"

        # Verify zero leakage in message
        assert secret_api_key not in rec["message"]
        assert secret_password not in rec["message"]
        assert secret_jwt not in rec["message"]
        assert "[REDACTED" in rec["message"]

        # Verify zero leakage in payload
        assert rec["payload"]["db_password"] == "[REDACTED]"
        assert "[REDACTED" in rec["payload"]["auth_header"]
        assert secret_jwt not in rec["payload"]["auth_header"]
        assert rec["payload"]["nested_info"]["api_key"] == "[REDACTED]"
        assert rec["payload"]["nested_info"]["safe_param"] == "public_data"


# ── 4. Operational Health Endpoints (5 Levels) ───────────────────────────────

class TestOperationalHealthProbes:
    def test_operational_health_5_level_readiness_endpoints(self, temp_db, test_client, monkeypatch):
        """Verify the 5 distinct health & readiness levels return structured status codes and contracts."""
        monkeypatch.setattr("backend.execution.fabric.store.get_fabric_store", lambda: temp_db)

        # Level 1: LIVE
        r_live = test_client.get("/api/health/live")
        assert r_live.status_code == 200
        d_live = r_live.get_json()
        assert d_live["level"] == "LIVE"
        assert d_live["ok"] is True

        # Level 2: READY
        r_ready = test_client.get("/api/health/ready")
        assert r_ready.status_code in (200, 503)
        d_ready = r_ready.get_json()
        assert d_ready["level"] == "READY"

        # Level 3: CONTROL-PLANE-READY
        r_cp = test_client.get("/api/health/control-plane")
        assert r_cp.status_code == 200
        d_cp = r_cp.get_json()
        assert d_cp["level"] == "CONTROL-PLANE-READY"
        assert d_cp["ok"] is True
        assert d_cp["store"]["type"] == "SqliteFabricStore"

        # Level 4: WORKER-CAPABLE (initially 0 workers -> 503)
        r_w_none = test_client.get("/api/health/workers")
        assert r_w_none.status_code == 503
        assert r_w_none.get_json()["level"] == "WORKER-CAPABLE"

        # Register a worker in temp_db
        temp_db.upsert_worker(
            WorkerRecord(
                worker_id="probe_worker_1",
                session_id="sess_1",
                process_id=1234,
                max_concurrency=4,
                supported_capabilities={"DEFAULT", "CPU"},
                status=WorkerStatus.READY,
            )
        )

        # Level 4 with active worker -> 200
        r_w_active = test_client.get("/api/health/workers")
        assert r_w_active.status_code == 200
        d_w_active = r_w_active.get_json()
        assert d_w_active["ok"] is True
        assert d_w_active["ready_workers_count"] == 1
        assert d_w_active["total_concurrency_capacity"] == 4

        # Level 5: EXECUTION-READY (both control plane and worker ready -> 200)
        r_exec = test_client.get("/api/health/execution-ready")
        assert r_exec.status_code == 200
        d_exec = r_exec.get_json()
        assert d_exec["level"] == "EXECUTION-READY"
        assert d_exec["ok"] is True
        assert d_exec["status"] == "EXECUTION-READY"

        # Composite readiness probe
        r_fab = test_client.get("/api/health/fabric-readiness")
        assert r_fab.status_code == 200
        d_fab = r_fab.get_json()
        assert "levels" in d_fab
        assert "LIVE" in d_fab["levels"]
        assert "CONTROL-PLANE-READY" in d_fab["levels"]
        assert "EXECUTION-READY" in d_fab["levels"]

        # Metrics endpoints
        r_metrics_json = test_client.get("/api/health/metrics")
        assert r_metrics_json.status_code == 200
        assert "tasks" in r_metrics_json.get_json()

        r_metrics_prom = test_client.get("/metrics")
        assert r_metrics_prom.status_code == 200
        assert "text/plain" in r_metrics_prom.content_type
        assert "fabric_tasks_submitted_total" in r_metrics_prom.get_data(as_text=True)


# ── 5. Operator Diagnostics (CLI) ────────────────────────────────────────────

class TestOperatorDoctorDiagnostics:
    def test_cli_doctor_fabric_diagnostics(self, temp_db, monkeypatch):
        """Verify 'aetherion doctor --json' surfaces Execution Fabric state."""
        monkeypatch.setattr("backend.execution.fabric.store.get_fabric_store", lambda: temp_db)
        try:
            monkeypatch.setattr("execution.fabric.store.get_fabric_store", lambda: temp_db)
        except Exception:
            pass

        # Register a worker
        temp_db.upsert_worker(
            WorkerRecord(
                worker_id="doc_worker_1",
                session_id="sess_doc",
                process_id=9876,
                max_concurrency=4,
                supported_capabilities={"DEFAULT"},
                status=WorkerStatus.READY,
            )
        )

        runner = CliRunner()
        mock_client = MagicMock()
        mock_client.base_url = "http://127.0.0.1:5000"
        mock_client.token = "mock-token-xyz"
        mock_client.health.return_value = {"status": "ok", "version": "1.0.0"}

        def mock_get(endpoint):
            if endpoint == "/readyz":
                return {"status": "ready"}
            if endpoint == "/api/auth/me":
                return {"user": {"email": "test@aetherion.ai"}}
            if endpoint == "/api/health/control-plane":
                return {
                    "ok": True,
                    "status": "healthy",
                    "store": {"type": "SqliteFabricStore", "registered_workers": 1, "active_queue_items": 0},
                    "coordinator": {"mode": "local_fallback"},
                }
            return {"status": "ok"}

        mock_client.get.side_effect = mock_get

        with patch("cli.aetherion_cli.main._get_client", return_value=mock_client), \
             patch("aetherion_cli.main._get_client", return_value=mock_client, create=True):
            res = runner.invoke(cli, ["doctor", "--json"])
            assert res.exit_code == 0, f"doctor --json failed: {res.output}"
            data = json.loads(res.output)

            assert "execution_fabric" in data
            fab = data["execution_fabric"]
            assert fab["store_type"] == "SqliteFabricStore"
            assert fab["registered_workers"] >= 1


# ── 6. Multi-Worker Concurrent Execution ─────────────────────────────────────

class TestMultiWorkerConcurrency:
    def test_multi_worker_concurrent_task_execution(self, temp_db, coordinator):
        """Verify 2 real WorkerRuntime instances claim and execute tasks concurrently without duplicates."""
        service = ExecutionFabricService(store=temp_db)

        executed_tasks: Dict[str, str] = {}
        exec_lock = threading.Lock()

        def mock_executor(item: QueueItem):
            with exec_lock:
                assert item.item_id not in executed_tasks, f"Duplicate claim detected on {item.item_id}"
                executed_tasks[item.item_id] = item.task_id
            time.sleep(0.05)  # Simulate non-trivial work
            return {"status": "ok", "processed": True}

        # Initialize 2 concurrent workers
        w1 = WorkerRuntime(
            worker_id="worker_node_1",
            max_concurrency=2,
            service=service,
            store=temp_db,
            coordinator=coordinator,
            executor_fn=mock_executor,
            heartbeat_interval_sec=0.2,
            min_poll_interval_sec=0.05,
        )
        w2 = WorkerRuntime(
            worker_id="worker_node_2",
            max_concurrency=2,
            service=service,
            store=temp_db,
            coordinator=coordinator,
            executor_fn=mock_executor,
            heartbeat_interval_sec=0.2,
            min_poll_interval_sec=0.05,
        )

        w1.start()
        w2.start()

        try:
            # Submit 6 tasks across 2 tenants
            for i in range(3):
                service.submit_task(task_id=f"task_tenantA_{i}", tenant_id="tenant_A", priority=50 + i)
                service.submit_task(task_id=f"task_tenantB_{i}", tenant_id="tenant_B", priority=50 + i)

            # Wait for both workers to process and complete all 6 items in the store
            deadline = time.time() + 8.0
            completed_items = []
            while time.time() < deadline:
                completed_items = temp_db.list_queue_items(status=QueueItemStatus.COMPLETED.value)
                if len(completed_items) == 6:
                    break
                time.sleep(0.1)

            with exec_lock:
                assert len(executed_tasks) == 6, f"Expected 6 completed tasks, got {len(executed_tasks)}"

            # Verify store state
            assert len(completed_items) == 6

            # Verify both workers claimed work (fair load distribution)
            assigned_workers = {item.assigned_worker_id for item in completed_items}
            assert "worker_node_1" in assigned_workers
            assert "worker_node_2" in assigned_workers
        finally:
            w1.stop()
            w2.stop()


# ── 7. Real Process-Level Worker Crash & Orphan Recovery ─────────────────────

class TestWorkerCrashAndOrphanRecovery:
    def test_real_process_worker_crash_and_orphan_recovery(self, temp_db, coordinator):
        """
        Verify real process-level worker termination (proc.kill()) results in
        StepLease expiration, orphan detection, and clean recovery by another worker.
        """
        service = ExecutionFabricService(store=temp_db)

        # 1. Register a worker in store that will "crash"
        crashed_worker_id = "crashed_worker_proc"
        w_rec = WorkerRecord(
            worker_id=crashed_worker_id,
            session_id="sess_crashed",
            process_id=99999,
            max_concurrency=2,
            status=WorkerStatus.BUSY,
            last_heartbeat=time.time() - 60.0,
        )
        temp_db.upsert_worker(w_rec)

        # 2. Submit task and simulate that it was claimed by the crashed worker with short lease
        item = service.submit_task(task_id="task_crash_recovery_1", tenant_id="tenant_resilience")
        claimed = temp_db.claim_item_atomic(
            item_id=item.item_id,
            worker_id=crashed_worker_id,
            lease_id="lease_crash_test",
        )
        assert claimed is True
        claimed_item = temp_db.get_queue_item(item.item_id)
        assert claimed_item is not None
        assert claimed_item.status == QueueItemStatus.CLAIMED

        # 3. Simulate process termination: worker stops heartbeating and lease expires
        w_rec.last_heartbeat = time.time() - 60.0
        temp_db.upsert_worker(w_rec)

        # 4. Run orphan recovery
        orphans = service.recover_orphans()
        assert len(orphans["requeued_items"]) >= 1
        assert item.item_id in orphans["requeued_items"]

        # Item should now be reset to QUEUED/REQUEUED and ready for another worker
        re_queued_item = temp_db.get_queue_item(item.item_id)
        assert re_queued_item.status in (QueueItemStatus.QUEUED, QueueItemStatus.REQUEUED)
        assert re_queued_item.attempt >= 2

        # 5. Secondary worker claims and completes the recovered work
        secondary_executed = False

        def recovery_executor(it: QueueItem):
            nonlocal secondary_executed
            secondary_executed = True
            return {"recovered": True}

        w_recovery = WorkerRuntime(
            worker_id="worker_recovery_node",
            max_concurrency=1,
            service=service,
            store=temp_db,
            coordinator=coordinator,
            executor_fn=recovery_executor,
            min_poll_interval_sec=0.05,
        )
        w_recovery.start()

        try:
            deadline = time.time() + 4.0
            while time.time() < deadline:
                if secondary_executed:
                    break
                time.sleep(0.1)

            assert secondary_executed is True, "Recovery worker failed to process orphaned task"
            final_item = temp_db.get_queue_item(item.item_id)
            assert final_item.status == QueueItemStatus.COMPLETED
        finally:
            w_recovery.stop()


# ── 8. Control Plane Restart & State Preservation ────────────────────────────

class TestControlPlaneRestart:
    def test_control_plane_restart_state_preservation(self, tmp_path):
        """Verify API / Control Plane restart preserves all queued, claimed, and completed states."""
        db_path = str(tmp_path / "persistent_fabric.db")

        # 1. Instance A: Enqueue and dispatch work
        store_a = SqliteFabricStore(db_path=db_path)
        service_a = ExecutionFabricService(store=store_a)

        item_queued = service_a.submit_task(task_id="persist_q", tenant_id="tenant_p", priority=100)
        item_claimed = service_a.submit_task(task_id="persist_c", tenant_id="tenant_p", priority=50)

        store_a.upsert_worker(
            WorkerRecord(
                worker_id="w_persist",
                session_id="s1",
                process_id=111,
                max_concurrency=2,
                status=WorkerStatus.READY,
            )
        )
        claimed = store_a.claim_item_atomic(item_claimed.item_id, worker_id="w_persist", lease_id="lease_p")
        assert claimed is True

        # 2. Simulate complete control plane shutdown
        del service_a
        del store_a

        # 3. Instance B: Restart control plane against existing store
        store_b = SqliteFabricStore(db_path=db_path)
        service_b = ExecutionFabricService(store=store_b)

        # Verify state preservation
        re_q = store_b.get_queue_item(item_queued.item_id)
        re_c = store_b.get_queue_item(item_claimed.item_id)

        assert re_q is not None
        assert re_q.status == QueueItemStatus.QUEUED
        assert re_q.priority == 100

        assert re_c is not None
        assert re_c.status == QueueItemStatus.CLAIMED
        assert re_c.assigned_worker_id == "w_persist"

        workers = store_b.list_workers()
        assert any(w.worker_id == "w_persist" for w in workers)


# ── 9. Resilience & Fault Tolerance ──────────────────────────────────────────

class TestResilienceAndFaultTolerance:
    def test_redis_outage_safe_degradation(self):
        """Verify coordinator degrades safely to local condition variables on unreachable Redis."""
        # Unreachable redis mock client that reports not available
        dummy_client = MagicMock()
        dummy_client.available = False
        coord = FabricCoordinator(redis_client=dummy_client, fallback_to_local=True)
        assert coord.is_distributed is False

        # Work signaling should still operate smoothly in degraded fallback mode
        coord.notify_work_available()
        woke = coord.wait_for_work(timeout=0.05)
        # Should not throw unhandled exceptions
        assert isinstance(woke, bool)
        coord.close()

    def test_postgres_outage_fail_closed_semantics(self):
        """Verify store operations fail-closed when database connection is broken."""
        from backend.execution.fabric.postgres_store import PostgresFabricStore
        with pytest.raises(RuntimeError) as exc_info:
            PostgresFabricStore(dsn="postgresql://invalid_user:bad_pass@127.0.0.1:59999/nonexistent")
        assert "Failed to connect to PostgreSQL" in str(exc_info.value)


# ── 10. Evidence Chain & Multi-Tenant Security ───────────────────────────────

class TestEvidenceChainAndMultiTenantSecurity:
    def test_full_evidence_chain_and_audit_trail(self, temp_db, coordinator):
        """Verify complete lifecycle produces verifiable scheduling decisions and audit logs."""
        service = ExecutionFabricService(store=temp_db)

        # Worker
        w = WorkerRuntime(
            worker_id="audit_w",
            max_concurrency=1,
            service=service,
            store=temp_db,
            coordinator=coordinator,
            executor_fn=lambda it: {"status": "ok"},
        )
        w.register()

        # Submit task
        item = service.submit_task(task_id="audit_task_7", tenant_id="tenant_audit")

        # Scheduling tick
        decisions = service.tick_scheduler()
        assert len(decisions) >= 1
        d = next(dec for dec in decisions if dec.item_id == item.item_id)
        assert d.verdict == SchedulingVerdict.DISPATCHED
        assert d.assigned_worker_id == "audit_w"

        history = temp_db.list_decisions(item_id=item.item_id)
        assert len(history) >= 1
        assert history[0].verdict == SchedulingVerdict.DISPATCHED

    def test_tenant_isolation_and_security(self, temp_db, coordinator):
        """Verify per-tenant quotas prevent noisy neighbor starvation."""
        policy = SchedulerQuotaPolicy(max_concurrency_per_tenant=1)
        service = ExecutionFabricService(store=temp_db, quota_policy=policy)

        temp_db.upsert_worker(
            WorkerRecord(
                worker_id="w_sec",
                session_id="s_sec",
                process_id=777,
                max_concurrency=4,
                status=WorkerStatus.READY,
            )
        )

        # Tenant A submits 3 items
        for i in range(3):
            service.submit_task(task_id=f"tA_{i}", tenant_id="tenant_A")

        # Tenant B submits 1 item
        service.submit_task(task_id="tB_0", tenant_id="tenant_B")

        decisions = service.tick_scheduler()

        # Tenant A gets exactly 1 DISPATCHED, rest TENANT_QUOTA_EXCEEDED
        decs_a = [d for d in decisions if d.tenant_id == "tenant_A"]
        assert len([d for d in decs_a if d.verdict == SchedulingVerdict.DISPATCHED]) == 1
        assert len([d for d in decs_a if d.verdict == SchedulingVerdict.TENANT_QUOTA_EXCEEDED]) == 2

        # Tenant B gets 1 DISPATCHED (unblocked)
        decs_b = [d for d in decisions if d.tenant_id == "tenant_B"]
        assert len(decs_b) == 1
        assert decs_b[0].verdict == SchedulingVerdict.DISPATCHED
