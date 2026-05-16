import logging
import threading
import sqlite3
import json
import os
from typing import Dict, Any, Optional

logger = logging.getLogger("nexora.hitl")

class HITLEventTracker:
    """
    Manages human-in-the-loop (HITL) pauses and approvals.
    State is persisted to sessions.db to survive process restarts.
    """
    def __init__(self, db_path="sessions.db"):
        self.db_path = db_path
        self._pauses: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hitl_requests (
                    execution_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    feedback TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def pause_execution(self, execution_id: str) -> bool:
        """Triggers a pause state for an active execution."""
        with self._lock:
            if execution_id not in self._pauses:
                self._pauses[execution_id] = threading.Event()
            self._pauses[execution_id].clear() # Block wait() calls
        logger.info(f"[HITL] Execution {execution_id} paused by operator.")
        return True

    def resume_execution(self, execution_id: str) -> bool:
        """Releases a pause state, resuming the execution thread."""
        with self._lock:
            if execution_id in self._pauses:
                self._pauses[execution_id].set()
                logger.info(f"[HITL] Execution {execution_id} resumed by operator.")
                return True
        return False

    def request_approval(self, execution_id: str, payload: dict, timeout_sec: int = 300) -> Dict[str, Any]:
        """
        Blocks the active thread until a human operator approves or rejects the action.
        Persists request to sessions.db.
        """
        logger.info(f"[HITL] Execution {execution_id} awaiting approval for: {payload}")
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO hitl_requests (execution_id, payload, status) VALUES (?, ?, ?)",
                (execution_id, json.dumps(payload), "pending")
            )
            conn.commit()

        with self._lock:
            evt = threading.Event()
            self._pauses[execution_id] = evt

        # Broadcast hitl.approval_required to all SSE clients for the session
        try:
            from streaming.sse_manager import SSEManager
            session_id = payload.get("session_id", "")
            if session_id:
                SSEManager.broadcast_to_session(session_id, "hitl.approval_required", {
                    "execution_id": execution_id,
                    **payload,
                })
        except Exception as _sse_err:
            logger.warning(f"[HITL] SSE broadcast failed: {_sse_err}")

        # Block the executing thread (daemon worker — NOT a gunicorn request thread)
        evt.wait(timeout=timeout_sec)
        
        with self._lock:
            self._pauses.pop(execution_id, None)
            
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT status, feedback FROM hitl_requests WHERE execution_id = ?", (execution_id,))
            row = cur.fetchone()
            if row:
                status, feedback = row
            else:
                status, feedback = "timeout", ""
            
        logger.info(f"[HITL] Execution {execution_id} received approval response: {status}")
        return {"status": status, "feedback": feedback}

    def provide_approval(self, execution_id: str, status: str, feedback: str = "") -> bool:
        """
        Unblocks an execution thread with the operator's decision.
        Updates sessions.db.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE hitl_requests SET status = ?, feedback = ? WHERE execution_id = ?",
                (status, feedback, execution_id)
            )
            conn.commit()

        with self._lock:
            if execution_id in self._pauses:
                self._pauses[execution_id].set()
                return True
        return False

global_hitl_tracker = HITLEventTracker()
