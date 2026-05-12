from execution.worker import LightweightWorker, ExecutionTask, ResourceGovernance
from execution.events import create_event, EventTypes
from streaming.sse_manager import SSEManager
import os
import logging

logger = logging.getLogger("nexora.job_manager")

class JobManager:
    """
    Central orchestration interface.
    Manages queues, execution states, and delegates to isolated workers.
    """
    def __init__(self):
        self.worker = LightweightWorker()

    # ── REALTIME WIRING (Op-2): real agent runner ─────────────────────────────
    @staticmethod
    def _build_real_runner(session_id: str):
        """
        Returns a runner_fn bound to the given session_id.
        Activated ONLY when AETHERION_REALTIME_V1=true; otherwise falls back
        to a safe no-op stub so existing behaviour is fully preserved.
        """
        if os.getenv("AETHERION_REALTIME_V1", "").lower() != "true":
            # Feature-flag OFF: run the original mock to preserve legacy behaviour.
            def _legacy_runner(t: ExecutionTask):
                import time
                for i in range(5):
                    t.check_ttl()
                    if t.is_cancelled:
                        logger.info(f"Task {t.execution_id} cancelled during execution.")
                        break
                    evt = create_event(EventTypes.TOOL_CALLED, t.session_id, t.execution_id, tool="search", step=i)
                    SSEManager.broadcast_to_session(t.session_id, "runtime.event", evt.to_dict())
                    SSEManager.broadcast_to_session(t.session_id, "chunk", {"content": f" Thinking step {i}...\n"})
                    t._cancel_event.wait(0.5)
                return {"final_answer": "Task complete (legacy stub)"}
            return _legacy_runner

        # Feature-flag ON: wire real Agent execution.
        def _real_runner(t: ExecutionTask):
            """
            Bridges ExecutionTask lifecycle into Agent.run().
            - emit_fn forwards SSE events to all subscribers of the session.
            - Cancellation: the task's _cancel_event is checked after each
              SSE emission; if set, we raise an interrupt inside the emit path
              so the agent loop terminates cleanly on the next iteration.
            """
            from agent import Agent
            from config import Config
            from memory import Memory

            prompt = t.payload.get("prompt", "").strip()
            if not prompt:
                return {"ok": False, "error": "missing_prompt"}

            def _emit(kind: str, payload: dict) -> None:
                """SSE bridge: broadcast event; honour cancellation signal."""
                if t.is_cancelled:
                    # Raise to unwind the agent loop via the except clause.
                    raise InterruptedError(f"Task {t.execution_id} cancelled by operator.")
                try:
                    SSEManager.broadcast_to_session(
                        session_id,
                        kind,
                        {"execution_id": t.execution_id, **payload},
                    )
                except Exception as _sse_err:
                    # SSE broadcast failures must never crash the agent.
                    logger.warning(f"[JobManager] SSE broadcast failed: {_sse_err}")

            try:
                cfg    = Config()
                memory = Memory()
                agent  = Agent(
                    config=cfg,
                    memory=memory,
                    emit_fn=_emit,
                    session_id=session_id,
                )
                result = agent.run(prompt)
                return {"ok": True, "output": result}

            except InterruptedError as _ie:
                logger.info(f"[JobManager] {_ie}")
                return {"ok": False, "cancelled": True}

            except Exception as _e:
                logger.exception(f"[JobManager] Real runner crashed: {_e}")
                return {"ok": False, "error": str(_e)}

        return _real_runner

    def submit_task(self, session_id: str, payload: dict, limits: ResourceGovernance = None) -> ExecutionTask:
        """Creates and enqueues a new autonomous task."""
        if limits is None:
            limits = ResourceGovernance()

        task = ExecutionTask(session_id=session_id, payload=payload, limits=limits)
        runner = self._build_real_runner(session_id)
        self.worker.execute_async(task, runner_fn=runner)
        return task

    def cancel_task(self, execution_id: str) -> bool:
        """Attempts to cleanly interrupt a running task."""
        task = self.worker.get_task(execution_id)
        if not task:
            return False
        task.cancel()
        return True

    def get_status(self, execution_id: str) -> dict:
        """Returns structured state of a given task."""
        task = self.worker.get_task(execution_id)
        if not task:
            return {"status": "unknown_or_archived"}
        return {
            "execution_id": task.execution_id,
            "session_id":   task.session_id,
            "status":       task.status,
            "created_at":   task.created_at,
            "started_at":   task.started_at,
            "completed_at": task.completed_at,
        }

# Global singleton for the application boundary
global_job_manager = JobManager()
