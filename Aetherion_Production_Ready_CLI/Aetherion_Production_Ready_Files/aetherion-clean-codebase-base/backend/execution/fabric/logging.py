"""
backend/execution/fabric/logging.py — Phase 23 Structured JSON Logging & Secret Scrubbing.

Provides FabricStructuredLogger:
- Standardized JSON log emission with correlation IDs:
    task_id, tenant_id, step_id, worker_id, attempt, plan_revision
- Automatic context propagation via correlation_context()
- Recursive deep secret scrubbing (API keys, JWT/bearer tokens, passwords, DSNs)
- Dual output: integration with standard Python logging and in-memory trace buffers
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime
import json
import logging
import os
import re
import sys
import threading
from typing import Any, Dict, Iterator, List, Optional, Union

try:
    from core.preflight import sanitize_dsn
except ImportError:
    try:
        from backend.core.preflight import sanitize_dsn
    except ImportError:
        def sanitize_dsn(t: str) -> str:
            return re.sub(r"://([^:]+):([^@]+)@", r"://\1:[REDACTED]@", t)

try:
    from execution.orchestration.engineering_memory import scrub_secrets
except ImportError:
    try:
        from backend.execution.orchestration.engineering_memory import scrub_secrets
    except ImportError:
        def scrub_secrets(t: str) -> str:
            return re.sub(r"(sk-[a-zA-Z0-9_\-]{20,})", "[REDACTED_API_KEY]", t)

# ContextVar for async/thread context propagation
_CORRELATION_CTX: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "_CORRELATION_CTX",
    default={},
)


def deep_scrub(obj: Any) -> Any:
    """
    Recursively scrub sensitive keys and credential patterns from arbitrary objects.
    Handles strings, dicts, lists, tuples, and primitives.
    """
    if obj is None:
        return None
    if isinstance(obj, str):
        # Scrub DSNs first, then API keys / tokens / passwords
        s = sanitize_dsn(obj)
        return scrub_secrets(s)
    if isinstance(obj, dict):
        scrubbed = {}
        for k, v in obj.items():
            k_lower = str(k).lower()
            if any(term in k_lower for term in ("password", "secret", "token", "api_key", "authorization", "keyring")):
                scrubbed[k] = "[REDACTED]"
            else:
                scrubbed[k] = deep_scrub(v)
        return scrubbed
    if isinstance(obj, list):
        return [deep_scrub(elem) for elem in obj]
    if isinstance(obj, tuple):
        return tuple(deep_scrub(elem) for elem in obj)
    if isinstance(obj, (int, float, bool)):
        return obj
    # Fallback to string representation and scrub
    return scrub_secrets(str(obj))


@contextlib.contextmanager
def correlation_context(
    task_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    step_id: Optional[str] = None,
    worker_id: Optional[str] = None,
    attempt: Optional[int] = None,
    plan_revision: Optional[int] = None,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Context manager to attach correlation IDs to all logs executed within."""
    current = dict(_CORRELATION_CTX.get())
    updates = {
        "task_id": task_id,
        "tenant_id": tenant_id,
        "step_id": step_id,
        "worker_id": worker_id,
        "attempt": attempt,
        "plan_revision": plan_revision,
    }
    for k, v in kwargs.items():
        if v is not None:
            updates[k] = v

    for k, v in updates.items():
        if v is not None:
            current[k] = v

    token = _CORRELATION_CTX.set(current)
    try:
        yield current
    finally:
        _CORRELATION_CTX.reset(token)


class FabricStructuredLogger:
    """
    Structured JSON logger emitting correlated, secret-scrubbed records.
    """

    def __init__(
        self,
        name: str = "aetherion.fabric",
        buffer_size: int = 500,
    ) -> None:
        self.name = name
        self._raw_logger = logging.getLogger(name)
        self._buffer_size = buffer_size
        self._buffer: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def _build_record(
        self,
        level: str,
        message: str,
        event: Optional[str] = None,
        correlation_override: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Construct, correlate, and sanitize a structured log record."""
        # 1. Base correlation context
        ctx = dict(_CORRELATION_CTX.get())
        if correlation_override:
            ctx.update(correlation_override)

        # 2. Base payload
        merged_payload = dict(payload or {})

        # 3. Assemble raw record
        raw_record = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "logger": self.name,
            "level": level.upper(),
            "event": event or f"FABRIC_{level.upper()}",
            "message": message,
            "correlation": ctx,
            "payload": merged_payload,
        }

        # 4. Deep scrub all fields to guarantee zero secret leakage
        sanitized = deep_scrub(raw_record)
        return sanitized

    def _emit(self, record: Dict[str, Any]) -> None:
        """Store in memory buffer and forward to Python logging."""
        with self._lock:
            self._buffer.append(record)
            if len(self._buffer) > self._buffer_size:
                self._buffer.pop(0)

        # Forward formatted JSON string to python logger
        try:
            line = json.dumps(record, default=str)
            lvl = getattr(logging, record["level"], logging.INFO)
            self._raw_logger.log(lvl, line)
        except Exception:
            pass

    def debug(self, message: str, event: Optional[str] = None, **payload: Any) -> Dict[str, Any]:
        rec = self._build_record("DEBUG", message, event=event, payload=payload)
        self._emit(rec)
        return rec

    def info(self, message: str, event: Optional[str] = None, **payload: Any) -> Dict[str, Any]:
        rec = self._build_record("INFO", message, event=event, payload=payload)
        self._emit(rec)
        return rec

    def warning(self, message: str, event: Optional[str] = None, **payload: Any) -> Dict[str, Any]:
        rec = self._build_record("WARNING", message, event=event, payload=payload)
        self._emit(rec)
        return rec

    def error(self, message: str, event: Optional[str] = None, **payload: Any) -> Dict[str, Any]:
        rec = self._build_record("ERROR", message, event=event, payload=payload)
        self._emit(rec)
        return rec

    def critical(self, message: str, event: Optional[str] = None, **payload: Any) -> Dict[str, Any]:
        rec = self._build_record("CRITICAL", message, event=event, payload=payload)
        self._emit(rec)
        return rec

    def log_event(
        self,
        event: str,
        message: str,
        level: str = "INFO",
        correlation_override: Optional[Dict[str, Any]] = None,
        **payload: Any,
    ) -> Dict[str, Any]:
        """Explicit business event logging."""
        rec = self._build_record(
            level=level,
            message=message,
            event=event,
            correlation_override=correlation_override,
            payload=payload,
        )
        self._emit(rec)
        return rec

    def get_recent_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve copy of recent structured logs."""
        with self._lock:
            return list(self._buffer[-limit:])

    def clear_logs(self) -> None:
        """Clear memory trace buffer."""
        with self._lock:
            self._buffer.clear()


# ── Global Logger Singleton ──────────────────────────────────────────────────

_LOGGERS: Dict[str, FabricStructuredLogger] = {}
_LOGGER_LOCK = threading.Lock()


def get_structured_logger(name: str = "aetherion.fabric") -> FabricStructuredLogger:
    """Retrieve or create structured logger instance."""
    with _LOGGER_LOCK:
        if name not in _LOGGERS:
            _LOGGERS[name] = FabricStructuredLogger(name=name)
        return _LOGGERS[name]
