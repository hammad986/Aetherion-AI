"""
redis_layer.py — Nexora Multi-Worker Coordination Layer
═════════════════════════════════════════════════════════
Phase Z7: Eliminates all single-worker runtime assumptions.

Architecture
───────────
When REDIS_URL is set:
  • Task queue  → Redis LIST          nx:queue
  • Running     → Redis STRING        nx:running:<worker_id>
  • Owner map   → Redis STRING        nx:owner:<sid>
  • Stop signal → Redis STRING        nx:stop:<sid>
  • HITL state  → Redis HASH          nx:hitl:<sid>
  • Heartbeat   → Redis HASH          nx:worker:<worker_id>

When REDIS_URL is not set (or Redis is unreachable):
  • All operations fall back to the in-process runtime.state structures
  • Behavior is identical to the pre-Z7 single-worker mode
  • No exceptions are raised — every method has a silent fallback path

Thread Safety
─────────────
All Redis operations are serialised through redis-py's connection pool.
All local fallback operations use the existing locks from runtime.state.

Usage
─────
    from redis_layer import get_nx_redis
    _nx_redis = get_nx_redis()          # call once at module level
    # Then use _nx_redis.push(sid), _nx_redis.pop_blocking(), etc.
"""

import json
import logging
import os
import threading
import time

logger = logging.getLogger("nexora.redis")

# ── Key schema ──────────────────────────────────────────────────────────────────
_QUEUE_KEY      = "nx:queue"          # LIST of pending session IDs
_RUNNING_PFX    = "nx:running:"       # STRING per worker  — nx:running:<wid>
_OWNER_PFX      = "nx:owner:"         # STRING per session — nx:owner:<sid>
_STOP_PFX       = "nx:stop:"          # STRING flag        — nx:stop:<sid>
_HITL_PFX       = "nx:hitl:"          # HASH               — nx:hitl:<sid>
_WORKER_PFX     = "nx:worker:"        # HASH               — nx:worker:<wid>

_RUNNING_TTL    = int(os.getenv("NX_RUNNING_TTL", "600"))    # 10 min
_WORKER_TTL     = int(os.getenv("NX_WORKER_TTL", "60"))      # 1 min heartbeat
_HITL_TTL       = int(os.getenv("NX_HITL_TTL", "3600"))      # 1 hour HITL window
_STOP_TTL       = 300                                          # 5 min stop signal TTL
_STOP_ACK_TTL   = 60                                           # 1 min stop acknowledgement TTL
_ORPHAN_TTL     = int(os.getenv("NX_ORPHAN_TTL", "3600"))    # 1 hour orphan marker TTL
_PROC_TTL       = int(os.getenv("NX_PROC_TTL", "600"))       # 10 min proc PID record

_STOP_ACK_PFX   = "nx:stop_ack:"       # STRING — nx:stop_ack:<sid>
_ORPHAN_PFX     = "nx:orphan:"         # STRING — nx:orphan:<sid> (recoverable session)
_PROC_PFX       = "nx:proc:"           # HASH   — nx:proc:<sid> {pid, worker_id, started_at}


class NexoraRedisLayer:
    """
    Distributed coordination layer for multi-worker Gunicorn deployments.
    Provides a graceful in-process fallback when Redis is unavailable.
    """

    def __init__(self):
        self._r       = None
        self._ok      = False               # True only when Redis is reachable
        self._wid     = f"w{os.getpid()}"  # unique per worker process

        # ── Local fallback state (shared via runtime.state references) ──────────
        try:
            from runtime.state import (
                queue_lock, pending_queue, running,
                _hitl_state, _hitl_lock,
            )
            self._local_lock    = queue_lock
            self._local_queue   = pending_queue
            self._local_running = running
            self._local_hitl    = _hitl_state
            self._local_hitl_lk = _hitl_lock
        except Exception as e:
            logger.warning("[NexoraRedis] runtime.state import failed: %s — creating standalone fallback", e)
            self._local_lock    = threading.Lock()
            self._local_queue   = __import__("collections").deque()
            self._local_running = {"sid": None, "proc": None, "seq": 0}
            self._local_hitl    = {}
            self._local_hitl_lk = threading.Lock()

        self._heartbeat_thread: threading.Thread | None = None
        self._connect()

    # ── Connection ──────────────────────────────────────────────────────────────

    def _connect(self):
        url = os.getenv("REDIS_URL", "").strip()
        if not url or os.getenv("NX_REDIS_DISABLED", "0") == "1":
            logger.info("[NexoraRedis] Redis disabled — using in-process fallback (single-worker mode)")
            return
        try:
            import redis as _rmod
            self._r = _rmod.from_url(
                url,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=3,
                retry_on_timeout=True,
                health_check_interval=30,
                max_connections=20,
            )
            self._r.ping()
            self._ok = True
            logger.info("[NexoraRedis] Connected  worker=%s  url=%s", self._wid, url[:40])
            self._start_heartbeat()
        except Exception as e:
            self._ok = False
            logger.warning("[NexoraRedis] Redis unavailable (%s) — falling back to in-process", e)

    def _redis_call(self, fn, *args, **kwargs):
        """Execute a Redis call; on any error, return None and log."""
        if not self._ok or not self._r:
            return None
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            logger.warning("[NexoraRedis] Redis error in %s: %s", fn.__name__, e)
            return None

    # ── Task Queue ──────────────────────────────────────────────────────────────

    def push(self, sid: str) -> None:
        """Enqueue a session id for execution."""
        pushed = self._redis_call(self._r.lpush, _QUEUE_KEY, sid) if self._ok else None
        if pushed is None:
            with self._local_lock:
                self._local_queue.append(sid)

    def pop_blocking(self, timeout: int = 1) -> str | None:
        """
        Dequeue the next pending session, blocking up to timeout seconds.
        Returns the session id or None on timeout.
        """
        if self._ok and self._r:
            result = self._redis_call(self._r.brpop, _QUEUE_KEY, timeout=timeout)
            if result:
                return result[1]          # (key, value) tuple
            return None
        # Local fallback — non-blocking, caller will sleep
        with self._local_lock:
            if self._local_queue:
                return self._local_queue.popleft()
        return None

    def list_pending(self) -> list:
        """Return all pending session IDs (for /api/queue display)."""
        if self._ok and self._r:
            result = self._redis_call(self._r.lrange, _QUEUE_KEY, 0, -1)
            if result is not None:
                return list(result)
        with self._local_lock:
            return list(self._local_queue)

    def queue_depth(self) -> int:
        if self._ok and self._r:
            result = self._redis_call(self._r.llen, _QUEUE_KEY)
            if result is not None:
                return int(result)
        with self._local_lock:
            return len(self._local_queue)

    def remove_from_queue(self, sid: str) -> None:
        """Remove a specific sid from the pending queue (for cancellation)."""
        if self._ok and self._r:
            self._redis_call(self._r.lrem, _QUEUE_KEY, 0, sid)
        with self._local_lock:
            try:
                self._local_queue.remove(sid)
            except ValueError:
                pass

    # ── Running State ───────────────────────────────────────────────────────────

    def set_running(self, sid: str | None, seq: int = 0) -> None:
        """Record that this worker is now running session `sid`."""
        self._local_running["sid"] = sid
        self._local_running["seq"] = seq
        if self._ok and self._r:
            wkey = f"{_RUNNING_PFX}{self._wid}"
            if sid:
                self._redis_call(self._r.set, wkey, sid, ex=_RUNNING_TTL)
                self._redis_call(self._r.set, f"{_OWNER_PFX}{sid}", self._wid, ex=_RUNNING_TTL)
            else:
                self._redis_call(self._r.delete, wkey)

    def set_proc(self, proc) -> None:
        """Store the subprocess object (local only — not serialisable to Redis)."""
        self._local_running["proc"] = proc

    def get_proc(self):
        return self._local_running.get("proc")

    def get_local_running_sid(self) -> str | None:
        """Session running on THIS worker."""
        return self._local_running.get("sid")

    def get_any_running_sid(self) -> str | None:
        """
        Return a running session id from ANY worker.
        Falls back to local if Redis unavailable.
        """
        local = self._local_running.get("sid")
        if local:
            return local
        if self._ok and self._r:
            keys = self._redis_call(self._r.keys, f"{_RUNNING_PFX}*") or []
            for k in keys:
                v = self._redis_call(self._r.get, k)
                if v:
                    return v
        return None

    def list_running_workers(self) -> list:
        """Return {worker_id, sid} for all workers currently executing."""
        result = []
        if self._ok and self._r:
            keys = self._redis_call(self._r.keys, f"{_RUNNING_PFX}*") or []
            for k in keys:
                v = self._redis_call(self._r.get, k)
                if v:
                    wid = k[len(_RUNNING_PFX):]
                    result.append({"worker_id": wid, "sid": v})
        elif self._local_running.get("sid"):
            result.append({"worker_id": self._wid, "sid": self._local_running["sid"]})
        return result

    def release_running(self) -> None:
        """Clear this worker's running-session record (Z10: also clears proc PID tracking)."""
        old_sid = self._local_running.get("sid")
        self._local_running["sid"]  = None
        self._local_running["proc"] = None
        self._local_running["seq"]  = 0
        self._local_running["pid"]  = None
        if self._ok and self._r and old_sid:
            pipe = self._r.pipeline(transaction=False)
            pipe.delete(f"{_RUNNING_PFX}{self._wid}")
            pipe.delete(f"{_OWNER_PFX}{old_sid}")
            pipe.delete(f"{_STOP_PFX}{old_sid}")
            pipe.delete(f"{_PROC_PFX}{old_sid}")
            try:
                pipe.execute()
            except Exception as e:
                logger.warning("[NexoraRedis] release_running pipeline error: %s", e)

    # ── Proc PID Tracking (Z10) ─────────────────────────────────────────────────

    def set_proc_pid(self, sid: str, pid: int) -> None:
        """
        Record the PID of the subprocess spawned for this session.
        Stored in Redis for audit/diagnostics. Not cross-killable, but
        enables reconciliation to identify zombie PIDs after worker death.
        """
        self._local_running["pid"] = pid
        if self._ok and self._r:
            key = f"{_PROC_PFX}{sid}"
            self._redis_call(self._r.hset, key, mapping={
                "pid":       str(pid),
                "worker_id": self._wid,
                "started_at": str(round(time.time(), 3)),
            })
            self._redis_call(self._r.expire, key, _PROC_TTL)

    def get_proc_info(self, sid: str) -> dict:
        """Return {pid, worker_id, started_at} for the session's subprocess, or {}."""
        if self._ok and self._r:
            return self._redis_call(self._r.hgetall, f"{_PROC_PFX}{sid}") or {}
        pid = self._local_running.get("pid")
        return {"pid": str(pid), "worker_id": self._wid} if pid else {}

    def clear_proc_pid(self, sid: str) -> None:
        self._local_running["pid"] = None
        if self._ok and self._r:
            self._redis_call(self._r.delete, f"{_PROC_PFX}{sid}")

    # ── Stop Signals (Z10 — hardened) ───────────────────────────────────────────

    def request_stop(self, sid: str) -> bool:
        """
        Signal any worker to stop session `sid`.

        Z10 contract:
          • Idempotent — safe to call multiple times; ack check prevents re-fire.
          • Cross-worker safe — Redis flag polled by owning worker's execution loop.
          • Auto-expiring — flag TTL prevents stale stop signals from affecting
            future executions of the same session.
          • Returns False immediately if a stop is already acknowledged.
        """
        # Guard: don't re-issue if already acked
        if self.is_stop_acked(sid):
            logger.debug("[NexoraRedis] request_stop(%s): already acked — no-op", sid)
            return False

        # Set the distributed stop flag (idempotent SET with TTL)
        if self._ok and self._r:
            self._redis_call(self._r.set, f"{_STOP_PFX}{sid}", "1", ex=_STOP_TTL)
            logger.info("[NexoraRedis] Stop signal set for session %s (worker=%s)", sid, self._wid)
            return True

        # Local fallback — same-worker path; ExecutionTask.cancel() handles it
        if self._local_running.get("sid") == sid:
            logger.info("[NexoraRedis] Stop signal (local) for session %s", sid)
            return True

        return False

    def check_stop_requested(self, sid: str) -> bool:
        """
        Owning worker calls this inside the execution loop to detect cross-worker
        stop requests. Returns True if stop is signalled and not yet acknowledged.
        """
        # Local flag check (same-process cancellation)
        if self._local_running.get("sid") == sid:
            proc = self._local_running.get("proc")
            if proc is not None and hasattr(proc, "poll") and proc.poll() is not None:
                return True  # proc already dead

        if not self._ok or not self._r:
            return False
        val = self._redis_call(self._r.get, f"{_STOP_PFX}{sid}")
        return val == "1"

    def ack_stop(self, sid: str) -> None:
        """
        Owning worker calls this after it has performed the actual process
        termination. Clears the stop flag and writes the ack key.
        Prevents duplicate stop attempts.
        """
        if self._ok and self._r:
            pipe = self._r.pipeline(transaction=False)
            pipe.delete(f"{_STOP_PFX}{sid}")
            pipe.set(f"{_STOP_ACK_PFX}{sid}", self._wid, ex=_STOP_ACK_TTL)
            try:
                pipe.execute()
            except Exception as e:
                logger.warning("[NexoraRedis] ack_stop pipeline error: %s", e)
        else:
            # Local: just clear the local stop flag
            pass
        logger.info("[NexoraRedis] Stop acknowledged for session %s by worker %s", sid, self._wid)

    def is_stop_acked(self, sid: str) -> bool:
        """Returns True if the stop for this session has already been acknowledged."""
        if not self._ok or not self._r:
            return False
        val = self._redis_call(self._r.get, f"{_STOP_ACK_PFX}{sid}")
        return val is not None

    def clear_stop_signal(self, sid: str) -> None:
        """Full stop-state reset: flag + ack. Called on session cleanup."""
        if self._ok and self._r:
            self._redis_call(self._r.delete, f"{_STOP_PFX}{sid}")
            self._redis_call(self._r.delete, f"{_STOP_ACK_PFX}{sid}")

    # ── Orphan Markers (Z10) ─────────────────────────────────────────────────────

    def mark_orphan(self, sid: str, reason: str = "worker_death") -> None:
        """
        Mark a session as orphaned (worker died mid-execution).
        Persists until consumed by recovery or TTL expiry.
        """
        if self._ok and self._r:
            self._redis_call(
                self._r.set,
                f"{_ORPHAN_PFX}{sid}",
                json.dumps({"reason": reason, "ts": round(time.time(), 3), "worker": self._wid}),
                ex=_ORPHAN_TTL,
            )
        logger.warning("[NexoraRedis] Session %s marked orphan (%s)", sid, reason)

    def list_orphans(self) -> list:
        """Return all currently marked orphan sessions."""
        if not self._ok or not self._r:
            return []
        keys = self._redis_call(self._r.keys, f"{_ORPHAN_PFX}*") or []
        result = []
        for k in keys:
            sid = k[len(_ORPHAN_PFX):]
            raw = self._redis_call(self._r.get, k)
            try:
                meta = json.loads(raw) if raw else {}
            except Exception:
                meta = {}
            result.append({"session_id": sid, **meta})
        return result

    def clear_orphan(self, sid: str) -> None:
        """Remove the orphan marker once recovery is complete."""
        if self._ok and self._r:
            self._redis_call(self._r.delete, f"{_ORPHAN_PFX}{sid}")

    # ── HITL State ──────────────────────────────────────────────────────────────

    def hitl_get(self, sid: str) -> dict:
        """Return HITL state for sid. Always returns a dict with paused + inject_queue."""
        if self._ok and self._r:
            key = f"{_HITL_PFX}{sid}"
            raw = self._redis_call(self._r.hgetall, key) or {}
            paused = raw.get("paused", "0") == "1"
            try:
                inject_queue = json.loads(raw.get("inject_queue", "[]"))
            except Exception:
                inject_queue = []
            return {"paused": paused, "inject_queue": inject_queue}
        with self._local_hitl_lk:
            return self._local_hitl.setdefault(sid, {"paused": False, "inject_queue": []})

    def hitl_set_paused(self, sid: str, paused: bool) -> None:
        if self._ok and self._r:
            key = f"{_HITL_PFX}{sid}"
            self._redis_call(self._r.hset, key, "paused", "1" if paused else "0")
            self._redis_call(self._r.expire, key, _HITL_TTL)
            return
        state = self.hitl_get(sid)
        state["paused"] = paused

    def hitl_inject(self, sid: str, message: str) -> None:
        if self._ok and self._r:
            key = f"{_HITL_PFX}{sid}"
            raw = self._redis_call(self._r.hget, key, "inject_queue") or "[]"
            try:
                queue = json.loads(raw)
            except Exception:
                queue = []
            queue.append(message)
            pipe = self._r.pipeline(transaction=True)
            pipe.hset(key, "inject_queue", json.dumps(queue))
            pipe.hset(key, "paused", "0")   # auto-resume on inject
            pipe.expire(key, _HITL_TTL)
            try:
                pipe.execute()
            except Exception as e:
                logger.warning("[NexoraRedis] hitl_inject pipeline: %s", e)
            return
        state = self.hitl_get(sid)
        state["inject_queue"].append(message)
        state["paused"] = False

    def hitl_pop_inject(self, sid: str) -> str | None:
        """Atomically pop one message from the inject queue."""
        if self._ok and self._r:
            key = f"{_HITL_PFX}{sid}"
            # Lua script for atomic pop
            _LUA_POP = """
local key = KEYS[1]
local raw = redis.call('HGET', key, 'inject_queue')
if not raw then return nil end
local ok, q = pcall(cjson.decode, raw)
if not ok or #q == 0 then return nil end
local msg = table.remove(q, 1)
redis.call('HSET', key, 'inject_queue', cjson.encode(q))
return msg
"""
            try:
                result = self._r.eval(_LUA_POP, 1, key)
                return result
            except Exception:
                # Fallback non-atomic
                raw = self._redis_call(self._r.hget, key, "inject_queue") or "[]"
                try:
                    queue = json.loads(raw)
                except Exception:
                    queue = []
                if queue:
                    msg = queue.pop(0)
                    self._redis_call(self._r.hset, key, "inject_queue", json.dumps(queue))
                    return msg
                return None
        state = self.hitl_get(sid)
        q = state.get("inject_queue", [])
        if q:
            return q.pop(0)
        return None

    def hitl_clear(self, sid: str) -> None:
        if self._ok and self._r:
            self._redis_call(self._r.delete, f"{_HITL_PFX}{sid}")
        with self._local_hitl_lk:
            self._local_hitl.pop(sid, None)

    # ── Worker Heartbeat ────────────────────────────────────────────────────────

    def heartbeat(self) -> None:
        """Record that this worker is alive. Called periodically by the heartbeat thread."""
        if not self._ok or not self._r:
            return
        key = f"{_WORKER_PFX}{self._wid}"
        sid = self._local_running.get("sid") or ""
        try:
            pipe = self._r.pipeline(transaction=False)
            pipe.hset(key, mapping={
                "pid":        str(os.getpid()),
                "sid":        sid,
                "ts":         str(round(time.time(), 3)),
                "worker_id":  self._wid,
            })
            pipe.expire(key, _WORKER_TTL)
            pipe.execute()
        except Exception as e:
            logger.debug("[NexoraRedis] heartbeat error: %s", e)

    def list_workers(self) -> list:
        """Return all live workers (for /api/workers or health checks)."""
        if self._ok and self._r:
            keys = self._redis_call(self._r.keys, f"{_WORKER_PFX}*") or []
            workers = []
            for k in keys:
                data = self._redis_call(self._r.hgetall, k) or {}
                if data:
                    workers.append(data)
            return workers
        # Local mode: just return self
        return [{
            "worker_id": self._wid,
            "pid":       str(os.getpid()),
            "sid":       self._local_running.get("sid") or "",
            "ts":        str(round(time.time(), 3)),
        }]

    def _start_heartbeat(self) -> None:
        def _loop():
            while True:
                time.sleep(20)
                self.heartbeat()
        self._heartbeat_thread = threading.Thread(
            target=_loop, name="nx-redis-heartbeat", daemon=True
        )
        self._heartbeat_thread.start()
        logger.info("[NexoraRedis] Heartbeat thread started (interval=20s)")

    # ── Health ──────────────────────────────────────────────────────────────────

    def health(self) -> dict:
        if not self._ok:
            return {"mode": "local", "redis": "disabled", "worker_id": self._wid}
        try:
            t0 = time.monotonic()
            self._r.ping()
            lat = round((time.monotonic() - t0) * 1000, 1)
            return {
                "mode":        "redis",
                "redis":       "ok",
                "latency_ms":  lat,
                "worker_id":   self._wid,
                "queue_depth": self.queue_depth(),
            }
        except Exception as e:
            return {"mode": "degraded", "redis": "error", "error": str(e), "worker_id": self._wid}

    @property
    def available(self) -> bool:
        return self._ok

    @property
    def worker_id(self) -> str:
        return self._wid


# ── Module-level singleton ───────────────────────────────────────────────────────
_instance: "NexoraRedisLayer | None" = None
_init_lock = threading.Lock()


def get_nx_redis() -> NexoraRedisLayer:
    """Return the module-level singleton, creating it on first call."""
    global _instance
    if _instance is None:
        with _init_lock:
            if _instance is None:
                _instance = NexoraRedisLayer()
    return _instance
