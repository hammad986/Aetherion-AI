"""
db/postgres_store.py — PostgreSQL Store
========================================
Production-grade PostgreSQL backend using psycopg2.
Activated when DATABASE_URL env var points to a postgres:// URI.

Usage:
    from db.postgres_store import PostgresStore
    store = PostgresStore(dsn=os.environ["DATABASE_URL"])

Environment variables:
    DATABASE_URL   — postgresql://<user>:<password>@<host>:5432/<dbname>
    DB_POOL_MIN    — min pool connections (default 2)
    DB_POOL_MAX    — max pool connections (default 10)
"""
import logging
import os
from typing import Any, Dict, List, Optional

from .base_store import BaseStore

import re

def sanitize_dsn(dsn: str) -> str:
    """Redact password from connection strings."""
    if not dsn:
        return ""
    return re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', dsn)

logger = logging.getLogger("aetherion.db.postgres")


class PostgresStore(BaseStore):
    """
    Thread-safe PostgreSQL connection wrapper backed by a connection pool.
    """

    _pool = None   # class-level pool shared across instances

    def __init__(self, dsn: Optional[str] = None):
        self._dsn = dsn or os.getenv("DATABASE_URL", "")
        self._conn = None
        self._cursor = None
        if not self._dsn or not self._dsn.startswith(("postgres://", "postgresql://")):
            raise ValueError("Valid PostgreSQL DATABASE_URL not set")
        self._ensure_pool(self._dsn)
        self._acquire()

    @classmethod
    def _ensure_pool(cls, dsn: Optional[str] = None):
        if cls._pool is not None:
            return
        effective_dsn = dsn or os.getenv("DATABASE_URL", "")
        if not effective_dsn:
            raise ValueError("DATABASE_URL is not set for PostgreSQL connection pool")
        try:
            import psycopg2.pool
            min_conn = int(os.getenv("DB_POOL_MIN", 2))
            max_conn = int(os.getenv("DB_POOL_MAX", 10))
            cls._pool = psycopg2.pool.ThreadedConnectionPool(
                min_conn, max_conn,
                dsn=effective_dsn,
                connect_timeout=5,
            )
            logger.info("[PG] Connection pool created (min=%d max=%d)", min_conn, max_conn)
        except ImportError:
            raise RuntimeError(
                "psycopg2 is required for PostgreSQL support. "
                "Install it with: pip install psycopg2-binary"
            )
        except Exception as exc:
            sanitized = sanitize_dsn(str(exc))
            logger.error("[PG] Connection pool creation failed: %s", sanitized)
            raise RuntimeError(f"Failed to connect to PostgreSQL: {sanitized}") from exc

    def _acquire(self):
        self._conn = self._pool.getconn()
        self._conn.autocommit = False
        self._cursor = self._conn.cursor()

    def _normalize_sql(self, sql: str) -> str:
        if not sql:
            return ""
        s = sql.replace("?", "%s")
        if "INSERT OR IGNORE" in s:
            s = s.replace("INSERT OR IGNORE", "INSERT") + " ON CONFLICT DO NOTHING"
        if "INSERT OR REPLACE INTO hitl_requests" in s:
            s = s.replace(
                "INSERT OR REPLACE INTO hitl_requests (execution_id, payload, status) VALUES (%s, %s, %s)",
                "INSERT INTO hitl_requests (execution_id, payload, status) VALUES (%s, %s, %s) ON CONFLICT (execution_id) DO UPDATE SET payload = EXCLUDED.payload, status = EXCLUDED.status"
            )
        if "INSERT OR REPLACE INTO sessions" in s:
            s = s.replace(
                "INSERT OR REPLACE INTO sessions (id, user_id, title, task, status, model, created_at, updated_at)",
                "INSERT INTO sessions (id, user_id, title, task, status, model, created_at, updated_at)"
            )
            if "ON CONFLICT" not in s:
                s = s + " ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, task = EXCLUDED.task, status = EXCLUDED.status, model = EXCLUDED.model, updated_at = EXCLUDED.updated_at"
        if "INTEGER PRIMARY KEY AUTOINCREMENT" in s:
            s = s.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        return s

    def execute(self, sql: str, params: tuple = ()) -> Any:
        self._cursor.execute(self._normalize_sql(sql), params or ())
        return self._cursor

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict]:
        self._cursor.execute(self._normalize_sql(sql), params or ())
        row = self._cursor.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._cursor.description]
        return dict(zip(cols, row))

    def fetchall(self, sql: str, params: tuple = ()) -> List[Dict]:
        self._cursor.execute(self._normalize_sql(sql), params or ())
        rows = self._cursor.fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self._cursor.description]
        return [dict(zip(cols, r)) for r in rows]

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        if self._cursor:
            self._cursor.close()
            self._cursor = None
        if self._conn and self._pool:
            self._pool.putconn(self._conn)
            self._conn = None

    @classmethod
    def health_check(cls) -> Dict[str, Any]:
        """Return {ok, version, pool_used, pool_free}."""
        try:
            store = cls()
            row = store.fetchone("SELECT version()")
            pool = cls._pool
            result = {
                "ok": True,
                "version": row["version"] if row else "unknown",
                "pool_used": pool._used.__len__() if pool else 0,
                "pool_free": len(pool._pool) if pool else 0,
            }
            store.close()
            return result
        except Exception as e:
            return {"ok": False, "error": sanitize_dsn(str(e))}

    @classmethod
    def is_available(cls) -> bool:
        """True if DATABASE_URL is set and psycopg2 is importable."""
        if not os.getenv("DATABASE_URL", ""):
            return False
        try:
            import psycopg2  # noqa: F401
            return True
        except ImportError:
            return False
