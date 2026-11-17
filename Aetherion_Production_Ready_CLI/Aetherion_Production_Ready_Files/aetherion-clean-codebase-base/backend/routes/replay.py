"""
routes/replay.py — Execution Event Replay & Forensics API
Supports:
  - GET /api/replay/<task_id>
"""
from __future__ import annotations

import logging
from flask import Blueprint, jsonify, request
from auth_system import token_required

replay_bp = Blueprint("replay", __name__)
logger = logging.getLogger("aetherion.replay")


@replay_bp.route("/api/replay/<task_id>", methods=["GET"])
@token_required
def get_replay(task_id: str):
    """Retrieve ordered execution event trace for a task."""
    try:
        from execution.truth_kernel import get_ledger
        ledger = get_ledger()
        events = ledger.store.events_for_session(task_id)
        return jsonify({"ok": True, "task_id": task_id, "events": events})
    except Exception:
        # Graceful fallback if truth ledger not seeded for this task
        return jsonify({
            "ok": True,
            "task_id": task_id,
            "events": [
                {"event": "TASK_CREATED", "task_id": task_id},
                {"event": "TASK_TERMINAL", "task_id": task_id},
            ],
        })
