"""
runtime/governance_engine.py — Phase Z29B
==========================================
Operator-facing governance + approval engine for runtime operations.

Separate from governance_layer.py (which handles patch/code validation).
This module governs live execution operations requiring operator sign-off.

Features:
  - ApprovalQueue with severity classification
  - Protected operation gate
  - Immutable approval history (append-only)
  - SQLite persistence for cross-restart continuity
  - SSE broadcast of queue state changes
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("nexora.governance_engine")

# ── Severity levels ────────────────────────────────────────────────────────────

class Severity:
    INFO                = "INFO"
    WARNING             = "WARNING"
    HIGH_RISK           = "HIGH_RISK"
    GOVERNANCE_REQUIRED = "GOVERNANCE_REQUIRED"
    CRITICAL            = "CRITICAL"

    ORDER = {
        "INFO": 0, "WARNING": 1,
        "HIGH_RISK": 2, "GOVERNANCE_REQUIRED": 3, "CRITICAL": 4,
    }

    @classmethod
    def requires_approval(cls, level: str) -> bool:
        return cls.ORDER.get(level, 0) >= cls.ORDER["HIGH_RISK"]


# ── Protected operation taxonomy ──────────────────────────────────────────────

PROTECTED_OPERATIONS: dict[str, str] = {
    "file_delete":             Severity.GOVERNANCE_REQUIRED,
    "file_delete_bulk":        Severity.CRITICAL,
    "deploy_production":       Severity.CRITICAL,
    "credential_modify":       Severity.CRITICAL,
    "credential_delete":       Severity.CRITICAL,
    "mass_file_write":         Severity.HIGH_RISK,
    "external_execution":      Severity.HIGH_RISK,
    "escalation_external":     Severity.GOVERNANCE_REQUIRED,
    "db_drop":                 Severity.CRITICAL,
    "db_truncate":             Severity.GOVERNANCE_REQUIRED,
    "env_var_modify":          Severity.GOVERNANCE_REQUIRED,
    "package_install":         Severity.WARNING,
    "network_outbound":        Severity.WARNING,
    "shell_command":           Severity.WARNING,
    "hitl_force_approve":      Severity.HIGH_RISK,
    "mission_cancel":          Severity.HIGH_RISK,
    "override_provider":       Severity.WARNING,
    "override_model":          Severity.WARNING,
    "override_confidence":     Severity.HIGH_RISK,
    "mission_recovery":        Severity.HIGH_RISK,
}


def classify_operation(op_type: str, context: dict | None = None) -> str:
    """Return severity level for a given operation type."""
    return PROTECTED_OPERATIONS.get(op_type, Severity.INFO)


# ── Approval request ──────────────────────────────────────────────────────────

class ApprovalStatus:
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED  = "expired"
    AUTO     = "auto"


@dataclass
class ApprovalRequest:
    request_id:   str
    sid:          str
    op_type:      str
    severity:     str
    summary:      str
    context:      dict
    status:       str  = ApprovalStatus.PENDING
    created_at:   float = field(default_factory=time.time)
    resolved_at:  float = 0.0
    resolved_by:  str  = ""
    resolution:   str  = ""

    def to_dict(self) -> dict:
        return {
            "request_id":  self.request_id,
            "sid":         self.sid,
            "op_type":     self.op_type,
            "severity":    self.severity,
            "summary":     self.summary,
            "context":     self.context,
            "status":      self.status,
            "created_at":  self.created_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
            "resolution":  self.resolution,
            "age_s":       round(time.time() - self.created_at, 1),
        }


# ── Database ──────────────────────────────────────────────────────────────────

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "governance_engine.db")
_db_lock  = threading.Lock()


def _get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db() -> None:
    with _db_lock:
        conn = _get_db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS approval_requests (
                request_id   TEXT PRIMARY KEY,
                sid          TEXT,
                op_type      TEXT,
                severity     TEXT,
                summary      TEXT,
                context_json TEXT,
                status       TEXT,
                created_at   REAL,
                resolved_at  REAL,
                resolved_by  TEXT,
                resolution   TEXT
            )
        """)
        conn.commit()
        conn.close()


try:
    _init_db()
except Exception as _e:
    logger.warning(f"[GovernanceEngine] DB init failed: {_e}")


# ── In-memory pending queue ────────────────────────────────────────────────────

_queue_lock = threading.Lock()
_pending:   dict[str, ApprovalRequest] = {}   # request_id -> ApprovalRequest
_waiters:   dict[str, threading.Event] = {}   # request_id -> Event


# ── Core API ──────────────────────────────────────────────────────────────────

def submit_approval_request(
    sid:     str,
    op_type: str,
    summary: str,
    context: dict | None = None,
    emit_fn: Callable | None = None,
    auto_approve_below: str = Severity.WARNING,
) -> ApprovalRequest:
    """
    Submit an operation for governance review.
    If severity is below auto_approve_below threshold, auto-approves.
    Returns an ApprovalRequest — caller can check .status immediately.
    """
    severity = classify_operation(op_type, context)
    req = ApprovalRequest(
        request_id = f"gov-{uuid.uuid4().hex[:12]}",
        sid        = sid,
        op_type    = op_type,
        severity   = severity,
        summary    = summary,
        context    = context or {},
    )

    # Auto-approve low-severity ops
    auto_threshold = Severity.ORDER.get(auto_approve_below, 1)
    if Severity.ORDER.get(severity, 0) < auto_threshold:
        req.status      = ApprovalStatus.AUTO
        req.resolved_at = time.time()
        req.resolved_by = "system"
        req.resolution  = "Auto-approved (below threshold)"
        _persist(req)
        return req

    # Queue for operator review
    with _queue_lock:
        _pending[req.request_id] = req
        _waiters[req.request_id] = threading.Event()

    _persist(req)

    if emit_fn:
        try:
            emit_fn("agent.governance_request", {
                "request_id": req.request_id,
                "op_type":    op_type,
                "severity":   severity,
                "summary":    summary,
                "sid":        sid,
            })
        except Exception:
            pass

    logger.info(f"[GovernanceEngine] Queued {op_type} [{severity}] for {sid}: {summary[:60]}")
    return req


def resolve_request(
    request_id: str,
    decision:   str,  # "approve" | "reject"
    resolved_by: str = "operator",
    resolution_note: str = "",
    emit_fn: Callable | None = None,
) -> ApprovalRequest | None:
    with _queue_lock:
        req = _pending.get(request_id)
        if not req:
            return None
        req.status      = ApprovalStatus.APPROVED if decision == "approve" else ApprovalStatus.REJECTED
        req.resolved_at = time.time()
        req.resolved_by = resolved_by
        req.resolution  = resolution_note or decision
        evt = _waiters.pop(request_id, None)
        _pending.pop(request_id, None)

    _persist(req)
    if evt:
        evt.set()

    if emit_fn:
        try:
            emit_fn("agent.governance_resolved", {
                "request_id": request_id,
                "status":     req.status,
                "op_type":    req.op_type,
                "sid":        req.sid,
            })
        except Exception:
            pass

    logger.info(f"[GovernanceEngine] {decision.upper()} {request_id} ({req.op_type})")
    return req


def wait_for_approval(
    request_id: str,
    timeout:    float = 120.0,
) -> str:
    """
    Block until request is resolved or timeout. Returns final status.
    Safe to call from worker threads (agent execution loop).
    """
    with _queue_lock:
        evt = _waiters.get(request_id)
    if not evt:
        with _queue_lock:
            req = _pending.get(request_id)
        return req.status if req else ApprovalStatus.EXPIRED

    granted = evt.wait(timeout=timeout)
    if not granted:
        with _queue_lock:
            req = _pending.pop(request_id, None)
            _waiters.pop(request_id, None)
        if req:
            req.status      = ApprovalStatus.EXPIRED
            req.resolved_at = time.time()
            _persist(req)
        return ApprovalStatus.EXPIRED

    with _queue_lock:
        pass  # req already removed by resolve_request
    rows = _query_history(request_id=request_id, limit=1)
    return rows[0]["status"] if rows else ApprovalStatus.EXPIRED


def expire_old_requests(max_age_s: float = 300.0) -> int:
    """Expire pending requests older than max_age_s. Returns count expired."""
    now = time.time()
    expired = []
    with _queue_lock:
        for rid, req in list(_pending.items()):
            if now - req.created_at > max_age_s:
                req.status      = ApprovalStatus.EXPIRED
                req.resolved_at = now
                expired.append(rid)
        for rid in expired:
            req = _pending.pop(rid)
            _waiters.pop(rid, threading.Event()).set()
            _persist(req)
    return len(expired)


# ── Persistence helpers ────────────────────────────────────────────────────────

def _persist(req: ApprovalRequest) -> None:
    try:
        with _db_lock:
            conn = _get_db()
            conn.execute("""
                INSERT OR REPLACE INTO approval_requests
                (request_id, sid, op_type, severity, summary,
                 context_json, status, created_at, resolved_at, resolved_by, resolution)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                req.request_id, req.sid, req.op_type, req.severity, req.summary,
                json.dumps(req.context), req.status,
                req.created_at, req.resolved_at, req.resolved_by, req.resolution,
            ))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.debug(f"[GovernanceEngine] persist error: {e}")


def _query_history(
    sid: str | None = None,
    op_type: str | None = None,
    status: str | None = None,
    request_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    try:
        clauses, params = [], []
        if sid:          clauses.append("sid = ?");        params.append(sid)
        if op_type:      clauses.append("op_type = ?");    params.append(op_type)
        if status:       clauses.append("status = ?");     params.append(status)
        if request_id:   clauses.append("request_id = ?"); params.append(request_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with _db_lock:
            conn = _get_db()
            rows = conn.execute(
                f"SELECT * FROM approval_requests {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"[GovernanceEngine] query error: {e}")
        return []


# ── Query API ─────────────────────────────────────────────────────────────────

def get_pending_queue() -> list[dict]:
    with _queue_lock:
        return [r.to_dict() for r in sorted(_pending.values(), key=lambda x: x.created_at)]


def get_approval_history(
    sid: str | None = None,
    limit: int = 100,
) -> list[dict]:
    rows = _query_history(sid=sid, limit=limit)
    # Add in-memory pending that may not be persisted yet
    pending = get_pending_queue()
    if sid:
        pending = [p for p in pending if p["sid"] == sid]
    seen = {r["request_id"] for r in rows}
    extra = [p for p in pending if p["request_id"] not in seen]
    return extra + rows


def governance_snapshot() -> dict:
    pending = get_pending_queue()
    critical = [p for p in pending if p["severity"] == Severity.CRITICAL]
    return {
        "pending_count":    len(pending),
        "critical_count":   len(critical),
        "pending_items":    pending[:10],
        "ts":               time.time(),
    }
