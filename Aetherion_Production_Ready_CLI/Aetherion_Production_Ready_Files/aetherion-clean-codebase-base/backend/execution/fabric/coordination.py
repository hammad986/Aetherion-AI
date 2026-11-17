"""
backend/execution/fabric/coordination.py — Phase 22 Transient Fabric Coordination Layer.

Provides FabricCoordinator for inter-process and inter-worker signaling:
- Worker wakeups on task enqueue
- Transient heartbeat propagation
- Drain and stop signal coordination
- Clean fallback to in-process primitives when Redis is unavailable.

NOTE: All authoritative state resides in the relational database (Postgres / SQLite).
Redis and this coordinator are strictly for transient signaling and low-latency wakeups.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("aetherion.fabric.coordination")

_FABRIC_WAKEUP_CHANNEL = "fabric:events:work_available"
_FABRIC_HB_PREFIX = "fabric:hb:"
_FABRIC_STOP_PREFIX = "fabric:stop:"


class FabricCoordinator:
    """
    Transient coordination layer for Execution Fabric workers and schedulers.
    Uses Redis when available; falls back smoothly to thread conditions in local dev/tests.
    """

    def __init__(
        self,
        redis_client: Optional[Any] = None,
        fallback_to_local: bool = True,
    ) -> None:
        self._fallback_to_local = fallback_to_local
        self._redis = redis_client
        self._lock = threading.RLock()
        self._local_cv = threading.Condition(self._lock)
        self._notified = False
        self._local_heartbeats: Dict[str, Dict[str, Any]] = {}
        self._local_stop_signals: Dict[str, float] = {}

        if self._redis is None:
            try:
                from redis_layer import get_nx_redis
                self._redis = get_nx_redis()
            except Exception as exc:
                logger.debug("redis_layer unavailable (%s); using in-process coordinator.", exc)
                self._redis = None

    @property
    def is_distributed(self) -> bool:
        """True if backed by a live Redis instance; False if in-process local fallback."""
        if self._redis is None:
            return False
        return bool(getattr(self._redis, "available", False))

    # ── Work Notification & Wakeups ──────────────────────────────────────────

    def notify_work_available(self, tenant_id: Optional[str] = None) -> None:
        """
        Signal waiting workers that new work has been enqueued.
        """
        # Always signal local condition variable for any in-process workers
        with self._local_cv:
            self._notified = True
            self._local_cv.notify_all()

        # If distributed Redis is available, publish event or push to wakeup list
        if self.is_distributed and self._redis is not None:
            try:
                payload = json.dumps({"tenant_id": tenant_id, "timestamp": time.time()})
                client = getattr(self._redis, "_r", None)
                if client is not None:
                    client.publish(_FABRIC_WAKEUP_CHANNEL, payload)
                else:
                    self._redis.push(f"fabric:wakeups:{tenant_id or 'all'}")
            except Exception as exc:
                logger.warning("Failed to publish distributed work notification: %s", exc)

    def wait_for_work(self, timeout: float = 1.0) -> bool:
        """
        Block up to `timeout` seconds waiting for a work notification.
        Returns True if a notification was received, False on timeout.
        """
        deadline = time.monotonic() + max(0.01, timeout)

        # First check local condition
        with self._local_cv:
            if self._notified:
                self._notified = False
                return True

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False

            if not self.is_distributed:
                # Wait purely on thread condition
                got_signal = self._local_cv.wait(timeout=remaining)
                if got_signal:
                    self._notified = False
                    return True
                return False

        # In distributed mode with live Redis, poll Redis queue / pubsub or sleep slice
        # using short polling bounded by remaining deadline
        while time.monotonic() < deadline:
            with self._local_cv:
                if self._notified:
                    self._notified = False
                    return True
            rem = deadline - time.monotonic()
            if rem <= 0:
                break
            sleep_slice = min(rem, 0.1)
            time.sleep(sleep_slice)

        return False

    # ── Heartbeats ───────────────────────────────────────────────────────────

    def publish_worker_heartbeat(
        self,
        worker_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        ttl_seconds: int = 60,
    ) -> None:
        """
        Record a transient worker heartbeat.
        """
        payload = {
            "worker_id": worker_id,
            "timestamp": time.time(),
            "metadata": metadata or {},
        }
        with self._lock:
            self._local_heartbeats[worker_id] = payload

        if self.is_distributed and self._redis is not None:
            try:
                key = f"{_FABRIC_HB_PREFIX}{worker_id}"
                self._redis.set(key, json.dumps(payload), ex=ttl_seconds)
            except Exception as exc:
                logger.warning("Failed to publish worker heartbeat to Redis: %s", exc)

    def get_worker_heartbeat(self, worker_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the most recent transient heartbeat for a worker.
        """
        if self.is_distributed and self._redis is not None:
            try:
                key = f"{_FABRIC_HB_PREFIX}{worker_id}"
                raw = self._redis.get(key)
                if raw:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    return json.loads(raw)
            except Exception as exc:
                logger.warning("Failed to get worker heartbeat from Redis: %s", exc)

        with self._lock:
            return self._local_heartbeats.get(worker_id)

    # ── Stop / Drain Signaling ───────────────────────────────────────────────

    def request_stop(self, worker_id: str, ttl_seconds: int = 300) -> None:
        """
        Signal a worker to stop or drain.
        """
        with self._lock:
            self._local_stop_signals[worker_id] = time.time() + ttl_seconds

        if self.is_distributed and self._redis is not None:
            try:
                key = f"{_FABRIC_STOP_PREFIX}{worker_id}"
                self._redis.set(key, "1", ex=ttl_seconds)
            except Exception as exc:
                logger.warning("Failed to record stop signal in Redis: %s", exc)

        # Notify workers to wake up and check stop status immediately
        self.notify_work_available()

    def should_stop(self, worker_id: str) -> bool:
        """
        Check if a stop signal has been issued for this worker or globally.
        """
        now = time.time()
        with self._lock:
            for target in (worker_id, "*", "all"):
                exp = self._local_stop_signals.get(target)
                if exp is not None:
                    if now < exp:
                        return True
                    else:
                        del self._local_stop_signals[target]

        if self.is_distributed and self._redis is not None:
            try:
                for target in (worker_id, "*", "all"):
                    key = f"{_FABRIC_STOP_PREFIX}{target}"
                    if self._redis.get(key):
                        return True
            except Exception as exc:
                logger.warning("Failed to check stop signal in Redis: %s", exc)

        return False

    def clear_stop(self, worker_id: str) -> None:
        """
        Clear any active stop signal for a worker.
        """
        with self._lock:
            self._local_stop_signals.pop(worker_id, None)

        if self.is_distributed and self._redis is not None:
            try:
                key = f"{_FABRIC_STOP_PREFIX}{worker_id}"
                self._redis.delete(key)
            except Exception as exc:
                logger.warning("Failed to clear stop signal in Redis: %s", exc)

    def close(self) -> None:
        """Clean up resources."""
        with self._lock:
            self._notified = True
            with self._local_cv:
                self._local_cv.notify_all()
            self._local_heartbeats.clear()
            self._local_stop_signals.clear()
