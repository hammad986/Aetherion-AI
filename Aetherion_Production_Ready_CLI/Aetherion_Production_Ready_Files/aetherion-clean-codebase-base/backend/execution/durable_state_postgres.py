"""
execution/durable_state_postgres.py — Phase 3 (P3-STATE-01): Postgres adapter
for the durable execution control plane.

Implements the same store contract as
``execution/durable_state.SqliteDurableExecutionStore`` against a real
PostgreSQL database so that production/staging execution state is
Postgres-authoritative and shared safely across API replicas and workers.

Selection is NOT done here — ``durable_state.default_store()`` picks this
adapter whenever ``DATABASE_URL`` is a ``postgresql://`` DSN.

Contract notes (limitations made explicit):
  * Isolation is READ COMMITTED (Postgres default) + atomic compare-and-swap
    via ``UPDATE ... WHERE status_version = %s RETURNING *``. Two concurrent
    transitions on the same row serialize on the row lock; exactly one wins,
    the other sees zero rows and the caller's retry loop re-reads.
  * Idempotent-start uniqueness is enforced by a PARTIAL UNIQUE index on
    ``(kind, idempotency_key) WHERE idempotency_key IS NOT NULL``; a raced
    duplicate insert raises IntegrityError -> StoreError and the caller returns
    the winner's row.
  * Lease expiry/epoch fields are DOUBLE PRECISION (SQLite REAL is 8-byte; PG
    ``REAL`` is float4 and too coarse for epoch seconds — do not use it here).
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

try:  # psycopg2-binary is a declared runtime dependency (requirements.txt).
    import psycopg2
    from psycopg2 import extras
except Exception as _pg_import_error:  # pragma: no cover - import guard only
    psycopg2 = None
    extras = None
    _pg_import_error = _pg_import_error

from execution.durable_state import (
    DEFAULT_LEASE_SECONDS,
    STATUS_RUNNING,
    StoreError,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS durable_executions (
    execution_id     TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    tenant_id        TEXT NOT NULL,
    idempotency_key  TEXT,
    status           TEXT NOT NULL,
    status_version   INTEGER NOT NULL DEFAULT 0,
    lease_owner      TEXT,
    lease_expires    DOUBLE PRECISION,
    payload_json     TEXT NOT NULL DEFAULT '{}',
    result_json      TEXT,
    failure_class    TEXT,
    created_at       DOUBLE PRECISION NOT NULL,
    updated_at       DOUBLE PRECISION NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_durable_idem
    ON durable_executions (kind, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_durable_tenant ON durable_executions (tenant_id);
"""


class PostgresDurableExecutionStore:
    """Postgres-backed durable execution store (production authority)."""

    def __init__(self, dsn: str):
        if psycopg2 is None:  # pragma: no cover
            raise StoreError(
                "psycopg2 is required for the Postgres durable store; "
                "install requirements.txt"
            )
        self.dsn = dsn
        self._init_schema()

    # ── connection helpers ────────────────────────────────────────────────────

    def _connect(self):
        return psycopg2.connect(self.dsn)

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(_SCHEMA)
        finally:
            conn.close()

    def _fetchone(self, sql: str, params: tuple) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            with conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute(sql, params)
                    row = cur.fetchone()
                    return dict(row) if row is not None else None
        finally:
            conn.close()

    # ── contract methods (mirror SqliteDurableExecutionStore) ────────────────

    def fetch_by_idempotency_key(self, kind: str, key: str) -> Optional[Dict[str, Any]]:
        return self._fetchone(
            "SELECT * FROM durable_executions WHERE kind=%s AND idempotency_key=%s",
            (kind, key),
        )

    def insert(self, record: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO durable_executions
                           (execution_id, kind, tenant_id, idempotency_key, status,
                            status_version, lease_owner, lease_expires, payload_json,
                            result_json, failure_class, created_at, updated_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            record["execution_id"], record["kind"],
                            record["tenant_id"], record["idempotency_key"],
                            record["status"], record["status_version"],
                            record["lease_owner"], record["lease_expires"],
                            record["payload_json"], record["result_json"],
                            record["failure_class"], record["created_at"],
                            record["updated_at"],
                        ),
                    )
        except psycopg2.IntegrityError as exc:
            raise StoreError(f"idempotent insert raced: {exc}") from exc
        finally:
            conn.close()
        return dict(record)

    def fetch(self, execution_id: str) -> Optional[Dict[str, Any]]:
        return self._fetchone(
            "SELECT * FROM durable_executions WHERE execution_id=%s",
            (execution_id,),
        )

    def compare_and_transition(
        self,
        execution_id: str,
        expected_version: int,
        *,
        new_status: str,
        failure_class: Optional[str],
        lease_owner: Optional[str],
        lease_expires: Optional[float],
        result_json: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Atomic CAS on status_version (row-locked in Postgres). None on race."""
        return self._fetchone(
            """UPDATE durable_executions
               SET status=%s, status_version=status_version+1, failure_class=%s,
                   lease_owner=%s, lease_expires=%s,
                   result_json=COALESCE(%s, result_json), updated_at=%s
               WHERE execution_id=%s AND status_version=%s
               RETURNING *""",
            (
                new_status,
                failure_class,
                lease_owner,
                lease_expires,
                result_json,
                time.time(),
                execution_id,
                expected_version,
            ),
        )

    def acquire_expired_lease(
        self, owner: str, now: float
    ) -> list[Dict[str, Any]]:
        conn = self._connect()
        claimed: list[Dict[str, Any]] = []
        try:
            with conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute(
                        """SELECT * FROM durable_executions
                           WHERE status=%s
                             AND (lease_expires IS NULL OR lease_expires < %s)
                           FOR UPDATE SKIP LOCKED""",
                        (STATUS_RUNNING, now),
                    )
                    rows = cur.fetchall()
                    for row in rows:
                        cur.execute(
                            """UPDATE durable_executions
                               SET lease_owner=%s, lease_expires=%s, updated_at=%s
                               WHERE execution_id=%s AND status_version=%s
                               RETURNING *""",
                            (
                                owner,
                                now + DEFAULT_LEASE_SECONDS,
                                now,
                                row["execution_id"],
                                row["status_version"],
                            ),
                        )
                        fresh = cur.fetchone()
                        if fresh is not None:
                            claimed.append(dict(fresh))
        finally:
            conn.close()
        return claimed

    def count_kind(self, kind: str) -> int:
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM durable_executions WHERE kind=%s",
            (kind,),
        )
        return int(row["n"]) if row else 0

    def compare_and_update_payload(
        self,
        execution_id: str,
        expected_version: int,
        payload_json: str,
    ) -> Optional[Dict[str, Any]]:
        return self._fetchone(
            """UPDATE durable_executions
               SET payload_json=%s, status_version=status_version+1, updated_at=%s
               WHERE execution_id=%s AND status_version=%s
               RETURNING *""",
            (payload_json, time.time(), execution_id, expected_version),
        )

    def list_for_tenant(self, tenant_id: str, limit: int = 50) -> list[Dict[str, Any]]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM durable_executions WHERE tenant_id=%s ORDER BY created_at DESC LIMIT %s",
                    (tenant_id, limit),
                )
                rows = cur.fetchall() or []
                return [dict(r) for r in rows]
        finally:
            conn.close()


def _selfcheck_sql_translations() -> None:
    """Dev-only: assert the two adapters expose the same contract surface."""
    import inspect

    from execution import durable_state

    sqlite_methods = {
        n for n, _ in inspect.getmembers(
            durable_state.SqliteDurableExecutionStore, inspect.isfunction
        )
    }
    pg_methods = {n for n, _ in inspect.getmembers(
        PostgresDurableExecutionStore, inspect.isfunction
    )}
    missing = sqlite_methods - pg_methods
    assert not missing, f"Postgres adapter missing contract methods: {missing}"
    # Non-trivial logic leaves a runnable check behind (lazy-code rule).
    print("durable_state_postgres selfcheck OK", json.dumps(sorted(pg_methods)))


if __name__ == "__main__":  # pragma: no cover
    _selfcheck_sql_translations()
