"""
backend/execution/fabric/queue.py — Phase 21 Durable Task Queue.

Authoritative durable queue implementation managing units of execution work,
priority dispatching, deadline expiration, requeueing, and convergence with
the Phase 20 execution supervisor.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import uuid

from ..orchestration.engineering_memory import scrub_secrets
from .models import (
    FabricError,
    QueueEmptyError,
    QueueItem,
    QueueItemStatus,
)
from .store import FabricStore

logger = logging.getLogger("aetherion.fabric.queue")


class DurableTaskQueue:
    """
    Authoritative durable queue for executable tasks and graph steps.
    All enqueue, dispatch, claim, requeue, and completion states are persisted
    in the database and survive process crashes.
    """

    def __init__(
        self,
        store: Optional[FabricStore] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.store = store or FabricStore()
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
                logger.debug("Queue event callback error: %s", cb_exc)

    def enqueue(
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
        """
        Enqueue a unit of executable work into durable storage.
        """
        if not task_id or not str(task_id).strip():
            raise FabricError("task_id cannot be empty.")
        if not tenant_id or not str(tenant_id).strip():
            raise FabricError("tenant_id cannot be empty.")

        item_id = f"qitem_{uuid.uuid4().hex[:12]}"
        now = enqueued_at if enqueued_at is not None else time.time()
        caps = list(required_capabilities or [])
        p_load = dict(payload or {})

        item = QueueItem(
            item_id=item_id,
            task_id=task_id,
            tenant_id=tenant_id,
            step_id=step_id,
            priority=priority,
            enqueued_at=now,
            attempt=1,
            execution_class=execution_class,
            deadline=deadline,
            required_capabilities=caps,
            status=QueueItemStatus.QUEUED,
            payload=p_load,
        )

        with self._lock:
            self.store.insert_queue_item(item)
            self._emit_event("queue.enqueued", {
                "item_id": item_id,
                "task_id": task_id,
                "step_id": step_id,
                "tenant_id": tenant_id,
                "priority": priority,
                "execution_class": execution_class,
            })

        return item

    def peek_runnable(
        self,
        tenant_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[QueueItem]:
        """
        Fetch runnable queue items ordered by priority (descending) and enqueue time (ascending).
        """
        with self._lock:
            # Check QUEUED and REQUEUED items
            queued = self.store.list_queue_items(status=QueueItemStatus.QUEUED.value, tenant_id=tenant_id, limit=limit)
            requeued = self.store.list_queue_items(status=QueueItemStatus.REQUEUED.value, tenant_id=tenant_id, limit=limit)
            combined = queued + requeued
            combined.sort(key=lambda x: (-x.priority, x.enqueued_at))
            return combined[:limit]

    def mark_dispatching(self, item_id: str, worker_id: str) -> QueueItem:
        """
        Transition queue item from QUEUED/REQUEUED to DISPATCHING using atomic CAS.
        """
        with self._lock:
            if hasattr(self.store, "claim_item_for_dispatch_atomic"):
                success = self.store.claim_item_for_dispatch_atomic(item_id, worker_id)
                if not success:
                    raise FabricError(f"Cannot dispatch item '{item_id}': CAS contention or invalid state.")
                item = self.store.get_queue_item(item_id)
                if not item:
                    raise FabricError(f"Queue item '{item_id}' not found after atomic dispatch.")
            else:
                item = self.store.get_queue_item(item_id)
                if not item:
                    raise FabricError(f"Queue item '{item_id}' not found.")
                if item.status not in (QueueItemStatus.QUEUED, QueueItemStatus.REQUEUED):
                    raise FabricError(f"Cannot dispatch item '{item_id}' in state '{item.status.value}'.")

                item.status = QueueItemStatus.DISPATCHING
                item.assigned_worker_id = worker_id
                item.dispatched_at = time.time()
                self.store.update_queue_item(item)

            self._emit_event("queue.dispatched", {
                "item_id": item_id,
                "task_id": item.task_id,
                "step_id": item.step_id,
                "worker_id": worker_id,
            })
            return item

    def mark_claimed(self, item_id: str, worker_id: str, lease_id: str) -> QueueItem:
        """
        Transition queue item from DISPATCHING/QUEUED to CLAIMED with Phase 20 lease binding using atomic CAS.
        """
        with self._lock:
            if hasattr(self.store, "claim_item_atomic"):
                success = self.store.claim_item_atomic(
                    item_id=item_id,
                    worker_id=worker_id,
                    lease_id=lease_id,
                    expected_statuses=[
                        QueueItemStatus.DISPATCHING.value,
                        QueueItemStatus.QUEUED.value,
                        QueueItemStatus.REQUEUED.value,
                    ],
                )
                if not success:
                    raise FabricError(f"Cannot claim item '{item_id}' for worker '{worker_id}': CAS contention.")
                item = self.store.get_queue_item(item_id)
                if not item:
                    raise FabricError(f"Queue item '{item_id}' not found after atomic claim.")
            else:
                item = self.store.get_queue_item(item_id)
                if not item:
                    raise FabricError(f"Queue item '{item_id}' not found.")
                if item.status not in (QueueItemStatus.DISPATCHING, QueueItemStatus.QUEUED, QueueItemStatus.REQUEUED):
                    raise FabricError(f"Cannot claim item '{item_id}' in state '{item.status.value}'.")

                item.status = QueueItemStatus.CLAIMED
                item.assigned_worker_id = worker_id
                item.lease_id = lease_id
                self.store.update_queue_item(item)

            self._emit_event("worker.claimed", {
                "item_id": item_id,
                "task_id": item.task_id,
                "step_id": item.step_id,
                "worker_id": worker_id,
                "lease_id": lease_id,
            })
            return item

    def mark_running(self, item_id: str, worker_id: Optional[str] = None) -> QueueItem:
        """
        Transition queue item from CLAIMED to RUNNING with worker fencing.
        """
        with self._lock:
            if worker_id and hasattr(self.store, "mark_running_atomic"):
                success = self.store.mark_running_atomic(item_id, worker_id)
                if not success:
                    raise FabricError(
                        f"Cannot mark item '{item_id}' as running: worker '{worker_id}' is not authorized or item is not CLAIMED."
                    )
                item = self.store.get_queue_item(item_id)
                if not item:
                    raise FabricError(f"Queue item '{item_id}' not found.")
                return item

            item = self.store.get_queue_item(item_id)
            if not item:
                raise FabricError(f"Queue item '{item_id}' not found.")
            item.status = QueueItemStatus.RUNNING
            self.store.update_queue_item(item)
            return item

    def mark_completed(self, item_id: str, worker_id: Optional[str] = None) -> QueueItem:
        """
        Transition queue item to COMPLETED with worker fencing.
        """
        with self._lock:
            if worker_id and hasattr(self.store, "mark_completed_atomic"):
                now = time.time()
                success = self.store.mark_completed_atomic(item_id, worker_id, completed_at=now)
                if not success:
                    raise FabricError(
                        f"Cannot complete item '{item_id}': worker '{worker_id}' is not authorized or item is not RUNNING."
                    )
                item = self.store.get_queue_item(item_id)
                if not item:
                    raise FabricError(f"Queue item '{item_id}' not found.")
                return item

            item = self.store.get_queue_item(item_id)
            if not item:
                raise FabricError(f"Queue item '{item_id}' not found.")
            item.status = QueueItemStatus.COMPLETED
            item.completed_at = time.time()
            self.store.update_queue_item(item)
            return item

    def mark_failed(self, item_id: str, error_message: str, worker_id: Optional[str] = None) -> QueueItem:
        """
        Transition queue item to FAILED with worker fencing.
        """
        with self._lock:
            scrubbed_msg = scrub_secrets(error_message)
            if worker_id and hasattr(self.store, "mark_failed_atomic"):
                now = time.time()
                success = self.store.mark_failed_atomic(item_id, worker_id, scrubbed_msg, completed_at=now)
                if not success:
                    raise FabricError(
                        f"Cannot fail item '{item_id}': worker '{worker_id}' is not authorized."
                    )
                item = self.store.get_queue_item(item_id)
                if not item:
                    raise FabricError(f"Queue item '{item_id}' not found.")
                return item

            item = self.store.get_queue_item(item_id)
            if not item:
                raise FabricError(f"Queue item '{item_id}' not found.")
            item.status = QueueItemStatus.FAILED
            item.completed_at = time.time()
            item.error_message = scrubbed_msg
            self.store.update_queue_item(item)
            return item

    def requeue(self, item_id: str, reason: str = "Retry after failure or crash") -> QueueItem:
        """
        Requeue an item for a subsequent execution attempt.
        Clears worker binding and increments attempt counter.
        """
        with self._lock:
            item = self.store.get_queue_item(item_id)
            if not item:
                raise FabricError(f"Queue item '{item_id}' not found.")

            item.status = QueueItemStatus.REQUEUED
            item.attempt += 1
            item.assigned_worker_id = None
            item.lease_id = None
            item.dispatched_at = None
            item.error_message = scrub_secrets(reason)
            self.store.update_queue_item(item)

            self._emit_event("queue.requeued", {
                "item_id": item_id,
                "task_id": item.task_id,
                "step_id": item.step_id,
                "attempt": item.attempt,
                "reason": scrub_secrets(reason),
            })
            return item

    def expire_deadlines(self, now: Optional[float] = None) -> List[QueueItem]:
        """
        Scan and expire all queued/requeued items whose deadline has passed.
        """
        curr_time = now if now is not None else time.time()
        expired: List[QueueItem] = []

        with self._lock:
            runnable = self.peek_runnable(limit=200)
            for item in runnable:
                if item.deadline is not None and curr_time > item.deadline:
                    item.status = QueueItemStatus.EXPIRED
                    item.completed_at = curr_time
                    item.error_message = f"Execution deadline exceeded ({item.deadline} < {curr_time})."
                    self.store.update_queue_item(item)
                    expired.append(item)
                    self._emit_event("queue.expired", {
                        "item_id": item.item_id,
                        "task_id": item.task_id,
                        "step_id": item.step_id,
                        "deadline": item.deadline,
                    })
        return expired

    def get_item(self, item_id: str) -> Optional[QueueItem]:
        with self._lock:
            return self.store.get_queue_item(item_id)

    def get_queue_depth(self, tenant_id: Optional[str] = None) -> int:
        """Return the number of runnable items waiting in the queue."""
        with self._lock:
            queued = self.store.list_queue_items(status=QueueItemStatus.QUEUED.value, tenant_id=tenant_id, limit=1000)
            requeued = self.store.list_queue_items(status=QueueItemStatus.REQUEUED.value, tenant_id=tenant_id, limit=1000)
            return len(queued) + len(requeued)

    def count_active_tenant_items(self, tenant_id: str) -> int:
        with self._lock:
            return self.store.count_active_tenant_items(tenant_id)

    def count_active_task_items(self, task_id: str) -> int:
        with self._lock:
            return self.store.count_active_task_items(task_id)

    def count_global_active_items(self) -> int:
        with self._lock:
            return self.store.count_global_active_items()
