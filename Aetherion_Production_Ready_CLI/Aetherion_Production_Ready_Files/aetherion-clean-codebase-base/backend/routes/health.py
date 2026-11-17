"""
routes/health.py — Unified System Health Probes
Exposes:
  - GET /api/health (Standard CLI health check endpoint)
  - GET /api/v2/health (Canonical v2 response contract)
"""
from __future__ import annotations

import datetime
import os
import platform
from flask import Blueprint, jsonify

from core.environment import get_environment_mode

health_bp = Blueprint("health", __name__)


@health_bp.route("/api/health", methods=["GET"])
def api_health():
    """Real-time system health and readiness probe."""
    env_mode = get_environment_mode()

    # Determine runner mode
    runner_isolation = "container" if os.getenv("RUNNER_CONTAINER") == "1" else "process"

    # Best effort resource metrics
    cpu_pct = None
    mem_used_pct = None
    try:
        import psutil
        cpu_pct = psutil.cpu_percent(interval=None)
        mem_used_pct = psutil.virtual_memory().percent
    except Exception:
        pass

    return jsonify({
        "ok": True,
        "status": "ok",
        "version": "1.0.0",
        "service": "aetherion-backend",
        "environment": env_mode.value,
        "runner_isolation": runner_isolation,
        "metrics": {
            "cpu_pct": cpu_pct,
            "mem_pct": mem_used_pct,
        },
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }), 200


@health_bp.route("/api/v2/health", methods=["GET"])
def api_v2_health():
    """Canonical v2 health response contract."""
    from middleware.api_response import success_response
    return success_response(data={
        "status": "ok",
        "version": "2.0.0",
        "environment": get_environment_mode().value,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })


_SERVER_START_TIME = datetime.datetime.now(datetime.timezone.utc)


@health_bp.route("/livez", methods=["GET"])
def livez():
    """Kubernetes liveness probe — independent of external dependencies."""
    uptime = (datetime.datetime.now(datetime.timezone.utc) - _SERVER_START_TIME).total_seconds()
    return jsonify({
        "status": "ok",
        "pid": os.getpid(),
        "uptime_seconds": uptime,
    }), 200


from core.preflight import run_preflight, CheckStatus


@health_bp.route("/readyz", methods=["GET"])
def readyz():
    """Kubernetes readiness probe — reports task execution and dependency readiness."""
    report = run_preflight(probe_live=True)
    checks_dict = {}
    for c in report.checks:
        checks_dict[c.check_id] = {
            "ok": c.status == CheckStatus.PASS,
            "required": c.required,
            "detail": c.detail,
            "status": c.status.value,
            "severity": c.severity.value,
        }

    # Backward compatibility with existing preflight scripts
    db_check = next((c for c in report.checks if c.check_id == "database.readiness"), None)
    if db_check:
        checks_dict["database"] = {
            "ok": db_check.status == CheckStatus.PASS,
            "required": db_check.required,
            "detail": db_check.detail,
        }
    checks_dict["environment"] = {
        "ok": report.healthy,
        "required": True,
        "detail": report.environment,
    }

    payload = {
        "status": "ready" if report.ready else "not_ready",
        "ready": report.ready,
        "environment": report.environment,
        "checks": checks_dict,
        "timestamp": report.timestamp,
        "summary": report.summary,
    }
    status_code = 200 if report.ready else 503
    return jsonify(payload), status_code



@health_bp.route("/startupz", methods=["GET"])
def startupz():
    """Kubernetes startup probe."""
    return jsonify({
        "status": "started",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }), 200


@health_bp.route("/api/health/live", methods=["GET"])
def api_health_live():
    """Level 1: LIVE — Process liveness verification."""
    uptime = (datetime.datetime.now(datetime.timezone.utc) - _SERVER_START_TIME).total_seconds()
    return jsonify({
        "level": "LIVE",
        "ok": True,
        "status": "LIVE",
        "pid": os.getpid(),
        "uptime_seconds": uptime,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }), 200


@health_bp.route("/api/health/ready", methods=["GET"])
def api_health_ready():
    """Level 2: READY — Core dependencies and platform readiness."""
    report = run_preflight(probe_live=True)
    return jsonify({
        "level": "READY",
        "ok": report.ready,
        "status": "READY" if report.ready else "NOT-READY",
        "environment": report.environment,
        "summary": report.summary,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }), (200 if report.ready else 503)


@health_bp.route("/api/health/control-plane", methods=["GET"])
def api_health_control_plane():
    """Level 3: CONTROL-PLANE-READY — Execution Fabric Control Plane & Store probe."""
    try:
        from backend.core.preflight import check_control_plane, CheckStatus
    except ImportError:
        from core.preflight import check_control_plane, CheckStatus

    try:
        from backend.execution.fabric.store import get_fabric_store
        from backend.execution.fabric.coordination import FabricCoordinator
    except ImportError:
        from execution.fabric.store import get_fabric_store
        from execution.fabric.coordination import FabricCoordinator

    chk = check_control_plane(probe_live=True)
    status_ok = chk.status == CheckStatus.PASS

    store_type = "unknown"
    worker_count = 0
    active_items = 0
    coordinator_distributed = False

    try:
        store = get_fabric_store()
        store_type = type(store).__name__
        worker_count = len(store.list_workers())
        active_items = store.count_global_active_items()
    except Exception:
        pass

    try:
        coord = FabricCoordinator()
        coordinator_distributed = coord.is_distributed
    except Exception:
        pass

    return jsonify({
        "level": "CONTROL-PLANE-READY",
        "ok": status_ok,
        "status": "healthy" if status_ok else ("degraded" if chk.status == CheckStatus.WARN else "unhealthy"),
        "check": chk.to_dict(),
        "store": {
            "type": store_type,
            "registered_workers": worker_count,
            "active_queue_items": active_items,
        },
        "coordinator": {
            "distributed": coordinator_distributed,
            "mode": "redis" if coordinator_distributed else "local_fallback",
        },
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }), (200 if status_ok or chk.status == CheckStatus.WARN else 503)


@health_bp.route("/api/health/workers", methods=["GET"])
def api_health_workers():
    """Level 4: WORKER-CAPABLE — Worker fleet capability and availability probe."""
    try:
        from backend.execution.fabric.store import get_fabric_store
        from backend.execution.fabric.metrics import get_metrics_collector
    except ImportError:
        from execution.fabric.store import get_fabric_store
        from execution.fabric.metrics import get_metrics_collector

    store = get_fabric_store()
    collector = get_metrics_collector()
    workers = store.list_workers()
    collector.sync_worker_fleet(workers)

    ready_workers = [
        w for w in workers
        if getattr(w.status, "value", str(w.status)) in ("READY", "BUSY") and not getattr(w, "drain_state", False)
    ]
    total_capacity = sum(getattr(w, "max_concurrency", 0) for w in ready_workers)
    total_utilized = sum(getattr(w, "active_concurrency", 0) for w in ready_workers)

    is_capable = len(ready_workers) > 0

    return jsonify({
        "level": "WORKER-CAPABLE",
        "ok": is_capable,
        "status": "WORKER-CAPABLE" if is_capable else "NO-WORKERS-AVAILABLE",
        "registered_workers": len(workers),
        "ready_workers_count": len(ready_workers),
        "total_concurrency_capacity": total_capacity,
        "total_concurrency_utilized": total_utilized,
        "workers": [
            {
                "worker_id": w.worker_id,
                "status": getattr(w.status, "value", str(w.status)),
                "concurrency": f"{w.active_concurrency}/{w.max_concurrency}",
                "drain_state": w.drain_state,
                "capabilities": list(w.supported_capabilities),
            }
            for w in workers
        ],
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }), (200 if is_capable else 503)


@health_bp.route("/api/health/execution-ready", methods=["GET"])
def api_health_execution_ready():
    """Level 5: EXECUTION-READY — End-to-end task execution fabric operational readiness."""
    try:
        from backend.core.preflight import check_control_plane, CheckStatus
        from backend.execution.fabric.store import get_fabric_store
    except ImportError:
        from core.preflight import check_control_plane, CheckStatus
        from execution.fabric.store import get_fabric_store

    chk = check_control_plane(probe_live=True)
    control_plane_ok = chk.status in (CheckStatus.PASS, CheckStatus.WARN)

    store = get_fabric_store()
    workers = store.list_workers()
    ready_workers = [
        w for w in workers
        if getattr(w.status, "value", str(w.status)) in ("READY", "BUSY") and not getattr(w, "drain_state", False)
    ]
    worker_ok = len(ready_workers) > 0

    is_exec_ready = control_plane_ok and worker_ok
    status_str = "EXECUTION-READY" if is_exec_ready else ("CONTROL-PLANE-DEGRADED" if not control_plane_ok else "NO-ACTIVE-WORKERS")

    return jsonify({
        "level": "EXECUTION-READY",
        "ok": is_exec_ready,
        "status": status_str,
        "control_plane_ready": control_plane_ok,
        "workers_ready": worker_ok,
        "ready_worker_count": len(ready_workers),
        "active_queue_items": store.count_global_active_items(),
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }), (200 if is_exec_ready else 503)


@health_bp.route("/api/health/fabric-readiness", methods=["GET"])
def api_health_fabric_readiness():
    """Composite 5-level system operational readiness probe."""
    try:
        from backend.core.preflight import run_preflight, check_control_plane, CheckStatus
        from backend.execution.fabric.store import get_fabric_store
    except ImportError:
        from core.preflight import run_preflight, check_control_plane, CheckStatus
        from execution.fabric.store import get_fabric_store

    # Level 1: LIVE
    live_ok = True
    # Level 2: READY
    preflight_rep = run_preflight(probe_live=False)
    ready_ok = preflight_rep.ready
    # Level 3: CONTROL-PLANE-READY
    cp_chk = check_control_plane(probe_live=True)
    cp_ok = cp_chk.status in (CheckStatus.PASS, CheckStatus.WARN)
    # Level 4: WORKER-CAPABLE
    store = get_fabric_store()
    workers = store.list_workers()
    ready_workers = [
        w for w in workers
        if getattr(w.status, "value", str(w.status)) in ("READY", "BUSY") and not getattr(w, "drain_state", False)
    ]
    worker_ok = len(ready_workers) > 0
    # Level 5: EXECUTION-READY
    exec_ready = cp_ok and worker_ok

    overall_ok = live_ok and ready_ok and cp_ok and worker_ok

    return jsonify({
        "overall_ok": overall_ok,
        "levels": {
            "LIVE": {"ok": live_ok, "status": "LIVE"},
            "READY": {"ok": ready_ok, "status": "READY" if ready_ok else "NOT-READY"},
            "CONTROL-PLANE-READY": {"ok": cp_ok, "status": cp_chk.status.value},
            "WORKER-CAPABLE": {"ok": worker_ok, "active_workers": len(ready_workers)},
            "EXECUTION-READY": {"ok": exec_ready, "status": "EXECUTION-READY" if exec_ready else "DEGRADED"},
        },
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }), (200 if overall_ok else 503)


@health_bp.route("/api/health/metrics", methods=["GET"])
def api_health_metrics():
    """Execution Fabric metrics in structured JSON format."""
    try:
        from backend.execution.fabric.metrics import get_metrics_collector
        from backend.execution.fabric.store import get_fabric_store
    except ImportError:
        from execution.fabric.metrics import get_metrics_collector
        from execution.fabric.store import get_fabric_store

    collector = get_metrics_collector()
    try:
        store = get_fabric_store()
        collector.sync_worker_fleet(store.list_workers())
    except Exception:
        pass

    return jsonify(collector.get_metrics()), 200


@health_bp.route("/metrics", methods=["GET"])
def metrics():
    """Prometheus exposition format."""
    try:
        from backend.execution.fabric.metrics import get_metrics_collector
        from backend.execution.fabric.store import get_fabric_store
    except ImportError:
        from execution.fabric.metrics import get_metrics_collector
        from execution.fabric.store import get_fabric_store

    collector = get_metrics_collector()
    try:
        store = get_fabric_store()
        collector.sync_worker_fleet(store.list_workers())
    except Exception:
        pass

    body = collector.to_prometheus_format()
    return body, 200, {"Content-Type": "text/plain; version=0.0.4"}



