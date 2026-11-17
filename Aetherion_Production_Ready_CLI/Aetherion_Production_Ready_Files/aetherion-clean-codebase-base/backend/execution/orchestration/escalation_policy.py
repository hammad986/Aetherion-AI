"""
backend/execution/orchestration/escalation_policy.py — Deterministic Escalation Chain.

Escalation policy governs what happens when:
  - A model profile fails (provider error / budget exhausted)
  - Verification fails and repair is exhausted
  - A repair attempt produces no improvement

The escalation chain is:
  CHEAP profile → failure → STANDARD profile → failure → STRONG profile →
  failure → ESCALATION profile → failure → TERMINAL STRUCTURED FAILURE

Every escalation decision is persisted to replay events with an explicit reason.
No automatic privilege escalation. No infinite retries.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .model_profile import ModelProfile, ModelProfileRegistry, ModelRole, get_default_registry

logger = logging.getLogger("aetherion.orchestration.escalation")


# ---------------------------------------------------------------------------
# EscalationReason — explicit escalation cause enum
# ---------------------------------------------------------------------------

class EscalationReason(str, Enum):
    """Explicit, persisted reason for escalation."""
    PROVIDER_ERROR = "PROVIDER_ERROR"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    REPAIR_EXHAUSTED = "REPAIR_EXHAUSTED"
    TOOL_FAILURE = "TOOL_FAILURE"
    TIMEOUT = "TIMEOUT"
    MANUAL_ESCALATION = "MANUAL_ESCALATION"


# ---------------------------------------------------------------------------
# EscalationChain — tracks current position in the escalation ladder
# ---------------------------------------------------------------------------

@dataclass
class EscalationChain:
    """
    Ordered list of ModelProfiles representing the escalation ladder.

    Escalation always moves forward (cheap → strong → specialized).
    Once the last profile is exhausted, the chain is terminal.
    """
    profiles: List[ModelProfile]
    _current_index: int = field(default=0, init=False, repr=False)
    escalation_log: List[Dict[str, Any]] = field(default_factory=list, init=False)

    @property
    def current_profile(self) -> ModelProfile:
        if self._current_index >= len(self.profiles):
            raise IndexError("Escalation chain exhausted — no profiles remaining")
        return self.profiles[self._current_index]

    @property
    def is_terminal(self) -> bool:
        return self._current_index >= len(self.profiles)

    def escalate(
        self,
        reason: EscalationReason,
        details: str = "",
        replay_emit_fn=None,
    ) -> Optional[ModelProfile]:
        """
        Advance to the next profile. Returns the new profile or None if terminal.

        Args:
            reason         : Explicit escalation reason (persisted to replay).
            details        : Human-readable detail string.
            replay_emit_fn : Optional callable(event_dict) for replay persistence.

        Returns:
            The newly active ModelProfile, or None if the chain is now exhausted.
        """
        from_profile = self.profiles[self._current_index] if not self.is_terminal else None
        self._current_index += 1
        to_profile = self.profiles[self._current_index] if not self.is_terminal else None

        ev = {
            "type": "escalation",
            "reason": reason.value,
            "from_profile": from_profile.name if from_profile else None,
            "to_profile": to_profile.name if to_profile else None,
            "is_terminal": self.is_terminal,
            "details": details,
        }
        self.escalation_log.append(ev)
        logger.info(
            "Escalation: %s → %s (reason=%s, details=%s)",
            from_profile.name if from_profile else "?",
            to_profile.name if to_profile else "TERMINAL",
            reason.value,
            details,
        )

        if replay_emit_fn is not None:
            try:
                replay_emit_fn(ev)
            except Exception as exc:
                logger.debug("Escalation replay emit error: %s", exc)

        return to_profile

    def as_dict(self) -> Dict[str, Any]:
        return {
            "profiles": [p.name for p in self.profiles],
            "current_index": self._current_index,
            "is_terminal": self.is_terminal,
            "current_profile": self.profiles[self._current_index].name if not self.is_terminal else None,
            "escalation_log": self.escalation_log,
        }


# ---------------------------------------------------------------------------
# EscalationPolicy — builds and governs escalation chains
# ---------------------------------------------------------------------------

class EscalationPolicy:
    """
    Constructs escalation chains for each role and governs escalation decisions.

    Rules (all deterministic, no hidden fallbacks):
      1. cheap → standard → strong → specialized(escalation) → terminal
      2. Each step is explicit and logged.
      3. No step is skipped.
      4. Terminal failure produces structured evidence.
    """

    def __init__(self, registry: Optional[ModelProfileRegistry] = None) -> None:
        self._registry = registry or get_default_registry()

    def build_chain_for_role(self, role: ModelRole) -> EscalationChain:
        """
        Build the escalation chain for a given role.

        For EXECUTOR/PLANNER/REPAIR: cheap → standard → strong
        For VERIFIER: standard → strong
        For ESCALATION: specialized only (already at top)
        """
        profiles = self._registry.all_for_role(role)
        if not profiles:
            # Synthesize a single-profile chain as terminal fallback
            from .execution_policy import _synthetic_fallback
            profiles = [_synthetic_fallback(role)]
            logger.warning("No profiles for role %s; using synthetic fallback", role.value)

        # Also append the ESCALATION profile as final step if role != ESCALATION
        if role != ModelRole.ESCALATION:
            escalation_profiles = self._registry.all_for_role(ModelRole.ESCALATION)
            if escalation_profiles:
                # Only append if not already there
                escalation_names = {p.name for p in profiles}
                for ep in escalation_profiles:
                    if ep.name not in escalation_names:
                        profiles = profiles + [ep]

        return EscalationChain(profiles=profiles)

    def build_terminal_failure_evidence(
        self,
        chain: EscalationChain,
        reason: EscalationReason,
        details: str = "",
    ) -> Dict[str, Any]:
        """
        Produce structured terminal failure evidence when the chain is exhausted.

        This evidence is included in replay events and the final task result.
        No provider credentials are included.
        """
        return {
            "type": "terminal_escalation_failure",
            "reason": reason.value,
            "details": details,
            "escalation_log": chain.escalation_log,
            "profiles_attempted": [p.name for p in chain.profiles],
            "message": (
                f"All {len(chain.profiles)} escalation profiles exhausted. "
                f"Final reason: {reason.value}. Task cannot be completed within policy bounds."
            ),
        }
