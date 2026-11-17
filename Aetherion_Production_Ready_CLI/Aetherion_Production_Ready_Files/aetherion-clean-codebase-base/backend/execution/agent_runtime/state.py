"""
backend/execution/agent_runtime/state.py — Lifecycle and verification state definitions.
"""
from __future__ import annotations

from .. import durable_state
from ..verification.engine import (
    NOT_VERIFIED,
    PARTIALLY_VERIFIED,
    VERIFICATION_FAILED,
    VERIFIED,
)

# ── Lifecycle States ──────────────────────────────────────────────────────────
STATE_QUEUED = durable_state.STATUS_QUEUED
STATE_PLANNING = durable_state.STATUS_PLANNING
STATE_INSPECTING = durable_state.STATUS_INSPECTING
STATE_EXECUTING = durable_state.STATUS_EXECUTING
STATE_WAITING_FOR_APPROVAL = durable_state.STATUS_WAITING_FOR_APPROVAL
STATE_VERIFYING = durable_state.STATUS_VERIFYING
STATE_REPAIRING = durable_state.STATUS_REPAIRING
STATE_COMPLETED = durable_state.STATUS_COMPLETED
STATE_SUCCEEDED = durable_state.STATUS_SUCCEEDED
STATE_FAILED = durable_state.STATUS_FAILED
STATE_CANCELLED = durable_state.STATUS_CANCELLED
STATE_REVIEW_PENDING = durable_state.STATUS_REVIEW_PENDING
STATE_ACCEPTED = durable_state.STATUS_ACCEPTED
STATE_REJECTED = durable_state.STATUS_REJECTED

__all__ = [
    "STATE_QUEUED",
    "STATE_PLANNING",
    "STATE_INSPECTING",
    "STATE_EXECUTING",
    "STATE_WAITING_FOR_APPROVAL",
    "STATE_VERIFYING",
    "STATE_REPAIRING",
    "STATE_COMPLETED",
    "STATE_SUCCEEDED",
    "STATE_FAILED",
    "STATE_CANCELLED",
    "STATE_REVIEW_PENDING",
    "STATE_ACCEPTED",
    "STATE_REJECTED",
    "VERIFIED",
    "PARTIALLY_VERIFIED",
    "NOT_VERIFIED",
    "VERIFICATION_FAILED",
]
