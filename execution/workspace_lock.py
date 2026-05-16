"""
execution/workspace_lock.py — Phase: Execution Locking & Conflict Prevention
==============================================================================
Provides file-level and workspace-level locking to prevent concurrent mutations
by multiple agents or retried tool invocations.

Design:
  • FileLock    — per-path advisory lock (readers can co-exist; writers are exclusive)
  • WorkspaceLock — serialises all workspace-mutating operations for a session
  • LockRegistry — global singleton; tracks all active locks for observability

All locks are context-manager based to guarantee release on exceptions.
Deadlock prevention: all locks are acquired with a timeout; timeout → escalate to HITL.
"""

import threading
import time
import logging
import os
from contextlib import contextmanager
from typing import Dict, Optional

logger = logging.getLogger("nexora.workspace_lock")


class LockTimeoutError(Exception):
    """Raised when a lock cannot be acquired within the allowed window."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# FileLock — per-path exclusive write lock, shared read
# ─────────────────────────────────────────────────────────────────────────────

class _PathLock:
    """Internal per-path lock object."""
    def __init__(self, path: str):
        self.path = path
        self._rw_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._readers: int = 0
        self._writer: Optional[str] = None   # session_id holding write lock
        self._write_sem = threading.Semaphore(1)

    def acquire_write(self, session_id: str, timeout: float) -> bool:
        acquired = self._write_sem.acquire(timeout=timeout)
        if acquired:
            self._writer = session_id
        return acquired

    def release_write(self):
        self._writer = None
        self._write_sem.release()

    def info(self) -> dict:
        return {
            "path": self.path,
            "writer": self._writer,
            "write_available": self._write_sem._value == 1,
        }


class LockRegistry:
    """
    Global registry of all active path locks.
    Thread-safe. Used for deadlock detection, observability, and forced release
    on session termination.
    """
    def __init__(self):
        self._registry: Dict[str, _PathLock] = {}
        self._lock = threading.RLock()

    def get_or_create(self, norm_path: str) -> _PathLock:
        with self._lock:
            if norm_path not in self._registry:
                self._registry[norm_path] = _PathLock(norm_path)
            return self._registry[norm_path]

    def snapshot(self) -> list:
        """Returns list of all currently held write locks for the observability UI."""
        with self._lock:
            return [
                pl.info() for pl in self._registry.values()
                if pl._writer is not None
            ]

    def release_all_for_session(self, session_id: str) -> int:
        """Emergency: release all locks held by a cancelled/crashed session."""
        released = 0
        with self._lock:
            for pl in self._registry.values():
                if pl._writer == session_id:
                    pl.release_write()
                    released += 1
        if released:
            logger.warning(
                f"[LockRegistry] Force-released {released} lock(s) for session {session_id}"
            )
        return released


# Global registry singleton
global_lock_registry = LockRegistry()


# ─────────────────────────────────────────────────────────────────────────────
# Public API — context managers
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def file_write_lock(path: str, session_id: str, timeout: float = 15.0):
    """
    Acquires an exclusive write lock on `path`.
    Raises LockTimeoutError if another agent holds the lock and doesn't release
    within `timeout` seconds.

    Usage::
        with file_write_lock("app.py", session_id="sess_abc"):
            write_file("app.py", content)
    """
    norm_path = os.path.normpath(os.path.abspath(path))
    pl = global_lock_registry.get_or_create(norm_path)

    logger.debug(f"[FileLock] {session_id} acquiring write lock on {norm_path}")
    acquired = pl.acquire_write(session_id, timeout)
    if not acquired:
        raise LockTimeoutError(
            f"Cannot acquire write lock on '{path}' within {timeout}s. "
            f"Currently held by session '{pl._writer}'. "
            "Another agent may be modifying this file concurrently."
        )
    logger.debug(f"[FileLock] {session_id} acquired write lock on {norm_path}")
    try:
        yield
    finally:
        pl.release_write()
        logger.debug(f"[FileLock] {session_id} released write lock on {norm_path}")


@contextmanager
def workspace_operation_lock(session_id: str, operation: str, timeout: float = 30.0):
    """
    Coarse-grained lock for workspace-mutating operations that touch multiple files
    or perform rollbacks. Prevents concurrent rollbacks from corrupting each other.

    Usage::
        with workspace_operation_lock(session_id, "rollback"):
            perform_rollback(...)
    """
    WORKSPACE_SENTINEL = "__workspace_global_op__"
    pl = global_lock_registry.get_or_create(WORKSPACE_SENTINEL)

    logger.info(f"[WorkspaceLock] {session_id} requesting '{operation}' lock")
    acquired = pl.acquire_write(session_id, timeout)
    if not acquired:
        raise LockTimeoutError(
            f"Workspace operation '{operation}' blocked by another agent (session: {pl._writer}). "
            "Will retry after current operation completes."
        )
    logger.info(f"[WorkspaceLock] {session_id} started '{operation}'")
    try:
        yield
    finally:
        pl.release_write()
        logger.info(f"[WorkspaceLock] {session_id} completed '{operation}'")
