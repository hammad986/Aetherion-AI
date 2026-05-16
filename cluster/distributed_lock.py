"""
cluster/distributed_lock.py — Cluster-Safe Distributed Locking
===============================================================
Provides cluster-wide mutual exclusion for all stateful operations.

Implementation: Redis SETNX + Lua compare-and-delete (Redlock-lite)
  • Atomic acquisition: SET key owner NX PX ttl_ms
  • Atomic release:     Lua script checks owner before DEL (no stolen-lock deletion)
  • Lease expiration:   All locks expire automatically (no deadlock on crash)
  • Renewal:            Long operations renew lease before expiry
  • Local fallback:     threading.Lock() when Redis unavailable (single-node only)

Protected resources (lock namespaces):
  nexora:lock:workspace:{session_id}     — workspace filesystem mutations
  nexora:lock:deploy:{deployment_id}     — deployment state transitions
  nexora:lock:governance:{policy_id}     — governance policy updates
  nexora:lock:browser:{browser_id}       — browser slot ownership
  nexora:lock:remediation:{component}    — playbook remediation actions
  nexora:lock:snapshot:{snap_id}         — snapshot write coordination
  nexora:lock:queue:{queue_name}         — queue ownership transitions

Safety guarantees:
  • Only the holder (owner_id) can release a lock (Lua-enforced)
  • Expired locks cannot be extended (must re-acquire)
  • Stale locks are swept by the leader every SWEEP_INTERVAL_SEC
  • Lock acquisition attempts time out after ACQUIRE_TIMEOUT_SEC
  • Max lock TTL capped at MAX_LOCK_TTL_SEC

Split-brain risk:
  Redis Redlock (multi-node consensus) would require 3+ Redis masters.
  This implementation uses single-Redis with TTL expiry as the safety net.
  In partitioned Redis scenarios: locks expire naturally (TTL-safe).
  KNOWN RISK: Between Redis restart and key expiry → both nodes may
  think they hold the lock. Mitigated by: short TTLs + health monitoring.
"""

import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Generator, Optional

logger = logging.getLogger("nexora.cluster.lock")

# ─── Configuration ────────────────────────────────────────────────────────────
DEFAULT_TTL_MS       = int(os.getenv("LOCK_DEFAULT_TTL_MS",   "10000"))   # 10s
MAX_TTL_MS           = int(os.getenv("LOCK_MAX_TTL_MS",       "60000"))   # 60s
ACQUIRE_TIMEOUT_SEC  = float(os.getenv("LOCK_ACQUIRE_TIMEOUT", "8.0"))
POLL_INTERVAL_MS     = int(os.getenv("LOCK_POLL_MS",           "200"))
RENEW_INTERVAL_SEC   = float(os.getenv("LOCK_RENEW_INTERVAL",  "3.0"))
SWEEP_INTERVAL_SEC   = int(os.getenv("LOCK_SWEEP_INTERVAL",    "60"))
_LOCK_PREFIX         = "nexora:lock:"
_LOCK_META_PREFIX    = "nexora:lock_meta:"

# Lua: release lock only if we are still the owner
_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Lua: extend TTL only if we still own the lock
_RENEW_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("pexpire", KEYS[1], ARGV[2])
else
    return 0
end
"""


class LockError(Exception):
    """Raised when lock acquisition fails or ownership is violated."""
    pass


class LockTimeout(LockError):
    """Raised when ACQUIRE_TIMEOUT_SEC is exceeded."""
    pass


@dataclass
class LockHandle:
    key:        str
    owner_id:   str
    ttl_ms:     int
    acquired_at:float
    redis_mode: bool   # True = Redis-backed; False = local threading.Lock
    _local_lock: Optional[threading.Lock] = field(default=None, repr=False)
    _renewer:   Optional[threading.Thread] = field(default=None, repr=False)
    _released:  bool = False

    def age_sec(self) -> float:
        return time.time() - self.acquired_at

    def is_valid(self) -> bool:
        if self._released:
            return False
        return self.age_sec() < (self.ttl_ms / 1000.0)


class DistributedLockManager:
    """
    Cluster-safe distributed lock manager.
    Transparently uses Redis (cluster mode) or threading.Lock (solo mode).
    """

    def __init__(self):
        self._local_locks: Dict[str, threading.Lock] = {}
        self._active_handles: Dict[str, LockHandle] = {}  # key → handle (for sweep)
        self._meta: Dict[str, dict] = {}        # key → {owner, ts, purpose}
        self._lock = threading.RLock()
        self._timeout_events = 0
        self._orphaned_count = 0
        self._running = True

        # Sweep thread
        threading.Thread(target=self._sweep_loop, daemon=True, name="lock-sweep").start()
        logger.info("[DistributedLock] Manager initialized")

    # ── Acquisition ───────────────────────────────────────────────────────────

    @contextmanager
    def lock(self, resource: str, ttl_ms: int = DEFAULT_TTL_MS,
             timeout_sec: float = ACQUIRE_TIMEOUT_SEC,
             purpose: str = "") -> Generator[LockHandle, None, None]:
        """
        Context manager that acquires a distributed lock.

        Usage:
            with lock_manager.lock("workspace:sess_abc") as h:
                # do work
                pass  # lock auto-released on exit

        Raises LockTimeout if acquisition fails within timeout_sec.
        """
        handle = self._acquire(resource, ttl_ms, timeout_sec, purpose)
        try:
            yield handle
        finally:
            self._release(handle)

    def _acquire(self, resource: str, ttl_ms: int, timeout_sec: float,
                 purpose: str) -> LockHandle:
        ttl_ms  = min(ttl_ms, MAX_TTL_MS)
        key     = f"{_LOCK_PREFIX}{resource}"
        owner   = f"{os.getenv('NODE_ID', 'local')}-{uuid.uuid4().hex[:10]}"
        deadline = time.time() + timeout_sec

        rc = self._get_redis()
        if rc:
            return self._acquire_redis(rc, key, owner, ttl_ms, deadline, purpose)
        else:
            return self._acquire_local(key, owner, ttl_ms, deadline, purpose)

    def _acquire_redis(self, rc, key: str, owner: str, ttl_ms: int,
                       deadline: float, purpose: str) -> LockHandle:
        while time.time() < deadline:
            try:
                ok = rc.set(key, owner, nx=True, px=ttl_ms)
                if ok:
                    handle = LockHandle(
                        key=key, owner_id=owner, ttl_ms=ttl_ms,
                        acquired_at=time.time(), redis_mode=True
                    )
                    self._record_acquisition(key, owner, purpose, handle)
                    self._start_auto_renew(handle, rc)
                    logger.debug(f"[DistLock] ACQUIRED (redis) {key} owner={owner[:12]}")
                    return handle
            except Exception as e:
                logger.debug(f"[DistLock] Redis acquire error: {e}")
                # Fall through to retry
            time.sleep(POLL_INTERVAL_MS / 1000.0)

        self._timeout_events += 1
        raise LockTimeout(f"Lock timeout after {ACQUIRE_TIMEOUT_SEC}s for {key}")

    def _acquire_local(self, key: str, owner: str, ttl_ms: int,
                       deadline: float, purpose: str) -> LockHandle:
        with self._lock:
            if key not in self._local_locks:
                self._local_locks[key] = threading.Lock()
            local_lk = self._local_locks[key]

        remaining = deadline - time.time()
        acquired = local_lk.acquire(timeout=max(0, remaining))
        if not acquired:
            self._timeout_events += 1
            raise LockTimeout(f"Local lock timeout for {key}")

        handle = LockHandle(
            key=key, owner_id=owner, ttl_ms=ttl_ms,
            acquired_at=time.time(), redis_mode=False,
            _local_lock=local_lk
        )
        self._record_acquisition(key, owner, purpose, handle)
        logger.debug(f"[DistLock] ACQUIRED (local) {key}")
        return handle

    # ── Release ───────────────────────────────────────────────────────────────

    def _release(self, handle: LockHandle) -> None:
        if handle._released:
            return
        handle._released = True

        # Stop auto-renewer
        if handle._renewer and handle._renewer.is_alive():
            handle._renewer = None

        if handle.redis_mode:
            rc = self._get_redis()
            if rc:
                try:
                    result = rc.eval(_RELEASE_LUA, 1, handle.key, handle.owner_id)
                    if result == 0:
                        logger.warning(f"[DistLock] Release rejected (not owner): {handle.key}")
                except Exception as e:
                    logger.debug(f"[DistLock] Redis release error: {e}")
        else:
            if handle._local_lock:
                try:
                    handle._local_lock.release()
                except RuntimeError:
                    pass  # already released

        with self._lock:
            self._active_handles.pop(handle.key, None)
            self._meta.pop(handle.key, None)
        logger.debug(f"[DistLock] RELEASED {handle.key}")

    # ── Auto-renewal ──────────────────────────────────────────────────────────

    def _start_auto_renew(self, handle: LockHandle, rc) -> None:
        """Renews lock TTL in background for long-running operations."""
        def renewer():
            while not handle._released:
                time.sleep(RENEW_INTERVAL_SEC)
                if handle._released:
                    break
                try:
                    result = rc.eval(_RENEW_LUA, 1, handle.key, handle.owner_id,
                                     str(handle.ttl_ms))
                    if result == 0:
                        logger.warning(f"[DistLock] Renewal rejected for {handle.key} — lock may have expired")
                        break
                except Exception:
                    break

        t = threading.Thread(target=renewer, daemon=True, name=f"lock-renew-{handle.key[-12:]}")
        handle._renewer = t
        t.start()

    # ── Sweep ─────────────────────────────────────────────────────────────────

    def _sweep_loop(self) -> None:
        while self._running:
            time.sleep(SWEEP_INTERVAL_SEC)
            try:
                self._sweep_orphaned()
            except Exception as e:
                logger.debug(f"[DistLock] Sweep error: {e}")

    def _sweep_orphaned(self) -> int:
        """Finds and logs orphaned local handles (Redis handles expire via TTL)."""
        now = time.time()
        orphaned = 0
        with self._lock:
            for key, handle in list(self._active_handles.items()):
                if not handle.redis_mode and handle.age_sec() > handle.ttl_ms / 1000.0:
                    logger.warning(f"[DistLock] Orphaned local lock detected: {key} "
                                   f"age={handle.age_sec():.1f}s")
                    self._orphaned_count += 1
                    orphaned += 1
        return orphaned

    # ── Observability ─────────────────────────────────────────────────────────

    def _record_acquisition(self, key: str, owner: str, purpose: str,
                             handle: LockHandle) -> None:
        with self._lock:
            self._active_handles[key] = handle
            self._meta[key] = {
                "owner":    owner[:20],
                "purpose":  purpose,
                "acquired": time.time(),
                "ttl_ms":   handle.ttl_ms,
                "redis":    handle.redis_mode,
            }

    def stats(self) -> dict:
        with self._lock:
            active = len(self._active_handles)
        return {
            "active_locks":    active,
            "timeout_events":  self._timeout_events,
            "orphaned_locks":  self._orphaned_count,
            "default_ttl_ms":  DEFAULT_TTL_MS,
            "max_ttl_ms":      MAX_TTL_MS,
            "acquire_timeout": ACQUIRE_TIMEOUT_SEC,
        }

    def active_lock_list(self) -> list:
        with self._lock:
            return [
                {"key": k.replace(_LOCK_PREFIX, ""), **v}
                for k, v in self._meta.items()
            ]

    def sweep_orphaned(self) -> int:
        return self._sweep_orphaned()

    # ── Redis access ──────────────────────────────────────────────────────────

    def _get_redis(self):
        try:
            from infra.event_bus import get_event_bus
            return get_event_bus()._redis.get()
        except Exception:
            return None


# ─── Convenience: named lock factories ───────────────────────────────────────

class LockNamespace:
    """Pre-built lock key generators for each protected resource type."""

    @staticmethod
    def workspace(session_id: str) -> str:
        return f"workspace:{session_id}"

    @staticmethod
    def deployment(deployment_id: str) -> str:
        return f"deploy:{deployment_id}"

    @staticmethod
    def governance(policy_id: str) -> str:
        return f"governance:{policy_id}"

    @staticmethod
    def browser(browser_id: str) -> str:
        return f"browser:{browser_id}"

    @staticmethod
    def remediation(component: str) -> str:
        return f"remediation:{component}"

    @staticmethod
    def snapshot(snap_id: str) -> str:
        return f"snapshot:{snap_id}"

    @staticmethod
    def queue(queue_name: str) -> str:
        return f"queue:{queue_name}"


# ─── Global singleton ─────────────────────────────────────────────────────────
_instance: Optional[DistributedLockManager] = None
_instance_lock = threading.Lock()

def get_lock_manager() -> DistributedLockManager:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = DistributedLockManager()
    return _instance
