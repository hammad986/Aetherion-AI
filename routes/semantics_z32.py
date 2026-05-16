"""
routes/semantics_z32.py — Phase Z32 Semantic Execution Intelligence + Adaptive Runtime Stability

Z32A — Context Compression Engine     (hot/warm/cold layers, trigger-based, audit-emitting)
Z32B — Semantic Confidence Engine     (multi-source scoring, drift tracking, HITL escalation)
Z32C — Adaptive DAG Replanning        (failure-aware, replayable, transparent)
Z32D — Procedural Skill Memory        (extraction, metadata, recall, governance-safe)
Z32E — Semantic Failure Intelligence  (clustering, predictive warnings, pressure metrics)
"""

import sqlite3
import json
import time
import hashlib
import logging
import os
import re
from flask import Blueprint, request, jsonify

logger = logging.getLogger("nexora.z32")

semantics_z32_bp = Blueprint("semantics_z32", __name__, url_prefix="/api/z32")

FORENSICS_DB = os.environ.get("FORENSICS_DB", "forensics.db")
SKILLS_DB    = os.environ.get("SKILLS_DB",    "forensics.db")  # same DB for simplicity

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- Z32A: Context compression audit log
CREATE TABLE IF NOT EXISTS compression_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT    NOT NULL,
    trigger       TEXT    NOT NULL,
    rows_before   INTEGER DEFAULT 0,
    rows_after    INTEGER DEFAULT 0,
    tokens_saved  INTEGER DEFAULT 0,
    summary_json  TEXT,
    ts            REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

-- Z32B: Confidence snapshots (drift tracking)
CREATE TABLE IF NOT EXISTS confidence_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT    NOT NULL,
    score         REAL    NOT NULL,
    sources_json  TEXT,
    drift         REAL    DEFAULT 0,
    ts            REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

-- Z32C: Replanning events
CREATE TABLE IF NOT EXISTS replanning_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT    NOT NULL,
    trigger       TEXT    NOT NULL,
    action        TEXT    NOT NULL,
    node_id       TEXT,
    before_json   TEXT,
    after_json    TEXT,
    ts            REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

-- Z32D: Procedural skill memory
CREATE TABLE IF NOT EXISTS skills (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint   TEXT    NOT NULL UNIQUE,
    name          TEXT    NOT NULL,
    description   TEXT,
    workflow_json TEXT    NOT NULL,
    validation_rate REAL  DEFAULT 0,
    avg_retries   REAL    DEFAULT 0,
    provider      TEXT,
    success_count INTEGER DEFAULT 1,
    last_used     REAL,
    created_at    REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

-- Z32E: Failure clusters
CREATE TABLE IF NOT EXISTS failure_clusters (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT    NOT NULL,
    cluster_type  TEXT    NOT NULL,
    evidence_json TEXT,
    severity      TEXT    DEFAULT 'WARNING',
    ts            REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

CREATE INDEX IF NOT EXISTS idx_comp_sid   ON compression_events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_conf_sid   ON confidence_snapshots(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_replan_sid ON replanning_events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_skill_fp   ON skills(fingerprint);
CREATE INDEX IF NOT EXISTS idx_fc_sid     ON failure_clusters(session_id, ts);
"""

def _ensure_schema():
    try:
        with sqlite3.connect(FORENSICS_DB, timeout=10) as conn:
            conn.executescript(_SCHEMA_SQL)
    except Exception as e:
        logger.error(f"[Z32] Schema bootstrap failed: {e}")

_ensure_schema()


def _db(timeout: float = 10) -> sqlite3.Connection:
    conn = sqlite3.connect(FORENSICS_DB, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ═════════════════════════════════════════════════════════════════════════════
# Z32A — Context Compression Engine
# ═════════════════════════════════════════════════════════════════════════════

TOKEN_ROUGH_CHARS = 4  # rough chars-per-token estimate

COMPRESSION_TRIGGERS = {
    "token_pressure":     lambda m: m.get("token_count", 0) > 8000,
    "node_overflow":      lambda m: m.get("node_count", 0) > 30,
    "replay_growth":      lambda m: m.get("snapshot_count", 0) > 200,
    "retry_loop":         lambda m: m.get("retry_count", 0) >= 5,
    "context_redundancy": lambda m: m.get("redundancy_ratio", 0) > 0.4,
}


def _detect_trigger(metrics: dict) -> str | None:
    for name, fn in COMPRESSION_TRIGGERS.items():
        try:
            if fn(metrics):
                return name
        except Exception:
            pass
    return None


def _rough_token_count(rows: list) -> int:
    total_chars = sum(len(r.get("text", "") or r.get("message", "")) for r in rows)
    return max(1, total_chars // TOKEN_ROUGH_CHARS)


def _redundancy_ratio(rows: list) -> float:
    """Estimate fraction of duplicate/near-duplicate log rows."""
    if not rows:
        return 0.0
    seen = set()
    dups = 0
    for r in rows:
        t = (r.get("text") or r.get("message") or "")[:80]
        if t in seen:
            dups += 1
        seen.add(t)
    return dups / len(rows)


def _compress_rows(rows: list) -> tuple[list, dict]:
    """
    Adaptive compression:
    - Keep the last 30 rows (hot context)
    - Keep all ERROR/CRITICAL rows (recovery lineage)
    - Summarize middle section (warm)
    - Archive rest (cold — already in forensics.db)
    Returns (compressed_rows, summary)
    """
    if len(rows) <= 50:
        return rows, {}

    KEEP_RECENT = 30
    KEEP_ERRORS = True

    hot   = rows[-KEEP_RECENT:]
    older = rows[:-KEEP_RECENT]

    critical_kept = []
    warm_rows     = []

    for r in older:
        t = (r.get("text") or r.get("message") or "").lower()
        lvl = (r.get("level") or "log").lower()
        if KEEP_ERRORS and (lvl in ("error", "critical") or "error" in t or "failed" in t):
            critical_kept.append(r)
        else:
            warm_rows.append(r)

    # Warm summary: count, phases, providers
    from collections import Counter
    phases    = Counter()
    providers = Counter()
    for r in warm_rows:
        t = r.get("text") or ""
        for phase, kws in [("plan",["plan","think"]),("code",["cod","writ","impl"]),("debug",["debug","fix","retry"])]:
            if any(k.lower() in t.lower() for k in kws):
                phases[phase] += 1
        for p in ("openai","groq","anthropic","gemini","mistral"):
            if p in t.lower():
                providers[p] += 1

    summary = {
        "archived_rows":      len(warm_rows),
        "critical_preserved": len(critical_kept),
        "phase_counts":       dict(phases),
        "provider_counts":    dict(providers),
        "ts_range":           [
            warm_rows[0].get("created_at") if warm_rows else None,
            warm_rows[-1].get("created_at") if warm_rows else None,
        ],
    }

    compressed = critical_kept + hot
    return compressed, summary


# ═════════════════════════════════════════════════════════════════════════════
# Z32B — Semantic Confidence Engine
# ═════════════════════════════════════════════════════════════════════════════

def _compute_confidence(metrics: dict) -> dict:
    """
    Multi-source semantic confidence score.

    Sources:
    - validation_rate:      fraction of nodes that reached 'done' without error
    - retry_penalty:        inverse of retry frequency
    - tool_stability:       fraction of tool calls that succeeded
    - historical_baseline:  average confidence from prior sessions (from DB)
    - dependency_health:    fraction of nodes with no blocking dependency

    Returns score 0.0–1.0 with source breakdown.
    """
    n_nodes       = max(1, metrics.get("node_count", 1))
    n_done        = metrics.get("done_count", 0)
    n_error       = metrics.get("error_count", 0)
    n_retries     = metrics.get("retry_count", 0)
    n_tool_calls  = max(1, metrics.get("tool_calls", 1))
    n_tool_ok     = metrics.get("tool_ok", n_tool_calls)
    n_blocked     = metrics.get("blocked_count", 0)
    session_id    = metrics.get("session_id")

    validation_rate = max(0.0, (n_done) / n_nodes) if n_nodes > 0 else 0.5
    retry_penalty   = min(1.0, n_retries / 10.0)
    tool_stability  = max(0.0, n_tool_ok / n_tool_calls)
    dep_health      = max(0.0, 1.0 - n_blocked / n_nodes)

    # Semantic contradiction penalty: errors in "done" phase = hallucinated success
    contradiction_penalty = min(0.3, n_error / max(1, n_nodes) * 0.5)

    # Historical baseline (last 5 sessions)
    hist_baseline = 0.75  # default
    if session_id:
        try:
            with _db() as conn:
                rows = conn.execute(
                    "SELECT score FROM confidence_snapshots ORDER BY ts DESC LIMIT 10"
                ).fetchall()
                if rows:
                    hist_baseline = sum(r["score"] for r in rows) / len(rows)
        except Exception:
            pass

    score = (
        0.35 * validation_rate
      + 0.20 * (1.0 - retry_penalty)
      + 0.20 * tool_stability
      + 0.15 * dep_health
      + 0.10 * hist_baseline
    ) - contradiction_penalty

    score = max(0.0, min(1.0, score))

    # Escalation threshold
    escalation_required = score < 0.45

    return {
        "score":                round(score, 4),
        "pct":                  round(score * 100),
        "level":                "HIGH" if score >= 0.75 else ("MEDIUM" if score >= 0.45 else "LOW"),
        "escalation_required":  escalation_required,
        "sources": {
            "validation_rate":       round(validation_rate, 3),
            "retry_penalty":         round(retry_penalty, 3),
            "tool_stability":        round(tool_stability, 3),
            "dep_health":            round(dep_health, 3),
            "hist_baseline":         round(hist_baseline, 3),
            "contradiction_penalty": round(contradiction_penalty, 3),
        },
    }


def _track_confidence_drift(session_id: str, score: float, sources: dict):
    """Persist confidence snapshot and compute drift from previous."""
    try:
        with _db() as conn:
            prev = conn.execute(
                "SELECT score FROM confidence_snapshots WHERE session_id=? ORDER BY ts DESC LIMIT 1",
                (session_id,)
            ).fetchone()
            drift = round(score - (prev["score"] if prev else score), 4)
            conn.execute(
                "INSERT INTO confidence_snapshots (session_id, score, sources_json, drift) VALUES (?,?,?,?)",
                (session_id, score, json.dumps(sources), drift)
            )
            conn.commit()
        return drift
    except Exception:
        return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# Z32C — Adaptive DAG Replanning
# ═════════════════════════════════════════════════════════════════════════════

REPLAN_TRIGGERS = {
    "retry_threshold":     lambda m: m.get("retry_count", 0) > 3,
    "validation_failure":  lambda m: m.get("validation_failures", 0) > 2,
    "tool_instability":    lambda m: m.get("tool_error_rate", 0) > 0.4,
    "dependency_broken":   lambda m: m.get("blocked_count", 0) > 0,
    "provider_failure":    lambda m: m.get("provider_failures", 0) > 1,
}

REPLAN_ACTIONS = {
    "retry_threshold":    "fallback_execution_path",
    "validation_failure": "node_replacement",
    "tool_instability":   "provider_switching",
    "dependency_broken":  "dependency_rerouting",
    "provider_failure":   "provider_switching",
}


def _evaluate_replan(metrics: dict) -> dict | None:
    """Evaluate if replanning is needed. Returns replanning plan or None."""
    for trigger, fn in REPLAN_TRIGGERS.items():
        try:
            if fn(metrics):
                action = REPLAN_ACTIONS.get(trigger, "fallback_execution_path")
                return {
                    "trigger":      trigger,
                    "action":       action,
                    "confidence":   metrics.get("confidence_score", 0.5),
                    "node_id":      metrics.get("failing_node"),
                    "recommended":  _replan_recommendation(action, metrics),
                }
        except Exception:
            pass
    return None


def _replan_recommendation(action: str, metrics: dict) -> str:
    recs = {
        "fallback_execution_path": "Insert recovery branch: skip failing node, reroute to recovery handler",
        "node_replacement":        "Replace failing validation node with re-verification node",
        "provider_switching":      f"Switch from {metrics.get('current_provider','unknown')} to backup provider",
        "dependency_rerouting":    "Resolve dependency deadlock: inject dependency-resolution node",
    }
    return recs.get(action, "Evaluate manual intervention")


def _log_replan_event(session_id: str, plan: dict):
    try:
        with _db() as conn:
            conn.execute(
                "INSERT INTO replanning_events (session_id, trigger, action, node_id, before_json, after_json) "
                "VALUES (?,?,?,?,?,?)",
                (session_id, plan["trigger"], plan["action"], plan.get("node_id"),
                 json.dumps({"metrics": {}}),
                 json.dumps({"recommendation": plan.get("recommended")}))
            )
            conn.commit()
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
# Z32D — Procedural Skill Memory
# ═════════════════════════════════════════════════════════════════════════════

def _workflow_fingerprint(nodes: list) -> str:
    """Deterministic fingerprint for a successful workflow pattern."""
    pattern = sorted([n.get("stage") or n.get("phase") or n.get("id", "") for n in nodes if n.get("state") == "done"])
    return hashlib.sha256("|".join(pattern).encode()).hexdigest()[:24]


def _extract_skill(session_id: str, nodes: list, metrics: dict) -> dict | None:
    """Extract a reusable skill from a successful execution."""
    done_nodes = [n for n in nodes if n.get("state") == "done" and n.get("stage")]
    if len(done_nodes) < 2:
        return None

    fp = _workflow_fingerprint(done_nodes)

    # Compute skill quality metrics
    total_retries    = sum(n.get("retries", 0) for n in done_nodes)
    avg_retries      = total_retries / max(1, len(done_nodes))
    validation_rate  = len(done_nodes) / max(1, len(nodes))
    providers        = list({n.get("provider") for n in done_nodes if n.get("provider")})

    skill = {
        "fingerprint":      fp,
        "name":             f"Workflow:{'-'.join(n.get('stage','?') for n in done_nodes[:4])}",
        "description":      f"{len(done_nodes)} nodes completed successfully with {total_retries} total retries",
        "workflow_json":    json.dumps([{"stage": n.get("stage"), "state": n.get("state")} for n in done_nodes]),
        "validation_rate":  round(validation_rate, 3),
        "avg_retries":      round(avg_retries, 3),
        "provider":         providers[0] if providers else None,
        "last_used":        time.time(),
    }
    return skill


def _store_skill(skill: dict):
    try:
        with _db() as conn:
            existing = conn.execute("SELECT id, success_count FROM skills WHERE fingerprint=?", (skill["fingerprint"],)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE skills SET success_count=success_count+1, last_used=?, avg_retries=? WHERE fingerprint=?",
                    (time.time(), skill["avg_retries"], skill["fingerprint"])
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO skills "
                    "(fingerprint, name, description, workflow_json, validation_rate, avg_retries, provider, last_used) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (skill["fingerprint"], skill["name"], skill["description"],
                     skill["workflow_json"], skill["validation_rate"], skill["avg_retries"],
                     skill.get("provider"), skill["last_used"])
                )
            conn.commit()
    except Exception as e:
        logger.error(f"[Z32] skill store failed: {e}")


def _recall_skills(workflow_hint: list) -> list:
    """Find skills matching a workflow pattern."""
    fp = _workflow_fingerprint([{"state": "done", "stage": s} for s in workflow_hint])
    try:
        with _db() as conn:
            exact = conn.execute(
                "SELECT * FROM skills WHERE fingerprint=? LIMIT 1", (fp,)
            ).fetchone()
            if exact:
                return [dict(exact)]
            # Fallback: return top skills by success rate
            rows = conn.execute(
                "SELECT * FROM skills ORDER BY validation_rate DESC, success_count DESC LIMIT 5"
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


# ═════════════════════════════════════════════════════════════════════════════
# Z32E — Semantic Failure Intelligence
# ═════════════════════════════════════════════════════════════════════════════

FAILURE_CLUSTERS = {
    "retry_storm":      lambda m: m.get("retry_count", 0) >= 5,
    "hallucinated_success": lambda m: m.get("error_after_done", 0) > 0,
    "tool_instability": lambda m: m.get("tool_error_rate", 0) > 0.35,
    "context_pressure": lambda m: m.get("token_count", 0) > 6000 or m.get("context_pressure", 0) > 0.7,
    "dependency_deadlock": lambda m: m.get("blocked_count", 0) > 1,
    "provider_degradation": lambda m: m.get("provider_failures", 0) > 2,
}

PREDICTIVE_RULES = [
    {"name": "retry_escalation",
     "condition": lambda m: 2 <= m.get("retry_count", 0) < 5,
     "message": "Retry count trending toward storm threshold — preemptive provider switch recommended",
     "severity": "WARNING"},
    {"name": "context_approaching",
     "condition": lambda m: 4000 < m.get("token_count", 0) <= 6000,
     "message": "Context approaching compression threshold — warm summary recommended",
     "severity": "WARNING"},
    {"name": "confidence_decay",
     "condition": lambda m: 0.35 <= m.get("confidence_score", 1.0) < 0.55,
     "message": "Confidence degrading — recommend validation checkpoint before proceeding",
     "severity": "DEGRADED"},
    {"name": "tool_degrading",
     "condition": lambda m: 0.2 < m.get("tool_error_rate", 0) <= 0.35,
     "message": "Tool error rate rising — switch to fallback tool provider",
     "severity": "WARNING"},
    {"name": "recovery_saturation",
     "condition": lambda m: m.get("replan_count", 0) >= 2,
     "message": "Multiple replanning events — mission may require human escalation",
     "severity": "DEGRADED"},
]


def _cluster_failures(metrics: dict) -> list:
    clusters = []
    for name, fn in FAILURE_CLUSTERS.items():
        try:
            if fn(metrics):
                clusters.append({"type": name, "severity": _cluster_severity(name)})
        except Exception:
            pass
    return clusters


def _cluster_severity(cluster_type: str) -> str:
    HIGH = {"hallucinated_success", "dependency_deadlock", "provider_degradation"}
    WARN = {"retry_storm", "tool_instability", "context_pressure"}
    return "CRITICAL" if cluster_type in HIGH else ("WARNING" if cluster_type in WARN else "DEGRADED")


def _predictive_warnings(metrics: dict) -> list:
    warnings = []
    for rule in PREDICTIVE_RULES:
        try:
            if rule["condition"](metrics):
                warnings.append({
                    "name":     rule["name"],
                    "message":  rule["message"],
                    "severity": rule["severity"],
                })
        except Exception:
            pass
    return warnings


def _pressure_metrics(metrics: dict) -> dict:
    """Compute aggregated runtime pressure metrics."""
    token_count   = metrics.get("token_count", 0)
    retry_count   = metrics.get("retry_count", 0)
    error_count   = metrics.get("error_count", 0)
    replan_count  = metrics.get("replan_count", 0)
    confidence    = metrics.get("confidence_score", 1.0)

    context_pressure    = min(1.0, token_count / 8000.0)
    reasoning_degradation = max(0.0, 1.0 - confidence)
    recovery_saturation  = min(1.0, replan_count / 4.0)
    semantic_instability = min(1.0, (retry_count * 0.1 + error_count * 0.15))

    overall = max(context_pressure, reasoning_degradation, recovery_saturation, semantic_instability * 0.8)

    return {
        "context_pressure":     round(context_pressure, 3),
        "reasoning_degradation": round(reasoning_degradation, 3),
        "recovery_saturation":  round(recovery_saturation, 3),
        "semantic_instability": round(semantic_instability, 3),
        "overall_pressure":     round(overall, 3),
        "pressure_level":       "CRITICAL" if overall >= 0.8 else ("HIGH" if overall >= 0.6 else ("ELEVATED" if overall >= 0.35 else "NOMINAL")),
    }


def _store_clusters(session_id: str, clusters: list):
    if not clusters:
        return
    try:
        with _db() as conn:
            for c in clusters:
                conn.execute(
                    "INSERT INTO failure_clusters (session_id, cluster_type, severity) VALUES (?,?,?)",
                    (session_id, c["type"], c["severity"])
                )
            conn.commit()
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
# Routes
# ═════════════════════════════════════════════════════════════════════════════

@semantics_z32_bp.route("/compress/<sid>", methods=["POST"])
def compress_context(sid):
    """
    Z32A: Trigger context compression for a session.
    Body: {rows: [...log rows...], metrics: {...}}
    """
    try:
        body    = request.get_json(force=True, silent=True) or {}
        rows    = body.get("rows", [])
        metrics = body.get("metrics", {})
        metrics.setdefault("token_count",      _rough_token_count(rows))
        metrics.setdefault("node_count",        metrics.get("node_count", 0))
        metrics.setdefault("redundancy_ratio",  _redundancy_ratio(rows))

        trigger = _detect_trigger(metrics)
        if not trigger and not body.get("force"):
            return jsonify({"ok": True, "compressed": False, "reason": "No trigger conditions met",
                            "metrics": metrics})

        trigger = trigger or "manual"
        compressed, summary = _compress_rows(rows)
        tokens_before = _rough_token_count(rows)
        tokens_after  = _rough_token_count(compressed)

        with _db() as conn:
            conn.execute(
                "INSERT INTO compression_events "
                "(session_id, trigger, rows_before, rows_after, tokens_saved, summary_json) "
                "VALUES (?,?,?,?,?,?)",
                (sid, trigger, len(rows), len(compressed), tokens_before - tokens_after, json.dumps(summary))
            )
            conn.commit()

        return jsonify({
            "ok":            True,
            "compressed":    True,
            "trigger":       trigger,
            "rows_before":   len(rows),
            "rows_after":    len(compressed),
            "tokens_before": tokens_before,
            "tokens_after":  tokens_after,
            "tokens_saved":  tokens_before - tokens_after,
            "summary":       summary,
            "compressed_rows": compressed,
        })
    except Exception as e:
        logger.error(f"[Z32] compress failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 200


@semantics_z32_bp.route("/compress/<sid>/history", methods=["GET"])
def compress_history(sid):
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT trigger, rows_before, rows_after, tokens_saved, summary_json, ts "
                "FROM compression_events WHERE session_id=? ORDER BY ts DESC LIMIT 50",
                (sid,)
            ).fetchall()
        return jsonify({"ok": True, "events": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@semantics_z32_bp.route("/confidence/<sid>", methods=["POST"])
def compute_confidence(sid):
    """
    Z32B: Compute + persist semantic confidence for a session.
    Body: metrics dict including node_count, done_count, error_count, retry_count, etc.
    """
    try:
        metrics = request.get_json(force=True, silent=True) or {}
        metrics["session_id"] = sid
        result = _compute_confidence(metrics)
        drift  = _track_confidence_drift(sid, result["score"], result["sources"])
        result["drift"] = drift

        # HITL escalation if confidence critically low
        if result["escalation_required"]:
            try:
                with _db() as conn:
                    conn.execute(
                        "INSERT INTO failure_clusters (session_id, cluster_type, evidence_json, severity) VALUES (?,?,?,?)",
                        (sid, "low_confidence",
                         json.dumps({"score": result["score"], "sources": result["sources"]}),
                         "CRITICAL")
                    )
                    conn.commit()
            except Exception:
                pass

        return jsonify({"ok": True, "confidence": result, "session_id": sid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@semantics_z32_bp.route("/confidence/<sid>/history", methods=["GET"])
def confidence_history(sid):
    """Z32B: Confidence drift tracking history."""
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        with _db() as conn:
            rows = conn.execute(
                "SELECT score, drift, sources_json, ts FROM confidence_snapshots "
                "WHERE session_id=? ORDER BY ts DESC LIMIT ?",
                (sid, limit)
            ).fetchall()
        return jsonify({"ok": True, "history": [dict(r) for r in rows], "session_id": sid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@semantics_z32_bp.route("/replan/<sid>", methods=["POST"])
def evaluate_replan(sid):
    """
    Z32C: Evaluate if replanning is needed + log replanning event.
    Body: metrics dict including retry_count, validation_failures, tool_error_rate, etc.
    """
    try:
        metrics = request.get_json(force=True, silent=True) or {}
        plan = _evaluate_replan(metrics)
        if plan:
            _log_replan_event(sid, plan)

        return jsonify({
            "ok":              True,
            "replan_needed":   plan is not None,
            "plan":            plan,
            "session_id":      sid,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@semantics_z32_bp.route("/replan/<sid>/history", methods=["GET"])
def replan_history(sid):
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT trigger, action, node_id, before_json, after_json, ts "
                "FROM replanning_events WHERE session_id=? ORDER BY ts DESC LIMIT 50",
                (sid,)
            ).fetchall()
        return jsonify({"ok": True, "events": [dict(r) for r in rows], "session_id": sid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@semantics_z32_bp.route("/skills", methods=["GET"])
def list_skills():
    """Z32D: List all procedural skills."""
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        with _db() as conn:
            rows = conn.execute(
                "SELECT fingerprint, name, description, validation_rate, avg_retries, "
                "provider, success_count, last_used, created_at "
                "FROM skills ORDER BY validation_rate DESC, success_count DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return jsonify({"ok": True, "skills": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@semantics_z32_bp.route("/skills/extract/<sid>", methods=["POST"])
def extract_skill(sid):
    """Z32D: Extract + store a procedural skill from a completed session."""
    try:
        body    = request.get_json(force=True, silent=True) or {}
        nodes   = body.get("nodes", [])
        metrics = body.get("metrics", {})

        skill = _extract_skill(sid, nodes, metrics)
        if not skill:
            return jsonify({"ok": True, "extracted": False, "reason": "Insufficient completed nodes"})

        _store_skill(skill)
        return jsonify({"ok": True, "extracted": True, "skill": skill})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@semantics_z32_bp.route("/skills/recall", methods=["POST"])
def recall_skill():
    """Z32D: Recall the best matching skill for a workflow hint."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        hint = body.get("workflow_stages", [])
        skills = _recall_skills(hint)
        return jsonify({"ok": True, "skills": skills})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@semantics_z32_bp.route("/intelligence/<sid>", methods=["POST"])
def semantic_intelligence(sid):
    """
    Z32E: Full semantic failure intelligence report.
    Body: runtime metrics dict.
    Returns: failure clusters, predictive warnings, pressure metrics.
    """
    try:
        metrics = request.get_json(force=True, silent=True) or {}
        metrics["session_id"] = sid

        clusters = _cluster_failures(metrics)
        warnings = _predictive_warnings(metrics)
        pressure = _pressure_metrics(metrics)

        _store_clusters(sid, clusters)

        # Determine operator-facing signal (high-signal, not telemetry spam)
        top_signal = None
        if pressure["pressure_level"] in ("CRITICAL", "HIGH"):
            top_signal = f"Runtime pressure {pressure['pressure_level']}: {pressure['pressure_level']} — {max(clusters, key=lambda c: c['severity'] == 'CRITICAL', default={}).get('type', 'elevated instability')}"
        elif warnings:
            top_signal = warnings[0]["message"]

        return jsonify({
            "ok":               True,
            "session_id":       sid,
            "clusters":         clusters,
            "predictive_warnings": warnings,
            "pressure":         pressure,
            "top_signal":       top_signal,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@semantics_z32_bp.route("/intelligence/<sid>/history", methods=["GET"])
def failure_history(sid):
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        with _db() as conn:
            rows = conn.execute(
                "SELECT cluster_type, severity, evidence_json, ts "
                "FROM failure_clusters WHERE session_id=? ORDER BY ts DESC LIMIT ?",
                (sid, limit)
            ).fetchall()
        return jsonify({"ok": True, "clusters": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@semantics_z32_bp.route("/status/<sid>", methods=["GET"])
def session_semantic_status(sid):
    """
    Unified semantic status for a session — used by frontend for the pressure bar.
    Reads from DB directly (no body required).
    """
    try:
        with _db() as conn:
            conf_row = conn.execute(
                "SELECT score, drift FROM confidence_snapshots WHERE session_id=? ORDER BY ts DESC LIMIT 1",
                (sid,)
            ).fetchone()
            replan_count = conn.execute(
                "SELECT COUNT(*) AS c FROM replanning_events WHERE session_id=?", (sid,)
            ).fetchone()["c"]
            comp_count = conn.execute(
                "SELECT COUNT(*) AS c FROM compression_events WHERE session_id=?", (sid,)
            ).fetchone()["c"]
            cluster_rows = conn.execute(
                "SELECT cluster_type, severity FROM failure_clusters WHERE session_id=? ORDER BY ts DESC LIMIT 10",
                (sid,)
            ).fetchall()

        conf_score = conf_row["score"] if conf_row else None
        conf_drift = conf_row["drift"] if conf_row else 0

        return jsonify({
            "ok":             True,
            "session_id":     sid,
            "confidence":     conf_score,
            "confidence_drift": conf_drift,
            "replan_count":   replan_count,
            "compression_count": comp_count,
            "clusters":       [dict(r) for r in cluster_rows],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
