"""
backend/execution/fabric/postgres_store.py — Phase 22 PostgreSQL Store for Execution Fabric.

Authoritative production persistence adapter for durable queue, worker registry,
and scheduling audit decisions using psycopg2 with atomic CAS semantics.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Set

try:
    import psycopg2
    from psycopg2 import extras
    from psycopg2 import pool as pg_pool
except ImportError:
    psycopg2 = None
    extras = None
    pg_pool = None

from .models import (
    QueueItem,
    QueueItemStatus,
    SchedulingDecision,
    SchedulingVerdict,
    WorkerRecord,
    WorkerStatus,
)
from .migrations import apply_fabric_migrations
from .store import BaseFabricStore, sanitize_dsn

logger = logging.getLogger("aetherion.fabric.postgres_store")

_PG_FABRIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS fabric_queue_items (
    item_id                     TEXT PRIMARY KEY,
    task_id                     TEXT NOT NULL,
    tenant_id                   TEXT NOT NULL,
    step_id                     TEXT,
    priority                    INTEGER NOT NULL DEFAULT 50,
    enqueued_at                 DOUBLE PRECISION NOT NULL,
    attempt                     INTEGER NOT NULL DEFAULT 1,
    execution_class             TEXT NOT NULL DEFAULT 'DEFAULT',
    deadline                    DOUBLE PRECISION,
    required_capabilities_json  TEXT NOT NULL DEFAULT '[]',
    status                      TEXT NOT NULL DEFAULT 'QUEUED',
    assigned_worker_id          TEXT,
    lease_id                    TEXT,
    dispatched_at               DOUBLE PRECISION,
    completed_at                DOUBLE PRECISION,
    error_message               TEXT,
    payload_json                TEXT NOT NULL DEFAULT '{}',
    updated_at                  DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_fabric_queue_status_prio
    ON fabric_queue_items (status, priority DESC, enqueued_at ASC);

CREATE INDEX IF NOT EXISTS ix_fabric_queue_tenant
    ON fabric_queue_items (tenant_id, status);

CREATE INDEX IF NOT EXISTS ix_fabric_queue_task
    ON fabric_queue_items (task_id, status);

CREATE TABLE IF NOT EXISTS fabric_workers (
    worker_id                   TEXT PRIMARY KEY,
    session_id                  TEXT NOT NULL,
    process_id                  INTEGER NOT NULL,
    max_concurrency             INTEGER NOT NULL DEFAULT 4,
    active_concurrency          INTEGER NOT NULL DEFAULT 0,
    active_leases_json          TEXT NOT NULL DEFAULT '[]',
    supported_capabilities_json TEXT NOT NULL DEFAULT '[]',
    status                      TEXT NOT NULL DEFAULT 'READY',
    start_time                  DOUBLE PRECISION NOT NULL,
    last_heartbeat              DOUBLE PRECISION NOT NULL,
    drain_state                 INTEGER NOT NULL DEFAULT 0,
    metadata_json               TEXT NOT NULL DEFAULT '{}',
    updated_at                  DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_fabric_workers_status
    ON fabric_workers (status, drain_state);

CREATE TABLE IF NOT EXISTS fabric_scheduler_decisions (
    decision_id                 TEXT PRIMARY KEY,
    item_id                     TEXT NOT NULL,
    task_id                     TEXT NOT NULL,
    tenant_id                   TEXT NOT NULL,
    step_id                     TEXT,
    worker_id                   TEXT,
    verdict                     TEXT NOT NULL,
    reason                      TEXT NOT NULL,
    timestamp                   DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_fabric_decisions_item
    ON fabric_scheduler_decisions (item_id, timestamp DESC);
"""


class PostgresFabricStore(BaseFabricStore):
    """
    Production-grade PostgreSQL store implementing BaseFabricStore for the Execution Fabric.
    Uses connection pooling and atomic compare-and-swap (CAS) queries.
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        min_pool: int = 2,
        max_pool: int = 10,
    ) -> None:
        if psycopg2 is None:
            raise RuntimeError(
                "psycopg2 is required for PostgresFabricStore. "
                "Install it with: pip install psycopg2-binary"
            )

        self.dsn = dsn or os.environ.get("DATABASE_URL", "").strip()
        if not self.dsn or not self.dsn.startswith(("postgres://", "postgresql://")):
            raise ValueError(
                "PostgresFabricStore requires a valid postgresql:// or postgres:// DSN."
            )

        self._min_pool = min_pool
        self._max_pool = max_pool
        self._pool: Optional[pg_pool.ThreadedConnectionPool] = None
        self._lock = threading.RLock()
        self._init_pool()
        self._init_db()

    def _init_pool(self) -> None:
        try:
            self._pool = pg_pool.ThreadedConnectionPool(
                self._min_pool,
                self._max_pool,
                dsn=self.dsn,
                connect_timeout=5,
            )
            logger.info("PostgreSQL connection pool initialized (min=%d, max=%d)", self._min_pool, self._max_pool)
        except Exception as exc:
            sanitized = sanitize_dsn(str(exc))
            logger.error("Failed to initialize PostgreSQL pool: %s", sanitized)
            raise RuntimeError(f"Failed to connect to PostgreSQL: {sanitized}") from exc

    def _get_connection(self):
        if self._pool is None:
            raise RuntimeError("Connection pool is not initialized")
        conn = self._pool.getconn()
        conn.autocommit = False
        return conn

    def _put_connection(self, conn, close: bool = False) -> None:
        if self._pool is not None and conn is not None:
            try:
                self._pool.putconn(conn, close=close)
            except Exception:
                pass

    def _init_db(self) -> None:
        conn = self._get_connection()
        try:
            apply_fabric_migrations(conn, is_postgres=True)
            self._put_connection(conn)
        except Exception as exc:
            self._put_connection(conn, close=True)
            sanitized = sanitize_dsn(str(exc))
            logger.error("Failed to initialize PostgreSQL fabric schema: %s", sanitized)
            raise RuntimeError(f"Database schema initialization failed: {sanitized}") from exc

    def close(self) -> None:
        """Close connection pool."""
        with self._lock:
            if self._pool is not None:
                try:
                    self._pool.closeall()
                except Exception:
                    pass
                self._pool = None

    # ── Queue Operations ─────────────────────────────────────────────────────

    def insert_queue_item(self, item: QueueItem) -> None:
        now = time.time()
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO fabric_queue_items (
                            item_id, task_id, tenant_id, step_id, priority, enqueued_at,
                            attempt, execution_class, deadline, required_capabilities_json,
                            status, assigned_worker_id, lease_id, dispatched_at, completed_at,
                            error_message, payload_json, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            item.item_id,
                            item.task_id,
                            item.tenant_id,
                            item.step_id,
                            item.priority,
                            item.enqueued_at,
                            item.attempt,
                            item.execution_class,
                            item.deadline,
                            json.dumps(list(item.required_capabilities)),
                            item.status.value,
                            item.assigned_worker_id,
                            item.lease_id,
                            item.dispatched_at,
                            item.completed_at,
                            item.error_message,
                            json.dumps(item.payload),
                            now,
                        ),
                    )
            self._put_connection(conn)
        except Exception:
            self._put_connection(conn, close=True)
            raise

    def get_queue_item(self, item_id: str) -> Optional[QueueItem]:
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM fabric_queue_items WHERE item_id = %s", (item_id,))
                    row = cur.fetchone()
                    if not row:
                        return None
                    return self._row_to_queue_item(dict(row))
        finally:
            self._put_connection(conn)

    def update_queue_item(self, item: QueueItem) -> None:
        now = time.time()
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE fabric_queue_items
                        SET status = %s,
                            priority = %s,
                            attempt = %s,
                            assigned_worker_id = %s,
                            lease_id = %s,
                            dispatched_at = %s,
                            completed_at = %s,
                            error_message = %s,
                            payload_json = %s,
                            updated_at = %s
                        WHERE item_id = %s
                        """,
                        (
                            item.status.value,
                            item.priority,
                            item.attempt,
                            item.assigned_worker_id,
                            item.lease_id,
                            item.dispatched_at,
                            item.completed_at,
                            item.error_message,
                            json.dumps(item.payload),
                            now,
                            item.item_id,
                        ),
                    )
            self._put_connection(conn)
        except Exception:
            self._put_connection(conn, close=True)
            raise

    def list_queue_items(
        self,
        status: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[QueueItem]:
        clauses = []
        params = []
        if status:
            clauses.append("status = %s")
            params.append(status)
        if tenant_id:
            clauses.append("tenant_id = %s")
            params.append(tenant_id)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM fabric_queue_items {where} ORDER BY priority DESC, enqueued_at ASC LIMIT %s"
        params.append(limit)

        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute(sql, tuple(params))
                    rows = cur.fetchall()
                    return [self._row_to_queue_item(dict(r)) for r in rows]
        finally:
            self._put_connection(conn)

    def count_active_tenant_items(self, tenant_id: str) -> int:
        active_statuses = (
            QueueItemStatus.DISPATCHING.value,
            QueueItemStatus.CLAIMED.value,
            QueueItemStatus.RUNNING.value,
        )
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM fabric_queue_items WHERE tenant_id = %s AND status = ANY(%s)",
                        (tenant_id, list(active_statuses)),
                    )
                    row = cur.fetchone()
                    return int(row[0]) if row else 0
        finally:
            self._put_connection(conn)

    def count_active_task_items(self, task_id: str) -> int:
        active_statuses = (
            QueueItemStatus.DISPATCHING.value,
            QueueItemStatus.CLAIMED.value,
            QueueItemStatus.RUNNING.value,
        )
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM fabric_queue_items WHERE task_id = %s AND status = ANY(%s)",
                        (task_id, list(active_statuses)),
                    )
                    row = cur.fetchone()
                    return int(row[0]) if row else 0
        finally:
            self._put_connection(conn)

    def count_global_active_items(self) -> int:
        active_statuses = (
            QueueItemStatus.DISPATCHING.value,
            QueueItemStatus.CLAIMED.value,
            QueueItemStatus.RUNNING.value,
        )
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM fabric_queue_items WHERE status = ANY(%s)",
                        (list(active_statuses),),
                    )
                    row = cur.fetchone()
                    return int(row[0]) if row else 0
        finally:
            self._put_connection(conn)

    def claim_item_atomic(
        self,
        item_id: str,
        worker_id: str,
        lease_id: str,
        expected_statuses: Optional[List[str]] = None,
    ) -> bool:
        """
        Atomically transition an item to CLAIMED status using CAS query.
        Returns True if claimed, False if another worker won the race.
        """
        now = time.time()
        statuses = expected_statuses or [
            QueueItemStatus.QUEUED.value,
            QueueItemStatus.REQUEUED.value,
            QueueItemStatus.DISPATCHING.value,
        ]
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE fabric_queue_items
                        SET status = %s,
                            assigned_worker_id = %s,
                            lease_id = %s,
                            updated_at = %s
                        WHERE item_id = %s AND status = ANY(%s)
                        RETURNING item_id
                        """,
                        (QueueItemStatus.CLAIMED.value, worker_id, lease_id, now, item_id, list(statuses)),
                    )
                    row = cur.fetchone()
                    return row is not None
        finally:
            self._put_connection(conn)

    def claim_item_for_dispatch_atomic(
        self,
        item_id: str,
        worker_id: str,
    ) -> bool:
        """
        Atomically transition an item to DISPATCHING status using PostgreSQL CAS query.
        Returns True if claimed for dispatch, False if another worker/scheduler won the race.
        """
        now = time.time()
        statuses = [QueueItemStatus.QUEUED.value, QueueItemStatus.REQUEUED.value]
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE fabric_queue_items
                        SET status = %s,
                            assigned_worker_id = %s,
                            dispatched_at = %s,
                            updated_at = %s
                        WHERE item_id = %s AND status = ANY(%s)
                        RETURNING item_id
                        """,
                        (QueueItemStatus.DISPATCHING.value, worker_id, now, now, item_id, list(statuses)),
                    )
                    row = cur.fetchone()
                    return row is not None
        finally:
            self._put_connection(conn)

    def release_dispatch_claim(
        self,
        item_id: str,
        worker_id: str,
    ) -> bool:
        """
        Rollback DISPATCHING to QUEUED if dispatch failed (e.g. lease acquisition failed).
        Only reverts if item is currently in DISPATCHING status for this worker.
        """
        now = time.time()
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE fabric_queue_items
                        SET status = %s,
                            assigned_worker_id = NULL,
                            dispatched_at = NULL,
                            updated_at = %s
                        WHERE item_id = %s AND status = %s AND assigned_worker_id = %s
                        RETURNING item_id
                        """,
                        (QueueItemStatus.QUEUED.value, now, item_id, QueueItemStatus.DISPATCHING.value, worker_id),
                    )
                    row = cur.fetchone()
                    return row is not None
        finally:
            self._put_connection(conn)

    def mark_running_atomic(
        self,
        item_id: str,
        worker_id: str,
    ) -> bool:
        """Atomically transition queue item from CLAIMED to RUNNING with worker fencing."""
        now = time.time()
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE fabric_queue_items
                        SET status = %s,
                            updated_at = %s
                        WHERE item_id = %s AND status = %s AND assigned_worker_id = %s
                        RETURNING item_id
                        """,
                        (QueueItemStatus.RUNNING.value, now, item_id, QueueItemStatus.CLAIMED.value, worker_id),
                    )
                    row = cur.fetchone()
                    return row is not None
        finally:
            self._put_connection(conn)

    def mark_completed_atomic(
        self,
        item_id: str,
        worker_id: str,
        completed_at: Optional[float] = None,
    ) -> bool:
        """Atomically transition queue item from RUNNING to COMPLETED with worker fencing."""
        now = time.time()
        c_at = completed_at if completed_at is not None else now
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE fabric_queue_items
                        SET status = %s,
                            completed_at = %s,
                            updated_at = %s
                        WHERE item_id = %s AND status = %s AND assigned_worker_id = %s
                        RETURNING item_id
                        """,
                        (QueueItemStatus.COMPLETED.value, c_at, now, item_id, QueueItemStatus.RUNNING.value, worker_id),
                    )
                    row = cur.fetchone()
                    return row is not None
        finally:
            self._put_connection(conn)

    def mark_failed_atomic(
        self,
        item_id: str,
        worker_id: str,
        error_message: str,
        completed_at: Optional[float] = None,
    ) -> bool:
        """Atomically transition queue item to FAILED with worker fencing."""
        now = time.time()
        c_at = completed_at if completed_at is not None else now
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE fabric_queue_items
                        SET status = %s,
                            error_message = %s,
                            completed_at = %s,
                            updated_at = %s
                        WHERE item_id = %s AND status IN ('CLAIMED', 'RUNNING') AND assigned_worker_id = %s
                        RETURNING item_id
                        """,
                        (QueueItemStatus.FAILED.value, error_message, c_at, now, item_id, worker_id),
                    )
                    row = cur.fetchone()
                    return row is not None
        finally:
            self._put_connection(conn)

    def _row_to_queue_item(self, row: Dict[str, Any]) -> QueueItem:
        req_caps = json.loads(row["required_capabilities_json"] or "[]")
        payload = json.loads(row["payload_json"] or "{}")
        status_val = row["status"]
        try:
            st = QueueItemStatus(status_val)
        except ValueError:
            st = QueueItemStatus.FAILED

        return QueueItem(
            item_id=row["item_id"],
            task_id=row["task_id"],
            tenant_id=row["tenant_id"],
            step_id=row["step_id"],
            priority=row["priority"],
            enqueued_at=row["enqueued_at"],
            attempt=row["attempt"],
            execution_class=row["execution_class"],
            deadline=row["deadline"],
            required_capabilities=set(req_caps),
            status=st,
            assigned_worker_id=row["assigned_worker_id"],
            lease_id=row["lease_id"],
            dispatched_at=row["dispatched_at"],
            completed_at=row["completed_at"],
            error_message=row["error_message"],
            payload=payload,
        )

    # ── Worker Registry Operations ───────────────────────────────────────────

    def upsert_worker(self, worker: WorkerRecord) -> None:
        now = time.time()
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO fabric_workers (
                            worker_id, session_id, process_id, max_concurrency, active_concurrency,
                            active_leases_json, supported_capabilities_json, status, start_time,
                            last_heartbeat, drain_state, metadata_json, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (worker_id) DO UPDATE SET
                            session_id = EXCLUDED.session_id,
                            process_id = EXCLUDED.process_id,
                            max_concurrency = EXCLUDED.max_concurrency,
                            active_concurrency = EXCLUDED.active_concurrency,
                            active_leases_json = EXCLUDED.active_leases_json,
                            supported_capabilities_json = EXCLUDED.supported_capabilities_json,
                            status = EXCLUDED.status,
                            last_heartbeat = EXCLUDED.last_heartbeat,
                            drain_state = EXCLUDED.drain_state,
                            metadata_json = EXCLUDED.metadata_json,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (
                            worker.worker_id,
                            worker.session_id,
                            worker.process_id,
                            worker.max_concurrency,
                            worker.active_concurrency,
                            json.dumps(worker.active_leases),
                            json.dumps(list(worker.supported_capabilities)),
                            worker.status.value,
                            worker.start_time,
                            worker.last_heartbeat,
                            1 if worker.drain_state else 0,
                            json.dumps(worker.metadata),
                            now,
                        ),
                    )
            self._put_connection(conn)
        except Exception:
            self._put_connection(conn, close=True)
            raise

    def get_worker(self, worker_id: str) -> Optional[WorkerRecord]:
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM fabric_workers WHERE worker_id = %s", (worker_id,))
                    row = cur.fetchone()
                    if not row:
                        return None
                    return self._row_to_worker(dict(row))
        finally:
            self._put_connection(conn)

    def list_workers(self) -> List[WorkerRecord]:
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM fabric_workers ORDER BY start_time ASC")
                    rows = cur.fetchall()
                    return [self._row_to_worker(dict(r)) for r in rows]
        finally:
            self._put_connection(conn)

    def delete_worker(self, worker_id: str) -> None:
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM fabric_workers WHERE worker_id = %s", (worker_id,))
            self._put_connection(conn)
        except Exception:
            self._put_connection(conn, close=True)
            raise

    def update_worker_capacity_atomic(
        self,
        worker_id: str,
        delta: int,
        max_concurrency: int,
        lease_id: Optional[str] = None,
        add_lease: bool = True,
    ) -> bool:
        """
        Atomically modify worker active concurrency and lease tracking using CAS.
        """
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM fabric_workers WHERE worker_id = %s FOR UPDATE", (worker_id,))
                    row = cur.fetchone()
                    if not row:
                        return False
                    curr_act = int(row["active_concurrency"])
                    new_act = curr_act + delta
                    if new_act < 0 or new_act > max_concurrency:
                        return False

                    leases = json.loads(row["active_leases_json"] or "[]")
                    if lease_id:
                        if add_lease and lease_id not in leases:
                            leases.append(lease_id)
                        elif not add_lease and lease_id in leases:
                            leases.remove(lease_id)

                    new_status = WorkerStatus.BUSY.value if new_act >= max_concurrency else (
                        WorkerStatus.DRAINING.value if bool(row["drain_state"]) else WorkerStatus.READY.value
                    )
                    now = time.time()
                    cur.execute(
                        """
                        UPDATE fabric_workers
                        SET active_concurrency = %s,
                            active_leases_json = %s,
                            status = %s,
                            updated_at = %s
                        WHERE worker_id = %s AND active_concurrency = %s
                        RETURNING worker_id
                        """,
                        (new_act, json.dumps(leases), new_status, now, worker_id, curr_act),
                    )
                    res = cur.fetchone()
                    return res is not None
        finally:
            self._put_connection(conn)

    def _row_to_worker(self, row: Dict[str, Any]) -> WorkerRecord:
        leases = json.loads(row["active_leases_json"] or "[]")
        caps = set(json.loads(row["supported_capabilities_json"] or "[]"))
        meta = json.loads(row["metadata_json"] or "{}")
        try:
            st = WorkerStatus(row["status"])
        except ValueError:
            st = WorkerStatus.READY

        return WorkerRecord(
            worker_id=row["worker_id"],
            session_id=row["session_id"],
            process_id=row["process_id"],
            max_concurrency=row["max_concurrency"],
            active_concurrency=row["active_concurrency"],
            active_leases=leases,
            supported_capabilities=caps,
            status=st,
            start_time=row["start_time"],
            last_heartbeat=row["last_heartbeat"],
            drain_state=bool(row["drain_state"]),
            metadata=meta,
        )

    # ── Scheduler Decision Audit Log ─────────────────────────────────────────

    def record_decision(self, decision: SchedulingDecision) -> None:
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO fabric_scheduler_decisions (
                            decision_id, item_id, task_id, tenant_id, step_id, worker_id, verdict, reason, timestamp
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            decision.decision_id,
                            decision.item_id,
                            decision.task_id,
                            decision.tenant_id,
                            decision.step_id,
                            decision.assigned_worker_id,
                            decision.verdict.value,
                            decision.reason,
                            decision.timestamp,
                        ),
                    )
            self._put_connection(conn)
        except Exception:
            self._put_connection(conn, close=True)
            raise

    def list_decisions(self, item_id: Optional[str] = None, task_id: Optional[str] = None, limit: int = 50) -> List[SchedulingDecision]:
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    if item_id:
                        cur.execute(
                            "SELECT * FROM fabric_scheduler_decisions WHERE item_id = %s ORDER BY timestamp DESC LIMIT %s",
                            (item_id, limit),
                        )
                    elif task_id:
                        cur.execute(
                            "SELECT * FROM fabric_scheduler_decisions WHERE task_id = %s ORDER BY timestamp DESC LIMIT %s",
                            (task_id, limit),
                        )
                    else:
                        cur.execute(
                            "SELECT * FROM fabric_scheduler_decisions ORDER BY timestamp DESC LIMIT %s",
                            (limit,),
                        )
                    rows = cur.fetchall()
                    res = []
                    for row in rows:
                        res.append(SchedulingDecision(
                            decision_id=row["decision_id"],
                            item_id=row["item_id"],
                            task_id=row["task_id"],
                            tenant_id=row["tenant_id"],
                            step_id=row["step_id"],
                            assigned_worker_id=row["worker_id"],
                            verdict=SchedulingVerdict(row["verdict"]),
                            reason=row["reason"],
                            timestamp=row["timestamp"],
                        ))
                    return res
        finally:
            self._put_connection(conn)

