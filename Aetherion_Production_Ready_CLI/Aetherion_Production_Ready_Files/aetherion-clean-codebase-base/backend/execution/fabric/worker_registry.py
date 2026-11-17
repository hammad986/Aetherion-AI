"""
backend/execution/fabric/worker_registry.py — Phase 21 Worker Control Plane Registry.

Authoritative worker lifecycle, capacity management, heartbeat tracking,
graceful draining, and health evaluation.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid

from ..orchestration.engineering_memory import scrub_secrets
from .models import (
    CapabilityMismatchError,
    FabricError,
    StaleWorkerError,
    WorkerCapacityExceededError,
    WorkerNotFoundError,
    WorkerRecord,
    WorkerStatus,
)
from .store import FabricStore

logger = logging.getLogger("aetherion.fabric.worker_registry")


class WorkerRegistry:
    """
    Authoritative worker control plane managing worker registration, health,
    capacity allocation, and graceful shutdown/drain transitions.
    """

    def __init__(
        self,
        store: Optional[FabricStore] = None,
        heartbeat_timeout_sec: float = 30.0,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.store = store or FabricStore()
        self.heartbeat_timeout_sec = heartbeat_timeout_sec
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
                logger.debug("WorkerRegistry event callback error: %s", cb_exc)

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
        """Register a new or restarted worker in the fabric registry."""
        if not worker_id or not str(worker_id).strip():
            raise FabricError("worker_id cannot be empty.")

        curr_time = now if now is not None else time.time()
        caps = set(supported_capabilities or ["tool_execution", "sandbox", "model_access"])
        pid = process_id if process_id is not None else os.getpid()
        sid = session_id or f"sess_{uuid.uuid4().hex[:8]}"

        record = WorkerRecord(
            worker_id=worker_id,
            session_id=sid,
            process_id=pid,
            max_concurrency=max(1, max_concurrency),
            active_concurrency=0,
            active_leases=[],
            supported_capabilities=caps,
            status=WorkerStatus.READY,
            start_time=curr_time,
            last_heartbeat=curr_time,
            drain_state=False,
            metadata=dict(metadata or {}),
        )

        with self._lock:
            self.store.upsert_worker(record)
            self._emit_event("worker.registered", {
                "worker_id": worker_id,
                "session_id": sid,
                "process_id": pid,
                "max_concurrency": record.max_concurrency,
                "capabilities": list(caps),
            })

        return record

    def heartbeat(
        self,
        worker_id: str,
        now: Optional[float] = None,
    ) -> WorkerRecord:
        """Record worker liveness heartbeat and restore healthy status if recovering."""
        curr_time = now if now is not None else time.time()
        with self._lock:
            worker = self.store.get_worker(worker_id)
            if not worker:
                raise WorkerNotFoundError(f"Worker '{worker_id}' not found in registry.")

            worker.last_heartbeat = curr_time
            # If worker was marked unhealthy, restore to READY (or DRAINING if drained)
            if worker.status == WorkerStatus.UNHEALTHY:
                worker.status = WorkerStatus.DRAINING if worker.drain_state else WorkerStatus.READY

            self.store.upsert_worker(worker)
            self._emit_event("worker.heartbeat", {
                "worker_id": worker_id,
                "heartbeat_at": curr_time,
                "status": worker.status.value,
            })
            return worker

    def acquire_capacity(
        self,
        worker_id: str,
        lease_id: str,
        required_capabilities: Optional[List[str]] = None,
        now: Optional[float] = None,
    ) -> WorkerRecord:
        """
        Allocate an execution slot on the worker.
        Enforces health, non-drain state, capability compatibility, and concurrency limits.
        """
        curr_time = now if now is not None else time.time()
        with self._lock:
            worker = self.store.get_worker(worker_id)
            if not worker:
                raise WorkerNotFoundError(f"Worker '{worker_id}' not found.")

            if worker.status == WorkerStatus.STOPPED or worker.drain_state:
                raise WorkerCapacityExceededError(f"Worker '{worker_id}' is stopped or draining and cannot accept new work.")

            if not worker.is_healthy(self.heartbeat_timeout_sec, now=curr_time):
                worker.status = WorkerStatus.UNHEALTHY
                self.store.upsert_worker(worker)
                raise StaleWorkerError(f"Worker '{worker_id}' is unhealthy (heartbeat timed out).")

            if not worker.supports_capabilities(required_capabilities or []):
                raise CapabilityMismatchError(
                    f"Worker '{worker_id}' does not support required capabilities: {required_capabilities}"
                )

            if worker.active_concurrency >= worker.max_concurrency:
                raise WorkerCapacityExceededError(
                    f"Worker '{worker_id}' capacity full ({worker.active_concurrency}/{worker.max_concurrency})."
                )

            worker.active_concurrency += 1
            if lease_id not in worker.active_leases:
                worker.active_leases.append(lease_id)

            if worker.active_concurrency >= worker.max_concurrency:
                worker.status = WorkerStatus.BUSY
            else:
                worker.status = WorkerStatus.READY

            self.store.upsert_worker(worker)
            return worker

    def release_capacity(
        self,
        worker_id: str,
        lease_id: Optional[str] = None,
    ) -> WorkerRecord:
        """
        Free an execution slot on the worker.
        If worker is draining and in-flight count hits 0, completes transition to STOPPED.
        """
        with self._lock:
            worker = self.store.get_worker(worker_id)
            if not worker:
                raise WorkerNotFoundError(f"Worker '{worker_id}' not found.")

            worker.active_concurrency = max(0, worker.active_concurrency - 1)
            if lease_id and lease_id in worker.active_leases:
                worker.active_leases.remove(lease_id)

            if worker.drain_state:
                if worker.active_concurrency == 0:
                    worker.status = WorkerStatus.STOPPED
                    self._emit_event("worker.stopped", {
                        "worker_id": worker_id,
                        "reason": "Graceful drain completed",
                    })
                else:
                    worker.status = WorkerStatus.DRAINING
            else:
                if worker.active_concurrency < worker.max_concurrency:
                    worker.status = WorkerStatus.READY

            self.store.upsert_worker(worker)
            return worker

    def drain_worker(self, worker_id: str) -> WorkerRecord:
        """
        Initiate graceful drain on worker.
        Stops accepting new work while allowing in-flight work to complete safely.
        """
        with self._lock:
            worker = self.store.get_worker(worker_id)
            if not worker:
                raise WorkerNotFoundError(f"Worker '{worker_id}' not found.")

            worker.drain_state = True
            if worker.active_concurrency == 0:
                worker.status = WorkerStatus.STOPPED
                self._emit_event("worker.stopped", {
                    "worker_id": worker_id,
                    "reason": "Immediate drain on idle worker",
                })
            else:
                worker.status = WorkerStatus.DRAINING
                self._emit_event("worker.draining", {
                    "worker_id": worker_id,
                    "active_work_count": worker.active_concurrency,
                })

            self.store.upsert_worker(worker)
            return worker

    def stop_worker(self, worker_id: str, reason: str = "Explicit stop") -> WorkerRecord:
        """Mark worker as STOPPED."""
        with self._lock:
            worker = self.store.get_worker(worker_id)
            if not worker:
                raise WorkerNotFoundError(f"Worker '{worker_id}' not found.")

            worker.drain_state = True
            worker.status = WorkerStatus.STOPPED
            self.store.upsert_worker(worker)
            self._emit_event("worker.stopped", {
                "worker_id": worker_id,
                "reason": scrub_secrets(reason),
            })
            return worker

    def detect_unhealthy_workers(
        self,
        heartbeat_timeout_sec: Optional[float] = None,
        now: Optional[float] = None,
    ) -> List[WorkerRecord]:
        """
        Scan for active workers that have failed to heartbeat within the timeout window.
        Transitions them to UNHEALTHY.
        """
        timeout = heartbeat_timeout_sec or self.heartbeat_timeout_sec
        curr_time = now if now is not None else time.time()
        unhealthy: List[WorkerRecord] = []

        with self._lock:
            all_workers = self.store.list_workers()
            for worker in all_workers:
                if worker.status in (WorkerStatus.STOPPED, WorkerStatus.UNHEALTHY):
                    continue
                if (curr_time - worker.last_heartbeat) > timeout:
                    worker.status = WorkerStatus.UNHEALTHY
                    self.store.upsert_worker(worker)
                    unhealthy.append(worker)
                    self._emit_event("worker.unhealthy", {
                        "worker_id": worker.worker_id,
                        "last_heartbeat": worker.last_heartbeat,
                        "elapsed_sec": curr_time - worker.last_heartbeat,
                    })
        return unhealthy

    def get_worker(self, worker_id: str) -> Optional[WorkerRecord]:
        with self._lock:
            return self.store.get_worker(worker_id)

    def list_available_workers(
        self,
        required_capabilities: Optional[List[str]] = None,
        now: Optional[float] = None,
    ) -> List[WorkerRecord]:
        """
        List workers that are currently healthy, accepting work, and have available slots.
        """
        curr_time = now if now is not None else time.time()
        available: List[WorkerRecord] = []

        with self._lock:
            for w in self.store.list_workers():
                if w.has_capacity() and w.is_healthy(self.heartbeat_timeout_sec, now=curr_time):
                    if w.supports_capabilities(required_capabilities or []):
                        available.append(w)
        return available

    def list_all_workers(self) -> List[WorkerRecord]:
        with self._lock:
            return self.store.list_workers()
