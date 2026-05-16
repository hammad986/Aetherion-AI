"""
streaming/sse_redis.py — Redis Pub/Sub SSE Bridge
═══════════════════════════════════════════════════
Drop-in companion to SSEManager that routes SSE events through Redis
pub/sub, enabling correct multi-worker Gunicorn operation.

Architecture:
  - Each Gunicorn worker subscribes to a Redis channel per session
  - Events broadcast via `broadcast_to_session()` are published to Redis
  - A per-worker subscriber thread fans events into local SSEManager queues
  - Falls back transparently to in-process SSEManager when Redis unavailable

Usage (web_app.py module level):
    from streaming.sse_redis import RedisSSEBridge
    RedisSSEBridge.init()           # call once at module import
    # Then use RedisSSEBridge.broadcast_to_session() everywhere instead of
    # SSEManager.broadcast_to_session() — API-compatible drop-in.

Env vars:
    REDIS_URL          — Redis connection URI (e.g. redis://localhost:6379/0)
    REDIS_SSE_TTL      — Channel TTL in seconds (default 3600)
    REDIS_SSE_DISABLED — Set to "1" to force in-process fallback
"""

import json
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("nexora.sse_redis")

_REDIS_URL     = os.getenv("REDIS_URL", "")
_DISABLED      = os.getenv("REDIS_SSE_DISABLED", "0").strip() == "1"
_CHANNEL_PREFIX = "nx:sse:"
_REPLAY_KEY_PREFIX = "nx:replay:"
_REPLAY_TTL    = int(os.getenv("REDIS_SSE_TTL", "3600"))  # 1 hour replay window
_MAX_REPLAY    = 200                                        # events kept per channel


class RedisSSEBridge:
    """
    Multi-worker SSE event router via Redis pub/sub.

    Thread-safety: All Redis operations are serialised through the redis-py
    connection pool. Local fan-out into SSEManager queues uses SSEManager's
    own lock.
    """

    _redis         = None        # redis.Redis connection (publisher)
    _sub_thread:   Optional[threading.Thread] = None
    _sub_stop      = threading.Event()
    _subscriptions: set = set()  # session IDs this worker is subscribed to
    _sub_lock      = threading.Lock()
    _available     = False       # True only when Redis is reachable
    _sse_manager   = None        # reference to SSEManager (injected on init)

    # ── Init ──────────────────────────────────────────────────────────────────

    @classmethod
    def init(cls, sse_manager_cls=None):
        """
        Initialise the bridge. Must be called once at module level.

        Parameters
        ----------
        sse_manager_cls : SSEManager class
            Injected to avoid circular imports. Defaults to auto-import.
        """
        if _DISABLED or not _REDIS_URL:
            logger.info(
                "[RedisSSE] Redis SSE bridge disabled "
                "(REDIS_URL not set or REDIS_SSE_DISABLED=1). "
                "Using in-process SSEManager."
            )
            return

        if sse_manager_cls is None:
            try:
                from streaming.sse_manager import SSEManager
                sse_manager_cls = SSEManager
            except ImportError:
                logger.error("[RedisSSE] Cannot import SSEManager — bridge disabled.")
                return

        cls._sse_manager = sse_manager_cls

        try:
            import redis as _redis_mod
            cls._redis = _redis_mod.from_url(
                _REDIS_URL,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=3,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            cls._redis.ping()
            cls._available = True
            logger.info("[RedisSSE] Connected to Redis at %s", _REDIS_URL)
            cls._start_subscriber()
            # ── Inject bridge into SSEManager to break circular import ──────────
            # SSEManager.broadcast_to_session() will call this function directly
            # without any deferred import overhead on each event.
            sse_manager_cls.set_bridge(cls.broadcast_to_session)
        except Exception as e:
            cls._available = False
            logger.warning(
                "[RedisSSE] Redis unavailable (%s). "
                "Falling back to in-process SSEManager.", e
            )
            # No bridge injection needed — SSEManager defaults to local delivery.


    # ── Publisher ─────────────────────────────────────────────────────────────

    @classmethod
    def broadcast_to_session(cls, session_id: str, event_type: str, payload: dict) -> None:
        """
        Broadcast an event to all workers subscribed to this session.

        Falls back to in-process SSEManager.broadcast_to_session() if Redis
        is unavailable or this call originates from an unsubscribed worker.
        """
        if cls._available and cls._redis:
            try:
                msg = json.dumps({
                    "session_id": session_id,
                    "event_type": event_type,
                    "payload":    payload,
                    "ts":         time.time(),
                })
                channel = f"{_CHANNEL_PREFIX}{session_id}"
                cls._redis.publish(channel, msg)
                # Also append to per-session replay list for reconnecting clients
                replay_key = f"{_REPLAY_KEY_PREFIX}{session_id}"
                pipe = cls._redis.pipeline(transaction=False)
                pipe.rpush(replay_key, msg)
                pipe.ltrim(replay_key, -_MAX_REPLAY, -1)
                pipe.expire(replay_key, _REPLAY_TTL)
                pipe.execute()
                return
            except Exception as e:
                logger.warning("[RedisSSE] Publish failed (%s); falling back in-process.", e)

        # Fallback: direct in-process delivery
        if cls._sse_manager:
            cls._sse_manager._local_broadcast_to_session(session_id, event_type, payload)

    @classmethod
    def replay_since(cls, session_id: str, since_ts: float = 0.0) -> list:
        """
        Retrieve missed events from Redis replay buffer for reconnecting clients.
        Returns list of (event_type, payload) tuples.
        """
        if not cls._available or not cls._redis:
            return []
        try:
            replay_key = f"{_REPLAY_KEY_PREFIX}{session_id}"
            raw_events = cls._redis.lrange(replay_key, 0, -1)
            result = []
            for raw in raw_events:
                try:
                    ev = json.loads(raw)
                    if ev.get("ts", 0) > since_ts:
                        result.append((ev["event_type"], ev["payload"]))
                except Exception:
                    continue
            return result
        except Exception as e:
            logger.warning("[RedisSSE] replay_since failed: %s", e)
            return []

    # ── Subscriber thread ─────────────────────────────────────────────────────

    @classmethod
    def _start_subscriber(cls):
        cls._sub_stop.clear()
        cls._sub_thread = threading.Thread(
            target=cls._subscriber_loop,
            name="redis-sse-sub",
            daemon=True,
        )
        cls._sub_thread.start()
        logger.info("[RedisSSE] Subscriber thread started.")

    @classmethod
    def _subscriber_loop(cls):
        """
        Persistent subscriber loop. Reconnects automatically on Redis errors.
        Routes incoming pub/sub messages into the local SSEManager queues.
        """
        import redis as _redis_mod
        backoff = 1.0
        while not cls._sub_stop.is_set():
            try:
                sub_client = _redis_mod.from_url(
                    _REDIS_URL,
                    decode_responses=True,
                    socket_timeout=5,
                )
                pubsub = sub_client.pubsub(ignore_subscribe_messages=True)
                # Subscribe to all session channels via pattern
                pubsub.psubscribe(f"{_CHANNEL_PREFIX}*")
                backoff = 1.0
                logger.info("[RedisSSE] Subscribed to pattern %s*", _CHANNEL_PREFIX)

                for message in pubsub.listen():
                    if cls._sub_stop.is_set():
                        break
                    if message and message.get("type") == "pmessage":
                        cls._route_message(message.get("data", ""))

            except Exception as e:
                if cls._sub_stop.is_set():
                    break
                logger.warning(
                    "[RedisSSE] Subscriber error (%s). Reconnecting in %ss.", e, backoff
                )
                time.sleep(min(backoff, 30))
                backoff = min(backoff * 2, 30)

    @classmethod
    def _route_message(cls, raw: str) -> None:
        """Fan a received Redis message into local SSEManager queues."""
        if not cls._sse_manager:
            return
        try:
            ev = json.loads(raw)
            cls._sse_manager._local_broadcast_to_session(
                ev["session_id"],
                ev["event_type"],
                ev["payload"],
            )
        except Exception as e:
            logger.debug("[RedisSSE] Route message error: %s", e)

    # ── Subscription management ───────────────────────────────────────────────

    @classmethod
    def subscribe_session(cls, session_id: str) -> None:
        """Mark this worker as interested in events for session_id."""
        with cls._sub_lock:
            cls._subscriptions.add(session_id)

    @classmethod
    def unsubscribe_session(cls, session_id: str) -> None:
        """Remove interest in session_id (e.g. on session cleanup)."""
        with cls._sub_lock:
            cls._subscriptions.discard(session_id)

    @classmethod
    def stop(cls) -> None:
        """Graceful shutdown (called from Gunicorn worker_exit hook)."""
        cls._sub_stop.set()
        if cls._redis:
            try:
                cls._redis.close()
            except Exception:
                pass
        cls._available = False
        logger.info("[RedisSSE] Bridge stopped.")

    # ── Health ────────────────────────────────────────────────────────────────

    @classmethod
    def health(cls) -> dict:
        """Return bridge health for /api/health endpoint."""
        if not cls._available:
            return {"redis_sse": "disabled", "mode": "in-process"}
        try:
            latency_ms = None
            start = time.monotonic()
            cls._redis.ping()
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            return {
                "redis_sse": "ok",
                "mode": "redis_pubsub",
                "latency_ms": latency_ms,
                "subscriptions": len(cls._subscriptions),
            }
        except Exception as e:
            return {"redis_sse": "error", "error": str(e), "mode": "in-process"}
