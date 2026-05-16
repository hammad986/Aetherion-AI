"""
routes/cognition_z39.py — Phase Z39: Runtime Stabilization + Cognitive Integrity
==================================================================================

Endpoints:
  GET  /api/z39/status              — full Z39 system health summary
  GET  /api/z39/integrity           — Z39A cognitive integrity scan
  GET  /api/z39/replay/<exec_id>    — Z39B replay consistency for one execution
  GET  /api/z39/replay/bulk         — Z39B bulk replay confidence scores
  GET  /api/z39/memory/discipline   — Z39C memory discipline report
  GET  /api/z39/entropy             — Z39D execution entropy + chaos index
  GET  /api/z39/governance          — Z39E memory governance health
  POST /api/z39/governance/maintain — Z39E run maintenance cycle
  GET  /api/z39/stabilization       — Z39F runtime calmness assessment
  POST /api/z39/stabilization/pressure — Z39F record pressure event
"""

import time
import logging
from flask import Blueprint, request, jsonify

logger = logging.getLogger("nexora.z39")

cognition_z39_bp = Blueprint("cognition_z39", __name__, url_prefix="/api/z39")

# ── Lazy subsystem initialisation ──────────────────────────────────────────────
# All subsystems share the same ExecutionStore instance.

_store  = None
_integrity_scanner    = None
_replay_manager       = None
_memory_discipline    = None
_entropy_monitor      = None
_memory_governor      = None

def _get_store():
    global _store
    if _store is None:
        import os
        from execution.store import ExecutionStore
        db_path = os.environ.get("EXECUTION_STORE_DB", "workspace/execution_store.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        _store = ExecutionStore(db_path)
    return _store

def _get_integrity():
    global _integrity_scanner
    if _integrity_scanner is None:
        from execution.cognitive_integrity import IntegrityScanner
        _integrity_scanner = IntegrityScanner(_get_store())
    return _integrity_scanner

def _get_replay():
    global _replay_manager
    if _replay_manager is None:
        from execution.replay_consistency import ReplayConsistencyManager
        _replay_manager = ReplayConsistencyManager(_get_store())
    return _replay_manager

def _get_memory_discipline():
    global _memory_discipline
    if _memory_discipline is None:
        from execution.memory_discipline import MemoryDisciplineManager
        _memory_discipline = MemoryDisciplineManager(_get_store())
    return _memory_discipline

def _get_entropy():
    global _entropy_monitor
    if _entropy_monitor is None:
        from execution.entropy_analysis import EntropyMonitor
        _entropy_monitor = EntropyMonitor(_get_store())
    return _entropy_monitor

def _get_governance():
    global _memory_governor
    if _memory_governor is None:
        from execution.memory_governance import MemoryGovernor
        _memory_governor = MemoryGovernor(_get_store())
    return _memory_governor


# ── /api/z39/status ────────────────────────────────────────────────────────────

@cognition_z39_bp.route("/status", methods=["GET"])
def z39_status():
    """Aggregated Z39 health summary across all subsystems."""
    try:
        integrity   = _get_integrity().scan(max_executions=100)
        entropy     = _get_entropy().report()
        stabilization = __import__("execution.self_stabilization", fromlist=["get_stabilization_snapshot"]).get_stabilization_snapshot()
        governance  = _get_governance().health_report()

        return jsonify({
            "phase": "Z39",
            "status": "active",
            "generated_at": time.time(),
            "cognitive_integrity": {
                "severity": integrity.get("severity"),
                "total_findings": integrity.get("total_findings", 0),
                "loops": integrity.get("loop_count", 0),
                "orphans": integrity.get("orphan_count", 0),
            },
            "entropy": {
                "chaos_index": entropy.get("chaos_index"),
                "stability_label": entropy.get("stability_label"),
            },
            "stabilization": {
                "calmness_score": stabilization.get("calmness_score"),
                "verdict": stabilization.get("verdict"),
                "dampened_executions": stabilization.get("pressure", {}).get("dampened_count", 0),
                "blocked_executions": stabilization.get("loop_guard", {}).get("blocked_count", 0),
            },
            "governance": {
                "retention_tiers": governance.get("retention_tiers", {}),
                "fragmentation": {
                    "empty_lineage": governance.get("fragmentation", {}).get("empty_lineage_count", 0),
                    "hydration_gaps": governance.get("fragmentation", {}).get("hydration_gap_count", 0),
                    "stale_evolutions": governance.get("fragmentation", {}).get("stale_evolution_count", 0),
                },
                "db_size_mb": governance.get("db_stats", {}).get("size_mb", 0),
            },
        })
    except Exception as exc:
        logger.exception("[Z39] /status error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── /api/z39/integrity ─────────────────────────────────────────────────────────

@cognition_z39_bp.route("/integrity", methods=["GET"])
def z39_integrity():
    """Z39A: Full cognitive integrity scan."""
    try:
        max_ex = int(request.args.get("max_executions", 200))
        result = _get_integrity().scan(max_executions=max_ex)
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z39] /integrity error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── /api/z39/replay/<exec_id> ──────────────────────────────────────────────────

@cognition_z39_bp.route("/replay/<execution_id>", methods=["GET"])
def z39_replay_single(execution_id):
    """Z39B: Replay consistency analysis for a single execution."""
    try:
        result = _get_replay().analyse(execution_id)
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z39] /replay/%s error: %s", execution_id, exc)
        return jsonify({"error": str(exc)}), 500


# ── /api/z39/replay/bulk ───────────────────────────────────────────────────────

@cognition_z39_bp.route("/replay/bulk", methods=["GET"])
def z39_replay_bulk():
    """Z39B: Bulk replay confidence scores for all recent executions, ranked worst-first."""
    try:
        limit = int(request.args.get("limit", 100))
        results = _get_replay().bulk_analyse(limit=limit)
        return jsonify({
            "analysed": len(results),
            "results": results,
        })
    except Exception as exc:
        logger.exception("[Z39] /replay/bulk error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── /api/z39/memory/discipline ─────────────────────────────────────────────────

@cognition_z39_bp.route("/memory/discipline", methods=["GET"])
def z39_memory_discipline():
    """Z39C: Adaptive memory discipline report."""
    try:
        result = _get_memory_discipline().full_report()
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z39] /memory/discipline error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── /api/z39/entropy ──────────────────────────────────────────────────────────

@cognition_z39_bp.route("/entropy", methods=["GET"])
def z39_entropy():
    """Z39D: Execution entropy metrics and Runtime Chaos Index."""
    try:
        force = request.args.get("force", "false").lower() == "true"
        result = _get_entropy().report(force=force)
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z39] /entropy error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── /api/z39/governance ───────────────────────────────────────────────────────

@cognition_z39_bp.route("/governance", methods=["GET"])
def z39_governance():
    """Z39E: Memory governance health report."""
    try:
        result = _get_governance().health_report()
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z39] /governance error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z39_bp.route("/governance/maintain", methods=["POST"])
def z39_governance_maintain():
    """Z39E: Run a maintenance cycle (prune / WAL checkpoint / vacuum)."""
    try:
        dry_run = request.json.get("dry_run", True) if request.is_json else True
        result = _get_governance().run_maintenance(dry_run=dry_run)
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z39] /governance/maintain error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── /api/z39/stabilization ────────────────────────────────────────────────────

@cognition_z39_bp.route("/stabilization", methods=["GET"])
def z39_stabilization():
    """Z39F: Runtime calmness and stabilization assessment."""
    try:
        from execution.self_stabilization import get_stabilization_snapshot
        result = get_stabilization_snapshot()
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z39] /stabilization error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z39_bp.route("/stabilization/pressure", methods=["POST"])
def z39_record_pressure():
    """Z39F: Record a pressure event for an execution branch."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        execution_id = data.get("execution_id", "")
        severity     = float(data.get("severity", 1.0))
        if not execution_id:
            return jsonify({"error": "execution_id required"}), 400
        from execution.self_stabilization import record_execution_pressure
        result = record_execution_pressure(execution_id, severity)
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z39] /stabilization/pressure error: %s", exc)
        return jsonify({"error": str(exc)}), 500
