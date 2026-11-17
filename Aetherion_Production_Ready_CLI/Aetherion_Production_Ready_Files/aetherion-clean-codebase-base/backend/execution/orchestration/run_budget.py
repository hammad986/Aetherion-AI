"""
backend/execution/orchestration/run_budget.py — Hard RunBudget Enforcement.

RunBudget tracks and enforces six hard limits:
  - max_model_calls
  - max_tool_calls
  - max_repair_attempts
  - max_execution_time_sec
  - max_tokens (input + output combined)
  - max_escalation_attempts

When any limit is breached, BudgetExhaustedError is raised with structured
evidence identifying which limit was hit.

RunBudget integrates with TaskExecutionContext.execution_budget dict by
constructing itself from that dict — there is exactly ONE budget authority.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger("aetherion.orchestration.run_budget")


# ---------------------------------------------------------------------------
# BudgetExhaustedError — structured terminal failure
# ---------------------------------------------------------------------------

class BudgetExhaustedError(RuntimeError):
    """
    Raised when a RunBudget limit is breached.

    Attributes:
        limit_name     : Name of the exhausted limit.
        current_value  : Value at time of exhaustion.
        configured_max : Configured limit.
        terminal_action: Recommended action (e.g., "FAIL_TASK").
        evidence       : Full structured evidence dict for replay/operator.
    """

    def __init__(
        self,
        limit_name: str,
        current_value: float,
        configured_max: float,
        terminal_action: str = "FAIL_TASK",
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.limit_name = limit_name
        self.current_value = current_value
        self.configured_max = configured_max
        self.terminal_action = terminal_action
        self.evidence = evidence or {}
        msg = (
            f"RunBudget exhausted: {limit_name} "
            f"(current={current_value}, max={configured_max}). "
            f"Action: {terminal_action}"
        )
        super().__init__(msg)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "type": "budget_exhausted",
            "limit_name": self.limit_name,
            "current_value": self.current_value,
            "configured_max": self.configured_max,
            "terminal_action": self.terminal_action,
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# RunBudgetUsage — current consumption snapshot
# ---------------------------------------------------------------------------

@dataclass
class RunBudgetUsage:
    """Mutable snapshot of current budget consumption."""
    model_calls: int = 0
    tool_calls: int = 0
    repair_attempts: int = 0
    tokens_used: int = 0
    escalation_attempts: int = 0
    started_at: float = field(default_factory=time.time)

    def elapsed_sec(self) -> float:
        return time.time() - self.started_at

    def as_dict(self) -> Dict[str, Any]:
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "repair_attempts": self.repair_attempts,
            "tokens_used": self.tokens_used,
            "escalation_attempts": self.escalation_attempts,
            "elapsed_sec": round(self.elapsed_sec(), 3),
        }


# ---------------------------------------------------------------------------
# RunBudget — enforcement authority
# ---------------------------------------------------------------------------

class RunBudget:
    """
    Hard budget enforcement for a single task execution.

    Calling check() raises BudgetExhaustedError if any limit is breached.
    All limit-breaching events are recorded in .exhaustion_events for replay.

    Integrates with TaskExecutionContext.execution_budget via from_context_budget().
    There is exactly one RunBudget per task; it is the single source of truth for
    resource limits.
    """

    # Default limits (conservative but not extremely tight)
    DEFAULT_MAX_MODEL_CALLS = 20
    DEFAULT_MAX_TOOL_CALLS = 50
    DEFAULT_MAX_REPAIR_ATTEMPTS = 2
    DEFAULT_MAX_EXECUTION_TIME_SEC = 300.0
    DEFAULT_MAX_TOKENS = 50_000
    DEFAULT_MAX_ESCALATION_ATTEMPTS = 2

    def __init__(
        self,
        max_model_calls: int = DEFAULT_MAX_MODEL_CALLS,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        max_repair_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS,
        max_execution_time_sec: float = DEFAULT_MAX_EXECUTION_TIME_SEC,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_escalation_attempts: int = DEFAULT_MAX_ESCALATION_ATTEMPTS,
    ) -> None:
        self.max_model_calls = max_model_calls
        self.max_tool_calls = max_tool_calls
        self.max_repair_attempts = max_repair_attempts
        self.max_execution_time_sec = max_execution_time_sec
        self.max_tokens = max_tokens
        self.max_escalation_attempts = max_escalation_attempts

        self.usage = RunBudgetUsage()
        self.exhaustion_events: list[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Factory method — constructs from TaskExecutionContext.execution_budget
    # ------------------------------------------------------------------

    @classmethod
    def from_context_budget(cls, execution_budget: Dict[str, Any]) -> "RunBudget":
        """
        Construct a RunBudget from a TaskExecutionContext.execution_budget dict.

        Keys (all optional, fall back to class defaults):
          max_model_calls, max_tool_calls, max_repair_attempts,
          max_execution_time_sec, max_tokens, max_escalation_attempts
        """
        return cls(
            max_model_calls=int(execution_budget.get("max_model_calls", cls.DEFAULT_MAX_MODEL_CALLS)),
            max_tool_calls=int(execution_budget.get("max_tool_calls", cls.DEFAULT_MAX_TOOL_CALLS)),
            max_repair_attempts=int(execution_budget.get("max_repair_attempts", cls.DEFAULT_MAX_REPAIR_ATTEMPTS)),
            max_execution_time_sec=float(execution_budget.get("max_execution_time_sec", cls.DEFAULT_MAX_EXECUTION_TIME_SEC)),
            max_tokens=int(execution_budget.get("max_tokens", cls.DEFAULT_MAX_TOKENS)),
            max_escalation_attempts=int(execution_budget.get("max_escalation_attempts", cls.DEFAULT_MAX_ESCALATION_ATTEMPTS)),
        )

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    @property
    def model_calls_consumed(self) -> int:
        return self.usage.model_calls

    def consume_model_call(self, tokens: int = 0) -> None:
        """Record model call, consume tokens, and enforce limits immediately."""
        self.record_model_call(tokens_in=tokens, tokens_out=0)
        self.check()

    def record_model_call(self, tokens_in: int = 0, tokens_out: int = 0) -> None:
        """Record one model call and token consumption."""
        self.usage.model_calls += 1
        self.usage.tokens_used += tokens_in + tokens_out

    def record_tool_call(self) -> None:
        """Record one tool call."""
        self.usage.tool_calls += 1

    def record_repair_attempt(self) -> None:
        """Record one repair attempt."""
        self.usage.repair_attempts += 1

    def record_escalation(self) -> None:
        """Record one escalation attempt."""
        self.usage.escalation_attempts += 1

    # ------------------------------------------------------------------
    # Enforcement
    # ------------------------------------------------------------------

    def check(self) -> None:
        """
        Check all limits. Raises BudgetExhaustedError on first violation.
        The violation is recorded in .exhaustion_events before raising.
        """
        self._check_limit("max_model_calls", self.usage.model_calls, self.max_model_calls)
        self._check_limit("max_tool_calls", self.usage.tool_calls, self.max_tool_calls)
        self._check_limit("max_repair_attempts", self.usage.repair_attempts, self.max_repair_attempts)
        self._check_limit("max_execution_time_sec", self.usage.elapsed_sec(), self.max_execution_time_sec)
        self._check_limit("max_tokens", self.usage.tokens_used, self.max_tokens)
        self._check_limit("max_escalation_attempts", self.usage.escalation_attempts, self.max_escalation_attempts)

    def check_repair(self) -> None:
        """Specifically check the repair limit (called before entering repair loop)."""
        self._check_limit("max_repair_attempts", self.usage.repair_attempts, self.max_repair_attempts)

    def check_escalation(self) -> None:
        """Specifically check the escalation limit."""
        self._check_limit("max_escalation_attempts", self.usage.escalation_attempts, self.max_escalation_attempts)

    def remaining(self) -> Dict[str, Any]:
        """Return remaining budget for each dimension (negative = already over)."""
        return {
            "model_calls_remaining": self.max_model_calls - self.usage.model_calls,
            "tool_calls_remaining": self.max_tool_calls - self.usage.tool_calls,
            "repair_attempts_remaining": self.max_repair_attempts - self.usage.repair_attempts,
            "time_remaining_sec": round(
                self.max_execution_time_sec - self.usage.elapsed_sec(), 3
            ),
            "tokens_remaining": self.max_tokens - self.usage.tokens_used,
            "escalations_remaining": self.max_escalation_attempts - self.usage.escalation_attempts,
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "limits": {
                "max_model_calls": self.max_model_calls,
                "max_tool_calls": self.max_tool_calls,
                "max_repair_attempts": self.max_repair_attempts,
                "max_execution_time_sec": self.max_execution_time_sec,
                "max_tokens": self.max_tokens,
                "max_escalation_attempts": self.max_escalation_attempts,
            },
            "usage": self.usage.as_dict(),
            "remaining": self.remaining(),
        }

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _check_limit(self, name: str, current: float, maximum: float) -> None:
        if current > maximum:
            ev = {
                "limit_name": name,
                "current_value": current,
                "configured_max": maximum,
                "usage_snapshot": self.usage.as_dict(),
            }
            self.exhaustion_events.append(ev)
            logger.warning(
                "RunBudget: %s exhausted (current=%.2f, max=%.2f)",
                name, current, maximum,
            )
            raise BudgetExhaustedError(
                limit_name=name,
                current_value=current,
                configured_max=maximum,
                terminal_action="FAIL_TASK",
                evidence=ev,
            )
