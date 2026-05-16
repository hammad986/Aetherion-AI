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

        # Feature-flag ON: wire real Agent execution with Coordination Bus.
        def _real_runner(t: ExecutionTask):
            """
            Bridges ExecutionTask lifecycle into Agent.run() with full
            multi-agent coordination scaffolding (resource governance,
            workspace locking, agent registry, memory arbitration).
            """
            from agent import Agent
            from config import Config
            from memory import Memory
            from execution.coordination_bus import CoordinationBus
            from execution.agent_registry import AgentRole

            prompt = t.payload.get("prompt", "").strip()
            if not prompt:
                return {"ok": False, "error": "missing_prompt"}

            # ── Stand up coordination bus for this execution ──────────────────
            bus = CoordinationBus.for_session(session_id, emit_fn=None)

            def _emit(kind: str, payload: dict) -> None:
                """SSE bridge: broadcast event; honour cancellation signal."""
                if t.is_cancelled:
                    raise InterruptedError(f"Task {t.execution_id} cancelled by operator.")
                try:
                    SSEManager.broadcast_to_session(
                        session_id,
                        kind,
                        {"execution_id": t.execution_id, **payload},
                    )
                except Exception as _sse_err:
                    logger.warning(f"[JobManager] SSE broadcast failed: {_sse_err}")

            # Wire emit_fn into bus for coordination snapshots
            bus._emit = _emit

            try:
                agent_instance = bus.register_agent(current_step="initializing")
            except ValueError as _reg_err:
                logger.warning(f"[JobManager] Agent registration conflict: {_reg_err}")
                agent_instance = None  # proceed without registration (single-agent fallback)

            try:
                cfg    = Config()
                memory = Memory()
                agent  = Agent(
                    config=cfg,
                    memory=memory,
                    emit_fn=_emit,
                    session_id=session_id,
                )
                # Attach the coordination bus so agent can use workspace locks
                agent._coordination_bus = bus
                agent._execution_id = t.execution_id

                result = agent.run(prompt)

                # Record coordination outcome into memory arbiter
                bus.write_memory(
                    key="last_task_outcome",
                    value=f"completed: {str(result)[:100]}",
                    confidence=0.85,
                    verified=True,
                )
                bus.emit_coordination_snapshot()
                return {"ok": True, "output": result}

            except InterruptedError as _ie:
                logger.info(f"[JobManager] {_ie}")
                return {"ok": False, "cancelled": True}

            except Exception as _e:
                logger.exception(f"[JobManager] Real runner crashed: {_e}")
                return {"ok": False, "error": str(_e)}

            finally:
                bus.deregister_agent(status="completed" if not t.is_cancelled else "cancelled")

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
