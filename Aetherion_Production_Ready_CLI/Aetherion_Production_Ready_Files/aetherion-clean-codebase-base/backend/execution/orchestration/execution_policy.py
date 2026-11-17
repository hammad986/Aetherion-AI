"""
backend/execution/orchestration/execution_policy.py — Execution Policy Derivation.

Derives a concrete ExecutionPolicy from a TaskClassification, selecting:
  - The initial model profile for each role (PLANNER, EXECUTOR, VERIFIER, REPAIR)
  - RunBudget configuration
  - Whether HITL is required before execution
  - Maximum repair attempts

Policy derivation is deterministic given classification + registry.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .model_profile import ModelProfile, ModelProfileRegistry, ModelRole, ProfileTier, get_default_registry
from .run_budget import RunBudget
from .task_classifier import TaskClassification, TaskComplexity, TaskRisk

logger = logging.getLogger("aetherion.orchestration.execution_policy")


@dataclass
class ExecutionPolicy:
    """
    Concrete execution policy derived from task classification.

    Fields:
        classification   : Source classification.
        planner_profile  : Selected ModelProfile for PLANNER role.
        executor_profile : Selected ModelProfile for EXECUTOR role.
        verifier_profile : Selected ModelProfile for VERIFIER role.
        repair_profile   : Selected ModelProfile for REPAIR role.
        run_budget       : Constructed RunBudget for this task.
        require_hitl     : Whether HITL approval is required before execution.
        max_repair_attempts: Repair cap (also reflected in run_budget).
        rationale        : Human-readable policy derivation explanation.
    """
    classification: TaskClassification
    planner_profile: ModelProfile
    executor_profile: ModelProfile
    verifier_profile: ModelProfile
    repair_profile: ModelProfile
    run_budget: RunBudget
    require_hitl: bool
    max_repair_attempts: int
    rationale: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "classification": self.classification.as_dict(),
            "planner_profile": self.planner_profile.name,
            "executor_profile": self.executor_profile.name,
            "verifier_profile": self.verifier_profile.name,
            "repair_profile": self.repair_profile.name,
            "run_budget": self.run_budget.as_dict(),
            "require_hitl": self.require_hitl,
            "max_repair_attempts": self.max_repair_attempts,
            "rationale": self.rationale,
        }


def derive_execution_policy(
    classification: TaskClassification,
    registry: Optional[ModelProfileRegistry] = None,
    execution_budget_override: Optional[Dict[str, Any]] = None,
) -> ExecutionPolicy:
    """
    Derive a concrete ExecutionPolicy from a TaskClassification.

    Policy derivation rules:
      - Severity ≥ 7 (CRITICAL tasks)     → STRONG profiles, 1 repair, HITL required
      - Severity 4-6 (COMPLEX tasks)      → STANDARD profiles, 2 repairs, HITL if risk HIGH+
      - Severity 0-3 (MODERATE/TRIVIAL)   → CHEAP profiles, 1 repair, no HITL

    RunBudget is constructed from execution_budget_override (if supplied) or
    synthesised from the classification.

    This is the single authoritative policy derivation point. No profile selection
    happens anywhere else in the pipeline.
    """
    reg = registry or get_default_registry()
    severity = classification.overall_severity
    rationale_parts = [
        f"severity={severity}",
        f"complexity={classification.complexity.value}",
        f"risk={classification.risk.value}",
        f"repo_impact={classification.repo_impact.value}",
    ]

    # --- Profile tier selection ---
    if severity >= 7 or classification.risk in (TaskRisk.CRITICAL,):
        tier = ProfileTier.STRONG
        rationale_parts.append("tier=STRONG (high severity or critical risk)")
        max_repair = 1
        require_hitl = True
    elif severity >= 4 or classification.complexity in (TaskComplexity.COMPLEX, TaskComplexity.CRITICAL):
        tier = ProfileTier.STANDARD
        rationale_parts.append("tier=STANDARD (moderate-to-high severity)")
        max_repair = 2
        require_hitl = classification.risk in (TaskRisk.HIGH, TaskRisk.CRITICAL)
    else:
        tier = ProfileTier.CHEAP
        rationale_parts.append("tier=CHEAP (low severity)")
        max_repair = 1
        require_hitl = False

    # --- Resolve profiles ---
    planner_profile = _resolve_profile(reg, ModelRole.PLANNER, tier)
    executor_profile = _resolve_profile(reg, ModelRole.EXECUTOR, tier)
    verifier_profile = _resolve_profile(reg, ModelRole.VERIFIER, _min_tier(tier, ProfileTier.STANDARD))
    repair_profile = _resolve_profile(reg, ModelRole.REPAIR, _min_tier(tier, ProfileTier.STANDARD))


    rationale_parts.append(
        f"profiles: planner={planner_profile.name}, executor={executor_profile.name}, "
        f"verifier={verifier_profile.name}, repair={repair_profile.name}"
    )
    rationale_parts.append(f"require_hitl={require_hitl}, max_repair={max_repair}")

    # --- RunBudget construction ---
    if execution_budget_override:
        # Caller explicitly supplied budget keys — merge with derived defaults
        merged = {
            "max_repair_attempts": max_repair,
            "max_model_calls": _model_calls_for_tier(tier),
            "max_tool_calls": _tool_calls_for_tier(tier),
            "max_execution_time_sec": executor_profile.timeout_sec * 5,
            "max_tokens": executor_profile.token_budget * 10,
            "max_escalation_attempts": 2,
        }
        merged.update(execution_budget_override)
        run_budget = RunBudget.from_context_budget(merged)
        rationale_parts.append("budget=merged(override+derived)")
    else:
        run_budget = RunBudget(
            max_model_calls=_model_calls_for_tier(tier),
            max_tool_calls=_tool_calls_for_tier(tier),
            max_repair_attempts=max_repair,
            max_execution_time_sec=executor_profile.timeout_sec * 5,
            max_tokens=executor_profile.token_budget * 10,
            max_escalation_attempts=2,
        )
        rationale_parts.append("budget=derived")

    return ExecutionPolicy(
        classification=classification,
        planner_profile=planner_profile,
        executor_profile=executor_profile,
        verifier_profile=verifier_profile,
        repair_profile=repair_profile,
        run_budget=run_budget,
        require_hitl=require_hitl,
        max_repair_attempts=max_repair,
        rationale="; ".join(rationale_parts),
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

_TIER_ORDER = [ProfileTier.CHEAP, ProfileTier.STANDARD, ProfileTier.STRONG, ProfileTier.SPECIALIZED]


def _min_tier(a: ProfileTier, b: ProfileTier) -> ProfileTier:
    """Return the cheaper of two tiers."""
    return a if _TIER_ORDER.index(a) <= _TIER_ORDER.index(b) else b


def _resolve_profile(
    registry: ModelProfileRegistry,
    role: ModelRole,
    tier: ProfileTier,
) -> ModelProfile:
    """Resolve profile for role/tier; fall back to any available profile for role."""
    p = registry.get_for_role(role, min_tier=tier)
    if p is None:
        # Fallback: take cheapest available for role
        all_for_role = registry.all_for_role(role)
        p = all_for_role[0] if all_for_role else _synthetic_fallback(role)
        logger.warning(
            "No profile at tier %s for role %s; falling back to %s",
            tier.value, role.value, p.name,
        )
    return p



def _synthetic_fallback(role: ModelRole) -> ModelProfile:
    """Create a minimal synthetic profile when registry has no entry for a role."""
    from .model_profile import ModelProfile, RetryPolicy
    return ModelProfile(
        name=f"synthetic_{role.value.lower()}",
        role=role,
        tier=ProfileTier.CHEAP,
        provider_name="default",
        model_id="default",
        temperature=0.2,
        context_budget=4_000,
        token_budget=1_000,
        timeout_sec=60.0,
        retry_policy=RetryPolicy(),
        fallback_profile=None,
    )


def _model_calls_for_tier(tier: ProfileTier) -> int:
    return {
        ProfileTier.CHEAP: 10,
        ProfileTier.STANDARD: 20,
        ProfileTier.STRONG: 30,
        ProfileTier.SPECIALIZED: 40,
    }.get(tier, 20)


def _tool_calls_for_tier(tier: ProfileTier) -> int:
    return {
        ProfileTier.CHEAP: 20,
        ProfileTier.STANDARD: 50,
        ProfileTier.STRONG: 80,
        ProfileTier.SPECIALIZED: 100,
    }.get(tier, 50)
