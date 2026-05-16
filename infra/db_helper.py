"""
infra/db_helper.py — Centralized SQLite Concurrency Hardening
=============================================================
Phase Y — SQLite WAL Hardening

Provides a centralized connection helper that applies:
  - WAL journal mode (eliminates writer-blocks-all-readers)
  - NORMAL synchronous mode (safe with WAL, faster than FULL)
  - 5-second busy_timeout (retries on lock rather than instant fail)
  - 32MB mmap_size (reduces syscall overhead on large DBs)
  - Reasonable cache_size (-16000 = 16MB page cache)

The monkey-patch approach ensures all existing sqlite3.connect() calls
throughout the codebase automatically get WAL hardening without
requiring changes to hundreds of connection sites.

IMPORTANT: This module MUST be imported before any other module calls
sqlite3.connect(). In web_app.py this is guaranteed by the early import
block at the top of the file (lines 22-27).
"""

import sqlite3
import logging

logger = logging.getLogger("nexora.db")

_original_connect = sqlite3.connect
_patch_applied = False


def connect_with_wal(*args, **kwargs):
    """
    Centralized SQLite connection helper.

    Enables WAL mode, NORMAL synchronous, a generous busy_timeout,
    and performance pragmas to prevent 'database is locked' errors
    under concurrent multi-worker load.

    Safe to call for:
      - File-backed databases (WAL applied)
      - In-memory databases ':memory:' (WAL is silently ignored)
      - Any existing connection site (additive, non-breaking)
    """
    # Force a timeout to prevent instant failures on locked DBs.
    # Individual callers can still pass a larger timeout if they want.
    kwargs.setdefault("timeout", 5.0)

    conn = _original_connect(*args, **kwargs)
    try:
        # WAL mode: readers and writers do not block each other.
        # This is the single most impactful change for concurrent access.
        conn.execute("PRAGMA journal_mode=WAL;")
        # NORMAL is safe with WAL and significantly faster than FULL.
        conn.execute("PRAGMA synchronous=NORMAL;")
        # Retry up to 5 seconds before raising OperationalError.
        conn.execute("PRAGMA busy_timeout=5000;")
        # 16MB page cache — reduces I/O on large DBs like memory.db (127KB+).
        conn.execute("PRAGMA cache_size=-16000;")
        # Enable memory-mapped I/O for read-heavy access patterns.
        conn.execute("PRAGMA mmap_size=33554432;")  # 32MB
    except sqlite3.OperationalError as e:
        # Safe to ignore: in-memory DBs, read-only paths, or locked states
        # during migration. The connection is still returned and usable.
        logger.debug(
            "[DB Helper] PRAGMA failed (safe to ignore for in-memory or "
            "read-only connections): %s", e
        )
    return conn


def patch_sqlite_globally():
    """
    Monkey-patches the standard library sqlite3.connect so that ALL existing
    files in the Nexora codebase automatically inherit the WAL/concurrency
    benefits without needing to rewrite hundreds of connection points.

    Idempotent: safe to call multiple times (only patches once).
    """
    global _patch_applied
    if _patch_applied:
        logger.debug("[DB Helper] Global patch already applied — skipping.")
        return
    sqlite3.connect = connect_with_wal
    _patch_applied = True
    logger.info(
        "[DB Helper] Global sqlite3.connect patched with WAL concurrency "
        "hardening (WAL + NORMAL sync + 5s busy_timeout + 16MB cache + 32MB mmap)."
    )


def get_connection(db_path: str, row_factory=True):
    """
    Convenience wrapper: opens a WAL-hardened connection with Row factory.

    Use this in new code for explicit, readable connection construction.
    The monkey-patch already covers all legacy sqlite3.connect() calls.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file, or ':memory:'.
    row_factory : bool
        If True (default), sets conn.row_factory = sqlite3.Row for dict-like access.

    Returns
    -------
    sqlite3.Connection
    """
    conn = connect_with_wal(db_path, check_same_thread=False)
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn
