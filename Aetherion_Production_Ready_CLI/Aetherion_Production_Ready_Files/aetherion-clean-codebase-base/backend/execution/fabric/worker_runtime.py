"""
backend/execution/fabric/worker_runtime.py — Phase 22 Multi-Process Worker Runtime.

Provides WorkerRuntime for production execution fabric workers:
- Worker lifecycle states: STARTING, READY, BUSY, DRAINING, STOPPED, UNHEALTHY
- Automatic registration and periodic background heartbeat
- Adaptive backoff polling with instant coordination wakeups
- Atomic CAS work claiming and capacity tracking
- Safe execution in local threadpool with Phase 20 StepLease coordination
- Graceful drain on SIGINT / SIGTERM or operator drain command
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import logging
import os
import signal
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid

from .coordination import FabricCoordinator
from .models import (
    FabricError,
    QueueItem,
    QueueItemStatus,
    WorkerRecord,
    WorkerStatus,
)
from .service import ExecutionFabricService
from .store import BaseFabricStore, get_fabric_store

logger = logging.getLogger("aetherion.fabric.worker_runtime")


class WorkerRuntime:
    """
    Multi-process execution fabric worker runtime.
    Manages registration, heartbeats, work claiming, execution, and graceful draining.
    """

    def __init__(
        self,
        worker_id: Optional[str] = None,
        session_id: Optional[str] = None,
        max_concurrency: int = 4,
        supported_capabilities: Optional[Set[str] | List[str]] = None,
        service: Optional[ExecutionFabricService] = None,
        coordinator: Optional[FabricCoordinator] = None,
        store: Optional[BaseFabricStore] = None,
        executor_fn: Optional[Callable[[QueueItem], Any]] = None,
        heartbeat_interval_sec: float = 2.0,
        min_poll_interval_sec: float = 0.5,
        max_poll_interval_sec: float = 5.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.worker_id = worker_id or f"worker_{uuid.uuid4().hex[:8]}"
        self.session_id = session_id or f"sess_{uuid.uuid4().hex[:8]}"
        self.process_id = os.getpid()
        self.max_concurrency = max(1, max_concurrency)
        self.supported_capabilities: Set[str] = (
            set(supported_capabilities) if supported_capabilities is not None else {"DEFAULT"}
        )
        self.metadata = dict(metadata or {})
        self.executor_fn = executor_fn

        self.heartbeat_interval_sec = max(0.2, heartbeat_interval_sec)
        self.min_poll_interval_sec = max(0.1, min_poll_interval_sec)
        self.max_poll_interval_sec = max(self.min_poll_interval_sec, max_poll_interval_sec)

        # Store and service resolution
        self.store = store or (service.store if service else get_fabric_store())
        self.service = service or ExecutionFabricService(store=self.store)
        self.coordinator = coordinator or FabricCoordinator()

        # Concurrency & execution tracking
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._drain_event = threading.Event()
        self._current_status = WorkerStatus.STARTING
        self._active_futures: Dict[str, Future] = {}
        self._thread_pool: Optional[ThreadPoolExecutor] = None

        # Background loops
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._work_thread: Optional[threading.Thread] = None

        # Statistics
        self.items_processed = 0
        self.items_succeeded = 0
        self.items_failed = 0

    @property
    def status(self) -> WorkerStatus:
        with self._lock:
            return self._current_status

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    @property
    def is_draining(self) -> bool:
        return self._drain_event.is_set()

    @property
    def active_concurrency(self) -> int:
        with self._lock:
            return len(self._active_futures)

    # ── Lifecycle Management ─────────────────────────────────────────────────

    def register(self) -> WorkerRecord:
        """Register or re-register worker with store and worker registry."""
        with self._lock:
            record = self.service.register_worker(
                worker_id=self.worker_id,
                session_id=self.session_id,
                process_id=self.process_id,
                max_concurrency=self.max_concurrency,
                supported_capabilities=self.supported_capabilities,
                metadata=self.metadata,
            )
            self._current_status = WorkerStatus.READY
            self.coordinator.publish_worker_heartbeat(
                self.worker_id,
                metadata={"status": self._current_status.value, "pid": self.process_id},
            )
            return record

    def start(self) -> None:
        """Start worker in background threads."""
        with self._lock:
            if self._work_thread is not None and self._work_thread.is_alive():
                return
            self._stop_event.clear()
            self._drain_event.clear()
            self.register()

            self._thread_pool = ThreadPoolExecutor(
                max_workers=self.max_concurrency,
                thread_name_prefix=f"{self.worker_id}_pool",
            )

            # Start heartbeat loop
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"{self.worker_id}_hb",
                daemon=True,
            )
            self._heartbeat_thread.start()

            # Start worker loop
            self._work_thread = threading.Thread(
                target=self._worker_loop,
                name=f"{self.worker_id}_work",
                daemon=True,
            )
            self._work_thread.start()

    def run(self, timeout: Optional[float] = None) -> None:
        """Run worker loop synchronously in current thread."""
        self.start()
        start_time = time.monotonic()
        try:
            while not self._stop_event.is_set():
                if timeout is not None and (time.monotonic() - start_time) >= timeout:
                    self.stop(reason="Run timeout reached")
                    break
                time.sleep(0.1)
        except KeyboardInterrupt:
            logger.info("Worker %s received KeyboardInterrupt. Initiating drain...", self.worker_id)
            self.drain(wait=True, timeout=30.0)
            self.stop(reason="KeyboardInterrupt")

    def drain(self, wait: bool = False, timeout: float = 30.0) -> None:
        """
        Initiate graceful drain: stop accepting new work and wait for in-flight items to complete.
        """
        logger.info("Initiating graceful drain for worker %s", self.worker_id)
        with self._lock:
            self._drain_event.set()
            self._current_status = WorkerStatus.DRAINING
            try:
                self.service.drain_worker(self.worker_id)
            except Exception as exc:
                logger.warning("Error draining worker in registry: %s", exc)

        # Notify coordinator of state update
        self.coordinator.notify_work_available()

        if wait:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                with self._lock:
                    if len(self._active_futures) == 0:
                        break
                time.sleep(0.1)

            with self._lock:
                if len(self._active_futures) == 0:
                    self.stop(reason="Drain completed")
                else:
                    logger.warning(
                        "Worker %s drain timed out after %.1fs with %d active items remaining",
                        self.worker_id,
                        timeout,
                        len(self._active_futures),
                    )
                    self.stop(reason="Drain timeout")

    def stop(self, reason: str = "Explicit stop") -> None:
        """Stop worker execution and clean up resources."""
        logger.info("Stopping worker %s: %s", self.worker_id, reason)
        with self._lock:
            self._stop_event.set()
            self._current_status = WorkerStatus.STOPPED
            try:
                self.service.stop_worker(self.worker_id, reason=reason)
            except Exception as exc:
                logger.debug("Error stopping worker in registry: %s", exc)

        # Shutdown threadpool
        if self._thread_pool is not None:
            self._thread_pool.shutdown(wait=False, cancel_futures=False)

        # Wake up any coordinators waiting
        self.coordinator.clear_stop(self.worker_id)
        self.coordinator.notify_work_available()

    def setup_signal_handlers(self) -> None:
        """Attach SIGINT and SIGTERM handlers for graceful draining."""
        def _handle_signal(signum, frame):
            sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            logger.info("Worker %s caught %s; triggering graceful drain.", self.worker_id, sig_name)
            self.drain(wait=False)

        try:
            signal.signal(signal.SIGINT, _handle_signal)
            if hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, _handle_signal)
        except (ValueError, AttributeError):
            # Signal handling may fail if not in main thread; ignore safely
            pass

    # ── Background Loops ─────────────────────────────────────────────────────

    def _heartbeat_loop(self) -> None:
        """Periodic background heartbeat publisher."""
        while not self._stop_event.is_set():
            try:
                # Update store heartbeat
                with self._lock:
                    st = self._current_status
                self.service.worker_heartbeat(self.worker_id)

                # Update transient coordinator heartbeat
                self.coordinator.publish_worker_heartbeat(
                    self.worker_id,
                    metadata={
                        "status": st.value,
                        "active": self.active_concurrency,
                        "processed": self.items_processed,
                    },
                )
            except Exception as exc:
                logger.debug("Heartbeat loop exception in worker %s: %s", self.worker_id, exc)

            self._stop_event.wait(timeout=self.heartbeat_interval_sec)

    def _worker_loop(self) -> None:
        """Main scheduling evaluation and work dispatch loop."""
        backoff = self.min_poll_interval_sec

        while not self._stop_event.is_set():
            # Check coordinator stop signal
            if self.coordinator.should_stop(self.worker_id):
                logger.info("Worker %s detected stop signal in coordinator; draining.", self.worker_id)
                self.drain(wait=False)

            # Check if draining and all items done
            if self._drain_event.is_set():
                with self._lock:
                    if len(self._active_futures) == 0:
                        logger.info("Worker %s finished all active items during drain. Stopping.", self.worker_id)
                        self.stop(reason="Drain finished")
                        break
                # While draining, don't claim new work; wait for in-flight to finish
                time.sleep(0.2)
                continue

            # Check if worker has open capacity
            with self._lock:
                open_slots = self.max_concurrency - len(self._active_futures)

            if open_slots <= 0:
                with self._lock:
                    if self._current_status != WorkerStatus.DRAINING:
                        self._current_status = WorkerStatus.BUSY
                time.sleep(0.1)
                continue

            # Try to claim and dispatch work
            claimed_any = self._poll_and_dispatch()

            if claimed_any:
                backoff = self.min_poll_interval_sec
                with self._lock:
                    if not self._drain_event.is_set():
                        self._current_status = (
                            WorkerStatus.BUSY if len(self._active_futures) >= self.max_concurrency
                            else WorkerStatus.READY
                        )
            else:
                # No work found; back off exponentially or wait on coordinator notification
                with self._lock:
                    if not self._drain_event.is_set() and len(self._active_futures) == 0:
                        self._current_status = WorkerStatus.READY

                woke_up = self.coordinator.wait_for_work(timeout=backoff)
                if woke_up:
                    backoff = self.min_poll_interval_sec
                else:
                    backoff = min(backoff * 1.5, self.max_poll_interval_sec)

    def _poll_and_dispatch(self) -> bool:
        """
        Evaluate queue and dispatch claimed work to the threadpool.
        Returns True if at least one item was claimed/dispatched.
        """
        # First: check if items are already assigned/claimed for this worker in the store
        assigned_items = self.store.list_queue_items(
            status=QueueItemStatus.CLAIMED.value,
            limit=self.max_concurrency,
        )
        my_claimed = [item for item in assigned_items if item.assigned_worker_id == self.worker_id]

        dispatched_count = 0
        for item in my_claimed:
            with self._lock:
                if item.item_id not in self._active_futures and len(self._active_futures) < self.max_concurrency:
                    self._launch_execution(item)
                    dispatched_count += 1

        if dispatched_count > 0:
            return True

        # Second: trigger scheduler tick to match pending queued items with available workers
        try:
            decisions = self.service.tick_scheduler(limit=self.max_concurrency)
            for dec in decisions:
                if dec.assigned_worker_id == self.worker_id and dec.verdict.value == "DISPATCHED":
                    item = self.store.get_queue_item(dec.item_id)
                    if item and item.status == QueueItemStatus.CLAIMED:
                        with self._lock:
                            if item.item_id not in self._active_futures and len(self._active_futures) < self.max_concurrency:
                                self._launch_execution(item)
                                dispatched_count += 1
        except Exception as exc:
            logger.debug("Scheduler tick exception in worker %s: %s", self.worker_id, exc)

        return dispatched_count > 0

    def _launch_execution(self, item: QueueItem) -> None:
        """Submit claimed item to the threadpool for execution."""
        if self._thread_pool is None:
            return

        def _task_runner():
            self._execute_item_wrapper(item)

        future = self._thread_pool.submit(_task_runner)
        self._active_futures[item.item_id] = future

        def _on_done(f: Future):
            with self._lock:
                self._active_futures.pop(item.item_id, None)

        future.add_done_callback(_on_done)

    def _execute_item_wrapper(self, item: QueueItem) -> None:
        """Wrapper around service.execute_claimed_item with error handling, metrics, and structured logging."""
        logger.info("Worker %s executing item %s (task=%s)", self.worker_id, item.item_id, item.task_id)
        t_start = time.time()
        try:
            from .metrics import get_metrics_collector
            from .logging import get_structured_logger, correlation_context
        except ImportError:
            from backend.execution.fabric.metrics import get_metrics_collector
            from backend.execution.fabric.logging import get_structured_logger, correlation_context

        collector = get_metrics_collector()
        slogger = get_structured_logger()

        with correlation_context(
            task_id=item.task_id,
            tenant_id=item.tenant_id,
            step_id=item.step_id,
            worker_id=self.worker_id,
            attempt=item.attempt,
        ):
            slogger.log_event("TASK_EXECUTION_STARTED", f"Worker {self.worker_id} executing item {item.item_id}")
            try:
                self.service.execute_claimed_item(
                    item_id=item.item_id,
                    worker_id=self.worker_id,
                    executor_fn=self.executor_fn,
                )
                duration = time.time() - t_start
                with self._lock:
                    self.items_processed += 1
                    self.items_succeeded += 1
                collector.record_task_completed(task_id=item.task_id, tenant_id=item.tenant_id, duration_sec=duration)
                slogger.log_event("TASK_COMPLETED", f"Worker {self.worker_id} completed item {item.item_id}", duration_sec=round(duration, 4))
            except Exception as exc:
                duration = time.time() - t_start
                logger.error("Execution error on item %s: %s", item.item_id, exc)
                with self._lock:
                    self.items_processed += 1
                    self.items_failed += 1
                collector.record_task_failed(task_id=item.task_id, tenant_id=item.tenant_id, error_class=type(exc).__name__, duration_sec=duration)
                slogger.log_event("TASK_FAILED", f"Worker {self.worker_id} failed item {item.item_id}: {exc}", level="ERROR", error=str(exc), duration_sec=round(duration, 4))
