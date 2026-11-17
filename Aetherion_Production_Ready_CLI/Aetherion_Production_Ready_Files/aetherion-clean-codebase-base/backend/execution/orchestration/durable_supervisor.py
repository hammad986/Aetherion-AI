"""
backend/execution/orchestration/durable_supervisor.py — Phase 20 Durable Execution Supervisor.

Authoritative execution supervisor & crash-resilient workflow kernel for Aetherion.
Owns task execution state, graph state, step state, worker ownership, leases,
checkpoints, idempotency tracking, orphan detection, and deterministic recovery decisions.

Key Guarantees:
1. One authoritative execution supervisor owning the lifecycle.
2. Durable step state machine with atomic CAS claiming & lease enforcement.
3. Expired workers cannot commit stale results (StaleLeaseError).
4. Heartbeat tracking & deterministic orphan detection.
5. Deterministic RecoveryDecisionEngine (RESUME, RETRY, WAIT, FAIL_CLOSED).
6. Idempotency identities preventing duplicate side-effect execution across worker retries.
7. Durable boundary checkpointing and graph reconstitution across simulated process crashes.
8. Cancel-wins semantics preventing late success or recovery from overriding cancellation.
9. Full integration with RunBudget, HITL gates, and Verifier truth.
10. Typed replay events scrubbed of secrets.
"""

from __future__ import annotations

import collections
import dataclasses
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import uuid

from .engineering_memory import scrub_secrets
from .execution_plan import (
    ExecutionPlan,
    PlanRiskLevel,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
    PlanValidationError,
)
from .execution_graph import ExecutionGraph

logger = logging.getLogger("aetherion.orchestration.durable_supervisor")


# ── Domain Exceptions ─────────────────────────────────────────────────────────

class DurableSupervisorError(Exception):
    """Base error for durable supervisor operations."""


class StaleLeaseError(DurableSupervisorError):
    """Raised when an expired or overridden worker attempts to mutate or commit a step."""


class DuplicateClaimError(DurableSupervisorError):
    """Raised when a worker attempts to claim a step that is already claimed or not ready."""


class InvalidStepTransitionError(DurableSupervisorError):
    """Raised when an illegal step state transition is attempted."""


class DuplicateWorkerExecutionError(DurableSupervisorError):
    """Raised when multiple workers attempt the same side-effecting attempt concurrently."""


class TaskCancelledError(DurableSupervisorError):
    """Raised when an operation is rejected because the task was cancelled (cancel-wins)."""


class RecoveryFailedError(DurableSupervisorError):
    """Raised when recovery evaluation or execution fails closed."""


# ── Step State Machine & Transition Rules ────────────────────────────────────

VALID_STEP_TRANSITIONS: Dict[str, Set[str]] = {
    PlanStepStatus.PENDING.value: {
        PlanStepStatus.READY.value,
        PlanStepStatus.SKIPPED.value,
        PlanStepStatus.CANCELLED.value,
    },
    PlanStepStatus.READY.value: {
        PlanStepStatus.CLAIMED.value,
        PlanStepStatus.SKIPPED.value,
        PlanStepStatus.CANCELLED.value,
    },
    PlanStepStatus.CLAIMED.value: {
        PlanStepStatus.RUNNING.value,
        PlanStepStatus.ORPHANED.value,
        PlanStepStatus.FAILED.value,
        PlanStepStatus.CANCELLED.value,
    },
    PlanStepStatus.RUNNING.value: {
        PlanStepStatus.VERIFYING.value,
        PlanStepStatus.REPAIRING.value,
        PlanStepStatus.ORPHANED.value,
        PlanStepStatus.FAILED.value,
        PlanStepStatus.CANCELLED.value,
        PlanStepStatus.SUCCEEDED.value,
    },
    PlanStepStatus.VERIFYING.value: {
        PlanStepStatus.SUCCEEDED.value,
        PlanStepStatus.REPAIRING.value,
        PlanStepStatus.FAILED.value,
        PlanStepStatus.ORPHANED.value,
        PlanStepStatus.CANCELLED.value,
    },
    PlanStepStatus.REPAIRING.value: {
        PlanStepStatus.RUNNING.value,
        PlanStepStatus.FAILED.value,
        PlanStepStatus.CANCELLED.value,
    },
    PlanStepStatus.ORPHANED.value: {
        PlanStepStatus.RECOVERING.value,
        PlanStepStatus.FAILED.value,
        PlanStepStatus.CANCELLED.value,
    },
    PlanStepStatus.RECOVERING.value: {
        PlanStepStatus.RETRYING.value,
        PlanStepStatus.RUNNING.value,
        PlanStepStatus.VERIFYING.value,
        PlanStepStatus.FAILED.value,
        PlanStepStatus.CANCELLED.value,
    },
    PlanStepStatus.RETRYING.value: {
        PlanStepStatus.READY.value,
        PlanStepStatus.CLAIMED.value,
        PlanStepStatus.RUNNING.value,
        PlanStepStatus.FAILED.value,
        PlanStepStatus.CANCELLED.value,
    },
    PlanStepStatus.FAILED.value: {
        PlanStepStatus.RETRYING.value,
        PlanStepStatus.READY.value,
    },
    # Terminal states
    PlanStepStatus.SUCCEEDED.value: set(),  # Immutable
    PlanStepStatus.CANCELLED.value: set(),  # Strictly terminal (cancel-wins)
    PlanStepStatus.SKIPPED.value: set(),
    PlanStepStatus.BLOCKED.value: {
        PlanStepStatus.READY.value,
        PlanStepStatus.CANCELLED.value,
    },
}


# ── Recovery Models ──────────────────────────────────────────────────────────

class RecoveryDecision(str, Enum):
    """Deterministic recovery decision outcomes."""
    RESUME = "RESUME"
    RETRY = "RETRY"
    WAIT = "WAIT"
    FAIL_CLOSED = "FAIL_CLOSED"


@dataclass
class StepLease:
    """Ownership lease held by a worker executing a plan step."""
    step_id: str
    task_id: str
    worker_id: str
    lease_id: str
    lease_start: float
    lease_expires: float
    heartbeat_at: float
    attempt_number: int
    lease_duration_seconds: float = 30.0

    def is_expired(self, now: Optional[float] = None) -> bool:
        t = now if now is not None else time.time()
        return t > self.lease_expires

    def as_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "lease_id": self.lease_id,
            "lease_start": self.lease_start,
            "lease_expires": self.lease_expires,
            "heartbeat_at": self.heartbeat_at,
            "attempt_number": self.attempt_number,
            "lease_duration_seconds": self.lease_duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> StepLease:
        return cls(
            step_id=data["step_id"],
            task_id=data["task_id"],
            worker_id=data["worker_id"],
            lease_id=data["lease_id"],
            lease_start=float(data["lease_start"]),
            lease_expires=float(data["lease_expires"]),
            heartbeat_at=float(data["heartbeat_at"]),
            attempt_number=int(data["attempt_number"]),
            lease_duration_seconds=float(data.get("lease_duration_seconds", 30.0)),
        )


@dataclass
class RecoveryEvaluation:
    """Detailed audit record of an orphan recovery decision."""
    decision: RecoveryDecision
    reason: str
    step_id: str
    task_id: str
    attempt_number: int
    idempotent: bool
    remaining_retries: int
    has_unverified_side_effects: bool
    hitl_pending: bool
    budget_exhausted: bool
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": scrub_secrets(self.reason),
            "step_id": self.step_id,
            "task_id": self.task_id,
            "attempt_number": self.attempt_number,
            "idempotent": self.idempotent,
            "remaining_retries": self.remaining_retries,
            "has_unverified_side_effects": self.has_unverified_side_effects,
            "hitl_pending": self.hitl_pending,
            "budget_exhausted": self.budget_exhausted,
            "timestamp": self.timestamp,
        }


@dataclass
class ExecutionCheckpoint:
    """Durable state snapshot captured at execution boundaries."""
    checkpoint_id: str
    task_id: str
    graph_id: str
    revision_id: int
    boundary_type: str
    step_id: Optional[str]
    graph_state_json: str
    budget_state_json: str
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "graph_id": self.graph_id,
            "revision_id": self.revision_id,
            "boundary_type": self.boundary_type,
            "step_id": self.step_id,
            "graph_state_json": self.graph_state_json,
            "budget_state_json": self.budget_state_json,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExecutionCheckpoint:
        return cls(
            checkpoint_id=data["checkpoint_id"],
            task_id=data["task_id"],
            graph_id=data["graph_id"],
            revision_id=int(data.get("revision_id", 0)),
            boundary_type=data["boundary_type"],
            step_id=data.get("step_id"),
            graph_state_json=data["graph_state_json"],
            budget_state_json=data.get("budget_state_json", "{}"),
            timestamp=float(data.get("timestamp", time.time())),
        )


@dataclass
class DurableStepRecord:
    """Authoritative persistent record of a step within the supervisor store."""
    task_id: str
    step_id: str
    status: str
    version: int
    worker_id: Optional[str]
    lease_id: Optional[str]
    lease_start: Optional[float]
    lease_expires: Optional[float]
    heartbeat_at: Optional[float]
    attempt_number: int
    result_json: Optional[str]
    evidence_json: Optional[str]
    error_message: Optional[str]
    created_at: float
    updated_at: float

    def as_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


# ── Recovery Decision Engine ─────────────────────────────────────────────────

class RecoveryDecisionEngine:
    """
    Deterministic recovery evaluator for orphaned, crashed, or interrupted steps.
    Considers step state, attempt history, lease validity, tool side effects,
    verifier state, HITL gates, and RunBudget limits.
    """

    @staticmethod
    def evaluate(
        task_id: str,
        step_id: str,
        attempt_number: int,
        max_retries: int,
        is_idempotent: bool,
        has_unverified_mutations: bool,
        verifier_state: Optional[Dict[str, Any]] = None,
        hitl_state: Optional[Dict[str, Any]] = None,
        run_budget: Optional[Any] = None,
        task_status: Optional[str] = None,
    ) -> RecoveryEvaluation:
        """
        Evaluate and return a deterministic RecoveryEvaluation.
        Enforces:
        - Cancel-wins: Task cancellation terminates fail-closed.
        - Verifier truth: Succeeded verification allows RESUME to commit.
        - HITL safety: Pending HITL approvals cannot be blindly retried (WAIT).
        - Idempotency safety: Non-idempotent unverified side effects fail closed.
        - RunBudget compliance: Exhausted budget terminates fail closed.
        - Retry bounds: Exceeded max retries terminates fail closed.
        """
        v_state = verifier_state or {}
        h_state = hitl_state or {}
        remaining_retries = max(0, max_retries - attempt_number)

        # 1. Cancel-Wins check
        if task_status in ("CANCELLED", "STOPPED", PlanStatus.CANCELLED.value):
            return RecoveryEvaluation(
                decision=RecoveryDecision.FAIL_CLOSED,
                reason="Task has been cancelled or stopped; recovery rejected.",
                step_id=step_id,
                task_id=task_id,
                attempt_number=attempt_number,
                idempotent=is_idempotent,
                remaining_retries=remaining_retries,
                has_unverified_side_effects=has_unverified_mutations,
                hitl_pending=False,
                budget_exhausted=False,
            )

        # 2. Verifier truth check
        # If verification already passed prior to worker crash, resume to commit result safely
        if v_state.get("passed") is True or v_state.get("status") in ("PASSED", "SUCCEEDED"):
            return RecoveryEvaluation(
                decision=RecoveryDecision.RESUME,
                reason="Verification criteria already satisfied before worker disappearance; resuming to commit.",
                step_id=step_id,
                task_id=task_id,
                attempt_number=attempt_number,
                idempotent=is_idempotent,
                remaining_retries=remaining_retries,
                has_unverified_side_effects=False,
                hitl_pending=False,
                budget_exhausted=False,
            )

        # 3. HITL approval state check
        if h_state.get("requires_hitl") is True or h_state.get("waiting_approval") is True:
            if h_state.get("rejected") is True:
                return RecoveryEvaluation(
                    decision=RecoveryDecision.FAIL_CLOSED,
                    reason="HITL approval gate was explicitly rejected; cannot resume or retry.",
                    step_id=step_id,
                    task_id=task_id,
                    attempt_number=attempt_number,
                    idempotent=is_idempotent,
                    remaining_retries=remaining_retries,
                    has_unverified_side_effects=has_unverified_mutations,
                    hitl_pending=False,
                    budget_exhausted=False,
                )
            if h_state.get("approved") is True:
                return RecoveryEvaluation(
                    decision=RecoveryDecision.RESUME,
                    reason="Dangerous action was approved by human operator; resuming execution.",
                    step_id=step_id,
                    task_id=task_id,
                    attempt_number=attempt_number,
                    idempotent=is_idempotent,
                    remaining_retries=remaining_retries,
                    has_unverified_side_effects=has_unverified_mutations,
                    hitl_pending=False,
                    budget_exhausted=False,
                )
            # Pending approval - MUST NOT blindly retry or resume
            return RecoveryEvaluation(
                decision=RecoveryDecision.WAIT,
                reason="Dangerous action pending human operator approval gate; execution paused safely.",
                step_id=step_id,
                task_id=task_id,
                attempt_number=attempt_number,
                idempotent=is_idempotent,
                remaining_retries=remaining_retries,
                has_unverified_side_effects=has_unverified_mutations,
                hitl_pending=True,
                budget_exhausted=False,
            )

        # 4. Budget constraints check
        budget_exhausted = False
        if run_budget is not None:
            if hasattr(run_budget, "is_exhausted") and run_budget.is_exhausted():
                budget_exhausted = True
            elif hasattr(run_budget, "check"):
                try:
                    run_budget.check()
                except Exception:
                    budget_exhausted = True
            if not budget_exhausted and hasattr(run_budget, "usage") and hasattr(run_budget, "max_model_calls"):
                if (
                    run_budget.usage.model_calls >= run_budget.max_model_calls
                    or run_budget.usage.tool_calls >= run_budget.max_tool_calls
                    or run_budget.usage.repair_attempts >= run_budget.max_repair_attempts
                ):
                    budget_exhausted = True
            elif isinstance(run_budget, dict) and run_budget.get("is_exhausted") is True:
                budget_exhausted = True

        if budget_exhausted:
            return RecoveryEvaluation(
                decision=RecoveryDecision.FAIL_CLOSED,
                reason="RunBudget exhausted; cannot schedule retry or resume.",
                step_id=step_id,
                task_id=task_id,
                attempt_number=attempt_number,
                idempotent=is_idempotent,
                remaining_retries=remaining_retries,
                has_unverified_side_effects=has_unverified_mutations,
                hitl_pending=False,
                budget_exhausted=True,
            )

        # 5. Retry limits check
        if attempt_number >= max_retries:
            return RecoveryEvaluation(
                decision=RecoveryDecision.FAIL_CLOSED,
                reason=f"Step has reached maximum retry attempts ({max_retries}); failing closed.",
                step_id=step_id,
                task_id=task_id,
                attempt_number=attempt_number,
                idempotent=is_idempotent,
                remaining_retries=0,
                has_unverified_side_effects=has_unverified_mutations,
                hitl_pending=False,
                budget_exhausted=False,
            )

        # 6. Side-effect & idempotency check
        # Non-idempotent side effects partially applied without verifier truth must NOT be blindly retried
        if has_unverified_mutations and not is_idempotent:
            return RecoveryEvaluation(
                decision=RecoveryDecision.FAIL_CLOSED,
                reason="Non-idempotent side effects were partially applied without verification; failing closed to prevent repository corruption.",
                step_id=step_id,
                task_id=task_id,
                attempt_number=attempt_number,
                idempotent=False,
                remaining_retries=remaining_retries,
                has_unverified_side_effects=True,
                hitl_pending=False,
                budget_exhausted=False,
            )

        # 7. Safe retry
        return RecoveryEvaluation(
            decision=RecoveryDecision.RETRY,
            reason=f"Step is safe to retry (attempt {attempt_number + 1} of {max_retries}); scheduling retry.",
            step_id=step_id,
            task_id=task_id,
            attempt_number=attempt_number,
            idempotent=is_idempotent,
            remaining_retries=remaining_retries,
            has_unverified_side_effects=False,
            hitl_pending=False,
            budget_exhausted=False,
        )


# ── Durable Supervisor Store (SQLite WAL) ────────────────────────────────────

_SUPERVISOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS supervised_tasks (
    task_id          TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL,
    status           TEXT NOT NULL,
    current_revision INTEGER NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS supervised_steps (
    task_id          TEXT NOT NULL,
    step_id          TEXT NOT NULL,
    status           TEXT NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1,
    worker_id        TEXT,
    lease_id         TEXT,
    lease_start      REAL,
    lease_expires    REAL,
    heartbeat_at     REAL,
    attempt_number   INTEGER NOT NULL DEFAULT 0,
    result_json      TEXT,
    evidence_json    TEXT,
    error_message    TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    PRIMARY KEY (task_id, step_id)
);

CREATE TABLE IF NOT EXISTS supervised_checkpoints (
    checkpoint_id     TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL,
    graph_id          TEXT NOT NULL,
    revision_id       INTEGER NOT NULL DEFAULT 0,
    boundary_type     TEXT NOT NULL,
    step_id           TEXT,
    graph_state_json  TEXT NOT NULL,
    budget_state_json TEXT NOT NULL,
    timestamp         REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS supervised_recovery_decisions (
    decision_id      TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    step_id          TEXT NOT NULL,
    attempt_number   INTEGER NOT NULL,
    decision         TEXT NOT NULL,
    reason           TEXT NOT NULL,
    metadata_json    TEXT NOT NULL,
    timestamp        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS supervised_idempotency_records (
    token            TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    step_id          TEXT NOT NULL,
    attempt_number   INTEGER NOT NULL,
    operation_name   TEXT NOT NULL,
    worker_id        TEXT NOT NULL,
    status           TEXT NOT NULL,
    result_json      TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);
"""


class DurableSupervisorStore:
    """Thread-safe SQLite store managing persistent supervisor records with atomic CAS."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        if db_path is None:
            db_dir = Path(__file__).resolve().parent.parent.parent / "data"
            db_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = str(db_dir / "durable_supervisor.db")
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
                conn.executescript(_SUPERVISOR_SCHEMA)
            finally:
                conn.close()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute("SELECT * FROM supervised_tasks WHERE task_id = ?", (task_id,))
                row = cur.fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def upsert_task(self, task_id: str, tenant_id: str, status: str, current_revision: int = 0) -> None:
        with self._lock:
            conn = self.get_connection()
            try:
                now = time.time()
                conn.execute(
                    """
                    INSERT INTO supervised_tasks (task_id, tenant_id, status, current_revision, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        status = excluded.status,
                        current_revision = excluded.current_revision,
                        updated_at = excluded.updated_at
                    """,
                    (task_id, tenant_id, status, current_revision, now, now),
                )
            finally:
                conn.close()

    def get_step(self, task_id: str, step_id: str) -> Optional[DurableStepRecord]:
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute(
                    "SELECT * FROM supervised_steps WHERE task_id = ? AND step_id = ?",
                    (task_id, step_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return DurableStepRecord(
                    task_id=row["task_id"],
                    step_id=row["step_id"],
                    status=row["status"],
                    version=row["version"],
                    worker_id=row["worker_id"],
                    lease_id=row["lease_id"],
                    lease_start=row["lease_start"],
                    lease_expires=row["lease_expires"],
                    heartbeat_at=row["heartbeat_at"],
                    attempt_number=row["attempt_number"],
                    result_json=row["result_json"],
                    evidence_json=row["evidence_json"],
                    error_message=row["error_message"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            finally:
                conn.close()

    def list_steps(self, task_id: str) -> List[DurableStepRecord]:
        with self._lock:
            conn = self.get_connection()
            try:
                cur = conn.execute(
                    "SELECT * FROM supervised_steps WHERE task_id = ? ORDER BY step_id ASC",
                    (task_id,),
                )
                rows = cur.fetchall()
                res = []
                for row in rows:
                    res.append(DurableStepRecord(
                        task_id=row["task_id"],
                        step_id=row["step_id"],
                        status=row["status"],
                        version=row["version"],
                        worker_id=row["worker_id"],
                        lease_id=row["lease_id"],
                        lease_start=row["lease_start"],
                        lease_expires=row["lease_expires"],
                        heartbeat_at=row["heartbeat_at"],
                        attempt_number=row["attempt_number"],
                        result_json=row["result_json"],
                        evidence_json=row["evidence_json"],
                        error_message=row["error_message"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    ))
                return res
            finally:
                conn.close()

    def insert_step_initial(self, task_id: str, step_id: str, status: str = "PENDING") -> None:
        with self._lock:
            conn = self.get_connection()
            try:
                now = time.time()
                conn.execute(
                    """
                    INSERT INTO supervised_steps (
                        task_id, step_id, status, version, attempt_number, created_at, updated_at
                    ) VALUES (?, ?, ?, 1, 0, ?, ?)
                    ON CONFLICT(task_id, step_id) DO NOTHING
                    """,
                    (task_id, step_id, status, now, now),
                )
            finally:
                conn.close()


# ── Authoritative Durable Execution Supervisor ───────────────────────────────

class DurableExecutionSupervisor:
    """
    Authoritative Execution Supervisor owning task, graph, step, worker ownership,
    leases, checkpoints, idempotency, and recovery decisions.
    """

    def __init__(
        self,
        store: Optional[DurableSupervisorStore] = None,
        db_path: Optional[str | Path] = None,
        default_lease_seconds: float = 30.0,
        heartbeat_timeout_seconds: float = 30.0,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.store = store or DurableSupervisorStore(db_path=db_path)
        self.default_lease_seconds = default_lease_seconds
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.event_callback = event_callback
        self.events: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._active_graphs: Dict[str, ExecutionGraph] = {}

    def _emit_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Emit scrubbed typed event to local history and optional callback."""
        scrubbed = {k: (scrub_secrets(str(v)) if isinstance(v, str) else v) for k, v in payload.items()}
        ev = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "event_type": event_type,
            "timestamp": time.time(),
            "payload": scrubbed,
        }
        self.events.append(ev)
        if self.event_callback:
            try:
                self.event_callback(event_type, scrubbed)
            except Exception as cb_exc:
                logger.debug("Event callback error for %s: %s", event_type, cb_exc)

    # ── Task & Graph Registration ────────────────────────────────────────────

    def register_task(
        self,
        task_id: str,
        tenant_id: str,
        graph: ExecutionGraph,
        status: str = "RUNNING",
    ) -> None:
        """Register a task and its initial execution graph into the supervisor."""
        with self._lock:
            self._active_graphs[task_id] = graph
            self.store.upsert_task(
                task_id=task_id,
                tenant_id=tenant_id,
                status=status,
                current_revision=graph.plan.revision_count,
            )

            # Register all steps
            for s_id, step in graph.plan.steps.items():
                self.store.insert_step_initial(task_id, s_id, status=step.status.value)

            # Checkpoint graph registration
            self.create_checkpoint(
                task_id=task_id,
                boundary_type="plan_validation",
                graph=graph,
            )

    def get_graph(self, task_id: str) -> Optional[ExecutionGraph]:
        """Fetch active in-memory or reconstituted execution graph."""
        with self._lock:
            if task_id in self._active_graphs:
                return self._active_graphs[task_id]
            # Try reconstitution from checkpoint
            try:
                graph, _ = self.reconstitute_execution(task_id)
                return graph
            except Exception:
                return None

    # ── Step Lifecycle, CAS Claiming & Lease Management ──────────────────────

    def claim_step(
        self,
        task_id: str,
        step_id: str,
        worker_id: str,
        lease_duration: Optional[float] = None,
        now: Optional[float] = None,
    ) -> StepLease:
        """
        Atomic Compare-And-Set (CAS) step claiming.
        Enforces:
        - Task is not CANCELLED or STOPPED.
        - Step is currently in READY, RETRYING, or PENDING state.
        - Only one worker can claim the step. Duplicate claims are rejected.
        - Creates a durable StepLease and emits 'step.claimed'.
        """
        curr_time = now if now is not None else time.time()
        duration = lease_duration or self.default_lease_seconds

        with self._lock:
            # 1. Cancel-wins check
            task_rec = self.store.get_task(task_id)
            if task_rec and task_rec["status"] in ("CANCELLED", "STOPPED", PlanStatus.CANCELLED.value):
                raise TaskCancelledError(f"Task '{task_id}' is cancelled; step claiming rejected.")

            step_rec = self.store.get_step(task_id, step_id)
            if not step_rec:
                raise DurableSupervisorError(f"Step '{step_id}' not found in task '{task_id}'.")

            allowed_claim_states = {
                PlanStepStatus.READY.value,
                PlanStepStatus.PENDING.value,
                PlanStepStatus.RETRYING.value,
            }

            if step_rec.status not in allowed_claim_states:
                # If claimed/running and not expired, raise DuplicateClaimError
                if step_rec.lease_expires and step_rec.lease_expires > curr_time:
                    raise DuplicateClaimError(
                        f"Step '{step_id}' is currently owned by worker '{step_rec.worker_id}' with active lease."
                    )
                raise DuplicateClaimError(
                    f"Step '{step_id}' cannot be claimed from status '{step_rec.status}'."
                )

            new_lease_id = f"lease_{uuid.uuid4().hex[:12]}"
            lease_expires = curr_time + duration
            new_attempt = step_rec.attempt_number + 1

            conn = self.store.get_connection()
            try:
                # CAS atomic update
                cur = conn.execute(
                    """
                    UPDATE supervised_steps
                    SET status = ?,
                        version = version + 1,
                        worker_id = ?,
                        lease_id = ?,
                        lease_start = ?,
                        lease_expires = ?,
                        heartbeat_at = ?,
                        attempt_number = ?,
                        updated_at = ?
                    WHERE task_id = ? AND step_id = ? AND version = ?
                    """,
                    (
                        PlanStepStatus.CLAIMED.value,
                        worker_id,
                        new_lease_id,
                        curr_time,
                        lease_expires,
                        curr_time,
                        new_attempt,
                        curr_time,
                        task_id,
                        step_id,
                        step_rec.version,
                    ),
                )
                if cur.rowcount == 0:
                    raise DuplicateClaimError(f"CAS contention: Step '{step_id}' claimed by concurrent worker.")
            finally:
                conn.close()

            # Update active graph if present
            graph = self._active_graphs.get(task_id)
            if graph:
                step = graph.get_step(step_id)
                if step:
                    step.status = PlanStepStatus.CLAIMED
                    step.worker_id = worker_id
                    step.lease_id = new_lease_id
                    step.lease_expires = lease_expires
                    step.heartbeat_at = curr_time
                    step.retries_attempted = new_attempt - 1

            lease = StepLease(
                step_id=step_id,
                task_id=task_id,
                worker_id=worker_id,
                lease_id=new_lease_id,
                lease_start=curr_time,
                lease_expires=lease_expires,
                heartbeat_at=curr_time,
                attempt_number=new_attempt,
                lease_duration_seconds=duration,
            )

            self._emit_event("step.claimed", {
                "task_id": task_id,
                "step_id": step_id,
                "worker_id": worker_id,
                "lease_id": new_lease_id,
                "attempt_number": new_attempt,
                "lease_expires": lease_expires,
            })

            # Checkpoint step claim
            if graph:
                self.create_checkpoint(
                    task_id=task_id,
                    boundary_type="step_claim",
                    graph=graph,
                    step_id=step_id,
                )

            return lease

    def _verify_lease_holder(
        self,
        task_id: str,
        step_id: str,
        worker_id: str,
        lease_id: str,
        now: Optional[float] = None,
    ) -> DurableStepRecord:
        """
        Verify that caller holds the active valid lease for the step.
        Raises TaskCancelledError or StaleLeaseError on violation.
        """
        curr_time = now if now is not None else time.time()

        # Cancel-wins check
        task_rec = self.store.get_task(task_id)
        if task_rec and task_rec["status"] in ("CANCELLED", "STOPPED", PlanStatus.CANCELLED.value):
            raise TaskCancelledError(f"Task '{task_id}' is cancelled; mutation rejected.")

        step_rec = self.store.get_step(task_id, step_id)
        if not step_rec:
            raise DurableSupervisorError(f"Step '{step_id}' not found.")

        if step_rec.lease_id != lease_id:
            raise StaleLeaseError(
                f"Lease mismatch for step '{step_id}': expected '{step_rec.lease_id}', got '{lease_id}'."
            )

        if step_rec.worker_id != worker_id:
            raise StaleLeaseError(
                f"Worker mismatch for step '{step_id}': expected '{step_rec.worker_id}', got '{worker_id}'."
            )

        if step_rec.lease_expires is not None and curr_time > step_rec.lease_expires:
            raise StaleLeaseError(
                f"Lease '{lease_id}' for step '{step_id}' expired at {step_rec.lease_expires} (now {curr_time})."
            )

        return step_rec

    def heartbeat_step(
        self,
        task_id: str,
        step_id: str,
        worker_id: str,
        lease_id: str,
        lease_duration: Optional[float] = None,
        now: Optional[float] = None,
    ) -> StepLease:
        """
        Renew an active lease and update heartbeat timestamp.
        Raises StaleLeaseError if lease expired or stolen.
        """
        curr_time = now if now is not None else time.time()
        duration = lease_duration or self.default_lease_seconds

        with self._lock:
            step_rec = self._verify_lease_holder(task_id, step_id, worker_id, lease_id, now=curr_time)

            new_expires = curr_time + duration
            conn = self.store.get_connection()
            try:
                conn.execute(
                    """
                    UPDATE supervised_steps
                    SET heartbeat_at = ?,
                        lease_expires = ?,
                        version = version + 1,
                        updated_at = ?
                    WHERE task_id = ? AND step_id = ? AND lease_id = ?
                    """,
                    (curr_time, new_expires, curr_time, task_id, step_id, lease_id),
                )
            finally:
                conn.close()

            # Update active graph
            graph = self._active_graphs.get(task_id)
            if graph:
                step = graph.get_step(step_id)
                if step:
                    step.heartbeat_at = curr_time
                    step.lease_expires = new_expires

            lease = StepLease(
                step_id=step_id,
                task_id=task_id,
                worker_id=worker_id,
                lease_id=lease_id,
                lease_start=step_rec.lease_start or curr_time,
                lease_expires=new_expires,
                heartbeat_at=curr_time,
                attempt_number=step_rec.attempt_number,
                lease_duration_seconds=duration,
            )

            self._emit_event("step.heartbeat", {
                "task_id": task_id,
                "step_id": step_id,
                "worker_id": worker_id,
                "lease_id": lease_id,
                "heartbeat_at": curr_time,
                "lease_expires": new_expires,
            })

            return lease

    def start_step(
        self,
        task_id: str,
        step_id: str,
        worker_id: str,
        lease_id: str,
        now: Optional[float] = None,
    ) -> None:
        """Transition step to RUNNING state under valid lease."""
        curr_time = now if now is not None else time.time()
        with self._lock:
            step_rec = self._verify_lease_holder(task_id, step_id, worker_id, lease_id, now=curr_time)

            valid_targets = VALID_STEP_TRANSITIONS.get(step_rec.status, set())
            if PlanStepStatus.RUNNING.value not in valid_targets and step_rec.status != PlanStepStatus.RUNNING.value:
                raise InvalidStepTransitionError(
                    f"Cannot transition step '{step_id}' from '{step_rec.status}' to 'RUNNING'."
                )

            conn = self.store.get_connection()
            try:
                conn.execute(
                    """
                    UPDATE supervised_steps
                    SET status = ?,
                        heartbeat_at = ?,
                        version = version + 1,
                        updated_at = ?
                    WHERE task_id = ? AND step_id = ? AND lease_id = ?
                    """,
                    (PlanStepStatus.RUNNING.value, curr_time, curr_time, task_id, step_id, lease_id),
                )
            finally:
                conn.close()

            graph = self._active_graphs.get(task_id)
            if graph:
                step = graph.get_step(step_id)
                if step:
                    step.status = PlanStepStatus.RUNNING
                    step.started_at = curr_time
                self.create_checkpoint(
                    task_id=task_id,
                    boundary_type="step_start",
                    graph=graph,
                    step_id=step_id,
                )

    def record_tool_batch(
        self,
        task_id: str,
        step_id: str,
        worker_id: str,
        lease_id: str,
        tool_names: List[str],
        tool_results: Any,
        now: Optional[float] = None,
    ) -> None:
        """Record completion of a tool execution batch and checkpoint."""
        curr_time = now if now is not None else time.time()
        with self._lock:
            self._verify_lease_holder(task_id, step_id, worker_id, lease_id, now=curr_time)
            graph = self._active_graphs.get(task_id)
            if graph:
                self.create_checkpoint(
                    task_id=task_id,
                    boundary_type="tool_batch_completed",
                    graph=graph,
                    step_id=step_id,
                )

    def start_verification(
        self,
        task_id: str,
        step_id: str,
        worker_id: str,
        lease_id: str,
        now: Optional[float] = None,
    ) -> None:
        """Transition step to VERIFYING state under valid lease."""
        curr_time = now if now is not None else time.time()
        with self._lock:
            step_rec = self._verify_lease_holder(task_id, step_id, worker_id, lease_id, now=curr_time)

            valid_targets = VALID_STEP_TRANSITIONS.get(step_rec.status, set())
            if PlanStepStatus.VERIFYING.value not in valid_targets:
                raise InvalidStepTransitionError(
                    f"Cannot transition step '{step_id}' from '{step_rec.status}' to 'VERIFYING'."
                )

            conn = self.store.get_connection()
            try:
                conn.execute(
                    """
                    UPDATE supervised_steps
                    SET status = ?,
                        heartbeat_at = ?,
                        version = version + 1,
                        updated_at = ?
                    WHERE task_id = ? AND step_id = ? AND lease_id = ?
                    """,
                    (PlanStepStatus.VERIFYING.value, curr_time, curr_time, task_id, step_id, lease_id),
                )
            finally:
                conn.close()

            graph = self._active_graphs.get(task_id)
            if graph:
                step = graph.get_step(step_id)
                if step:
                    step.status = PlanStepStatus.VERIFYING
                self.create_checkpoint(
                    task_id=task_id,
                    boundary_type="verification_start",
                    graph=graph,
                    step_id=step_id,
                )

    def complete_step(
        self,
        task_id: str,
        step_id: str,
        worker_id: str,
        lease_id: str,
        result: Dict[str, Any],
        evidence_reference: Optional[str] = None,
        now: Optional[float] = None,
    ) -> None:
        """
        Transition step to SUCCEEDED state.
        Enforces:
        - Valid, unexpired lease holder.
        - Cancel-wins check: If task is cancelled, success commit is rejected.
        - Successful steps become permanently immutable.
        - Automatically unblocks downstream dependencies in the execution graph.
        """
        curr_time = now if now is not None else time.time()
        with self._lock:
            step_rec = self._verify_lease_holder(task_id, step_id, worker_id, lease_id, now=curr_time)

            # Check if step is already succeeded (immutable)
            if step_rec.status == PlanStepStatus.SUCCEEDED.value:
                logger.info("Step '%s' is already SUCCEEDED; ignoring duplicate commit.", step_id)
                return

            res_json = json.dumps(result or {})
            ev_list = [evidence_reference] if evidence_reference else []
            ev_json = json.dumps(ev_list)

            conn = self.store.get_connection()
            try:
                conn.execute(
                    """
                    UPDATE supervised_steps
                    SET status = ?,
                        result_json = ?,
                        evidence_json = ?,
                        version = version + 1,
                        updated_at = ?
                    WHERE task_id = ? AND step_id = ? AND lease_id = ?
                    """,
                    (PlanStepStatus.SUCCEEDED.value, res_json, ev_json, curr_time, task_id, step_id, lease_id),
                )
            finally:
                conn.close()

            # Update active graph
            graph = self._active_graphs.get(task_id)
            if graph:
                graph.mark_step_succeeded(step_id, result=result, evidence_reference=evidence_reference)
                # Check for unblocked steps
                ready_steps = graph.get_ready_steps()
                for r_step in ready_steps:
                    self.store.insert_step_initial(task_id, r_step.step_id, status=PlanStepStatus.READY.value)
                    # Update status in db if it was pending
                    c = self.store.get_connection()
                    try:
                        c.execute(
                            "UPDATE supervised_steps SET status = ? WHERE task_id = ? AND step_id = ? AND status = ?",
                            (PlanStepStatus.READY.value, task_id, r_step.step_id, PlanStepStatus.PENDING.value),
                        )
                    finally:
                        c.close()

                self.create_checkpoint(
                    task_id=task_id,
                    boundary_type="step_succeeded",
                    graph=graph,
                    step_id=step_id,
                )

            self._emit_event("step.recovered" if step_rec.status in (PlanStepStatus.RECOVERING.value, PlanStepStatus.RETRYING.value) else "step.completed", {
                "task_id": task_id,
                "step_id": step_id,
                "worker_id": worker_id,
                "lease_id": lease_id,
                "status": "SUCCEEDED",
            })

    def fail_step(
        self,
        task_id: str,
        step_id: str,
        worker_id: str,
        lease_id: str,
        error_message: str,
        now: Optional[float] = None,
    ) -> None:
        """Transition step to FAILED state under valid lease."""
        curr_time = now if now is not None else time.time()
        with self._lock:
            self._verify_lease_holder(task_id, step_id, worker_id, lease_id, now=curr_time)
            scrubbed_err = scrub_secrets(error_message)

            conn = self.store.get_connection()
            try:
                conn.execute(
                    """
                    UPDATE supervised_steps
                    SET status = ?,
                        error_message = ?,
                        version = version + 1,
                        updated_at = ?
                    WHERE task_id = ? AND step_id = ? AND lease_id = ?
                    """,
                    (PlanStepStatus.FAILED.value, scrubbed_err, curr_time, task_id, step_id, lease_id),
                )
            finally:
                conn.close()

            graph = self._active_graphs.get(task_id)
            if graph:
                graph.mark_step_failed(step_id, error=scrubbed_err)
                self.create_checkpoint(
                    task_id=task_id,
                    boundary_type="step_failed",
                    graph=graph,
                    step_id=step_id,
                )

    # ── Heartbeat & Orphan Detection ─────────────────────────────────────────

    def detect_orphans(
        self,
        heartbeat_timeout_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> List[DurableStepRecord]:
        """
        Scan for non-terminal steps whose lease has expired and heartbeat is stale.
        Transitions candidate steps to ORPHANED state and emits 'step.lease_expired'
        and 'step.orphaned' events.
        """
        curr_time = now if now is not None else time.time()
        timeout = heartbeat_timeout_seconds or self.heartbeat_timeout_seconds
        orphaned: List[DurableStepRecord] = []

        with self._lock:
            conn = self.store.get_connection()
            try:
                # Find all active non-terminal steps
                cur = conn.execute(
                    """
                    SELECT * FROM supervised_steps
                    WHERE status IN ('CLAIMED', 'RUNNING', 'VERIFYING')
                    """
                )
                rows = cur.fetchall()
                for row in rows:
                    hb = row["heartbeat_at"] or row["lease_start"] or 0.0
                    lease_exp = row["lease_expires"] or 0.0

                    # Stale heartbeat AND expired lease
                    if (curr_time - hb > timeout) and (curr_time > lease_exp):
                        task_id = row["task_id"]
                        step_id = row["step_id"]

                        # Check if task is already cancelled
                        task_rec = self.store.get_task(task_id)
                        target_status = PlanStepStatus.CANCELLED.value if (task_rec and task_rec["status"] == "CANCELLED") else PlanStepStatus.ORPHANED.value

                        conn.execute(
                            """
                            UPDATE supervised_steps
                            SET status = ?,
                                version = version + 1,
                                updated_at = ?
                            WHERE task_id = ? AND step_id = ? AND version = ?
                            """,
                            (target_status, curr_time, task_id, step_id, row["version"]),
                        )

                        rec = self.store.get_step(task_id, step_id)
                        if rec:
                            orphaned.append(rec)
                            graph = self._active_graphs.get(task_id)
                            if graph:
                                s = graph.get_step(step_id)
                                if s:
                                    s.status = PlanStepStatus(target_status)

                            self._emit_event("step.lease_expired", {
                                "task_id": task_id,
                                "step_id": step_id,
                                "worker_id": row["worker_id"],
                                "lease_id": row["lease_id"],
                                "lease_expires": lease_exp,
                            })
                            self._emit_event("step.orphaned", {
                                "task_id": task_id,
                                "step_id": step_id,
                                "worker_id": row["worker_id"],
                                "attempt_number": row["attempt_number"],
                            })
            finally:
                conn.close()

        return orphaned

    # ── Recovery Decision & Execution ────────────────────────────────────────

    def evaluate_and_recover_orphan(
        self,
        task_id: str,
        step_id: str,
        is_idempotent: bool = True,
        has_unverified_mutations: bool = False,
        verifier_state: Optional[Dict[str, Any]] = None,
        hitl_state: Optional[Dict[str, Any]] = None,
        run_budget: Optional[Any] = None,
        max_retries: int = 2,
    ) -> RecoveryEvaluation:
        """
        Evaluate an orphaned or crashed step through RecoveryDecisionEngine and apply recovery.
        Emits 'step.recovery_started', 'step.retry_scheduled', or terminal failure.
        """
        with self._lock:
            step_rec = self.store.get_step(task_id, step_id)
            if not step_rec:
                raise DurableSupervisorError(f"Step '{step_id}' not found.")

            task_rec = self.store.get_task(task_id)
            task_status = task_rec["status"] if task_rec else None

            evaluation = RecoveryDecisionEngine.evaluate(
                task_id=task_id,
                step_id=step_id,
                attempt_number=step_rec.attempt_number,
                max_retries=max_retries,
                is_idempotent=is_idempotent,
                has_unverified_mutations=has_unverified_mutations,
                verifier_state=verifier_state,
                hitl_state=hitl_state,
                run_budget=run_budget,
                task_status=task_status,
            )

            # Persist recovery decision
            decision_id = f"rec_{uuid.uuid4().hex[:12]}"
            conn = self.store.get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO supervised_recovery_decisions (
                        decision_id, task_id, step_id, attempt_number, decision, reason, metadata_json, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision_id,
                        task_id,
                        step_id,
                        step_rec.attempt_number,
                        evaluation.decision.value,
                        evaluation.reason,
                        json.dumps(evaluation.as_dict()),
                        time.time(),
                    ),
                )
            finally:
                conn.close()

            # Apply state transition based on decision
            now = time.time()
            if evaluation.decision == RecoveryDecision.RESUME:
                self._transition_step_status(task_id, step_id, PlanStepStatus.RECOVERING.value)
                self._emit_event("step.recovery_started", {
                    "task_id": task_id,
                    "step_id": step_id,
                    "decision": "RESUME",
                    "reason": evaluation.reason,
                })

            elif evaluation.decision == RecoveryDecision.RETRY:
                # Set step to RETRYING then READY so workers can re-claim it
                self._transition_step_status(task_id, step_id, PlanStepStatus.RETRYING.value)
                self._transition_step_status(task_id, step_id, PlanStepStatus.READY.value)
                self._emit_event("step.retry_scheduled", {
                    "task_id": task_id,
                    "step_id": step_id,
                    "attempt_number": step_rec.attempt_number + 1,
                    "reason": evaluation.reason,
                })

            elif evaluation.decision == RecoveryDecision.WAIT:
                logger.info("Step '%s' is in WAIT state awaiting HITL.", step_id)

            elif evaluation.decision == RecoveryDecision.FAIL_CLOSED:
                self._transition_step_status(task_id, step_id, PlanStepStatus.FAILED.value, error_message=evaluation.reason)
                graph = self._active_graphs.get(task_id)
                if graph:
                    graph.mark_step_failed(step_id, error=evaluation.reason)
                    self.create_checkpoint(
                        task_id=task_id,
                        boundary_type="step_failed",
                        graph=graph,
                        step_id=step_id,
                    )
                self._emit_event("step.failed", {
                    "task_id": task_id,
                    "step_id": step_id,
                    "reason": evaluation.reason,
                })

            return evaluation

    def _transition_step_status(
        self,
        task_id: str,
        step_id: str,
        new_status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """Internal helper to transition step status in store and graph."""
        now = time.time()
        conn = self.store.get_connection()
        try:
            if error_message:
                conn.execute(
                    """
                    UPDATE supervised_steps
                    SET status = ?,
                        error_message = ?,
                        version = version + 1,
                        updated_at = ?
                    WHERE task_id = ? AND step_id = ?
                    """,
                    (new_status, scrub_secrets(error_message), now, task_id, step_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE supervised_steps
                    SET status = ?,
                        version = version + 1,
                        updated_at = ?
                    WHERE task_id = ? AND step_id = ?
                    """,
                    (new_status, now, task_id, step_id),
                )
        finally:
            conn.close()

        graph = self._active_graphs.get(task_id)
        if graph:
            step = graph.get_step(step_id)
            if step:
                try:
                    step.status = PlanStepStatus(new_status)
                except ValueError:
                    pass

    # ── Idempotency Identity & Tracking ──────────────────────────────────────

    def generate_idempotency_token(
        self,
        task_id: str,
        step_id: str,
        attempt_number: int,
        operation_name: str,
        params: Dict[str, Any],
    ) -> str:
        """Generate deterministic idempotency identity token."""
        raw_key = f"{task_id}:{step_id}:{attempt_number}:{operation_name}:{json.dumps(params, sort_keys=True)}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def check_or_record_operation(
        self,
        task_id: str,
        step_id: str,
        attempt_number: int,
        operation_name: str,
        params: Dict[str, Any],
        worker_id: str,
    ) -> Tuple[bool, Optional[Any]]:
        """
        Check if an operation was already committed for this attempt or is owned by another worker.
        Returns (is_already_committed, committed_result).
        Raises DuplicateWorkerExecutionError if a different worker started the same operation on the same attempt.
        """
        token = self.generate_idempotency_token(task_id, step_id, attempt_number, operation_name, params)
        with self._lock:
            conn = self.store.get_connection()
            try:
                cur = conn.execute(
                    "SELECT * FROM supervised_idempotency_records WHERE token = ?",
                    (token,),
                )
                row = cur.fetchone()
                if row:
                    if row["status"] == "COMMITTED":
                        res = json.loads(row["result_json"]) if row["result_json"] else None
                        return True, res
                    if row["status"] == "STARTED" and row["worker_id"] != worker_id:
                        raise DuplicateWorkerExecutionError(
                            f"Operation '{operation_name}' on attempt {attempt_number} already in flight by worker '{row['worker_id']}'."
                        )
                    return False, None

                # Record operation start
                now = time.time()
                conn.execute(
                    """
                    INSERT INTO supervised_idempotency_records (
                        token, task_id, step_id, attempt_number, operation_name, worker_id, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'STARTED', ?, ?)
                    """,
                    (token, task_id, step_id, attempt_number, operation_name, worker_id, now, now),
                )
                return False, None
            finally:
                conn.close()

    def commit_operation(
        self,
        task_id: str,
        step_id: str,
        attempt_number: int,
        operation_name: str,
        params: Dict[str, Any],
        result: Any,
    ) -> None:
        """Mark an idempotency operation as COMMITTED with its result."""
        token = self.generate_idempotency_token(task_id, step_id, attempt_number, operation_name, params)
        with self._lock:
            conn = self.store.get_connection()
            try:
                now = time.time()
                res_json = json.dumps(result or {})
                conn.execute(
                    """
                    UPDATE supervised_idempotency_records
                    SET status = 'COMMITTED',
                        result_json = ?,
                        updated_at = ?
                    WHERE token = ?
                    """,
                    (res_json, now, token),
                )
            finally:
                conn.close()

    # ── Checkpointing & Reconstitution (Simulated Process Crash) ─────────────

    def create_checkpoint(
        self,
        task_id: str,
        boundary_type: str,
        graph: ExecutionGraph,
        step_id: Optional[str] = None,
        budget: Optional[Any] = None,
    ) -> ExecutionCheckpoint:
        """Capture and persist durable boundary checkpoint."""
        checkpoint_id = f"chk_{uuid.uuid4().hex[:12]}"
        now = time.time()

        graph_state = {
            "plan_id": graph.plan.plan_id,
            "revision_count": graph.plan.revision_count,
            "status": graph.plan.status.value,
            "steps": {s_id: s.as_dict() for s_id, s in graph.plan.steps.items()},
        }
        graph_json = json.dumps(graph_state)

        budget_state = {}
        if budget is not None:
            if hasattr(budget, "as_dict"):
                budget_state = budget.as_dict()
            elif isinstance(budget, dict):
                budget_state = budget
        budget_json = json.dumps(budget_state)

        with self._lock:
            conn = self.store.get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO supervised_checkpoints (
                        checkpoint_id, task_id, graph_id, revision_id, boundary_type, step_id, graph_state_json, budget_state_json, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        checkpoint_id,
                        task_id,
                        graph.plan.plan_id,
                        graph.plan.revision_count,
                        boundary_type,
                        step_id,
                        graph_json,
                        budget_json,
                        now,
                    ),
                )
            finally:
                conn.close()

            chk = ExecutionCheckpoint(
                checkpoint_id=checkpoint_id,
                task_id=task_id,
                graph_id=graph.plan.plan_id,
                revision_id=graph.plan.revision_count,
                boundary_type=boundary_type,
                step_id=step_id,
                graph_state_json=graph_json,
                budget_state_json=budget_json,
                timestamp=now,
            )

            self._emit_event("checkpoint.created", {
                "checkpoint_id": checkpoint_id,
                "task_id": task_id,
                "boundary_type": boundary_type,
                "step_id": step_id,
            })

            return chk

    def load_latest_checkpoint(self, task_id: str) -> Optional[ExecutionCheckpoint]:
        """Fetch the most recent durable checkpoint for a task."""
        with self._lock:
            conn = self.store.get_connection()
            try:
                cur = conn.execute(
                    """
                    SELECT * FROM supervised_checkpoints
                    WHERE task_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 1
                    """,
                    (task_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return ExecutionCheckpoint(
                    checkpoint_id=row["checkpoint_id"],
                    task_id=row["task_id"],
                    graph_id=row["graph_id"],
                    revision_id=row["revision_id"],
                    boundary_type=row["boundary_type"],
                    step_id=row["step_id"],
                    graph_state_json=row["graph_state_json"],
                    budget_state_json=row["budget_state_json"],
                    timestamp=row["timestamp"],
                )
            finally:
                conn.close()

    def reconstitute_execution(
        self,
        task_id: str,
        plan_fallback: Optional[ExecutionPlan] = None,
    ) -> Tuple[ExecutionGraph, Optional[ExecutionCheckpoint]]:
        """
        Reconstruct execution graph and durable state after simulated process crash or restart.
        Restores completed step history, checks for orphaned in-flight steps, and resumes.
        Emits 'execution.resumed'.
        """
        with self._lock:
            chk = self.load_latest_checkpoint(task_id)
            if chk:
                g_data = json.loads(chk.graph_state_json)
                steps_dict = {
                    s_id: PlanStep.from_dict(s_data)
                    for s_id, s_data in g_data.get("steps", {}).items()
                }
                plan = ExecutionPlan(
                    plan_id=g_data.get("plan_id", f"plan_{task_id}"),
                    task_id=task_id,
                    tenant_id="reconstituted",
                    objective="Reconstituted plan",
                    steps=steps_dict,
                    revision=int(g_data.get("revision", g_data.get("revision_count", 1))),
                    status=PlanStatus(g_data.get("status", "EXECUTING")),
                )
            elif plan_fallback:
                plan = plan_fallback
            else:
                raise DurableSupervisorError(f"No checkpoint or fallback available to reconstitute task '{task_id}'.")

            # Reconcile with latest step states in store
            db_steps = self.store.list_steps(task_id)
            for db_s in db_steps:
                if db_s.step_id in plan.steps:
                    p_step = plan.steps[db_s.step_id]
                    try:
                        p_step.status = PlanStepStatus(db_s.status)
                    except ValueError:
                        pass
                    p_step.worker_id = db_s.worker_id
                    p_step.lease_id = db_s.lease_id
                    p_step.lease_expires = db_s.lease_expires
                    p_step.heartbeat_at = db_s.heartbeat_at
                    p_step.retries_attempted = max(0, db_s.attempt_number - 1)
                    if db_s.result_json:
                        try:
                            p_step.result = json.loads(db_s.result_json)
                        except Exception:
                            pass
                    if db_s.error_message:
                        p_step.error_message = db_s.error_message

            graph = ExecutionGraph(plan)
            self._active_graphs[task_id] = graph

            self._emit_event("execution.resumed", {
                "task_id": task_id,
                "checkpoint_id": chk.checkpoint_id if chk else None,
                "step_count": len(plan.steps),
                "completed_steps": [s.step_id for s in plan.steps.values() if s.status == PlanStepStatus.SUCCEEDED],
            })

            return graph, chk

    # ── Task Cancellation (Cancel-Wins Semantics) ────────────────────────────

    def cancel_task(self, task_id: str, reason: str = "User cancelled execution") -> None:
        """
        Authoritative task cancellation enforcing cancel-wins semantics.
        Marks task as CANCELLED and transitions all non-terminal steps to CANCELLED.
        Subsequent late commits or recovery attempts are strictly rejected.
        """
        scrubbed_reason = scrub_secrets(reason)
        curr_time = time.time()
        with self._lock:
            # 1. Update task status in store
            conn = self.store.get_connection()
            try:
                conn.execute(
                    """
                    UPDATE supervised_tasks
                    SET status = 'CANCELLED',
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (curr_time, task_id),
                )
                # 2. Cancel non-terminal steps
                conn.execute(
                    """
                    UPDATE supervised_steps
                    SET status = 'CANCELLED',
                        error_message = ?,
                        version = version + 1,
                        updated_at = ?
                    WHERE task_id = ? AND status NOT IN ('SUCCEEDED', 'CANCELLED', 'SKIPPED')
                    """,
                    (scrubbed_reason, curr_time, task_id),
                )
            finally:
                conn.close()

            # 3. Update active graph
            graph = self._active_graphs.get(task_id)
            if graph:
                graph.plan.status = PlanStatus.CANCELLED
                for step in graph.plan.steps.values():
                    if step.status not in (PlanStepStatus.SUCCEEDED, PlanStepStatus.CANCELLED, PlanStepStatus.SKIPPED):
                        step.status = PlanStepStatus.CANCELLED
                        step.error_message = scrubbed_reason

            self._emit_event("task.cancelled", {
                "task_id": task_id,
                "reason": scrubbed_reason,
            })
