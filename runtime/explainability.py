"""
runtime/explainability.py — Phase Z26 Lightweight Explainability System
========================================================================
Gives operators high-level, readable explanations of WHY the system made
major execution decisions.

Exposes ONLY operational summaries — never hidden reasoning or raw chain-of-thought.

Decision types tracked:
  - model selection
  - retry
  - escalation
  - replanning
  - execution pause
  - tool rejection

FUTURE_RUNTIME_NOTE: If a richer causal graph is needed later, this module
should remain the single entry point for operator-facing explanations.
Never expose internal scoring internals directly to the UI.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("nexora.explainability")

# ── Decision types ────────────────────────────────────────────────────────────

class DecisionType:
    MODEL_SELECTION  = "model_selection"
    RETRY            = "retry"
    ESCALATION       = "escalation"
    REPLANNING       = "replanning"
    EXECUTION_PAUSE  = "execution_pause"
    TOOL_REJECTION   = "tool_rejection"
    CONTEXT_COMPRESS = "context_compression"
    PROVIDER_SWITCH  = "provider_switch"


# ── Data structure ────────────────────────────────────────────────────────────

@dataclass
class DecisionRecord:
    sid: str
    step_id: str
    decision_type: str
    summary: str                    # human-readable one-liner
    reason_category: str            # "performance", "safety", "budget", "error", "policy"
    contributing_factors: list[str] # short list of observable facts
    outcome: str                    # what actually happened as a result
    confidence_at_decision: float = 1.0
    ts: float = field(default_factory=time.time)
    record_id: str = ""

    def __post_init__(self):
        if not self.record_id:
            self.record_id = f"{self.sid[:8]}-{self.decision_type}-{int(self.ts*1000)%100000}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id":          self.record_id,
            "sid":                self.sid,
            "step_id":            self.step_id,
            "decision_type":      self.decision_type,
            "summary":            self.summary,
            "reason_category":    self.reason_category,
            "contributing_factors": self.contributing_factors,
            "outcome":            self.outcome,
            "confidence":         round(self.confidence_at_decision, 3),
            "ts":                 self.ts,
        }


# ── Registry ──────────────────────────────────────────────────────────────────

_records: list[DecisionRecord] = []
_records_lock = threading.Lock()
_MAX_RECORDS = 2000


def record_decision(
    sid: str,
    step_id: str,
    decision_type: str,
    summary: str,
    reason_category: str,
    contributing_factors: list[str],
    outcome: str,
    confidence: float = 1.0,
) -> DecisionRecord:
    """
    Record an operational decision for operator visibility.
    All parameters should describe OBSERVABLE facts only — no model internals.
    """
    rec = DecisionRecord(
        sid=sid,
        step_id=step_id,
        decision_type=decision_type,
        summary=summary,
        reason_category=reason_category,
        contributing_factors=contributing_factors,
        outcome=outcome,
        confidence_at_decision=confidence,
    )
    with _records_lock:
        _records.append(rec)
        if len(_records) > _MAX_RECORDS:
            _records.pop(0)

    logger.info("[Explain] %s | sid=%s | %s", decision_type, sid[:12], summary)
    return rec


# ── Convenience recorders ─────────────────────────────────────────────────────

def explain_model_selection(
    sid: str, step_id: str,
    model: str, reason: str,
    factors: list[str], confidence: float = 1.0,
):
    return record_decision(
        sid=sid, step_id=step_id,
        decision_type=DecisionType.MODEL_SELECTION,
        summary=f"Selected model '{model}': {reason}",
        reason_category="performance",
        contributing_factors=factors,
        outcome=f"Using {model} for this step",
        confidence=confidence,
    )


def explain_retry(
    sid: str, step_id: str,
    attempt: int, failure_reason: str,
    factors: list[str],
):
    return record_decision(
        sid=sid, step_id=step_id,
        decision_type=DecisionType.RETRY,
        summary=f"Retry attempt {attempt}: {failure_reason}",
        reason_category="error",
        contributing_factors=factors,
        outcome=f"Attempting step again (attempt {attempt})",
        confidence=max(0.1, 1.0 - attempt * 0.15),
    )


def explain_escalation(
    sid: str, step_id: str,
    trigger: str, factors: list[str],
    confidence: float = 0.0,
):
    return record_decision(
        sid=sid, step_id=step_id,
        decision_type=DecisionType.ESCALATION,
        summary=f"Escalated to operator: {trigger}",
        reason_category="safety",
        contributing_factors=factors,
        outcome="Execution paused — waiting for operator input",
        confidence=confidence,
    )


def explain_replanning(
    sid: str, step_id: str,
    original_plan_summary: str,
    reason: str, factors: list[str],
):
    return record_decision(
        sid=sid, step_id=step_id,
        decision_type=DecisionType.REPLANNING,
        summary=f"Replanning triggered: {reason}",
        reason_category="error",
        contributing_factors=factors,
        outcome=f"Original plan revised (was: {original_plan_summary[:80]})",
    )


def explain_execution_pause(
    sid: str, step_id: str,
    reason: str, factors: list[str],
):
    return record_decision(
        sid=sid, step_id=step_id,
        decision_type=DecisionType.EXECUTION_PAUSE,
        summary=f"Execution paused: {reason}",
        reason_category="policy",
        contributing_factors=factors,
        outcome="Execution halted until condition resolved",
    )


def explain_tool_rejection(
    sid: str, step_id: str,
    tool_name: str, reason: str,
    factors: list[str],
):
    return record_decision(
        sid=sid, step_id=step_id,
        decision_type=DecisionType.TOOL_REJECTION,
        summary=f"Tool '{tool_name}' rejected: {reason}",
        reason_category="safety",
        contributing_factors=factors,
        outcome=f"Tool call to '{tool_name}' was not executed",
    )


def explain_provider_switch(
    sid: str, step_id: str,
    from_provider: str, to_provider: str,
    reason: str, factors: list[str],
):
    return record_decision(
        sid=sid, step_id=step_id,
        decision_type=DecisionType.PROVIDER_SWITCH,
        summary=f"Switched from {from_provider} to {to_provider}: {reason}",
        reason_category="performance",
        contributing_factors=factors,
        outcome=f"Continuing with {to_provider}",
    )


def explain_context_compression(
    sid: str, step_id: str,
    messages_compressed: int, episode_index: int,
):
    return record_decision(
        sid=sid, step_id=step_id,
        decision_type=DecisionType.CONTEXT_COMPRESS,
        summary=f"Compressed {messages_compressed} messages into episode {episode_index}",
        reason_category="budget",
        contributing_factors=[
            f"{messages_compressed} messages exceeded active window",
            "token budget threshold reached",
        ],
        outcome=f"Episode {episode_index} summary stored; active window pruned",
    )


# ── Query API ─────────────────────────────────────────────────────────────────

def get_decisions(
    sid: str | None = None,
    decision_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Query the decision log. Filters by session and/or decision type.
    Returns operator-safe dicts only (no internal scoring details).
    """
    with _records_lock:
        filtered = [
            r for r in _records
            if (sid is None or r.sid == sid)
            and (decision_type is None or r.decision_type == decision_type)
        ]
    return [r.to_dict() for r in filtered[-limit:]]


def get_session_explanation_summary(sid: str) -> dict[str, Any]:
    """
    Return a high-level explanation summary for a session — suitable for
    displaying in the operator UI.
    """
    with _records_lock:
        session_records = [r for r in _records if r.sid == sid]

    if not session_records:
        return {"sid": sid, "decision_count": 0, "decisions": []}

    by_type: dict[str, int] = {}
    for r in session_records:
        by_type[r.decision_type] = by_type.get(r.decision_type, 0) + 1

    escalations = [r for r in session_records if r.decision_type == DecisionType.ESCALATION]
    retries     = [r for r in session_records if r.decision_type == DecisionType.RETRY]

    return {
        "sid":              sid,
        "decision_count":   len(session_records),
        "by_type":          by_type,
        "escalation_count": len(escalations),
        "retry_count":      len(retries),
        "last_decision":    session_records[-1].to_dict() if session_records else None,
    }


# ── Telemetry ─────────────────────────────────────────────────────────────────

def explainability_telemetry() -> dict[str, Any]:
    with _records_lock:
        total = len(_records)
        by_type: dict[str, int] = {}
        for r in _records:
            by_type[r.decision_type] = by_type.get(r.decision_type, 0) + 1
    return {
        "total_decisions":  total,
        "by_type":          by_type,
        "snapshot_ts":      time.time(),
    }
