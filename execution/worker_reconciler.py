"""
execution/worker_reconciler.py — Phase Z10: Worker Death Reconciliation
========================================================================

Implements the distributed execution reconciliation loop.

Responsibilities:
  1. Detect workers whose heartbeat has expired (presumed dead).
  2. For each dead worker: identify its owned sessions.
  3. Clear stale ownership records from Redis (nx:running:<wid>, nx:owner:<sid>).
  4. Mark interrupted sessions as orphans (nx:orphan:<sid>) so the recovery
     system can attempt resume.
  5. Emit HITL timeout on any HITL-paused sessions owned by the dead worker.
  6. Append a TASK_FAILED event to the ExecutionStore for each orphaned execution.
  7. Persist reconciliation metadata to sessions.db for audit.

Architecture:
  • Runs as a daemon thread inside each Gunicorn worker.
  • Uses a distributed lock (nx:reconcile:lock) to ensure only ONE worker
    runs reconciliation at a time, even across multiple Gunicorn workers.
  • Lock TTL = RECONCILE_INTERVAL × 2 to handle crash-of-reconciler scenarios.
  • Falls back gracefully when Redis is unavailable (no-op in local mode).

Usage:
    from execution.worker_reconciler import start_worker_reconciler
    start_worker_reconciler()   # call once at Flask app startup
"""

import json
import logging
import os
import sqlite3
import threading
import time
from typing import List, Dict, Optional

logger = logging.getLogger("nexora.worker_reconciler")

RECONCILE_INTERVAL  = int(os.getenv("NX_RECONCILE_INTERVAL", "30"))   # seconds
WORKER_HEARTBEAT_TTL = int(os.getenv("NX_WORKER_TTL", "60"))           # must match redis_layer
_RECONCILE_LOCK_KEY = "nx:reconcile:lock"
_RECONCILE_LOCK_TTL = RECONCILE_INTERVAL * 2

# ─────────────────────────────────────────────────────────────────────────────
# SQLite audit log
# ─────────────────────────────────────────────────────────────────────────────

_DB_PATH = os.getenv("NX_SESSIONS_DB", "sessions.db")


def _ensure_reconcile_table() -> None:
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reconciliation_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at      REAL    NOT NULL,
                    dead_worker TEXT    NOT NULL,
                    session_id  TEXT    NOT NULL,
                    action      TEXT    NOT NULL,
                    detail      TEXT
                )
            """)
            conn.commit()
    except Exception as e:
        logger.warning("[Reconciler] Could not create reconciliation_log table: %s", e)


def _log_reconcile_action(dead_worker: str, session_id: str, action: str, detail: str = "") -> None:
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                "INSERT INTO reconciliation_log (run_at, dead_worker, session_id, action, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (round(time.time(), 3), dead_worker, session_id, action, detail),
            )
            conn.commit()
    except Exception as e:
        logger.debug("[Reconciler] audit log write error: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Core reconciler
# ─────────────────────────────────────────────────────────────────────────────

class WorkerReconciler:
    """
    Scans Redis for dead workers and reconciles their orphaned sessions.

    Dead worker: a worker whose nx:worker:<wid> heartbeat key has expired
    (key no longer exists in Redis, meaning heartbeat TTL has elapsed).

    For each dead worker found to own a session:
      • Clears nx:running:<wid>
      • Clears nx:owner:<sid>
      • Sets nx:orphan:<sid> with reason=worker_death
      • Appends TASK_FAILED event to ExecutionStore
      • Resolves any stuck HITL pauses by injecting a timeout response
      • Logs the action to reconciliation_log SQLite table
    """

    def __init__(self):
        self._nx = None
        self._store = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def _lazy_init(self) -> bool:
        """Lazy-load Redis and ExecutionStore to avoid import-time circular deps."""
        if self._nx is not None:
            return self._nx.available

        try:
            from redis_layer import get_nx_redis
            self._nx = get_nx_redis()
        except Exception as e:
            logger.debug("[Reconciler] Redis layer unavailable: %s", e)
            return False

        if not self._nx.available:
            return False

        try:
            from execution.store import ExecutionStore
            self._store = ExecutionStore()
        except Exception as e:
            logger.warning("[Reconciler] ExecutionStore unavailable: %s", e)

        _ensure_reconcile_table()
        return True

    # ── Distributed lock ──────────────────────────────────────────────────────

    def _acquire_reconcile_lock(self) -> bool:
        """
        Attempt to acquire the distributed reconciliation lock via Redis SET NX.
        Returns True if this worker is the elected reconciler for this cycle.
        """
        if not self._nx or not self._nx.available:
            return True  # local mode: always reconcile (single worker)
        try:
            result = self._nx._r.set(
                _RECONCILE_LOCK_KEY,
                self._nx.worker_id,
                nx=True,           # only set if NOT already exists
                ex=_RECONCILE_LOCK_TTL,
            )
            return result is not None
        except Exception as e:
            logger.debug("[Reconciler] Lock acquisition error: %s", e)
            return False

    def _release_reconcile_lock(self) -> None:
        try:
            if self._nx and self._nx.available:
                self._nx._r.delete(_RECONCILE_LOCK_KEY)
        except Exception:
            pass

    # ── Identify dead workers ─────────────────────────────────────────────────

    def _find_dead_worker_sessions(self) -> List[Dict]:
        """
        Returns a list of {worker_id, session_id} pairs for workers that
        have an nx:running:<wid> key but NO corresponding nx:worker:<wid>
        heartbeat (heartbeat TTL expired → worker presumed dead).
        """
        if not self._nx or not self._nx.available:
            return []

        dead = []
        try:
            running_keys = self._nx._r.keys("nx:running:*") or []
            for key in running_keys:
                wid = key[len("nx:running:"):]
                # Check if heartbeat is still alive
                heartbeat_exists = self._nx._r.exists(f"nx:worker:{wid}")
                if not heartbeat_exists:
                    sid = self._nx._r.get(key)
                    if sid:
                        dead.append({"worker_id": wid, "session_id": sid, "running_key": key})
        except Exception as e:
            logger.warning("[Reconciler] Dead worker scan error: %s", e)

        return dead

    # ── Per-orphan remediation ────────────────────────────────────────────────

    def _remediate_orphan(self, worker_id: str, session_id: str, running_key: str) -> None:
        """
        Performs all remediation steps for one orphaned session.
        """
        logger.warning(
            "[Reconciler] Orphan detected — worker=%s session=%s",
            worker_id, session_id,
        )

        # 1. Clear stale ownership
        try:
            pipe = self._nx._r.pipeline(transaction=False)
            pipe.delete(running_key)                          # nx:running:<wid>
            pipe.delete(f"nx:owner:{session_id}")             # nx:owner:<sid>
            pipe.delete(f"nx:proc:{session_id}")              # nx:proc:<sid>
            pipe.execute()
            _log_reconcile_action(worker_id, session_id, "ownership_cleared")
        except Exception as e:
            logger.warning("[Reconciler] Ownership clear error for %s: %s", session_id, e)

        # 2. Mark as orphan (recoverable)
        try:
            self._nx.mark_orphan(session_id, reason="worker_death")
            _log_reconcile_action(worker_id, session_id, "orphan_marked")
        except Exception as e:
            logger.warning("[Reconciler] Orphan mark error for %s: %s", session_id, e)

        # 3. Append TASK_FAILED to ExecutionStore (replay durability)
        if self._store:
            try:
                from execution.events import create_event, EventTypes
                # Use a synthetic execution_id for the recovery event
                synthetic_eid = f"reconcile_{session_id}_{int(time.time())}"
                fail_evt = create_event(
                    EventTypes.TASK_FAILED,
                    session_id,
                    synthetic_eid,
                    error=f"Worker {worker_id} died mid-execution — session marked recoverable.",
                )
                self._store.append_event(fail_evt, correlation_id="z10_reconciler")
                self._store.upsert_execution(
                    execution_id=synthetic_eid,
                    session_id=session_id,
                    status="failed",
                    payload={"recovery": "orphaned", "dead_worker": worker_id},
                )
                _log_reconcile_action(worker_id, session_id, "store_event_appended", synthetic_eid)
            except Exception as e:
                logger.warning("[Reconciler] ExecutionStore write error for %s: %s", session_id, e)

        # 4. Resolve stuck HITL pause — inject a timeout response
        try:
            hitl_state = self._nx.hitl_get(session_id)
            if hitl_state.get("paused"):
                # Inject a synthetic timeout message so the HITL thread unblocks
                self._nx.hitl_inject(session_id, "__RECONCILER_TIMEOUT__")
                _log_reconcile_action(
                    worker_id, session_id, "hitl_timeout_injected",
                    "HITL pause resolved by reconciler (worker death)",
                )
                logger.info("[Reconciler] HITL timeout injected for session %s", session_id)

                # Also update the hitl_requests SQLite table so UI reflects the resolution
                try:
                    with sqlite3.connect(_DB_PATH) as conn:
                        conn.execute(
                            "UPDATE hitl_requests SET status = 'timeout', feedback = ? "
                            "WHERE execution_id LIKE ? AND status = 'pending'",
                            (
                                f"Worker {worker_id} died — auto-resolved by reconciler",
                                f"%{session_id}%",
                            ),
                        )
                        conn.commit()
                except Exception as _sql_err:
                    logger.debug("[Reconciler] hitl_requests update error: %s", _sql_err)
        except Exception as e:
            logger.warning("[Reconciler] HITL resolution error for %s: %s", session_id, e)

        # 5. Clear any stale stop signal to prevent false-positive on resume
        try:
            self._nx.clear_stop_signal(session_id)
        except Exception:
            pass

        logger.info("[Reconciler] Remediation complete for session %s", session_id)

    # ── Main reconcile cycle ──────────────────────────────────────────────────

    def reconcile_once(self) -> int:
        """
        Run a single reconciliation sweep. Returns count of orphans processed.
        Thread-safe; distributed-lock-guarded.
        """
        if not self._lazy_init():
            return 0

        if not self._acquire_reconcile_lock():
            logger.debug("[Reconciler] Lock held by another worker — skipping cycle")
            return 0

        count = 0
        try:
            dead_sessions = self._find_dead_worker_sessions()
            for entry in dead_sessions:
                try:
                    self._remediate_orphan(
                        worker_id=entry["worker_id"],
                        session_id=entry["session_id"],
                        running_key=entry["running_key"],
                    )
                    count += 1
                except Exception as e:
                    logger.exception(
                        "[Reconciler] Unhandled error remediating session %s: %s",
                        entry.get("session_id"), e,
                    )

            if count:
                logger.info("[Reconciler] Cycle complete — %d orphan(s) remediated", count)
        finally:
            self._release_reconcile_lock()

        return count

    # ── Background thread ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background reconciliation daemon thread."""
        if self._running:
            return
        self._running = True

        def _loop():
            # Stagger startup to avoid thundering-herd on multi-worker boot
            time.sleep(RECONCILE_INTERVAL * 0.5 + (os.getpid() % 7))
            while self._running:
                try:
                    self.reconcile_once()
                except Exception as e:
                    logger.exception("[Reconciler] Unexpected error in reconcile loop: %s", e)
                time.sleep(RECONCILE_INTERVAL)

        self._thread = threading.Thread(
            target=_loop,
            daemon=True,
            name="nx-worker-reconciler",
        )
        self._thread.start()
        logger.info(
            "[Reconciler] Background reconciler started (interval=%ds, lock_ttl=%ds)",
            RECONCILE_INTERVAL, _RECONCILE_LOCK_TTL,
        )

    def stop(self) -> None:
        self._running = False


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton + startup helper
# ─────────────────────────────────────────────────────────────────────────────

_reconciler: Optional[WorkerReconciler] = None
_reconciler_lock = threading.Lock()


def start_worker_reconciler() -> WorkerReconciler:
    """
    Initialise and start the global WorkerReconciler singleton.
    Safe to call multiple times (idempotent).
    """
    global _reconciler
    with _reconciler_lock:
        if _reconciler is None:
            _reconciler = WorkerReconciler()
            _reconciler.start()
    return _reconciler


def get_reconciler() -> Optional[WorkerReconciler]:
    return _reconciler
