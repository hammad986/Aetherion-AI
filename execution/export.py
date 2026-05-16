import json
import logging
from typing import Dict, Any
from execution.replay import ExecutionReplayEngine
from execution.store import ExecutionStore

logger = logging.getLogger("nexora.export")

class ForensicExportSystem:
    """
    Builds portable export bundles containing execution replay timelines, 
    forensic logs, DAG ancestry, and runtime metrics for compliance and offline review.
    """
    def __init__(self, store: ExecutionStore):
        self.engine = ExecutionReplayEngine(store)

    def generate_audit_bundle(self, execution_id: str) -> Dict[str, Any]:
        """
        Creates a comprehensive JSON-serializable forensic bundle for an execution.
        """
        logger.info(f"[Forensics] Generating audit bundle for {execution_id}")
        
        replay_data = self.engine.reconstruct_timeline(execution_id)
        if not replay_data["timeline"]:
            raise ValueError(f"No execution history found for {execution_id}")
            
        # In a full system, this would merge DAG data, user policy, and metric snapshots
        bundle = {
            "metadata": {
                "execution_id": execution_id,
                "exported_at": "CURRENT_TIMESTAMP", # Placeholder
                "schema_version": "v1.0"
            },
            "summary": replay_data["summary"],
            "timeline": replay_data["timeline"],
            "policy_context": {
                "sandbox_active": True,
                "hitl_events_triggered": sum(1 for evt in replay_data["timeline"] if evt.get("event") == "task.paused")
            }
        }
        
        return bundle

    def export_to_json(self, execution_id: str) -> str:
        """Serializes the forensic bundle to a standard JSON format."""
        bundle = self.generate_audit_bundle(execution_id)
        return json.dumps(bundle, indent=2)
