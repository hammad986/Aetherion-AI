"""
routes/graph_z49.py — Phase Z49C: Operational Graph API
========================================================
Z49C endpoints:
  GET  /api/graph/artifacts              — paginated artifact relationship list
  GET  /api/graph/session/<sid>          — all relationships for a session
  GET  /api/graph/replay/<replay_id>     — replay relationships + outcomes
  GET  /api/graph/recovery/<sid>         — recovery relationships for session
  GET  /api/graph/search                 — relationship-aware search
  GET  /api/graph/node/<entity_id>       — full lineage + execution ancestry
  POST /api/graph/relationship           — add artifact relationship
  POST /api/graph/execution/retry        — record retry attempt
  POST /api/graph/execution/recovery     — record recovery event
  POST /api/graph/execution/escalation   — record escalation
  POST /api/graph/execution/pressure     — record pressure snapshot
  POST /api/graph/replay/outcome         — record replay outcome
  POST /api/graph/replay/bookmark        — add replay bookmark
  GET  /api/graph/replay/<id>/bookmarks  — list replay bookmarks
  POST /api/graph/annotation             — add operator annotation
  GET  /api/graph/annotation/<type>/<id> — get annotations
  GET  /api/graph/summaries              — list summaries (paginated)
  POST /api/graph/summary/execution      — generate execution summary
  POST /api/graph/summary/replay         — generate replay summary
  POST /api/graph/summary/artifact       — generate artifact summary
  POST /api/graph/summary/failure        — generate failure summary
  GET  /api/graph/search/suggest         — search autocomplete suggestions
  GET  /api/graph/audit                  — run/list stability audits
  GET  /api/graph/maintenance            — run performance governance
  GET  /api/graph/stats                  — graph database statistics
"""
import json
import logging
import time

from flask import Blueprint, jsonify, request

from operational_graph import (
    ArtifactGraph, ExecutionMemory, GraphSearch,
    PerformanceGovernor, StabilityAuditor, SummaryEngine,
    PAGE_SIZE_DEFAULT,
)

logger = logging.getLogger("nexora.z49")

graph_z49_bp = Blueprint("graph_z49", __name__, url_prefix="/api/graph")


# ── helpers ────────────────────────────────────────────────────────────────────

def _ok(data: dict, **kw) -> tuple:
    return jsonify({"ok": True, **data, **kw}), 200


def _err(msg: str, code: int = 400) -> tuple:
    return jsonify({"ok": False, "error": msg}), code


def _page_params():
    try:
        limit  = min(int(request.args.get("limit", PAGE_SIZE_DEFAULT)), 500)
        offset = max(int(request.args.get("offset", 0)), 0)
    except (ValueError, TypeError):
        limit, offset = PAGE_SIZE_DEFAULT, 0
    return limit, offset


# ══════════════════════════════════════════════════════════════════════════════
# Z49C — Graph endpoints
# ══════════════════════════════════════════════════════════════════════════════

@graph_z49_bp.get("/artifacts")
def graph_artifacts():
    """List artifact relationships with optional type filter."""
    limit, offset = _page_params()
    rel_type = request.args.get("rel_type", "")
    session_id = request.args.get("session_id", "")

    import sqlite3, os
    from operational_graph import _connect, MAX_GRAPH_RESULTS
    limit = min(limit, MAX_GRAPH_RESULTS)

    with _connect() as c:
        filters, params = [], []
        if rel_type:
            filters.append("rel_type=?"); params.append(rel_type)
        if session_id:
            filters.append("session_id=?"); params.append(session_id)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        total = c.execute(
            f"SELECT COUNT(*) FROM artifact_relationships {where}", params
        ).fetchone()[0]
        rows = c.execute(
            f"SELECT * FROM artifact_relationships {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()

    return _ok({
        "relationships": [dict(r) for r in rows],
        "total": total, "limit": limit, "offset": offset,
    })


@graph_z49_bp.get("/session/<sid>")
def graph_session(sid: str):
    """All relationships linked to a session."""
    limit, offset = _page_params()
    rels = ArtifactGraph.list_by_session(sid, limit=limit)
    recovery = ExecutionMemory.get_recovery_log(sid, limit=50)
    escalations = ExecutionMemory.get_escalations(sid, limit=50)
    annotations = ExecutionMemory.get_annotations("session", sid)
    return _ok({
        "session_id": sid,
        "relationships": rels,
        "recovery_events": recovery,
        "escalations": escalations,
        "annotations": annotations,
    })


@graph_z49_bp.get("/replay/<replay_id>")
def graph_replay(replay_id: str):
    """Replay relationships, outcomes, and bookmarks."""
    rels      = ArtifactGraph.list_by_replay(replay_id)
    outcomes  = ExecutionMemory.get_replay_outcomes(replay_id=replay_id)
    bookmarks = ExecutionMemory.get_replay_bookmarks(replay_id)
    annotations = ExecutionMemory.get_annotations("replay", replay_id)
    return _ok({
        "replay_id": replay_id,
        "relationships": rels,
        "outcomes": outcomes,
        "bookmarks": bookmarks,
        "annotations": annotations,
    })


@graph_z49_bp.get("/recovery/<sid>")
def graph_recovery(sid: str):
    """Recovery relationships and log for a session."""
    rels    = ArtifactGraph.list_by_recovery(sid)
    log     = ExecutionMemory.get_recovery_log(sid)
    escalations = ExecutionMemory.get_escalations(sid)
    return _ok({
        "session_id": sid,
        "recovery_relationships": rels,
        "recovery_log": log,
        "escalations": escalations,
    })


@graph_z49_bp.get("/search")
def graph_search():
    """Relationship-aware full-text search."""
    query = request.args.get("q", "").strip()
    if not query:
        return _err("Query parameter 'q' is required")
    types_raw = request.args.get("types", "")
    entity_types = [t.strip() for t in types_raw.split(",") if t.strip()] or None
    artifact_scope = request.args.get("artifact_id", "")
    limit, offset = _page_params()

    if artifact_scope:
        results = GraphSearch.search_with_lineage(artifact_scope, query, limit=limit)
        return _ok({"results": results, "total": len(results), "scoped_to": artifact_scope})

    results, total = GraphSearch.search(query, entity_types=entity_types, limit=limit, offset=offset)
    return _ok({"results": results, "total": total, "limit": limit, "offset": offset})


@graph_z49_bp.get("/node/<entity_id>")
def graph_node(entity_id: str):
    """Full lineage, execution ancestry, and related data for any entity."""
    lineage     = ArtifactGraph.get_lineage(entity_id)
    deps        = ArtifactGraph.get_dependencies(entity_id)
    replay_rels = ArtifactGraph.list_by_replay(entity_id)  # if entity is a replay
    annotations = ExecutionMemory.get_annotations("artifact", entity_id)
    retries     = ExecutionMemory.get_retry_history(entity_id, limit=20)
    summary_artifact = None
    try:
        summary_artifact = SummaryEngine.generate_artifact_summary(entity_id)
    except Exception:
        pass
    return _ok({
        "entity_id": entity_id,
        "lineage": lineage,
        "dependencies": deps,
        "replay_relationships": replay_rels,
        "annotations": annotations,
        "execution_retries": retries,
        "summary": summary_artifact,
    })


# ── Relationship management ────────────────────────────────────────────────────

@graph_z49_bp.post("/relationship")
def graph_add_relationship():
    d = request.get_json(force=True, silent=True) or {}
    source_id  = (d.get("source_id") or "").strip()
    target_id  = (d.get("target_id") or "").strip()
    rel_type   = (d.get("rel_type") or "").strip()
    if not source_id or not target_id or not rel_type:
        return _err("source_id, target_id, rel_type are required")
    try:
        rel = ArtifactGraph.add_relationship(
            source_id, target_id, rel_type,
            session_id=d.get("session_id", ""),
            execution_id=d.get("execution_id", ""),
            replay_id=d.get("replay_id", ""),
            metadata=d.get("metadata"),
        )
        # Auto-index both entities for search
        GraphSearch.index_entity("artifact", source_id, f"{source_id} {rel_type}", [target_id])
        GraphSearch.index_entity("artifact", target_id, f"{target_id} {rel_type}", [source_id])
        return _ok({"relationship": rel})
    except ValueError as e:
        return _err(str(e))


# ── Execution memory writes ────────────────────────────────────────────────────

@graph_z49_bp.post("/execution/retry")
def graph_record_retry():
    d = request.get_json(force=True, silent=True) or {}
    execution_id = (d.get("execution_id") or "").strip()
    if not execution_id:
        return _err("execution_id is required")
    rid = ExecutionMemory.record_retry(
        execution_id=execution_id,
        attempt_number=int(d.get("attempt_number", 1)),
        strategy=d.get("strategy", ""),
        outcome=d.get("outcome", "unknown"),
        error_summary=d.get("error_summary", ""),
        duration_s=float(d.get("duration_s", 0)),
        session_id=d.get("session_id", ""),
    )
    return _ok({"id": rid})


@graph_z49_bp.post("/execution/recovery")
def graph_record_recovery():
    d = request.get_json(force=True, silent=True) or {}
    session_id = (d.get("session_id") or "").strip()
    if not session_id:
        return _err("session_id is required")
    rid = ExecutionMemory.record_recovery(
        session_id=session_id,
        trigger_event=d.get("trigger_event", ""),
        recovery_action=d.get("recovery_action", ""),
        outcome=d.get("outcome", "unknown"),
        execution_id=d.get("execution_id", ""),
        metadata=d.get("metadata"),
    )
    return _ok({"id": rid})


@graph_z49_bp.post("/execution/escalation")
def graph_record_escalation():
    d = request.get_json(force=True, silent=True) or {}
    session_id = (d.get("session_id") or "").strip()
    if not session_id:
        return _err("session_id is required")
    eid = ExecutionMemory.record_escalation(
        session_id=session_id,
        from_level=d.get("from_level", ""),
        to_level=d.get("to_level", ""),
        reason=d.get("reason", ""),
        execution_id=d.get("execution_id", ""),
    )
    return _ok({"id": eid})


@graph_z49_bp.post("/execution/pressure")
def graph_record_pressure():
    d = request.get_json(force=True, silent=True) or {}
    session_id = (d.get("session_id") or "").strip()
    if not session_id:
        return _err("session_id is required")
    pid = ExecutionMemory.record_pressure(
        session_id=session_id,
        cpu_pct=float(d.get("cpu_pct", 0)),
        mem_pct=float(d.get("mem_pct", 0)),
        queue_depth=int(d.get("queue_depth", 0)),
        active_workers=int(d.get("active_workers", 0)),
        pressure_score=float(d.get("pressure_score", 0)),
    )
    return _ok({"id": pid})


# ── Replay ─────────────────────────────────────────────────────────────────────

@graph_z49_bp.post("/replay/outcome")
def graph_record_replay_outcome():
    d = request.get_json(force=True, silent=True) or {}
    replay_id = (d.get("replay_id") or "").strip()
    if not replay_id:
        return _err("replay_id is required")
    rid = ExecutionMemory.record_replay_outcome(
        replay_id=replay_id,
        outcome=d.get("outcome", "unknown"),
        session_id=d.get("session_id", ""),
        artifact_id=d.get("artifact_id", ""),
        events_replayed=int(d.get("events_replayed", 0)),
        duration_s=float(d.get("duration_s", 0)),
        divergence_notes=d.get("divergence_notes", ""),
    )
    GraphSearch.index_entity("replay", replay_id, f"replay {replay_id} {d.get('outcome', '')}")
    return _ok({"id": rid})


@graph_z49_bp.post("/replay/bookmark")
def graph_add_replay_bookmark():
    d = request.get_json(force=True, silent=True) or {}
    replay_id = (d.get("replay_id") or "").strip()
    label     = (d.get("label") or "").strip()
    if not replay_id or not label:
        return _err("replay_id and label are required")
    bid = ExecutionMemory.add_replay_bookmark(
        replay_id=replay_id,
        label=label,
        event_index=int(d.get("event_index", 0)),
        timestamp_mark=float(d.get("timestamp_mark", time.time())),
        notes=d.get("notes", ""),
        session_id=d.get("session_id", ""),
    )
    return _ok({"id": bid})


@graph_z49_bp.get("/replay/<replay_id>/bookmarks")
def graph_replay_bookmarks(replay_id: str):
    limit, _ = _page_params()
    bookmarks = ExecutionMemory.get_replay_bookmarks(replay_id, limit=limit)
    return _ok({"replay_id": replay_id, "bookmarks": bookmarks})


# ── Annotations ───────────────────────────────────────────────────────────────

@graph_z49_bp.post("/annotation")
def graph_add_annotation():
    d = request.get_json(force=True, silent=True) or {}
    target_type = (d.get("target_type") or "").strip()
    target_id   = (d.get("target_id") or "").strip()
    annotation  = (d.get("annotation") or "").strip()
    if not target_type or not target_id or not annotation:
        return _err("target_type, target_id, annotation are required")
    aid = ExecutionMemory.add_annotation(
        target_type=target_type,
        target_id=target_id,
        annotation=annotation,
        author=d.get("author", "operator"),
    )
    return _ok({"id": aid})


@graph_z49_bp.get("/annotation/<target_type>/<target_id>")
def graph_get_annotations(target_type: str, target_id: str):
    annotations = ExecutionMemory.get_annotations(target_type, target_id)
    return _ok({"target_type": target_type, "target_id": target_id, "annotations": annotations})


# ── Summaries ─────────────────────────────────────────────────────────────────

@graph_z49_bp.get("/summaries")
def graph_list_summaries():
    limit, offset = _page_params()
    summary_type = request.args.get("type", "")
    session_id   = request.args.get("session_id", "")
    rows, total  = SummaryEngine.list_summaries(
        summary_type=summary_type, session_id=session_id,
        limit=limit, offset=offset,
    )
    for r in rows:
        if isinstance(r.get("key_moments"), str):
            try:
                r["key_moments"] = json.loads(r["key_moments"])
            except Exception:
                r["key_moments"] = []
    return _ok({"summaries": rows, "total": total, "limit": limit, "offset": offset})


@graph_z49_bp.post("/summary/execution")
def graph_summary_execution():
    d = request.get_json(force=True, silent=True) or {}
    session_id   = (d.get("session_id") or "").strip()
    execution_id = (d.get("execution_id") or "").strip()
    if not session_id:
        return _err("session_id is required")
    summary = SummaryEngine.generate_execution_summary(session_id, execution_id)
    return _ok({"summary": summary})


@graph_z49_bp.post("/summary/replay")
def graph_summary_replay():
    d = request.get_json(force=True, silent=True) or {}
    replay_id  = (d.get("replay_id") or "").strip()
    session_id = (d.get("session_id") or "").strip()
    if not replay_id:
        return _err("replay_id is required")
    summary = SummaryEngine.generate_replay_summary(replay_id, session_id)
    return _ok({"summary": summary})


@graph_z49_bp.post("/summary/artifact")
def graph_summary_artifact():
    d = request.get_json(force=True, silent=True) or {}
    artifact_id = (d.get("artifact_id") or "").strip()
    if not artifact_id:
        return _err("artifact_id is required")
    summary = SummaryEngine.generate_artifact_summary(artifact_id)
    return _ok({"summary": summary})


@graph_z49_bp.post("/summary/failure")
def graph_summary_failure():
    d = request.get_json(force=True, silent=True) or {}
    session_id = (d.get("session_id") or "").strip()
    if not session_id:
        return _err("session_id is required")
    summary = SummaryEngine.generate_failure_summary(session_id)
    return _ok({"summary": summary})


# ── Search autocomplete ────────────────────────────────────────────────────────

@graph_z49_bp.get("/search/suggest")
def graph_search_suggest():
    prefix      = request.args.get("q", "").strip()
    entity_type = request.args.get("type", "")
    if not prefix:
        return _ok({"suggestions": []})
    suggestions = GraphSearch.suggest(prefix, entity_type=entity_type)
    return _ok({"suggestions": suggestions, "query": prefix})


# ── Search index write ─────────────────────────────────────────────────────────

@graph_z49_bp.post("/search/index")
def graph_index_entity():
    d = request.get_json(force=True, silent=True) or {}
    entity_type = (d.get("entity_type") or "").strip()
    entity_id   = (d.get("entity_id") or "").strip()
    keywords    = (d.get("keywords") or "").strip()
    if not entity_type or not entity_id or not keywords:
        return _err("entity_type, entity_id, keywords are required")
    GraphSearch.index_entity(entity_type, entity_id, keywords, d.get("related_ids"))
    return _ok({"indexed": True})


# ── Stability audit ────────────────────────────────────────────────────────────

@graph_z49_bp.get("/audit")
def graph_audit():
    run = request.args.get("run", "0") == "1"
    if run:
        result = StabilityAuditor.run_full_audit()
        return _ok({"audit": result})
    history = StabilityAuditor.get_audit_history()
    return _ok({"history": history})


# ── Performance maintenance ────────────────────────────────────────────────────

@graph_z49_bp.get("/maintenance")
def graph_maintenance():
    run = request.args.get("run", "0") == "1"
    if run:
        result = PerformanceGovernor.run_maintenance()
        return _ok({"maintenance": result})
    stats = PerformanceGovernor.graph_stats()
    return _ok({"stats": stats})


# ── Database stats ─────────────────────────────────────────────────────────────

@graph_z49_bp.get("/stats")
def graph_stats():
    stats = PerformanceGovernor.graph_stats()
    return _ok({"stats": stats})
