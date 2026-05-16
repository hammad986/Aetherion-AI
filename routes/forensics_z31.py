"""
routes/forensics_z31.py — Phase Z31 Persistent Runtime Forensics + Execution Memory

Provides:
  POST /api/z31/snapshot/<sid>           — persist a DAG snapshot
  GET  /api/z31/snapshots/<sid>          — list all snapshots for a session
  GET  /api/z31/snapshot/<sid>/latest    — latest persisted snapshot
  GET  /api/z31/snapshot/<sid>/<idx>     — specific snapshot by index
  GET  /api/z31/fingerprint/<sid>        — execution fingerprint for session
  GET  /api/z31/integrity/<sid>          — replay integrity report + drift detection
  GET  /api/z31/sessions                 — historical session browser with filters
  GET  /api/z31/export/<sid>             — generate portable forensic bundle
  POST /api/z31/import                   — import forensic bundle into isolated replay
  DELETE /api/z31/snapshots/<sid>        — purge session snapshots
"""

import sqlite3
import json
import time
import hashlib
import logging
import os
import gzip
import base64
from flask import Blueprint, request, jsonify

logger = logging.getLogger("nexora.z31")

forensics_z31_bp = Blueprint("forensics_z31", __name__, url_prefix="/api/z31")

FORENSICS_DB  = os.environ.get("FORENSICS_DB", "forensics.db")
MAX_SNAPSHOTS_PER_SESSION = 500
MAX_BUNDLE_NODES          = 1000
REPLAY_VERSION            = 1

# ── Schema bootstrap ─────────────────────────────────────────────────────────

_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS dag_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT    NOT NULL,
    snapshot_index INTEGER NOT NULL,
    snapshot_hash  TEXT    NOT NULL,
    nodes_json     TEXT    NOT NULL,
    edges_json     TEXT    NOT NULL DEFAULT '[]',
    metrics_json   TEXT,
    fingerprint    TEXT,
    created_at     REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec')),
    UNIQUE (session_id, snapshot_index)
);

CREATE TABLE IF NOT EXISTS replay_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    event_type  TEXT    NOT NULL,
    node_id     TEXT,
    payload_json TEXT,
    fingerprint TEXT,
    ts          REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

CREATE TABLE IF NOT EXISTS forensic_exports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    export_hash  TEXT NOT NULL,
    bundle_json  TEXT NOT NULL,
    created_at   REAL NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

CREATE INDEX IF NOT EXISTS idx_dag_snapshots_sid  ON dag_snapshots(session_id, snapshot_index);
CREATE INDEX IF NOT EXISTS idx_replay_events_sid   ON replay_events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_forensic_exports_sid ON forensic_exports(session_id);
"""

def _ensure_schema():
    """Bootstrap forensics.db schema once."""
    try:
        with sqlite3.connect(FORENSICS_DB, timeout=10) as conn:
            conn.executescript(_SCHEMA_SQL)
    except Exception as e:
        logger.error(f"[Z31] Schema bootstrap failed: {e}")

_ensure_schema()


# ── DB helpers ────────────────────────────────────────────────────────────────

def _fdb(timeout: float = 10) -> sqlite3.Connection:
    conn = sqlite3.connect(FORENSICS_DB, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ── Fingerprinting ────────────────────────────────────────────────────────────

def _fingerprint_nodes(nodes: list) -> str:
    """Deterministic SHA-256 fingerprint for a node list."""
    canonical = sorted(
        [{"id": n.get("id"), "state": n.get("state"), "retries": n.get("retries", 0)}
         for n in nodes],
        key=lambda x: x["id"]
    )
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:24]


def _fingerprint_event(event_type: str, node_id: str, payload: dict) -> str:
    """Deterministic fingerprint for a single replay event."""
    content = json.dumps({"t": event_type, "n": node_id, "p": payload}, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _session_fingerprint(session_id: str) -> str:
    """
    Folds all replay events for a session into a single deterministic fingerprint.
    Used for cross-device replay integrity validation.
    """
    try:
        with _fdb() as conn:
            rows = conn.execute(
                "SELECT fingerprint FROM replay_events WHERE session_id=? ORDER BY ts ASC",
                (session_id,)
            ).fetchall()
            combined = "|".join(r["fingerprint"] or "" for r in rows)
            return hashlib.sha256(combined.encode()).hexdigest()[:32]
    except Exception:
        return ""


# ── Drift detection ───────────────────────────────────────────────────────────

def _detect_drift(session_id: str, submitted_fingerprint: str) -> dict:
    """
    Compares a submitted replay fingerprint against the server-computed fingerprint.
    Returns drift report.
    """
    server_fp = _session_fingerprint(session_id)
    match      = server_fp == submitted_fingerprint

    try:
        with _fdb() as conn:
            n_events = conn.execute(
                "SELECT COUNT(*) as c FROM replay_events WHERE session_id=?",
                (session_id,)
            ).fetchone()["c"]
    except Exception:
        n_events = 0

    return {
        "match":              match,
        "server_fingerprint": server_fp,
        "submitted":          submitted_fingerprint,
        "drift_detected":     not match,
        "event_count":        n_events,
        "risk":               "NONE" if match else ("HIGH" if n_events > 0 else "UNKNOWN"),
    }


# ── Snapshot helpers ──────────────────────────────────────────────────────────

def _next_snapshot_index(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(snapshot_index), -1) + 1 AS nxt FROM dag_snapshots WHERE session_id=?",
        (session_id,)
    ).fetchone()
    return int(row["nxt"]) if row else 0


def _trim_snapshots(conn: sqlite3.Connection, session_id: str):
    """Keep only the last MAX_SNAPSHOTS_PER_SESSION snapshots to control DB growth."""
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM dag_snapshots WHERE session_id=?",
        (session_id,)
    ).fetchone()["c"]
    if count > MAX_SNAPSHOTS_PER_SESSION:
        cutoff = conn.execute(
            "SELECT id FROM dag_snapshots WHERE session_id=? ORDER BY snapshot_index ASC LIMIT ?",
            (session_id, count - MAX_SNAPSHOTS_PER_SESSION)
        ).fetchall()
        ids = [r["id"] for r in cutoff]
        conn.execute(
            f"DELETE FROM dag_snapshots WHERE id IN ({','.join('?'*len(ids))})",
            ids
        )


# ── Integrity scoring ─────────────────────────────────────────────────────────

def _integrity_score(session_id: str) -> dict:
    """
    Computes an integrity score (0–100) based on:
    - Snapshot count continuity
    - Fingerprint consistency
    - Out-of-order event detection
    - Duplicate event detection
    """
    score = 100
    issues = []

    try:
        with _fdb() as conn:
            snapshots = conn.execute(
                "SELECT snapshot_index, snapshot_hash, fingerprint, created_at "
                "FROM dag_snapshots WHERE session_id=? ORDER BY snapshot_index ASC",
                (session_id,)
            ).fetchall()

            events = conn.execute(
                "SELECT fingerprint, ts FROM replay_events WHERE session_id=? ORDER BY ts ASC",
                (session_id,)
            ).fetchall()
    except Exception as e:
        return {"score": 0, "issues": [f"DB error: {e}"], "verdict": "UNKNOWN"}

    # Gap detection: check for non-contiguous snapshot indices
    indices = [r["snapshot_index"] for r in snapshots]
    for i in range(len(indices) - 1):
        if indices[i + 1] != indices[i] + 1:
            score -= 10
            issues.append(f"Gap in snapshot sequence: {indices[i]} → {indices[i+1]}")

    # Duplicate event fingerprint check
    fps = [r["fingerprint"] for r in events if r["fingerprint"]]
    if len(fps) != len(set(fps)):
        dups = len(fps) - len(set(fps))
        score -= min(20, dups * 3)
        issues.append(f"{dups} duplicate replay event fingerprint(s)")

    # Out-of-order timestamp check
    tss = [r["ts"] for r in events]
    for i in range(len(tss) - 1):
        if tss[i] and tss[i + 1] and tss[i] > tss[i + 1]:
            score -= 5
            issues.append(f"Out-of-order event timestamps at index {i}")

    # Empty snapshot check
    if not snapshots:
        score = 0
        issues.append("No snapshots found for session")

    score = max(0, min(100, score))
    verdict = "HEALTHY" if score >= 90 else ("DEGRADED" if score >= 60 else ("CORRUPT" if score < 30 else "WARNING"))

    return {
        "score":           score,
        "verdict":         verdict,
        "snapshot_count":  len(snapshots),
        "event_count":     len(events),
        "issues":          issues,
        "session_fingerprint": _session_fingerprint(session_id),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@forensics_z31_bp.route("/snapshot/<sid>", methods=["POST"])
def persist_snapshot(sid):
    """Persist a DAG snapshot. Body: {nodes, edges, metrics, fingerprint}"""
    try:
        body = request.get_json(force=True, silent=True) or {}
        nodes    = body.get("nodes", [])
        edges    = body.get("edges", [])
        metrics  = body.get("metrics") or {}
        fp       = body.get("fingerprint") or _fingerprint_nodes(nodes)

        if len(nodes) > MAX_BUNDLE_NODES:
            nodes = nodes[-MAX_BUNDLE_NODES:]

        snap_hash = hashlib.sha256(
            json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True).encode()
        ).hexdigest()[:32]

        with _fdb() as conn:
            idx = _next_snapshot_index(conn, sid)
            conn.execute(
                "INSERT OR REPLACE INTO dag_snapshots "
                "(session_id, snapshot_index, snapshot_hash, nodes_json, edges_json, metrics_json, fingerprint) "
                "VALUES (?,?,?,?,?,?,?)",
                (sid, idx,
                 snap_hash,
                 json.dumps(nodes),
                 json.dumps(edges),
                 json.dumps(metrics),
                 fp)
            )
            _trim_snapshots(conn, sid)

            # Log replay event
            conn.execute(
                "INSERT INTO replay_events (session_id, event_type, node_id, payload_json, fingerprint) "
                "VALUES (?,?,?,?,?)",
                (sid, "snapshot", None,
                 json.dumps({"index": idx, "node_count": len(nodes)}),
                 fp)
            )
            conn.commit()

        return jsonify({"ok": True, "index": idx, "hash": snap_hash, "fingerprint": fp})
    except Exception as e:
        logger.error(f"[Z31] persist_snapshot failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 200


@forensics_z31_bp.route("/snapshots/<sid>", methods=["GET"])
def list_snapshots(sid):
    """List all snapshots for a session (metadata only, no full node JSON)."""
    try:
        limit  = min(int(request.args.get("limit", 100)), 500)
        offset = int(request.args.get("offset", 0))
        with _fdb() as conn:
            rows = conn.execute(
                "SELECT snapshot_index, snapshot_hash, fingerprint, created_at, "
                "json_array_length(nodes_json) AS node_count "
                "FROM dag_snapshots WHERE session_id=? "
                "ORDER BY snapshot_index DESC LIMIT ? OFFSET ?",
                (sid, limit, offset)
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM dag_snapshots WHERE session_id=?", (sid,)
            ).fetchone()["c"]
        return jsonify({
            "ok": True,
            "snapshots": [dict(r) for r in rows],
            "total": total,
            "session_id": sid,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "snapshots": []}), 200


@forensics_z31_bp.route("/snapshot/<sid>/latest", methods=["GET"])
def get_latest_snapshot(sid):
    """Get the latest persisted snapshot for a session (full node data)."""
    try:
        with _fdb() as conn:
            row = conn.execute(
                "SELECT * FROM dag_snapshots WHERE session_id=? ORDER BY snapshot_index DESC LIMIT 1",
                (sid,)
            ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "No snapshots found", "snapshot": None}), 200
        data = dict(row)
        data["nodes"] = json.loads(data.pop("nodes_json"))
        data["edges"] = json.loads(data.pop("edges_json"))
        data["metrics"] = json.loads(data.pop("metrics_json") or "{}")
        return jsonify({"ok": True, "snapshot": data, "session_id": sid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@forensics_z31_bp.route("/snapshot/<sid>/<int:idx>", methods=["GET"])
def get_snapshot_by_index(sid, idx):
    """Get a specific snapshot by index."""
    try:
        with _fdb() as conn:
            row = conn.execute(
                "SELECT * FROM dag_snapshots WHERE session_id=? AND snapshot_index=?",
                (sid, idx)
            ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": f"Snapshot {idx} not found"}), 200
        data = dict(row)
        data["nodes"] = json.loads(data.pop("nodes_json"))
        data["edges"] = json.loads(data.pop("edges_json"))
        data["metrics"] = json.loads(data.pop("metrics_json") or "{}")
        return jsonify({"ok": True, "snapshot": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@forensics_z31_bp.route("/fingerprint/<sid>", methods=["GET"])
def get_fingerprint(sid):
    try:
        fp = _session_fingerprint(sid)
        try:
            with _fdb() as conn:
                n = conn.execute(
                    "SELECT COUNT(*) AS c FROM replay_events WHERE session_id=?", (sid,)
                ).fetchone()["c"]
        except Exception:
            n = 0
        return jsonify({"ok": True, "session_id": sid, "fingerprint": fp, "event_count": n})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@forensics_z31_bp.route("/integrity/<sid>", methods=["GET"])
def get_integrity(sid):
    """Full replay integrity report with drift detection."""
    try:
        report    = _integrity_score(sid)
        client_fp = request.args.get("fingerprint", "")
        if client_fp:
            report["drift"] = _detect_drift(sid, client_fp)
        return jsonify({"ok": True, "integrity": report, "session_id": sid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@forensics_z31_bp.route("/sessions", methods=["GET"])
def list_sessions_forensic():
    """
    Historical session browser with filters:
    ?filter=failed|retries|escalated|instability&provider=openai&since=<ts>&until=<ts>&limit=50
    """
    try:
        flt      = request.args.get("filter", "")
        provider = request.args.get("provider", "")
        since    = request.args.get("since", type=float)
        until    = request.args.get("until", type=float)
        limit    = min(int(request.args.get("limit", 50)), 200)

        with _fdb() as conn:
            rows = conn.execute(
                "SELECT session_id, "
                "  MAX(snapshot_index)+1 AS snapshot_count, "
                "  MIN(created_at) AS first_ts, "
                "  MAX(created_at) AS last_ts "
                "FROM dag_snapshots "
                "GROUP BY session_id "
                "ORDER BY last_ts DESC LIMIT ?",
                (limit,)
            ).fetchall()

        sessions = []
        for r in rows:
            sid  = r["session_id"]
            # Compute summary metrics per session
            with _fdb() as conn:
                ev_row = conn.execute(
                    "SELECT COUNT(*) AS ec FROM replay_events WHERE session_id=?", (sid,)
                ).fetchone()
                ec = ev_row["ec"] if ev_row else 0

            # Apply time filters
            if since and r["first_ts"] and r["first_ts"] < since:
                continue
            if until and r["last_ts"] and r["last_ts"] > until:
                continue

            sessions.append({
                "session_id":     sid,
                "snapshot_count": r["snapshot_count"],
                "event_count":    ec,
                "first_ts":       r["first_ts"],
                "last_ts":        r["last_ts"],
                "age_s":          int(time.time() - (r["last_ts"] or 0)),
            })

        return jsonify({"ok": True, "sessions": sessions, "total": len(sessions)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "sessions": []}), 200


@forensics_z31_bp.route("/export/<sid>", methods=["GET"])
def export_forensic_bundle(sid):
    """
    Generate a portable forensic bundle for a session.
    Returns gzip-compressed JSON bundle as base64.
    """
    try:
        with _fdb() as conn:
            snapshots = conn.execute(
                "SELECT snapshot_index, snapshot_hash, fingerprint, nodes_json, edges_json, metrics_json, created_at "
                "FROM dag_snapshots WHERE session_id=? ORDER BY snapshot_index ASC",
                (sid,)
            ).fetchall()
            events = conn.execute(
                "SELECT event_type, node_id, payload_json, fingerprint, ts "
                "FROM replay_events WHERE session_id=? ORDER BY ts ASC",
                (sid,)
            ).fetchall()

        bundle = {
            "version":    REPLAY_VERSION,
            "session_id": sid,
            "exported_at": time.time(),
            "fingerprint": _session_fingerprint(sid),
            "snapshots": [
                {
                    "index":       r["snapshot_index"],
                    "hash":        r["snapshot_hash"],
                    "fingerprint": r["fingerprint"],
                    "nodes":       json.loads(r["nodes_json"] or "[]"),
                    "edges":       json.loads(r["edges_json"] or "[]"),
                    "metrics":     json.loads(r["metrics_json"] or "{}"),
                    "created_at":  r["created_at"],
                }
                for r in snapshots
            ],
            "events": [
                {
                    "type":        r["event_type"],
                    "node_id":     r["node_id"],
                    "payload":     json.loads(r["payload_json"] or "{}"),
                    "fingerprint": r["fingerprint"],
                    "ts":          r["ts"],
                }
                for r in events
            ],
        }

        raw     = json.dumps(bundle, separators=(",", ":")).encode()
        compressed = gzip.compress(raw, compresslevel=6)
        b64     = base64.b64encode(compressed).decode()

        bundle_hash = hashlib.sha256(raw).hexdigest()[:32]

        # Persist export record
        with _fdb() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO forensic_exports (session_id, export_hash, bundle_json) VALUES (?,?,?)",
                (sid, bundle_hash, json.dumps({"hash": bundle_hash, "size": len(raw), "ts": time.time()}))
            )
            conn.commit()

        return jsonify({
            "ok":           True,
            "session_id":   sid,
            "bundle_hash":  bundle_hash,
            "size_bytes":   len(raw),
            "compressed_b64": b64,
            "snapshot_count": len(bundle["snapshots"]),
            "event_count":  len(bundle["events"]),
        })
    except Exception as e:
        logger.error(f"[Z31] export failed for {sid}: {e}")
        return jsonify({"ok": False, "error": str(e)}), 200


@forensics_z31_bp.route("/import", methods=["POST"])
def import_forensic_bundle():
    """
    Import a forensic bundle into isolated replay mode.
    NEVER affects the active runtime — all data written under isolated session prefix.
    Body: {bundle_b64: str, session_alias: str (optional)}
    """
    try:
        body   = request.get_json(force=True, silent=True) or {}
        b64    = body.get("bundle_b64", "")
        alias  = body.get("session_alias", "")

        if not b64:
            return jsonify({"ok": False, "error": "bundle_b64 required"}), 400

        raw    = gzip.decompress(base64.b64decode(b64))
        bundle = json.loads(raw)

        if bundle.get("version") != REPLAY_VERSION:
            return jsonify({"ok": False, "error": f"Unsupported bundle version: {bundle.get('version')}"}), 400

        original_sid = bundle.get("session_id", "unknown")
        replay_sid   = f"replay:{alias or original_sid}"

        # Validate fingerprint
        imported_fp  = bundle.get("fingerprint", "")
        events       = bundle.get("events", [])
        combined     = "|".join(e.get("fingerprint", "") for e in events)
        expected_fp  = hashlib.sha256(combined.encode()).hexdigest()[:32]
        fp_valid     = imported_fp == expected_fp

        # Write under isolated replay session ID
        snapshots = bundle.get("snapshots", [])
        with _fdb() as conn:
            for snap in snapshots:
                conn.execute(
                    "INSERT OR IGNORE INTO dag_snapshots "
                    "(session_id, snapshot_index, snapshot_hash, nodes_json, edges_json, metrics_json, fingerprint, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (replay_sid,
                     snap["index"],
                     snap.get("hash", ""),
                     json.dumps(snap.get("nodes", [])),
                     json.dumps(snap.get("edges", [])),
                     json.dumps(snap.get("metrics", {})),
                     snap.get("fingerprint", ""),
                     snap.get("created_at", time.time()))
                )
            for ev in events:
                conn.execute(
                    "INSERT INTO replay_events (session_id, event_type, node_id, payload_json, fingerprint, ts) "
                    "VALUES (?,?,?,?,?,?)",
                    (replay_sid, ev.get("type", "unknown"), ev.get("node_id"),
                     json.dumps(ev.get("payload", {})),
                     ev.get("fingerprint", ""),
                     ev.get("ts", time.time()))
                )
            conn.commit()

        return jsonify({
            "ok":              True,
            "replay_session_id": replay_sid,
            "original_session":  original_sid,
            "snapshot_count":    len(snapshots),
            "event_count":       len(events),
            "fingerprint_valid": fp_valid,
            "isolated":          True,
        })
    except Exception as e:
        logger.error(f"[Z31] import failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 200


@forensics_z31_bp.route("/snapshots/<sid>", methods=["DELETE"])
def purge_snapshots(sid):
    """Purge all snapshots and events for a session."""
    try:
        with _fdb() as conn:
            conn.execute("DELETE FROM dag_snapshots  WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM replay_events   WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM forensic_exports WHERE session_id=?", (sid,))
            conn.commit()
        return jsonify({"ok": True, "session_id": sid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@forensics_z31_bp.route("/db/stats", methods=["GET"])
def db_stats():
    """Operational DB stats for memory/scaling audit."""
    try:
        with _fdb() as conn:
            snap_count = conn.execute("SELECT COUNT(*) AS c FROM dag_snapshots").fetchone()["c"]
            ev_count   = conn.execute("SELECT COUNT(*) AS c FROM replay_events").fetchone()["c"]
            sess_count = conn.execute("SELECT COUNT(DISTINCT session_id) AS c FROM dag_snapshots").fetchone()["c"]
            try:
                page_size  = conn.execute("PRAGMA page_size").fetchone()[0]
                page_count = conn.execute("PRAGMA page_count").fetchone()[0]
                db_size_kb = int(page_size * page_count / 1024)
            except Exception:
                db_size_kb = -1
        return jsonify({
            "ok": True,
            "snapshot_count": snap_count,
            "event_count":    ev_count,
            "session_count":  sess_count,
            "db_size_kb":     db_size_kb,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
