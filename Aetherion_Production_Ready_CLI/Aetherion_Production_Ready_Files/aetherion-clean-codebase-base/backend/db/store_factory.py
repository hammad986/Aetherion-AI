import os
import sqlite3
import logging
from typing import Optional
from .base_store import BaseStore
from .postgres_store import PostgresStore

logger = logging.getLogger("aetherion.db")

class SQLiteStore(BaseStore):
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = None

    def _get_conn(self):
        if not self._conn:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def execute(self, sql: str, params: tuple = ()):
        cursor = self._get_conn().cursor()
        cursor.execute(sql, params)
        return cursor

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        cursor = self.execute(sql, params)
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = self.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def commit(self) -> None:
        if self._conn:
            self._conn.commit()

    def rollback(self) -> None:
        if self._conn:
            self._conn.rollback()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

try:
    from core.environment import is_fail_closed_environment
except ImportError:
    def is_fail_closed_environment() -> bool:
        env = os.environ.get("ENVIRONMENT", os.environ.get("FLASK_ENV", "")).strip().lower()
        return env in ("production", "staging")

import re

def sanitize_dsn(dsn: str) -> str:
    """Redact password from connection strings to prevent credential leaks."""
    if not dsn:
        return ""
    return re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', dsn)

def is_production_or_staging() -> bool:
    """True if running in production or staging environment."""
    return is_fail_closed_environment()

class FallbackStore(BaseStore):
    def __init__(self, primary: BaseStore, fallback: BaseStore):
        self.primary = primary
        self.fallback = fallback
        self._active = primary

    def _execute_with_fallback(self, method_name, *args, **kwargs):
        try:
            func = getattr(self._active, method_name)
            return func(*args, **kwargs)
        except Exception as e:
            if self._active == self.primary:
                if is_fail_closed_environment():
                    raise RuntimeError(
                        f"PostgreSQL store operation failed in fail-closed environment ({sanitize_dsn(str(e))}). "
                        "Fallback to SQLite is strictly forbidden in production and staging."
                    ) from e
                logger.warning(f"[DB] Primary store failed ({sanitize_dsn(str(e))}). Falling back to SQLite.")
                self._active = self.fallback
                func = getattr(self._active, method_name)
                return func(*args, **kwargs)
            raise

    def execute(self, sql: str, params: tuple = ()):
        return self._execute_with_fallback('execute', sql, params)

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        return self._execute_with_fallback('fetchone', sql, params)

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        return self._execute_with_fallback('fetchall', sql, params)

    def commit(self) -> None:
        return self._execute_with_fallback('commit')

    def rollback(self) -> None:
        return self._execute_with_fallback('rollback')

    def close(self) -> None:
        self.primary.close()
        self.fallback.close()

def get_store(db_path: str = "sessions.db") -> BaseStore:
    """
    Authoritative store factory:
    - Production and staging environments explicitly require PostgreSQL.
    - Missing or invalid PostgreSQL configuration fails closed (raises RuntimeError).
    - SQLite is strictly isolated to development and test environments.
    """
    db_url = os.environ.get("DATABASE_URL", "").strip()
    is_prod = is_production_or_staging()
    is_pg = db_url.startswith(("postgres://", "postgresql://"))

    if is_prod:
        if not is_pg:
            raise RuntimeError(
                "Production and staging environments require PostgreSQL and fail closed. "
                "DATABASE_URL must be configured with a valid postgresql:// or postgres:// URI."
            )
        try:
            return PostgresStore(dsn=db_url)
        except Exception as e:
            logger.error(f"[DB] Failed to initialize PostgresStore in production/staging: {sanitize_dsn(str(e))}")
            raise RuntimeError(f"Production PostgreSQL store initialization failed: {sanitize_dsn(str(e))}") from e

    # Development / Testing environment
    mode = os.environ.get("POSTGRES_MODE", "false").lower()
    if is_pg or mode == "true":
        try:
            if not is_pg:
                raise ValueError("POSTGRES_MODE is true but DATABASE_URL is not set")
            return PostgresStore(dsn=db_url)
        except Exception as e:
            logger.warning(f"[DB] Dev/test PostgresStore init failed ({sanitize_dsn(str(e))}). Using SQLite fallback.")
            return SQLiteStore(db_path)

    return SQLiteStore(db_path)

