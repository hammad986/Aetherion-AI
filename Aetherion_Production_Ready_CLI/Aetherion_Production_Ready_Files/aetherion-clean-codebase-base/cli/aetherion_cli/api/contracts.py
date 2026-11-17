"""
aetherion_cli/api/contracts.py
Typed request/response data contracts and execution lifecycle enums for Aetherion CLI.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ExecutionState(str, Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    VERIFYING = "verifying"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    ORPHANED = "orphaned"
    STOPPED = "stopped"

    @classmethod
    def is_terminal(cls, status: str) -> bool:
        normalized = str(status).lower().strip()
        return normalized in {
            cls.CANCELLED.value,
            cls.COMPLETED.value,
            cls.SUCCEEDED.value,
            cls.FAILED.value,
            cls.TIMEOUT.value,
            cls.STOPPED.value,
        }


@dataclass
class TaskCreateRequest:
    task: str
    workspace_id: Optional[str] = None
    model: str = "auto"
    mode: str = "autonomous"
    budget_cap: Optional[float] = None
    use_worktree: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskStatusResponse:
    task_id: str
    status: str
    workspace_id: Optional[str] = None
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    terminal: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskStatusResponse:
        st = data.get("status") or "queued"
        return cls(
            task_id=data.get("task_id") or data.get("id") or "",
            status=st,
            workspace_id=data.get("workspace_id") or data.get("session_id"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            result=data.get("result"),
            error=data.get("error"),
            terminal=ExecutionState.is_terminal(st),
        )


@dataclass
class WorkspaceInfo:
    workspace_id: str
    name: str
    status: str = "ready"
    created_at: Optional[float] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WorkspaceInfo:
        return cls(
            workspace_id=data.get("id") or data.get("workspace_id") or "",
            name=data.get("name") or data.get("title") or "default",
            status=data.get("status") or "ready",
            created_at=data.get("created_at"),
        )


@dataclass
class ArtifactInfo:
    artifact_id: str
    session_id: str
    name: str
    file_size: int = 0
    checksum: str = ""
    created_at: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ArtifactInfo:
        return cls(
            artifact_id=data.get("artifact_id") or data.get("id") or "",
            session_id=data.get("session_id") or "",
            name=data.get("name") or "artifact",
            file_size=data.get("file_size") or 0,
            checksum=data.get("checksum") or "",
            created_at=data.get("created_at"),
        )
