import threading
import time
import logging
import uuid
from typing import Callable, Any, Dict, Optional
from execution.events import create_event, EventTypes
from streaming.sse_manager import SSEManager

logger = logging.getLogger("nexora.worker")

class ResourceGovernance:
    def __init__(self, ttl_seconds=3600, max_tokens=100000, max_memory_mb=1024):
        self.ttl_seconds = ttl_seconds
        self.max_tokens = max_tokens
        self.max_memory_mb = max_memory_mb

class ExecutionTask:
    def __init__(self, session_id: str, payload: Dict[str, Any], limits: ResourceGovernance):
        self.execution_id = f"exec_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        self.session_id = session_id
        self.payload = payload
        self.limits = limits
        
        self.status = "queued"
        self.created_at = time.time()
        self.started_at = None
        self.completed_at = None
        
        self.is_cancelled = False
        self._cancel_event = threading.Event()

    def cancel(self):
        """Signals the task to stop."""
        self.is_cancelled = True
        self._cancel_event.set()
        
    def check_ttl(self):
        """Raises TimeoutError if TTL exceeded."""
        if self.started_at and (time.time() - self.started_at) > self.limits.ttl_seconds:
            raise TimeoutError(f"Execution {self.execution_id} exceeded TTL of {self.limits.ttl_seconds}s")

class LightweightWorker:
    """
    Isolates task execution from the HTTP request thread.
    Enforces cancellation and TTL checks.
    """
    def __init__(self):
        self._active_tasks: Dict[str, ExecutionTask] = {}
        self._lock = threading.Lock()

    def register_task(self, task: ExecutionTask):
        with self._lock:
            self._active_tasks[task.execution_id] = task

    def get_task(self, execution_id: str) -> Optional[ExecutionTask]:
        with self._lock:
            return self._active_tasks.get(execution_id)

    def execute_async(self, task: ExecutionTask, runner_fn: Callable):
        """Spawns a managed background thread for the task."""
        self.register_task(task)
        
        def _wrapper():
            task.started_at = time.time()
            task.status = "running"
            
            # Emit TASK_STARTED
            SSEManager.broadcast_to_session(
                task.session_id, 
                "runtime.event", 
                create_event(EventTypes.TASK_STARTED, task.session_id, task.execution_id).to_dict()
            )
            
            try:
                # runner_fn is expected to yield chunks or progress, checking task.is_cancelled internally
                result = runner_fn(task)
                
                if task.is_cancelled:
                    task.status = "cancelled"
                    evt_type = EventTypes.TASK_CANCELLED
                else:
                    task.status = "completed"
                    evt_type = EventTypes.TASK_COMPLETED
                    
                SSEManager.broadcast_to_session(
                    task.session_id, 
                    "runtime.event", 
                    create_event(evt_type, task.session_id, task.execution_id, result=result).to_dict()
                )
                
            except Exception as e:
                logger.exception(f"Task {task.execution_id} failed: {e}")
                task.status = "failed"
                SSEManager.broadcast_to_session(
                    task.session_id, 
                    "runtime.event", 
                    create_event(EventTypes.TASK_FAILED, task.session_id, task.execution_id, error=str(e)).to_dict()
                )
            finally:
                task.completed_at = time.time()
                SSEManager.broadcast_to_session(task.session_id, "done", {"status": task.status})
                with self._lock:
                    self._active_tasks.pop(task.execution_id, None)

        thread = threading.Thread(target=_wrapper, daemon=True, name=f"worker-{task.execution_id}")
        thread.start()
        return task
