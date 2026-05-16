"""
routes/cognition_z41.py — Phase Z41: Predictive Runtime Coordination + Latency Intelligence
============================================================================================

Endpoints:
  GET  /api/z41/status                        — full Z41 system health summary
  GET  /api/z41/coordination                  — Z41A coordination graph + arbitration
  POST /api/z41/coordination/update           — Z41A update subsystem pressure
  GET  /api/z41/scheduling                    — Z41B predictive scheduling report
  POST /api/z41/scheduling/sample             — Z41B record a pressure sample
  POST /api/z41/scheduling/enqueue            — Z41B enqueue execution with priority
  GET  /api/z41/latency                       — Z41C latency intelligence report
  POST /api/z41/latency/record                — Z41C record a latency observation
  GET  /api/z41/latency/surface/<surface>     — Z41C stats + recent traces for one surface
  GET  /api/z41/priority                      — Z41D adaptive priority snapshot
  POST /api/z41/priority/register             — Z41D register a chain with priority
  POST /api/z41/priority/rebalance            — Z41D rebalance all chain priorities
  GET  /api/z41/compression/validate          — Z41E global compression validation
  POST /api/z41/compression/validate/session  — Z41E validate a specific session
  GET  /api/z41/pacing                        — Z41F operational pacing report
  POST /api/z41/pacing/tick                   — Z41F record an adaptive sweep tick
  POST /api/z41/pacing/slot/request           — Z41F request execution slot
  POST /api/z41/pacing/slot/release           — Z41F release execution slot
  GET  /api/z41/pacing/can-run/<operation>    — Z41F check if operation is within sync window
"""

import time
import logging
from flask import Blueprint, request, jsonify

logger = logging.getLogger("nexora.z41")

cognition_z41_bp = Blueprint("cognition_z41", __name__, url_prefix="/api/z41")


# ── Lazy subsystem accessors ───────────────────────────────────────────────────

def _coord():
    from execution.runtime_coordination import get_coordination_manager
    return get_coordination_manager()

def _sched():
    from execution.predictive_scheduling import get_scheduling_manager
    return get_scheduling_manager()

def _latency():
    from execution.latency_intelligence import get_latency_manager
    return get_latency_manager()

def _priority():
    from execution.adaptive_priority import get_priority_manager
    return get_priority_manager()

def _validate():
    from execution.compression_validation import get_validation_manager
    return get_validation_manager()

def _pacing():
    from execution.operational_pacing import get_pacing_manager
    return get_pacing_manager()

def _compression():
    from execution.context_compression import get_compression_ledger
    return get_compression_ledger()

def _get_entropy() -> float:
    try:
        # Use the Z39 blueprint's store singleton to avoid creating a new DB handle
        from routes.cognition_z40 import _get_store
        from execution.entropy_analysis import EntropyMonitor
        return EntropyMonitor(_get_store()).report().get("chaos_index", 0.0)
    except Exception:
        return 0.0

def _get_calmness() -> float:
    try:
        from execution.self_stabilization import get_stabilization_snapshot
        return get_stabilization_snapshot().get("calmness_score", 100.0)
    except Exception:
        return 100.0

def _get_resource_risk() -> float:
    try:
        # Reuse the Z40 module-level resource manager singleton (has 60s cache)
        from routes.cognition_z40 import _get_resource_mgr
        report = _get_resource_mgr().report(chaos_index=_get_entropy())
        sev = report.get("severity", "LIGHT")
        return report["forecast"]["overall_risk"]
    except Exception:
        return 0.0


# ── /api/z41/status ────────────────────────────────────────────────────────────

@cognition_z41_bp.route("/status", methods=["GET"])
def z41_status():
    try:
        chaos    = _get_entropy()
        calmness = _get_calmness()
        risk     = _get_resource_risk()

        coord_report  = _coord().report()
        sched_report  = _sched().report()
        latency_rep   = _latency().report()
        prio_snap     = _priority().snapshot()
        pacing_rep    = _pacing().report()

        return jsonify({
            "phase":        "Z41",
            "status":       "active",
            "generated_at": time.time(),
            "coordination": {
                "severity":       coord_report["severity"],
                "conflict_count": coord_report["arbitration"]["conflict_count"],
            },
            "scheduling": {
                "spike_risk_label": sched_report["forecast"].get("spike_risk_label", "UNKNOWN"),
                "precool_active":   sched_report["precool"].get("precool", False),
                "queue_length":     sched_report["queue"]["length"],
            },
            "latency": {
                "hottest_surface":   latency_rep["heatmap"]["hottest_surface"],
                "hottest_severity":  latency_rep["heatmap"]["hottest_severity"],
                "degraded_count":    latency_rep["degraded_count"],
            },
            "priority": {
                "chain_count":    prio_snap["chain_count"],
                "suppressed":     prio_snap["suppressed"],
                "by_priority":    prio_snap["by_priority"],
            },
            "pacing": {
                "storming":       pacing_rep["pacer"]["storming"],
                "storm_rate":     pacing_rep["pacer"]["storm_rate"],
                "active_slots":   pacing_rep["negotiator"]["active_count"],
            },
            "runtime": {
                "chaos_index":    chaos,
                "calmness_score": calmness,
                "resource_risk":  risk,
            },
        })
    except Exception as exc:
        logger.exception("[Z41] /status error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Z41A: Runtime coordination ────────────────────────────────────────────────

@cognition_z41_bp.route("/coordination", methods=["GET"])
def z41_coordination():
    try:
        return jsonify(_coord().report())
    except Exception as exc:
        logger.exception("[Z41] /coordination error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z41_bp.route("/coordination/update", methods=["POST"])
def z41_coordination_update():
    try:
        data      = request.get_json(force=True, silent=True) or {}
        subsystem = data.get("subsystem", "")
        pressure  = float(data.get("pressure", 0.0))
        action    = data.get("action", "update")
        if not subsystem:
            return jsonify({"error": "subsystem required"}), 400
        _coord().update(subsystem, pressure, action)
        return jsonify({"updated": True, "subsystem": subsystem, "pressure": pressure})
    except Exception as exc:
        logger.exception("[Z41] /coordination/update error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Z41B: Predictive scheduling ────────────────────────────────────────────────

@cognition_z41_bp.route("/scheduling", methods=["GET"])
def z41_scheduling():
    try:
        return jsonify(_sched().report())
    except Exception as exc:
        logger.exception("[Z41] /scheduling error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z41_bp.route("/scheduling/sample", methods=["POST"])
def z41_scheduling_sample():
    try:
        data = request.get_json(force=True, silent=True) or {}
        chaos    = float(data.get("chaos_index",   _get_entropy()))
        risk     = float(data.get("resource_risk", _get_resource_risk()))
        retry    = float(data.get("retry_rate",    0.0))
        drift    = float(data.get("drift_score",   0.0))
        _sched().record_sample(chaos, risk, retry, drift)
        forecast = _sched().forecaster.forecast()
        return jsonify({"sampled": True, "forecast": forecast})
    except Exception as exc:
        logger.exception("[Z41] /scheduling/sample error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z41_bp.route("/scheduling/enqueue", methods=["POST"])
def z41_scheduling_enqueue():
    try:
        data         = request.get_json(force=True, silent=True) or {}
        execution_id = data.get("execution_id", "")
        raw_priority = data.get("priority", "NORMAL")
        _int_map = {0: "BACKGROUND", 1: "NORMAL", 2: "HIGH", 3: "CRITICAL"}
        priority = _int_map.get(raw_priority, str(raw_priority)) if isinstance(raw_priority, int) else str(raw_priority)
        entropy_boost = float(data.get("entropy_boost", 0.0))
        reason       = data.get("reason", "")
        if not execution_id:
            return jsonify({"error": "execution_id required"}), 400
        entry = _sched().queue.enqueue(execution_id, priority, entropy_boost, reason)
        return jsonify({
            "enqueued":     True,
            "execution_id": entry.execution_id,
            "priority":     entry.priority,
            "score":        entry.score,
            "queue_length": _sched().queue.queue_length(),
        })
    except Exception as exc:
        logger.exception("[Z41] /scheduling/enqueue error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Z41C: Latency intelligence ────────────────────────────────────────────────

@cognition_z41_bp.route("/latency", methods=["GET"])
def z41_latency():
    try:
        return jsonify(_latency().report())
    except Exception as exc:
        logger.exception("[Z41] /latency error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z41_bp.route("/latency/record", methods=["POST"])
def z41_latency_record():
    try:
        data        = request.get_json(force=True, silent=True) or {}
        surface     = data.get("surface", "coordination")
        duration_ms = float(data.get("duration_ms", 0.0))
        caused_by   = data.get("caused_by", "")
        trace = _latency().record(surface, duration_ms, caused_by)
        return jsonify(trace.to_dict())
    except Exception as exc:
        logger.exception("[Z41] /latency/record error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z41_bp.route("/latency/surface/<surface>", methods=["GET"])
def z41_latency_surface(surface):
    try:
        mgr    = _latency()
        stats  = mgr.tracer.surface_stats(surface)
        recent = mgr.tracer.recent_traces(surface, 10)
        return jsonify({"stats": stats, "recent_traces": recent})
    except Exception as exc:
        logger.exception("[Z41] /latency/surface error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Z41D: Adaptive priority ────────────────────────────────────────────────────

@cognition_z41_bp.route("/priority", methods=["GET"])
def z41_priority():
    try:
        return jsonify(_priority().snapshot())
    except Exception as exc:
        logger.exception("[Z41] /priority error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z41_bp.route("/priority/register", methods=["POST"])
def z41_priority_register():
    try:
        data         = request.get_json(force=True, silent=True) or {}
        chain_id     = data.get("chain_id", "")
        if not chain_id:
            return jsonify({"error": "chain_id required"}), 400
        cp = _priority().register(
            chain_id=chain_id,
            is_mission_critical=bool(data.get("is_mission_critical", False)),
            failure_count=int(data.get("failure_count", 0)),
            replay_importance=float(data.get("replay_importance", 0.5)),
            entropy=float(data.get("entropy", _get_entropy())),
        )
        return jsonify(cp.to_dict())
    except Exception as exc:
        logger.exception("[Z41] /priority/register error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z41_bp.route("/priority/rebalance", methods=["POST"])
def z41_priority_rebalance():
    try:
        data    = request.get_json(force=True, silent=True) or {}
        entropy = float(data.get("entropy", _get_entropy()))
        risk    = float(data.get("resource_risk", _get_resource_risk()))
        coord_sev = data.get("coordination_severity", _coord().report()["severity"])
        result  = _priority().rebalance_all(entropy, risk, coord_sev)
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z41] /priority/rebalance error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Z41E: Compression validation ──────────────────────────────────────────────

@cognition_z41_bp.route("/compression/validate", methods=["GET"])
def z41_compression_validate():
    try:
        return jsonify(_validate().global_report(_compression()))
    except Exception as exc:
        logger.exception("[Z41] /compression/validate error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z41_bp.route("/compression/validate/session", methods=["POST"])
def z41_compression_validate_session():
    try:
        data       = request.get_json(force=True, silent=True) or {}
        session_id = data.get("session_id", "")
        if not session_id:
            return jsonify({"error": "session_id required"}), 400
        result = _validate().validate_session(session_id, _compression())
        return jsonify(result)
    except Exception as exc:
        logger.exception("[Z41] /compression/validate/session error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Z41F: Operational pacing ──────────────────────────────────────────────────

@cognition_z41_bp.route("/pacing", methods=["GET"])
def z41_pacing():
    try:
        return jsonify(_pacing().report())
    except Exception as exc:
        logger.exception("[Z41] /pacing error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z41_bp.route("/pacing/tick", methods=["POST"])
def z41_pacing_tick():
    try:
        return jsonify(_pacing().tick())
    except Exception as exc:
        logger.exception("[Z41] /pacing/tick error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z41_bp.route("/pacing/slot/request", methods=["POST"])
def z41_pacing_slot_request():
    try:
        data      = request.get_json(force=True, silent=True) or {}
        subsystem = data.get("subsystem", "")
        if not subsystem:
            return jsonify({"error": "subsystem required"}), 400
        return jsonify(_pacing().negotiator.request_slot(subsystem))
    except Exception as exc:
        logger.exception("[Z41] /pacing/slot/request error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z41_bp.route("/pacing/slot/release", methods=["POST"])
def z41_pacing_slot_release():
    try:
        data      = request.get_json(force=True, silent=True) or {}
        subsystem = data.get("subsystem", "")
        if not subsystem:
            return jsonify({"error": "subsystem required"}), 400
        _pacing().negotiator.release_slot(subsystem)
        return jsonify({"released": True, "subsystem": subsystem})
    except Exception as exc:
        logger.exception("[Z41] /pacing/slot/release error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@cognition_z41_bp.route("/pacing/can-run/<operation>", methods=["GET"])
def z41_pacing_can_run(operation):
    try:
        force = request.args.get("force", "false").lower() == "true"
        return jsonify(_pacing().can_run(operation, force))
    except Exception as exc:
        logger.exception("[Z41] /pacing/can-run error: %s", exc)
        return jsonify({"error": str(exc)}), 500
