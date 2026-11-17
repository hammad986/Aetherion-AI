"""
backend/execution/sessions/models.py — Durable Session and SessionMessage Data Models.
"""
from __future__ import annotations

import dataclasses
import enum
import json
import time
import uuid
from typing import Any, Dict, List, Optional


class SessionStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"


@dataclasses.dataclass
class SessionMessage:
    id: str = dataclasses.field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    session_id: str = ""
    owner_id: str = ""
    role: str = "user"  # user, assistant, system, tool
    content: str = ""
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_result: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)
    created_at: float = dataclasses.field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "owner_id": self.owner_id,
            "role": self.role,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "tool_result": self.tool_result,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SessionMessage:
        return cls(
            id=data.get("id") or f"msg_{uuid.uuid4().hex[:12]}",
            session_id=data.get("session_id", ""),
            owner_id=data.get("owner_id", ""),
            role=data.get("role", "user"),
            content=data.get("content", ""),
            tool_name=data.get("tool_name"),
            tool_call_id=data.get("tool_call_id"),
            tool_result=data.get("tool_result"),
            metadata=data.get("metadata") or {},
            created_at=float(data.get("created_at") or time.time()),
        )


@dataclasses.dataclass
class Session:
    id: str = dataclasses.field(default_factory=lambda: f"sess_{uuid.uuid4().hex[:12]}")
    owner_id: str = ""
    title: str = "Untitled Session"
    status: str = SessionStatus.ACTIVE.value
    model: str = "claude-3-5-sonnet"
    provider_id: str = "anthropic"
    workspace_id: str = "default_workspace"
    summary: str = ""
    message_count: int = 0
    token_usage: int = 0
    files_changed: int = 0
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)
    created_at: float = dataclasses.field(default_factory=time.time)
    updated_at: float = dataclasses.field(default_factory=time.time)
    last_activity_at: float = dataclasses.field(default_factory=time.time)
    archived_at: Optional[float] = None

    def validate(self) -> None:
        if not self.id or not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Session id cannot be empty.")
        if not isinstance(self.owner_id, str) or not self.owner_id.strip():
            raise ValueError("Session owner_id cannot be empty.")
        if not self.title or not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("Session title cannot be empty.")
        if self.status not in {s.value for s in SessionStatus}:
            raise ValueError(f"Invalid Session status: {self.status}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "title": self.title,
            "status": self.status,
            "model": self.model,
            "provider_id": self.provider_id,
            "workspace_id": self.workspace_id,
            "summary": self.summary,
            "message_count": self.message_count,
            "token_usage": self.token_usage,
            "files_changed": self.files_changed,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_activity_at": self.last_activity_at,
            "archived_at": self.archived_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Session:
        return cls(
            id=data.get("id") or f"sess_{uuid.uuid4().hex[:12]}",
            owner_id=data.get("owner_id", ""),
            title=data.get("title", "Untitled Session"),
            status=data.get("status", SessionStatus.ACTIVE.value),
            model=data.get("model", "claude-3-5-sonnet"),
            provider_id=data.get("provider_id", "anthropic"),
            workspace_id=data.get("workspace_id", "default_workspace"),
            summary=data.get("summary", ""),
            message_count=int(data.get("message_count", 0)),
            token_usage=int(data.get("token_usage", 0)),
            files_changed=int(data.get("files_changed", 0)),
            metadata=data.get("metadata") or {},
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            last_activity_at=float(data.get("last_activity_at") or time.time()),
            archived_at=float(data["archived_at"]) if data.get("archived_at") is not None else None,
        )
