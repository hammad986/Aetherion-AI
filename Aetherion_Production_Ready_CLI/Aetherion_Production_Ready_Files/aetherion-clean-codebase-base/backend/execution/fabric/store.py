"""
backend/execution/fabric/store.py — Phase 21 & Phase 22 Persistent Store for Execution Fabric.

Provides abstract BaseFabricStore interface, thread-safe SQLite implementation
(SqliteFabricStore, aliased as FabricStore), and factory helper get_fabric_store().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Set

from .models import (
    QueueItem,
    QueueItemStatus,
    SchedulingDecision,
    SchedulingVerdict,
    WorkerRecord,
    WorkerStatus,
)

from .migrations import apply_fabric_migrations

logger = logging.getLogger("aetherion.fabric.store")

_FABRIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS fabric_queue_items (
    item_id                     TEXT PRIMARY KEY,
    task_id                     TEXT NOT NULL,
    tenant_id                   TEXT NOT NULL,
    step_id                     TEXT,
    priority                    INTEGER NOT NULL DEFAULT 50,
    enqueued_at                 REAL NOT NULL,
    attempt                     INTEGER NOT NULL DEFAULT 1,
    execution_class             TEXT NOT NULL DEFAULT 'DEFAULT',
    deadline                    REAL,
    required_capabilities_json  TEXT NOT NULL DEFAULT '[]',
    status                      TEXT NOT NULL DEFAULT 'QUEUED',
    assigned_worker_id          TEXT,
    lease_id                    TEXT,
    dispatched_at               REAL,
    completed_at                REAL,
    error_message               TEXT,
    payload_json                TEXT NOT NULL DEFAULT '{}',
    updated_at                  REAL NOT NULL
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
    start_time                  REAL NOT NULL,
    last_heartbeat              REAL NOT NULL,
    drain_state                 INTEGER NOT NULL DEFAULT 0,
    metadata_json               TEXT NOT NULL DEFAULT '{}',
    updated_at                  REAL NOT NULL
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
    timestamp                   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_fabric_decisions_item
    ON fabric_scheduler_decisions (item_id, timestamp DESC);
"""


def sanitize_dsn(dsn: str) -> str:
    """Mask password in DSN strings to prevent secret leakage."""
    if not dsn:
        return ""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", str(dsn))


# ── Abstract Base Store Interface ────────────────────────────────────────────

class BaseFabricStore(ABC):
    """Abstract persistence interface for durable queue, workers, and scheduler decisions."""

    # ── Queue Operations ─────────────────────────────────────────────────────
    @abstractmethod
    def insert_queue_item(self, item: QueueItem) -> None:
        """Insert a new queue item."""

    @abstractmethod
    def get_queue_item(self, item_id: str) -> Optional[QueueItem]:
        """Fetch queue item by ID."""

    @abstractmethod
    def update_queue_item(self, item: QueueItem) -> None:
        """Update mutable fields of a queue item."""

    @abstractmethod
    def list_queue_items(
        self,
        status: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[QueueItem]:
        """List queue items filtered by status/tenant."""

    @abstractmethod
    def count_active_tenant_items(self, tenant_id: str) -> int:
        """Count active running items for a tenant."""

    @abstractmethod
    def count_active_task_items(self, task_id: str) -> int:
        """Count active running items for a task."""

    @abstractmethod
    def count_global_active_items(self) -> int:
        """Count all active items globally."""

    @abstractmethod
    def claim_item_atomic(
        self,
        item_id: str,
        worker_id: str,
        lease_id: str,
        expected_statuses: Optional[List[str]] = None,
    ) -> bool:
        """Atomically claim queue item using CAS semantics."""

    @abstractmethod
    def claim_item_for_dispatch_atomic(
        self,
        item_id: str,
        worker_id: str,
    ) -> bool:
        """Atomically transition queue item from QUEUED/REQUEUED to DISPATCHING."""

    @abstractmethod
    def release_dispatch_claim(
        self,
        item_id: str,
        worker_id: str,
    ) -> bool:
        """Rollback DISPATCHING to QUEUED if dispatch failed."""

    @abstractmethod
    def mark_running_atomic(
        self,
        item_id: str,
        worker_id: str,
    ) -> bool:
        """Atomically transition queue item from CLAIMED to RUNNING with worker fencing."""

    @abstractmethod
    def mark_completed_atomic(
        self,
        item_id: str,
        worker_id: str,
        completed_at: Optional[float] = None,
    ) -> bool:
        """Atomically transition queue item from RUNNING to COMPLETED with worker fencing."""

    @abstractmethod
    def mark_failed_atomic(
        self,
        item_id: str,
        worker_id: str,
        error_message: str,
        completed_at: Optional[float] = None,
    ) -> bool:
        """Atomically transition queue item to FAILED with worker fencing."""

    # ── Worker Registry Operations ───────────────────────────────────────────
    @abstractmethod
    def upsert_worker(self, worker: WorkerRecord) -> None:
        """Insert or update worker record."""

    @abstractmethod
    def get_worker(self, worker_id: str) -> Optional[WorkerRecord]:
        """Fetch worker record by ID."""

    @abstractmethod
    def list_workers(self) -> List[WorkerRecord]:
        """List all registered workers."""

    @abstractmethod
    def delete_worker(self, worker_id: str) -> None:
        """Remove a worker record."""

    @abstractmethod
    def update_worker_capacity_atomic(
        self,
        worker_id: str,
        delta: int,
        max_concurrency: int,
        lease_id: Optional[str] = None,
        add_lease: bool = True,
    ) -> bool:
        """Atomically increment or decrement worker active concurrency."""

    # ── Scheduler Decisions ──────────────────────────────────────────────────
    @abstractmethod
    def record_decision(self, decision: SchedulingDecision) -> None:
        """Record scheduler audit decision."""

    @abstractmethod
    def list_decisions(self, item_id: Optional[str] = None, task_id: Optional[str] = None, limit: int = 50) -> List[SchedulingDecision]:
        """List scheduler decisions."""



# ── SQLite Implementation ────────────────────────────────────────────────────

class SqliteFabricStore(BaseFabricStore):
    """Thread-safe SQLite store for durable queue, workers, and scheduler decisions."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        if db_path is None:
            db_dir = Path(__file__).resolve().parent.parent.parent / "data"
            db_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = str(db_dir / "fabric_state.db")
        else:
            self.db_path = str(db_path)
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self.get_connection()
            try:
                apply_fabric_migrations(conn, is_postgres=False)
            finally:
                conn.close()

    # ── Queue Operations ─────────────────────────────────────────────────────

    def insert_queue_item(self, item: QueueItem) -> None:
        now = time.time()
        with self._lock:
            conn = self.get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO fabric_queue_items (
                        item_id, task_id, tenant_id, step_id, priority, enqueued_at,
                        attempt, execution_class, deadline, required_capabilities_json,
                        status, assigned_worker_id, lease_id, dispatched_at, completed_at,
                        error_message, payload_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        json.dumps(item.required_capabilities),
                        item.status.value if isinstance(item.status, QueueItemStatus) else str(item.status),
                        item.assigned_worker_id,
                        item.lease_id,
                        item.dispatched_at,
                        item.completed_at,
                        item.error_message,
                        json.dumps(item.payload),
                        now,
                    ),
                )
            finally:
                conn.close()

    def get_queue_item(self, item_id: str) -> Optional[QueueItem]:
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute("SELECT * FROM fabric_queue_items WHERE item_id = ?", (item_id,))
                row = cur.fetchone()
                return self._row_to_queue_item(row) if row else None
            finally:
                conn.close()

    def update_queue_item(self, item: QueueItem) -> None:
        now = time.time()
        with self._lock:
            conn = self.get_connection()
            try:
                conn.execute(
                    """
                    UPDATE fabric_queue_items
                    SET status = ?,
                        assigned_worker_id = ?,
                        lease_id = ?,
                        attempt = ?,
                        dispatched_at = ?,
                        completed_at = ?,
                        error_message = ?,
                        updated_at = ?
                    WHERE item_id = ?
                    """,
                    (
                        item.status.value if isinstance(item.status, QueueItemStatus) else str(item.status),
                        item.assigned_worker_id,
                        item.lease_id,
                        item.attempt,
                        item.dispatched_at,
                        item.completed_at,
                        item.error_message,
                        now,
                        item.item_id,
                    ),
                )
            finally:
                conn.close()

    def list_queue_items(
        self,
        status: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[QueueItem]:
        with self._lock:
            conn = self.get_connection()
            try:
                query = "SELECT * FROM fabric_queue_items WHERE 1=1"
                params: List[Any] = []
                if status:
                    query += " AND status = ?"
                    params.append(status)
                if tenant_id:
                    query += " AND tenant_id = ?"
                    params.append(tenant_id)
                query += " ORDER BY priority DESC, enqueued_at ASC LIMIT ?"
                params.append(limit)

                cur = conn.execute(query, tuple(params))
                return [self._row_to_queue_item(row) for row in cur.fetchall()]
            finally:
                conn.close()

    def count_active_tenant_items(self, tenant_id: str) -> int:
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute(
                    """
                    SELECT COUNT(*) as cnt FROM fabric_queue_items
                    WHERE tenant_id = ? AND status IN ('DISPATCHING', 'CLAIMED', 'RUNNING')
                    """,
                    (tenant_id,),
                )
                row = cur.fetchone()
                return int(row["cnt"]) if row else 0
            finally:
                conn.close()

    def count_active_task_items(self, task_id: str) -> int:
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute(
                    """
                    SELECT COUNT(*) as cnt FROM fabric_queue_items
                    WHERE task_id = ? AND status IN ('DISPATCHING', 'CLAIMED', 'RUNNING')
                    """,
                    (task_id,),
                )
                row = cur.fetchone()
                return int(row["cnt"]) if row else 0
            finally:
                conn.close()

    def count_global_active_items(self) -> int:
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute(
                    """
                    SELECT COUNT(*) as cnt FROM fabric_queue_items
                    WHERE status IN ('DISPATCHING', 'CLAIMED', 'RUNNING')
                    """
                )
                row = cur.fetchone()
                return int(row["cnt"]) if row else 0
            finally:
                conn.close()

    def claim_item_atomic(
        self,
        item_id: str,
        worker_id: str,
        lease_id: str,
        expected_statuses: Optional[List[str]] = None,
    ) -> bool:
        """
        Atomically transition an item to CLAIMED status.
        Only succeeds if current status is in expected_statuses (default: QUEUED, REQUEUED, DISPATCHING).
        Returns True if claimed, False on CAS conflict.
        """
        now = time.time()
        statuses = expected_statuses or [
            QueueItemStatus.QUEUED.value,
            QueueItemStatus.REQUEUED.value,
            QueueItemStatus.DISPATCHING.value,
        ]
        placeholders = ",".join("?" for _ in statuses)
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute(
                    f"""
                    UPDATE fabric_queue_items
                    SET status = ?,
                        assigned_worker_id = ?,
                        lease_id = ?,
                        updated_at = ?
                    WHERE item_id = ? AND status IN ({placeholders})
                    """,
                    (QueueItemStatus.CLAIMED.value, worker_id, lease_id, now, item_id, *statuses),
                )
                return cur.rowcount > 0
            finally:
                conn.close()

    def claim_item_for_dispatch_atomic(
        self,
        item_id: str,
        worker_id: str,
    ) -> bool:
        """
        Atomically transition an item to DISPATCHING status using CAS.
        Returns True if claimed for dispatch, False if another worker/scheduler won.
        """
        now = time.time()
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute(
                    """
                    UPDATE fabric_queue_items
                    SET status = ?,
                        assigned_worker_id = ?,
                        dispatched_at = ?,
                        updated_at = ?
                    WHERE item_id = ? AND status IN ('QUEUED', 'REQUEUED')
                    """,
                    (QueueItemStatus.DISPATCHING.value, worker_id, now, now, item_id),
                )
                return cur.rowcount > 0
            finally:
                conn.close()

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
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute(
                    """
                    UPDATE fabric_queue_items
                    SET status = ?,
                        assigned_worker_id = NULL,
                        dispatched_at = NULL,
                        updated_at = ?
                    WHERE item_id = ? AND status = ? AND assigned_worker_id = ?
                    """,
                    (QueueItemStatus.QUEUED.value, now, item_id, QueueItemStatus.DISPATCHING.value, worker_id),
                )
                return cur.rowcount > 0
            finally:
                conn.close()

    def mark_running_atomic(
        self,
        item_id: str,
        worker_id: str,
    ) -> bool:
        """Atomically transition queue item from CLAIMED to RUNNING with worker fencing."""
        now = time.time()
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute(
                    """
                    UPDATE fabric_queue_items
                    SET status = ?,
                        updated_at = ?
                    WHERE item_id = ? AND status = ? AND assigned_worker_id = ?
                    """,
                    (QueueItemStatus.RUNNING.value, now, item_id, QueueItemStatus.CLAIMED.value, worker_id),
                )
                return cur.rowcount > 0
            finally:
                conn.close()

    def mark_completed_atomic(
        self,
        item_id: str,
        worker_id: str,
        completed_at: Optional[float] = None,
    ) -> bool:
        """Atomically transition queue item from RUNNING to COMPLETED with worker fencing."""
        now = time.time()
        c_at = completed_at if completed_at is not None else now
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute(
                    """
                    UPDATE fabric_queue_items
                    SET status = ?,
                        completed_at = ?,
                        updated_at = ?
                    WHERE item_id = ? AND status = ? AND assigned_worker_id = ?
                    """,
                    (QueueItemStatus.COMPLETED.value, c_at, now, item_id, QueueItemStatus.RUNNING.value, worker_id),
                )
                return cur.rowcount > 0
            finally:
                conn.close()

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
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute(
                    """
                    UPDATE fabric_queue_items
                    SET status = ?,
                        error_message = ?,
                        completed_at = ?,
                        updated_at = ?
                    WHERE item_id = ? AND status IN ('CLAIMED', 'RUNNING') AND assigned_worker_id = ?
                    """,
                    (QueueItemStatus.FAILED.value, error_message, c_at, now, item_id, worker_id),
                )
                return cur.rowcount > 0
            finally:
                conn.close()

    def _row_to_queue_item(self, row: sqlite3.Row) -> QueueItem:
        req_caps = json.loads(row["required_capabilities_json"] or "[]")
        payload = json.loads(row["payload_json"] or "{}")
        status_val = row["status"]
        try:
            st = QueueItemStatus(status_val)
        except ValueError:
            st = QueueItemStatus.QUEUED

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
            required_capabilities=req_caps,
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
        with self._lock:
            conn = self.get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO fabric_workers (
                        worker_id, session_id, process_id, max_concurrency, active_concurrency,
                        active_leases_json, supported_capabilities_json, status, start_time,
                        last_heartbeat, drain_state, metadata_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(worker_id) DO UPDATE SET
                        session_id = excluded.session_id,
                        process_id = excluded.process_id,
                        max_concurrency = excluded.max_concurrency,
                        active_concurrency = excluded.active_concurrency,
                        active_leases_json = excluded.active_leases_json,
                        supported_capabilities_json = excluded.supported_capabilities_json,
                        status = excluded.status,
                        last_heartbeat = excluded.last_heartbeat,
                        drain_state = excluded.drain_state,
                        metadata_json = excluded.metadata_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        worker.worker_id,
                        worker.session_id,
                        worker.process_id,
                        worker.max_concurrency,
                        worker.active_concurrency,
                        json.dumps(worker.active_leases),
                        json.dumps(list(worker.supported_capabilities)),
                        worker.status.value if isinstance(worker.status, WorkerStatus) else str(worker.status),
                        worker.start_time,
                        worker.last_heartbeat,
                        1 if worker.drain_state else 0,
                        json.dumps(worker.metadata),
                        now,
                    ),
                )
            finally:
                conn.close()

    def get_worker(self, worker_id: str) -> Optional[WorkerRecord]:
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute("SELECT * FROM fabric_workers WHERE worker_id = ?", (worker_id,))
                row = cur.fetchone()
                return self._row_to_worker(row) if row else None
            finally:
                conn.close()

    def list_workers(self) -> List[WorkerRecord]:
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute("SELECT * FROM fabric_workers ORDER BY start_time ASC")
                return [self._row_to_worker(row) for row in cur.fetchall()]
            finally:
                conn.close()

    def delete_worker(self, worker_id: str) -> None:
        with self._lock:
            conn = self.get_connection()
            try:
                conn.execute("DELETE FROM fabric_workers WHERE worker_id = ?", (worker_id,))
            finally:
                conn.close()

    def update_worker_capacity_atomic(
        self,
        worker_id: str,
        delta: int,
        max_concurrency: int,
        lease_id: Optional[str] = None,
        add_lease: bool = True,
    ) -> bool:
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute("SELECT * FROM fabric_workers WHERE worker_id = ?", (worker_id,))
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
                cur_up = conn.execute(
                    """
                    UPDATE fabric_workers
                    SET active_concurrency = ?,
                        active_leases_json = ?,
                        status = ?,
                        updated_at = ?
                    WHERE worker_id = ? AND active_concurrency = ?
                    """,
                    (new_act, json.dumps(leases), new_status, now, worker_id, curr_act),
                )
                return cur_up.rowcount > 0
            finally:
                conn.close()

    def _row_to_worker(self, row: sqlite3.Row) -> WorkerRecord:
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
        with self._lock:
            conn = self.get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO fabric_scheduler_decisions (
                        decision_id, item_id, task_id, tenant_id, step_id, worker_id, verdict, reason, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            finally:
                conn.close()

    def list_decisions(self, item_id: Optional[str] = None, task_id: Optional[str] = None, limit: int = 50) -> List[SchedulingDecision]:
        with self._lock:
            conn = self.get_connection()
            try:
                if item_id:
                    cur = conn.execute(
                        "SELECT * FROM fabric_scheduler_decisions WHERE item_id = ? ORDER BY timestamp DESC LIMIT ?",
                        (item_id, limit),
                    )
                elif task_id:
                    cur = conn.execute(
                        "SELECT * FROM fabric_scheduler_decisions WHERE task_id = ? ORDER BY timestamp DESC LIMIT ?",
                        (task_id, limit),
                    )
                else:
                    cur = conn.execute(
                        "SELECT * FROM fabric_scheduler_decisions ORDER BY timestamp DESC LIMIT ?",
                        (limit,),
                    )
                res = []
                for row in cur.fetchall():
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
                conn.close()



# Backward-compatible alias
FabricStore = SqliteFabricStore


# ── Factory Helper ───────────────────────────────────────────────────────────

def get_fabric_store(
    db_path: Optional[str | Path] = None,
    dsn: Optional[str] = None,
) -> BaseFabricStore:
    """
    Authoritative factory for Execution Fabric stores:
    - Production and staging environments require PostgreSQL and fail closed.
    - If DATABASE_URL or dsn is configured with postgresql:// or postgres://, PostgresFabricStore is used.
    - SQLite is used for local development and testing.
    """
    env_dsn = dsn or os.environ.get("DATABASE_URL", "").strip()
    is_pg = env_dsn.startswith(("postgres://", "postgresql://"))

    # Check fail-closed production / staging
    try:
        from core.environment import is_fail_closed_environment
        fail_closed = is_fail_closed_environment()
    except ImportError:
        env = os.environ.get("ENVIRONMENT", os.environ.get("FLASK_ENV", "")).strip().lower()
        fail_closed = env in ("production", "staging")

    if fail_closed:
        if not is_pg:
            raise RuntimeError(
                "Production and staging environments require PostgreSQL and fail closed. "
                "DATABASE_URL must be configured with a valid postgresql:// or postgres:// URI."
            )
        from .postgres_store import PostgresFabricStore
        try:
            return PostgresFabricStore(dsn=env_dsn)
        except Exception as exc:
            logger.error("Failed to initialize PostgresFabricStore in production: %s", sanitize_dsn(str(exc)))
            raise RuntimeError(f"Production PostgreSQL store initialization failed: {sanitize_dsn(str(exc))}") from exc

    # In dev/test: use Postgres if requested, otherwise SQLite
    if is_pg:
        from .postgres_store import PostgresFabricStore
        try:
            return PostgresFabricStore(dsn=env_dsn)
        except Exception as exc:
            logger.warning("PostgresFabricStore init failed (%s); falling back to SQLite in dev/test.", sanitize_dsn(str(exc)))
            return SqliteFabricStore(db_path=db_path)

    return SqliteFabricStore(db_path=db_path)
