"""
backend/execution/extensions/protocol.py — Structured IPC protocol for isolated extension execution.
"""
from __future__ import annotations

import dataclasses
import json
import uuid
from typing import Any, Dict, List, Optional


@dataclasses.dataclass
class ExtensionExecutionRequest:
    """Structured request payload sent to the isolated extension runner/worker."""
    request_id: str = dataclasses.field(default_factory=lambda: f"req_{uuid.uuid4().hex[:12]}")
    plugin_id: str = ""
    entrypoint: str = "main.py:run"
    tool_name: str = ""
    arguments: Dict[str, Any] = dataclasses.field(default_factory=dict)
    context: Dict[str, Any] = dataclasses.field(default_factory=dict)
    timeout_seconds: float = 30.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "plugin_id": self.plugin_id,
            "entrypoint": self.entrypoint,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "context": self.context,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExtensionExecutionRequest:
        return cls(
            request_id=data.get("request_id") or f"req_{uuid.uuid4().hex[:12]}",
            plugin_id=data.get("plugin_id", ""),
            entrypoint=data.get("entrypoint", "main.py:run"),
            tool_name=data.get("tool_name", ""),
            arguments=data.get("arguments") or {},
            context=data.get("context") or {},
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> ExtensionExecutionRequest:
        return cls.from_dict(json.loads(json_str))


@dataclasses.dataclass
class ExtensionExecutionResponse:
    """Structured response payload returned by the isolated extension runner/worker."""
    request_id: str = ""
    ok: bool = True
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    data: Optional[Any] = None
    duration_ms: float = 0.0
    security_event: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "data": self.data,
            "duration_ms": self.duration_ms,
            "security_event": self.security_event,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExtensionExecutionResponse:
        return cls(
            request_id=data.get("request_id", ""),
            ok=bool(data.get("ok", True)),
            exit_code=int(data.get("exit_code", 0)),
            stdout=str(data.get("stdout") or ""),
            stderr=str(data.get("stderr") or ""),
            error=data.get("error"),
            data=data.get("data"),
            duration_ms=float(data.get("duration_ms", 0.0)),
            security_event=bool(data.get("security_event", False)),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> ExtensionExecutionResponse:
        return cls.from_dict(json.loads(json_str))
