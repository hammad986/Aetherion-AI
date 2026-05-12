import json
import logging
import time
import queue
import threading
import uuid as _uuid

logger = logging.getLogger("nexora.sse")

class SSEEvent:
    def __init__(self, data, event=None, id=None):
        self.data = data
        self.event = event
        self.id = id

    def encode(self):
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
    def __init__(self, client_id, session_id):
        self.client_id = client_id
        self.session_id = session_id
        self.queue = queue.Queue(maxsize=1000)  # Backpressure protection
        self.last_active = time.time()
        self.connected = True

    def put(self, event: SSEEvent, timeout=2.0):
        if not self.connected:
            return False
        try:
            self.queue.put(event, timeout=timeout)
            self.last_active = time.time()   # F-2: track delivery time for reaper
            return True
        except queue.Full:
            logger.warning(f"[SSE] Queue full for client {self.client_id}. Dropping event.")
            return False

class SSEManager:
    """
    Manages Server-Sent Events lifecycle, client tracking, disconnects,
    and standardizes payload schemas.
    """
    _clients = {}
    _lock = threading.Lock()
    _MAX_CLIENTS_PER_SESSION = 5          # F-3: cap per-session connections
    _STALE_TTL_SECONDS       = 120        # F-2: evict disconnected clients after 2 min
    _reaper_started          = False

    @classmethod
    def _start_reaper_once(cls):
        """Lazily start a background thread that evicts stale disconnected clients."""
        if cls._reaper_started:
            return
        cls._reaper_started = True
        def _reap():
            while True:
                time.sleep(60)
                cutoff = time.time() - cls._STALE_TTL_SECONDS
                with cls._lock:
                    stale = [
                        cid for cid, c in cls._clients.items()
                        if not c.connected and c.last_active < cutoff
                    ]
                for cid in stale:
                    with cls._lock:
                        cls._clients.pop(cid, None)
                    logger.info(f"[SSE] Reaped stale client {cid}")
        t = threading.Thread(target=_reap, daemon=True, name="sse-reaper")
        t.start()

    @classmethod
    def register_client(cls, session_id):
        cls._start_reaper_once()
        # BUGFIX: timestamp-only IDs collide under concurrent registration.
        # uuid4 suffix guarantees uniqueness regardless of timing.
        client_id = f"sse_{int(time.time() * 1000)}_{_uuid.uuid4().hex[:8]}"
        client = SSEClient(client_id, session_id)
        with cls._lock:
            # F-3: enforce per-session client cap — evict oldest if exceeded
            session_clients = sorted(
                [c for c in cls._clients.values() if c.session_id == session_id],
                key=lambda c: c.last_active
            )
            while len(session_clients) >= cls._MAX_CLIENTS_PER_SESSION:
                evict = session_clients.pop(0)
                evict.connected = False
                cls._clients.pop(evict.client_id, None)
                logger.info(f"[SSE] Evicted oldest client {evict.client_id} (session cap reached)")
            cls._clients[client_id] = client
        logger.info(f"[SSE] Client registered: {client_id} for session {session_id}")
        return client

    @classmethod
    def remove_client(cls, client_id):
        with cls._lock:
            client = cls._clients.pop(client_id, None)
            if client:
                client.connected = False
                logger.info(f"[SSE] Client removed: {client_id}")

    @classmethod
    def broadcast_to_session(cls, session_id, event_type, payload):
        """Sends an event to all connected SSE clients for a specific session."""
        event = SSEEvent(data=payload, event=event_type)
        with cls._lock:
            targets = [c for c in cls._clients.values() if c.session_id == session_id]
        
        for client in targets:
            client.put(event)

    @staticmethod
    def format_event(event_type, payload):
        return SSEEvent(data=payload, event=event_type).encode()

    @staticmethod
    def format_chunk(text):
        return SSEManager.format_event("chunk", {"content": text})

    @staticmethod
    def format_error(msg, details=None):
        payload = {"msg": msg}
        if details:
            payload["details"] = details
        return SSEManager.format_event("error", payload)

    @staticmethod
    def format_done():
        return SSEManager.format_event("done", {"status": "complete"})
