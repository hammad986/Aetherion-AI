"""
streaming/sse_manager.py — Replay-Safe SSE Architecture v2
===========================================================
Phase: Runtime Reliability — SSE Sequence Numbering + Replay

Features:
  - Global monotonic sequence counter per session
  - Last-Event-ID support: every SSEEvent gets a numeric id
  - Per-session replay buffer (last REPLAY_BUFFER_SIZE events)
  - Reconnect replay: new clients receive missed events since their Last-Event-ID
  - Dead subscriber fast detection (connected=False guard)
  - Per-session client cap (F-3: 5 max)
  - Stale client reaper (30s TTL for fast cleanup under disconnect storms)
  - Zero duplicate rendering: clients track seen sequence numbers
"""

import json
import logging
import queue as _queue_mod
import threading
import time
import uuid as _uuid
from collections import defaultdict, deque

logger = logging.getLogger("nexora.sse")

REPLAY_BUFFER_SIZE   = 200   # events per session kept for reconnect replay
STALE_TTL_SECONDS    = 30    # evict disconnected clients after this many seconds


class SSEEvent:
    """An SSE frame with optional id, event type, and JSON/text data."""

    def __init__(self, data, event=None, id=None):
        self.data  = data
        self.event = event
        self.id    = id  # Monotonic sequence number (int or None)

    def encode(self) -> str:
        msg = ""
        if self.id is not None:
            msg += f"id: {self.id}\n"
        if self.event is not None:
            msg += f"event: {self.event}\n"
        if isinstance(self.data, dict):
            data_str = json.dumps(self.data)
        else:
            data_str = str(self.data)
        msg += f"data: {data_str}\n\n"
        return msg


class SSEClient:
    """Represents a single active SSE connection."""

    def __init__(self, client_id: str, session_id: str, last_event_id: int = 0):
        self.client_id     = client_id
        self.session_id    = session_id
        self.last_event_id = last_event_id
        self.queue         = _queue_mod.Queue(maxsize=2000)
        self.last_active   = time.time()
        self.connected     = True

    def put(self, event: 'SSEEvent', timeout: float = 0.1) -> bool:
        if not self.connected:
            return False
        try:
            self.queue.put(event, timeout=timeout)
            self.last_active = time.time()
            return True
        except _queue_mod.Full:
            logger.warning("[SSE] Queue full for client %s — dropping event", self.client_id)
            return False


class SSEManager:
    """
    Manages Server-Sent Events lifecycle with replay-safe sequencing.

    Every event is stamped with a monotonic integer id per session.
    New clients receive missed events since their Last-Event-ID (up to REPLAY_BUFFER_SIZE).
    """

    _clients:        dict          = {}
    _lock:           threading.Lock = threading.Lock()
    _seq_counters:   object        = defaultdict(int)
    _replay_buffers: object        = defaultdict(deque)
    _seq_lock:       threading.Lock = threading.Lock()
    _MAX_CLIENTS_PER_SESSION       = 5
    _reaper_started: bool          = False

    @classmethod
    def _start_reaper_once(cls):
        if cls._reaper_started:
            return
        cls._reaper_started = True

        def _reap():
            while True:
                time.sleep(30)
                cutoff = time.time() - STALE_TTL_SECONDS
                with cls._lock:
                    stale = [
                        cid for cid, c in cls._clients.items()
                        if not c.connected and c.last_active < cutoff
                    ]
                for cid in stale:
                    with cls._lock:
                        cls._clients.pop(cid, None)
                    logger.info("[SSE] Reaped stale client %s", cid)
                # Prune replay buffers for sessions with no active clients
                with cls._lock:
                    active_sessions = {c.session_id for c in cls._clients.values()}
                with cls._seq_lock:
                    dead = [
                        sid for sid in list(cls._replay_buffers.keys())
                        if sid not in active_sessions
                    ]
                for sid in dead:
                    del cls._replay_buffers[sid]
                    cls._seq_counters.pop(sid, None)
                    logger.debug("[SSE] Pruned replay buffer for idle session %s", sid)

        threading.Thread(target=_reap, daemon=True, name="sse-reaper").start()

    @classmethod
    def _next_seq(cls, session_id: str) -> int:
        with cls._seq_lock:
            cls._seq_counters[session_id] += 1
            return cls._seq_counters[session_id]

    @classmethod
    def register_client(cls, session_id: str, last_event_id: int = 0) -> 'SSEClient':
        """
        Register a new SSE client. Replays events since last_event_id on reconnect.
        """
        cls._start_reaper_once()
        client_id = f"sse_{int(time.time() * 1000)}_{_uuid.uuid4().hex[:8]}"
        client = SSEClient(client_id, session_id, last_event_id)

        with cls._lock:
            session_clients = sorted(
                [c for c in cls._clients.values() if c.session_id == session_id],
                key=lambda c: c.last_active,
            )
            while len(session_clients) >= cls._MAX_CLIENTS_PER_SESSION:
                evict = session_clients.pop(0)
                evict.connected = False
                cls._clients.pop(evict.client_id, None)
                logger.info("[SSE] Evicted oldest client %s (session cap reached)", evict.client_id)
            cls._clients[client_id] = client

        if last_event_id > 0:
            cls._replay_since(client, last_event_id, session_id)

        logger.info("[SSE] Client registered: %s for session %s (last_event_id=%d)",
                    client_id, session_id, last_event_id)
        return client

    @classmethod
    def _replay_since(cls, client: 'SSEClient', since_seq: int, session_id: str) -> None:
        with cls._seq_lock:
            buf = list(cls._replay_buffers.get(session_id, []))
        replayed = 0
        for event in buf:
            if event.id is not None and event.id > since_seq:
                client.put(event)
                replayed += 1
        if replayed:
            logger.info("[SSE] Replayed %d events (since seq=%d) to client %s",
                        replayed, since_seq, client.client_id)

    @classmethod
    def remove_client(cls, client_id: str) -> None:
        with cls._lock:
            client = cls._clients.pop(client_id, None)
            if client:
                client.connected = False
                logger.info("[SSE] Client removed: %s", client_id)

    @classmethod
    def _local_broadcast_to_session(cls, session_id: str, event_type: str, payload: dict) -> None:
        """Broadcast a sequenced event to all connected clients for a session."""
        seq = cls._next_seq(session_id)
        stamped = dict(payload)
        stamped['_seq'] = seq
        stamped['_sid'] = session_id
        event = SSEEvent(data=stamped, event=event_type, id=seq)

        with cls._seq_lock:
            buf = cls._replay_buffers[session_id]
            buf.append(event)
            while len(buf) > REPLAY_BUFFER_SIZE:
                buf.popleft()

        with cls._lock:
            targets = [c for c in cls._clients.values()
                       if c.session_id == session_id and c.connected]
        for client in targets:
            client.put(event)

    # ── Bridge injection ──────────────────────────────────────────────────────
    # Populated by RedisSSEBridge.init() to avoid circular imports.
    # Falls back to local-only delivery if never set.
    _bridge_fn = None  # type: Optional[callable]

    @classmethod
    def set_bridge(cls, fn) -> None:
        """Inject the Redis bridge broadcast function. Called once at startup."""
        cls._bridge_fn = fn
        logger.info("[SSE] Redis bridge injected: %s", fn)

    @classmethod
    def broadcast_to_session(cls, session_id: str, event_type: str, payload: dict) -> None:
        """
        Broadcast an event to all connected clients for a session.

        Routes via Redis pub/sub bridge when available (multi-worker mode).
        Falls back to local in-process delivery when bridge is absent.
        """
        if cls._bridge_fn is not None:
            cls._bridge_fn(session_id, event_type, payload)
        else:
            cls._local_broadcast_to_session(session_id, event_type, payload)

    @classmethod
    def get_session_seq(cls, session_id: str) -> int:
        """Return current sequence number for a session (used in X-SSE-Seq response header)."""
        with cls._seq_lock:
            return cls._seq_counters.get(session_id, 0)

    # ── Static formatting helpers ────────────────────────────────────────────

    @staticmethod
    def format_event(event_type: str, payload) -> str:
        return SSEEvent(data=payload, event=event_type).encode()

    @staticmethod
    def format_chunk(text: str) -> str:
        return SSEManager.format_event("chunk", {"content": text})

    @staticmethod
    def format_error(msg: str, details=None) -> str:
        payload = {"msg": msg}
        if details:
            payload["details"] = details
        return SSEManager.format_event("error", payload)

    @staticmethod
    def format_done() -> str:
        return SSEManager.format_event("done", {"status": "complete"})
