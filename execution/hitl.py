import logging
import threading
from typing import Dict, Any, Optional

logger = logging.getLogger("nexora.hitl")

class HITLEventTracker:
    """
    Manages human-in-the-loop (HITL) pauses and approvals.
    Uses threading Events to block execution threads until human intervention.
    """
    def __init__(self):
        # Maps execution_id to a threading.Event used for pausing
        self._pauses: Dict[str, threading.Event] = {}
        # Maps execution_id to a dict holding operator feedback (e.g., approval status)
        self._responses: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

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
        Called BY the execution thread.
        """
        logger.info(f"[HITL] Execution {execution_id} awaiting approval for: {payload}")
        
        with self._lock:
            evt = threading.Event()
            self._pauses[execution_id] = evt
            self._responses.pop(execution_id, None)

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
            response = self._responses.pop(execution_id, {"status": "timeout"})
            
        logger.info(f"[HITL] Execution {execution_id} received approval response: {response['status']}")
        return response

    def provide_approval(self, execution_id: str, status: str, feedback: str = "") -> bool:
        """
        Unblocks an execution thread with the operator's decision.
        Called BY the API/Frontend request.
        """
        with self._lock:
            if execution_id in self._pauses:
                self._responses[execution_id] = {"status": status, "feedback": feedback}
                self._pauses[execution_id].set()
                return True
        return False

global_hitl_tracker = HITLEventTracker()
