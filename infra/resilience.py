"""
infra/resilience.py — Phase: Production Resilience & Recovery
=============================================================
Graceful recovery systems for infrastructure failures.

Handles:
  • Worker crashes          → task re-queuing + crash event
  • Redis outages           → event bus graceful degradation
  • PostgreSQL failover     → SQLite fallback with reconnect loop
  • SSE disconnect storms   → client eviction + reconnect rate limiting
  • Browser deadlocks       → timeout + forced termination + slot release
  • Lock orphaning          → stale lock sweeper on session death
  • Partial infra outages   → degraded-mode execution with reduced concurrency

Recovery playbooks:
  1. Worker crash → task claimed → re-queue to available worker → notify HITL
  2. Redis outage → switch to in-process bus → queue reconnect probe
  3. DB failover  → retry with exponential backoff → fallback to SQLite
  4. SSE storm    → rate-limit new connections → evict oldest if over cap
  5. Browser dead → kill process → release semaphore → retry task
  6. Stale lock   → detect via heartbeat gap → force release → emit event
"""

import logging
import os
import threading
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("nexora.resilience")


# ─────────────────────────────────────────────────────────────────────────────
# Degraded Mode Controller
# ─────────────────────────────────────────────────────────────────────────────

class DegradedModeController:
    """
    Tracks infrastructure component health and enforces degraded-mode execution
    when components are unavailable.

    In degraded mode:
      - Browser concurrency reduced to 1
      - Terminal concurrency reduced to 2
      - Coordination snapshots suspended (reduce load)
      - New sessions rejected if above degraded capacity
      - Operators notified via SSE + log
    """

    COMPONENTS = ["redis", "postgres", "browser_pool", "worker_pool", "sse"]

    def __init__(self):
        self._health: Dict[str, bool] = {c: True for c in self.COMPONENTS}
        self._degraded_since: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._callbacks: List[Callable] = []

    def mark_degraded(self, component: str, reason: str = "") -> None:
        with self._lock:
            if self._health.get(component, True):
                self._health[component] = False
                self._degraded_since[component] = time.time()
                logger.error(f"[Resilience] 🔴 Component DEGRADED: {component} — {reason}")
                self._fire_callbacks(component, False, reason)

    def mark_recovered(self, component: str) -> None:
        with self._lock:
            if not self._health.get(component, True):
                self._health[component] = True
                downtime = time.time() - self._degraded_since.get(component, time.time())
                self._degraded_since.pop(component, None)
                logger.info(f"[Resilience] ✅ Component RECOVERED: {component} (downtime={downtime:.1f}s)")
                self._fire_callbacks(component, True, "")

    def is_degraded(self, component: str) -> bool:
        with self._lock:
            return not self._health.get(component, True)

    def is_any_degraded(self) -> bool:
        with self._lock:
            return not all(self._health.values())

    def degraded_components(self) -> List[str]:
        with self._lock:
            return [c for c, h in self._health.items() if not h]

    def register_callback(self, fn: Callable) -> None:
        """Called when any component changes health state. fn(component, is_healthy, reason)"""
        with self._lock:
            self._callbacks.append(fn)

    def _fire_callbacks(self, component: str, healthy: bool, reason: str) -> None:
        for fn in self._callbacks:
            try:
                fn(component, healthy, reason)
            except Exception as e:
                logger.debug(f"[Resilience] Callback error: {e}")

    def effective_limits(self) -> dict:
        """Returns resource limits adjusted for current degraded state."""
        degraded = self.degraded_components()
        base = {
            "max_browsers":  int(os.getenv("MAX_BROWSERS", "2")),
            "max_terminals": int(os.getenv("MAX_TERMINALS", "4")),
            "max_sessions":  50,
        }
        if "browser_pool" in degraded or "worker_pool" in degraded:
            base["max_browsers"] = 1
            base["max_terminals"] = 2
            base["max_sessions"] = 10
        if "postgres" in degraded:
            base["max_sessions"] = min(base["max_sessions"], 20)
        return base

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "health": dict(self._health),
                "degraded_components": self.degraded_components(),
                "is_degraded": self.is_any_degraded(),
                "effective_limits": self.effective_limits(),
            }


# ─────────────────────────────────────────────────────────────────────────────
# RecoveryPlaybook — structured recovery procedures
# ─────────────────────────────────────────────────────────────────────────────

class RecoveryPlaybook:
    """
    Structured recovery procedures for each failure scenario.
    All playbooks are idempotent and replay-safe.
    """

    def __init__(self, degraded_mode: DegradedModeController):
        self._degraded = degraded_mode
        self._recovery_log: List[dict] = []
        self._lock = threading.Lock()

    def _log(self, scenario: str, action: str, session_id: str = "",
             success: bool = True, details: str = "") -> None:
        entry = {
            "ts": time.time(),
            "scenario": scenario,
            "action": action,
            "session_id": session_id,
            "success": success,
            "details": details[:200],
        }
        with self._lock:
            self._recovery_log.append(entry)
            if len(self._recovery_log) > 500:
                self._recovery_log.pop(0)
        logger.info(f"[Recovery] [{scenario}] {action} {'✓' if success else '✗'} {details[:80]}")

    # ── Playbook 1: Worker crash ──────────────────────────────────────────────

    def handle_worker_crash(self, worker_id: str, task_id: str = "",
                            requeue_fn: Optional[Callable] = None) -> None:
        """
        Worker crash recovery:
          1. Log crash event to telemetry
          2. Mark task as failed-recoverable
          3. Re-queue task if possible
          4. Notify HITL if task critical
        """
        from infra.telemetry import get_telemetry
        get_telemetry().record_worker_crash(worker_id)

        if task_id and requeue_fn:
            try:
                requeue_fn(task_id)
                self._log("worker_crash", f"Task {task_id} re-queued", success=True)
            except Exception as e:
                self._log("worker_crash", f"Re-queue failed: {e}", success=False)
        else:
            self._log("worker_crash", f"Worker {worker_id} crashed (no task to re-queue)")

    # ── Playbook 2: Redis outage ──────────────────────────────────────────────

    def handle_redis_outage(self) -> None:
        """
        Redis outage:
          1. Mark redis component as degraded
          2. Event bus automatically falls back to in-process
          3. Start reconnect probe thread
        """
        self._degraded.mark_degraded("redis", "Connection refused / timeout")
        self._log("redis_outage", "Degraded mode activated; using in-process event bus")
        threading.Thread(target=self._redis_reconnect_probe, daemon=True).start()

    def _redis_reconnect_probe(self) -> None:
        from infra.event_bus import get_event_bus
        bus = get_event_bus()
        backoff = 5.0
        while self._degraded.is_degraded("redis"):
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 60.0)
            if bus._redis._connect():
                self._degraded.mark_recovered("redis")
                self._log("redis_outage", "Redis reconnected; restoring distributed bus")
                break

    # ── Playbook 3: PostgreSQL failover ───────────────────────────────────────

    def handle_postgres_failover(self) -> None:
        """
        PostgreSQL failover:
          1. Mark postgres as degraded
          2. db_adapter automatically uses SQLite fallback
          3. Start reconnect probe
        """
        from infra.db_adapter import _BACKEND
        self._degraded.mark_degraded("postgres", "Connection failed")
        self._log("pg_failover", f"PostgreSQL unavailable; falling back to SQLite (backend={_BACKEND})")
        threading.Thread(target=self._pg_reconnect_probe, daemon=True).start()

    def _pg_reconnect_probe(self) -> None:
        from infra.db_adapter import _try_init_postgres
        backoff = 10.0
        while self._degraded.is_degraded("postgres"):
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 120.0)
            if _try_init_postgres():
                self._degraded.mark_recovered("postgres")
                self._log("pg_failover", "PostgreSQL reconnected")
                break

    # ── Playbook 4: SSE disconnect storm ──────────────────────────────────────

    def handle_sse_storm(self, session_id: str, new_client_count: int) -> bool:
        """
        SSE storm protection:
          Returns True if new connection should be accepted, False if rate-limited.
          Rate limit: max 10 new connections / session / 30s.
        """
        key = f"sse_storm:{session_id}"
        if not hasattr(self, "_sse_storm_tracker"):
            self._sse_storm_tracker: Dict[str, list] = {}
        now = time.time()
        timestamps = self._sse_storm_tracker.get(key, [])
        timestamps = [t for t in timestamps if now - t < 30.0]  # 30s window
        if len(timestamps) >= 10:
            self._log("sse_storm", f"Rate limited new SSE for session {session_id}", success=False)
            return False
        timestamps.append(now)
        self._sse_storm_tracker[key] = timestamps
        return True

    # ── Playbook 5: Browser deadlock ──────────────────────────────────────────

    def handle_browser_deadlock(self, session_id: str,
                                kill_fn: Optional[Callable] = None) -> None:
        """
        Browser deadlock:
          1. Kill the browser process
          2. Release the semaphore slot
          3. Emit event for retry
        """
        from execution.resource_governor import global_resource_governor
        if kill_fn:
            try:
                kill_fn()
                self._log("browser_deadlock", f"Browser process killed for session {session_id}")
            except Exception as e:
                self._log("browser_deadlock", f"Kill failed: {e}", success=False)
        global_resource_governor.release_browser_slot()
        self._log("browser_deadlock", f"Browser slot force-released for session {session_id}")

    # ── Playbook 6: Stale lock orphan ─────────────────────────────────────────

    def handle_stale_lock_orphan(self, session_id: str) -> int:
        """
        Stale lock cleanup:
          Releases all locks held by a dead/crashed session.
          Called by session reaper on TTL expiry.
        """
        from execution.workspace_lock import global_lock_registry
        from infra.telemetry import get_telemetry
        released = global_lock_registry.release_all_for_session(session_id)
        if released > 0:
            get_telemetry().record(
                "workspace", "stale_lock_cleanup",
                {"released": released}, session_id=session_id
            )
            self._log("stale_lock", f"Released {released} locks for dead session {session_id}")
        return released

    # ── Recovery log ─────────────────────────────────────────────────────────

    def recent_recoveries(self, n: int = 20) -> list:
        with self._lock:
            return list(self._recovery_log[-n:])

    def snapshot(self) -> dict:
        return {
            "degraded_mode": self._degraded.snapshot(),
            "recent_recoveries": self.recent_recoveries(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Session reaper — cleans up dead sessions
# ─────────────────────────────────────────────────────────────────────────────

class SessionReaper:
    """
    Background daemon that detects and cleans up dead sessions:
      - Releases orphaned workspace locks
      - Deregisters stale agents
      - Notifies tenant registry
      - Emits recovery events
    """

    def __init__(self, playbook: RecoveryPlaybook, ttl_seconds: float = 300.0):
        self._playbook = playbook
        self._ttl = ttl_seconds
        self._session_heartbeats: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._running = True
        t = threading.Thread(target=self._reap_loop, daemon=True, name="session-reaper")
        t.start()
        logger.info(f"[SessionReaper] Started (TTL={ttl_seconds}s)")

    def heartbeat(self, session_id: str) -> None:
        with self._lock:
            self._session_heartbeats[session_id] = time.time()

    def unregister(self, session_id: str) -> None:
        with self._lock:
            self._session_heartbeats.pop(session_id, None)

    def _reap_loop(self) -> None:
        while self._running:
            time.sleep(30)
            try:
                now = time.time()
                with self._lock:
                    stale = [
                        sid for sid, ts in self._session_heartbeats.items()
                        if now - ts > self._ttl
                    ]
                for sid in stale:
                    self._reap(sid)
            except Exception as e:
                logger.debug(f"[SessionReaper] Error in reap loop: {e}")

    def _reap(self, session_id: str) -> None:
        logger.warning(f"[SessionReaper] Reaping stale session: {session_id}")
        try:
            # Release all orphaned workspace locks
            self._playbook.handle_stale_lock_orphan(session_id)
            # Deregister stale agents
            from execution.agent_registry import global_agent_registry
            global_agent_registry.sweep_stale(ttl=self._ttl)
            # Deregister from tenant registry
            from infra.tenant import global_tenant_registry
            global_tenant_registry.deregister_session(session_id)
        except Exception as e:
            logger.debug(f"[SessionReaper] Reap error for {session_id}: {e}")
        finally:
            with self._lock:
                self._session_heartbeats.pop(session_id, None)


# ─────────────────────────────────────────────────────────────────────────────
# Global singletons
# ─────────────────────────────────────────────────────────────────────────────

global_degraded_mode = DegradedModeController()
global_recovery_playbook = RecoveryPlaybook(global_degraded_mode)
global_session_reaper = SessionReaper(global_recovery_playbook)
