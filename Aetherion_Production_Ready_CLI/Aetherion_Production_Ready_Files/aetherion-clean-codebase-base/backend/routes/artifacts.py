"""
routes/artifacts.py — Task Artifact Registry & Integrity Verification API
Supports:
  - GET /api/artifacts/<task_id> (List task artifacts)
  - GET /api/artifacts/<task_id>/<path:filepath> (Download / stream artifact)
"""
from __future__ import annotations

import hashlib
import os
from flask import Blueprint, jsonify, request, send_file, abort
from auth_system import token_required

artifacts_bp = Blueprint("artifacts", __name__)
WORKSPACE_BASE = os.path.join(os.getcwd(), "workspace")


def _safe_resolve_path(task_id: str, relative_path: str = "") -> str:
    base = os.path.realpath(os.path.join(WORKSPACE_BASE, task_id))
    target = os.path.realpath(os.path.join(base, relative_path))
    if os.path.commonpath([base, target]) != base:
        raise ValueError("Directory traversal attempt detected")
    return target


@artifacts_bp.route("/api/artifacts", methods=["GET"])
@artifacts_bp.route("/api/artifacts/<task_id>", methods=["GET"])
@token_required
def list_artifacts(task_id: str = ""):
    """List all artifacts generated during task execution."""
    target_id = task_id or request.args.get("session_id") or request.args.get("task_id") or ""
    if not target_id:
        return jsonify({"ok": True, "artifacts": []})
    try:
        task_dir = _safe_resolve_path(target_id)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid task ID"}), 400

    if not os.path.isdir(task_dir):
        return jsonify({"ok": True, "task_id": target_id, "artifacts": []})

    artifacts = []
    for root, _, files in os.walk(task_dir):
        for f in files:
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, task_dir).replace("\\", "/")
            stat = os.stat(full_path)
            
            # Compute SHA-256 integrity hash
            sha256 = hashlib.sha256()
            with open(full_path, "rb") as fh:
                while chunk := fh.read(65536):
                    sha256.update(chunk)

            artifacts.append({
                "path": rel_path,
                "size_bytes": stat.st_size,
                "sha256": sha256.hexdigest(),
                "modified_at": stat.st_mtime,
            })

    return jsonify({"ok": True, "task_id": task_id, "artifacts": artifacts})


@artifacts_bp.route("/api/artifacts/<task_id>/<path:filepath>", methods=["GET"])
@token_required
def get_artifact(task_id: str, filepath: str):
    """Download a specific task artifact with SHA256 integrity header."""
    try:
        full_path = _safe_resolve_path(task_id, filepath)
    except ValueError:
        return jsonify({"ok": False, "error": "Path traversal detected"}), 403

    if not os.path.isfile(full_path):
        return jsonify({"ok": False, "error": "Artifact not found"}), 404

    # Calculate checksum
    sha256 = hashlib.sha256()
    with open(full_path, "rb") as fh:
        while chunk := fh.read(65536):
            sha256.update(chunk)

    response = send_file(full_path, as_attachment=True)
    response.headers["ETag"] = f'"{sha256.hexdigest()}"'
    response.headers["X-Content-SHA256"] = sha256.hexdigest()
    return response
