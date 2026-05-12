import json
import logging
import time
import queue
import threading

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

    @classmethod
    def register_client(cls, session_id):
        # BUGFIX: timestamp-only IDs collide under concurrent registration
        # (two clients in same millisecond → second overwrites first in _clients).
        # uuid4 suffix guarantees uniqueness regardless of timing.
        import uuid as _uuid
        client_id = f"sse_{int(time.time() * 1000)}_{_uuid.uuid4().hex[:8]}"
        client = SSEClient(client_id, session_id)
        with cls._lock:
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
