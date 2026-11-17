"""
backend/execution/fabric/__init__.py — Phase 21 Execution Fabric & Worker Control Plane.

Public exports for durable queue, worker registry, scheduler, retry safety policy,
and fabric service.
"""

from .coordination import FabricCoordinator
from .models import (
    CapabilityMismatchError,
    FabricError,
    QueueEmptyError,
    QueueItem,
    QueueItemStatus,
    QuotaExceededError,
    RetrySafetyClass,
    SchedulerError,
    SchedulingDecision,
    SchedulingVerdict,
    StaleWorkerError,
    WorkerCapacityExceededError,
    WorkerNotFoundError,
    WorkerRecord,
    WorkerStatus,
)
from .postgres_store import PostgresFabricStore
from .queue import DurableTaskQueue
from .retry_policy import (
    DANGEROUS_SIDE_EFFECT_TOOLS,
    SAFE_READ_TOOLS,
    evaluate_retry_safety,
    map_safety_to_recovery_decision,
)
from .scheduler import ExecutionScheduler, SchedulerQuotaPolicy
from .service import ExecutionFabricService
from .store import BaseFabricStore, FabricStore, SqliteFabricStore, get_fabric_store
from .worker_registry import WorkerRegistry
from .worker_runtime import WorkerRuntime

__all__ = [
    # Models & Enums
    "QueueItem",
    "QueueItemStatus",
    "WorkerRecord",
    "WorkerStatus",
    "SchedulingVerdict",
    "SchedulingDecision",
    "RetrySafetyClass",
    # Exceptions
    "FabricError",
    "QueueEmptyError",
    "WorkerNotFoundError",
    "WorkerCapacityExceededError",
    "StaleWorkerError",
    "QuotaExceededError",
    "CapabilityMismatchError",
    "SchedulerError",
    # Core Classes & Persistence
    "BaseFabricStore",
    "SqliteFabricStore",
    "PostgresFabricStore",
    "FabricStore",
    "get_fabric_store",
    "DurableTaskQueue",
    "WorkerRegistry",
    "SchedulerQuotaPolicy",
    "ExecutionScheduler",
    "ExecutionFabricService",
    # Phase 22 Multi-Process Worker & Coordination
    "FabricCoordinator",
    "WorkerRuntime",
    # Retry Safety
    "SAFE_READ_TOOLS",
    "DANGEROUS_SIDE_EFFECT_TOOLS",
    "evaluate_retry_safety",
    "map_safety_to_recovery_decision",
]

