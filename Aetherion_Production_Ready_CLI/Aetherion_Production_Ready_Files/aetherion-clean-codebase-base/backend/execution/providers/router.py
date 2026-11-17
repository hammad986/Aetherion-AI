"""
backend/execution/providers/router.py — Model routing and provider resolution layer.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from .anthropic import AnthropicRuntimeProvider
from .base import BaseModelProvider, ProviderUnavailableException
from .custom import DeterministicTestProvider, OfflineFallbackProvider
from .openai import OpenAIRuntimeProvider
from .registry import ProviderRegistry

logger = logging.getLogger("aetherion.providers.router")

_GLOBAL_ROUTER: Optional[ModelRouter] = None


class ModelRouter:
    """Dynamic router resolving requests to appropriate model provider."""

    def __init__(self, registry: Optional[ProviderRegistry] = None):
        self.registry = registry or ProviderRegistry()

    def resolve(
        self,
        model_name: str = "auto",
        provider_id: Optional[str] = None,
    ) -> BaseModelProvider:
        """Resolve model provider based on priority order:

        1. Explicit provider ID
        2. Test mode environment / deterministic override
        3. Explicit model name matching registered custom provider
        4. Default registered provider in registry
        5. Legacy environment variables (ANTHROPIC_API_KEY, OPENAI_API_KEY)
        6. CI / pytest test runner fallback (when model_name == 'auto')
        7. Truthful offline fallback
        """
        # 1. Explicit provider ID
        if provider_id:
            rec = self.registry.get_provider(provider_id, include_raw_key=True)
            if not rec:
                raise ProviderUnavailableException(f"Provider '{provider_id}' not found in registry.")
            if not rec.get("is_enabled"):
                raise ProviderUnavailableException(f"Provider '{provider_id}' is currently disabled.")
            return self.registry.build_instance(rec, target_model=model_name if model_name != "auto" else None)

        # 2. Test mode / deterministic override
        if os.environ.get("AETHERION_TEST_MODE") == "1" or "deterministic" in model_name.lower():
            inject_fail = os.environ.get("AETHERION_TEST_INJECT_FAILURE") == "1"
            force_fail = os.environ.get("AETHERION_TEST_FORCE_FAIL") == "1"
            return DeterministicTestProvider(inject_failure_once=inject_fail, force_verification_fail=force_fail)

        # 3. Explicit model name matching registered provider
        if model_name and model_name != "auto":
            for p in self.registry.list_providers(include_raw_keys=True):
                if not p.get("is_enabled"):
                    continue
                avail = p.get("available_models", [])
                if model_name == p.get("default_model") or model_name in avail:
                    return self.registry.build_instance(p, target_model=model_name)

        # 4. Default registered provider
        def_prov = self.registry.get_default_provider(include_raw_key=True)
        if def_prov and def_prov.get("is_enabled"):
            target_m = model_name if model_name != "auto" else def_prov.get("default_model")
            return self.registry.build_instance(def_prov, target_model=target_m)

        # 5. Legacy environment variables
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        anthropic_base = os.environ.get("ANTHROPIC_BASE_URL", "").strip() or "https://api.anthropic.com"
        env_anthropic_model = os.environ.get("ANTHROPIC_MODEL", "").strip()
        if anthropic_key and ("claude" in model_name.lower() or "deepseek" in model_name.lower() or "glm" in model_name.lower() or model_name == "auto"):
            if env_anthropic_model and ("claude" in model_name.lower() or model_name == "auto"):
                target_model = env_anthropic_model
            else:
                target_model = model_name if model_name != "auto" else (env_anthropic_model or "claude-3-5-sonnet-20241022")
            return AnthropicRuntimeProvider(api_key=anthropic_key, base_url=anthropic_base, model=target_model)

        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        openai_base = os.environ.get("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1"
        if openai_key and ("gpt" in model_name.lower() or "o1" in model_name.lower() or "o3" in model_name.lower() or model_name == "auto"):
            target_model = model_name if model_name != "auto" else "gpt-4o"
            return OpenAIRuntimeProvider(api_key=openai_key, base_url=openai_base, model=target_model)

        # 6. Forced offline or CI test fallback
        if os.environ.get("AETHERION_FORCE_OFFLINE") == "1" or model_name.lower() in ("offline", "none"):
            return OfflineFallbackProvider()

        if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
            if model_name == "auto":
                return DeterministicTestProvider()

        # 7. Truthful offline fallback
        return OfflineFallbackProvider()


def get_router() -> ModelRouter:
    """Get global ModelRouter singleton."""
    global _GLOBAL_ROUTER
    if _GLOBAL_ROUTER is None:
        _GLOBAL_ROUTER = ModelRouter()
    return _GLOBAL_ROUTER


def get_provider(model_name: str = "auto", provider_id: Optional[str] = None) -> BaseModelProvider:
    """Canonical factory function to obtain configured model provider."""
    return get_router().resolve(model_name=model_name, provider_id=provider_id)
