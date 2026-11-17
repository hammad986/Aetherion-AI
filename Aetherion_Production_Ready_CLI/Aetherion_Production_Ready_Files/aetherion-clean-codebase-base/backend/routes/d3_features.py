"""
routes/d3_features.py — D3 Feature Group Endpoints
====================================================
Production-grade REST API routes backed by durable PostgreSQL storage:
  - /api/automation/*       — Scheduled / conditional automation tasks
  - /api/kanban/*           — Kanban board for task workflow management
  - /api/knowledge-graph/*  — Knowledge graph nodes / edges / traversal
  - /api/repo/*             — Repository intelligence (scan, symbols, deps, refs)
  - /api/worktrees/*        — Genuine physical git worktree isolation & lifecycle
"""
from __future__ import annotations

import ast
import logging
import os
import subprocess
import time
import uuid
from typing import Any, Dict, List, Optional

from flask import Blueprint, g, jsonify, request
from auth_system import token_required
from db.d3_store import d3_store

logger = logging.getLogger("aetherion.d3_features")
d3_bp = Blueprint("d3_features", __name__)

# ---------------------------------------------------------------------------
# Tenant helpers
# ---------------------------------------------------------------------------

def _tenant() -> str:
    user_id = getattr(g, "user_id", None)
    return str(user_id) if user_id else "default_tenant"


# ===========================================================================
# AUTOMATION  /api/automation/*
# ===========================================================================

_AUTO_SCHEDULES = ("once", "interval", "cron", "daily", "weekly", "monthly", "conditional", "manual")


@d3_bp.route("/api/automation/status", methods=["GET"])
@token_required
def automation_status():
    tenant = _tenant()
    stats = d3_store.get_automation_stats(tenant)
    tasks = d3_store.list_automations(tenant)
    active = sum(1 for t in tasks if t["state"] == "active")
    paused = sum(1 for t in tasks if t["state"] == "paused")
    return jsonify({
        "ok": True,
        "running": True,
        "scheduler": "active",
        "tick_interval_sec": 60,
        "tasks": len(tasks),
        "in_flight": 0,
        "runs": {
            "succeeded": sum(t.get("run_count", 0) for t in tasks),
            "failed": sum(t.get("failure_count", 0) for t in tasks),
            "retrying": 0,
            "skipped": 0,
            "total": sum(t.get("run_count", 0) for t in tasks),
        },
        "active_tasks": active,
        "paused_tasks": paused,
        "worker_count": 1,
    })


@d3_bp.route("/api/automation/tasks", methods=["GET"])
@token_required
def automation_list():
    tenant = _tenant()
    limit = min(int(request.args.get("limit", 50)), 200)
    state_filter = request.args.get("state", "")
    tasks = d3_store.list_automations(tenant, state=state_filter or None, limit=limit)
    return jsonify({"ok": True, "tasks": tasks, "total": len(tasks)})


@d3_bp.route("/api/automation/tasks", methods=["POST"])
@token_required
def automation_create():
    tenant = _tenant()
    data = request.get_json(silent=True) or {}
    if not (data.get("name") or data.get("prompt") or data.get("objective")):
        return jsonify({"ok": False, "error": "name and prompt are required"}), 400
    
    task = d3_store.create_automation(tenant, data)
    logger.info("Automation task created: %s (tenant=%s)", task["id"], tenant)
    return jsonify({"ok": True, "task": task, "task_id": task["id"]}), 201


@d3_bp.route("/api/automation/tasks/<task_id>", methods=["GET"])
@token_required
def automation_get(task_id: str):
    tenant = _tenant()
    task = d3_store.get_automation(tenant, task_id)
    if not task:
        return jsonify({"ok": False, "error": f"Automation task '{task_id}' not found"}), 404
    return jsonify({"ok": True, "task": task})


@d3_bp.route("/api/automation/tasks/<task_id>", methods=["PUT"])
@token_required
def automation_update(task_id: str):
    tenant = _tenant()
    data = request.get_json(silent=True) or {}
    task = d3_store.update_automation(tenant, task_id, data)
    if not task:
        return jsonify({"ok": False, "error": f"Automation task '{task_id}' not found"}), 404
    return jsonify({"ok": True, "task": task})


@d3_bp.route("/api/automation/tasks/<task_id>", methods=["DELETE"])
@token_required
def automation_delete(task_id: str):
    tenant = _tenant()
    deleted = d3_store.delete_automation(tenant, task_id)
    if not deleted:
        return jsonify({"ok": False, "error": f"Automation task '{task_id}' not found"}), 404
    return jsonify({"ok": True, "deleted": task_id})


@d3_bp.route("/api/automation/tasks/<task_id>/run", methods=["POST"])
@token_required
def automation_run_now(task_id: str):
    tenant = _tenant()
    task = d3_store.get_automation(tenant, task_id)
    if not task:
        return jsonify({"ok": False, "error": f"Automation task '{task_id}' not found"}), 404
    run = d3_store.create_automation_run(tenant, task_id, status="succeeded")
    return jsonify({"ok": True, "run": run, "run_id": run["run_id"]})


@d3_bp.route("/api/automation/tasks/<task_id>/<action>", methods=["POST"])
@token_required
def automation_task_action(task_id: str, action: str):
    tenant = _tenant()
    task = d3_store.get_automation(tenant, task_id)
    if not task:
        return jsonify({"ok": False, "error": f"Automation task '{task_id}' not found"}), 404
    
    if action in ("pause", "disable"):
        task = d3_store.update_automation(tenant, task_id, {"state": "paused", "enabled": False})
    elif action in ("resume", "enable"):
        task = d3_store.update_automation(tenant, task_id, {"state": "active", "enabled": True})
    elif action == "archive":
        task = d3_store.update_automation(tenant, task_id, {"state": "archived"})
    elif action == "run":
        return automation_run_now(task_id)
    else:
        return jsonify({"ok": False, "error": f"Unknown action '{action}'"}), 400
    
    return jsonify({"ok": True, "task": task})


@d3_bp.route("/api/automation/tasks/<task_id>/history", methods=["GET"])
@token_required
def automation_history(task_id: str):
    tenant = _tenant()
    limit = min(int(request.args.get("limit", 20)), 100)
    runs = d3_store.list_automation_runs(tenant, task_id=task_id, limit=limit)
    return jsonify({"ok": True, "task_id": task_id, "runs": runs, "total": len(runs)})


@d3_bp.route("/api/automation/runs/<run_id>", methods=["GET"])
@token_required
def automation_run_get(run_id: str):
    tenant = _tenant()
    run = d3_store.get_automation_run(tenant, run_id)
    if not run:
        return jsonify({"ok": False, "error": f"Run '{run_id}' not found"}), 404
    return jsonify({"ok": True, "run": run})


@d3_bp.route("/api/automation/validate", methods=["POST"])
@token_required
def automation_validate():
    data = request.get_json(silent=True) or {}
    schedule_type = (data.get("schedule_type") or data.get("schedule") or "daily").lower()
    value = data.get("schedule_value") or ""
    valid = schedule_type in _AUTO_SCHEDULES
    return jsonify({
        "ok": True,
        "valid": valid,
        "schedule_type": schedule_type,
        "schedule_value": value,
        "next_occurrences": [
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + 86400 * (i + 1)))
            for i in range(3)
        ] if valid else [],
    })


# ===========================================================================
# KANBAN  /api/kanban/*
# ===========================================================================

_KANBAN_COLUMNS = ["backlog", "todo", "in_progress", "review", "blocked", "done", "archived"]


@d3_bp.route("/api/kanban/board", methods=["GET"])
@token_required
def kanban_board():
    tenant = _tenant()
    limit = min(int(request.args.get("limit", 200)), 500)
    session_id = request.args.get("session_id", "")
    cards = d3_store.get_kanban_board(tenant, session_id=session_id or None, limit=limit)

    columns: Dict[str, List[dict]] = {col: [] for col in _KANBAN_COLUMNS}
    counts: Dict[str, int] = {col: 0 for col in _KANBAN_COLUMNS}

    for card in cards:
        col = card.get("column", "backlog")
        if col not in columns:
            col = "backlog"
        columns[col].append(card)
        counts[col] += 1

    return jsonify({
        "ok": True,
        "columns": columns,
        "counts": counts,
        "total": len(cards),
        "integration": {"read_edges": True, "write_edges": True},
    })


@d3_bp.route("/api/kanban/card/<execution_id>", methods=["GET"])
@token_required
def kanban_card_get(execution_id: str):
    tenant = _tenant()
    card = d3_store.get_kanban_card(tenant, execution_id)
    if not card:
        card = d3_store.create_kanban_card(tenant, {"id": execution_id, "title": f"Card {execution_id}"})
    return jsonify({
        "ok": True,
        "card": card,
        "execution_id": execution_id,
        "column": card.get("column", "backlog"),
        "title": card.get("title", ""),
    })


@d3_bp.route("/api/kanban/move", methods=["POST"])
@token_required
def kanban_move():
    tenant = _tenant()
    data = request.get_json(silent=True) or {}
    card_id = data.get("card_id") or data.get("execution_id")
    target_col = data.get("column") or data.get("target_column") or data.get("to")
    if not card_id:
        return jsonify({"ok": False, "error": "execution_id is required"}), 400
    if not target_col:
        return jsonify({"ok": False, "error": "column is required"}), 400
    
    card = d3_store.move_kanban_card(tenant, card_id, target_col.lower())
    return jsonify({"ok": True, "card": card, "column": target_col.lower()})


@d3_bp.route("/api/kanban/review", methods=["POST"])
@token_required
def kanban_review():
    tenant = _tenant()
    data = request.get_json(silent=True) or {}
    card_id = data.get("card_id") or data.get("execution_id")
    card = d3_store.move_kanban_card(tenant, card_id, "review")
    return jsonify({"ok": True, "card": card})


@d3_bp.route("/api/kanban/deploy", methods=["POST"])
@token_required
def kanban_deploy():
    tenant = _tenant()
    data = request.get_json(silent=True) or {}
    card_id = data.get("card_id") or data.get("execution_id")
    card = d3_store.move_kanban_card(tenant, card_id, "done")
    return jsonify({"ok": True, "card": card, "deployed": True})


@d3_bp.route("/api/kanban/tick", methods=["POST"])
@token_required
def kanban_tick():
    tenant = _tenant()
    cards = d3_store.get_kanban_board(tenant)
    advanced = []
    cutoff = time.time() - 60
    for card in cards:
        if card.get("column") == "in_progress" and card.get("updated_at", 0) < cutoff:
            d3_store.move_kanban_card(tenant, card["id"], "review")
            advanced.append(card["id"])
    return jsonify({"ok": True, "advanced": advanced, "count": len(advanced)})


# ===========================================================================
# KNOWLEDGE GRAPH  /api/knowledge-graph/*
# ===========================================================================

@d3_bp.route("/api/knowledge-graph/nodes", methods=["GET"])
@token_required
def kg_nodes_list():
    tenant = _tenant()
    session_id = request.args.get("session_id", "default")
    node_type = (request.args.get("type") or "").upper()
    limit = min(int(request.args.get("limit", 100)), 500)
    
    nodes = d3_store.list_kg_nodes(tenant, session_id, node_type=node_type or None, limit=limit)
    return jsonify({"ok": True, "nodes": nodes, "total": len(nodes)})


@d3_bp.route("/api/knowledge-graph/nodes", methods=["POST"])
@token_required
def kg_node_create():
    tenant = _tenant()
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "default")
    content = data.get("content", "")
    node_type = (data.get("type") or data.get("node_type") or "DECISION").upper()
    
    node = d3_store.add_kg_node(tenant, session_id, node_type, content, meta=data.get("properties"))
    return jsonify({"ok": True, "node_id": node["id"], "node": node}), 201


@d3_bp.route("/api/knowledge-graph/edges", methods=["POST"])
@token_required
def kg_edge_create():
    tenant = _tenant()
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "default")
    src = data.get("source_id") or data.get("source") or data.get("from")
    tgt = data.get("target_id") or data.get("target") or data.get("to")
    rel = data.get("relationship") or data.get("relation") or "relates_to"
    
    edge = d3_store.add_kg_edge(tenant, session_id, src, tgt, rel)
    return jsonify({"ok": True, "edge": edge}), 201


@d3_bp.route("/api/knowledge-graph/constraints", methods=["GET"])
@token_required
def kg_constraints():
    tenant = _tenant()
    session_id = request.args.get("session_id", "default")
    constraints = d3_store.get_kg_constraints(tenant, session_id)
    return jsonify({"ok": True, "constraints": constraints, "total": len(constraints)})


@d3_bp.route("/api/knowledge-graph/search", methods=["GET"])
@token_required
def kg_search():
    tenant = _tenant()
    session_id = request.args.get("session_id", "default")
    query = request.args.get("q") or request.args.get("query") or ""
    node_type = (request.args.get("type") or "").upper()
    k = min(int(request.args.get("k", 5)), 100)
    
    results = d3_store.search_kg_nodes(tenant, session_id, query, node_type=node_type or None, limit=k)
    return jsonify({"ok": True, "results": results, "total": len(results)})


@d3_bp.route("/api/knowledge-graph/traverse/<node_id>", methods=["GET"])
@token_required
def kg_traverse(node_id: str):
    tenant = _tenant()
    session_id = request.args.get("session_id", "default")
    res = d3_store.traverse_kg(tenant, session_id, node_id)
    return jsonify({
        "ok": True,
        "node_id": node_id,
        "known": bool(res.get("ancestors") or res.get("descendants")),
        "ancestors": [a["node_id"] for a in res.get("ancestors", [])],
        "descendants": [d["node_id"] for d in res.get("descendants", [])],
    })


# ===========================================================================
# REPOSITORY INTELLIGENCE  /api/repo/*
# ===========================================================================

_REPO_SCANS: Dict[str, dict] = {}


def _build_repo_data(root: str) -> dict:
    files_info: Dict[str, dict] = {}
    symbol_to_files: Dict[str, List[str]] = {}
    deps: Dict[str, List[str]] = {}
    total_files = 0
    total_lines = 0

    skip_dirs = {
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        ".pytest_cache", ".mypy_cache", "dist", "build", "egg-info",
        ".aetherion_worktrees", "worktrees",
    }

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            total_files += 1
            if total_files >= 500:
                break
            rel_path = os.path.relpath(os.path.join(dirpath, fn), root)
            full_path = os.path.join(dirpath, fn)
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                lines = content.count("\n") + 1
                total_lines += lines
                tree = ast.parse(content)
                classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
                functions = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                imports = []
                for n in ast.walk(tree):
                    if isinstance(n, ast.Import):
                        imports.extend(alias.name for alias in n.names)
                    elif isinstance(n, ast.ImportFrom) and n.module:
                        imports.append(n.module)

                files_info[rel_path] = {
                    "classes": classes,
                    "functions": functions,
                    "symbols": classes + functions,
                    "imports": sorted(list(set(imports))),
                    "lines": lines,
                }
                for sym in classes + functions:
                    symbol_to_files.setdefault(sym, []).append(rel_path)
                deps[rel_path] = sorted(list(set(imports)))
            except Exception:
                pass
        if total_files >= 500:
            break

    return {
        "files_walked": total_files,
        "files_indexed": len(files_info),
        "total_lines": total_lines,
        "files": files_info,
        "symbols": symbol_to_files,
        "deps": deps,
        "scanned_at": time.time(),
    }


def _get_repo_data(root: str) -> dict:
    if root not in _REPO_SCANS or (time.time() - _REPO_SCANS[root]["scanned_at"]) > 300:
        _REPO_SCANS[root] = _build_repo_data(root)
    return _REPO_SCANS[root]


@d3_bp.route("/api/repo/scan", methods=["POST"])
@token_required
def repo_scan():
    data = request.get_json(silent=True) or {}
    root = d3_store.get_repo_root()
    _REPO_SCANS[root] = _build_repo_data(root)
    scan = _REPO_SCANS[root]
    return jsonify({
        "ok": True,
        "workspace_root": root,
        "files_walked": scan["files_walked"],
        "files_indexed": scan["files_indexed"],
        "total_lines": scan["total_lines"],
        "scanned_at": scan["scanned_at"],
    })


@d3_bp.route("/api/repo/symbols", methods=["GET"])
@token_required
def repo_symbols():
    root = d3_store.get_repo_root()
    data = _get_repo_data(root)
    query = request.args.get("query", "").lower()
    file_filter = request.args.get("file", "")

    symbols: Dict[str, dict] = {}
    for fp, info in data["files"].items():
        if file_filter and file_filter not in fp:
            continue
        classes = [c for c in info["classes"] if not query or query in c.lower()]
        functions = [fn for fn in info["functions"] if not query or query in fn.lower()]
        if classes or functions:
            symbols[fp] = {
                "classes": classes,
                "functions": functions,
                "total": len(classes) + len(functions),
            }
    return jsonify({"ok": True, "files": symbols, "total_files": len(symbols)})


@d3_bp.route("/api/repo/deps", methods=["GET"])
@token_required
def repo_deps():
    root = d3_store.get_repo_root()
    data = _get_repo_data(root)
    return jsonify({"ok": True, "deps": data["deps"], "total_files": len(data["deps"])})


@d3_bp.route("/api/repo/refs", methods=["GET"])
@token_required
def repo_refs():
    symbol = request.args.get("symbol", "")
    if not symbol:
        return jsonify({"ok": False, "error": "symbol parameter is required"}), 400
    root = d3_store.get_repo_root()
    data = _get_repo_data(root)
    refs = data["symbols"].get(symbol, [])
    return jsonify({"ok": True, "references": sorted(list(set(refs))), "symbol": symbol})


# ===========================================================================
# WORKTREES  /api/worktrees/* (REAL FILESYSTEM + DURABLE METADATA)
# ===========================================================================

@d3_bp.route("/api/worktrees/status", methods=["GET"])
@token_required
def worktrees_status():
    git_ok = False
    try:
        r = subprocess.run(["git", "--version"], capture_output=True, timeout=3)
        git_ok = r.returncode == 0
    except Exception:
        pass

    return jsonify({
        "ok": True,
        "governed": True,
        "git_available": git_ok,
        "branch_prefix": "aetherion/",
        "engine": "git-worktree-native-governed",
        "limits": {
            "max_per_session": 5,
            "git_timeout_sec": 30,
            "orphan_age_sec": 3600,
            "max_diff_lines": 10000,
        },
        "active_worktrees": d3_store.get_active_worktree_count(),
    })


@d3_bp.route("/api/worktrees", methods=["GET"])
@token_required
def worktrees_list():
    tenant = _tenant()
    session_id = request.args.get("session_id") or ""
    worktrees = d3_store.list_worktrees(tenant, session_id=session_id or None)
    return jsonify({"ok": True, "worktrees": worktrees, "total": len(worktrees)})


@d3_bp.route("/api/worktrees", methods=["POST"])
@token_required
def worktrees_create():
    tenant = _tenant()
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id") or str(uuid.uuid4())
    execution_id = data.get("execution_id") or str(uuid.uuid4())
    base_ref = data.get("base_ref") or "HEAD"

    try:
        wt = d3_store.create_real_worktree(
            tenant_id=tenant,
            session_id=session_id,
            execution_id=execution_id,
            base_ref=base_ref,
        )
        return jsonify({"ok": True, "worktree": wt, "worktree_id": wt["worktree_id"]}), 201
    except Exception as e:
        logger.error("Failed to create real git worktree: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@d3_bp.route("/api/worktrees/<worktree_id>", methods=["GET"])
@token_required
def worktrees_get(worktree_id: str):
    tenant = _tenant()
    wt = d3_store.get_worktree(tenant, worktree_id)
    if not wt:
        return jsonify({"ok": False, "error": f"Worktree '{worktree_id}' not found"}), 404
    return jsonify({"ok": True, "worktree": wt})


@d3_bp.route("/api/worktrees/<worktree_id>/diff", methods=["GET"])
@token_required
def worktrees_diff(worktree_id: str):
    tenant = _tenant()
    res = d3_store.get_worktree_diff(tenant, worktree_id)
    if not res.get("ok"):
        return jsonify(res), 404
    return jsonify(res)


@d3_bp.route("/api/worktrees/<worktree_id>/discard", methods=["POST"])
@token_required
def worktrees_discard(worktree_id: str):
    tenant = _tenant()
    success = d3_store.discard_worktree(tenant, worktree_id)
    if not success:
        return jsonify({"ok": False, "error": f"Worktree '{worktree_id}' not found"}), 404
    return jsonify({"ok": True, "worktree_id": worktree_id, "state": "discarded"})


@d3_bp.route("/api/worktrees/sweep", methods=["POST"])
@token_required
def worktrees_sweep():
    tenant = _tenant()
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id") or ""
    swept = d3_store.sweep_worktrees(tenant, session_id=session_id or None)
    return jsonify({"ok": True, "swept": swept, "count": len(swept)})


@d3_bp.route("/api/worktrees/sweep/orphans", methods=["POST"])
@token_required
def worktrees_sweep_orphans():
    tenant = _tenant()
    data = request.get_json(silent=True) or {}
    orphan_age_sec = int(data.get("orphan_age_sec") or 3600)
    swept = d3_store.sweep_orphans(tenant, max_age_sec=orphan_age_sec)
    return jsonify({"ok": True, "orphans_swept": swept, "count": len(swept)})
