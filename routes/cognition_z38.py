"""
routes/cognition_z38.py — Phase Z38 Persistent Runtime Cognition + Adaptive Operational Memory

Provides:
  POST /api/z38/memory                 — persist node execution record
  GET  /api/z38/memory/<node_id>       — retrieve node history
  GET  /api/z38/memory                 — list all known nodes (summary)
  POST /api/z38/recovery               — record a recovery outcome
  GET  /api/z38/patterns               — chronic instability / bottleneck patterns
  GET  /api/z38/evolution              — runtime health trend (per-session aggregates)
  POST /api/z38/replay/hydrate         — hydrate replay with persisted node context
  DELETE /api/z38/memory/<node_id>     — prune specific node (admin)
  POST /api/z38/gc                     — run bounded garbage collection
"""

import sqlite3
import json
import time
import logging
import os
from flask import Blueprint, request, jsonify

logger = logging.getLogger("nexora.z38")

cognition_z38_bp = Blueprint("cognition_z38", __name__, url_prefix="/api/z38")

Z38_DB      = os.environ.get("Z38_DB", "z38_cognition.db")
SCHEMA_VER  = 1

# Retention bounds
MAX_RECORDS_PER_NODE  = 50    # per-node execution records
MAX_RECOVERY_PER_NODE = 30    # per-node recovery events
MAX_PRESSURE_POINTS   = 60    # per-node pressure trace entries
MAX_TOTAL_NODES       = 2000  # total node records before GC
MAX_EVOLUTION_ROWS    = 500   # per-session evolution entries

# ── Schema ─────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS z38_schema_version (
    version     INTEGER NOT NULL,
    created_at  REAL    NOT NULL DEFAULT (unixepoch('now','subsec'))
);

CREATE TABLE IF NOT EXISTS z38_node_memory (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         TEXT    NOT NULL,
    session_id      TEXT    NOT NULL DEFAULT '',
    state           TEXT    NOT NULL DEFAULT 'pending',
    heat            REAL    NOT NULL DEFAULT 0,
    retries         INTEGER NOT NULL DEFAULT 0,
    errors          INTEGER NOT NULL DEFAULT 0,
    dur_ms          REAL,
    parent_id       TEXT,
    branch_type     TEXT    NOT NULL DEFAULT 'main',
    confidence      REAL,
    provider        TEXT,
    decision_chain  TEXT    NOT NULL DEFAULT '[]',
    failure_reasons TEXT    NOT NULL DEFAULT '[]',
    pressure_trace  TEXT    NOT NULL DEFAULT '[]',
    created_at      REAL    NOT NULL DEFAULT (unixepoch('now','subsec')),
    updated_at      REAL    NOT NULL DEFAULT (unixepoch('now','subsec'))
);

CREATE TABLE IF NOT EXISTS z38_recovery_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         TEXT    NOT NULL,
    session_id      TEXT    NOT NULL DEFAULT '',
    recovery_type   TEXT    NOT NULL,
    success         INTEGER NOT NULL DEFAULT 1,
    confidence_before REAL,
    confidence_after  REAL,
    notes           TEXT,
    created_at      REAL    NOT NULL DEFAULT (unixepoch('now','subsec'))
);

CREATE TABLE IF NOT EXISTS z38_evolution (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL,
    ts              REAL    NOT NULL DEFAULT (unixepoch('now','subsec')),
    avg_heat        REAL    NOT NULL DEFAULT 0,
    total_retries   INTEGER NOT NULL DEFAULT 0,
    total_errors    INTEGER NOT NULL DEFAULT 0,
    total_recoveries INTEGER NOT NULL DEFAULT 0,
    risk_level      TEXT    NOT NULL DEFAULT 'LOW',
    node_count      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_z38_node_memory_node_id  ON z38_node_memory(node_id);
CREATE INDEX IF NOT EXISTS idx_z38_node_memory_session  ON z38_node_memory(session_id);
CREATE INDEX IF NOT EXISTS idx_z38_recovery_node_id     ON z38_recovery_events(node_id);
CREATE INDEX IF NOT EXISTS idx_z38_evolution_session    ON z38_evolution(session_id, ts);
"""

# ── DB helpers ─────────────────────────────────────────────────────────────

def _get_conn():
    conn = sqlite3.connect(Z38_DB, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _bootstrap():
    try:
        conn = _get_conn()
        with conn:
            conn.executescript(_SCHEMA_SQL)
            exists = conn.execute("SELECT count(*) FROM z38_schema_version").fetchone()[0]
            if not exists:
                conn.execute("INSERT INTO z38_schema_version (version) VALUES (?)", (SCHEMA_VER,))
        conn.close()
        logger.info("[Z38] Schema bootstrapped (v%d)", SCHEMA_VER)
    except Exception as e:
        logger.error("[Z38] Bootstrap error: %s", e)

_bootstrap()

# ── WAL checkpoint helper ──────────────────────────────────────────────────

def _safe_write(conn, sql, params=()):
    """Execute a write with WAL integrity check."""
    try:
        conn.execute(sql, params)
        return True
    except sqlite3.OperationalError as e:
        logger.warning("[Z38] Write error (WAL): %s", e)
        return False

# ── Bounded GC ─────────────────────────────────────────────────────────────

def _gc_node(conn, node_id):
    """Prune oldest rows if a node exceeds retention bounds."""
    count = conn.execute(
        "SELECT count(*) FROM z38_node_memory WHERE node_id=?", (node_id,)
    ).fetchone()[0]
    if count > MAX_RECORDS_PER_NODE:
        prune = count - MAX_RECORDS_PER_NODE
        conn.execute("""
            DELETE FROM z38_node_memory WHERE id IN (
                SELECT id FROM z38_node_memory WHERE node_id=? ORDER BY created_at ASC LIMIT ?
            )
        """, (node_id, prune))

    r_count = conn.execute(
        "SELECT count(*) FROM z38_recovery_events WHERE node_id=?", (node_id,)
    ).fetchone()[0]
    if r_count > MAX_RECOVERY_PER_NODE:
        prune = r_count - MAX_RECOVERY_PER_NODE
        conn.execute("""
            DELETE FROM z38_recovery_events WHERE id IN (
                SELECT id FROM z38_recovery_events WHERE node_id=? ORDER BY created_at ASC LIMIT ?
            )
        """, (node_id, prune))

def _gc_global(conn):
    """Prune total node count if over MAX_TOTAL_NODES."""
    total = conn.execute("SELECT count(DISTINCT node_id) FROM z38_node_memory").fetchone()[0]
    if total > MAX_TOTAL_NODES:
        prune = total - MAX_TOTAL_NODES
        conn.execute("""
            DELETE FROM z38_node_memory WHERE node_id IN (
                SELECT node_id FROM z38_node_memory
                GROUP BY node_id ORDER BY max(updated_at) ASC LIMIT ?
            )
        """, (prune,))

    ev_total = conn.execute("SELECT count(*) FROM z38_evolution").fetchone()[0]
    if ev_total > MAX_EVOLUTION_ROWS:
        prune = ev_total - MAX_EVOLUTION_ROWS
        conn.execute("""
            DELETE FROM z38_evolution WHERE id IN (
                SELECT id FROM z38_evolution ORDER BY ts ASC LIMIT ?
            )
        """, (prune,))

# ── Node record builder ────────────────────────────────────────────────────

def _build_node_summary(rows, recovery_rows):
    """Build a summary dict from raw DB rows for a node."""
    if not rows:
        return None

    total_retries  = sum(r["retries"] for r in rows)
    total_errors   = sum(r["errors"]  for r in rows)
    occurrences    = len(rows)
    unstable_count = sum(1 for r in rows if r["errors"] > 0 or r["retries"] > 0)
    avg_heat       = sum(r["heat"] for r in rows) / occurrences if occurrences else 0
    dur_vals       = [r["dur_ms"] for r in rows if r["dur_ms"] is not None]
    avg_dur        = sum(dur_vals) / len(dur_vals) if dur_vals else None

    # Recovery stats by type
    recovery_by_type = {}
    for rv in recovery_rows:
        rt = rv["recovery_type"]
        if rt not in recovery_by_type:
            recovery_by_type[rt] = {"count": 0, "successes": 0}
        recovery_by_type[rt]["count"] += 1
        if rv["success"]:
            recovery_by_type[rt]["successes"] += 1

    best_recovery = None
    best_rate     = 0
    for rt, stats in recovery_by_type.items():
        rate = stats["successes"] / stats["count"] if stats["count"] else 0
        if rate > best_rate:
            best_rate     = rate
            best_recovery = {"type": rt, "rate": round(rate, 3), **stats}

    # Most recent record for current state
    latest   = rows[-1]
    node_id  = latest["node_id"]

    # Insight generation
    insight_parts = []
    if unstable_count >= 3:
        insight_parts.append(f"historically unstable ({unstable_count} occurrences)")
    if avg_dur is not None and avg_dur > 30000:
        insight_parts.append(f"execution-heavy (~{round(avg_dur/1000)}s avg)")
    if best_recovery and best_recovery["rate"] >= 0.5:
        insight_parts.append(f"best recovery: '{best_recovery['type']}' ({round(best_recovery['rate']*100)}% success)")
    if total_retries >= 5:
        insight_parts.append(f"retry-heavy ({total_retries} total retries)")

    return {
        "node_id":        node_id,
        "occurrences":    occurrences,
        "unstable_count": unstable_count,
        "total_retries":  total_retries,
        "total_errors":   total_errors,
        "avg_heat":       round(avg_heat, 3),
        "avg_dur_ms":     round(avg_dur, 1) if avg_dur is not None else None,
        "recovery_by_type": recovery_by_type,
        "best_recovery":  best_recovery,
        "latest_state":   latest["state"],
        "latest_heat":    latest["heat"],
        "parent_id":      latest["parent_id"],
        "branch_type":    latest["branch_type"],
        "insight":        " · ".join(insight_parts) if insight_parts else None,
        "decision_chain": _safe_json(latest["decision_chain"]),
        "failure_reasons": _safe_json(latest["failure_reasons"]),
        "pressure_trace": _safe_json(latest["pressure_trace"]),
        "updated_at":     latest["updated_at"],
    }

def _safe_json(val, fallback=None):
    if val is None:
        return fallback if fallback is not None else []
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return fallback if fallback is not None else []

# ══════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════

@cognition_z38_bp.route("/memory", methods=["POST"])
def persist_memory():
    """Upsert a node execution record."""
    try:
        data = request.get_json(force=True, silent=True) or {}

        node_id    = str(data.get("node_id", "")).strip()[:128]
        if not node_id:
            return jsonify({"error": "node_id required"}), 400

        session_id  = str(data.get("session_id", ""))[:128]
        state       = str(data.get("state", "pending"))[:32]
        heat        = float(data.get("heat", 0))
        retries     = int(data.get("retries", 0))
        errors      = int(data.get("errors", 0))
        dur_ms      = data.get("dur_ms")
        parent_id   = data.get("parent_id")
        branch_type = str(data.get("branch_type", "main"))[:32]
        confidence  = data.get("confidence")
        provider    = data.get("provider")

        # Bounded JSON fields
        decision_chain  = json.dumps(_safe_json(data.get("decision_chain", []))[:20])
        failure_reasons = json.dumps(_safe_json(data.get("failure_reasons", []))[:8])

        # Pressure trace — bounded to MAX_PRESSURE_POINTS
        raw_trace  = _safe_json(data.get("pressure_trace", []))
        if len(raw_trace) > MAX_PRESSURE_POINTS:
            raw_trace = raw_trace[-MAX_PRESSURE_POINTS:]
        pressure_trace = json.dumps(raw_trace)

        now = time.time()

        conn = _get_conn()
        with conn:
            _safe_write(conn, """
                INSERT INTO z38_node_memory (
                    node_id, session_id, state, heat, retries, errors, dur_ms,
                    parent_id, branch_type, confidence, provider,
                    decision_chain, failure_reasons, pressure_trace, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                node_id, session_id, state, heat, retries, errors,
                dur_ms, parent_id, branch_type, confidence, provider,
                decision_chain, failure_reasons, pressure_trace, now,
            ))
            _gc_node(conn, node_id)

        conn.close()
        return jsonify({"ok": True, "node_id": node_id}), 201

    except Exception as e:
        logger.exception("[Z38] persist_memory error")
        return jsonify({"error": str(e)}), 500


@cognition_z38_bp.route("/memory/<node_id>", methods=["GET"])
def get_memory(node_id):
    """Retrieve full historical record for a node."""
    try:
        node_id = str(node_id).strip()[:128]
        limit   = min(int(request.args.get("limit", 50)), MAX_RECORDS_PER_NODE)

        conn  = _get_conn()
        rows  = conn.execute("""
            SELECT * FROM z38_node_memory WHERE node_id=?
            ORDER BY created_at DESC LIMIT ?
        """, (node_id, limit)).fetchall()
        recovery_rows = conn.execute("""
            SELECT * FROM z38_recovery_events WHERE node_id=?
            ORDER BY created_at DESC LIMIT ?
        """, (node_id, MAX_RECOVERY_PER_NODE)).fetchall()
        conn.close()

        if not rows:
            return jsonify({"node_id": node_id, "found": False}), 404

        rows = list(reversed(rows))  # chronological
        summary = _build_node_summary(rows, recovery_rows)
        return jsonify({"found": True, **summary})

    except Exception as e:
        logger.exception("[Z38] get_memory error")
        return jsonify({"error": str(e)}), 500


@cognition_z38_bp.route("/memory", methods=["GET"])
def list_memory():
    """List all known nodes with their summary (most recently updated first)."""
    try:
        limit   = min(int(request.args.get("limit", 100)), 500)
        session = request.args.get("session_id")

        conn = _get_conn()
        if session:
            rows = conn.execute("""
                SELECT node_id, max(updated_at) as last_seen,
                       sum(retries) as total_retries,
                       sum(errors)  as total_errors,
                       avg(heat)    as avg_heat,
                       max(state)   as latest_state,
                       max(branch_type) as branch_type
                FROM z38_node_memory
                WHERE session_id=?
                GROUP BY node_id ORDER BY last_seen DESC LIMIT ?
            """, (session, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT node_id, max(updated_at) as last_seen,
                       sum(retries) as total_retries,
                       sum(errors)  as total_errors,
                       avg(heat)    as avg_heat,
                       max(state)   as latest_state,
                       max(branch_type) as branch_type
                FROM z38_node_memory
                GROUP BY node_id ORDER BY last_seen DESC LIMIT ?
            """, (limit,)).fetchall()
        conn.close()

        return jsonify({
            "nodes": [dict(r) for r in rows],
            "count": len(rows),
        })

    except Exception as e:
        logger.exception("[Z38] list_memory error")
        return jsonify({"error": str(e)}), 500


@cognition_z38_bp.route("/recovery", methods=["POST"])
def record_recovery():
    """Record a recovery outcome."""
    try:
        data = request.get_json(force=True, silent=True) or {}

        node_id       = str(data.get("node_id", "")).strip()[:128]
        if not node_id:
            return jsonify({"error": "node_id required"}), 400

        session_id    = str(data.get("session_id", ""))[:128]
        recovery_type = str(data.get("recovery_type", "unknown"))[:64]
        success       = bool(data.get("success", True))
        conf_before   = data.get("confidence_before")
        conf_after    = data.get("confidence_after")
        notes         = str(data.get("notes", ""))[:256]

        conn = _get_conn()
        with conn:
            _safe_write(conn, """
                INSERT INTO z38_recovery_events
                    (node_id, session_id, recovery_type, success,
                     confidence_before, confidence_after, notes)
                VALUES (?,?,?,?,?,?,?)
            """, (node_id, session_id, recovery_type, int(success),
                  conf_before, conf_after, notes))
            _gc_node(conn, node_id)
        conn.close()
        return jsonify({"ok": True}), 201

    except Exception as e:
        logger.exception("[Z38] record_recovery error")
        return jsonify({"error": str(e)}), 500


@cognition_z38_bp.route("/patterns", methods=["GET"])
def get_patterns():
    """Return chronic instability patterns, bottlenecks, escalation chains."""
    try:
        limit = min(int(request.args.get("limit", 20)), 100)

        conn = _get_conn()

        # Chronically unstable nodes
        unstable = conn.execute("""
            SELECT node_id,
                   count(*) as occurrences,
                   sum(errors)  as total_errors,
                   sum(retries) as total_retries,
                   avg(heat)    as avg_heat
            FROM z38_node_memory
            WHERE errors > 0 OR retries > 0
            GROUP BY node_id
            ORDER BY (sum(errors) + sum(retries)) DESC
            LIMIT ?
        """, (limit,)).fetchall()

        # Bottlenecks: highest avg dur_ms
        bottlenecks = conn.execute("""
            SELECT node_id,
                   count(*)   as occurrences,
                   avg(dur_ms) as avg_dur_ms,
                   sum(retries) as total_retries
            FROM z38_node_memory
            WHERE dur_ms IS NOT NULL
            GROUP BY node_id
            ORDER BY avg(dur_ms) DESC
            LIMIT ?
        """, (limit,)).fetchall()

        # Best recovery types overall
        recovery_stats = conn.execute("""
            SELECT recovery_type,
                   count(*) as total,
                   sum(success) as successes,
                   round(cast(sum(success) as real)/count(*), 3) as success_rate
            FROM z38_recovery_events
            GROUP BY recovery_type
            ORDER BY success_rate DESC
        """).fetchall()

        # Persistent bottleneck branches
        retry_heavy = conn.execute("""
            SELECT branch_type, sum(retries) as total_retries, count(*) as nodes
            FROM z38_node_memory
            GROUP BY branch_type
            ORDER BY total_retries DESC
        """).fetchall()

        conn.close()

        return jsonify({
            "unstable_nodes":   [dict(r) for r in unstable],
            "bottlenecks":      [dict(r) for r in bottlenecks],
            "recovery_stats":   [dict(r) for r in recovery_stats],
            "retry_by_branch":  [dict(r) for r in retry_heavy],
        })

    except Exception as e:
        logger.exception("[Z38] get_patterns error")
        return jsonify({"error": str(e)}), 500


@cognition_z38_bp.route("/evolution", methods=["GET"])
def get_evolution():
    """Runtime health trend per-session and global pressure averages."""
    try:
        session_id = request.args.get("session_id")
        limit      = min(int(request.args.get("limit", 50)), MAX_EVOLUTION_ROWS)

        conn = _get_conn()

        if session_id:
            rows = conn.execute("""
                SELECT * FROM z38_evolution WHERE session_id=?
                ORDER BY ts ASC LIMIT ?
            """, (session_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM z38_evolution ORDER BY ts ASC LIMIT ?
            """, (limit,)).fetchall()

        # Global aggregates
        totals = conn.execute("""
            SELECT
                round(avg(avg_heat), 3)     as global_avg_heat,
                sum(total_retries)          as global_retries,
                sum(total_errors)           as global_errors,
                sum(total_recoveries)       as global_recoveries,
                count(DISTINCT session_id)  as session_count
            FROM z38_evolution
        """).fetchone()

        conn.close()

        return jsonify({
            "evolution":  [dict(r) for r in rows],
            "totals":     dict(totals) if totals else {},
            "count":      len(rows),
        })

    except Exception as e:
        logger.exception("[Z38] get_evolution error")
        return jsonify({"error": str(e)}), 500


@cognition_z38_bp.route("/evolution", methods=["POST"])
def record_evolution():
    """Record a session evolution snapshot (called from frontend on each Z37 forecast cycle)."""
    try:
        data = request.get_json(force=True, silent=True) or {}

        session_id      = str(data.get("session_id", ""))[:128]
        avg_heat        = float(data.get("avg_heat", 0))
        total_retries   = int(data.get("total_retries", 0))
        total_errors    = int(data.get("total_errors", 0))
        total_recoveries = int(data.get("total_recoveries", 0))
        risk_level      = str(data.get("risk_level", "LOW"))[:16]
        node_count      = int(data.get("node_count", 0))

        conn = _get_conn()
        with conn:
            _safe_write(conn, """
                INSERT INTO z38_evolution
                    (session_id, avg_heat, total_retries, total_errors,
                     total_recoveries, risk_level, node_count)
                VALUES (?,?,?,?,?,?,?)
            """, (session_id, avg_heat, total_retries, total_errors,
                  total_recoveries, risk_level, node_count))
            _gc_global(conn)
        conn.close()
        return jsonify({"ok": True}), 201

    except Exception as e:
        logger.exception("[Z38] record_evolution error")
        return jsonify({"error": str(e)}), 500


@cognition_z38_bp.route("/replay/hydrate", methods=["POST"])
def replay_hydrate():
    """
    Hydrate a replay context with persisted node history.
    Accepts a list of node_ids, returns their full persisted records.
    """
    try:
        data     = request.get_json(force=True, silent=True) or {}
        node_ids = [str(n)[:128] for n in (data.get("node_ids") or [])[:50]]

        if not node_ids:
            return jsonify({"nodes": {}}), 200

        conn = _get_conn()
        results = {}
        for nid in node_ids:
            rows = conn.execute("""
                SELECT * FROM z38_node_memory WHERE node_id=?
                ORDER BY created_at ASC LIMIT ?
            """, (nid, MAX_RECORDS_PER_NODE)).fetchall()
            recovery_rows = conn.execute("""
                SELECT * FROM z38_recovery_events WHERE node_id=?
                ORDER BY created_at ASC LIMIT ?
            """, (nid, MAX_RECOVERY_PER_NODE)).fetchall()
            summary = _build_node_summary(list(rows), list(recovery_rows))
            if summary:
                results[nid] = summary
        conn.close()

        return jsonify({"nodes": results, "hydrated": len(results)})

    except Exception as e:
        logger.exception("[Z38] replay_hydrate error")
        return jsonify({"error": str(e)}), 500


@cognition_z38_bp.route("/memory/<node_id>", methods=["DELETE"])
def delete_memory(node_id):
    """Hard-delete all records for a node (admin / prune)."""
    try:
        node_id = str(node_id).strip()[:128]
        conn    = _get_conn()
        with conn:
            conn.execute("DELETE FROM z38_node_memory WHERE node_id=?", (node_id,))
            conn.execute("DELETE FROM z38_recovery_events WHERE node_id=?", (node_id,))
        conn.close()
        return jsonify({"ok": True, "pruned": node_id})
    except Exception as e:
        logger.exception("[Z38] delete_memory error")
        return jsonify({"error": str(e)}), 500


@cognition_z38_bp.route("/gc", methods=["POST"])
def run_gc():
    """Trigger global garbage collection."""
    try:
        conn = _get_conn()
        with conn:
            _gc_global(conn)
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        conn.close()
        return jsonify({"ok": True, "action": "gc_complete"})
    except Exception as e:
        logger.exception("[Z38] gc error")
        return jsonify({"error": str(e)}), 500
