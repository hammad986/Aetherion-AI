"""
operational_graph.py — Phase Z49: Persistent Operational Graph + Execution Memory
==================================================================================
Z49A  Persistent Artifact Graph         — artifact_relationships table + lineage
Z49B  Execution Memory Persistence      — retry/recovery/escalation/replay history
Z49D  Execution Summary Engine          — event-derived summaries (no hallucination)
Z49E  Relationship-Aware Search         — artifact/session/replay-aware search
Z49F  Operational Stability Pass        — orphan detection, stale-link pruning
Z49G  Performance Governance            — query budgets, pagination, WAL safety
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("nexora.z49")

# ── Database path ──────────────────────────────────────────────────────────────
Z49_DB = os.environ.get("Z49_DB", "z49_graph.db")

# ── Query limits (Z49G — Performance Governance) ───────────────────────────────
MAX_GRAPH_RESULTS   = 500
MAX_SEARCH_RESULTS  = 200
MAX_SUMMARY_EVENTS  = 1000
STALE_REL_DAYS      = 90
PAGE_SIZE_DEFAULT   = 50


# ══════════════════════════════════════════════════════════════════════════════
# DDL — All Z49 tables
# ══════════════════════════════════════════════════════════════════════════════

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Z49A: Artifact relationship graph
CREATE TABLE IF NOT EXISTS artifact_relationships (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    rel_type        TEXT NOT NULL,
    -- rel_type values:
    --   parent_child | version | replay | recovery | failure |
    --   session | execution | dependency
    session_id      TEXT,
    execution_id    TEXT,
    replay_id       TEXT,
    metadata        TEXT DEFAULT '{}',
    created_at      REAL NOT NULL,
    UNIQUE(source_id, target_id, rel_type)
);
CREATE INDEX IF NOT EXISTS idx_ar_source   ON artifact_relationships(source_id);
CREATE INDEX IF NOT EXISTS idx_ar_target   ON artifact_relationships(target_id);
CREATE INDEX IF NOT EXISTS idx_ar_type     ON artifact_relationships(rel_type);
CREATE INDEX IF NOT EXISTS idx_ar_session  ON artifact_relationships(session_id);

-- Z49B: Execution memory — retry history
CREATE TABLE IF NOT EXISTS execution_retry_history (
    id              TEXT PRIMARY KEY,
    execution_id    TEXT NOT NULL,
    session_id      TEXT,
    attempt_number  INTEGER NOT NULL,
    strategy        TEXT,
    outcome         TEXT,   -- success | failure | partial
    error_summary   TEXT,
    duration_s      REAL,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_erh_exec    ON execution_retry_history(execution_id);

-- Z49B: Execution memory — recovery tracking
CREATE TABLE IF NOT EXISTS execution_recovery_log (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    execution_id    TEXT,
    trigger_event   TEXT,
    recovery_action TEXT,
    outcome         TEXT,   -- success | failure | partial
    recovered_at    REAL NOT NULL,
    metadata        TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_erl_session ON execution_recovery_log(session_id);

-- Z49B: Escalation history
CREATE TABLE IF NOT EXISTS execution_escalations (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    execution_id    TEXT,
    from_level      TEXT,
    to_level        TEXT,
    reason          TEXT,
    resolved        INTEGER DEFAULT 0,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ee_session  ON execution_escalations(session_id);

-- Z49B: Pressure trend persistence
CREATE TABLE IF NOT EXISTS execution_pressure_trends (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    recorded_at     REAL NOT NULL,
    cpu_pct         REAL,
    mem_pct         REAL,
    queue_depth     INTEGER,
    active_workers  INTEGER,
    pressure_score  REAL
);
CREATE INDEX IF NOT EXISTS idx_ept_session ON execution_pressure_trends(session_id);
CREATE INDEX IF NOT EXISTS idx_ept_time    ON execution_pressure_trends(recorded_at);

-- Z49B: Replay outcome persistence
CREATE TABLE IF NOT EXISTS replay_outcomes (
    id              TEXT PRIMARY KEY,
    replay_id       TEXT NOT NULL,
    session_id      TEXT,
    artifact_id     TEXT,
    outcome         TEXT,   -- success | failure | partial | aborted
    events_replayed INTEGER DEFAULT 0,
    duration_s      REAL,
    divergence_notes TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ro_replay   ON replay_outcomes(replay_id);
CREATE INDEX IF NOT EXISTS idx_ro_session  ON replay_outcomes(session_id);

-- Z49B: Replay bookmarks
CREATE TABLE IF NOT EXISTS replay_bookmarks (
    id              TEXT PRIMARY KEY,
    replay_id       TEXT NOT NULL,
    session_id      TEXT,
    label           TEXT NOT NULL,
    event_index     INTEGER,
    timestamp_mark  REAL,
    notes           TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rb_replay   ON replay_bookmarks(replay_id);

-- Z49B: Operator annotations
CREATE TABLE IF NOT EXISTS operator_annotations (
    id              TEXT PRIMARY KEY,
    target_type     TEXT NOT NULL,  -- artifact | session | replay | execution
    target_id       TEXT NOT NULL,
    author          TEXT DEFAULT 'operator',
    annotation      TEXT NOT NULL,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oa_target   ON operator_annotations(target_type, target_id);

-- Z49D: Execution summaries
CREATE TABLE IF NOT EXISTS execution_summaries (
    id              TEXT PRIMARY KEY,
    session_id      TEXT,
    execution_id    TEXT,
    replay_id       TEXT,
    artifact_id     TEXT,
    summary_type    TEXT NOT NULL,
    -- summary_type: execution | failure | recovery | replay | artifact | mission
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    key_moments     TEXT DEFAULT '[]',  -- JSON array of {ts, label, detail}
    outcome         TEXT,
    generated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_es_session  ON execution_summaries(session_id);
CREATE INDEX IF NOT EXISTS idx_es_type     ON execution_summaries(summary_type);

-- Z49E: Relationship-aware search index
CREATE TABLE IF NOT EXISTS search_index (
    id              TEXT PRIMARY KEY,
    entity_type     TEXT NOT NULL,  -- artifact | session | replay | execution
    entity_id       TEXT NOT NULL,
    keywords        TEXT NOT NULL,
    related_ids     TEXT DEFAULT '[]',  -- JSON array of related entity IDs
    last_accessed   REAL,
    access_count    INTEGER DEFAULT 0,
    indexed_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_si_type     ON search_index(entity_type);
CREATE INDEX IF NOT EXISTS idx_si_entity   ON search_index(entity_id);

-- Z49F: Operational stability audit log
CREATE TABLE IF NOT EXISTS stability_audit_log (
    id              TEXT PRIMARY KEY,
    audit_type      TEXT NOT NULL,
    findings        TEXT DEFAULT '[]',
    orphan_count    INTEGER DEFAULT 0,
    broken_refs     INTEGER DEFAULT 0,
    stale_count     INTEGER DEFAULT 0,
    score           REAL DEFAULT 100.0,
    audited_at      REAL NOT NULL
);
"""


# ══════════════════════════════════════════════════════════════════════════════
# Database connection + init
# ══════════════════════════════════════════════════════════════════════════════

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(Z49_DB, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_json(v: Any, default=None) -> Any:
    if default is None:
        default = {}
    try:
        return json.loads(v) if v else default
    except Exception:
        return default


def init_z49() -> None:
    """Initialize all Z49 tables. Safe to call multiple times."""
    with _connect() as c:
        c.executescript(_DDL)
        c.commit()
    logger.info("[Z49] Operational graph database initialized at %s", Z49_DB)


# ══════════════════════════════════════════════════════════════════════════════
# Z49A — Persistent Artifact Graph
# ══════════════════════════════════════════════════════════════════════════════

class ArtifactGraph:
    """All relationship linkage operations for Z49A."""

    # Relationship type constants
    REL_PARENT_CHILD = "parent_child"
    REL_VERSION      = "version"
    REL_REPLAY       = "replay"
    REL_RECOVERY     = "recovery"
    REL_FAILURE      = "failure"
    REL_SESSION      = "session"
    REL_EXECUTION    = "execution"
    REL_DEPENDENCY   = "dependency"

    VALID_REL_TYPES = {
        REL_PARENT_CHILD, REL_VERSION, REL_REPLAY, REL_RECOVERY,
        REL_FAILURE, REL_SESSION, REL_EXECUTION, REL_DEPENDENCY,
    }

    @staticmethod
    def add_relationship(
        source_id: str,
        target_id: str,
        rel_type: str,
        session_id: str = "",
        execution_id: str = "",
        replay_id: str = "",
        metadata: Optional[dict] = None,
    ) -> dict:
        if rel_type not in ArtifactGraph.VALID_REL_TYPES:
            raise ValueError(f"Invalid rel_type: {rel_type}")
        rid = uuid.uuid4().hex[:14]
        now = time.time()
        with _connect() as c:
            try:
                c.execute(
                    """INSERT OR IGNORE INTO artifact_relationships
                       (id, source_id, target_id, rel_type, session_id,
                        execution_id, replay_id, metadata, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (rid, source_id, target_id, rel_type,
                     session_id or "", execution_id or "", replay_id or "",
                     json.dumps(metadata or {}), now)
                )
                c.commit()
            except sqlite3.IntegrityError:
                pass  # Duplicate — silently skip (append-safe)
        return {"id": rid, "source_id": source_id, "target_id": target_id,
                "rel_type": rel_type, "created_at": now}

    @staticmethod
    def get_lineage(artifact_id: str, depth: int = 3) -> dict:
        """Return the full ancestor + descendant lineage up to `depth` hops."""
        visited: set = set()
        ancestors: List[dict] = []
        descendants: List[dict] = []

        def _walk(aid: str, direction: str, current_depth: int):
            if current_depth <= 0 or aid in visited:
                return
            visited.add(aid)
            with _connect() as c:
                if direction == "up":
                    rows = c.execute(
                        "SELECT * FROM artifact_relationships WHERE target_id=? AND rel_type IN (?,?) LIMIT 50",
                        (aid, ArtifactGraph.REL_PARENT_CHILD, ArtifactGraph.REL_VERSION)
                    ).fetchall()
                    for r in rows:
                        ancestors.append(dict(r))
                        _walk(r["source_id"], "up", current_depth - 1)
                else:
                    rows = c.execute(
                        "SELECT * FROM artifact_relationships WHERE source_id=? AND rel_type IN (?,?) LIMIT 50",
                        (aid, ArtifactGraph.REL_PARENT_CHILD, ArtifactGraph.REL_VERSION)
                    ).fetchall()
                    for r in rows:
                        descendants.append(dict(r))
                        _walk(r["target_id"], "down", current_depth - 1)

        _walk(artifact_id, "up", depth)
        visited.clear()
        _walk(artifact_id, "down", depth)

        with _connect() as c:
            all_rels = c.execute(
                """SELECT * FROM artifact_relationships
                   WHERE source_id=? OR target_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (artifact_id, artifact_id, MAX_GRAPH_RESULTS)
            ).fetchall()

        return {
            "artifact_id": artifact_id,
            "ancestors": ancestors,
            "descendants": descendants,
            "all_relationships": [dict(r) for r in all_rels],
        }

    @staticmethod
    def get_dependencies(artifact_id: str) -> List[dict]:
        with _connect() as c:
            rows = c.execute(
                "SELECT * FROM artifact_relationships WHERE source_id=? AND rel_type=? LIMIT ?",
                (artifact_id, ArtifactGraph.REL_DEPENDENCY, MAX_GRAPH_RESULTS)
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def list_by_session(session_id: str, limit: int = PAGE_SIZE_DEFAULT) -> List[dict]:
        limit = min(limit, MAX_GRAPH_RESULTS)
        with _connect() as c:
            rows = c.execute(
                "SELECT * FROM artifact_relationships WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def list_by_replay(replay_id: str, limit: int = PAGE_SIZE_DEFAULT) -> List[dict]:
        limit = min(limit, MAX_GRAPH_RESULTS)
        with _connect() as c:
            rows = c.execute(
                "SELECT * FROM artifact_relationships WHERE replay_id=? ORDER BY created_at DESC LIMIT ?",
                (replay_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def list_by_recovery(session_id: str, limit: int = PAGE_SIZE_DEFAULT) -> List[dict]:
        limit = min(limit, MAX_GRAPH_RESULTS)
        with _connect() as c:
            rows = c.execute(
                """SELECT * FROM artifact_relationships
                   WHERE session_id=? AND rel_type=?
                   ORDER BY created_at DESC LIMIT ?""",
                (session_id, ArtifactGraph.REL_RECOVERY, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def detect_orphans() -> List[str]:
        """Return artifact IDs that appear in relationships but have no matching
        artifact record in data/artifacts.db."""
        orphans: List[str] = []
        artifacts_db = os.path.join("data", "artifacts.db")
        if not os.path.exists(artifacts_db):
            return orphans
        with _connect() as c:
            known = {r[0] for r in c.execute(
                "SELECT DISTINCT source_id FROM artifact_relationships"
            ).fetchall()} | {r[0] for r in c.execute(
                "SELECT DISTINCT target_id FROM artifact_relationships"
            ).fetchall()}
        if not known:
            return orphans
        with sqlite3.connect(artifacts_db) as ac:
            existing = {r[0] for r in ac.execute("SELECT id FROM artifacts").fetchall()}
        orphans = list(known - existing)
        return orphans

    @staticmethod
    def prune_stale(days: int = STALE_REL_DAYS) -> int:
        cutoff = time.time() - (days * 86400)
        with _connect() as c:
            n = c.execute(
                "DELETE FROM artifact_relationships WHERE created_at < ?", (cutoff,)
            ).rowcount
            c.commit()
        if n:
            logger.info("[Z49G] Pruned %d stale artifact relationships (>%dd)", n, days)
        return n


# ══════════════════════════════════════════════════════════════════════════════
# Z49B — Execution Memory Persistence
# ══════════════════════════════════════════════════════════════════════════════

class ExecutionMemory:

    @staticmethod
    def record_retry(
        execution_id: str, attempt_number: int, strategy: str,
        outcome: str, error_summary: str = "", duration_s: float = 0.0,
        session_id: str = "",
    ) -> str:
        rid = uuid.uuid4().hex[:14]
        with _connect() as c:
            c.execute(
                """INSERT INTO execution_retry_history
                   (id, execution_id, session_id, attempt_number, strategy,
                    outcome, error_summary, duration_s, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rid, execution_id, session_id, attempt_number, strategy,
                 outcome, error_summary[:1000], duration_s, time.time())
            )
            c.commit()
        return rid

    @staticmethod
    def record_recovery(
        session_id: str, trigger_event: str, recovery_action: str,
        outcome: str, execution_id: str = "", metadata: Optional[dict] = None,
    ) -> str:
        rid = uuid.uuid4().hex[:14]
        with _connect() as c:
            c.execute(
                """INSERT INTO execution_recovery_log
                   (id, session_id, execution_id, trigger_event,
                    recovery_action, outcome, recovered_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (rid, session_id, execution_id, trigger_event,
                 recovery_action, outcome, time.time(),
                 json.dumps(metadata or {}))
            )
            c.commit()
        return rid

    @staticmethod
    def record_escalation(
        session_id: str, from_level: str, to_level: str,
        reason: str, execution_id: str = "",
    ) -> str:
        eid = uuid.uuid4().hex[:14]
        with _connect() as c:
            c.execute(
                """INSERT INTO execution_escalations
                   (id, session_id, execution_id, from_level, to_level, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (eid, session_id, execution_id, from_level, to_level, reason, time.time())
            )
            c.commit()
        return eid

    @staticmethod
    def record_pressure(
        session_id: str, cpu_pct: float = 0.0, mem_pct: float = 0.0,
        queue_depth: int = 0, active_workers: int = 0, pressure_score: float = 0.0,
    ) -> str:
        pid = uuid.uuid4().hex[:14]
        with _connect() as c:
            c.execute(
                """INSERT INTO execution_pressure_trends
                   (id, session_id, recorded_at, cpu_pct, mem_pct,
                    queue_depth, active_workers, pressure_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (pid, session_id, time.time(), cpu_pct, mem_pct,
                 queue_depth, active_workers, pressure_score)
            )
            c.commit()
        return pid

    @staticmethod
    def record_replay_outcome(
        replay_id: str, outcome: str, session_id: str = "",
        artifact_id: str = "", events_replayed: int = 0,
        duration_s: float = 0.0, divergence_notes: str = "",
    ) -> str:
        rid = uuid.uuid4().hex[:14]
        with _connect() as c:
            c.execute(
                """INSERT INTO replay_outcomes
                   (id, replay_id, session_id, artifact_id, outcome,
                    events_replayed, duration_s, divergence_notes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rid, replay_id, session_id, artifact_id, outcome,
                 events_replayed, duration_s, divergence_notes[:2000], time.time())
            )
            c.commit()
        return rid

    @staticmethod
    def add_replay_bookmark(
        replay_id: str, label: str, event_index: int = 0,
        timestamp_mark: float = 0.0, notes: str = "", session_id: str = "",
    ) -> str:
        bid = uuid.uuid4().hex[:14]
        with _connect() as c:
            c.execute(
                """INSERT INTO replay_bookmarks
                   (id, replay_id, session_id, label, event_index, timestamp_mark, notes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (bid, replay_id, session_id, label, event_index,
                 timestamp_mark, notes[:500], time.time())
            )
            c.commit()
        return bid

    @staticmethod
    def add_annotation(
        target_type: str, target_id: str, annotation: str, author: str = "operator",
    ) -> str:
        aid = uuid.uuid4().hex[:14]
        with _connect() as c:
            c.execute(
                """INSERT INTO operator_annotations
                   (id, target_type, target_id, author, annotation, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (aid, target_type, target_id, author, annotation[:2000], time.time())
            )
            c.commit()
        return aid

    @staticmethod
    def get_retry_history(execution_id: str, limit: int = PAGE_SIZE_DEFAULT) -> List[dict]:
        limit = min(limit, MAX_GRAPH_RESULTS)
        with _connect() as c:
            rows = c.execute(
                "SELECT * FROM execution_retry_history WHERE execution_id=? ORDER BY attempt_number DESC LIMIT ?",
                (execution_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_recovery_log(session_id: str, limit: int = PAGE_SIZE_DEFAULT) -> List[dict]:
        limit = min(limit, MAX_GRAPH_RESULTS)
        with _connect() as c:
            rows = c.execute(
                "SELECT * FROM execution_recovery_log WHERE session_id=? ORDER BY recovered_at DESC LIMIT ?",
                (session_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_escalations(session_id: str, limit: int = PAGE_SIZE_DEFAULT) -> List[dict]:
        limit = min(limit, MAX_GRAPH_RESULTS)
        with _connect() as c:
            rows = c.execute(
                "SELECT * FROM execution_escalations WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_pressure_trends(session_id: str, limit: int = 100) -> List[dict]:
        limit = min(limit, MAX_GRAPH_RESULTS)
        with _connect() as c:
            rows = c.execute(
                "SELECT * FROM execution_pressure_trends WHERE session_id=? ORDER BY recorded_at DESC LIMIT ?",
                (session_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_replay_outcomes(replay_id: str = "", session_id: str = "",
                            limit: int = PAGE_SIZE_DEFAULT) -> List[dict]:
        limit = min(limit, MAX_GRAPH_RESULTS)
        with _connect() as c:
            if replay_id:
                rows = c.execute(
                    "SELECT * FROM replay_outcomes WHERE replay_id=? ORDER BY created_at DESC LIMIT ?",
                    (replay_id, limit)
                ).fetchall()
            elif session_id:
                rows = c.execute(
                    "SELECT * FROM replay_outcomes WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                    (session_id, limit)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM replay_outcomes ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_replay_bookmarks(replay_id: str, limit: int = PAGE_SIZE_DEFAULT) -> List[dict]:
        limit = min(limit, MAX_GRAPH_RESULTS)
        with _connect() as c:
            rows = c.execute(
                "SELECT * FROM replay_bookmarks WHERE replay_id=? ORDER BY event_index ASC LIMIT ?",
                (replay_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_annotations(target_type: str, target_id: str) -> List[dict]:
        with _connect() as c:
            rows = c.execute(
                "SELECT * FROM operator_annotations WHERE target_type=? AND target_id=? ORDER BY created_at DESC LIMIT 50",
                (target_type, target_id)
            ).fetchall()
        return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# Z49D — Execution Summary Engine
# ══════════════════════════════════════════════════════════════════════════════

class SummaryEngine:
    """Generates summaries purely from persisted event data — no hallucination."""

    VALID_TYPES = {"execution", "failure", "recovery", "replay", "artifact", "mission"}

    @staticmethod
    def generate_execution_summary(session_id: str, execution_id: str = "") -> dict:
        """Build an execution summary from retry history and recovery log."""
        retries  = ExecutionMemory.get_retry_history(execution_id, limit=MAX_SUMMARY_EVENTS) if execution_id else []
        recovery = ExecutionMemory.get_recovery_log(session_id, limit=MAX_SUMMARY_EVENTS)
        escalations = ExecutionMemory.get_escalations(session_id)
        annotations = ExecutionMemory.get_annotations("session", session_id)

        successes = [r for r in retries if r.get("outcome") == "success"]
        failures  = [r for r in retries if r.get("outcome") == "failure"]
        rec_ok    = [r for r in recovery if r.get("outcome") == "success"]

        outcome = "unknown"
        if retries:
            last = retries[0]
            outcome = last.get("outcome", "unknown")

        key_moments: List[dict] = []
        if retries:
            key_moments.append({
                "ts": retries[-1].get("created_at", 0),
                "label": "First attempt",
                "detail": retries[-1].get("strategy", ""),
            })
        for esc in escalations[:3]:
            key_moments.append({
                "ts": esc.get("created_at", 0),
                "label": f"Escalation: {esc.get('from_level')} → {esc.get('to_level')}",
                "detail": esc.get("reason", ""),
            })
        if successes:
            key_moments.append({
                "ts": successes[0].get("created_at", 0),
                "label": "First success",
                "detail": successes[0].get("strategy", ""),
            })
        key_moments.sort(key=lambda x: x.get("ts", 0))

        body_lines = [
            f"Attempts: {len(retries)} | Successes: {len(successes)} | Failures: {len(failures)}",
            f"Recoveries attempted: {len(recovery)} | Successful: {len(rec_ok)}",
            f"Escalations: {len(escalations)}",
        ]
        if annotations:
            body_lines.append(f"Operator notes: {len(annotations)} annotation(s)")

        summary_id = SummaryEngine._persist(
            summary_type="execution",
            session_id=session_id,
            execution_id=execution_id,
            title=f"Execution Summary — {session_id[:12]}",
            body="\n".join(body_lines),
            key_moments=key_moments,
            outcome=outcome,
        )
        return {
            "id": summary_id,
            "summary_type": "execution",
            "session_id": session_id,
            "execution_id": execution_id,
            "title": f"Execution Summary — {session_id[:12]}",
            "body": "\n".join(body_lines),
            "key_moments": key_moments,
            "outcome": outcome,
        }

    @staticmethod
    def generate_replay_summary(replay_id: str, session_id: str = "") -> dict:
        outcomes   = ExecutionMemory.get_replay_outcomes(replay_id=replay_id)
        bookmarks  = ExecutionMemory.get_replay_bookmarks(replay_id)
        rels       = ArtifactGraph.list_by_replay(replay_id)

        total_events = sum(o.get("events_replayed", 0) for o in outcomes)
        ok_count  = len([o for o in outcomes if o.get("outcome") == "success"])
        fail_count = len([o for o in outcomes if o.get("outcome") == "failure"])

        key_moments: List[dict] = []
        for bm in bookmarks[:10]:
            key_moments.append({
                "ts": bm.get("timestamp_mark", bm.get("created_at", 0)),
                "label": bm.get("label", ""),
                "detail": bm.get("notes", ""),
            })

        outcome = outcomes[0].get("outcome", "unknown") if outcomes else "unknown"
        body = (
            f"Replay runs: {len(outcomes)} | Total events replayed: {total_events}\n"
            f"Successful: {ok_count} | Failed: {fail_count}\n"
            f"Bookmarks: {len(bookmarks)} | Linked artifacts: {len(rels)}"
        )

        summary_id = SummaryEngine._persist(
            summary_type="replay",
            session_id=session_id,
            replay_id=replay_id,
            title=f"Replay Summary — {replay_id[:12]}",
            body=body,
            key_moments=key_moments,
            outcome=outcome,
        )
        return {
            "id": summary_id,
            "summary_type": "replay",
            "replay_id": replay_id,
            "title": f"Replay Summary — {replay_id[:12]}",
            "body": body,
            "key_moments": key_moments,
            "outcome": outcome,
        }

    @staticmethod
    def generate_artifact_summary(artifact_id: str) -> dict:
        lineage = ArtifactGraph.get_lineage(artifact_id)
        deps    = ArtifactGraph.get_dependencies(artifact_id)
        annots  = ExecutionMemory.get_annotations("artifact", artifact_id)

        ancestors   = lineage.get("ancestors", [])
        descendants = lineage.get("descendants", [])
        all_rels    = lineage.get("all_relationships", [])

        rel_types: dict = {}
        for r in all_rels:
            rt = r.get("rel_type", "unknown")
            rel_types[rt] = rel_types.get(rt, 0) + 1

        body = (
            f"Ancestors: {len(ancestors)} | Descendants: {len(descendants)}\n"
            f"Dependencies: {len(deps)}\n"
            f"Relationship types: {rel_types}\n"
            f"Operator annotations: {len(annots)}"
        )

        summary_id = SummaryEngine._persist(
            summary_type="artifact",
            artifact_id=artifact_id,
            title=f"Artifact Summary — {artifact_id[:12]}",
            body=body,
            key_moments=[],
            outcome="ready",
        )
        return {
            "id": summary_id,
            "summary_type": "artifact",
            "artifact_id": artifact_id,
            "title": f"Artifact Summary — {artifact_id[:12]}",
            "body": body,
            "lineage_depth": len(ancestors) + len(descendants),
            "relationship_types": rel_types,
        }

    @staticmethod
    def generate_failure_summary(session_id: str) -> dict:
        recovery  = ExecutionMemory.get_recovery_log(session_id)
        escalations = ExecutionMemory.get_escalations(session_id)
        failures  = [r for r in recovery if r.get("outcome") == "failure"]

        key_moments: List[dict] = []
        for f in failures[:5]:
            key_moments.append({
                "ts": f.get("recovered_at", 0),
                "label": f"Recovery failed: {f.get('trigger_event', '')}",
                "detail": f.get("recovery_action", ""),
            })
        for e in escalations[:3]:
            key_moments.append({
                "ts": e.get("created_at", 0),
                "label": f"Escalation to {e.get('to_level')}",
                "detail": e.get("reason", ""),
            })
        key_moments.sort(key=lambda x: x.get("ts", 0))

        outcome = "recovered" if len(failures) < len(recovery) else "unresolved"
        body = (
            f"Recovery attempts: {len(recovery)} | Failed: {len(failures)}\n"
            f"Escalations: {len(escalations)}\n"
            f"Status: {outcome}"
        )

        summary_id = SummaryEngine._persist(
            summary_type="failure",
            session_id=session_id,
            title=f"Failure Summary — {session_id[:12]}",
            body=body,
            key_moments=key_moments,
            outcome=outcome,
        )
        return {
            "id": summary_id,
            "summary_type": "failure",
            "session_id": session_id,
            "title": f"Failure Summary — {session_id[:12]}",
            "body": body,
            "key_moments": key_moments,
            "outcome": outcome,
        }

    @staticmethod
    def list_summaries(
        summary_type: str = "",
        session_id: str = "",
        limit: int = PAGE_SIZE_DEFAULT,
        offset: int = 0,
    ) -> Tuple[List[dict], int]:
        limit = min(limit, MAX_GRAPH_RESULTS)
        with _connect() as c:
            filters, params = [], []
            if summary_type:
                filters.append("summary_type=?"); params.append(summary_type)
            if session_id:
                filters.append("session_id=?"); params.append(session_id)
            where = ("WHERE " + " AND ".join(filters)) if filters else ""
            total = c.execute(
                f"SELECT COUNT(*) FROM execution_summaries {where}", params
            ).fetchone()[0]
            rows = c.execute(
                f"SELECT * FROM execution_summaries {where} ORDER BY generated_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset]
            ).fetchall()
        return [dict(r) for r in rows], total

    @staticmethod
    def _persist(
        summary_type: str, title: str, body: str,
        key_moments: List[dict], outcome: str,
        session_id: str = "", execution_id: str = "",
        replay_id: str = "", artifact_id: str = "",
    ) -> str:
        sid = uuid.uuid4().hex[:14]
        with _connect() as c:
            c.execute(
                """INSERT INTO execution_summaries
                   (id, session_id, execution_id, replay_id, artifact_id,
                    summary_type, title, body, key_moments, outcome, generated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sid, session_id, execution_id, replay_id, artifact_id,
                 summary_type, title, body, json.dumps(key_moments),
                 outcome, time.time())
            )
            c.commit()
        return sid


# ══════════════════════════════════════════════════════════════════════════════
# Z49E — Relationship-Aware Search
# ══════════════════════════════════════════════════════════════════════════════

class GraphSearch:
    """Search that understands workspace relationships."""

    @staticmethod
    def index_entity(
        entity_type: str, entity_id: str, keywords: str,
        related_ids: Optional[List[str]] = None,
    ) -> None:
        now = time.time()
        with _connect() as c:
            c.execute(
                """INSERT OR REPLACE INTO search_index
                   (id, entity_type, entity_id, keywords, related_ids, indexed_at)
                   VALUES (coalesce(
                       (SELECT id FROM search_index WHERE entity_type=? AND entity_id=?),
                       ?
                   ), ?, ?, ?, ?, ?)""",
                (entity_type, entity_id, uuid.uuid4().hex[:14],
                 entity_type, entity_id, keywords,
                 json.dumps(related_ids or []), now)
            )
            c.commit()

    @staticmethod
    def search(
        query: str,
        entity_types: Optional[List[str]] = None,
        limit: int = PAGE_SIZE_DEFAULT,
        offset: int = 0,
    ) -> Tuple[List[dict], int]:
        """Full-text keyword search across all indexed entities."""
        limit = min(limit, MAX_SEARCH_RESULTS)
        tokens = [t.strip().lower() for t in query.split() if len(t.strip()) > 1]
        if not tokens:
            return [], 0

        # Build LIKE conditions — safe against injection (no user data in column names)
        conditions = " AND ".join(["LOWER(keywords) LIKE ?" for _ in tokens])
        params = [f"%{t}%" for t in tokens]

        if entity_types:
            placeholders = ",".join("?" * len(entity_types))
            type_clause = f" AND entity_type IN ({placeholders})"
            params_ext = params + entity_types
        else:
            type_clause = ""
            params_ext = params

        with _connect() as c:
            total = c.execute(
                f"SELECT COUNT(*) FROM search_index WHERE {conditions}{type_clause}",
                params_ext
            ).fetchone()[0]
            rows = c.execute(
                f"""SELECT * FROM search_index
                    WHERE {conditions}{type_clause}
                    ORDER BY access_count DESC, last_accessed DESC NULLS LAST
                    LIMIT ? OFFSET ?""",
                params_ext + [limit, offset]
            ).fetchall()

            # Update access stats
            ids = [r["id"] for r in rows]
            if ids:
                c.execute(
                    f"UPDATE search_index SET access_count = access_count + 1, last_accessed = ? WHERE id IN ({','.join('?'*len(ids))})",
                    [time.time()] + ids
                )
                c.commit()

        results = []
        for r in rows:
            rd = dict(r)
            rd["related_ids"]  = _safe_json(rd.get("related_ids"), [])
            # Enrich with relationship count
            with _connect() as c2:
                rel_count = c2.execute(
                    "SELECT COUNT(*) FROM artifact_relationships WHERE source_id=? OR target_id=?",
                    (rd["entity_id"], rd["entity_id"])
                ).fetchone()[0]
            rd["relationship_count"] = rel_count
            results.append(rd)

        return results, total

    @staticmethod
    def search_with_lineage(artifact_id: str, query: str, limit: int = 20) -> List[dict]:
        """Search restricted to an artifact's lineage subgraph."""
        lineage = ArtifactGraph.get_lineage(artifact_id)
        related = {artifact_id}
        for r in lineage.get("all_relationships", []):
            related.add(r.get("source_id", ""))
            related.add(r.get("target_id", ""))
        related.discard("")

        results, _ = GraphSearch.search(query, limit=MAX_SEARCH_RESULTS)
        filtered = [r for r in results if r["entity_id"] in related]
        return filtered[:limit]

    @staticmethod
    def suggest(prefix: str, entity_type: str = "", limit: int = 10) -> List[str]:
        """Return keyword suggestions matching a prefix (for UI autocomplete)."""
        limit = min(limit, 50)
        with _connect() as c:
            if entity_type:
                rows = c.execute(
                    """SELECT keywords FROM search_index WHERE entity_type=? AND LOWER(keywords) LIKE ?
                       ORDER BY access_count DESC LIMIT ?""",
                    (entity_type, f"%{prefix.lower()}%", limit)
                ).fetchall()
            else:
                rows = c.execute(
                    """SELECT keywords FROM search_index WHERE LOWER(keywords) LIKE ?
                       ORDER BY access_count DESC LIMIT ?""",
                    (f"%{prefix.lower()}%", limit)
                ).fetchall()

        suggestions: set = set()
        for r in rows:
            for word in r[0].split():
                if word.lower().startswith(prefix.lower()):
                    suggestions.add(word)
        return sorted(suggestions)[:limit]


# ══════════════════════════════════════════════════════════════════════════════
# Z49F — Operational Stability Pass
# ══════════════════════════════════════════════════════════════════════════════

class StabilityAuditor:

    @staticmethod
    def run_full_audit() -> dict:
        orphan_ids   = ArtifactGraph.detect_orphans()
        broken_refs  = StabilityAuditor._check_broken_replay_refs()
        stale_count  = StabilityAuditor._count_stale_relationships()
        dup_count    = StabilityAuditor._count_duplicate_relationships()
        wal_ok       = StabilityAuditor._validate_wal()

        findings: List[str] = []
        if orphan_ids:
            findings.append(f"{len(orphan_ids)} orphaned artifact reference(s) detected")
        if broken_refs:
            findings.append(f"{broken_refs} broken replay references in relationships")
        if stale_count:
            findings.append(f"{stale_count} stale relationships (>{STALE_REL_DAYS}d)")
        if dup_count:
            findings.append(f"{dup_count} duplicate relationship entries present")
        if not wal_ok:
            findings.append("WAL mode not active — risk of write contention")

        # Score: start at 100, deduct per finding category
        score = 100.0
        score -= min(len(orphan_ids) * 2, 20)
        score -= min(broken_refs * 3, 15)
        score -= min(stale_count * 1, 10)
        score -= min(dup_count * 2, 10)
        if not wal_ok:
            score -= 5
        score = max(0.0, score)

        audit_id = uuid.uuid4().hex[:14]
        with _connect() as c:
            c.execute(
                """INSERT INTO stability_audit_log
                   (id, audit_type, findings, orphan_count, broken_refs, stale_count, score, audited_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (audit_id, "full", json.dumps(findings),
                 len(orphan_ids), broken_refs, stale_count, score, time.time())
            )
            c.commit()

        return {
            "audit_id": audit_id,
            "orphans": orphan_ids,
            "broken_replay_refs": broken_refs,
            "stale_relationships": stale_count,
            "duplicate_relationships": dup_count,
            "wal_active": wal_ok,
            "findings": findings,
            "score": round(score, 1),
            "audited_at": time.time(),
        }

    @staticmethod
    def _check_broken_replay_refs() -> int:
        """Count relationship entries with a replay_id that has no outcome record."""
        with _connect() as c:
            rows = c.execute(
                "SELECT DISTINCT replay_id FROM artifact_relationships WHERE replay_id != ''"
            ).fetchall()
            total = 0
            for r in rows:
                rid = r[0]
                found = c.execute(
                    "SELECT COUNT(*) FROM replay_outcomes WHERE replay_id=?", (rid,)
                ).fetchone()[0]
                if not found:
                    total += 1
        return total

    @staticmethod
    def _count_stale_relationships() -> int:
        cutoff = time.time() - (STALE_REL_DAYS * 86400)
        with _connect() as c:
            return c.execute(
                "SELECT COUNT(*) FROM artifact_relationships WHERE created_at < ?", (cutoff,)
            ).fetchone()[0]

    @staticmethod
    def _count_duplicate_relationships() -> int:
        """Count rows that would be duplicates (same source/target/type) beyond the first."""
        with _connect() as c:
            result = c.execute(
                """SELECT SUM(cnt - 1) FROM (
                     SELECT COUNT(*) as cnt FROM artifact_relationships
                     GROUP BY source_id, target_id, rel_type
                     HAVING cnt > 1
                   )"""
            ).fetchone()[0]
        return int(result or 0)

    @staticmethod
    def _validate_wal() -> bool:
        with _connect() as c:
            mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        return mode.lower() == "wal"

    @staticmethod
    def get_audit_history(limit: int = 20) -> List[dict]:
        with _connect() as c:
            rows = c.execute(
                "SELECT * FROM stability_audit_log ORDER BY audited_at DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for r in rows:
            rd = dict(r)
            rd["findings"] = _safe_json(rd.get("findings"), [])
            result.append(rd)
        return result


# ══════════════════════════════════════════════════════════════════════════════
# Z49G — Performance Governance
# ══════════════════════════════════════════════════════════════════════════════

class PerformanceGovernor:

    # Graph query budget: no single query returns more than this
    QUERY_BUDGET = MAX_GRAPH_RESULTS

    @staticmethod
    def vacuum_search_index(max_entries: int = 10000) -> int:
        """Remove oldest search index entries if over limit."""
        with _connect() as c:
            total = c.execute("SELECT COUNT(*) FROM search_index").fetchone()[0]
            if total <= max_entries:
                return 0
            excess = total - max_entries
            c.execute(
                """DELETE FROM search_index WHERE id IN (
                     SELECT id FROM search_index
                     ORDER BY COALESCE(last_accessed, indexed_at) ASC
                     LIMIT ?
                   )""", (excess,)
            )
            c.commit()
        logger.info("[Z49G] Vacuumed %d stale search index entries", excess)
        return excess

    @staticmethod
    def vacuum_pressure_trends(max_per_session: int = 500) -> int:
        """Keep only the most recent N pressure records per session."""
        with _connect() as c:
            sessions = [r[0] for r in c.execute(
                "SELECT DISTINCT session_id FROM execution_pressure_trends"
            ).fetchall()]
            total_pruned = 0
            for sid in sessions:
                count = c.execute(
                    "SELECT COUNT(*) FROM execution_pressure_trends WHERE session_id=?", (sid,)
                ).fetchone()[0]
                if count > max_per_session:
                    excess = count - max_per_session
                    c.execute(
                        """DELETE FROM execution_pressure_trends WHERE id IN (
                             SELECT id FROM execution_pressure_trends
                             WHERE session_id=? ORDER BY recorded_at ASC LIMIT ?
                           )""", (sid, excess)
                    )
                    total_pruned += excess
            c.commit()
        if total_pruned:
            logger.info("[Z49G] Pruned %d excess pressure trend records", total_pruned)
        return total_pruned

    @staticmethod
    def prune_stale_summaries(days: int = 180) -> int:
        cutoff = time.time() - (days * 86400)
        with _connect() as c:
            n = c.execute(
                "DELETE FROM execution_summaries WHERE generated_at < ?", (cutoff,)
            ).rowcount
            c.commit()
        if n:
            logger.info("[Z49G] Pruned %d stale execution summaries", n)
        return n

    @staticmethod
    def run_maintenance() -> dict:
        pruned_rels    = ArtifactGraph.prune_stale()
        pruned_index   = PerformanceGovernor.vacuum_search_index()
        pruned_pressure = PerformanceGovernor.vacuum_pressure_trends()
        pruned_summaries = PerformanceGovernor.prune_stale_summaries()
        return {
            "pruned_stale_relationships": pruned_rels,
            "pruned_search_index_entries": pruned_index,
            "pruned_pressure_records": pruned_pressure,
            "pruned_old_summaries": pruned_summaries,
            "ran_at": time.time(),
        }

    @staticmethod
    def graph_stats() -> dict:
        with _connect() as c:
            rel_count    = c.execute("SELECT COUNT(*) FROM artifact_relationships").fetchone()[0]
            mem_count    = c.execute("SELECT COUNT(*) FROM execution_retry_history").fetchone()[0]
            rec_count    = c.execute("SELECT COUNT(*) FROM execution_recovery_log").fetchone()[0]
            esc_count    = c.execute("SELECT COUNT(*) FROM execution_escalations").fetchone()[0]
            replay_count = c.execute("SELECT COUNT(*) FROM replay_outcomes").fetchone()[0]
            bm_count     = c.execute("SELECT COUNT(*) FROM replay_bookmarks").fetchone()[0]
            summ_count   = c.execute("SELECT COUNT(*) FROM execution_summaries").fetchone()[0]
            idx_count    = c.execute("SELECT COUNT(*) FROM search_index").fetchone()[0]
            aud_count    = c.execute("SELECT COUNT(*) FROM stability_audit_log").fetchone()[0]
        return {
            "artifact_relationships": rel_count,
            "execution_retries": mem_count,
            "recovery_events": rec_count,
            "escalations": esc_count,
            "replay_outcomes": replay_count,
            "replay_bookmarks": bm_count,
            "summaries": summ_count,
            "search_index_entries": idx_count,
            "audit_events": aud_count,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Module bootstrap
# ══════════════════════════════════════════════════════════════════════════════

try:
    init_z49()
except Exception as _e:
    logger.warning("[Z49] Init deferred: %s", _e)
