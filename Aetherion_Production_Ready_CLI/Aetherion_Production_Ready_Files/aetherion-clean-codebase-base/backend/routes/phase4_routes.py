"""
backend/routes/phase4_routes.py — REST APIs for Sessions, Memory, and Background Jobs.
Fully tenant-scoped with BOLA prevention, input validation, and backwards compatibility.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from flask import Blueprint, jsonify, request, g

from auth_system import token_required, admin_required
from execution.sessions import SessionManager, SessionStatus
from execution.memory import MemoryManager, MemoryStatus, MemoryImportException
from execution.jobs import JobManager, JobStatus

logger = logging.getLogger("aetherion.routes.phase4")
phase4_bp = Blueprint("phase4", __name__)

_session_manager = SessionManager()
_memory_manager = MemoryManager()
_job_manager = JobManager()


def _get_tenant() -> str:
    user_id = getattr(g, "user_id", None)
    return str(user_id) if user_id else "default_tenant"


# ==============================================================================
# 1. SESSIONS API
# ==============================================================================

@phase4_bp.route("/api/sessions", methods=["GET"])
@token_required
def list_sessions():
    tenant = _get_tenant()
    query = request.args.get("q", "").strip()
    status = request.args.get("status")
    model = request.args.get("model")
    provider = request.args.get("provider_id")
    workspace = request.args.get("workspace_id")
    limit = min(int(request.args.get("limit", 50)), 100)

    if query or model or provider or workspace:
        sessions = _session_manager.search_sessions(
            owner_id=tenant,
            query=query,
            model=model,
            provider_id=provider,
            workspace_id=workspace,
            status=status,
            limit=limit,
        )
    else:
        sessions = _session_manager.list_sessions(owner_id=tenant, status=status, limit=limit)

    dicts = [s.to_dict() for s in sessions]
    return jsonify({"ok": True, "sessions": dicts, "data": dicts, "count": len(dicts)})


@phase4_bp.route("/api/sessions", methods=["POST"])
@phase4_bp.route("/api/sessions/create", methods=["POST"])
@token_required
def create_session():
    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    
    # Strict validation of title (P2-API-01)
    raw_title = data.get("title") if "title" in data else (data.get("name") if "name" in data else data.get("task"))
    if raw_title is None:
        return jsonify({"ok": False, "error": "title is required"}), 400
    if not isinstance(raw_title, str) or not raw_title.strip():
        return jsonify({"ok": False, "error": "title cannot be empty or whitespace-only"}), 400
    if len(raw_title.strip()) > 200:
        return jsonify({"ok": False, "error": "title exceeds maximum allowed length of 200 characters"}), 400

    title = raw_title.strip()
    model = data.get("model") or "claude-3-5-sonnet"
    provider_id = data.get("provider_id") or "anthropic"
    workspace_id = data.get("workspace_id") or "default_workspace"
    metadata = data.get("metadata") or {}

    try:
        session = _session_manager.create_session(
            owner_id=tenant,
            title=title,
            model=model,
            provider_id=provider_id,
            workspace_id=workspace_id,
            metadata=metadata,
        )
        return jsonify({"ok": True, "session": session.to_dict(), "session_id": session.id}), 201
    except Exception as exc:
        logger.error("Create session error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 400


@phase4_bp.route("/api/sessions/<sid>", methods=["GET"])
@token_required
def get_session(sid: str):
    tenant = _get_tenant()
    try:
        session = _session_manager.open_session(sid, tenant)
        bounded = _session_manager.build_bounded_context(sid, tenant)
        return jsonify({"ok": True, "session": session.to_dict(), "context": bounded})
    except KeyError:
        return jsonify({"ok": False, "error": f"Session '{sid}' not found."}), 404
    except ValueError as ve:
        return jsonify({"ok": False, "error": str(ve)}), 410


@phase4_bp.route("/api/sessions/<sid>", methods=["PUT"])
@token_required
def update_session(sid: str):
    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    title = data.get("title")
    if not title or not title.strip():
        return jsonify({"ok": False, "error": "title is required"}), 400

    try:
        updated = _session_manager.rename_session(sid, tenant, title)
        return jsonify({"ok": True, "session": updated.to_dict()})
    except KeyError:
        return jsonify({"ok": False, "error": f"Session '{sid}' not found."}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@phase4_bp.route("/api/sessions/<sid>", methods=["DELETE"])
@token_required
def delete_session(sid: str):
    tenant = _get_tenant()
    ok = _session_manager.delete_session(sid, tenant)
    if not ok:
        return jsonify({"ok": False, "error": f"Session '{sid}' not found."}), 404
    return jsonify({"ok": True, "deleted": True, "session_id": sid})


@phase4_bp.route("/api/sessions/<sid>/archive", methods=["POST"])
@token_required
def archive_session(sid: str):
    tenant = _get_tenant()
    try:
        archived = _session_manager.archive_session(sid, tenant)
        return jsonify({"ok": True, "session": archived.to_dict()})
    except KeyError:
        return jsonify({"ok": False, "error": f"Session '{sid}' not found."}), 404


@phase4_bp.route("/api/sessions/<sid>/duplicate", methods=["POST"])
@token_required
def duplicate_session(sid: str):
    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    new_title = data.get("title")
    try:
        dup = _session_manager.duplicate_session(sid, tenant, new_title=new_title)
        return jsonify({"ok": True, "session": dup.to_dict(), "session_id": dup.id}), 201
    except KeyError:
        return jsonify({"ok": False, "error": f"Session '{sid}' not found."}), 404


@phase4_bp.route("/api/sessions/<sid>/export", methods=["GET"])
@token_required
def export_session(sid: str):
    tenant = _get_tenant()
    try:
        exported = _session_manager.export_session(sid, tenant)
        return jsonify({"ok": True, "export": exported})
    except KeyError:
        return jsonify({"ok": False, "error": f"Session '{sid}' not found."}), 404


# ==============================================================================
# 2. MEMORY API
# ==============================================================================

@phase4_bp.route("/api/memory", methods=["GET"])
@token_required
def list_memory():
    tenant = _get_tenant()
    query = request.args.get("q", "").strip()
    m_type = request.args.get("type")
    status = request.args.get("status")
    source = request.args.get("source")
    limit = min(int(request.args.get("limit", 50)), 100)

    if query:
        memories = _memory_manager.search_memories(owner_id=tenant, query=query, type=m_type, limit=limit)
    else:
        memories = _memory_manager.list_memories(owner_id=tenant, type=m_type, status=status, source=source, limit=limit)

    dicts = [m.to_dict() for m in memories]
    return jsonify({"ok": True, "memories": dicts, "results": dicts, "count": len(dicts)})


@phase4_bp.route("/api/memory", methods=["POST"])
@token_required
def create_memory():
    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    title = data.get("title") or "Untitled Memory"
    content = data.get("content") or data.get("text")
    if not content or not content.strip():
        return jsonify({"ok": False, "error": "content is required"}), 400

    m_type = data.get("type") or "KNOWLEDGE"
    importance = float(data.get("importance", 0.7))
    tags = data.get("tags") or []
    source = data.get("source") or "USER_CREATED"

    try:
        mem = _memory_manager.create_memory(
            owner_id=tenant,
            title=title,
            content=content,
            type=m_type,
            importance=importance,
            tags=tags,
            source=source,
        )
        return jsonify({"ok": True, "memory": mem.to_dict(), "id": mem.id}), 201
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@phase4_bp.route("/api/memory/<mid>", methods=["GET"])
@token_required
def get_memory(mid: str):
    tenant = _get_tenant()
    mem = _memory_manager.get_memory(mid, tenant)
    if not mem:
        return jsonify({"ok": False, "error": f"Memory '{mid}' not found."}), 404
    return jsonify({"ok": True, "memory": mem.to_dict()})


@phase4_bp.route("/api/memory/<mid>", methods=["PUT"])
@token_required
def update_memory(mid: str):
    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    try:
        updated = _memory_manager.update_memory(
            memory_id=mid,
            owner_id=tenant,
            title=data.get("title"),
            content=data.get("content"),
            type=data.get("type"),
            importance=data.get("importance"),
            tags=data.get("tags"),
        )
        return jsonify({"ok": True, "memory": updated.to_dict()})
    except KeyError:
        return jsonify({"ok": False, "error": f"Memory '{mid}' not found."}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@phase4_bp.route("/api/memory/<mid>", methods=["DELETE"])
@token_required
def delete_memory(mid: str):
    tenant = _get_tenant()
    ok = _memory_manager.delete_memory(mid, tenant)
    if not ok:
        return jsonify({"ok": False, "error": f"Memory '{mid}' not found."}), 404
    return jsonify({"ok": True, "deleted": True, "memory_id": mid})


@phase4_bp.route("/api/memory/<mid>/archive", methods=["POST"])
@phase4_bp.route("/api/memory/archive", methods=["POST"])
@token_required
def archive_memory(mid: Optional[str] = None):
    tenant = _get_tenant()
    if not mid:
        data = request.get_json(silent=True) or {}
        mid = data.get("id") or data.get("memory_id")
    if not mid:
        return jsonify({"ok": False, "error": "memory_id is required"}), 400

    archived = _memory_manager.archive_memory(mid, tenant)
    if not archived:
        return jsonify({"ok": False, "error": f"Memory '{mid}' not found."}), 404
    return jsonify({"ok": True, "memory": archived.to_dict()})


@phase4_bp.route("/api/memory/<mid>/restore", methods=["POST"])
@phase4_bp.route("/api/memory/restore", methods=["POST"])
@token_required
def restore_memory(mid: Optional[str] = None):
    tenant = _get_tenant()
    if not mid:
        data = request.get_json(silent=True) or {}
        mid = data.get("id") or data.get("memory_id")
    if not mid:
        return jsonify({"ok": False, "error": "memory_id is required"}), 400

    restored = _memory_manager.restore_memory(mid, tenant)
    if not restored:
        return jsonify({"ok": False, "error": f"Memory '{mid}' not found."}), 404
    return jsonify({"ok": True, "memory": restored.to_dict()})


@phase4_bp.route("/api/memory/import", methods=["POST"])
@token_required
def import_memory():
    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    raw_content = data.get("content") or data.get("raw") or data.get("text") or data.get("data")
    commit_now = data.get("commit", False)
    preview_id = data.get("preview_id")
    format_type = data.get("format", "auto")

    try:
        if preview_id:
            # Commit previously generated preview
            committed = _memory_manager.commit_import(preview_id, owner_id=tenant)
            return jsonify({"ok": True, "committed_count": len(committed), "memories": [m.to_dict() for m in committed]}), 201

        if not raw_content:
            return jsonify({"ok": False, "error": "content or preview_id is required"}), 400

        preview = _memory_manager.preview_import(raw_content, owner_id=tenant, source_format=format_type)

        if commit_now:
            committed = _memory_manager.commit_import(preview["preview_id"], owner_id=tenant)
            return jsonify({"ok": True, "committed_count": len(committed), "memories": [m.to_dict() for m in committed]}), 201

        return jsonify({"ok": True, "preview": preview})
    except MemoryImportException as mie:
        return jsonify({"ok": False, "error": str(mie)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── Backward-Compatibility Memory Aliases & Phase 5 Hybrid Retrieval ───────────

@phase4_bp.route("/api/memory/retrieve", methods=["GET", "POST"])
@token_required
def retrieve_memory_hybrid():
    """
    Phase 5 & 6 Hybrid Semantic Retrieval endpoint combining BM25 lexical search,
    dual-engine vector similarity (Exact / IVF-Flat), recency decay, trust-tier weighting,
    and modular reranking with diversity damping.
    """
    tenant = _get_tenant()
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        query = str(data.get("query") or data.get("q") or "").strip()
        limit = min(max(1, int(data.get("limit", 10))), 100)
        try:
            alpha = float(data.get("alpha", 0.6))
        except (ValueError, TypeError):
            alpha = 0.6
        try:
            threshold = float(data.get("threshold", 0.0))
        except (ValueError, TypeError):
            threshold = 0.0
        filter_type = data.get("type")
        index_mode = data.get("index_mode", "auto")
    else:
        query = request.args.get("q", request.args.get("query", "")).strip()
        try:
            limit = min(max(1, int(request.args.get("limit", 10))), 100)
        except (ValueError, TypeError):
            limit = 10
        try:
            alpha = float(request.args.get("alpha", 0.6))
        except (ValueError, TypeError):
            alpha = 0.6
        try:
            threshold = float(request.args.get("threshold", 0.0))
        except (ValueError, TypeError):
            threshold = 0.0
        filter_type = request.args.get("type")
        index_mode = request.args.get("index_mode", "auto")

    if not query:
        return jsonify({"ok": False, "error": "query parameter is required"}), 400

    try:
        results = _memory_manager.search_hybrid(
            owner_id=tenant,
            query=query,
            limit=limit,
            alpha=alpha,
            threshold=threshold,
            filter_type=filter_type,
            index_mode=index_mode,
        )
        rendered_context = _memory_manager.build_system_memory_context(
            query=query,
            owner_id=tenant,
            max_memories=limit,
            max_chars=3000,
            index_mode=index_mode,
        )
        return jsonify({
            "ok": True,
            "query": query,
            "count": len(results),
            "results": results,
            "rendered_context": rendered_context,
            "index_mode": index_mode,
        })
    except Exception as exc:
        logger.error("Hybrid memory retrieval error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@phase4_bp.route("/api/memory/maintenance/<action>", methods=["POST"])
@token_required
@admin_required
def memory_maintenance(action: str):
    """
    Trigger Phase 6/7 memory maintenance jobs asynchronously.
    Returns immediately with a job_id; use GET /api/jobs/<job_id> to poll status.
    Restricted to administrators to prevent resource exhaustion and DoS.
    """
    import threading
    import uuid

    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    batch_size = max(1, min(int(data.get("batch_size", 50)), 500))

    # Map action names to (job_name, kwargs_builder) pairs
    _VALID_ACTIONS = {
        "backfill": "embedding_backfill",
        "repair": "stale_embedding_repair",
        "dedup": "duplicate_detection",
        "duplicates": "duplicate_detection",
        "reindex": "index_rebuild",
        "rebuild": "index_rebuild",
        "contradiction": "contradiction_scan",
        "contradictions": "contradiction_scan",
        "conflicts": "contradiction_scan",
        "consolidation": "consolidation_scan",
        "consolidations": "consolidation_scan",
        "consolidate": "consolidation_scan",
        "prune_cache": "cache_prune_expired",
        "cache_prune": "cache_prune_expired",
        "lifecycle": "memory_lifecycle_cleanup",
        "staleness_prune": "memory_lifecycle_cleanup",
        "prune_staleness": "memory_lifecycle_cleanup",
    }

    if action not in _VALID_ACTIONS:
        return jsonify({"ok": False, "error": f"Unknown maintenance action '{action}'"}), 400

    maintenance_job_name = _VALID_ACTIONS[action]

    # Build kwargs appropriate for the maintenance job
    if action in ("dedup", "duplicates"):
        extra_kwargs: Dict[str, Any] = {"similarity_threshold": float(data.get("similarity_threshold", 0.92))}
    elif action in ("reindex", "rebuild"):
        extra_kwargs = {"force_mode": data.get("force_mode")}
    elif action in ("prune_cache", "cache_prune"):
        extra_kwargs = {}
    else:
        extra_kwargs = {"limit": batch_size}

    try:
        # Per-tenant concurrency guard: prevent DoS through unbounded concurrent maintenance jobs
        active_jobs = _job_manager.list_jobs(owner_id=tenant, status=JobStatus.RUNNING.value, limit=10)
        if isinstance(active_jobs, list):
            running_maint = [j for j in active_jobs if getattr(j, "name", "") and getattr(j, "name", "").startswith("memory_maintenance:")]
            if len(running_maint) >= 3:
                return jsonify({
                    "ok": False,
                    "error": "Concurrent maintenance job limit exceeded (max 3). Please wait for active maintenance jobs to complete.",
                }), 429

        # Create a tracked job record (QUEUED state)
        job = _job_manager.create_job(
            owner_id=tenant,
            name=f"memory_maintenance:{action}",
            task=maintenance_job_name,
            metadata={"action": action, "type": "maintenance", "batch_size": batch_size, **{k: str(v) for k, v in extra_kwargs.items()}},
        )
        job_id = job.id

        # Dispatch maintenance work through JobRunner — tracked in _active_jobs with cancellation & attempt recording
        _job_manager.start_job(job_id=job_id, owner_id=tenant)

        return jsonify({
            "ok": True,
            "job_id": job_id,
            "status": "queued",
            "action": action,
            "message": f"Maintenance job '{action}' dispatched asynchronously via JobRunner. Poll GET /api/jobs/{job_id} for status.",
        }), 202

    except Exception as exc:
        logger.error("Memory maintenance dispatch error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500



# ── Phase 7 Intelligence Endpoints ───────────────────────────────────────────

@phase4_bp.route("/api/memory/conflicts", methods=["GET"])
@phase4_bp.route("/api/memory/contradictions", methods=["GET"])
@token_required
def list_memory_conflicts():
    tenant = _get_tenant()
    state = request.args.get("state")
    conflicts = _memory_manager.list_contradictions(tenant, state=state)
    return jsonify({"ok": True, "conflicts": conflicts, "count": len(conflicts)})


@phase4_bp.route("/api/memory/conflicts/scan", methods=["POST"])
@phase4_bp.route("/api/memory/contradictions/scan", methods=["POST"])
@token_required
def scan_memory_conflicts():
    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    limit = min(max(1, int(data.get("limit", 100))), 500)
    detected = _memory_manager.scan_contradictions(tenant, limit=limit, persist=True)
    return jsonify({"ok": True, "detected_conflicts": detected, "count": len(detected)})


@phase4_bp.route("/api/memory/conflicts/<cid>/resolve", methods=["POST"])
@phase4_bp.route("/api/memory/contradictions/<cid>/resolve", methods=["POST"])
@token_required
def resolve_memory_conflict(cid: str):
    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    strategy = data.get("strategy", "resolve")
    winner_id = data.get("winner_id")
    ok = _memory_manager.resolve_contradiction(cid, tenant, strategy=strategy, winner_id=winner_id)
    if not ok:
        return jsonify({"ok": False, "error": f"Contradiction '{cid}' not found or update failed."}), 404
    return jsonify({"ok": True, "resolved": True, "conflict_id": cid, "strategy": strategy})


@phase4_bp.route("/api/memory/consolidate", methods=["GET"])
@phase4_bp.route("/api/memory/consolidations", methods=["GET"])
@token_required
def list_memory_consolidations():
    tenant = _get_tenant()
    state = request.args.get("state")
    consolidations = _memory_manager.list_consolidations(tenant, state=state)
    return jsonify({"ok": True, "consolidations": consolidations, "count": len(consolidations)})


@phase4_bp.route("/api/memory/consolidate/scan", methods=["POST"])
@phase4_bp.route("/api/memory/consolidations/scan", methods=["POST"])
@token_required
def scan_memory_consolidations():
    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    limit = min(max(1, int(data.get("limit", 200))), 1000)
    proposals = _memory_manager.scan_consolidations(tenant, limit=limit, persist=True)
    return jsonify({"ok": True, "proposed_consolidations": proposals, "count": len(proposals)})


@phase4_bp.route("/api/memory/consolidate/<cid>/apply", methods=["POST"])
@phase4_bp.route("/api/memory/consolidations/<cid>/apply", methods=["POST"])
@token_required
def apply_memory_consolidation(cid: str):
    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    merge_tags = data.get("merge_tags", True)
    merge_metadata = data.get("merge_metadata", True)
    ok = _memory_manager.apply_consolidation(cid, tenant, merge_tags=merge_tags, merge_metadata=merge_metadata)
    if not ok:
        return jsonify({"ok": False, "error": f"Consolidation proposal '{cid}' not found or apply failed."}), 404
    return jsonify({"ok": True, "applied": True, "consolidation_id": cid})


@phase4_bp.route("/api/memory/consolidate/<cid>/reject", methods=["POST"])
@phase4_bp.route("/api/memory/consolidations/<cid>/reject", methods=["POST"])
@token_required
def reject_memory_consolidation(cid: str):
    tenant = _get_tenant()
    ok = _memory_manager.reject_consolidation(cid, tenant)
    if not ok:
        return jsonify({"ok": False, "error": f"Consolidation proposal '{cid}' not found."}), 404
    return jsonify({"ok": True, "rejected": True, "consolidation_id": cid})


@phase4_bp.route("/api/memory/evaluate", methods=["POST"])
@token_required
def evaluate_memory_retrieval():
    eval_res = _memory_manager.evaluate_retrieval()
    return jsonify({"ok": True, "benchmark_results": eval_res})


@phase4_bp.route("/api/memory/health", methods=["GET"])
@token_required
def get_memory_health_report():
    tenant = _get_tenant()
    health = _memory_manager.get_memory_health(tenant)
    return jsonify({"ok": True, "health": health})


@phase4_bp.route("/api/memory/telemetry", methods=["GET"])
@token_required
def get_memory_telemetry_metrics():
    """
    Retrieve Phase 6 operational telemetry and cache statistics.
    Tenants observe strictly their own operational metrics.
    Administrators can query global aggregate metrics with ?global=1.
    """
    tenant = _get_tenant()
    is_admin = getattr(g, "user_role", "user") == "admin"
    show_global = is_admin and request.args.get("global") == "1"

    if show_global:
        telemetry = _memory_manager.get_telemetry(include_global=True)
        cache_stats = _memory_manager.get_cache_stats()
    else:
        telemetry = _memory_manager.get_telemetry(owner_id=tenant)
        cache_stats = _memory_manager.get_cache_stats(owner_id=tenant)

    return jsonify({
        "ok": True,
        "telemetry": telemetry,
        "cache": cache_stats,
    })



@phase4_bp.route("/api/memory/list", methods=["GET"])
@phase4_bp.route("/api/memory/search", methods=["GET"])
@token_required
def legacy_memory_list_search():
    return list_memory()


@phase4_bp.route("/api/memory/remember", methods=["POST"])
@token_required
def legacy_memory_remember():
    return create_memory()


@phase4_bp.route("/api/memory/clear", methods=["POST"])
@token_required
def legacy_memory_clear():
    tenant = _get_tenant()
    memories = _memory_manager.list_memories(owner_id=tenant, limit=500)
    for m in memories:
        _memory_manager.delete_memory(m.id, tenant)
    return jsonify({"ok": True, "cleared": True})


# ==============================================================================
# 3. BACKGROUND JOBS API
# ==============================================================================

@phase4_bp.route("/api/jobs", methods=["GET"])
@token_required
def list_jobs():
    tenant = _get_tenant()
    status = request.args.get("status")
    limit = min(int(request.args.get("limit", 50)), 100)
    jobs = _job_manager.list_jobs(owner_id=tenant, status=status, limit=limit)
    dicts = [j.to_dict() for j in jobs]
    return jsonify({"ok": True, "jobs": dicts, "count": len(dicts)})


@phase4_bp.route("/api/jobs", methods=["POST"])
@token_required
def create_job():
    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    name = data.get("name") or "Untitled Job"
    task = data.get("task") or data.get("prompt")
    if not task or not task.strip():
        return jsonify({"ok": False, "error": "task is required"}), 400

    workspace_id = data.get("workspace_id") or "default_workspace"
    session_id = data.get("session_id")
    metadata = data.get("metadata") or {}

    try:
        job = _job_manager.create_job(
            owner_id=tenant,
            name=name,
            task=task,
            workspace_id=workspace_id,
            session_id=session_id,
            metadata=metadata,
        )
        return jsonify({"ok": True, "job": job.to_dict(), "job_id": job.id}), 201
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@phase4_bp.route("/api/jobs/<jid>", methods=["GET"])
@token_required
def get_job(jid: str):
    tenant = _get_tenant()
    job = _job_manager.get_job(jid, tenant)
    if not job:
        return jsonify({"ok": False, "error": f"Job '{jid}' not found."}), 404
    return jsonify({"ok": True, "job": job.to_dict()})


@phase4_bp.route("/api/jobs/<jid>/start", methods=["POST"])
@token_required
def start_job(jid: str):
    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    workspace_dir = data.get("workspace_dir") or Path.cwd()
    try:
        job = _job_manager.start_job(jid, tenant, workspace_dir=workspace_dir)
        return jsonify({"ok": True, "job": job.to_dict(), "status": job.status})
    except KeyError:
        return jsonify({"ok": False, "error": f"Job '{jid}' not found."}), 404
    except ValueError as ve:
        return jsonify({"ok": False, "error": str(ve)}), 409


@phase4_bp.route("/api/jobs/<jid>/pause", methods=["POST"])
@token_required
def pause_job(jid: str):
    tenant = _get_tenant()
    try:
        ok = _job_manager.pause_job(jid, tenant)
        return jsonify({"ok": True, "paused": ok, "job_id": jid})
    except KeyError:
        return jsonify({"ok": False, "error": f"Job '{jid}' not found."}), 404
    except ValueError as ve:
        return jsonify({"ok": False, "error": str(ve)}), 409


@phase4_bp.route("/api/jobs/<jid>/resume", methods=["POST"])
@token_required
def resume_job(jid: str):
    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    workspace_dir = data.get("workspace_dir") or Path.cwd()
    try:
        resumed = _job_manager.resume_job(jid, tenant, workspace_dir=workspace_dir)
        return jsonify({"ok": True, "job": resumed.to_dict()})
    except KeyError:
        return jsonify({"ok": False, "error": f"Job '{jid}' not found."}), 404
    except ValueError as ve:
        return jsonify({"ok": False, "error": str(ve)}), 409


@phase4_bp.route("/api/jobs/<jid>/cancel", methods=["POST"])
@token_required
def cancel_job(jid: str):
    tenant = _get_tenant()
    try:
        ok = _job_manager.cancel_job(jid, tenant)
        return jsonify({"ok": True, "cancelled": ok, "job_id": jid})
    except KeyError:
        return jsonify({"ok": False, "error": f"Job '{jid}' not found."}), 404


@phase4_bp.route("/api/jobs/<jid>/retry", methods=["POST"])
@token_required
def retry_job(jid: str):
    tenant = _get_tenant()
    data = request.get_json(silent=True) or {}
    workspace_dir = data.get("workspace_dir")
    try:
        retried = _job_manager.retry_job(jid, tenant, workspace_dir=workspace_dir)
        return jsonify({"ok": True, "job": retried.to_dict(), "attempt_number": retried.attempt_number})
    except KeyError:
        return jsonify({"ok": False, "error": f"Job '{jid}' not found."}), 404
    except ValueError as ve:
        return jsonify({"ok": False, "error": str(ve)}), 409


@phase4_bp.route("/api/jobs/<jid>", methods=["DELETE"])
@token_required
def delete_job(jid: str):
    tenant = _get_tenant()
    try:
        ok = _job_manager.delete_job(jid, tenant)
        if not ok:
            return jsonify({"ok": False, "error": f"Job '{jid}' not found."}), 404
        return jsonify({"ok": True, "deleted": True, "job_id": jid})
    except KeyError:
        return jsonify({"ok": False, "error": f"Job '{jid}' not found."}), 404
    except ValueError as ve:
        return jsonify({"ok": False, "error": str(ve)}), 409


@phase4_bp.route("/api/jobs/<jid>/attempts", methods=["GET"])
@token_required
def list_job_attempts(jid: str):
    tenant = _get_tenant()
    try:
        attempts = _job_manager.get_attempts(jid, tenant)
        return jsonify({"ok": True, "attempts": [a.to_dict() for a in attempts]})
    except KeyError:
        return jsonify({"ok": False, "error": f"Job '{jid}' not found."}), 404
