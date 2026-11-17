"""
backend/execution/fabric/retry_policy.py — Phase 21 & Phase 24 Side-Effect Retry Safety Policy.

Defines explicit failure classification and safety categorization for task and step retries,
preventing unsafe duplicate execution of non-idempotent or dangerous external operations,
and failing closed whenever retry safety is uncertain.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from ..orchestration.durable_supervisor import RecoveryDecision
from .models import FailureCategory, RetrySafetyClass

logger = logging.getLogger("aetherion.fabric.retry_policy")

# Read-only tools that are always safe to retry
SAFE_READ_TOOLS: Set[str] = {
    "read_file",
    "view_file",
    "list_directory",
    "grep_search",
    "file_search",
    "symbol_search",
    "git_status",
    "git_diff",
    "inspect_code",
    "get_task_context",
    "memory_retrieve",
}

# Dangerous or external tools that must fail closed on unverified retry
DANGEROUS_SIDE_EFFECT_TOOLS: Set[str] = {
    "execute_command",
    "run_shell",
    "deploy",
    "delete_file",
    "network_request",
    "db_drop",
    "publish_event",
    "git_push",
}


def evaluate_retry_safety(
    tool_names: Optional[List[str]] = None,
    has_unverified_mutations: bool = False,
    is_idempotent: bool = False,
    risk_level: str = "LOW",
    requires_hitl: bool = False,
    unknown_state: bool = False,
) -> RetrySafetyClass:
    """
    Deterministically categorize the retry safety of an operation.

    Rules:
    1. If unknown_state is True: FAIL_CLOSED.
    2. If requires_hitl or CRITICAL/HIGH risk with mutations: FAIL_CLOSED.
    3. If tools contain dangerous side-effect tools without explicit idempotency: FAIL_CLOSED.
    4. If only safe read tools were invoked: SAFE_RETRY.
    5. If is_idempotent is True: IDEMPOTENT.
    6. If unverified mutations exist without idempotency: FAIL_CLOSED.
    7. Low/Medium risk with isolated worktree: CONDITIONAL_RETRY.
    8. Any uncertain state: FAIL_CLOSED.
    """
    if unknown_state:
        return RetrySafetyClass.FAIL_CLOSED

    tools = set(tool_names or [])
    norm_risk = str(risk_level).upper()

    if requires_hitl or norm_risk == "CRITICAL":
        if has_unverified_mutations:
            return RetrySafetyClass.FAIL_CLOSED

    if any(t in DANGEROUS_SIDE_EFFECT_TOOLS for t in tools):
        if not is_idempotent:
            return RetrySafetyClass.FAIL_CLOSED

    if tools and tools.issubset(SAFE_READ_TOOLS) and not has_unverified_mutations:
        return RetrySafetyClass.SAFE_RETRY

    if is_idempotent:
        return RetrySafetyClass.IDEMPOTENT

    if has_unverified_mutations:
        return RetrySafetyClass.FAIL_CLOSED

    if norm_risk in ("LOW", "MEDIUM"):
        return RetrySafetyClass.CONDITIONAL_RETRY

    return RetrySafetyClass.FAIL_CLOSED


def classify_failure(
    tool_names: Optional[List[str]] = None,
    has_unverified_mutations: bool = False,
    is_idempotent: bool = False,
    risk_level: str = "LOW",
    requires_hitl: bool = False,
    hitl_pending: bool = False,
    verification_pending: bool = False,
    unknown_state: bool = False,
) -> FailureCategory:
    """
    Explicit Phase 24 failure classification taxonomy:
    - SAFE_IDEMPOTENT: read-only, idempotent calculations
    - CONDITIONAL_RETRY: safe low/medium risk with isolated workspace
    - NON_IDEMPOTENT: mutations or dangerous side effects without idempotency
    - UNKNOWN_STATE: unverified crash or missing metadata -> fail closed
    - VERIFICATION_PENDING: execution done, awaiting verifier confirmation
    - HITL_PENDING: awaiting human approval gate
    """
    if unknown_state:
        return FailureCategory.UNKNOWN_STATE

    if hitl_pending or requires_hitl:
        return FailureCategory.HITL_PENDING

    if verification_pending:
        return FailureCategory.VERIFICATION_PENDING

    safety = evaluate_retry_safety(
        tool_names=tool_names,
        has_unverified_mutations=has_unverified_mutations,
        is_idempotent=is_idempotent,
        risk_level=risk_level,
        requires_hitl=requires_hitl,
        unknown_state=unknown_state,
    )

    if safety in (RetrySafetyClass.SAFE_RETRY, RetrySafetyClass.IDEMPOTENT):
        return FailureCategory.SAFE_IDEMPOTENT
    elif safety == RetrySafetyClass.CONDITIONAL_RETRY:
        return FailureCategory.CONDITIONAL_RETRY
    else:
        return FailureCategory.NON_IDEMPOTENT


def map_safety_to_recovery_decision(
    safety: RetrySafetyClass,
    verifier_passed: bool = False,
    hitl_pending: bool = False,
    remaining_retries: int = 1,
    budget_exhausted: bool = False,
) -> RecoveryDecision:
    """
    Map RetrySafetyClass directly into a Phase 20 RecoveryDecision.
    """
    if budget_exhausted or remaining_retries <= 0:
        return RecoveryDecision.FAIL_CLOSED

    if hitl_pending:
        return RecoveryDecision.WAIT

    if verifier_passed:
        return RecoveryDecision.RESUME

    if safety in (RetrySafetyClass.SAFE_RETRY, RetrySafetyClass.IDEMPOTENT):
        return RecoveryDecision.RETRY

    if safety == RetrySafetyClass.CONDITIONAL_RETRY:
        return RecoveryDecision.RETRY

    return RecoveryDecision.FAIL_CLOSED


def map_failure_category_to_recovery(
    category: FailureCategory,
    remaining_retries: int = 1,
    budget_exhausted: bool = False,
    verifier_passed: bool = False,
) -> RecoveryDecision:
    """
    Deterministic mapping of explicit Phase 24 FailureCategory to RecoveryDecision.
    """
    if budget_exhausted or remaining_retries <= 0:
        return RecoveryDecision.FAIL_CLOSED

    if category == FailureCategory.HITL_PENDING:
        return RecoveryDecision.WAIT

    if category == FailureCategory.VERIFICATION_PENDING:
        if verifier_passed:
            return RecoveryDecision.RESUME
        return RecoveryDecision.WAIT

    if category in (FailureCategory.UNKNOWN_STATE, FailureCategory.NON_IDEMPOTENT):
        return RecoveryDecision.FAIL_CLOSED

    if category in (FailureCategory.SAFE_IDEMPOTENT, FailureCategory.CONDITIONAL_RETRY):
        return RecoveryDecision.RETRY

    return RecoveryDecision.FAIL_CLOSED
