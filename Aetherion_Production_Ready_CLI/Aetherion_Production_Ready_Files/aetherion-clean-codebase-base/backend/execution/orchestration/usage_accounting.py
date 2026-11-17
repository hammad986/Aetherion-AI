"""
backend/execution/orchestration/usage_accounting.py — Structured Usage Accounting.

Records per-turn and aggregate usage for model calls, tool calls, tokens,
repair attempts, escalations, and elapsed time.

Unavailable metrics (e.g., actual token counts from providers that don't report them)
are recorded as None — they are NEVER fabricated.

The UsageAccounting object is attached to the task result and replay events so
operators can see which profile was selected, why, and what resources were consumed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TurnUsage:
    """Usage for a single model call (one iteration of the agent loop)."""
    turn_index: int
    role: str                          # ModelRole.value
    profile_name: str                  # ModelProfile.name
    model_id: str
    tokens_in: Optional[int] = None    # None if provider does not report
    tokens_out: Optional[int] = None   # None if provider does not report
    elapsed_sec: Optional[float] = None
    tool_calls: int = 0
    was_repair: bool = False
    was_escalation: bool = False
    provider_name: str = "default"
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "turn_index": self.turn_index,
            "role": self.role,
            "profile_name": self.profile_name,
            "model_id": self.model_id,
            "tokens_in": self.tokens_in,      # May be None — not fabricated
            "tokens_out": self.tokens_out,     # May be None — not fabricated
            "elapsed_sec": self.elapsed_sec,
            "tool_calls": self.tool_calls,
            "was_repair": self.was_repair,
            "was_escalation": self.was_escalation,
            "provider_name": self.provider_name,
            "timestamp": self.timestamp,
        }


@dataclass
class UsageAccounting:
    """
    Aggregate usage accounting for a task execution.

    Accumulates per-turn records and exposes totals.
    Token counts are summed only when reported by the provider (not fabricated).
    """
    task_id: str
    tenant_id: str
    initial_profile_name: str          # Profile selected at policy derivation time
    initial_profile_selection_rationale: str
    started_at: float = field(default_factory=time.time)
    turns: List[TurnUsage] = field(default_factory=list)
    total_escalations: int = 0
    total_repair_attempts: int = 0
    final_profile_name: Optional[str] = None
    escalation_reasons: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_turn(self, turn: TurnUsage) -> None:
        """Append a completed turn record."""
        self.turns.append(turn)
        if turn.was_escalation:
            self.total_escalations += 1
        if turn.was_repair:
            self.total_repair_attempts += 1
        self.final_profile_name = turn.profile_name

    def record_escalation_reason(self, reason: str) -> None:
        self.escalation_reasons.append(reason)

    # ------------------------------------------------------------------
    # Aggregates
    # ------------------------------------------------------------------

    def total_model_calls(self) -> int:
        return len(self.turns)

    def total_tool_calls(self) -> int:
        return sum(t.tool_calls for t in self.turns)

    def total_tokens_in(self) -> Optional[int]:
        """Sum of reported tokens_in; None if no turn reported tokens."""
        vals = [t.tokens_in for t in self.turns if t.tokens_in is not None]
        return sum(vals) if vals else None

    def total_tokens_out(self) -> Optional[int]:
        """Sum of reported tokens_out; None if no turn reported tokens."""
        vals = [t.tokens_out for t in self.turns if t.tokens_out is not None]
        return sum(vals) if vals else None

    def elapsed_sec(self) -> float:
        return time.time() - self.started_at

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "initial_profile_name": self.initial_profile_name,
            "initial_profile_selection_rationale": self.initial_profile_selection_rationale,
            "final_profile_name": self.final_profile_name,
            "total_model_calls": self.total_model_calls(),
            "total_tool_calls": self.total_tool_calls(),
            "total_repair_attempts": self.total_repair_attempts,
            "total_escalations": self.total_escalations,
            "total_tokens_in": self.total_tokens_in(),   # May be None
            "total_tokens_out": self.total_tokens_out(), # May be None
            "elapsed_sec": round(self.elapsed_sec(), 3),
            "escalation_reasons": self.escalation_reasons,
            "turns": [t.as_dict() for t in self.turns],
        }

    def operator_summary(self) -> str:
        """Human-readable operator-facing summary for inclusion in replay/result."""
        lines = [
            f"Task: {self.task_id} | Tenant: {self.tenant_id}",
            f"Initial profile: {self.initial_profile_name}",
            f"  Rationale: {self.initial_profile_selection_rationale}",
            f"Final profile: {self.final_profile_name or self.initial_profile_name}",
            f"Model calls: {self.total_model_calls()} | Tool calls: {self.total_tool_calls()}",
            f"Repairs: {self.total_repair_attempts} | Escalations: {self.total_escalations}",
        ]
        ti = self.total_tokens_in()
        to_ = self.total_tokens_out()
        if ti is not None or to_ is not None:
            lines.append(f"Tokens in: {ti} | Tokens out: {to_}")
        else:
            lines.append("Tokens: not reported by provider")
        lines.append(f"Elapsed: {self.elapsed_sec():.1f}s")
        if self.escalation_reasons:
            lines.append(f"Escalation reasons: {'; '.join(self.escalation_reasons)}")
        return "\n".join(lines)
