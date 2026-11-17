"""
backend/execution/fabric/rolling_lifecycle.py — Phase 24 Worker Rolling Lifecycle Controller.

Orchestrates zero-downtime rolling worker replacement:
  OLD WORKER -> DRAIN -> NO NEW CLAIMS -> FINISH IN-FLIGHT SAFE WORK -> STOP
  NEW WORKER -> PREFLIGHT -> REGISTER -> READY -> CLAIM PENDING WORK

Invariants Enforced:
  - Zero duplicate task ownership / step lease collisions.
  - No unsafe continuation of work by drained or stale workers.
  - Preserved HITL gates, verifier validation, and RunBudget limits across handover.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .coordination import FabricCoordinator
from .models import (
    FabricError,
    QueueItem,
    QueueItemStatus,
    WorkerRecord,
    WorkerStatus,
)
from .service import ExecutionFabricService
from .worker_runtime import WorkerRuntime

logger = logging.getLogger("aetherion.fabric.rolling_lifecycle")


class RollingHandoverError(FabricError):
    """Raised on failure or safety invariant breach during worker rolling replacement."""
    pass


@dataclass
class RollingHandoverReport:
    """Audit report of a rolling worker replacement lifecycle."""
    retiring_worker_id: str
    incoming_worker_id: str
    success: bool
    drain_initiated_at: float
    handover_completed_at: Optional[float] = None
    items_completed_by_retiring: int = 0
    items_claimed_by_incoming: int = 0
    duplicate_claims_detected: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "retiring_worker_id": self.retiring_worker_id,
            "incoming_worker_id": self.incoming_worker_id,
            "success": self.success,
            "drain_initiated_at": self.drain_initiated_at,
            "handover_completed_at": self.handover_completed_at,
            "items_completed_by_retiring": self.items_completed_by_retiring,
            "items_claimed_by_incoming": self.items_claimed_by_incoming,
            "duplicate_claims_detected": self.duplicate_claims_detected,
            "errors": list(self.errors),
        }


class RollingReplacementController:
    """
    Manages safe, controlled rolling replacement of worker runtimes without
    dropping queued work or creating split-brain multi-worker ownership.
    """

    def __init__(
        self,
        service: ExecutionFabricService,
        coordinator: Optional[FabricCoordinator] = None,
    ) -> None:
        self.service = service
        self.store = service.store
        self.coordinator = coordinator or FabricCoordinator()

    def execute_rolling_handover(
        self,
        retiring_worker: WorkerRuntime,
        incoming_worker: WorkerRuntime,
        drain_timeout_sec: float = 30.0,
    ) -> RollingHandoverReport:
        """
        Execute deterministic rolling replacement from retiring_worker to incoming_worker.
        """
        t0 = time.time()
        report = RollingHandoverReport(
            retiring_worker_id=retiring_worker.worker_id,
            incoming_worker_id=incoming_worker.worker_id,
            success=False,
            drain_initiated_at=t0,
        )

        logger.info(
            "Starting rolling replacement: retiring=%s -> incoming=%s",
            retiring_worker.worker_id,
            incoming_worker.worker_id,
        )

        try:
            # 1. Start & register incoming worker first
            if not incoming_worker.is_running:
                incoming_worker.start()
            incoming_worker.register()

            # Verify incoming worker registered and is READY
            rec = self.store.get_worker(incoming_worker.worker_id)
            if not rec or getattr(rec.status, "value", str(rec.status)) != WorkerStatus.READY.value:
                raise RollingHandoverError(
                    f"Incoming worker '{incoming_worker.worker_id}' failed to reach READY status."
                )

            # 2. Signal retiring worker to DRAIN
            # Retiring worker immediately stops accepting new claims
            retiring_worker.drain(wait=False)

            if not retiring_worker.is_draining:
                raise RollingHandoverError(
                    f"Retiring worker '{retiring_worker.worker_id}' failed to enter draining state."
                )

            # Wake up coordinator so incoming worker can claim newly available tasks
            self.coordinator.notify_work_available()

            # 3. Wait for retiring worker to complete active in-flight tasks
            deadline = time.monotonic() + drain_timeout_sec
            while time.monotonic() < deadline:
                if retiring_worker.active_concurrency == 0:
                    break
                time.sleep(0.1)

            # 4. Stop retiring worker
            if retiring_worker.active_concurrency == 0:
                retiring_worker.stop(reason="Rolling replacement complete")
                logger.info("Retiring worker %s cleanly drained and stopped.", retiring_worker.worker_id)
            else:
                logger.warning(
                    "Retiring worker %s drain timed out with %d active tasks remaining; stopping forcefully.",
                    retiring_worker.worker_id,
                    retiring_worker.active_concurrency,
                )
                retiring_worker.stop(reason="Drain timeout during rolling replacement")
                # Trigger orphan recovery for any abandoned leases
                recov = self.service.recover_orphans()
                logger.info("Orphan recovery during rolling handover: %s", recov)

            # 5. Verify invariant: Zero duplicate claims
            # Inspect queue items in store to ensure no two workers own the same step
            items = self.store.list_queue_items(limit=200)
            seen_step_leases: Dict[str, str] = {}
            duplicates = 0
            for item in items:
                if item.step_id and item.status in (QueueItemStatus.CLAIMED, QueueItemStatus.RUNNING):
                    if item.step_id in seen_step_leases:
                        duplicates += 1
                        report.errors.append(
                            f"Step lease collision on step '{item.step_id}': owned by '{item.assigned_worker_id}' and '{seen_step_leases[item.step_id]}'"
                        )
                    seen_step_leases[item.step_id] = item.assigned_worker_id or "unknown"

            report.duplicate_claims_detected = duplicates
            report.items_completed_by_retiring = retiring_worker.items_succeeded
            report.items_claimed_by_incoming = incoming_worker.items_processed
            report.handover_completed_at = time.time()
            report.success = len(report.errors) == 0

            return report

        except Exception as exc:
            report.errors.append(str(exc))
            report.success = False
            logger.error("Rolling replacement error: %s", exc)
            return report
