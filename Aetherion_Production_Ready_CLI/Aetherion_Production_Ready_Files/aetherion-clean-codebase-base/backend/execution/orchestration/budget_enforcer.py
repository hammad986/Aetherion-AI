"""
backend/execution/orchestration/budget_enforcer.py — Live Budget Enforcement.

Phase 16: Bridges the Phase 15 RunBudget and UsageAccounting objects into the
live RealAgentRuntime execution loop.

BudgetEnforcer is the single authority for:
  - Constructing a RunBudget from TaskExecutionContext.execution_budget
  - Recording every model call via record_model_call()
  - Recording every tool call via record_tool_call()
  - Recording every repair attempt via record_repair_attempt()
  - Checking all limits via check() — raises BudgetExhaustedError on breach
  - Constructing TurnUsage records for UsageAccounting
  - Producing a final usage evidence dict for durable state and replay

Architecture invariants preserved:
  - One authoritative budget (RunBudget.from_context_budget)
  - One verifier (not touched)
  - One runtime (RealAgentRuntime, not replaced)
  - HITL intact (not bypassed)
  - No hidden model fallback
  - No infinite retries
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .run_budget import BudgetExhaustedError, RunBudget
from .usage_accounting import TurnUsage, UsageAccounting

logger = logging.getLogger("aetherion.orchestration.budget_enforcer")


# ---------------------------------------------------------------------------
# BudgetEnforcer — single coordinating object per task
# ---------------------------------------------------------------------------

class BudgetEnforcer:
    """
    Live runtime budget enforcement and usage accounting coordinator.

    Usage (inside RealAgentRuntime):

        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget=context.execution_budget,
            task_id=task_id,
            tenant_id=tenant_id,
            profile_name=policy_profile_name,
            rationale=policy_rationale,
        )

        # After each model call:
        enforcer.after_model_call(
            tokens_in=..., tokens_out=..., duration_ms=..., profile_name=...
        )
        enforcer.check()           # raises BudgetExhaustedError if over limit

        # After each tool call:
        enforcer.after_tool_call(tool_name=..., ok=...)

        # After each repair attempt:
        enforcer.after_repair_attempt()

        # At end of run:
        evidence = enforcer.final_evidence()
    """

    def __init__(
        self,
        run_budget: RunBudget,
        usage_accounting: UsageAccounting,
    ) -> None:
        self._budget = run_budget
        self._usage = usage_accounting
        self._turn_started_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_execution_budget(
        cls,
        execution_budget: Dict[str, Any],
        task_id: str,
        tenant_id: str,
        profile_name: str,
        rationale: str,
    ) -> "BudgetEnforcer":
        """
        Construct BudgetEnforcer from a TaskExecutionContext.execution_budget dict.

        This is the one authoritative construction path.
        """
        budget = RunBudget.from_context_budget(execution_budget)
        usage = UsageAccounting(
            task_id=task_id,
            tenant_id=tenant_id,
            initial_profile_name=profile_name,
            initial_profile_selection_rationale=rationale,
        )
        return cls(run_budget=budget, usage_accounting=usage)

    # ------------------------------------------------------------------
    # Per-call recording
    # ------------------------------------------------------------------

    def start_turn(self) -> None:
        """Mark the start of a model call for duration tracking."""
        self._turn_started_at = time.time()

    def after_model_call(
        self,
        tokens_in: int = 0,
        tokens_out: int = 0,
        profile_name: Optional[str] = None,
        role: str = "executor",
        model_id: str = "unknown",
        provider_name: str = "default",
        was_escalation: bool = False,
        was_repair: bool = False,
        tool_calls_in_turn: int = 0,
    ) -> None:
        """
        Record one completed model call.

        Must be called after every provider.chat() returns, before check().
        """
        elapsed_sec: Optional[float] = None
        if self._turn_started_at is not None:
            elapsed_sec = round(time.time() - self._turn_started_at, 3)
            self._turn_started_at = None

        # 1. Record in RunBudget
        self._budget.record_model_call(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

        # 2. Record in UsageAccounting
        turn_index = len(self._usage.turns)
        turn = TurnUsage(
            turn_index=turn_index,
            role=role,
            profile_name=profile_name or self._usage.initial_profile_name,
            model_id=model_id,
            tokens_in=tokens_in if tokens_in > 0 else None,
            tokens_out=tokens_out if tokens_out > 0 else None,
            elapsed_sec=elapsed_sec,
            tool_calls=tool_calls_in_turn,
            was_escalation=was_escalation,
            was_repair=was_repair,
            provider_name=provider_name,
        )
        self._usage.record_turn(turn)


    def after_tool_call(self, tool_name: str = "", ok: bool = True) -> None:
        """Record one tool call. Call after every tool dispatch."""
        self._budget.record_tool_call()

    def after_repair_attempt(self) -> None:
        """Record a repair attempt (called when entering repair loop)."""
        self._budget.record_repair_attempt()

    def after_escalation(self, reason: str = "") -> None:
        """Record an escalation event."""
        self._budget.record_escalation()
        self._usage.record_escalation_reason(reason)

    # ------------------------------------------------------------------
    # Enforcement
    # ------------------------------------------------------------------

    def check(self) -> None:
        """
        Check all limits. Raises BudgetExhaustedError if any is breached.

        Call this after each model call and after each tool call in the loop.
        """
        self._budget.check()

    def check_repair(self) -> None:
        """Check repair limit specifically. Raises BudgetExhaustedError if over."""
        self._budget.check_repair()

    def check_escalation(self) -> None:
        """Check escalation limit specifically. Raises BudgetExhaustedError if over."""
        self._budget.check_escalation()

    def remaining(self) -> Dict[str, Any]:
        """Return remaining budget dimensions (negative = already over)."""
        return self._budget.remaining()

    def budget_summary(self) -> Dict[str, Any]:
        """Return RunBudget current state (limits + usage + remaining)."""
        return self._budget.as_dict()

    # ------------------------------------------------------------------
    # Final evidence (for durable state and replay events)
    # ------------------------------------------------------------------

    def final_evidence(self) -> Dict[str, Any]:
        """
        Produce structured usage evidence for durable state and replay.

        Returned dict is safe to serialize — no provider credentials.
        """
        usage_dict = self._usage.as_dict()
        budget_dict = self._budget.as_dict()
        return {
            "type": "budget_enforcement_final",
            "task_id": self._usage.task_id,
            "tenant_id": self._usage.tenant_id,
            "initial_profile": self._usage.initial_profile_name,
            "final_profile": self._usage.final_profile_name,
            "profile_selection_rationale": self._usage.initial_profile_selection_rationale,
            "usage": usage_dict,
            "budget": budget_dict,
            "exhaustion_events": self._budget.exhaustion_events,
        }

    # ------------------------------------------------------------------
    # Accessors (for tests and pipeline)
    # ------------------------------------------------------------------

    @property
    def run_budget(self) -> RunBudget:
        return self._budget

    @property
    def usage_accounting(self) -> UsageAccounting:
        return self._usage

    @property
    def total_model_calls(self) -> int:
        return self._budget.usage.model_calls

    @property
    def total_tool_calls(self) -> int:
        return self._budget.usage.tool_calls

    @property
    def total_tokens_used(self) -> int:
        return self._budget.usage.tokens_used

    @property
    def total_repair_attempts(self) -> int:
        return self._budget.usage.repair_attempts

    @property
    def total_escalations(self) -> int:
        return self._budget.usage.escalation_attempts
