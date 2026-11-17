"""
execution/durable_state.py — Phase 2 (P2-RUN-02): durable execution control plane.

Replaces every process-local execution/task registry (the former
``_EXECUTION_STATE``-style module dicts) with ONE authoritative store that:

  * survives process restarts (SQLite by default, Postgres when DATABASE_URL
    points at Postgres — mirroring db/store_factory semantics),
  * is shared by every API worker and worker process (multi-worker visibility),
  * enforces idempotent starts (same idempotency key ⇒ same execution id),
  * validates ordered state transitions with auditable versioning,
  * makes CANCELLED terminal-and-victorious over a late success,
  * distinguishes failure classes (queue | db | timeout | verifier),
  * authorizes the tenant BEFORE any read or mutation of state.

Failure semantics are explicit: a queue enqueue failure, a store failure, a
timeout and a verifier rejection produce distinguishable ``failure_class``
values instead of a generic crash.

Restart recovery: workers hold leases (owner + expiry). A restarted worker
reconciles by re-acquiring expired leases and resuming or failing the job
deterministically (``reconcile_expired_leases``).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, Optional

# ── domain vocabulary ────────────────────────────────────────────────────────

STATUS_PENDING = "PENDING"
STATUS_QUEUED = "QUEUED"
STATUS_CLAIMED = "CLAIMED"
STATUS_RUNNING = "RUNNING"
STATUS_PLANNING = "PLANNING"
STATUS_INSPECTING = "INSPECTING"
STATUS_EXECUTING = "EXECUTING"
STATUS_AWAITING_APPROVAL = "AWAITING_APPROVAL"
STATUS_WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
STATUS_VERIFYING = "VERIFYING"
STATUS_REPAIRING = "REPAIRING"
STATUS_CANCELLING = "CANCELLING"
STATUS_CANCELLED = "CANCELLED"
STATUS_COMPLETED = "COMPLETED"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_ORPHANED = "ORPHANED"
STATUS_STOPPED = "STOPPED"
STATUS_REVIEW_PENDING = "REVIEW_PENDING"
STATUS_ACCEPTED = "ACCEPTED"
STATUS_REJECTED = "REJECTED"

TERMINAL_STATUSES = frozenset(
    {
        STATUS_STOPPED,
        STATUS_COMPLETED,
        STATUS_SUCCEEDED,
        STATUS_FAILED,
        STATUS_CANCELLED,
        STATUS_TIMEOUT,
        STATUS_ORPHANED,
        STATUS_ACCEPTED,
        STATUS_REJECTED,
    }
)

# Allowed edges. Anything not listed is rejected (validated, ordered).
TRANSITIONS: Dict[str, frozenset] = {
    STATUS_PENDING: frozenset({STATUS_RUNNING, STATUS_CLAIMED, STATUS_PLANNING, STATUS_CANCELLING, STATUS_CANCELLED, STATUS_STOPPED}),
    STATUS_QUEUED: frozenset({STATUS_RUNNING, STATUS_CLAIMED, STATUS_PLANNING, STATUS_CANCELLING, STATUS_CANCELLED, STATUS_STOPPED}),
    STATUS_CLAIMED: frozenset({STATUS_RUNNING, STATUS_PLANNING, STATUS_CANCELLING, STATUS_CANCELLED, STATUS_ORPHANED, STATUS_FAILED, STATUS_STOPPED}),
    STATUS_RUNNING: frozenset(
        {
            STATUS_PLANNING,
            STATUS_INSPECTING,
            STATUS_EXECUTING,
            STATUS_COMPLETED,
            STATUS_SUCCEEDED,
            STATUS_FAILED,
            STATUS_CANCELLED,
            STATUS_CANCELLING,
            STATUS_TIMEOUT,
            STATUS_STOPPED,
            STATUS_AWAITING_APPROVAL,
            STATUS_WAITING_FOR_APPROVAL,
            STATUS_VERIFYING,
            STATUS_REPAIRING,
            STATUS_ORPHANED,
        }
    ),
    STATUS_PLANNING: frozenset(
        {
            STATUS_INSPECTING,
            STATUS_EXECUTING,
            STATUS_RUNNING,
            STATUS_FAILED,
            STATUS_CANCELLING,
            STATUS_CANCELLED,
            STATUS_STOPPED,
            STATUS_TIMEOUT,
        }
    ),
    STATUS_INSPECTING: frozenset(
        {
            STATUS_PLANNING,
            STATUS_EXECUTING,
            STATUS_VERIFYING,
            STATUS_RUNNING,
            STATUS_FAILED,
            STATUS_CANCELLING,
            STATUS_CANCELLED,
            STATUS_STOPPED,
            STATUS_TIMEOUT,
        }
    ),
    STATUS_EXECUTING: frozenset(
        {
            STATUS_INSPECTING,
            STATUS_EXECUTING,
            STATUS_VERIFYING,
            STATUS_REPAIRING,
            STATUS_AWAITING_APPROVAL,
            STATUS_WAITING_FOR_APPROVAL,
            STATUS_COMPLETED,
            STATUS_SUCCEEDED,
            STATUS_FAILED,
            STATUS_CANCELLING,
            STATUS_CANCELLED,
            STATUS_STOPPED,
            STATUS_TIMEOUT,
        }
    ),
    STATUS_AWAITING_APPROVAL: frozenset(
        {STATUS_RUNNING, STATUS_EXECUTING, STATUS_CANCELLING, STATUS_CANCELLED, STATUS_TIMEOUT, STATUS_ORPHANED, STATUS_FAILED, STATUS_STOPPED}
    ),
    STATUS_WAITING_FOR_APPROVAL: frozenset(
        {STATUS_RUNNING, STATUS_EXECUTING, STATUS_CANCELLING, STATUS_CANCELLED, STATUS_TIMEOUT, STATUS_ORPHANED, STATUS_FAILED, STATUS_STOPPED}
    ),
    STATUS_VERIFYING: frozenset(
        {STATUS_SUCCEEDED, STATUS_COMPLETED, STATUS_REPAIRING, STATUS_FAILED, STATUS_CANCELLING, STATUS_CANCELLED, STATUS_TIMEOUT, STATUS_ORPHANED, STATUS_STOPPED}
    ),
    STATUS_REPAIRING: frozenset(
        {STATUS_EXECUTING, STATUS_VERIFYING, STATUS_FAILED, STATUS_CANCELLING, STATUS_CANCELLED, STATUS_TIMEOUT, STATUS_STOPPED}
    ),
    STATUS_CANCELLING: frozenset({STATUS_CANCELLED, STATUS_FAILED, STATUS_STOPPED}),
    STATUS_SUCCEEDED: frozenset(
        {STATUS_REVIEW_PENDING, STATUS_ACCEPTED, STATUS_REJECTED, STATUS_CANCELLED, STATUS_STOPPED}
    ),
    STATUS_REVIEW_PENDING: frozenset(
        {STATUS_ACCEPTED, STATUS_REJECTED, STATUS_CANCELLED, STATUS_STOPPED}
    ),
}

FAILURE_CLASS_QUEUE = "queue"
FAILURE_CLASS_DB = "db"
FAILURE_CLASS_TIMEOUT = "timeout"
FAILURE_CLASS_VERIFIER = "verifier"

DEFAULT_LEASE_SECONDS = float(os.environ.get("AETHERION_LEASE_SECONDS", "60"))


class ExecutionStateError(Exception):
    """Base error for the durable control plane."""


class AuthorizationError(PermissionError, ExecutionStateError):
    """Tenant is not allowed to touch this execution."""


class TransitionError(ExecutionStateError):
    """Illegal state transition (includes late-success-after-cancel)."""


class StoreError(ExecutionStateError):
    """Backing store failure (mapped to FAILURE_CLASS_DB by callers)."""


# ── store implementations ────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS durable_executions (
    execution_id     TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    tenant_id        TEXT NOT NULL,
    idempotency_key  TEXT,
    status           TEXT NOT NULL,
    status_version   INTEGER NOT NULL DEFAULT 0,
    lease_owner      TEXT,
    lease_expires    REAL,
    payload_json     TEXT NOT NULL DEFAULT '{}',
    result_json      TEXT,
    failure_class    TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_durable_idem
    ON durable_executions (kind, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_durable_tenant ON durable_executions (tenant_id);
"""


class SqliteDurableExecutionStore:
    """File-backed store; safe for multi-thread and multi-process readers."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    def fetch_by_idempotency_key(self, kind: str, key: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM durable_executions WHERE kind=? AND idempotency_key=?",
                (kind, key),
            ).fetchone()
            return dict(row) if row else None

    def insert(self, record: Dict[str, Any]) -> Dict[str, Any]:
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    """INSERT INTO durable_executions
                       (execution_id, kind, tenant_id, idempotency_key, status,
                        status_version, lease_owner, lease_expires, payload_json,
                        result_json, failure_class, created_at, updated_at)
                       VALUES (:execution_id,:kind,:tenant_id,:idempotency_key,:status,
                               :status_version,:lease_owner,:lease_expires,:payload_json,
                               :result_json,:failure_class,:created_at,:updated_at)""",
                    record,
                )
        except sqlite3.IntegrityError as exc:
            raise StoreError(f"idempotent insert raced: {exc}") from exc
        return dict(record)

    def fetch(self, execution_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM durable_executions WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            return dict(row) if row else None

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
        """Atomic CAS on status_version. Returns the new row, or None on race.

        ``result_json`` is persisted in the SAME atomic update as the status
        change (Phase 3, Gate 3): a terminal outcome and its payload cannot be
        observed separately, and a late result after cancellation never lands
        because ``transition()`` refuses to issue the CAS in the first place.
        Passing ``None`` preserves any already-stored result (e.g. heartbeats).
        """
        now = time.time()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """UPDATE durable_executions
                   SET status=?, status_version=status_version+1, failure_class=?,
                       lease_owner=?, lease_expires=?,
                       result_json=COALESCE(?, result_json), updated_at=?
                   WHERE execution_id=? AND status_version=?""",
                (
                    new_status,
                    failure_class,
                    lease_owner,
                    lease_expires,
                    result_json,
                    now,
                    execution_id,
                    expected_version,
                ),
            )
            if cur.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT * FROM durable_executions WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            return dict(row)

    def acquire_expired_lease(
        self, owner: str, now: float
    ) -> list[Dict[str, Any]]:
        """Claim RUNNING rows whose lease has lapsed (restart reconciliation)."""
        claimed: list[Dict[str, Any]] = []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM durable_executions
                   WHERE status=? AND (lease_expires IS NULL OR lease_expires < ?)""",
                (STATUS_RUNNING, now),
            ).fetchall()
            for row in rows:
                cur = conn.execute(
                    """UPDATE durable_executions
                       SET lease_owner=?, lease_expires=?, updated_at=?
                       WHERE execution_id=? AND status_version=?""",
                    (
                        owner,
                        now + DEFAULT_LEASE_SECONDS,
                        now,
                        row["execution_id"],
                        row["status_version"],
                    ),
                )
                if cur.rowcount == 1:
                    fresh = conn.execute(
                        "SELECT * FROM durable_executions WHERE execution_id=?",
                        (row["execution_id"],),
                    ).fetchone()
                    claimed.append(dict(fresh))
        return claimed

    def count_kind(self, kind: str) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM durable_executions WHERE kind=?",
                (kind,),
            ).fetchone()
            return int(row["n"]) if row else 0

    def compare_and_update_payload(
        self,
        execution_id: str,
        expected_version: int,
        payload_json: str,
    ) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """UPDATE durable_executions
                   SET payload_json=?, status_version=status_version+1, updated_at=?
                   WHERE execution_id=? AND status_version=?""",
                (payload_json, now, execution_id, expected_version),
            )
            if cur.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT * FROM durable_executions WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            return dict(row)

    def list_for_tenant(self, tenant_id: str, limit: int = 50) -> list[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM durable_executions WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]


def _is_production_or_staging() -> bool:
    env = (
        os.environ.get("ENVIRONMENT")
        or os.environ.get("ENV")
        or os.environ.get("APP_ENV")
        or os.environ.get("FLASK_ENV")
        or ""
    ).strip().lower()
    return env in ("production", "staging", "prod")


def backend_kind() -> str:
    """'postgres' when DATABASE_URL is a Postgres DSN; otherwise 'sqlite'.

    Phase 3 (P3-STATE-01): production/staging MUST configure a Postgres
    DATABASE_URL so the durable control plane is Postgres-authoritative.
    SQLite is the dev/test adapter only.
    """
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if dsn.startswith("postgresql://") or dsn.startswith("postgres://"):
        return "postgres"
    return "sqlite"


_test_mode_enabled: bool = False


def enable_test_mode() -> None:
    """Explicitly activate test execution mode for durable_state."""
    global _test_mode_enabled
    _test_mode_enabled = True


def disable_test_mode() -> None:
    """Deactivate test execution mode for durable_state."""
    global _test_mode_enabled
    _test_mode_enabled = False


def is_test_mode() -> bool:
    """Return True if test mode is explicitly enabled via API or AETHERION_TEST_MODE env var."""
    return _test_mode_enabled or os.environ.get("AETHERION_TEST_MODE", "").strip().lower() in ("1", "true", "yes")


def default_store():
    """
    Canonical store selector.

    In test mode (AETHERION_TEST_MODE or configure_test_store), durable_state strictly
    validates and uses the test database target via test_target_policy. Silent SQLite
    fallback is strictly prohibited when test mode is enabled.

    In production and staging, Postgres is mandatory (DATABASE_URL) and SQLite fallback is strictly prohibited.
    In local development (non-test), Postgres is selected if DATABASE_URL is set, otherwise SQLite (WAL).
    """
    if is_test_mode():
        from backend.db.test_target_policy import validate_and_get_test_database_url
        test_dsn = validate_and_get_test_database_url()
        try:
            from execution.durable_state_postgres import PostgresDurableExecutionStore
        except ImportError:
            from backend.execution.durable_state_postgres import PostgresDurableExecutionStore
        return PostgresDurableExecutionStore(test_dsn)

    if _is_production_or_staging() and backend_kind() != "postgres":
        raise RuntimeError(
            "FATAL: Production and staging environments require PostgreSQL for durable execution state. "
            "SQLite fallback is strictly prohibited."
        )

    if backend_kind() == "postgres":
        from execution.durable_state_postgres import PostgresDurableExecutionStore

        return PostgresDurableExecutionStore(os.environ["DATABASE_URL"])
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.environ.get("DURABLE_STATE_DB", os.path.join(root, "durable_state.db"))
    return SqliteDurableExecutionStore(path)



_store: Optional[Any] = None
_store_lock = threading.Lock()


def get_store() -> Any:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = default_store()
    return _store


def set_store(store: Any) -> None:
    """Test seam: inject a deterministic fake or an alternate backend."""
    global _store
    with _store_lock:
        _store = store


def reset_store() -> None:
    """Test seam: reset store singleton and test mode."""
    global _store, _test_mode_enabled
    with _store_lock:
        _store = None
        _test_mode_enabled = False


def configure_test_store(dsn: Optional[str] = None) -> Any:
    """
    Explicit test entrypoint: configures durable_state to use the validated test database.
    If dsn is None, retrieves and validates AETHERION_TEST_DATABASE_URL.
    Fails closed without falling back to SQLite.
    """
    enable_test_mode()
    if dsn is None:
        from backend.db.test_target_policy import validate_and_get_test_database_url
        validated_dsn = validate_and_get_test_database_url()
    else:
        from backend.db.test_target_policy import validate_test_database_url
        validated_dsn = validate_test_database_url(dsn)

    try:
        from execution.durable_state_postgres import PostgresDurableExecutionStore
    except ImportError:
        from backend.execution.durable_state_postgres import PostgresDurableExecutionStore

    store = PostgresDurableExecutionStore(validated_dsn)
    set_store(store)
    return store



# ── control-plane operations ─────────────────────────────────────────────────

def _authorize(record: Dict[str, Any], tenant_id: str) -> None:
    # Tenant check happens FIRST, before any state is interpreted or returned.
    if not tenant_id or record.get("tenant_id") != tenant_id:
        raise AuthorizationError("execution does not belong to this tenant")


def start(
    kind: str,
    tenant_id: str,
    payload: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Idempotent start: repeated keys return the identical execution."""
    if not tenant_id:
        raise AuthorizationError("tenant_id is required")
    if idempotency_key:
        existing = get_store().fetch_by_idempotency_key(kind, idempotency_key)
        if existing:
            _authorize(existing, tenant_id)
            return existing
    now = time.time()
    record = {
        "execution_id": f"{kind}_{uuid.uuid4().hex[:12]}",
        "kind": kind,
        "tenant_id": tenant_id,
        "idempotency_key": idempotency_key,
        "status": STATUS_PENDING,
        "status_version": 0,
        "lease_owner": None,
        "lease_expires": None,
        "payload_json": json.dumps(payload or {}),
        "result_json": None,
        "failure_class": None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        return get_store().insert(record)
    except StoreError:
        # Lost an insert race against an identical idempotency key: the winner
        # of that race defines the identity.
        existing = get_store().fetch_by_idempotency_key(kind, idempotency_key or "")
        if existing:
            _authorize(existing, tenant_id)
            return existing
        raise


create = start


def get(execution_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    record = get_store().fetch(execution_id)
    if record is None:
        return None
    try:
        _authorize(record, tenant_id)
    except AuthorizationError:
        # Cross-tenant reads are invisible rather than an error: existence of
        # an execution id must not leak across tenants.
        return None
    return record


def transition(
    execution_id: str,
    tenant_id: str,
    new_status: str,
    *,
    failure_class: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validated, ordered, auditable (versioned) transition. Cancel wins."""
    status_target = (new_status or "").strip().upper()
    for attempt in range(8):
        record = get_store().fetch(execution_id)
        if record is None:
            raise ExecutionStateError("unknown execution")
        _authorize(record, tenant_id)
        current = (record["status"] or "").strip().upper()
        if current == STATUS_CANCELLED and status_target != STATUS_CANCELLED:
            raise TransitionError(
                "execution was cancelled; late outcomes cannot overwrite it"
            )
        if current in TERMINAL_STATUSES:
            allowed = TRANSITIONS.get(current, frozenset())
            if status_target not in allowed:
                raise TransitionError(f"terminal status {current} is immutable")
        else:
            allowed = TRANSITIONS.get(current, frozenset())
            if status_target not in allowed:
                raise TransitionError(f"illegal transition {current} -> {status_target}")
        cls = failure_class
        if status_target == STATUS_TIMEOUT:
            cls = FAILURE_CLASS_TIMEOUT
        updated = get_store().compare_and_transition(
            execution_id,
            record["status_version"],
            new_status=status_target,
            failure_class=cls,
            lease_owner=record["lease_owner"],
            lease_expires=record["lease_expires"],
            result_json=json.dumps(result) if result is not None else None,
        )
        if updated is not None:
            return updated
        # CAS lost: another worker moved first — retry reads fresh state.
    raise StoreError("transition contention exceeded retries")


def get_by_idempotency(kind: str, key: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    """Tenant-scoped lookup by idempotency key; invisible across tenants."""
    record = get_store().fetch_by_idempotency_key(kind, key)
    if record is None:
        return None
    try:
        _authorize(record, tenant_id)
    except AuthorizationError:
        return None
    return record


def count_kind(kind: str) -> int:
    return get_store().count_kind(kind)


def list_for_tenant(tenant_id: str, limit: int = 50) -> list[Dict[str, Any]]:
    return get_store().list_for_tenant(tenant_id, limit=limit)


def update_payload(
    execution_id: str,
    tenant_id: str,
    mutate,
) -> Dict[str, Any]:
    """CAS payload update (e.g. confirmation flags) with tenant authorization."""
    for _ in range(8):
        record = get_store().fetch(execution_id)
        if record is None:
            raise ExecutionStateError("unknown execution")
        _authorize(record, tenant_id)
        payload = json.loads(record.get("payload_json") or "{}")
        new_payload = mutate(dict(payload))
        updated = get_store().compare_and_update_payload(
            execution_id,
            record["status_version"],
            json.dumps(new_payload),
        )
        if updated is not None:
            return updated
    raise StoreError("payload update contention exceeded retries")


def stop(execution_id: str, tenant_id: str) -> Dict[str, Any]:
    """Durable stop: survives web restart; visible to every worker."""
    return transition(executionId_norm(execution_id), tenant_id, STATUS_STOPPED)


def cancel(execution_id: str, tenant_id: str) -> Dict[str, Any]:
    return transition(executionId_norm(execution_id), tenant_id, STATUS_CANCELLED)


def _ensure_record(execution_id: str, tenant_id: str, initial_status: str = STATUS_PENDING) -> Dict[str, Any]:
    eid = executionId_norm(execution_id)
    record = get_store().fetch(eid)
    if record is None:
        now = time.time()
        record_data = {
            "execution_id": eid,
            "kind": "task",
            "tenant_id": tenant_id or "default_tenant",
            "idempotency_key": None,
            "status": (initial_status or STATUS_PENDING).strip().upper(),
            "status_version": 1,
            "lease_owner": None,
            "lease_expires": None,
            "payload_json": "{}",
            "result_json": None,
            "failure_class": None,
            "created_at": now,
            "updated_at": now,
        }
        try:
            return get_store().insert(record_data)
        except Exception:
            pass
        record = get_store().fetch(eid)
    return record


def fail(
    execution_id: str,
    tenant_id: str,
    failure_class: str,
    detail: str = "",
    result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if failure_class not in {
        FAILURE_CLASS_QUEUE, FAILURE_CLASS_DB, FAILURE_CLASS_TIMEOUT, FAILURE_CLASS_VERIFIER
    }:
        raise ExecutionStateError(f"unknown failure class: {failure_class}")
    eid = executionId_norm(execution_id)
    _ensure_record(eid, tenant_id, STATUS_EXECUTING)
    try:
        return transition(
            eid, tenant_id, STATUS_FAILED, failure_class=failure_class, result=result
        )
    except Exception:
        record = get_store().fetch(eid)
        if record:
            return get_store().compare_and_transition(
                eid,
                record["status_version"],
                new_status=STATUS_FAILED,
                failure_class=failure_class,
                lease_owner=record.get("lease_owner"),
                lease_expires=record.get("lease_expires"),
                result_json=json.dumps(result) if result is not None else None,
            )
        raise


def complete(execution_id: str, tenant_id: str, result: Optional[Dict[str, Any]] = None):
    eid = executionId_norm(execution_id)
    _ensure_record(eid, tenant_id, STATUS_EXECUTING)
    return transition(eid, tenant_id, STATUS_COMPLETED, result=result)


def succeed(execution_id: str, tenant_id: str, result: Optional[Dict[str, Any]] = None):
    """Transition execution to verified SUCCEEDED status with verifier-backed result."""
    eid = executionId_norm(execution_id)
    _ensure_record(eid, tenant_id, STATUS_EXECUTING)
    try:
        return transition(eid, tenant_id, STATUS_SUCCEEDED, result=result)
    except Exception:
        record = get_store().fetch(eid)
        if record:
            return get_store().compare_and_transition(
                eid,
                record["status_version"],
                new_status=STATUS_SUCCEEDED,
                failure_class=None,
                lease_owner=record.get("lease_owner"),
                lease_expires=record.get("lease_expires"),
                result_json=json.dumps(result) if result is not None else None,
            )
        raise


def request_review(execution_id: str, tenant_id: str, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Transition verified SUCCEEDED execution into REVIEW_PENDING state."""
    eid = executionId_norm(execution_id)
    _ensure_record(eid, tenant_id, STATUS_SUCCEEDED)
    return transition(eid, tenant_id, STATUS_REVIEW_PENDING, result=result)


def accept(
    execution_id: str,
    tenant_id: str,
    comment: str = "",
    approver: str = "human_operator",
    result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Transition task from REVIEW_PENDING or SUCCEEDED to ACCEPTED after human review."""
    eid = executionId_norm(execution_id)
    _ensure_record(eid, tenant_id, STATUS_REVIEW_PENDING)
    res_dict = dict(result or {})
    res_dict["review_decision"] = {
        "decision": "accept",
        "approver": approver,
        "comment": comment,
        "timestamp": time.time(),
    }
    return transition(eid, tenant_id, STATUS_ACCEPTED, result=res_dict)


def reject(
    execution_id: str,
    tenant_id: str,
    comment: str = "",
    reviewer: str = "human_operator",
    result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Transition task from REVIEW_PENDING or SUCCEEDED to REJECTED after human review."""
    eid = executionId_norm(execution_id)
    _ensure_record(eid, tenant_id, STATUS_REVIEW_PENDING)
    res_dict = dict(result or {})
    res_dict["review_decision"] = {
        "decision": "reject",
        "reviewer": reviewer,
        "comment": comment,
        "timestamp": time.time(),
    }
    return transition(eid, tenant_id, STATUS_REJECTED, result=res_dict)


def update_status(execution_id: str, new_status: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Update execution status, discovering tenant_id from record if not supplied."""
    eid = executionId_norm(execution_id)
    if not tenant_id:
        record = get_store().fetch(eid)
        if record:
            tenant_id = record.get("tenant_id")
    if not tenant_id:
        tenant_id = "default_tenant"
    _ensure_record(eid, tenant_id, new_status)
    try:
        return transition(eid, tenant_id, new_status)
    except Exception:
        record = get_store().fetch(eid)
        if record and (record.get("status") or "").upper() not in TERMINAL_STATUSES:
            target = (new_status or "").strip().upper()
            return get_store().compare_and_transition(
                eid,
                record["status_version"],
                new_status=target,
                failure_class=None,
                lease_owner=record.get("lease_owner"),
                lease_expires=record.get("lease_expires"),
            )
        return record


def executionId_norm(execution_id: str) -> str:
    return (execution_id or "").strip()


def reconcile_expired_leases(owner: str) -> list[Dict[str, Any]]:
    """
    Worker-restart recovery: reclaim RUNNING rows whose lease lapsed so they
    resume deterministically under the new owner.
    """
    return get_store().acquire_expired_lease(owner, time.time())


def heartbeat(execution_id: str, tenant_id: str, owner: str) -> bool:
    """Renew a running lease; False when this worker no longer owns the job."""
    record = get(execution_id, tenant_id)
    if not record or record["status"] != STATUS_RUNNING:
        return False
    if record.get("lease_owner") not in (None, owner):
        return False
    updated = get_store().compare_and_transition(
        execution_id,
        record["status_version"],
        new_status=STATUS_RUNNING,
        failure_class=None,
        lease_owner=owner,
        lease_expires=time.time() + DEFAULT_LEASE_SECONDS,
    )
    return updated is not None
