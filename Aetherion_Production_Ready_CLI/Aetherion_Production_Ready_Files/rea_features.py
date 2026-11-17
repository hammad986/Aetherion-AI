"""
backend/routes/rea_features.py

Flask Blueprint exposing REA (Reverse Engineer Anything - morluto/rea) endpoints
for the Aetherion Control Plane, CLI, and Autonomous Agent Runtime.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from execution.extensions.rea_bridge import (
    rea_inspect_binary,
    rea_observe_js_runtime,
    rea_reconstruct_feature,
    rea_scope_gate,
)

rea_bp = Blueprint("rea_features", __name__, url_prefix="/api/v1/rea")


@rea_bp.route("/scope-gate", methods=["POST"])
def api_rea_scope_gate():
    payload = request.get_json(silent=True) or {}
    result = rea_scope_gate(payload)
    status_code = 200 if result.get("status") == "authorized" else 400
    return jsonify(result), status_code


@rea_bp.route("/inspect", methods=["POST"])
def api_rea_inspect():
    payload = request.get_json(silent=True) or {}
    result = rea_inspect_binary(payload)
    status_code = 200 if result.get("status") == "ok" else 400
    return jsonify(result), status_code


@rea_bp.route("/js-observe", methods=["POST"])
def api_rea_js_observe():
    payload = request.get_json(silent=True) or {}
    result = rea_observe_js_runtime(payload)
    status_code = 200 if result.get("status") == "ok" else 400
    return jsonify(result), status_code


@rea_bp.route("/reconstruct", methods=["POST"])
def api_rea_reconstruct():
    payload = request.get_json(silent=True) or {}
    result = rea_reconstruct_feature(payload)
    status_code = 200 if result.get("status") == "ok" else 400
    return jsonify(result), status_code
