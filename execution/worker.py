import os
import signal
import threading
import time
import logging
import uuid
from typing import Callable, Any, Dict, Optional
from execution.events import create_event, EventTypes
from streaming.sse_manager import SSEManager

logger = logging.getLogger("nexora.worker")

# Z10: lazy import to avoid circular dependency at module load time
def _get_nx_redis():
    try:
        from redis_layer import get_nx_redis
        return get_nx_redis()
    except Exception:
        return None


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

        # Z10: track the subprocess handle so cancel() can SIGTERM it
        self._proc = None
        self._proc_lock = threading.Lock()

    def attach_proc(self, proc) -> None:
        """Register a subprocess handle so cancel() can terminate it directly."""
        with self._proc_lock:
            self._proc = proc

    def detach_proc(self) -> None:
        """Remove the subprocess handle after it has exited."""
        with self._proc_lock:
            self._proc = None

    def cancel(self):
        """
        Z10-hardened cancel:
          1. Sets cooperative cancellation flag (checked by runner_fn loops).
          2. SIGTERMs the attached subprocess (if any) — no zombie left behind.
          3. Falls back to SIGKILL after 3 s if SIGTERM is ignored.
        """
        self.is_cancelled = True
        self._cancel_event.set()

        with self._proc_lock:
            proc = self._proc

        if proc is None:
            return

        try:
            # Prefer process-group kill to catch grandchildren
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                logger.info(
                    "[Worker] SIGTERM sent to process group %d (session=%s exec=%s)",
                    pgid, self.session_id, self.execution_id,
                )
            except (ProcessLookupError, OSError):
                proc.terminate()
                logger.info(
                    "[Worker] SIGTERM sent to pid %d (session=%s exec=%s)",
                    proc.pid, self.session_id, self.execution_id,
                )

            # Escalate to SIGKILL if process still alive after 3 s
            def _escalate():
                time.sleep(3)
                if proc.poll() is None:
                    try:
                        pgid = os.getpgid(proc.pid)
                        os.killpg(pgid, signal.SIGKILL)
                        logger.warning(
                            "[Worker] SIGKILL escalated for process group %d (session=%s)",
                            pgid, self.session_id,
                        )
                    except (ProcessLookupError, OSError):
                        try:
                            proc.kill()
                        except Exception:
                            pass

            threading.Thread(target=_escalate, daemon=True,
                             name=f"sigkill-escalator-{self.execution_id}").start()

        except Exception as e:
            logger.warning("[Worker] cancel() proc termination error: %s", e)

    def check_ttl(self):
        """Raises TimeoutError if TTL exceeded."""
        if self.started_at and (time.time() - self.started_at) > self.limits.ttl_seconds:
            raise TimeoutError(f"Execution {self.execution_id} exceeded TTL of {self.limits.ttl_seconds}s")


class LightweightWorker:
    """
    Isolates task execution from the HTTP request thread.

    Z10 hardening:
      • Polls Redis stop signal every STOP_POLL_INTERVAL seconds.
      • Calls ExecutionTask.cancel() (which SIGTERMs the subprocess) when
        a cross-worker stop is detected.
      • Acknowledges the stop signal via NexoraRedisLayer.ack_stop() to
        prevent duplicate termination attempts.
      • Clears proc PID from Redis on completion/failure/cancellation.
    """

    STOP_POLL_INTERVAL = 1.0   # seconds between Redis stop flag checks

    def __init__(self):
        self._active_tasks: Dict[str, ExecutionTask] = {}
        self._lock = threading.Lock()

    def register_task(self, task: ExecutionTask):
        with self._lock:
            self._active_tasks[task.execution_id] = task

    def get_task(self, execution_id: str) -> Optional[ExecutionTask]:
        with self._lock:
            return self._active_tasks.get(execution_id)

    def _start_stop_poller(self, task: ExecutionTask) -> threading.Thread:
        """
        Background thread that polls Redis for a cross-worker stop signal.
        When detected, calls task.cancel() and acknowledges the stop.
        """
        nx = _get_nx_redis()

        def _poll():
            while not task._cancel_event.is_set():
                task._cancel_event.wait(timeout=self.STOP_POLL_INTERVAL)
                if task._cancel_event.is_set():
                    break
                if nx is None:
                    continue
                try:
                    if nx.check_stop_requested(task.session_id):
                        logger.info(
                            "[Worker] Cross-worker stop detected for session %s (exec=%s)",
                            task.session_id, task.execution_id,
                        )
                        task.cancel()
                        nx.ack_stop(task.session_id)
                        break
                except Exception as e:
                    logger.debug("[Worker] stop poller error: %s", e)

        t = threading.Thread(
            target=_poll,
            daemon=True,
            name=f"stop-poller-{task.execution_id}",
        )
        t.start()
        return t

    def execute_async(self, task: ExecutionTask, runner_fn: Callable):
        """Spawns a managed background thread for the task."""
        self.register_task(task)
        nx = _get_nx_redis()

        def _wrapper():
            task.started_at = time.time()
            task.status = "running"

            # Z10: start Redis stop poller
            poller = self._start_stop_poller(task)

            # Emit TASK_STARTED
            SSEManager.broadcast_to_session(
                task.session_id,
                "runtime.event",
                create_event(EventTypes.TASK_STARTED, task.session_id, task.execution_id).to_dict()
            )

            try:
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
                # Z10: signal stop-poller thread to exit
                task._cancel_event.set()

                # Z10: clear proc PID from Redis
                if nx:
                    try:
                        nx.clear_proc_pid(task.session_id)
                        # Clear orphan marker if session was marked during interruption
                        nx.clear_orphan(task.session_id)
                    except Exception as _e:
                        logger.debug("[Worker] Redis cleanup error: %s", _e)

                SSEManager.broadcast_to_session(task.session_id, "done", {"status": task.status})
                with self._lock:
                    self._active_tasks.pop(task.execution_id, None)

        thread = threading.Thread(target=_wrapper, daemon=True, name=f"worker-{task.execution_id}")
        thread.start()
        return task
