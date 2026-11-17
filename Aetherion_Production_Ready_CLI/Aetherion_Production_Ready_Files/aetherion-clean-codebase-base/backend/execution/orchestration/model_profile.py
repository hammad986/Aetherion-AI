"""
backend/execution/orchestration/model_profile.py — Canonical Model Profile Abstraction.

Defines:
  - ModelRole enum  : PLANNER, EXECUTOR, VERIFIER, REPAIR, ESCALATION
  - ModelProfile    : per-role provider configuration (immutable dataclass)
  - ModelProfileRegistry : ordered registry with profile lookup by role + tier
  - get_default_registry : returns the authoritative default registry

No provider credentials are stored in profiles. No model provider is invoked here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger("aetherion.orchestration.model_profile")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ModelRole(str, Enum):
    """Logical agent roles within a task execution pipeline."""
    PLANNER = "PLANNER"
    EXECUTOR = "EXECUTOR"
    VERIFIER = "VERIFIER"
    REPAIR = "REPAIR"
    ESCALATION = "ESCALATION"


class ProfileTier(str, Enum):
    """Cost / capability tiers for model profiles."""
    CHEAP = "CHEAP"
    STANDARD = "STANDARD"
    STRONG = "STRONG"
    SPECIALIZED = "SPECIALIZED"


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry configuration for a single model role invocation."""
    max_retries: int = 2
    backoff_sec: float = 1.0
    max_backoff_sec: float = 10.0

    def as_dict(self) -> Dict:
        return {
            "max_retries": self.max_retries,
            "backoff_sec": self.backoff_sec,
            "max_backoff_sec": self.max_backoff_sec,
        }


# ---------------------------------------------------------------------------
# ModelProfile — immutable per-role model configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelProfile:
    """
    Immutable model profile associating a logical role with execution parameters.

    Fields:
        name            : Human-readable profile identifier.
        role            : Logical pipeline role this profile serves.
        tier            : Cost/capability tier (CHEAP < STANDARD < STRONG < SPECIALIZED).
        provider_name   : Logical provider key used to look up the provider at runtime.
        model_id        : Model identifier string passed to the provider.
        temperature     : Sampling temperature (0.0 = deterministic).
        context_budget  : Maximum context window size in tokens.
        token_budget    : Maximum output tokens per single model call.
        timeout_sec     : Maximum seconds to wait for a model response.
        retry_policy    : Retry configuration for transient provider errors.
        fallback_profile: Name of the profile to escalate to on exhaustion.
                          None means terminal (no escalation possible).
    """
    name: str
    role: ModelRole
    tier: ProfileTier
    provider_name: str = "default"
    model_id: str = "default"
    temperature: float = 0.2
    context_budget: int = 8_000
    token_budget: int = 2_000
    timeout_sec: float = 60.0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    fallback_profile: Optional[str] = None

    def as_dict(self) -> Dict:
        return {
            "name": self.name,
            "role": self.role.value,
            "tier": self.tier.value,
            "provider_name": self.provider_name,
            "model_id": self.model_id,
            "temperature": self.temperature,
            "context_budget": self.context_budget,
            "token_budget": self.token_budget,
            "timeout_sec": self.timeout_sec,
            "retry_policy": self.retry_policy.as_dict(),
            "fallback_profile": self.fallback_profile,
        }


# ---------------------------------------------------------------------------
# ModelProfileRegistry
# ---------------------------------------------------------------------------

class ModelProfileRegistry:
    """
    Registry of named ModelProfiles with role/tier lookup.

    Profiles are stored by name; lookup by role returns the cheapest tier
    unless a minimum tier is specified.
    """

    def __init__(self, profiles: Optional[Sequence[ModelProfile]] = None) -> None:
        self._profiles: Dict[str, ModelProfile] = {}
        for p in (profiles or []):
            self.register(p)

    def register(self, profile: ModelProfile) -> None:
        """Register a profile by name (overwrites existing)."""
        self._profiles[profile.name] = profile
        logger.debug("Registered model profile '%s' (role=%s, tier=%s)", profile.name, profile.role, profile.tier)

    def get(self, name: str) -> Optional[ModelProfile]:
        """Look up a profile by exact name."""
        return self._profiles.get(name)

    def get_for_role(
        self,
        role: ModelRole,
        min_tier: ProfileTier = ProfileTier.CHEAP,
    ) -> Optional[ModelProfile]:
        """
        Return the lowest-cost profile for *role* that meets *min_tier*.

        Tier ordering: CHEAP < STANDARD < STRONG < SPECIALIZED.
        """
        tier_order = [ProfileTier.CHEAP, ProfileTier.STANDARD, ProfileTier.STRONG, ProfileTier.SPECIALIZED]
        min_idx = tier_order.index(min_tier) if min_tier in tier_order else 0
        eligible = [
            p for p in self._profiles.values()
            if p.role == role and tier_order.index(p.tier) >= min_idx
        ]
        if not eligible:
            return None
        eligible.sort(key=lambda p: tier_order.index(p.tier))
        return eligible[0]

    def all_for_role(self, role: ModelRole) -> List[ModelProfile]:
        """Return all profiles for *role* ordered cheapest-first."""
        tier_order = [ProfileTier.CHEAP, ProfileTier.STANDARD, ProfileTier.STRONG, ProfileTier.SPECIALIZED]
        result = [p for p in self._profiles.values() if p.role == role]
        result.sort(key=lambda p: tier_order.index(p.tier))
        return result

    def names(self) -> List[str]:
        return list(self._profiles.keys())

    def as_dict(self) -> Dict[str, Dict]:
        return {name: p.as_dict() for name, p in self._profiles.items()}


# ---------------------------------------------------------------------------
# Default registry
# ---------------------------------------------------------------------------

def get_default_registry() -> ModelProfileRegistry:
    """
    Return the authoritative default ModelProfileRegistry.

    Profiles use provider_name="default" which resolves to whatever provider
    is configured in the environment. All model_id values are logical keys;
    no credentials are embedded.
    """
    retry_light = RetryPolicy(max_retries=1, backoff_sec=0.5)
    retry_std = RetryPolicy(max_retries=2, backoff_sec=1.0)
    retry_strong = RetryPolicy(max_retries=3, backoff_sec=2.0)

    profiles = [
        # ---- PLANNER profiles ----
        ModelProfile(
            name="planner_cheap",
            role=ModelRole.PLANNER,
            tier=ProfileTier.CHEAP,
            provider_name="default",
            model_id="default",
            temperature=0.1,
            context_budget=4_000,
            token_budget=1_000,
            timeout_sec=30.0,
            retry_policy=retry_light,
            fallback_profile="planner_standard",
        ),
        ModelProfile(
            name="planner_standard",
            role=ModelRole.PLANNER,
            tier=ProfileTier.STANDARD,
            provider_name="default",
            model_id="default",
            temperature=0.1,
            context_budget=8_000,
            token_budget=2_000,
            timeout_sec=60.0,
            retry_policy=retry_std,
            fallback_profile="planner_strong",
        ),
        ModelProfile(
            name="planner_strong",
            role=ModelRole.PLANNER,
            tier=ProfileTier.STRONG,
            provider_name="default",
            model_id="default",
            temperature=0.05,
            context_budget=16_000,
            token_budget=4_000,
            timeout_sec=120.0,
            retry_policy=retry_strong,
            fallback_profile=None,
        ),
        # ---- EXECUTOR profiles ----
        ModelProfile(
            name="executor_cheap",
            role=ModelRole.EXECUTOR,
            tier=ProfileTier.CHEAP,
            provider_name="default",
            model_id="default",
            temperature=0.2,
            context_budget=4_000,
            token_budget=1_500,
            timeout_sec=45.0,
            retry_policy=retry_light,
            fallback_profile="executor_standard",
        ),
        ModelProfile(
            name="executor_standard",
            role=ModelRole.EXECUTOR,
            tier=ProfileTier.STANDARD,
            provider_name="default",
            model_id="default",
            temperature=0.2,
            context_budget=8_000,
            token_budget=2_000,
            timeout_sec=90.0,
            retry_policy=retry_std,
            fallback_profile="executor_strong",
        ),
        ModelProfile(
            name="executor_strong",
            role=ModelRole.EXECUTOR,
            tier=ProfileTier.STRONG,
            provider_name="default",
            model_id="default",
            temperature=0.1,
            context_budget=16_000,
            token_budget=4_000,
            timeout_sec=180.0,
            retry_policy=retry_strong,
            fallback_profile=None,
        ),
        # ---- VERIFIER profiles ----
        ModelProfile(
            name="verifier_standard",
            role=ModelRole.VERIFIER,
            tier=ProfileTier.STANDARD,
            provider_name="default",
            model_id="default",
            temperature=0.0,
            context_budget=6_000,
            token_budget=1_000,
            timeout_sec=60.0,
            retry_policy=retry_std,
            fallback_profile="verifier_strong",
        ),
        ModelProfile(
            name="verifier_strong",
            role=ModelRole.VERIFIER,
            tier=ProfileTier.STRONG,
            provider_name="default",
            model_id="default",
            temperature=0.0,
            context_budget=12_000,
            token_budget=2_000,
            timeout_sec=120.0,
            retry_policy=retry_strong,
            fallback_profile=None,
        ),
        # ---- REPAIR profiles ----
        ModelProfile(
            name="repair_standard",
            role=ModelRole.REPAIR,
            tier=ProfileTier.STANDARD,
            provider_name="default",
            model_id="default",
            temperature=0.15,
            context_budget=8_000,
            token_budget=2_000,
            timeout_sec=90.0,
            retry_policy=retry_std,
            fallback_profile="repair_strong",
        ),
        ModelProfile(
            name="repair_strong",
            role=ModelRole.REPAIR,
            tier=ProfileTier.STRONG,
            provider_name="default",
            model_id="default",
            temperature=0.1,
            context_budget=16_000,
            token_budget=4_000,
            timeout_sec=180.0,
            retry_policy=retry_strong,
            fallback_profile=None,
        ),
        # ---- ESCALATION profile (terminal) ----
        ModelProfile(
            name="escalation_specialized",
            role=ModelRole.ESCALATION,
            tier=ProfileTier.SPECIALIZED,
            provider_name="default",
            model_id="default",
            temperature=0.0,
            context_budget=32_000,
            token_budget=8_000,
            timeout_sec=300.0,
            retry_policy=retry_strong,
            fallback_profile=None,
        ),
    ]

    registry = ModelProfileRegistry(profiles)
    return registry
