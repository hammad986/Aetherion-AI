"""
backend/execution/fabric/service.py — Phase 21 Execution Fabric Orchestration Service.

Unified service orchestrating durable task queuing, worker control plane,
priority/fair scheduling, atomic Phase 20 step leases, side-effect safety,
and crash recovery.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid

from ..orchestration.durable_supervisor import RecoveryDecision
from ..orchestration.engineering_memory import scrub_secrets
from .models import (
    CapabilityMismatchError,
    FabricError,
    FailureCategory,
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
from .queue import DurableTaskQueue
from .retry_policy import (
    classify_failure,
    evaluate_retry_safety,
    map_failure_category_to_recovery,
    map_safety_to_recovery_decision,
)
from .scheduler import ExecutionScheduler, SchedulerQuotaPolicy
from .store import FabricStore
from .worker_registry import WorkerRegistry

logger = logging.getLogger("aetherion.fabric.service")


class ExecutionFabricService:
    """
    Unified execution fabric service connecting queue, workers, scheduler,
    and durable supervisor.
    """

    def __init__(
        self,
        store: Optional[FabricStore] = None,
        supervisor: Optional[Any] = None,
        quota_policy: Optional[SchedulerQuotaPolicy] = None,
        heartbeat_timeout_sec: float = 30.0,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.store = store or FabricStore()
        self.supervisor = supervisor
        self.quota_policy = quota_policy or SchedulerQuotaPolicy()
        self.heartbeat_timeout_sec = heartbeat_timeout_sec
        self.event_callback = event_callback

        self.queue = DurableTaskQueue(store=self.store, event_callback=event_callback)
        self.worker_registry = WorkerRegistry(
            store=self.store,
            heartbeat_timeout_sec=heartbeat_timeout_sec,
            event_callback=event_callback,
        )
        self.scheduler = ExecutionScheduler(
            queue=self.queue,
            worker_registry=self.worker_registry,
            supervisor=self.supervisor,
            quota_policy=self.quota_policy,
            event_callback=event_callback,
        )
        self._lock = threading.RLock()

    # ── Task & Step Submission ───────────────────────────────────────────────

    def submit_task(
        self,
        task_id: str,
        tenant_id: str,
        step_id: Optional[str] = None,
        priority: int = 50,
        execution_class: str = "DEFAULT",
        deadline: Optional[float] = None,
        required_capabilities: Optional[List[str]] = None,
        payload: Optional[Dict[str, Any]] = None,
        enqueued_at: Optional[float] = None,
    ) -> QueueItem:
        """Enqueue an individual executable unit of work."""
        item = self.queue.enqueue(
            task_id=task_id,
            tenant_id=tenant_id,
            step_id=step_id,
            priority=priority,
            execution_class=execution_class,
            deadline=deadline,
            required_capabilities=required_capabilities,
            payload=payload,
            enqueued_at=enqueued_at,
        )
        try:
            from .metrics import get_metrics_collector
            get_metrics_collector().record_task_submitted(task_id=task_id, tenant_id=tenant_id, priority=priority)
        except Exception:
            pass
        return item

    def enqueue_graph_steps(
        self,
        task_id: str,
        tenant_id: str,
        graph: Any,
        default_priority: int = 50,
        deadline: Optional[float] = None,
    ) -> List[QueueItem]:
        """
        Walk execution graph and enqueue all steps into durable queue.
        Dependencies are preserved and evaluated dynamically by the scheduler.
        """
        items: List[QueueItem] = []
        steps = getattr(graph, "plan", graph).steps.values()
        for step in steps:
            req_caps = list(step.allowed_skills or [])
            if not req_caps:
                req_caps = ["tool_execution", "sandbox"]

            item_payload = {
                "step_id": step.step_id,
                "objective": step.objective,
                "dependencies": list(step.dependencies),
                "risk_level": getattr(step.risk_level, "value", str(step.risk_level)),
                "requires_hitl": step.requires_hitl,
                "allowed_tools": list(step.allowed_tools or []),
                "max_retries": step.max_retries,
            }

            q_item = self.queue.enqueue(
                task_id=task_id,
                tenant_id=tenant_id,
                step_id=step.step_id,
                priority=default_priority,
                deadline=deadline,
                required_capabilities=req_caps,
                payload=item_payload,
            )
            items.append(q_item)

        return items

    # ── Worker Control Plane ─────────────────────────────────────────────────

    def register_worker(
        self,
        worker_id: str,
        session_id: Optional[str] = None,
        process_id: Optional[int] = None,
        max_concurrency: int = 4,
        supported_capabilities: Optional[Set[str] | List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> WorkerRecord:
        """Register a worker in the control plane."""
        return self.worker_registry.register_worker(
            worker_id=worker_id,
            session_id=session_id,
            process_id=process_id,
            max_concurrency=max_concurrency,
            supported_capabilities=supported_capabilities,
            metadata=metadata,
            now=now,
        )

    def worker_heartbeat(
        self,
        worker_id: str,
        now: Optional[float] = None,
    ) -> WorkerRecord:
        """Record worker heartbeat."""
        return self.worker_registry.heartbeat(worker_id=worker_id, now=now)

    def drain_worker(self, worker_id: str) -> WorkerRecord:
        """Initiate graceful drain for a worker."""
        return self.worker_registry.drain_worker(worker_id=worker_id)

    def stop_worker(self, worker_id: str, reason: str = "Explicit stop") -> WorkerRecord:
        """Mark worker as stopped."""
        return self.worker_registry.stop_worker(worker_id=worker_id, reason=reason)

    def get_worker(self, worker_id: str) -> Optional[WorkerRecord]:
        return self.worker_registry.get_worker(worker_id)

    def list_workers(self) -> List[WorkerRecord]:
        return self.worker_registry.list_all_workers()

    # ── Scheduler Ticks & Item Execution ─────────────────────────────────────

    def tick_scheduler(
        self,
        limit: int = 20,
        now: Optional[float] = None,
    ) -> List[SchedulingDecision]:
        """Run one scheduler evaluation cycle."""
        return self.scheduler.schedule_cycle(limit=limit, now=now)

    def execute_claimed_item(
        self,
        item_id: str,
        worker_id: str,
        executor_fn: Optional[Callable[[QueueItem], Any]] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Execute a claimed queue item on behalf of an assigned worker.
        Handles status updates, supervisor lifecycle, retry safety categorization,
        and capacity release.
        """
        item = self.queue.get_item(item_id)
        if not item:
            raise FabricError(f"Item '{item_id}' not found.")
        if item.assigned_worker_id != worker_id:
            raise FabricError(
                f"Worker '{worker_id}' is not the assigned owner of item '{item_id}' (owned by '{item.assigned_worker_id}')."
            )

        curr_time = now if now is not None else time.time()
        self.worker_registry.heartbeat(worker_id, now=curr_time)
        self.queue.mark_running(item_id, worker_id=worker_id)

        # Notify supervisor if present
        if self.supervisor and item.step_id and item.lease_id:
            try:
                self.supervisor.start_step(
                    task_id=item.task_id,
                    step_id=item.step_id,
                    worker_id=worker_id,
                    lease_id=item.lease_id,
                    now=curr_time,
                )
            except Exception as start_exc:
                logger.debug("Supervisor start_step note: %s", start_exc)

        # Execute payload or callback
        result = None
        exec_error = None
        try:
            if executor_fn:
                result = executor_fn(item)
            else:
                result = {"status": "SUCCESS", "message": "Executed default task handler"}
        except Exception as exc:
            exec_error = exc

        # Success path
        if exec_error is None:
            self.queue.mark_completed(item_id, worker_id=worker_id)
            if self.supervisor and item.step_id and item.lease_id:
                try:
                    self.supervisor.complete_step(
                        task_id=item.task_id,
                        step_id=item.step_id,
                        worker_id=worker_id,
                        lease_id=item.lease_id,
                        result=result if isinstance(result, dict) else {"output": str(result)},
                        now=curr_time,
                    )
                except Exception as comp_exc:
                    logger.debug("Supervisor complete_step note: %s", comp_exc)

            self.worker_registry.release_capacity(worker_id, item.lease_id)
            return {
                "ok": True,
                "status": "COMPLETED",
                "item_id": item_id,
                "result": result,
            }

        # Failure / retry safety analysis path
        error_msg = str(exec_error)
        tool_names = item.payload.get("allowed_tools") or []
        has_mutations = bool(item.payload.get("has_unverified_mutations", False))
        is_idempotent = bool(item.payload.get("is_idempotent", False))
        risk_level = item.payload.get("risk_level", "LOW")
        requires_hitl = bool(item.payload.get("requires_hitl", False))
        max_retries = int(item.payload.get("max_retries", 3))

        safety = evaluate_retry_safety(
            tool_names=tool_names,
            has_unverified_mutations=has_mutations,
            is_idempotent=is_idempotent,
            risk_level=risk_level,
            requires_hitl=requires_hitl,
        )

        recovery_decision = map_safety_to_recovery_decision(
            safety=safety,
            remaining_retries=max_retries - item.attempt,
        )

        self.worker_registry.release_capacity(worker_id, item.lease_id)

        if recovery_decision == RecoveryDecision.RETRY and item.attempt < max_retries:
            requeued_item = self.queue.requeue(item_id, reason=error_msg)
            if self.supervisor and item.step_id and item.lease_id:
                try:
                    self.supervisor.fail_step(
                        task_id=item.task_id,
                        step_id=item.step_id,
                        worker_id=worker_id,
                        lease_id=item.lease_id,
                        error_message=error_msg,
                        now=curr_time,
                    )
                except Exception as fail_exc:
                    logger.debug("Supervisor fail_step note: %s", fail_exc)

            return {
                "ok": False,
                "status": "REQUEUED",
                "item_id": item_id,
                "attempt": requeued_item.attempt,
                "error": error_msg,
                "safety_class": safety.value,
            }
        else:
            self.queue.mark_failed(item_id, error_msg, worker_id=worker_id)
            if self.supervisor and item.step_id and item.lease_id:
                try:
                    self.supervisor.fail_step(
                        task_id=item.task_id,
                        step_id=item.step_id,
                        worker_id=worker_id,
                        lease_id=item.lease_id,
                        error_message=error_msg,
                        now=curr_time,
                    )
                except Exception as fail_exc:
                    logger.debug("Supervisor fail_step note: %s", fail_exc)

            return {
                "ok": False,
                "status": "FAILED",
                "item_id": item_id,
                "error": error_msg,
                "safety_class": safety.value,
            }

    # ── Crash & Orphan Recovery ──────────────────────────────────────────────

    def recover_orphans(self, now: Optional[float] = None) -> Dict[str, Any]:
        """
        Comprehensive crash and orphan recovery across workers, queue, and supervisor:
        1. Detects unhealthy workers whose heartbeat timed out.
        2. Reclaims or fails in-flight items assigned to dead workers based on retry safety.
        3. Invokes Phase 20 supervisor orphan detection and recovery.
        """
        curr_time = now if now is not None else time.time()
        recovery_summary: Dict[str, Any] = {
            "unhealthy_workers": [],
            "requeued_items": [],
            "failed_items": [],
            "supervisor_orphans": [],
        }

        with self._lock:
            # 1. Unhealthy workers
            dead_workers = self.worker_registry.detect_unhealthy_workers(now=curr_time)
            recovery_summary["unhealthy_workers"] = [w.worker_id for w in dead_workers]

            # 2. Reclaim active queue items assigned to dead workers
            for worker in dead_workers:
                active_items = self.store.list_queue_items(limit=200)
                for item in active_items:
                    if item.assigned_worker_id == worker.worker_id and item.status in (
                        QueueItemStatus.DISPATCHING,
                        QueueItemStatus.CLAIMED,
                        QueueItemStatus.RUNNING,
                    ):
                        tool_names = item.payload.get("allowed_tools") or []
                        has_mutations = bool(item.payload.get("has_unverified_mutations", False))
                        is_idempotent = bool(item.payload.get("is_idempotent", False))
                        risk_level = item.payload.get("risk_level", "LOW")
                        requires_hitl = bool(item.payload.get("requires_hitl", False))
                        hitl_pending = bool(item.payload.get("hitl_pending", False)) or requires_hitl
                        verification_pending = bool(item.payload.get("verification_pending", False))
                        unknown_state = bool(item.payload.get("unknown_state", False))
                        budget_exhausted = bool(item.payload.get("budget_exhausted", False))
                        max_retries = int(item.payload.get("max_retries", 3))

                        category = classify_failure(
                            tool_names=tool_names,
                            has_unverified_mutations=has_mutations,
                            is_idempotent=is_idempotent,
                            risk_level=risk_level,
                            requires_hitl=requires_hitl,
                            hitl_pending=hitl_pending,
                            verification_pending=verification_pending,
                            unknown_state=unknown_state,
                        )

                        decision = map_failure_category_to_recovery(
                            category=category,
                            remaining_retries=max_retries - item.attempt,
                            budget_exhausted=budget_exhausted,
                            verifier_passed=False,
                        )

                        if decision == RecoveryDecision.RETRY and item.attempt < max_retries:
                            self.queue.requeue(
                                item.item_id,
                                reason=f"Worker '{worker.worker_id}' heartbeat timed out (crash recovery - {category.value})",
                            )
                            recovery_summary["requeued_items"].append(item.item_id)
                        elif decision == RecoveryDecision.WAIT:
                            recovery_summary.setdefault("waiting_items", []).append(item.item_id)
                        else:
                            self.queue.mark_failed(
                                item.item_id,
                                f"Worker '{worker.worker_id}' died during unverified, unknown, or non-idempotent operation ({category.value})",
                            )
                            recovery_summary["failed_items"].append(item.item_id)

            # 3. Phase 20 supervisor orphan detection
            if self.supervisor:
                try:
                    orphaned_steps = self.supervisor.detect_orphans(now=curr_time)
                    for step_rec in orphaned_steps:
                        recov = self.supervisor.evaluate_and_recover_orphan(
                            task_id=step_rec.task_id,
                            step_id=step_rec.step_id,
                            now=curr_time,
                        )
                        recovery_summary["supervisor_orphans"].append({
                            "task_id": step_rec.task_id,
                            "step_id": step_rec.step_id,
                            "decision": recov.decision.value if hasattr(recov, "decision") else str(recov),
                        })
                except Exception as sup_orphan_exc:
                    logger.debug("Supervisor orphan detection error: %s", sup_orphan_exc)

        return recovery_summary

    # ── Observability & Diagnostics ──────────────────────────────────────────

    def get_metrics(self) -> Dict[str, Any]:
        """Aggregate operational metrics across queue, workers, and scheduler."""
        all_workers = self.worker_registry.list_all_workers()
        total_capacity = sum(w.max_concurrency for w in all_workers if not w.drain_state)
        active_concurrency = sum(w.active_concurrency for w in all_workers)
        available_slots = sum(w.available_slots() for w in all_workers)

        return {
            "queue_depth": self.queue.get_queue_depth(),
            "total_workers": len(all_workers),
            "workers_ready": sum(1 for w in all_workers if w.status == WorkerStatus.READY),
            "workers_busy": sum(1 for w in all_workers if w.status == WorkerStatus.BUSY),
            "workers_draining": sum(1 for w in all_workers if w.status == WorkerStatus.DRAINING),
            "workers_stopped": sum(1 for w in all_workers if w.status == WorkerStatus.STOPPED),
            "workers_unhealthy": sum(1 for w in all_workers if w.status == WorkerStatus.UNHEALTHY),
            "total_capacity": total_capacity,
            "active_concurrency": active_concurrency,
            "available_slots": available_slots,
            "global_active_items": self.queue.count_global_active_items(),
        }
