"""
redis_layer.py — Phase Z7: Redis multi-worker coordination layer

Provides a AetherionRedis wrapper that exposes all multi-worker coordination
primitives. Falls back to an in-process stub when Redis is unavailable so
the application degrades gracefully rather than crashing.
"""

import os
import time
import logging
import threading
from collections import deque

logger = logging.getLogger(__name__)

_QUEUE_KEY    = "nx:task_queue"
_RUNNING_KEY  = "nx:running"
_PENDING_KEY  = "nx:pending_set"
_STOP_PREFIX  = "nx:stop:"


class _LocalFallback:
    """
    In-process fallback used when Redis is unreachable.
    Mimics the AetherionRedis API with a simple deque + threading.
    Single-process only — multi-worker coordination disabled.
    """

    def __init__(self):
        self.available  = False
        self.worker_id  = f"local-{os.getpid()}"
        self._queue     = deque()
        self._running   = None
        self._stop_sids: set = set()
        self._hitl_state: dict = {}
        self._kv: dict = {}
        self._lock      = threading.Lock()

    # ── Queue ops ──────────────────────────────────────────────────────

    def push(self, sid: str):
        with self._lock:
            self._queue.append(sid)

    def pop_blocking(self, timeout: float = 1.0) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._queue:
                    return self._queue.popleft()
            time.sleep(0.05)
        return None

    def queue_length(self) -> int:
        with self._lock:
            return len(self._queue)

    def list_pending(self) -> list:
        with self._lock:
            return list(self._queue)

    # ── Running-session slot ───────────────────────────────────────────

    def set_running(self, sid: str, seq: int = 0):
        with self._lock:
            self._running = sid

    def get_any_running_sid(self) -> str | None:
        with self._lock:
            return self._running

    def release_running(self):
        with self._lock:
            self._running = None

    # ── Stop signals ──────────────────────────────────────────────────

    def request_stop(self, sid: str, ttl: int = 120):
        with self._lock:
            self._stop_sids.add(sid)

    def should_stop(self, sid: str) -> bool:
        with self._lock:
            return sid in self._stop_sids

    def clear_stop(self, sid: str):
        with self._lock:
            self._stop_sids.discard(sid)

    # ── HITL ops ──────────────────────────────────────────────────────

    def hitl_get(self, sid: str) -> dict:
        with self._lock:
            return dict(self._hitl_state.get(sid, {}))

    def hitl_set_paused(self, sid: str, paused: bool):
        with self._lock:
            state = self._hitl_state.setdefault(sid, {})
            state["paused"] = paused

    def hitl_inject(self, sid: str, message: dict):
        with self._lock:
            state = self._hitl_state.setdefault(sid, {})
            state.setdefault("injected", []).append(message)

    # ── Passthrough: raw key access ───────────────────────────────────

    def get(self, key: str):
        with self._lock:
            if key in self._kv:
                val, expires = self._kv[key]
                if expires is None or time.time() < expires:
                    return val
                del self._kv[key]
            return None

    def set(self, key: str, value, ex: int | None = None):
        with self._lock:
            expires = time.time() + ex if ex else None
            self._kv[key] = (value, expires)

    def exists(self, key: str) -> bool:
        with self._lock:
            if key in self._kv:
                val, expires = self._kv[key]
                if expires is None or time.time() < expires:
                    return True
                del self._kv[key]
            return False

    def delete(self, *keys):
        with self._lock:
            for k in keys:
                self._kv.pop(k, None)

    def keys(self, pattern: str = "*") -> list:
        import fnmatch
        with self._lock:
            now = time.time()
            valid_keys = []
            for k, (v, exp) in list(self._kv.items()):
                if exp is not None and now >= exp:
                    del self._kv[k]
                    continue
                k_str = k.decode() if isinstance(k, bytes) else str(k)
                pat_str = pattern.decode() if isinstance(pattern, bytes) else str(pattern)
                if fnmatch.fnmatch(k_str, pat_str):
                    valid_keys.append(k.encode() if isinstance(pattern, bytes) else k_str)
            return valid_keys

    def pipeline(self):
        return _FakePipeline()

    def ping(self):
        return True

    def health(self) -> dict:
        return {
            "ok": True,
            "available": False,
            "mode": "local_fallback",
            "worker_id": self.worker_id,
            "queue_length": self.queue_length(),
            "pending": len(self.list_pending()),
            "running": self.get_any_running_sid(),
        }


class _FakePipeline:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def zremrangebyscore(self, *a, **kw): return self
    def zcard(self, *a): return self
    def zadd(self, *a, **kw): return self
    def expire(self, *a): return self
    def execute(self): return [0, 0, 0, 0]


class AetherionRedis:
    """
    Production Redis wrapper with full multi-worker coordination API.
    All methods are safe to call whether or not Redis is available.
    """

    def __init__(self, client):
        self._r        = client
        self.available = True
        self.worker_id = f"redis-{os.getpid()}"
        self._running_sid = None

    # ── Queue ops ──────────────────────────────────────────────────────

    def push(self, sid: str):
        try:
            self._r.rpush(_QUEUE_KEY, sid)
            self._r.sadd(_PENDING_KEY, sid)
        except Exception as e:
            logger.warning("[redis_layer] push failed: %s", e)

    def pop_blocking(self, timeout: float = 1.0) -> str | None:
        try:
            result = self._r.blpop(_QUEUE_KEY, timeout=int(max(1, timeout)))
            if result:
                sid = result[1]
                if isinstance(sid, bytes):
                    sid = sid.decode()
                self._r.srem(_PENDING_KEY, sid)
                return sid
        except Exception as e:
            logger.warning("[redis_layer] pop_blocking failed: %s", e)
        return None

    def queue_length(self) -> int:
        try:
            return self._r.llen(_QUEUE_KEY)
        except Exception:
            return 0

    def list_pending(self) -> list:
        try:
            items = self._r.smembers(_PENDING_KEY)
            return [s.decode() if isinstance(s, bytes) else s for s in items]
        except Exception:
            return []

    # ── Running-session slot ───────────────────────────────────────────

    def set_running(self, sid: str, seq: int = 0, ttl: int = 3600):
        try:
            self._running_sid = sid
            self._r.set(_RUNNING_KEY, sid, ex=ttl)
            worker_ttl = int(os.getenv("NX_WORKER_TTL", "60"))
            self._r.set(f"nx:worker:{self.worker_id}", str(time.time()), ex=worker_ttl)
            self._r.set(f"nx:running:{self.worker_id}", sid, ex=ttl)
            self._r.set(f"nx:owner:{sid}", self.worker_id, ex=ttl)
        except Exception as e:
            logger.warning("[redis_layer] set_running failed: %s", e)

    def get_any_running_sid(self) -> str | None:
        try:
            val = self._r.get(_RUNNING_KEY)
            if val:
                return val.decode() if isinstance(val, bytes) else val
        except Exception:
            pass
        return None

    def release_running(self):
        try:
            sid = self._running_sid
            global_sid = self.get_any_running_sid()
            pipe = self._r.pipeline(transaction=False)
            if global_sid == sid:
                pipe.delete(_RUNNING_KEY)
            pipe.delete(f"nx:running:{self.worker_id}")
            pipe.delete(f"nx:worker:{self.worker_id}")
            if sid:
                pipe.delete(f"nx:owner:{sid}")
            pipe.execute()
            self._running_sid = None
        except Exception as e:
            logger.warning("[redis_layer] release_running failed: %s", e)

    # ── Stop signals ──────────────────────────────────────────────────

    def request_stop(self, sid: str, ttl: int = 120):
        try:
            self._r.set(f"{_STOP_PREFIX}{sid}", "1", ex=ttl)
        except Exception as e:
            logger.warning("[redis_layer] request_stop failed: %s", e)

    def should_stop(self, sid: str) -> bool:
        try:
            return bool(self._r.exists(f"{_STOP_PREFIX}{sid}"))
        except Exception:
            return False

    def clear_stop(self, sid: str):
        try:
            self._r.delete(f"{_STOP_PREFIX}{sid}")
        except Exception:
            pass

    def clear_stop_signal(self, sid: str):
        """Compatibility alias used by worker reconciliation."""
        self.clear_stop(sid)

    def mark_orphan(self, sid: str, reason: str = "worker_death", ttl: int = 3600):
        try:
            import json
            self._r.set(
                f"nx:orphan:{sid}",
                json.dumps({"session_id": sid, "reason": reason, "ts": time.time()}),
                ex=ttl,
            )
        except Exception as e:
            logger.warning("[redis_layer] mark_orphan failed: %s", e)

    # ── HITL ops ──────────────────────────────────────────────────────

    def hitl_get(self, sid: str) -> dict:
        try:
            import json
            raw = self._r.get(f"nx:hitl:{sid}")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return {}

    def hitl_set_paused(self, sid: str, paused: bool):
        try:
            import json
            state = self.hitl_get(sid)
            state["paused"] = paused
            self._r.set(f"nx:hitl:{sid}", json.dumps(state))
        except Exception:
            pass

    def hitl_inject(self, sid: str, message: dict):
        try:
            import json
            state = self.hitl_get(sid)
            injected = state.get("injected", [])
            injected.append(message)
            state["injected"] = injected
            self._r.set(f"nx:hitl:{sid}", json.dumps(state))
        except Exception:
            pass

    # ── Raw passthrough for callers that use Redis directly ───────────

    def get(self, key: str):
        if not getattr(self, "available", True):
            return None
        try:
            return self._r.get(key)
        except Exception:
            self.available = False
            return None

    def set(self, key: str, value, ex: int | None = None):
        if not getattr(self, "available", True):
            return
        try:
            self._r.set(key, value, ex=ex)
        except Exception:
            self.available = False
            pass

    def exists(self, key: str) -> bool:
        if not getattr(self, "available", True):
            return False
        try:
            return bool(self._r.exists(key))
        except Exception:
            self.available = False
            return False

    def delete(self, *keys):
        if not getattr(self, "available", True):
            return
        try:
            self._r.delete(*keys)
        except Exception:
            self.available = False
            pass

    def keys(self, pattern: str = "*") -> list:
        if not getattr(self, "available", True):
            return []
        try:
            return self._r.keys(pattern)
        except Exception as e:
            logger.warning("[redis_layer] keys() scan failed: %s", e)
            return []

    def pipeline(self):
        if not getattr(self, "available", True):
            return _FakePipeline()
        try:
            return self._r.pipeline()
        except Exception:
            self.available = False
            return _FakePipeline()

    def ping(self) -> bool:
        if not getattr(self, "available", True):
            return False
        try:
            return self._r.ping()
        except Exception:
            self.available = False
            return False

    def health(self) -> dict:
        ping_ok = self.ping()
        return {
            "ok": ping_ok,
            "available": bool(getattr(self, "available", False) and ping_ok),
            "mode": "redis" if ping_ok else "degraded",
            "worker_id": self.worker_id,
            "queue_length": self.queue_length(),
            "pending": len(self.list_pending()),
            "running": self.get_any_running_sid(),
        }


# ── Singleton factory ──────────────────────────────────────────────────────────

_instance = None
_init_lock = threading.Lock()


def get_nx_redis() -> AetherionRedis | _LocalFallback:
    """
    Returns the singleton AetherionRedis wrapper (or _LocalFallback if Redis
    is unreachable). Thread-safe. Safe to call multiple times.
    """
    global _instance

    if _instance is not None:
        return _instance

    with _init_lock:
        if _instance is not None:
            return _instance

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        try:
            import redis as _redis_lib
            client = _redis_lib.from_url(
                redis_url,
                socket_connect_timeout=2,
                socket_timeout=2,
                decode_responses=False,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            client.ping()
            _instance = AetherionRedis(client)
            logger.info("[redis_layer] Connected to Redis at %s", redis_url.split("@")[-1])
        except ImportError:
            logger.warning("[redis_layer] redis-py not installed — using in-process fallback")
            _instance = _LocalFallback()
        except Exception as e:
            logger.warning("[redis_layer] Redis unavailable (%s) — using in-process fallback", e)
            _instance = _LocalFallback()

    return _instance


def reset_connection():
    """Force re-initialization on next call (useful after Redis restart)."""
    global _instance
    _instance = None
