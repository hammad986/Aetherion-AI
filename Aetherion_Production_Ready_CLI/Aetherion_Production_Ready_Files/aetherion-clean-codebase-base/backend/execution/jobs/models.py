"""
backend/execution/jobs/models.py — Background Job Data Models and Dynamic Duration Calculations.
"""
from __future__ import annotations

import dataclasses
import enum
import json
import time
import uuid
from typing import Any, Dict, List, Optional


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_JOB_STATUSES = frozenset({
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
})


@dataclasses.dataclass
class JobAttempt:
    id: Optional[int] = None
    job_id: str = ""
    owner_id: str = ""
    attempt_number: int = 1
    status: str = JobStatus.QUEUED.value
    started_at: float = dataclasses.field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "owner_id": self.owner_id,
            "attempt_number": self.attempt_number,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result": self.result,
        }


@dataclasses.dataclass
class BackgroundJob:
    id: str = dataclasses.field(default_factory=lambda: f"job_{uuid.uuid4().hex[:12]}")
    owner_id: str = ""
    name: str = "Untitled Job"
    status: str = JobStatus.QUEUED.value
    task: str = ""
    workspace_id: str = "default_workspace"
    session_id: Optional[str] = None
    created_at: float = dataclasses.field(default_factory=time.time)
    started_at: Optional[float] = None
    updated_at: float = dataclasses.field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    attempt_number: int = 1
    previous_error: Optional[str] = None

    def validate(self) -> None:
        if not self.id or not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Job id cannot be empty.")
        if not isinstance(self.owner_id, str) or not self.owner_id.strip():
            raise ValueError("Job owner_id cannot be empty.")
        if not self.name or not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Job name cannot be empty.")
        if not self.task or not isinstance(self.task, str) or not self.task.strip():
            raise ValueError("Job task cannot be empty.")
        if self.status not in {s.value for s in JobStatus}:
            raise ValueError(f"Invalid JobStatus: '{self.status}'.")

    @property
    def elapsed_seconds(self) -> float:
        """Dynamic elapsed duration in seconds calculated from timestamps."""
        if not self.started_at:
            return 0.0
        end_time = self.finished_at if (self.finished_at and self.status in TERMINAL_JOB_STATUSES) else time.time()
        return max(0.0, end_time - self.started_at)

    @property
    def elapsed_minutes(self) -> str:
        """Human-readable elapsed duration (e.g. '17 min' or '45 sec')."""
        sec = self.elapsed_seconds
        if sec < 60.0:
            return f"{int(sec)} sec"
        minutes = int(sec // 60)
        return f"{minutes} min"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "name": self.name,
            "status": self.status,
            "task": self.task,
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "metadata": self.metadata,
            "result": self.result,
            "attempt_number": self.attempt_number,
            "previous_error": self.previous_error,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "elapsed_minutes": self.elapsed_minutes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BackgroundJob:
        return cls(
            id=data.get("id") or f"job_{uuid.uuid4().hex[:12]}",
            owner_id=data.get("owner_id", ""),
            name=data.get("name", "Untitled Job"),
            status=data.get("status", JobStatus.QUEUED.value),
            task=data.get("task", ""),
            workspace_id=data.get("workspace_id", "default_workspace"),
            session_id=data.get("session_id"),
            created_at=float(data.get("created_at") or time.time()),
            started_at=float(data["started_at"]) if data.get("started_at") is not None else None,
            updated_at=float(data.get("updated_at") or time.time()),
            finished_at=float(data["finished_at"]) if data.get("finished_at") is not None else None,
            error=data.get("error"),
            metadata=data.get("metadata") or {},
            result=data.get("result"),
            attempt_number=int(data.get("attempt_number", 1)),
            previous_error=data.get("previous_error"),
        )
