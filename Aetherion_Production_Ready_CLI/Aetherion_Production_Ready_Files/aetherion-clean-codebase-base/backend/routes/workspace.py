"""
routes/workspace.py — Workspace and Session Management API
Supports:
  - GET /api/sessions
  - POST /api/sessions/create
  - GET /api/session/<sid>
  - DELETE /api/session/<sid>
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from flask import Blueprint, jsonify, request, g
from auth_system import token_required
from db.store_factory import get_store

logger = logging.getLogger("aetherion.workspace")
workspace_bp = Blueprint("workspace", __name__)

WORKSPACE_BASE = os.path.join(os.getcwd(), "workspace")
os.makedirs(WORKSPACE_BASE, exist_ok=True)


def _init_sessions_table(store):
    store.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            title TEXT,
            task TEXT,
            status TEXT DEFAULT 'created',
            model TEXT DEFAULT 'default',
            created_at REAL,
            updated_at REAL
        )
    """)
    store.commit()


@workspace_bp.route("/api/workspaces", methods=["GET"])
@token_required
def list_sessions():
    """List recent workspaces for the current user."""
    limit = min(int(request.args.get("limit", 50)), 200)
    user_id = getattr(g, "user_id", None)
    store = get_store("sessions.db")
    _init_sessions_table(store)

    if user_id:
        rows = store.fetchall(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
    else:
        rows = store.fetchall(
            "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    # Format for both sessions and workspaces consumers
    workspaces = []
    for r in rows:
        workspaces.append({
            "id": r.get("id"),
            "workspace_id": r.get("id"),
            "name": r.get("title") or r.get("task") or "Workspace",
            "title": r.get("title") or r.get("task") or "Workspace",
            "status": r.get("status", "ready"),
            "created_at": r.get("created_at"),
        })
    return jsonify({"ok": True, "sessions": rows, "workspaces": workspaces, "data": workspaces})


@workspace_bp.route("/api/workspaces", methods=["POST"])
@token_required
def create_session():
    """Create a new workspace/session."""
    data = request.get_json(silent=True) or {}
    sid = str(data.get("id") or data.get("session_id") or uuid.uuid4())
    title = str(data.get("name") or data.get("title") or data.get("task") or "New Workspace")
    model = str(data.get("model") or "default")
    user_id = getattr(g, "user_id", 1)
    now = time.time()

    store = get_store("sessions.db")
    _init_sessions_table(store)

    store.execute(
        """
        INSERT INTO sessions (id, user_id, title, task, status, model, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'ready', ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            title = EXCLUDED.title,
            task = EXCLUDED.task,
            status = EXCLUDED.status,
            model = EXCLUDED.model,
            updated_at = EXCLUDED.updated_at
        """,
        (sid, user_id, title, title, model, now, now),
    )
    store.commit()

    # Create workspace filesystem directory
    ws_dir = os.path.join(WORKSPACE_BASE, sid)
    os.makedirs(ws_dir, exist_ok=True)

    return jsonify({
        "ok": True,
        "id": sid,
        "session_id": sid,
        "workspace_id": sid,
        "name": title,
        "title": title,
        "status": "ready",
        "created_at": now,
        "data": {
            "workspace_id": sid,
            "name": title,
            "status": "ready",
        }
    }), 201


@workspace_bp.route("/api/session/<sid>", methods=["GET"])
@token_required
def get_session(sid: str):
    """Retrieve details for a single workspace."""
    store = get_store("sessions.db")
    _init_sessions_table(store)
    row = store.fetchone("SELECT * FROM sessions WHERE id = ?", (sid,))
    if not row:
        return jsonify({"ok": False, "error": "Workspace not found"}), 404
    return jsonify({"ok": True, "session": row})


@workspace_bp.route("/api/session/<sid>", methods=["DELETE"])
@token_required
def delete_session(sid: str):
    """Delete a workspace and cleanup state."""
    store = get_store("sessions.db")
    _init_sessions_table(store)
    store.execute("DELETE FROM sessions WHERE id = ?", (sid,))
    store.commit()
    return jsonify({"ok": True, "deleted": sid})
