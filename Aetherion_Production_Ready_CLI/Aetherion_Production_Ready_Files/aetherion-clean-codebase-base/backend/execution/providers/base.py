"""
backend/execution/providers/base.py — Canonical provider abstraction and contract types.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional


class ProviderUnavailableException(Exception):
    """Raised when a required LLM provider is not reachable or configured."""


@dataclasses.dataclass
class ToolCall:
    """Normalized tool call emitted by a model."""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclasses.dataclass
class ProviderCapabilities:
    """Feature capabilities advertised by a provider adapter."""
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_system_messages: bool = True
    supports_json: bool = True
    supports_vision: bool = False
    supports_parallel_tools: bool = False


@dataclasses.dataclass
class ModelResponse:
    """Normalized response produced by any model provider.

    Provides dict-like backward-compatibility so existing runtime loops can access
    resp['thought'], resp['tool'], resp['arguments'], resp['tool_call_id'] seamlessly.
    """
    text: str = ""
    tool_calls: List[ToolCall] = dataclasses.field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: Dict[str, Any] = dataclasses.field(default_factory=dict)
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        if key == "thought":
            return self.text
        if key == "tool":
            return self.tool_calls[0].name if self.tool_calls else None
        if key == "arguments":
            return self.tool_calls[0].arguments if self.tool_calls else {}
        if key == "tool_call_id":
            return self.tool_calls[0].id if self.tool_calls else ""
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            val = self[key]
            return val if val is not None else default
        except KeyError:
            return default

    def __contains__(self, key: str) -> bool:
        return key in {
            "thought",
            "tool",
            "arguments",
            "tool_call_id",
            "text",
            "tool_calls",
            "finish_reason",
            "usage",
            "metadata",
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "thought": self.text,
            "tool": self.tool_calls[0].name if self.tool_calls else None,
            "arguments": self.tool_calls[0].arguments if self.tool_calls else {},
            "tool_call_id": self.tool_calls[0].id if self.tool_calls else "",
            "text": self.text,
            "tool_calls": [dataclasses.asdict(tc) for tc in self.tool_calls],
            "finish_reason": self.finish_reason,
            "usage": self.usage,
            "metadata": self.metadata,
        }


class BaseModelProvider:
    """Logical model provider interface."""

    def name(self) -> str:
        return "base"

    def is_available(self) -> bool:
        return False

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ModelResponse | Dict[str, Any]:
        """Produce next action: text and/or tool calls."""
        raise NotImplementedError

    def health_check(self) -> Dict[str, Any]:
        """Execute diagnostic health check.

        Returns:
            {
                "status": "CONNECTED" | "AUTH_FAILED" | "NOT_FOUND" | "TIMEOUT" | "NETWORK_ERROR" | "INVALID_CONFIGURATION" | "UNSUPPORTED_PROTOCOL",
                "latency_ms": float,
                "diagnostics": str,
            }
        """
        return {
            "status": "CONNECTED" if self.is_available() else "INVALID_CONFIGURATION",
            "latency_ms": 0.0,
            "diagnostics": "Provider base health check.",
        }
