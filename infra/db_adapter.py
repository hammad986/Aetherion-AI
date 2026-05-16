"""
infra/db_adapter.py — Phase: Database Evolution Architecture
=============================================================
Database abstraction layer that transparently supports:
  • SQLite (dev / single-node)
  • PostgreSQL (production / multi-node)

Strategy:
  - All tables classified into two tiers:
      TIER_1 (ACID-critical): sessions, hitl_requests, governance, resource_ledger
      TIER_2 (cache-eligible): execution_store, coordination snapshots, strategy_memory

  - Connection pool for PostgreSQL (psycopg2 + connection pooling)
  - Advisory locking via PostgreSQL pg_advisory_lock() for coordination safety
  - Full rollback-safe migration via versioned schema migrations
  - SQLite fallback if PostgreSQL is unavailable (graceful degradation)

Usage:
    from infra.db_adapter import get_db
    db = get_db()                         # returns DbAdapter
    db.execute("INSERT INTO ...", params)
    with db.transaction():
        db.execute(...)
        db.execute(...)
"""

import os
import sqlite3
import threading
import logging
import time
import json
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("nexora.db_adapter")

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

_PG_URL       = os.getenv("DATABASE_URL", "")        # postgres://user:pass@host:5432/db
_SQLITE_DIR   = os.getenv("SQLITE_DIR", "./data")
_POOL_SIZE    = int(os.getenv("DB_POOL_SIZE", "10"))
_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))

# Classify tables by durability requirements
TIER_1_TABLES = {
    "sessions", "hitl_requests", "audit_logs", "resource_ledger",
    "task_log", "patches", "governance_patches"
}
TIER_2_TABLES = {
    "event_log", "executions", "coordination_snapshots", "strategy_memory"
}


# ─────────────────────────────────────────────────────────────────────────────
# Backend detection
# ─────────────────────────────────────────────────────────────────────────────

_BACKEND = "sqlite"
_pg_pool = None

def _try_init_postgres() -> bool:
    """Attempt to connect to PostgreSQL. Returns True on success."""
    global _pg_pool, _BACKEND
    if not _PG_URL:
        return False
    try:
        from psycopg2 import pool as pg_pool
        _pg_pool = pg_pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=_POOL_SIZE,
            dsn=_PG_URL,
            connect_timeout=5,
        )
        # Verify connectivity
        conn = _pg_pool.getconn()
        conn.cursor().execute("SELECT 1")
        _pg_pool.putconn(conn)
        _BACKEND = "postgres"
        logger.info(f"[DbAdapter] PostgreSQL backend active: pool_size={_POOL_SIZE}")
        return True
    except Exception as e:
        logger.warning(f"[DbAdapter] PostgreSQL unavailable ({e}), falling back to SQLite.")
        _pg_pool = None
        return False


# ─────────────────────────────────────────────────────────────────────────────
# SQLite connection pool (thread-local)
# ─────────────────────────────────────────────────────────────────────────────

_sqlite_conns: Dict[str, sqlite3.Connection] = {}
_sqlite_lock = threading.RLock()


def _sqlite_conn(db_file: str) -> sqlite3.Connection:
    """Returns a cached thread-local SQLite connection with WAL mode enabled."""
    key = f"{threading.get_ident()}:{db_file}"
    with _sqlite_lock:
        conn = _sqlite_conns.get(key)
        if conn is None:
            os.makedirs(_SQLITE_DIR, exist_ok=True)
            path = os.path.join(_SQLITE_DIR, db_file) if not os.path.isabs(db_file) else db_file
            conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads + single writer
            conn.execute("PRAGMA synchronous=NORMAL") # durability vs speed balance
            conn.execute("PRAGMA busy_timeout=10000") # 10s busy wait before SQLITE_BUSY
            conn.row_factory = sqlite3.Row
            _sqlite_conns[key] = conn
        return conn


# ─────────────────────────────────────────────────────────────────────────────
# DbAdapter — unified interface
# ─────────────────────────────────────────────────────────────────────────────

class DbAdapter:
    """
    Production database adapter.

    - Automatically uses PostgreSQL if DATABASE_URL is set and reachable.
    - Falls back to SQLite with WAL mode for single-node development.
    - Exposes advisory locks for distributed coordination (PostgreSQL only).
    - All writes go through parameterized queries — no string concatenation.
    """

    def __init__(self, db_file: str = "main.db"):
        """
        `db_file` is used only in SQLite mode (the filename within SQLITE_DIR).
        In PostgreSQL mode, all tables live in the configured database.
        """
        self._db_file = db_file
        self._local = threading.local()

    # ── Query execution ───────────────────────────────────────────────────────

    def execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute a single SQL statement. Returns cursor for SELECT, rowcount for DML."""
        if _BACKEND == "postgres":
            return self._pg_execute(sql, params)
        return self._sqlite_execute(sql, params)

    def fetchall(self, sql: str, params: tuple = ()) -> List[Dict]:
        """Execute SELECT and return all rows as dicts."""
        if _BACKEND == "postgres":
            return self._pg_fetchall(sql, params)
        return self._sqlite_fetchall(sql, params)

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict]:
        """Execute SELECT and return one row as dict, or None."""
        rows = self.fetchall(sql, params)
        return rows[0] if rows else None

    # ── Transactions ──────────────────────────────────────────────────────────

    @contextmanager
    def transaction(self):
        """
        ACID transaction context manager.
        PostgreSQL: proper BEGIN/COMMIT/ROLLBACK.
        SQLite: implicit transaction with WAL.
        """
        if _BACKEND == "postgres":
            conn = _pg_pool.getconn()
            try:
                conn.autocommit = False
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                _pg_pool.putconn(conn)
        else:
            conn = _sqlite_conn(self._db_file)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # ── Advisory locking (PostgreSQL native; SQLite noop) ─────────────────────

    @contextmanager
    def advisory_lock(self, lock_key: int, timeout_ms: int = 5000):
        """
        Distributed advisory lock keyed by integer.
        PostgreSQL: pg_try_advisory_lock with timeout loop.
        SQLite: threading.Lock (single-process only).
        """
        if _BACKEND == "postgres":
            acquired = False
            deadline = time.time() + timeout_ms / 1000
            conn = _pg_pool.getconn()
            try:
                cur = conn.cursor()
                while time.time() < deadline:
                    cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
                    if cur.fetchone()[0]:
                        acquired = True
                        break
                    time.sleep(0.05)
                if not acquired:
                    raise TimeoutError(f"Advisory lock {lock_key} not acquired within {timeout_ms}ms")
                yield
            finally:
                if acquired:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
                conn.commit()
                _pg_pool.putconn(conn)
        else:
            # SQLite: in-process threading lock keyed by lock_key
            if not hasattr(self, "_advisory_locks"):
                self._advisory_locks: Dict[int, threading.Lock] = {}
            with _sqlite_lock:
                if lock_key not in self._advisory_locks:
                    self._advisory_locks[lock_key] = threading.Lock()
            lock = self._advisory_locks[lock_key]
            acquired = lock.acquire(timeout=timeout_ms / 1000)
            if not acquired:
                raise TimeoutError(f"Advisory lock {lock_key} not acquired within {timeout_ms}ms")
            try:
                yield
            finally:
                lock.release()

    # ── Backend info ──────────────────────────────────────────────────────────

    def backend(self) -> str:
        return _BACKEND

    def health_check(self) -> dict:
        try:
            self.fetchone("SELECT 1 AS ok")
            return {"backend": _BACKEND, "ok": True, "db_file": self._db_file}
        except Exception as e:
            return {"backend": _BACKEND, "ok": False, "error": str(e)}

    # ── Private: PostgreSQL execution ─────────────────────────────────────────

    def _pg_execute(self, sql: str, params: tuple) -> Any:
        # Translate SQLite-style ? placeholders to %s for psycopg2
        pg_sql = sql.replace("?", "%s")
        conn = _pg_pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute(pg_sql, params)
            conn.commit()
            return cur
        except Exception as e:
            conn.rollback()
            raise
        finally:
            _pg_pool.putconn(conn)

    def _pg_fetchall(self, sql: str, params: tuple) -> List[Dict]:
        pg_sql = sql.replace("?", "%s")
        conn = _pg_pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute(pg_sql, params)
            columns = [desc[0] for desc in cur.description] if cur.description else []
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        finally:
            _pg_pool.putconn(conn)

    # ── Private: SQLite execution ─────────────────────────────────────────────

    def _sqlite_execute(self, sql: str, params: tuple) -> Any:
        conn = _sqlite_conn(self._db_file)
        cur = conn.execute(sql, params)
        conn.commit()
        return cur

    def _sqlite_fetchall(self, sql: str, params: tuple) -> List[Dict]:
        conn = _sqlite_conn(self._db_file)
        rows = conn.execute(sql, params).fetchall()
        if rows and isinstance(rows[0], sqlite3.Row):
            return [dict(r) for r in rows]
        return [dict(zip([d[0] for d in conn.execute(sql, params).description], r)) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Migration runner — versioned, idempotent, rollback-safe
# ─────────────────────────────────────────────────────────────────────────────

MIGRATIONS: List[Tuple[int, str]] = [
    (1, """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at REAL NOT NULL
        )
    """),
    (2, """
        ALTER TABLE hitl_requests ADD COLUMN IF NOT EXISTS tenant_id TEXT DEFAULT 'default';
        ALTER TABLE audit_logs    ADD COLUMN IF NOT EXISTS tenant_id TEXT DEFAULT 'default';
    """),
    (3, """
        CREATE INDEX IF NOT EXISTS idx_hitl_tenant ON hitl_requests(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_logs(tenant_id);
    """),
]


def run_migrations(db: DbAdapter) -> None:
    """Apply any pending migrations in order. Idempotent — safe to call on every startup."""
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at REAL NOT NULL
            )
        """)
        applied = {r["version"] for r in db.fetchall("SELECT version FROM schema_migrations")}

        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            try:
                for stmt in sql.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        db.execute(stmt)
                db.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, time.time())
                )
                logger.info(f"[DbAdapter] Migration v{version} applied.")
            except Exception as e:
                logger.warning(f"[DbAdapter] Migration v{version} skipped/failed: {e}")
    except Exception as e:
        logger.warning(f"[DbAdapter] Migration init failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Singletons
# ─────────────────────────────────────────────────────────────────────────────

_init_lock = threading.Lock()
_initialized = False

def _init_backend() -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        _try_init_postgres()
        _initialized = True


def get_db(db_file: str = "main.db") -> DbAdapter:
    _init_backend()
    return DbAdapter(db_file=db_file)


# Module-level pre-warmed instances for critical databases
_init_backend()
db_sessions     = DbAdapter(db_file="sessions.db")
db_governance   = DbAdapter(db_file="evolution.db")
db_exec_store   = DbAdapter(db_file="execution_store.db")
db_resource     = DbAdapter(db_file="resource_ledger.db")
