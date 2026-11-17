"""
backend/execution/fabric/models.py — Phase 21 Execution Fabric Data Models.

Defines queue items, worker records, scheduling decisions, retry safety classes,
and domain exceptions for the durable execution fabric and worker control plane.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
import json
import time
from typing import Any, Dict, List, Optional, Set
import uuid

from ..orchestration.engineering_memory import scrub_secrets


# ── Domain Exceptions ─────────────────────────────────────────────────────────

class FabricError(Exception):
    """Base error for execution fabric operations."""


class QueueEmptyError(FabricError):
    """Raised when an operation expected a queue item but queue was empty."""


class WorkerNotFoundError(FabricError):
    """Raised when a referenced worker does not exist."""


class WorkerCapacityExceededError(FabricError):
    """Raised when work is assigned to a worker whose capacity is full."""


class StaleWorkerError(FabricError):
    """Raised when a worker with stale heartbeat attempts to claim or commit work."""


class QuotaExceededError(FabricError):
    """Raised when a tenant or task exceeds allowed concurrency quotas."""


class CapabilityMismatchError(FabricError):
    """Raised when no registered worker supports the required capabilities."""


class SchedulerError(FabricError):
    """Raised on internal scheduler conflicts or validation failures."""


# ── Queue State & Models ─────────────────────────────────────────────────────

class QueueItemStatus(str, Enum):
    """Durable queue item lifecycle states."""
    QUEUED = "QUEUED"
    DISPATCHING = "DISPATCHING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    REQUEUED = "REQUEUED"
    CANCELLED = "CANCELLED"


@dataclass
class QueueItem:
    """Durable queue item representing a unit of executable work."""
    item_id: str
    task_id: str
    tenant_id: str
    step_id: Optional[str] = None
    priority: int = 50
    enqueued_at: float = field(default_factory=time.time)
    attempt: int = 1
    execution_class: str = "DEFAULT"
    deadline: Optional[float] = None
    required_capabilities: List[str] = field(default_factory=list)
    status: QueueItemStatus = QueueItemStatus.QUEUED
    assigned_worker_id: Optional[str] = None
    lease_id: Optional[str] = None
    dispatched_at: Optional[float] = None
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now: Optional[float] = None) -> bool:
        t = now if now is not None else time.time()
        return self.deadline is not None and t > self.deadline

    def as_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "tenant_id": self.tenant_id,
            "priority": self.priority,
            "enqueued_at": self.enqueued_at,
            "attempt": self.attempt,
            "execution_class": self.execution_class,
            "deadline": self.deadline,
            "required_capabilities": list(self.required_capabilities),
            "status": self.status.value if isinstance(self.status, QueueItemStatus) else str(self.status),
            "assigned_worker_id": self.assigned_worker_id,
            "lease_id": self.lease_id,
            "dispatched_at": self.dispatched_at,
            "completed_at": self.completed_at,
            "error_message": scrub_secrets(self.error_message) if self.error_message else None,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> QueueItem:
        d = dict(data)
        st = d.get("status", QueueItemStatus.QUEUED)
        if isinstance(st, str):
            try:
                st = QueueItemStatus(st.upper())
            except ValueError:
                st = QueueItemStatus.QUEUED

        return cls(
            item_id=d["item_id"],
            task_id=d["task_id"],
            tenant_id=d["tenant_id"],
            step_id=d.get("step_id"),
            priority=int(d.get("priority", 50)),
            enqueued_at=float(d.get("enqueued_at", time.time())),
            attempt=int(d.get("attempt", 1)),
            execution_class=d.get("execution_class", "DEFAULT"),
            deadline=d.get("deadline"),
            required_capabilities=list(d.get("required_capabilities", [])),
            status=st,
            assigned_worker_id=d.get("assigned_worker_id"),
            lease_id=d.get("lease_id"),
            dispatched_at=d.get("dispatched_at"),
            completed_at=d.get("completed_at"),
            error_message=d.get("error_message"),
            payload=d.get("payload", {}),
        )


# ── Worker Status & Models ───────────────────────────────────────────────────

class WorkerStatus(str, Enum):
    """Lifecycle statuses for an execution worker."""
    STARTING = "STARTING"
    READY = "READY"
    BUSY = "BUSY"
    DRAINING = "DRAINING"
    STOPPED = "STOPPED"
    UNHEALTHY = "UNHEALTHY"


@dataclass
class WorkerRecord:
    """Authoritative registry record for an execution worker."""
    worker_id: str
    session_id: str
    process_id: int
    max_concurrency: int = 4
    active_concurrency: int = 0
    active_leases: List[str] = field(default_factory=list)
    supported_capabilities: Set[str] = field(default_factory=set)
    status: WorkerStatus = WorkerStatus.READY
    start_time: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    drain_state: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def has_capacity(self) -> bool:
        """True if worker is accepting work and concurrency slot is free."""
        if self.drain_state or self.status not in (WorkerStatus.READY, WorkerStatus.BUSY):
            return False
        return self.active_concurrency < self.max_concurrency

    def available_slots(self) -> int:
        """Number of open execution slots."""
        if self.drain_state or self.status not in (WorkerStatus.READY, WorkerStatus.BUSY):
            return 0
        return max(0, self.max_concurrency - self.active_concurrency)

    def supports_capabilities(self, required: List[str]) -> bool:
        """True if worker possesses all required capabilities."""
        if not required:
            return True
        return set(required).issubset(self.supported_capabilities)

    def is_healthy(self, timeout_sec: float = 30.0, now: Optional[float] = None) -> bool:
        """True if worker has renewed heartbeat within timeout window."""
        t = now if now is not None else time.time()
        return (t - self.last_heartbeat) <= timeout_sec

    def as_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "session_id": self.session_id,
            "process_id": self.process_id,
            "max_concurrency": self.max_concurrency,
            "active_concurrency": self.active_concurrency,
            "available_slots": self.available_slots(),
            "active_leases": list(self.active_leases),
            "supported_capabilities": list(self.supported_capabilities),
            "status": self.status.value if isinstance(self.status, WorkerStatus) else str(self.status),
            "start_time": self.start_time,
            "last_heartbeat": self.last_heartbeat,
            "drain_state": self.drain_state,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WorkerRecord:
        d = dict(data)
        st = d.get("status", WorkerStatus.READY)
        if isinstance(st, str):
            try:
                st = WorkerStatus(st.upper())
            except ValueError:
                st = WorkerStatus.READY

        return cls(
            worker_id=d["worker_id"],
            session_id=d.get("session_id", f"sess_{uuid.uuid4().hex[:8]}"),
            process_id=int(d.get("process_id", 0)),
            max_concurrency=int(d.get("max_concurrency", 4)),
            active_concurrency=int(d.get("active_concurrency", 0)),
            active_leases=list(d.get("active_leases", [])),
            supported_capabilities=set(d.get("supported_capabilities", [])),
            status=st,
            start_time=float(d.get("start_time", time.time())),
            last_heartbeat=float(d.get("last_heartbeat", time.time())),
            drain_state=bool(d.get("drain_state", False)),
            metadata=d.get("metadata", {}),
        )


# ── Scheduling Verdicts & Decisions ──────────────────────────────────────────

class SchedulingVerdict(str, Enum):
    """Deterministic scheduler evaluation outcomes."""
    DISPATCHED = "DISPATCHED"
    QUEUED_BACKPRESSURE = "QUEUED_BACKPRESSURE"
    EXPIRED = "EXPIRED"
    TENANT_QUOTA_EXCEEDED = "TENANT_QUOTA_EXCEEDED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    BLOCKED_DEPENDENCIES = "BLOCKED_DEPENDENCIES"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"


@dataclass
class SchedulingDecision:
    """Audit record capturing an authoritative scheduling decision."""
    decision_id: str
    item_id: str
    task_id: str
    tenant_id: str
    verdict: SchedulingVerdict
    reason: str
    assigned_worker_id: Optional[str] = None
    step_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "item_id": self.item_id,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "step_id": self.step_id,
            "verdict": self.verdict.value,
            "reason": scrub_secrets(self.reason),
            "assigned_worker_id": self.assigned_worker_id,
            "timestamp": self.timestamp,
        }


# ── Retry Safety Classification ──────────────────────────────────────────────

class RetrySafetyClass(str, Enum):
    """Categorization of execution safety under worker retry or crash recovery."""
    SAFE_RETRY = "SAFE_RETRY"
    IDEMPOTENT = "IDEMPOTENT"
    CONDITIONAL_RETRY = "CONDITIONAL_RETRY"
    FAIL_CLOSED = "FAIL_CLOSED"


class FailureCategory(str, Enum):
    """Explicit failure classification taxonomy for recovery behavior."""
    SAFE_IDEMPOTENT = "SAFE_IDEMPOTENT"
    CONDITIONAL_RETRY = "CONDITIONAL_RETRY"
    NON_IDEMPOTENT = "NON_IDEMPOTENT"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    HITL_PENDING = "HITL_PENDING"

