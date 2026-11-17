import contextlib
import logging
import threading
import sqlite3
import json
import os
from typing import Dict, Any, Optional

logger = logging.getLogger("aetherion.hitl")

class HITLEventTracker:
    """
    Manages human-in-the-Loop (HITL) pauses and approvals.
    State is persisted to sessions.db to survive process restarts.
    """
    def __init__(self, db_path="sessions.db"):
        self.db_path = db_path
        self._pauses: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS hitl_requests (
                        execution_id TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        status TEXT NOT NULL,
                        feedback TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)


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

    def request_approval(self, execution_id: str, payload: dict, timeout_sec: int = 300, cancel_event: Optional[threading.Event] = None) -> Dict[str, Any]:
        """
        Blocks the active thread until a human operator approves or rejects the action.
        Persists request to sessions.db.
        """
        if cancel_event and cancel_event.is_set():
            return {"status": "cancelled", "feedback": "Task cancelled before approval"}

        logger.info(f"[HITL] Execution {execution_id} awaiting approval for: {payload}")
        
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO hitl_requests (execution_id, payload, status, feedback) VALUES (?, ?, 'pending', '')",
                    (execution_id, json.dumps(payload))
                )

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

        # ── Wire to Z29 Mission Control ──
        session_id = payload.get("session_id", "")
        if session_id:
            try:
                from runtime.mission_control import pause_mission
                pause_mission(session_id, note=f"Awaiting HITL approval: {payload.get('prompt', 'Operator Input Required')}")
            except Exception as e:
                logger.debug(f"[Z29] Failed to route pause to mission control: {e}")

        # Lightweight watcher for cancellation
        watcher_stop = threading.Event()
        if cancel_event:
            def _cancel_monitor():
                while not watcher_stop.is_set():
                    if cancel_event.is_set():
                        self.provide_approval(execution_id, status="cancelled", feedback="Execution cancelled")
                        break
                    watcher_stop.wait(timeout=0.05)

            threading.Thread(target=_cancel_monitor, daemon=True).start()

        # Block the executing thread (daemon worker — NOT a gunicorn request thread)
        completed = evt.wait(timeout=timeout_sec)
        watcher_stop.set()

        # If wait timed out without event being triggered
        if not completed:
            with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
                with conn:
                    conn.execute(
                        "UPDATE hitl_requests SET status = 'timeout', feedback = 'Operator approval timed out' WHERE execution_id = ? AND status = 'pending'",
                        (execution_id,)
                    )
        
        if session_id:
            try:
                from runtime.mission_control import resume_mission
                resume_mission(session_id, note="HITL approval resolved")
            except Exception as e:
                logger.debug(f"[Z29] Failed to route resume to mission control: {e}")
        
        with self._lock:
            self._pauses.pop(execution_id, None)
            
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            cur = conn.execute("SELECT status, feedback FROM hitl_requests WHERE execution_id = ?", (execution_id,))
            row = cur.fetchone()
            if row:
                status, feedback = row
                if status == "pending":
                    status = "timeout"
            else:
                status, feedback = "timeout", ""
            
        logger.info(f"[HITL] Execution {execution_id} received approval response: {status}")
        return {"status": status, "feedback": feedback or ""}

    def provide_approval(self, execution_id: str, status: str, feedback: str = "") -> bool:
        """
        Unblocks an execution thread with the operator's decision.
        Atomic conditional update: returns True only if pending request was transitioned.
        """
        with self._lock:
            updated_db = False
            with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
                with conn:
                    cur = conn.execute(
                        "UPDATE hitl_requests SET status = ?, feedback = ? WHERE execution_id = ? AND status = 'pending'",
                        (status, feedback, execution_id)
                    )
                    updated_db = cur.rowcount > 0

            if execution_id in self._pauses:
                self._pauses[execution_id].set()
                return updated_db

            return updated_db

    def cancel_approval(self, execution_id: str, feedback: str = "Execution cancelled") -> bool:
        """Explicitly cancels an active approval request."""
        return self.provide_approval(execution_id, status="cancelled", feedback=feedback)

global_hitl_tracker = HITLEventTracker()


def resolve_gate(execution_id: str, approved: bool, comments: str = "") -> bool:
    """
    Resolves a Human-in-the-Loop gate for an execution.
    Called by task approval route and API handlers.
    """
    status = "approved" if approved else "rejected"
    return global_hitl_tracker.provide_approval(execution_id, status=status, feedback=comments)


def resume_session_hitl(session_id: str, status: str = "approved", feedback: str = "") -> bool:
    """Finds any pending HITL approval for this session and unblocks it."""
    unblocked = False
    try:
        with contextlib.closing(sqlite3.connect(global_hitl_tracker.db_path)) as conn:
            cur = conn.execute("SELECT execution_id, payload FROM hitl_requests WHERE status = 'pending'")
            rows = cur.fetchall()

        for exec_id, payload_str in rows:
            try:
                payload = json.loads(payload_str)
                if payload.get("session_id") == session_id:
                    global_hitl_tracker.provide_approval(exec_id, status, feedback)
                    unblocked = True
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"[HITL] Failed to resume session HITL: {e}")
    return unblocked

