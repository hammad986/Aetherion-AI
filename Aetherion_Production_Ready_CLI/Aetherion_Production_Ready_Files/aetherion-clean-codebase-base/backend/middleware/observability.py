"""
middleware/observability.py — Phase 40 / Workstream D: Observability & Telemetry Integration

Provides:
  - Correlation ID extraction & propagation (X-Correlation-ID, X-Request-ID, traceparent, X-Aetherion-Trace-Id)
  - RED metrics instrumentation (Rate, Errors, Duration) via Prometheus
  - Structured JSON logging in production with PII & secret redaction filter
  - Context propagation for tracing across web requests, worker dispatch, and runners
"""
import json
import logging
import os
import re
import sys
import time
import uuid
from typing import Optional

from flask import request, g, has_request_context

logger = logging.getLogger("aetherion.observability")

# ── Redaction & PII Filters ──────────────────────────────────────────────────
_REDACT_PATTERNS = (
    re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{6,}"),
    re.compile(r"(?i)((?:api[_-]?key|secret|token|password|authorization|jwt|passwd))\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{6,}"),
    re.compile(r"://([^:]+):([^@]+)@"),
    re.compile(r"(?i)(\b(?:sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{20,}|AIza[0-9A-Za-z-_]{35}))"),
)


def redact(text: Optional[str]) -> str:
    """Scrub sensitive credentials, tokens, and passwords from logs/metrics."""
    if not text:
        return text or ""
    out = str(text)
    for pat in _REDACT_PATTERNS:
        out = pat.sub(r"\1<redacted>", out)
    return out


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs sensitive strings from all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: redact(str(v)) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(redact(str(v)) if isinstance(v, str) else v for v in record.args)
        return True


class StructuredJsonFormatter(logging.Formatter):
    """Serializes log records to structured JSON with correlation context."""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }

        # Inject correlation ID if available
        cid = getattr(record, "correlation_id", None)
        if not cid and has_request_context():
            cid = getattr(g, "correlation_id", None) or getattr(request, "correlation_id", None)
        if cid:
            data["correlation_id"] = cid

        if has_request_context():
            data["http"] = {
                "method": request.method,
                "path": request.path,
                "remote_addr": request.remote_addr,
            }

        if record.exc_info:
            data["exception"] = redact(self.formatException(record.exc_info))

        return json.dumps(data)


# ── RED Prometheus Metrics ────────────────────────────────────────────────────
try:
    from prometheus_client import Counter, Histogram, Gauge
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False

if _PROMETHEUS_AVAILABLE:
    try:
        from observability import (
            http_requests_total,
            http_request_duration_seconds,
            worker_active_threads,
        )
    except Exception:
        http_requests_total = Counter(
            "http_requests_total",
            "Total HTTP requests",
            ["method", "route", "status"]
        )
        http_request_duration_seconds = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency in seconds",
            ["method", "route"]
        )
        worker_active_threads = Gauge(
            "worker_active_threads",
            "Number of active threads on this worker"
        )
    
    try:
        queue_depth = Gauge(
            "aetherion_queue_depth",
            "Current number of pending execution tasks in the queue"
        )
    except Exception:
        queue_depth = None
else:
    http_requests_total = None
    http_request_duration_seconds = None
    worker_active_threads = None
    queue_depth = None


def _is_production() -> bool:
    env = (
        os.getenv("ENVIRONMENT")
        or os.getenv("ENV")
        or os.getenv("APP_ENV")
        or os.getenv("FLASK_ENV")
        or ""
    ).strip().lower()
    return env in ("production", "staging", "prod")


def setup_observability(app):
    """Hooks into the Flask app lifecycle to add request tracing,

    timing, Prometheus RED metrics, and structured logging.
    """
    # 1. Install redacting filter on loggers
    redact_filter = RedactingFilter()
    app.logger.addFilter(redact_filter)
    logging.getLogger().addFilter(redact_filter)
    logger.addFilter(redact_filter)

    # 2. Configure structured JSON logging in production or when requested
    use_json_logs = _is_production() or os.getenv("LOG_FORMAT", "").strip().lower() == "json"
    if use_json_logs:
        for handler in app.logger.handlers or logging.getLogger().handlers:
            handler.setFormatter(StructuredJsonFormatter())

    @app.before_request
    def start_timer():
        g.start_time = time.time()
        
        # Correlation ID resolution: prioritize inbound traceparent, X-Correlation-ID, X-Request-ID
        cid = (
            request.headers.get("X-Correlation-ID")
            or request.headers.get("X-Request-ID")
            or request.headers.get("X-Aetherion-Trace-Id")
            or request.headers.get("traceparent")
            or f"aeth_{uuid.uuid4().hex[:16]}"
        )
        g.correlation_id = cid
        g.request_id = cid
        request.correlation_id = cid
        request._prom_start_time = g.start_time

        try:
            from observability.context import TraceContext
            view_args = request.view_args or {}
            sid = (
                request.headers.get("X-Aetherion-Session-Id")
                or view_args.get("sid")
                or view_args.get("session_id")
                or request.args.get("sid")
                or ""
            )
            g.trace_context = TraceContext(
                trace_id=cid,
                session_id=str(sid or ""),
                task_id=request.headers.get("X-Aetherion-Task-Id") or request.args.get("task_id", ""),
                execution_id=request.headers.get("X-Aetherion-Execution-Id") or request.args.get("execution_id", ""),
                worker_id=request.headers.get("X-Aetherion-Worker-Id") or "",
                workspace_id=request.headers.get("X-Aetherion-Workspace-Id") or str(sid or ""),
            )
        except Exception as e:
            logger.debug("trace context setup failed: %s", e)

        # Log incoming request (excluding noise assets)
        if not request.path.startswith("/static/"):
            logger.info(
                f"[REQ] {request.method} {request.path} | cid={g.correlation_id}",
                extra={"correlation_id": g.correlation_id}
            )

    @app.after_request
    def log_response(response):
        latency_sec = time.time() - getattr(g, "start_time", time.time())
        exec_ms = round(latency_sec * 1000, 2)
        status = response.status_code

        # Propagate tracing & correlation headers
        cid = getattr(g, "correlation_id", "unknown")
        response.headers["X-Correlation-ID"] = cid
        response.headers["X-Request-Id"] = cid
        response.headers["X-Execution-Time-Ms"] = str(exec_ms)

        if hasattr(g, "trace_context"):
            ctx = g.trace_context
            response.headers["X-Aetherion-Trace-Id"] = ctx.trace_id
            if ctx.session_id:
                response.headers["X-Aetherion-Session-Id"] = ctx.session_id
            if ctx.task_id:
                response.headers["X-Aetherion-Task-Id"] = ctx.task_id
            if ctx.execution_id:
                response.headers["X-Aetherion-Execution-Id"] = ctx.execution_id

        # Record RED metrics
        if _PROMETHEUS_AVAILABLE and http_requests_total and http_request_duration_seconds:
            if not request.path.startswith("/static/") and not request.path.startswith("/api/pty"):
                route = request.endpoint or request.path
                try:
                    http_requests_total.labels(
                        method=request.method,
                        route=route,
                        status=str(status)
                    ).inc()
                    http_request_duration_seconds.labels(
                        method=request.method,
                        route=route
                    ).observe(latency_sec)
                except Exception:
                    pass

        # Structured response log
        if not request.path.startswith("/static/"):
            log_msg = f"[RES] {request.method} {request.path} | status={status} | ms={exec_ms} | cid={cid}"
            extra = {"correlation_id": cid}
            if status >= 500:
                logger.error(log_msg, extra=extra)
            elif status >= 400:
                logger.warning(log_msg, extra=extra)
            else:
                logger.info(log_msg, extra=extra)

        return response

    @app.errorhandler(Exception)
    def handle_unhandled_exception(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e

        cid = getattr(g, "correlation_id", "unknown")
        logger.exception(
            f"[ERROR] Unhandled exception in {request.path}: {redact(str(e))} | cid={cid}",
            extra={"correlation_id": cid}
        )
        try:
            from middleware.api_response import error_response
            return error_response(
                message="An unexpected internal error occurred.",
                code="INTERNAL_SERVER_ERROR",
                status=500
            )
        except ImportError:
            return {"error": "Internal Server Error", "correlation_id": cid}, 500
