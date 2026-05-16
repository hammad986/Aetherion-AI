"""
routes/dag_z30.py — Phase Z30 DAG + Runtime Visibility API

Provides:
  GET /api/z30/dag/<sid>            — current synthesized DAG state for a session
  GET /api/z30/dag/<sid>/timeline   — chronological execution timeline
  GET /api/z30/instability/<sid>    — runtime instability metrics
  GET /api/z30/node/<sid>/<node_id> — per-node intelligence detail
"""

import sqlite3
import json
import time
import logging
import os
from flask import Blueprint, request, jsonify

logger = logging.getLogger("nexora.z30")

dag_z30_bp = Blueprint("dag_z30", __name__, url_prefix="/api/z30")

# ── DB helpers ────────────────────────────────────────────────────────────────

def _session_db(sid: str) -> str:
    base = os.path.join("workspace", sid, "sessions.db")
    if os.path.exists(base):
        return base
    fallback = "sessions.db"
    return fallback if os.path.exists(fallback) else ""


def _query_session_logs(sid: str, limit: int = 500):
    db = _session_db(sid)
    if not db:
        return []
    try:
        with sqlite3.connect(db, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM logs WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (sid, limit)
            ).fetchall()
            return [dict(r) for r in reversed(rows)]
    except Exception as e:
        logger.debug(f"[Z30] session log query failed for {sid}: {e}")
        return []


def _query_sessions_meta():
    """List all known sessions with minimal metadata."""
    for db_path in ["sessions.db"]:
        if not os.path.exists(db_path):
            continue
        try:
            with sqlite3.connect(db_path, timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT session_id, status, created_at FROM sessions ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            pass
    return []


# ── DAG synthesis from session logs ──────────────────────────────────────────

PHASE_KEYWORDS = {
    "plan":   ["plan", "think", "analyz", "design", "understand", "decompos", "breaking down"],
    "code":   ["cod", "impl", "generat", "writ", "build", "creat", "Writing file", "Creating file"],
    "debug":  ["debug", "test", "fix", "verif", "retry", "patch", "error", "fallback"],
    "tool":   ["tool:", "calling", "executing tool", "run tool"],
    "done":   ["task finished", "completed successfully", "status=success", "exit=0"],
}

SEVERITY_MAP = {
    "error":      "CRITICAL",
    "failure":    "CRITICAL",
    "retry":      "WARNING",
    "fallback":   "WARNING",
    "warn":       "WARNING",
    "slow":       "DEGRADED",
    "timeout":    "DEGRADED",
    "success":    "INFO",
    "info":       "INFO",
}


def _detect_phase(text: str) -> str:
    t = text.lower()
    for phase, kws in PHASE_KEYWORDS.items():
        if any(k.lower() in t for k in kws):
            return phase
    return "unknown"


def _detect_severity(text: str) -> str:
    t = text.lower()
    for kw, sev in SEVERITY_MAP.items():
        if kw in t:
            return sev
    return "INFO"


def _synthesize_dag(logs: list) -> dict:
    """
    Converts a flat log stream into a structured DAG with nodes and edges.
    Groups logs by detected phase, tracks retries, durations, and node states.
    """
    nodes = {}
    edges = []
    node_counter = 0
    current_phase_id = None
    phase_start_ts = None
    retry_counts = {}
    severity_counts = {"INFO": 0, "WARNING": 0, "DEGRADED": 0, "CRITICAL": 0}

    for row in logs:
        text = row.get("text") or row.get("message") or row.get("content") or ""
        level = (row.get("level") or "log").lower()
        ts = row.get("created_at") or row.get("timestamp") or time.time()

        phase = _detect_phase(text)
        severity = _detect_severity(text)
        severity_counts[severity] = severity_counts.get(severity, 0) + 1

        node_id = f"{phase}"

        if node_id not in nodes:
            node_counter += 1
            nodes[node_id] = {
                "id":          node_id,
                "index":       node_counter,
                "label":       phase.title(),
                "state":       "running" if level not in ("error", "success") else ("error" if level == "error" else "done"),
                "stage":       phase,
                "retries":     0,
                "dur_ms":      0,
                "start_ts":    ts,
                "last_ts":     ts,
                "severity":    severity,
                "lines":       0,
                "is_critical": phase in ("code", "debug"),
                "confidence":  None,
                "provider":    None,
                "model":       None,
                "tokens":      0,
                "verified":    False,
            }
            # Add edge from previous phase node
            if current_phase_id and current_phase_id != node_id:
                edges.append({"from": current_phase_id, "to": node_id})
            current_phase_id = node_id

        n = nodes[node_id]
        n["lines"] += 1
        n["last_ts"] = ts
        n["dur_ms"] = int((float(ts) - float(n["start_ts"])) * 1000) if ts and n["start_ts"] else 0

        # Detect retries
        if "retry" in text.lower() or "[retry" in text.lower():
            n["retries"] = n.get("retries", 0) + 1
            retry_counts[node_id] = retry_counts.get(node_id, 0) + 1

        # Detect provider/model hints
        for provider in ("openai", "groq", "anthropic", "gemini", "mistral", "deepseek", "grok", "together", "fireworks"):
            if provider in text.lower():
                n["provider"] = provider
                break
        for model_kw in ("gpt-4", "gpt-3", "claude", "gemini", "llama", "mistral", "deepseek", "qwen", "mixtral"):
            if model_kw in text.lower():
                n["model"] = model_kw
                break

        # Detect token usage
        import re
        tok_m = re.search(r"(\d+)\s*(?:tokens?|tok)", text, re.IGNORECASE)
        if tok_m:
            n["tokens"] = n.get("tokens", 0) + int(tok_m.group(1))

        # Detect confidence scores
        conf_m = re.search(r"confidence[:\s]+([0-9.]+)", text, re.IGNORECASE)
        if conf_m:
            try:
                n["confidence"] = float(conf_m.group(1))
                if n["confidence"] > 1.0:
                    n["confidence"] /= 100.0
            except ValueError:
                pass

        # Finalize state
        if level == "error" or "error" in text.lower() or "failed" in text.lower():
            n["state"] = "error"
            n["severity"] = "CRITICAL"
        elif phase == "done" or "completed successfully" in text.lower():
            n["state"] = "done"
            n["verified"] = True

    # Final state for non-error nodes still in "running"
    for n in nodes.values():
        if n["state"] == "running" and n["lines"] > 0:
            n["state"] = "done"

    nodes_list = sorted(nodes.values(), key=lambda x: x["index"])
    return {
        "nodes": nodes_list,
        "edges": edges,
        "metrics": {
            "total_nodes": len(nodes_list),
            "severity_counts": severity_counts,
            "retry_counts": retry_counts,
            "total_retries": sum(retry_counts.values()),
        }
    }


def _detect_instability(logs: list) -> dict:
    """Analyzes logs for runtime instability patterns."""
    retry_storm_threshold = 5
    stuck_threshold_s = 120

    retries = 0
    errors = 0
    provider_failures = {}
    context_pressure = 0
    compression_events = 0
    sse_events = 0
    stuck_warning = False
    last_activity_ts = None

    for row in logs:
        text = (row.get("text") or row.get("message") or "").lower()
        ts = row.get("created_at") or row.get("timestamp")
        if ts:
            last_activity_ts = float(ts)

        if "retry" in text:
            retries += 1
        if "error" in text or "failed" in text:
            errors += 1
        if "context" in text and ("pressure" in text or "compress" in text or "limit" in text):
            context_pressure += 1
        if "compress" in text:
            compression_events += 1
        if "sse" in text or "stream" in text:
            sse_events += 1

        for provider in ("openai", "groq", "anthropic", "gemini", "mistral"):
            if provider in text and ("fail" in text or "error" in text or "timeout" in text):
                provider_failures[provider] = provider_failures.get(provider, 0) + 1

    now = time.time()
    if last_activity_ts and (now - last_activity_ts) > stuck_threshold_s:
        stuck_warning = True

    overall = "STABLE"
    if retries >= retry_storm_threshold or errors > 10:
        overall = "CRITICAL"
    elif retries >= 3 or errors > 5 or context_pressure >= 3:
        overall = "DEGRADED"
    elif retries >= 1 or errors > 0:
        overall = "WARNING"

    return {
        "overall": overall,
        "retry_storm": retries >= retry_storm_threshold,
        "retry_count": retries,
        "error_count": errors,
        "provider_failures": provider_failures,
        "context_pressure_events": context_pressure,
        "compression_events": compression_events,
        "sse_events": sse_events,
        "stuck_node": stuck_warning,
        "last_activity_ago_s": int(now - last_activity_ts) if last_activity_ts else None,
        "heatmap": {
            "retries":       min(1.0, retries / 10.0),
            "errors":        min(1.0, errors / 20.0),
            "context":       min(1.0, context_pressure / 5.0),
            "compression":   min(1.0, compression_events / 10.0),
        }
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@dag_z30_bp.route("/dag/<sid>", methods=["GET"])
def get_dag(sid):
    try:
        logs = _query_session_logs(sid, limit=400)
        dag = _synthesize_dag(logs)
        return jsonify({"ok": True, "dag": dag, "session_id": sid})
    except Exception as e:
        logger.error(f"[Z30] DAG synthesis failed for {sid}: {e}")
        return jsonify({"ok": False, "error": str(e), "dag": {"nodes": [], "edges": [], "metrics": {}}}), 200


@dag_z30_bp.route("/dag/<sid>/timeline", methods=["GET"])
def get_timeline(sid):
    try:
        logs = _query_session_logs(sid, limit=200)
        timeline = []
        for row in logs:
            text = row.get("text") or row.get("message") or ""
            ts = row.get("created_at") or row.get("timestamp") or time.time()
            level = row.get("level") or "log"
            timeline.append({
                "ts":       float(ts),
                "text":     text[:200],
                "level":    level,
                "phase":    _detect_phase(text),
                "severity": _detect_severity(text),
            })
        return jsonify({"ok": True, "timeline": timeline, "session_id": sid})
    except Exception as e:
        logger.error(f"[Z30] Timeline failed for {sid}: {e}")
        return jsonify({"ok": False, "error": str(e), "timeline": []}), 200


@dag_z30_bp.route("/instability/<sid>", methods=["GET"])
def get_instability(sid):
    try:
        logs = _query_session_logs(sid, limit=300)
        report = _detect_instability(logs)
        return jsonify({"ok": True, "instability": report, "session_id": sid})
    except Exception as e:
        logger.error(f"[Z30] Instability check failed for {sid}: {e}")
        return jsonify({"ok": False, "error": str(e)}), 200


@dag_z30_bp.route("/node/<sid>/<node_id>", methods=["GET"])
def get_node_detail(sid, node_id):
    try:
        logs = _query_session_logs(sid, limit=400)
        dag = _synthesize_dag(logs)
        node = next((n for n in dag["nodes"] if n["id"] == node_id), None)
        if not node:
            return jsonify({"ok": False, "error": "Node not found"}), 404
        node_logs = [
            {
                "ts":    row.get("created_at") or row.get("timestamp"),
                "text":  (row.get("text") or row.get("message") or "")[:300],
                "level": row.get("level") or "log",
            }
            for row in logs
            if _detect_phase(row.get("text") or row.get("message") or "") == node_id
        ]
        return jsonify({"ok": True, "node": node, "logs": node_logs[-50:], "session_id": sid})
    except Exception as e:
        logger.error(f"[Z30] Node detail failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 200


@dag_z30_bp.route("/sessions", methods=["GET"])
def list_sessions():
    try:
        sessions = _query_sessions_meta()
        return jsonify({"ok": True, "sessions": sessions})
    except Exception as e:
        return jsonify({"ok": False, "sessions": []}), 200
