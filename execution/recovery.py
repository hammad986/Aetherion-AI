import logging
from execution.store import ExecutionStore
from execution.job_manager import global_job_manager
from execution.events import create_event, EventTypes

logger = logging.getLogger("nexora.recovery")

class RuntimeRecovery:
    """
    Handles crash recovery, stale job cleanup, and execution resumption
    by interfacing with the ExecutionStore and the active JobManager.
    """
    def __init__(self, store: ExecutionStore):
        self.store = store

    def sweep_stale_jobs(self):
        """
        Identifies jobs abandoned due to worker crash or ungraceful shutdown.
        Marks them as failed in the persistent store.
        """
        stale_ids = self.store.get_stale_executions()
        for eid in stale_ids:
            logger.warning(f"[Recovery] Reaping stale execution {eid}")
            
            # Retrieve last known state
            events = self.store.get_events(eid)
            session_id = events[0]["session_id"] if events else "unknown"
            
            # Append failure event to log
            fail_evt = create_event(EventTypes.TASK_FAILED, session_id, eid, 
                                    error="Execution orphaned (worker crash/timeout).")
            self.store.append_event(fail_evt, correlation_id="recovery_sweep")
            
            # Update snapshot
            self.store.upsert_execution(
                execution_id=eid, 
                session_id=session_id, 
                status="failed", 
                payload={"recovery": "orphaned"}
            )

    def attempt_resume(self, execution_id: str) -> bool:
        """
        Attempts to resume an interrupted task from its last checkpoint.
        Requires event history to rebuild context.
        """
        events = self.store.get_events(execution_id)
        if not events:
            logger.error(f"[Recovery] Cannot resume {execution_id}: No event history.")
            return False
            
        # Determine last valid state
        last_evt = events[-1]
        if last_evt["event_type"] in [EventTypes.TASK_COMPLETED, EventTypes.TASK_CANCELLED]:
            logger.info(f"[Recovery] Execution {execution_id} is already terminal.")
            return False
            
        logger.info(f"[Recovery] Preparing to resume {execution_id} from state: {last_evt['event_type']}")
        
        # In a full implementation, we would extract the payload, re-instantiate the ExecutionTask,
        # and submit it to the JobManager, skipping already completed tool calls based on the event log.
        # This function acts as the interface contract for that architecture.
        return True
