from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional
import time
import uuid

@dataclass
class RuntimeEvent:
    """Canonical event schema for all runtime activities."""
    type: str  # task.started, tool.called, stream.chunk, etc.
    session_id: str
    execution_id: str
    payload: Dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class EventTypes:
    # Task Lifecycle
    TASK_QUEUED = "task.queued"
    TASK_STARTED = "task.started"
    TASK_PROGRESS = "task.progress"
    TASK_WAITING = "task.waiting"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    
    # Tool/Action Lifecycle
    TOOL_CALLED = "tool.called"
    TOOL_COMPLETED = "tool.completed"
    FILE_MODIFIED = "file.modified"
    CMD_RUN = "command.run"
    
    # Stream Lifecycle
    STREAM_CHUNK = "stream.chunk"
    STREAM_CLOSED = "stream.closed"

def create_event(event_type: str, session_id: str, execution_id: str, **kwargs) -> RuntimeEvent:
    """Helper to generate a structured runtime event."""
    return RuntimeEvent(
        type=event_type,
        session_id=session_id,
        execution_id=execution_id,
        payload=kwargs
    )
