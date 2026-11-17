"""
backend/execution/fabric/scheduler.py — Phase 21 Execution Scheduler & Quota Enforcer.

Authoritative scheduler managing priority dispatching, fairness, multi-tenant quotas,
worker capacity matching, and dependency evaluation against the Phase 20 supervisor.
"""

from __future__ import annotations

from collections import defaultdict, deque
import dataclasses
from dataclasses import dataclass, field
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid

from ..orchestration.engineering_memory import scrub_secrets
from ..orchestration.execution_graph import PlanStepStatus
from .models import (
    CapabilityMismatchError,
    FabricError,
    QueueItem,
    QueueItemStatus,
    QuotaExceededError,
    SchedulerError,
    SchedulingDecision,
    SchedulingVerdict,
    WorkerRecord,
    WorkerStatus,
)
from .queue import DurableTaskQueue
from .worker_registry import WorkerRegistry

logger = logging.getLogger("aetherion.fabric.scheduler")


@dataclass
class SchedulerQuotaPolicy:
    """Concurrency quotas and fairness configuration."""
    max_concurrency_per_tenant: int = 5
    max_global_concurrency: int = 50
    max_concurrency_per_task: int = 2
    fairness_strategy: str = "fair_share"  # "fair_share" or "round_robin"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "max_concurrency_per_tenant": self.max_concurrency_per_tenant,
            "max_global_concurrency": self.max_global_concurrency,
            "max_concurrency_per_task": self.max_concurrency_per_task,
            "fairness_strategy": self.fairness_strategy,
        }


class ExecutionScheduler:
    """
    Authoritative scheduler matching runnable tasks from DurableTaskQueue
    to available workers in WorkerRegistry while strictly enforcing quotas,
    priorities, deadlines, capabilities, and dependency readiness.
    """

    def __init__(
        self,
        queue: DurableTaskQueue,
        worker_registry: WorkerRegistry,
        supervisor: Optional[Any] = None,
        quota_policy: Optional[SchedulerQuotaPolicy] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.queue = queue
        self.worker_registry = worker_registry
        self.supervisor = supervisor
        self.quota_policy = quota_policy or SchedulerQuotaPolicy()
        self.event_callback = event_callback
        self.events: List[Dict[str, Any]] = []
        self._lock = threading.RLock()

    def _emit_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Emit scrubbed typed event to local history and optional listener."""
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
                logger.debug("Scheduler event callback error: %s", cb_exc)

    def schedule_cycle(
        self,
        limit: int = 20,
        now: Optional[float] = None,
    ) -> List[SchedulingDecision]:
        """
        Execute one authoritative scheduling tick:
        1. Expire deadlined items.
        2. Detect unhealthy workers.
        3. Fetch runnable items.
        4. Apply multi-tenant fair grouping.
        5. For each candidate:
           - Check global concurrency.
           - Check tenant concurrency.
           - Check task concurrency.
           - Check step dependency readiness in supervisor.
           - Check budget availability.
           - Match eligible worker with capacity and required capabilities.
           - Dispatch and claim atomically with Phase 20 StepLease.
        """
        curr_time = now if now is not None else time.time()
        decisions: List[SchedulingDecision] = []

        with self._lock:
            # 1. Expire items exceeding deadline
            expired_items = self.queue.expire_deadlines(now=curr_time)
            for exp_item in expired_items:
                dec = SchedulingDecision(
                    decision_id=f"dec_{uuid.uuid4().hex[:12]}",
                    item_id=exp_item.item_id,
                    task_id=exp_item.task_id,
                    tenant_id=exp_item.tenant_id,
                    step_id=exp_item.step_id,
                    verdict=SchedulingVerdict.EXPIRED,
                    reason=f"Deadline exceeded ({exp_item.deadline} < {curr_time})",
                    timestamp=curr_time,
                )
                self.queue.store.record_decision(dec)
                decisions.append(dec)

            # 2. Check worker health
            self.worker_registry.detect_unhealthy_workers(now=curr_time)

            # 3. Peek runnable items
            runnable = self.queue.peek_runnable(limit=limit * 3)
            if not runnable:
                return decisions

            # 4. Multi-tenant fair grouping
            # Group items by tenant, preserving priority and enqueue order within tenant
            tenant_queues: Dict[str, deque[QueueItem]] = defaultdict(deque)
            for item in runnable:
                tenant_queues[item.tenant_id].append(item)

            # Sort tenants by priority of their top item
            tenant_list = sorted(
                tenant_queues.keys(),
                key=lambda t: (-tenant_queues[t][0].priority, tenant_queues[t][0].enqueued_at)
            )

            dispatched_count = 0
            all_workers = self.worker_registry.list_all_workers()

            # 5. Fair round-robin across tenants
            while tenant_list and dispatched_count < limit:
                active_tenants = list(tenant_list)
                progress_made = False

                for tenant_id in active_tenants:
                    if not tenant_queues[tenant_id]:
                        tenant_list.remove(tenant_id)
                        continue

                    # Global concurrency check
                    global_active = self.queue.count_global_active_items()
                    if global_active >= self.quota_policy.max_global_concurrency:
                        item = tenant_queues[tenant_id].popleft()
                        dec = SchedulingDecision(
                            decision_id=f"dec_{uuid.uuid4().hex[:12]}",
                            item_id=item.item_id,
                            task_id=item.task_id,
                            tenant_id=item.tenant_id,
                            step_id=item.step_id,
                            verdict=SchedulingVerdict.QUEUED_BACKPRESSURE,
                            reason=f"Global concurrency limit reached ({global_active}/{self.quota_policy.max_global_concurrency})",
                            timestamp=curr_time,
                        )
                        self.queue.store.record_decision(dec)
                        decisions.append(dec)
                        # Backpressure reached globally; halt further dispatches this cycle
                        return decisions

                    # Tenant concurrency check
                    tenant_active = self.queue.count_active_tenant_items(tenant_id)
                    if tenant_active >= self.quota_policy.max_concurrency_per_tenant:
                        while tenant_queues[tenant_id]:
                            item = tenant_queues[tenant_id].popleft()
                            dec = SchedulingDecision(
                                decision_id=f"dec_{uuid.uuid4().hex[:12]}",
                                item_id=item.item_id,
                                task_id=item.task_id,
                                tenant_id=item.tenant_id,
                                step_id=item.step_id,
                                verdict=SchedulingVerdict.TENANT_QUOTA_EXCEEDED,
                                reason=f"Tenant '{tenant_id}' quota exceeded ({tenant_active}/{self.quota_policy.max_concurrency_per_tenant})",
                                timestamp=curr_time,
                            )
                            self.queue.store.record_decision(dec)
                            decisions.append(dec)
                        tenant_list.remove(tenant_id)
                        continue

                    # Fetch candidate item
                    item = tenant_queues[tenant_id].popleft()

                    # Task concurrency check
                    task_active = self.queue.count_active_task_items(item.task_id)
                    if task_active >= self.quota_policy.max_concurrency_per_task:
                        dec = SchedulingDecision(
                            decision_id=f"dec_{uuid.uuid4().hex[:12]}",
                            item_id=item.item_id,
                            task_id=item.task_id,
                            tenant_id=item.tenant_id,
                            step_id=item.step_id,
                            verdict=SchedulingVerdict.QUEUED_BACKPRESSURE,
                            reason=f"Task '{item.task_id}' concurrency limit reached ({task_active}/{self.quota_policy.max_concurrency_per_task})",
                            timestamp=curr_time,
                        )
                        self.queue.store.record_decision(dec)
                        decisions.append(dec)
                        continue

                    # Check budget exhaustion if present in payload
                    if item.payload.get("budget_exhausted"):
                        dec = SchedulingDecision(
                            decision_id=f"dec_{uuid.uuid4().hex[:12]}",
                            item_id=item.item_id,
                            task_id=item.task_id,
                            tenant_id=item.tenant_id,
                            step_id=item.step_id,
                            verdict=SchedulingVerdict.BUDGET_EXHAUSTED,
                            reason="Task execution budget has been exhausted",
                            timestamp=curr_time,
                        )
                        self.queue.store.record_decision(dec)
                        decisions.append(dec)
                        continue

                    # Check step dependency readiness in supervisor if applicable
                    if self.supervisor and item.step_id:
                        graph = self.supervisor.get_graph(item.task_id)
                        if graph:
                            step = graph.get_step(item.step_id)
                            if step and step.dependencies:
                                unsat = [
                                    dep_id for dep_id in step.dependencies
                                    if dep_id in graph.plan.steps and graph.plan.steps[dep_id].status != PlanStepStatus.SUCCEEDED
                                ]
                                if unsat:
                                    dec = SchedulingDecision(
                                        decision_id=f"dec_{uuid.uuid4().hex[:12]}",
                                        item_id=item.item_id,
                                        task_id=item.task_id,
                                        tenant_id=item.tenant_id,
                                        step_id=item.step_id,
                                        verdict=SchedulingVerdict.BLOCKED_DEPENDENCIES,
                                        reason=f"Step '{item.step_id}' waiting for unsatisfied dependencies: {unsat}",
                                        timestamp=curr_time,
                                    )
                                    self.queue.store.record_decision(dec)
                                    decisions.append(dec)
                                    continue

                    # Worker Capability & Capacity Selection
                    # Check if any registered worker supports the required capabilities
                    capable_workers = [
                        w for w in all_workers
                        if w.supports_capabilities(item.required_capabilities)
                    ]
                    if item.required_capabilities and not capable_workers:
                        dec = SchedulingDecision(
                            decision_id=f"dec_{uuid.uuid4().hex[:12]}",
                            item_id=item.item_id,
                            task_id=item.task_id,
                            tenant_id=item.tenant_id,
                            step_id=item.step_id,
                            verdict=SchedulingVerdict.CAPABILITY_UNAVAILABLE,
                            reason=f"No worker registered with capabilities: {item.required_capabilities}",
                            timestamp=curr_time,
                        )
                        self.queue.store.record_decision(dec)
                        decisions.append(dec)
                        continue

                    # Check available workers with open capacity
                    available_workers = self.worker_registry.list_available_workers(
                        required_capabilities=item.required_capabilities,
                        now=curr_time,
                    )
                    if not available_workers:
                        # All capable workers are busy or draining: Backpressure
                        dec = SchedulingDecision(
                            decision_id=f"dec_{uuid.uuid4().hex[:12]}",
                            item_id=item.item_id,
                            task_id=item.task_id,
                            tenant_id=item.tenant_id,
                            step_id=item.step_id,
                            verdict=SchedulingVerdict.QUEUED_BACKPRESSURE,
                            reason="All capable workers are at capacity or draining",
                            timestamp=curr_time,
                        )
                        self.queue.store.record_decision(dec)
                        decisions.append(dec)
                        continue

                    # Pick worker with the most available slots (load balancing)
                    selected_worker = max(available_workers, key=lambda w: w.available_slots())

                    # Atomic Dispatch and Claim
                    try:
                        self.queue.mark_dispatching(item.item_id, selected_worker.worker_id)
                    except Exception as dispatch_claim_exc:
                        # Another concurrent scheduler or worker won the atomic dispatch CAS
                        logger.debug("Item %s already claimed by another scheduler cycle: %s", item.item_id, dispatch_claim_exc)
                        continue

                    lease_id = f"lease_{uuid.uuid4().hex[:12]}"
                    try:
                        # If supervisor is present and step_id exists, acquire authoritative CAS StepLease
                        if self.supervisor and item.step_id:
                            supervisor_lease = self.supervisor.claim_step(
                                task_id=item.task_id,
                                step_id=item.step_id,
                                worker_id=selected_worker.worker_id,
                                now=curr_time,
                            )
                            lease_id = supervisor_lease.lease_id

                        # Allocate worker capacity
                        self.worker_registry.acquire_capacity(
                            worker_id=selected_worker.worker_id,
                            lease_id=lease_id,
                            required_capabilities=item.required_capabilities,
                            now=curr_time,
                        )

                        # Mark queue item as CLAIMED
                        self.queue.mark_claimed(
                            item_id=item.item_id,
                            worker_id=selected_worker.worker_id,
                            lease_id=lease_id,
                        )

                        dec = SchedulingDecision(
                            decision_id=f"dec_{uuid.uuid4().hex[:12]}",
                            item_id=item.item_id,
                            task_id=item.task_id,
                            tenant_id=item.tenant_id,
                            step_id=item.step_id,
                            verdict=SchedulingVerdict.DISPATCHED,
                            reason=f"Dispatched to worker '{selected_worker.worker_id}' with lease '{lease_id}'",
                            assigned_worker_id=selected_worker.worker_id,
                            timestamp=curr_time,
                        )
                        self.queue.store.record_decision(dec)
                        decisions.append(dec)

                        self._emit_event("scheduler.dispatched", {
                            "decision_id": dec.decision_id,
                            "item_id": item.item_id,
                            "task_id": item.task_id,
                            "step_id": item.step_id,
                            "worker_id": selected_worker.worker_id,
                            "lease_id": lease_id,
                        })

                        dispatched_count += 1
                        progress_made = True

                    except Exception as dispatch_exc:
                        logger.warning(
                            "Dispatch failed for item %s to worker %s after dispatch claim: %s",
                            item.item_id, selected_worker.worker_id, dispatch_exc,
                        )
                        # Release capacity if acquired
                        try:
                            self.worker_registry.release_capacity(selected_worker.worker_id, lease_id)
                        except Exception:
                            pass

                        # Rollback dispatch claim cleanly to QUEUED (never clobber another worker)
                        if hasattr(self.queue.store, "release_dispatch_claim"):
                            self.queue.store.release_dispatch_claim(item.item_id, selected_worker.worker_id)
                        else:
                            try:
                                self.queue.requeue(item.item_id, reason=f"Dispatch error: {dispatch_exc}")
                            except Exception:
                                pass

                # If no tenant could dispatch any item in a pass, break out of loop
                if not progress_made:
                    break

        return decisions
