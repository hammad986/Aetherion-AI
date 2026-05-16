"""
routes/cognition_z40.py — Phase Z40: Resource Intelligence + Context Compression
==================================================================================

Endpoints:
  GET  /api/z40/status                      — full Z40 system health summary
  GET  /api/z40/compression                 — Z40A context compression snapshot
  POST /api/z40/compression/push            — Z40A push context item for a session
  POST /api/z40/compression/force           — Z40A force compression on a session
  GET  /api/z40/resources                   — Z40B resource intelligence report
  GET  /api/z40/budget                      — Z40C adaptive budget snapshot
  POST /api/z40/budget/consume              — Z40C record consumption
  POST /api/z40/budget/cool                 — Z40C apply cooling to a session budget
  GET  /api/z40/continuity                  — Z40D long-session continuity snapshot
  POST /api/z40/continuity/anchor           — Z40D anchor a continuity dimension
  POST /api/z40/continuity/refresh          — Z40D trigger context refresh
  GET  /api/z40/replay/governance           — Z40E replay tier report
  POST /api/z40/replay/compact              — Z40E compact historical replay chains
  GET  /api/z40/replay/hydration-plan       — Z40E hydration plan
  GET  /api/z40/load                        — Z40F cognitive load balancing assessment
  POST /api/z40/load/surface                — Z40F update surface pressure
"""

import os
import time
import logging
from flask import Blueprint, request, jsonify

logger = logging.getLogger("nexora.z40")

cognition_z40_bp = Blueprint("cognition_z40", __name__, url_prefix="/api/z40")

# ── Lazy subsystem accessors ───────────────────────────────────────────────────

_store = None
_resource_mgr = None
_replay_gov = None


def _get_store():
    global _store
    if _store is None:
        from execution.store import ExecutionStore
        db_path = os.environ.get("EXECUTION_STORE_DB", "workspace/execution_store.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        _store = ExecutionStore(db_path)
    return _store


def _get_resource_mgr():
    global _resource_mgr
    if _resource_mgr is None:
        from execution.resource_intelligence import ResourceIntelligenceManager
        _resource_mgr = ResourceIntelligenceManager(_get_store())
    return _resource_mgr


def _get_replay_gov():
    global _replay_gov
    if _replay_gov is None:
        from execution.replay_compression_governance import ReplayCompressionGovernor
        _replay_gov = ReplayCompressionGovernor(_get_store())
    return _replay_gov


def _get_compression():
    from execution.context_compression import get_compression_ledger
    return get_compression_ledger()


def _get_budget():
    from execution.execution_budgeting import get_budget_manager
    return get_budget_manager()


def _get_continuity():
    from execution.long_session_continuity import get_continuity_manager
    return get_continuity_manager()


def _get_load_balancer():
    from execution.cognitive_load_balancing import get_load_balancer
    return get_load_balancer()


def _get_entropy_index() -> float:
    """Pull chaos_index from Z39 entropy monitor if available."""
    try:
        from execution.entropy_analysis import EntropyMonitor
        monitor = EntropyMonitor(_get_store())
        return monitor.report().get("chaos_index", 0.0)
    except Exception:
        return 0.0


def _get_calmness_score() -> float:
    try:
        from execution.self_stabilization import get_stabilization_snapshot
        return get_stabilization_snapshot().get("calmness_score", 100.0)
    except Exception:
        return 100.0


# ── /api/z40/status ────────────────────────────────────────────────────────────

@cognition_z40_bp.route("/status", methods=["GET"])
def z40_status():
    """Aggregated Z40 health summary across all subsystems."""
    try:
        chaos     = _get_entropy_index()
        calmness  = _get_calmness_score()

        compression_snap = _get_compression().global_snapshot()
        resource_report  = _get_resource_mgr().report(chaos_index=chaos)
        budget_snap      = _get_budget().snapshot()
        continuity_snap  = _get_continuity().global_snapshot()
        replay_tiers     = _get_replay_gov().tier_report(limit=200)
        load_snap        = _get_load_balancer().assess(chaos, calmness)

        return jsonify({
            "phase":        "Z40",
            "status":       "active",
            "generated_at": time.time(),
            "compression": {
                "sessions":           compression_snap["session_count"],
                "active_items":       compression_snap["total_active_items"],
                "compressed_blocks":  compression_snap["total_compressed_blocks"],
                "avg_confidence":     compression_snap["avg_compression_confidence"],
            },
            "resources": {
                "severity":     resource_report["severity"],
                "overall_risk": resource_report["forecast"]["overall_risk"],
                "top_risk":     resource_report["forecast"]["top_risk"],
            },
            "budgeting": {
                "sessions":            budget_snap["session_count"],
                "stabilized_sessions": budget_snap["stabilized_sessions"],
            },
            "continuity": {
                "sessions":         continuity_snap["session_count"],
                "drifted_sessions": continuity_snap["drifted_sessions"],
            },
            "replay_governance": {
                "tier_counts": replay_tiers["tier_counts"],
            },
            "load_balancing": {
                "calm_directive": load_snap["calm_directive"],
                "overall_surface_pressure": load_snap["surface_pressure"]["overall"],
            },
        })
    except Exception as exc:
        logger.exception("[Z40] /status error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Z40A: Context compression ──────────────────────────────────────────────────

@cognition_z40_bp.route("/compression", methods=["GET"])
def z40_compression():
    try:
        return jsonify(_get_compression().global_snapshot())
    except Exception as exc:
        logger.exception("[Z40] /compression error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z40_bp.route("/compression/push", methods=["POST"])
def z40_compression_push():
    try:
        import uuid
        from execution.context_compression import ContextItem
        data       = request.get_json(force=True, silent=True) or {}
        session_id = data.get("session_id", "")
        content    = data.get("content", "")
        item_type  = data.get("type", "chunk")
        signal     = float(data.get("signal", 0.5))
        lineage_id = data.get("lineage_id", "")

        if not session_id or not content:
            return jsonify({"error": "session_id and content required"}), 400

        item = ContextItem(
            item_id=uuid.uuid4().hex[:16],
            session_id=session_id,
            content=content,
            item_type=item_type,
            signal=signal,
            lineage_id=lineage_id,
        )
        _get_compression().push(session_id, item)
        win  = _get_compression().get_or_create(session_id)
        return jsonify({"pushed": True, "window": win.snapshot()})
    except Exception as exc:
        logger.exception("[Z40] /compression/push error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z40_bp.route("/compression/force", methods=["POST"])
def z40_compression_force():
    try:
        data       = request.get_json(force=True, silent=True) or {}
        session_id = data.get("session_id", "")
        if not session_id:
            return jsonify({"error": "session_id required"}), 400
        block = _get_compression().force_compress(session_id)
        return jsonify({
            "compressed": block is not None,
            "block_id":   block.block_id if block else None,
            "confidence": block.overall_confidence() if block else None,
        })
    except Exception as exc:
        logger.exception("[Z40] /compression/force error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Z40B: Resource intelligence ────────────────────────────────────────────────

@cognition_z40_bp.route("/resources", methods=["GET"])
def z40_resources():
    try:
        force = request.args.get("force", "false").lower() == "true"
        chaos = _get_entropy_index()
        return jsonify(_get_resource_mgr().report(chaos_index=chaos, force=force))
    except Exception as exc:
        logger.exception("[Z40] /resources error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Z40C: Adaptive budgeting ───────────────────────────────────────────────────

@cognition_z40_bp.route("/budget", methods=["GET"])
def z40_budget():
    try:
        return jsonify(_get_budget().snapshot())
    except Exception as exc:
        logger.exception("[Z40] /budget error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z40_bp.route("/budget/consume", methods=["POST"])
def z40_budget_consume():
    try:
        data       = request.get_json(force=True, silent=True) or {}
        session_id = data.get("session_id", "")
        tokens     = int(data.get("tokens", 0))
        retries    = int(data.get("retries", 0))
        if not session_id:
            return jsonify({"error": "session_id required"}), 400
        result = _get_budget().consume(session_id, tokens=tokens, retries=retries)
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z40] /budget/consume error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z40_bp.route("/budget/cool", methods=["POST"])
def z40_budget_cool():
    try:
        data           = request.get_json(force=True, silent=True) or {}
        session_id     = data.get("session_id", "")
        chaos_index    = float(data.get("chaos_index", _get_entropy_index()))
        retry_rate     = float(data.get("retry_rate", 0.0))
        entropy_level  = float(data.get("entropy_level", chaos_index / 100.0))
        stab_conf      = float(data.get("stabilization_confidence", _get_calmness_score() / 100.0))
        plan           = data.get("plan", "pro")

        if not session_id:
            return jsonify({"error": "session_id required"}), 400

        mgr     = _get_budget()
        mgr.get_or_create(session_id, plan=plan)
        profile = mgr.update_cooling(session_id, chaos_index, retry_rate, entropy_level, stab_conf)
        return jsonify(profile.to_dict())
    except Exception as exc:
        logger.exception("[Z40] /budget/cool error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Z40D: Long-session continuity ─────────────────────────────────────────────

@cognition_z40_bp.route("/continuity", methods=["GET"])
def z40_continuity():
    try:
        return jsonify(_get_continuity().global_snapshot())
    except Exception as exc:
        logger.exception("[Z40] /continuity error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z40_bp.route("/continuity/anchor", methods=["POST"])
def z40_continuity_anchor():
    try:
        data       = request.get_json(force=True, silent=True) or {}
        session_id = data.get("session_id", "")
        dimension  = data.get("dimension", "mission")
        content    = data.get("content", "")
        coherence  = float(data.get("coherence", 1.0))

        if not session_id or not content:
            return jsonify({"error": "session_id and content required"}), 400

        _get_continuity().anchor(session_id, dimension, content, coherence)
        drift = _get_continuity().detect_drift(session_id)
        return jsonify({"anchored": True, "drift": drift})
    except Exception as exc:
        logger.exception("[Z40] /continuity/anchor error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z40_bp.route("/continuity/refresh", methods=["POST"])
def z40_continuity_refresh():
    try:
        data       = request.get_json(force=True, silent=True) or {}
        session_id = data.get("session_id", "")
        if not session_id:
            return jsonify({"error": "session_id required"}), 400
        result = _get_continuity().refresh(session_id, _get_compression())
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z40] /continuity/refresh error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Z40E: Replay compression governance ──────────────────────────────────────

@cognition_z40_bp.route("/replay/governance", methods=["GET"])
def z40_replay_governance():
    try:
        limit = int(request.args.get("limit", 500))
        return jsonify(_get_replay_gov().tier_report(limit=limit))
    except Exception as exc:
        logger.exception("[Z40] /replay/governance error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z40_bp.route("/replay/compact", methods=["POST"])
def z40_replay_compact():
    try:
        data        = request.get_json(force=True, silent=True) or {}
        max_compact = int(data.get("max_compact", 50))
        result      = _get_replay_gov().compact_historical(max_compact)
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z40] /replay/compact error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z40_bp.route("/replay/hydration-plan", methods=["GET"])
def z40_hydration_plan():
    try:
        resource_report   = _get_resource_mgr().report(chaos_index=_get_entropy_index())
        sev_raw           = resource_report.get("severity", "LIGHT")
        resource_severity = sev_raw.value if hasattr(sev_raw, "value") else str(sev_raw)
        result = _get_replay_gov().hydration_plan(resource_severity=resource_severity)
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z40] /replay/hydration-plan error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Z40F: Cognitive load balancing ────────────────────────────────────────────

@cognition_z40_bp.route("/load", methods=["GET"])
def z40_load():
    try:
        chaos    = _get_entropy_index()
        calmness = _get_calmness_score()
        return jsonify(_get_load_balancer().assess(chaos, calmness))
    except Exception as exc:
        logger.exception("[Z40] /load error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z40_bp.route("/load/surface", methods=["POST"])
def z40_load_surface():
    try:
        data = request.get_json(force=True, silent=True) or {}
        result = _get_load_balancer().update_surface(
            timeline_density  = data.get("timeline_density"),
            dag_complexity    = data.get("dag_complexity"),
            inspector_load    = data.get("inspector_load"),
            replay_hydration  = data.get("replay_hydration"),
        )
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z40] /load/surface error: %s", exc)
        return jsonify({"error": str(exc)}), 500
