"""
aetherion_cli/session_store.py
Local SQLite session store for the Aetherion CLI + TUI.

Design decisions (informed by Hermes hermes_state.py forensics):
- WAL mode with NFS/SMB fallback to DELETE journal mode
- FTS5 full-text search over session content
- macOS F_FULLFSYNC (checkpoint_fullfsync) for crash safety
- Thread-safe: RLock + check-then-act pattern
- Schema migrations via SCHEMA_VERSION integer
- Messages stored as JSON blobs — role, content, ts, metadata
- Sessions carry: id, title, model, created_at, updated_at, message_count
- Input history persisted to separate table (not JSONL)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 3

_DDL = """
-- Core session table
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    total_tokens  INTEGER NOT NULL DEFAULT 0,
    metadata     TEXT NOT NULL DEFAULT '{}'
);

-- Per-session message log
CREATE TABLE IF NOT EXISTS messages (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role         TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system', 'tool')),
    content      TEXT NOT NULL,
    ts           REAL NOT NULL,
    tokens       INTEGER NOT NULL DEFAULT 0,
    metadata     TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS messages_session_idx ON messages(session_id, ts);

-- FTS5 over session content for fast search
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    session_id UNINDEXED,
    content='messages',
    content_rowid='rowid'
);

-- Auto-maintain FTS index via triggers
CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, session_id)
    VALUES (new.rowid, new.content, new.session_id);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, session_id)
    VALUES ('delete', old.rowid, old.content, old.session_id);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, session_id)
    VALUES ('delete', old.rowid, old.content, old.session_id);
    INSERT INTO messages_fts(rowid, content, session_id)
    VALUES (new.rowid, new.content, new.session_id);
END;

-- Persistent CLI input history
CREATE TABLE IF NOT EXISTS input_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT NOT NULL,
    ts         REAL NOT NULL,
    session_id TEXT
);

-- Schema version sentinel
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# ---------------------------------------------------------------------------
# WAL compatibility (identical pattern to Hermes)
# ---------------------------------------------------------------------------

_WAL_INCOMPAT_MARKERS = ("locking protocol", "not authorized")
_wal_warned: set[str] = set()
_wal_warned_lock = threading.Lock()


def _apply_wal(conn: sqlite3.Connection, db_label: str = "sessions.db") -> str:
    """Enable WAL mode with graceful NFS/SMB fallback."""
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
        if row and row[0] == "wal":
            _macos_checkpoint_barrier(conn)
            return "wal"
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        _macos_checkpoint_barrier(conn)
        return "wal"
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if not any(m in msg for m in _WAL_INCOMPAT_MARKERS):
            raise
        _warn_wal_once(db_label, exc)
        conn.execute("PRAGMA journal_mode=DELETE")
        return "delete"


def _macos_checkpoint_barrier(conn: sqlite3.Connection) -> None:
    """F_FULLFSYNC at checkpoints — macOS only, no-op elsewhere."""
    if sys.platform != "darwin":
        return
    try:
        conn.execute("PRAGMA checkpoint_fullfsync=1")
        conn.execute("PRAGMA synchronous=FULL")
    except sqlite3.OperationalError:
        pass


def _warn_wal_once(db_label: str, exc: Exception) -> None:
    with _wal_warned_lock:
        if db_label in _wal_warned:
            return
        _wal_warned.add(db_label)
    logger.warning(
        "%s: WAL mode unsupported on this filesystem (%s) — "
        "falling back to DELETE journal mode (NFS/SMB/FUSE detected).",
        db_label, exc,
    )


# ---------------------------------------------------------------------------
# Session DB
# ---------------------------------------------------------------------------

_DB_PATH: Optional[Path] = None
_db_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
_last_open_error: Optional[str] = None


def _get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        from aetherion_cli.config import CONFIG_DIR
        _DB_PATH = CONFIG_DIR / "sessions.db"
    return _DB_PATH


def _open() -> Optional[sqlite3.Connection]:
    """Open and initialise the session database, returning None on error."""
    global _conn, _last_open_error
    with _db_lock:
        if _conn is not None:
            return _conn
        path = _get_db_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(path),
                check_same_thread=False,
                timeout=10.0,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            _apply_wal(conn, db_label="sessions.db")
            conn.execute("PRAGMA cache_size=-8000")  # 8 MB page cache
            _init_schema(conn)
            _conn = conn
            _last_open_error = None
            return conn
        except Exception as exc:
            _last_open_error = str(exc)
            logger.error("Failed to open session store: %s", exc)
            return None


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and run pending migrations."""
    conn.executescript(_DDL)
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key='version'"
    ).fetchone()
    stored = int(row["value"]) if row else 0
    if stored < SCHEMA_VERSION:
        _run_migrations(conn, stored)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def _run_migrations(conn: sqlite3.Connection, from_version: int) -> None:
    """Incremental schema migrations."""
    if from_version < 1:
        # v1 → v2: add total_tokens to sessions
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN total_tokens INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # already exists
    if from_version < 2:
        # v2 → v3: add session_id index on input_history
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS ih_session_idx ON input_history(session_id, ts)")
        except sqlite3.OperationalError:
            pass
    if from_version < 3:
        # v3: nothing new — schema_meta row update only
        pass
    conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_session(
    title: str = "",
    model: str = "",
    session_id: Optional[str] = None,
    metadata: Optional[Dict] = None,
) -> Optional[str]:
    """Create a new session, returning its ID or None on failure."""
    conn = _open()
    if conn is None:
        return None
    sid = session_id or str(uuid.uuid4())
    now = time.time()
    with _db_lock:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO sessions(id, title, model, created_at, updated_at, metadata)"
                " VALUES(?,?,?,?,?,?)",
                (sid, title, model, now, now, json.dumps(metadata or {})),
            )
            conn.commit()
            return sid
        except Exception as exc:
            logger.error("create_session failed: %s", exc)
            return None


def save_message(
    session_id: str,
    role: str,
    content: str,
    tokens: int = 0,
    metadata: Optional[Dict] = None,
) -> Optional[str]:
    """Append a message to a session. Returns message ID or None."""
    conn = _open()
    if conn is None:
        return None
    mid = str(uuid.uuid4())
    now = time.time()
    with _db_lock:
        try:
            conn.execute(
                "INSERT INTO messages(id, session_id, role, content, ts, tokens, metadata)"
                " VALUES(?,?,?,?,?,?,?)",
                (mid, session_id, role, content, now, tokens, json.dumps(metadata or {})),
            )
            conn.execute(
                "UPDATE sessions SET updated_at=?, message_count=message_count+1,"
                " total_tokens=total_tokens+? WHERE id=?",
                (now, tokens, session_id),
            )
            conn.commit()
            return mid
        except Exception as exc:
            logger.error("save_message failed: %s", exc)
            return None


def update_session_title(session_id: str, title: str) -> bool:
    """Update the session title. Returns True on success."""
    conn = _open()
    if conn is None:
        return False
    with _db_lock:
        try:
            conn.execute(
                "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
                (title, time.time(), session_id),
            )
            conn.commit()
            return True
        except Exception as exc:
            logger.error("update_session_title failed: %s", exc)
            return False


def list_sessions(
    limit: int = 50,
    offset: int = 0,
    query: Optional[str] = None,
) -> List[Dict]:
    """Return recent sessions, newest first. Optional FTS5 query filter."""
    conn = _open()
    if conn is None:
        return []
    with _db_lock:
        try:
            if query and query.strip():
                # Search via FTS5 — match sessions containing the query
                q = query.strip()[:2048]
                rows = conn.execute(
                    "SELECT DISTINCT s.id, s.title, s.model, s.created_at, s.updated_at,"
                    " s.message_count, s.total_tokens"
                    " FROM sessions s"
                    " JOIN messages_fts f ON f.session_id = s.id"
                    " WHERE messages_fts MATCH ?"
                    " ORDER BY s.updated_at DESC LIMIT ? OFFSET ?",
                    (q, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, title, model, created_at, updated_at, message_count, total_tokens"
                    " FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.error("list_sessions failed: %s", exc)
            return []


def get_session(session_id: str) -> Optional[Dict]:
    """Return session metadata or None if not found."""
    conn = _open()
    if conn is None:
        return None
    with _db_lock:
        try:
            row = conn.execute(
                "SELECT id, title, model, created_at, updated_at, message_count, total_tokens, metadata"
                " FROM sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            return dict(row) if row else None
        except Exception as exc:
            logger.error("get_session failed: %s", exc)
            return None


def get_messages(
    session_id: str,
    limit: int = 200,
    offset: int = 0,
) -> List[Dict]:
    """Return messages for a session, oldest first."""
    conn = _open()
    if conn is None:
        return []
    with _db_lock:
        try:
            rows = conn.execute(
                "SELECT id, role, content, ts, tokens, metadata"
                " FROM messages WHERE session_id=? ORDER BY ts ASC LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["metadata"] = json.loads(d.get("metadata") or "{}")
                except Exception:
                    d["metadata"] = {}
                result.append(d)
            return result
        except Exception as exc:
            logger.error("get_messages failed: %s", exc)
            return []


def delete_session(session_id: str) -> bool:
    """Delete a session and all its messages. Returns True on success."""
    conn = _open()
    if conn is None:
        return False
    with _db_lock:
        try:
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            conn.commit()
            return True
        except Exception as exc:
            logger.error("delete_session failed: %s", exc)
            return False


def search_sessions(query: str, limit: int = 20) -> List[Dict]:
    """FTS5 search returning matching sessions with snippet."""
    conn = _open()
    if conn is None:
        return []
    q = query.strip()[:2048]
    if not q:
        return []
    with _db_lock:
        try:
            rows = conn.execute(
                "SELECT s.id, s.title, s.model, s.updated_at,"
                " snippet(messages_fts, 0, '[', ']', '…', 8) AS snippet"
                " FROM sessions s"
                " JOIN messages_fts f ON f.session_id = s.id"
                " WHERE messages_fts MATCH ?"
                " ORDER BY s.updated_at DESC LIMIT ?",
                (q, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.error("search_sessions failed: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Input history persistence
# ---------------------------------------------------------------------------

def append_input_history(text: str, session_id: Optional[str] = None) -> None:
    """Persist a CLI/TUI input line to the local history store."""
    conn = _open()
    if conn is None:
        return
    text = text.strip()
    if not text:
        return
    with _db_lock:
        try:
            conn.execute(
                "INSERT INTO input_history(text, ts, session_id) VALUES(?,?,?)",
                (text, time.time(), session_id),
            )
            conn.commit()
        except Exception:
            pass  # history write is best-effort


def get_input_history(limit: int = 500, session_id: Optional[str] = None) -> List[str]:
    """Return input history lines, newest first."""
    conn = _open()
    if conn is None:
        return []
    with _db_lock:
        try:
            if session_id:
                rows = conn.execute(
                    "SELECT text FROM input_history WHERE session_id=? ORDER BY ts DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT text FROM input_history ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [r["text"] for r in rows]
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def get_store_status() -> Dict[str, Any]:
    """Return diagnostic info about the local session store."""
    conn = _open()
    if conn is None:
        return {
            "available": False,
            "error": _last_open_error or "unknown",
            "path": str(_get_db_path()),
        }
    with _db_lock:
        try:
            session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            message_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            history_count = conn.execute("SELECT COUNT(*) FROM input_history").fetchone()[0]
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
            path = _get_db_path()
            size_bytes = path.stat().st_size if path.exists() else 0
            return {
                "available": True,
                "path": str(path),
                "size_bytes": size_bytes,
                "journal_mode": journal,
                "schema_version": SCHEMA_VERSION,
                "session_count": session_count,
                "message_count": message_count,
                "history_count": history_count,
            }
        except Exception as exc:
            return {"available": False, "error": str(exc)}
