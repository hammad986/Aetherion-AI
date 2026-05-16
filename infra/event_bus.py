"""
infra/event_bus.py — Phase: Redis Coordination & Event Bus
===========================================================
Distributed event backbone for Aetherion's multi-agent runtime.

Architecture:
  • Redis Pub/Sub for coordination events (fan-out to all connected nodes)
  • Redis Streams for replay-safe ordered event history
  • In-process NxBus fallback when Redis is unavailable (single-node mode)
  • Automatic reconnect with exponential backoff
  • Deduplication: events carry idempotency keys to prevent storm amplification
  • Backpressure: stream MAXLEN trimming prevents unbounded growth

Event channels:
  nexora:events:{session_id}     — per-session SSE fan-out
  nexora:coordination             — global coordination state
  nexora:trust                    — trust signals
  nexora:governance               — governance escalations
  nexora:heartbeat                — worker liveness

Usage:
    from infra.event_bus import get_event_bus
    bus = get_event_bus()
    bus.publish("nexora:events:sess_123", "agent.dag_update", payload)
    bus.subscribe("nexora:coordination", callback)
"""

import json
import logging
import os
import threading
import time
import uuid
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("nexora.event_bus")

_REDIS_URL         = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_STREAM_MAXLEN     = int(os.getenv("EVENT_STREAM_MAXLEN", "10000"))   # per-channel cap
_DEDUP_WINDOW_SEC  = 2.0   # idempotency window for storm prevention
_RECONNECT_BASE    = 1.0
_RECONNECT_MAX     = 30.0


# ─────────────────────────────────────────────────────────────────────────────
# Redis connection management
# ─────────────────────────────────────────────────────────────────────────────

class _RedisClient:
    """Thread-safe Redis client wrapper with automatic reconnect."""

    def __init__(self):
        self._rc = None
        self._pubsub = None
        self._lock = threading.RLock()
        self._connected = False
        self._backoff = _RECONNECT_BASE
        self._connect()

    def _connect(self) -> bool:
        try:
            import redis as _redis_mod
            rc = _redis_mod.from_url(
                _REDIS_URL,
                socket_connect_timeout=3,
                socket_timeout=5,
                decode_responses=True,
                retry_on_timeout=True,
            )
            rc.ping()
            with self._lock:
                self._rc = rc
                self._connected = True
                self._backoff = _RECONNECT_BASE
            logger.info("[EventBus] Redis connected.")
            return True
        except Exception as e:
            logger.warning(f"[EventBus] Redis unavailable: {e}. Using in-process fallback.")
            with self._lock:
                self._connected = False
            return False

    def is_connected(self) -> bool:
        return self._connected

    def get(self):
        """Returns raw redis client or None."""
        with self._lock:
            return self._rc if self._connected else None

    def reconnect_if_needed(self):
        if not self._connected:
            if self._connect():
                self._backoff = _RECONNECT_BASE
            else:
                self._backoff = min(self._backoff * 2, _RECONNECT_MAX)


# ─────────────────────────────────────────────────────────────────────────────
# In-process fallback bus (single-node, no Redis)
# ─────────────────────────────────────────────────────────────────────────────

class _InProcessBus:
    """Thread-safe in-process pub/sub — used when Redis is not available."""

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._lock = threading.RLock()
        self._dedup: Dict[str, float] = {}   # idempotency_key → timestamp

    def publish(self, channel: str, event_type: str, payload: dict,
                idempotency_key: str = "") -> None:
        # Dedup check
        if idempotency_key:
            now = time.time()
            with self._lock:
                last = self._dedup.get(idempotency_key, 0)
                if now - last < _DEDUP_WINDOW_SEC:
                    return
                self._dedup[idempotency_key] = now
                # Prune stale dedup entries
                if len(self._dedup) > 2000:
                    cutoff = now - _DEDUP_WINDOW_SEC * 10
                    self._dedup = {k: v for k, v in self._dedup.items() if v > cutoff}

        with self._lock:
            callbacks = list(self._subscribers.get(channel, []))

        for cb in callbacks:
            try:
                cb(channel, event_type, payload)
            except Exception as e:
                logger.debug(f"[EventBus] In-process subscriber error: {e}")

    def subscribe(self, channel: str, callback: Callable) -> None:
        with self._lock:
            if channel not in self._subscribers:
                self._subscribers[channel] = []
            if callback not in self._subscribers[channel]:
                self._subscribers[channel].append(callback)

    def unsubscribe(self, channel: str, callback: Callable) -> None:
        with self._lock:
            subs = self._subscribers.get(channel, [])
            if callback in subs:
                subs.remove(callback)

    def subscriber_count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._subscribers.values())


# ─────────────────────────────────────────────────────────────────────────────
# EventBus — unified interface
# ─────────────────────────────────────────────────────────────────────────────

class EventBus:
    """
    Unified event bus. Automatically uses Redis when available,
    falls back to in-process pub/sub for single-node deployments.

    All events are:
      - Typed (event_type string)
      - Idempotency-keyed (prevents storm amplification)
      - Replay-safe (stored in Redis Streams when Redis is available)
    """

    def __init__(self):
        self._redis = _RedisClient()
        self._fallback = _InProcessBus()
        self._sub_thread: Optional[threading.Thread] = None
        self._redis_subscriptions: Dict[str, List[Callable]] = {}
        self._sub_lock = threading.RLock()

    # ── Publishing ────────────────────────────────────────────────────────────

    def publish(
        self,
        channel: str,
        event_type: str,
        payload: dict,
        idempotency_key: str = "",
        session_id: str = "",
    ) -> str:
        """
        Publishes an event to `channel`.
        Returns event_id for tracing.
        """
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        envelope = {
            "event_id": event_id,
            "event_type": event_type,
            "channel": channel,
            "session_id": session_id,
            "ts": time.time(),
            "payload": payload,
        }
        ikey = idempotency_key or f"{channel}:{event_type}:{hash(json.dumps(payload, sort_keys=True, default=str))}"

        rc = self._redis.get()
        if rc:
            try:
                # Pub/Sub for live fan-out
                rc.publish(channel, json.dumps(envelope))
                # Streams for replay (MAXLEN cap prevents unbounded growth)
                stream_key = f"nexora:stream:{channel}"
                rc.xadd(stream_key, {"data": json.dumps(envelope)}, maxlen=_STREAM_MAXLEN, approximate=True)
                return event_id
            except Exception as e:
                logger.warning(f"[EventBus] Redis publish failed: {e} — using fallback")
                self._redis._connected = False

        # Fallback
        self._fallback.publish(channel, event_type, payload, idempotency_key=ikey)
        return event_id

    def publish_to_session(self, session_id: str, event_type: str, payload: dict) -> str:
        """Convenience: publishes to the per-session coordination channel."""
        channel = f"nexora:session:{session_id}"
        return self.publish(channel, event_type, payload, session_id=session_id)

    def publish_global(self, event_type: str, payload: dict) -> str:
        """Publishes to the global coordination channel (all nodes receive it)."""
        return self.publish("nexora:coordination", event_type, payload)

    # ── Subscribing ───────────────────────────────────────────────────────────

    def subscribe(self, channel: str, callback: Callable) -> None:
        """
        Subscribes `callback(channel, event_type, payload)` to a channel.
        Uses Redis Pub/Sub when available; in-process fallback otherwise.
        """
        self._fallback.subscribe(channel, callback)   # Always register in fallback
        rc = self._redis.get()
        if rc:
            with self._sub_lock:
                if channel not in self._redis_subscriptions:
                    self._redis_subscriptions[channel] = []
                self._redis_subscriptions[channel].append(callback)
            self._ensure_redis_listener()

    def unsubscribe(self, channel: str, callback: Callable) -> None:
        self._fallback.unsubscribe(channel, callback)
        with self._sub_lock:
            subs = self._redis_subscriptions.get(channel, [])
            if callback in subs:
                subs.remove(callback)

    # ── Replay from stream ────────────────────────────────────────────────────

    def replay(self, channel: str, since_ts: float = 0, max_events: int = 200) -> List[dict]:
        """
        Returns ordered events from the Redis Stream for `channel`
        since `since_ts` (Unix timestamp). Returns [] if Redis unavailable.
        """
        rc = self._redis.get()
        if not rc:
            return []
        try:
            stream_key = f"nexora:stream:{channel}"
            # Convert timestamp to Redis stream ID
            min_id = f"{int(since_ts * 1000)}-0" if since_ts else "-"
            entries = rc.xrange(stream_key, min=min_id, count=max_events)
            result = []
            for entry_id, fields in entries:
                try:
                    result.append(json.loads(fields.get("data", "{}")))
                except Exception:
                    pass
            return result
        except Exception as e:
            logger.debug(f"[EventBus] Replay failed: {e}")
            return []

    # ── Health ────────────────────────────────────────────────────────────────

    def health(self) -> dict:
        rc = self._redis.get()
        return {
            "backend": "redis" if rc else "in_process",
            "redis_connected": self._redis.is_connected(),
            "in_process_subscribers": self._fallback.subscriber_count(),
            "redis_subscriptions": len(self._redis_subscriptions),
        }

    # ── Redis listener thread ─────────────────────────────────────────────────

    def _ensure_redis_listener(self):
        with self._sub_lock:
            if self._sub_thread and self._sub_thread.is_alive():
                return
            t = threading.Thread(target=self._redis_listener_loop, daemon=True, name="redis-sub")
            self._sub_thread = t
            t.start()

    def _redis_listener_loop(self):
        """Persistent Redis Pub/Sub listener with reconnect logic."""
        while True:
            rc = self._redis.get()
            if not rc:
                self._redis.reconnect_if_needed()
                time.sleep(self._redis._backoff)
                continue
            try:
                ps = rc.pubsub(ignore_subscribe_messages=True)
                with self._sub_lock:
                    channels = list(self._redis_subscriptions.keys())
                if channels:
                    ps.subscribe(*channels)
                for message in ps.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        envelope = json.loads(message["data"])
                        ch = envelope.get("channel", message["channel"])
                        et = envelope.get("event_type", "unknown")
                        pl = envelope.get("payload", {})
                        with self._sub_lock:
                            callbacks = list(self._redis_subscriptions.get(ch, []))
                        for cb in callbacks:
                            try:
                                cb(ch, et, pl)
                            except Exception as e:
                                logger.debug(f"[EventBus] Redis subscriber callback error: {e}")
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"[EventBus] Redis listener disconnected: {e}. Reconnecting...")
                self._redis._connected = False
                self._redis.reconnect_if_needed()
                time.sleep(self._redis._backoff)


# ─────────────────────────────────────────────────────────────────────────────
# Global singleton
# ─────────────────────────────────────────────────────────────────────────────

_bus_instance: Optional[EventBus] = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    global _bus_instance
    with _bus_lock:
        if _bus_instance is None:
            _bus_instance = EventBus()
    return _bus_instance
