import logging
from typing import List, Dict, Any, Optional
from execution.store import ExecutionStore
from execution.events import EventTypes

logger = logging.getLogger("nexora.replay")

class ExecutionReplayEngine:
    """
    Reconstructs chronological execution state, reasoning steps, tool calls,
    and file modifications by folding the append-only event log.
    Enables time-travel debugging and execution seek playback.
    """
    def __init__(self, store: ExecutionStore):
        self.store = store

    def reconstruct_timeline(self, execution_id: str) -> Dict[str, Any]:
        """
        Builds a comprehensive structural timeline of the execution.
        """
        events = self.store.get_events(execution_id)
        if not events:
            return {"execution_id": execution_id, "timeline": [], "state": "unknown"}

        timeline = []
        state_summary = {
            "total_tools_called": 0,
            "total_files_modified": 0,
            "has_errors": False,
            "final_status": "in_progress",
            "duration_ms": 0
        }

        start_time = events[0]["timestamp"]
        end_time = start_time

        for evt in events:
            evt_type = evt["event_type"]
            end_time = max(end_time, evt["timestamp"])
            
            # Record semantic timeline points
            if evt_type in [EventTypes.TASK_STARTED, EventTypes.TASK_COMPLETED, EventTypes.TASK_FAILED, EventTypes.TASK_CANCELLED]:
                timeline.append({
                    "timestamp": evt["timestamp"],
                    "type": "lifecycle",
                    "event": evt_type,
                    "details": evt["payload"]
                })
                if evt_type in [EventTypes.TASK_COMPLETED, EventTypes.TASK_FAILED, EventTypes.TASK_CANCELLED]:
                    state_summary["final_status"] = evt_type.split('.')[-1]
                if evt_type == EventTypes.TASK_FAILED:
                    state_summary["has_errors"] = True

            elif evt_type == EventTypes.TOOL_CALLED:
                state_summary["total_tools_called"] += 1
                timeline.append({
                    "timestamp": evt["timestamp"],
                    "type": "tool",
                    "tool": evt["payload"].get("tool"),
                    "details": evt["payload"]
                })

            elif evt_type == EventTypes.FILE_MODIFIED:
                state_summary["total_files_modified"] += 1
                timeline.append({
                    "timestamp": evt["timestamp"],
                    "type": "filesystem",
                    "file": evt["payload"].get("file_path"),
                    "details": evt["payload"]
                })
                
        state_summary["duration_ms"] = int((end_time - start_time) * 1000)

        return {
            "execution_id": execution_id,
            "summary": state_summary,
            "timeline": timeline
        }

    def seek_state(self, execution_id: str, target_timestamp: float) -> Dict[str, Any]:
        """
        Reconstructs the precise runtime state context at an arbitrary point in time.
        """
        events = self.store.get_events(execution_id)
        
        reconstructed_state = {
            "active_tools": [],
            "files_modified_up_to_point": [],
            "last_known_status": "queued",
            "stream_transcript": ""
        }
        
        for evt in events:
            if evt["timestamp"] > target_timestamp:
                break
                
            evt_type = evt["event_type"]
            
            if evt_type == EventTypes.TASK_STARTED:
                reconstructed_state["last_known_status"] = "running"
            elif evt_type == EventTypes.TOOL_CALLED:
                reconstructed_state["active_tools"].append(evt["payload"].get("tool"))
            elif evt_type == EventTypes.FILE_MODIFIED:
                reconstructed_state["files_modified_up_to_point"].append(evt["payload"].get("file_path"))
            elif evt_type == EventTypes.STREAM_CHUNK:
                reconstructed_state["stream_transcript"] += evt["payload"].get("content", "")
                
        return reconstructed_state
