"""
backend/execution/providers/registry.py — Persistent provider registry and management.
"""
from __future__ import annotations

import copy
import logging
import uuid
from typing import Any, Dict, List, Optional

from .anthropic import AnthropicRuntimeProvider
from .base import BaseModelProvider, ProviderUnavailableException
from .custom import GenericChatProvider
from .openai import OpenAIRuntimeProvider
from .store import ProviderStore, decrypt_api_key, mask_api_key

logger = logging.getLogger("aetherion.providers.registry")

SUPPORTED_TYPES = {
    "OPENAI_COMPATIBLE",
    "ANTHROPIC_COMPATIBLE",
    "CHAT_COMPATIBLE",
}


class ProviderRegistry:
    """Registry and factory for user-defined and custom LLM providers."""

    def __init__(self, store: Optional[ProviderStore] = None):
        self.store = store or ProviderStore()

    def create_provider(self, data: Dict[str, Any], owner_id: Optional[str] = None) -> Dict[str, Any]:
        """Validate and register a new provider."""
        name = data.get("name", "").strip()
        if not name:
            raise ValueError("Provider name is required.")

        ptype = (data.get("provider_type") or "OPENAI_COMPATIBLE").upper()
        if ptype not in SUPPORTED_TYPES:
            raise ValueError(f"Unsupported provider_type: '{ptype}'. Must be one of: {sorted(SUPPORTED_TYPES)}")

        base_url = data.get("base_url", "").strip()
        if not base_url:
            raise ValueError("Provider base_url is required.")

        owner = str(owner_id or data.get("owner_id") or "")

        # Check for existing provider with same name for this owner
        existing = self.store.get_by_name(name, owner_id=owner)
        if existing:
            raise ValueError(f"Provider with name '{name}' already exists.")

        pid = data.get("id") or f"prov_{uuid.uuid4().hex[:10]}"
        record = {
            "id": pid,
            "name": name,
            "provider_type": ptype,
            "base_url": base_url,
            "api_key": data.get("api_key", "").strip(),
            "default_model": data.get("default_model", ""),
            "available_models": data.get("available_models", []),
            "is_default": bool(data.get("is_default", False)),
            "is_enabled": bool(data.get("is_enabled", True)),
            "custom_headers": data.get("custom_headers", {}),
            "metadata": data.get("metadata", {}),
            "owner_id": owner,
        }
        return self.store.insert(record, owner_id=owner)

    def create_or_update_provider(self, data: Dict[str, Any], owner_id: Optional[str] = None) -> Dict[str, Any]:
        """Validate and register a new provider or update existing by ID or name."""
        name = data.get("name", "").strip()
        if not name:
            raise ValueError("Provider name is required.")

        ptype = (data.get("provider_type") or "OPENAI_COMPATIBLE").upper()
        if ptype not in SUPPORTED_TYPES:
            raise ValueError(f"Unsupported provider_type: '{ptype}'. Must be one of: {sorted(SUPPORTED_TYPES)}")

        base_url = data.get("base_url", "").strip()
        if not base_url:
            raise ValueError("Provider base_url is required.")

        owner = str(owner_id or data.get("owner_id") or "")
        pid = data.get("id")

        # 1. Resolve existing by ID if provided
        existing = self.store.get(pid, owner_id=owner) if pid else None

        # 2. If not found by ID, resolve by name for the same owner
        if not existing:
            existing = self.store.get_by_name(name, owner_id=owner)

        if existing:
            target_id = existing["id"]
            updates: Dict[str, Any] = {
                "name": name,
                "provider_type": ptype,
                "base_url": base_url,
                "default_model": data.get("default_model", existing.get("default_model", "")),
                "is_enabled": bool(data.get("is_enabled", existing.get("is_enabled", True))),
            }
            if "api_key" in data and data["api_key"].strip():
                updates["api_key"] = data["api_key"].strip()
            if "is_default" in data:
                updates["is_default"] = bool(data["is_default"])
            if "available_models" in data:
                updates["available_models"] = data["available_models"]
            if "custom_headers" in data:
                updates["custom_headers"] = data["custom_headers"]
            if "metadata" in data:
                updates["metadata"] = data["metadata"]

            updated = self.store.update(target_id, updates, owner_id=owner)
            if updated:
                return updated

        # 3. Create fresh record
        target_pid = pid if (pid and not pid.startswith("new_") and not pid.startswith("prov_temp")) else f"prov_{uuid.uuid4().hex[:10]}"
        record = {
            "id": target_pid,
            "name": name,
            "provider_type": ptype,
            "base_url": base_url,
            "api_key": data.get("api_key", "").strip(),
            "default_model": data.get("default_model", ""),
            "available_models": data.get("available_models", []),
            "is_default": bool(data.get("is_default", False)),
            "is_enabled": bool(data.get("is_enabled", True)),
            "custom_headers": data.get("custom_headers", {}),
            "metadata": data.get("metadata", {}),
            "owner_id": owner,
        }
        return self.store.insert(record, owner_id=owner)

    def get_provider(self, provider_id: str, include_raw_key: bool = False, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieve provider record by ID."""
        return self.store.get(provider_id, include_raw_key=include_raw_key, owner_id=owner_id)

    def get_by_name(self, name: str, include_raw_key: bool = False, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieve provider record by name."""
        return self.store.get_by_name(name, include_raw_key=include_raw_key, owner_id=owner_id)

    def list_providers(self, include_raw_keys: bool = False, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all registered providers."""
        return self.store.list_all(include_raw_keys=include_raw_keys, owner_id=owner_id)

    def update_provider(self, provider_id: str, updates: Dict[str, Any], owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Update provider settings."""
        if "name" in updates and not updates["name"].strip():
            raise ValueError("Provider name cannot be empty.")
        if "provider_type" in updates:
            ptype = updates["provider_type"].upper()
            if ptype not in SUPPORTED_TYPES:
                raise ValueError(f"Unsupported provider_type: '{ptype}'.")
            updates["provider_type"] = ptype

        return self.store.update(provider_id, updates, owner_id=owner_id)

    def delete_provider(self, provider_id: str, owner_id: Optional[str] = None) -> bool:
        """Delete a provider by ID."""
        return self.store.delete(provider_id, owner_id=owner_id)

    def duplicate_provider(self, provider_id: str, new_name: Optional[str] = None, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Duplicate an existing provider record."""
        orig = self.store.get(provider_id, include_raw_key=True, owner_id=owner_id)
        if not orig:
            return None

        dup_name = new_name or f"{orig['name']} (Copy)"
        counter = 1
        while self.store.get_by_name(dup_name, owner_id=owner_id):
            counter += 1
            dup_name = f"{orig['name']} (Copy {counter})"

        dup_data = {
            "name": dup_name,
            "provider_type": orig["provider_type"],
            "base_url": orig["base_url"],
            "api_key": orig.get("api_key", ""),
            "default_model": orig["default_model"],
            "available_models": copy.deepcopy(orig.get("available_models", [])),
            "is_default": False,
            "is_enabled": orig.get("is_enabled", True),
            "custom_headers": copy.deepcopy(orig.get("custom_headers", {})),
            "metadata": copy.deepcopy(orig.get("metadata", {})),
            "owner_id": owner_id or orig.get("owner_id", ""),
        }
        return self.create_provider(dup_data, owner_id=owner_id)

    def set_default(self, provider_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Set a provider as the default provider."""
        return self.store.set_default(provider_id, owner_id=owner_id)

    def set_enabled(self, provider_id: str, enabled: bool, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Enable or disable a provider."""
        return self.store.set_enabled(provider_id, enabled, owner_id=owner_id)

    def get_default_provider(self, include_raw_key: bool = False, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get the current default provider if any is enabled."""
        all_provs = self.store.list_all(include_raw_keys=include_raw_key, owner_id=owner_id)
        for p in all_provs:
            if p.get("is_default") and p.get("is_enabled"):
                return p
        return None

    def build_instance(self, provider_record: Dict[str, Any], target_model: Optional[str] = None) -> BaseModelProvider:
        """Construct a live BaseModelProvider instance from a registry record."""
        ptype = provider_record.get("provider_type", "OPENAI_COMPATIBLE").upper()
        raw_key = provider_record.get("api_key", "")
        if not raw_key and "api_key_encrypted" in provider_record:
            raw_key = decrypt_api_key(provider_record["api_key_encrypted"])

        base_url = provider_record.get("base_url", "").strip()
        model = target_model or provider_record.get("default_model") or "auto"
        headers = provider_record.get("custom_headers", {})

        if ptype == "ANTHROPIC_COMPATIBLE":
            return AnthropicRuntimeProvider(
                api_key=raw_key,
                base_url=base_url or "https://api.anthropic.com",
                model=model if model != "auto" else "claude-3-5-sonnet-20241022",
                custom_headers=headers,
            )
        elif ptype == "CHAT_COMPATIBLE":
            return GenericChatProvider(
                api_key=raw_key,
                base_url=base_url,
                model=model if model != "auto" else "gpt-4o",
                custom_headers=headers,
            )
        else:  # OPENAI_COMPATIBLE default
            return OpenAIRuntimeProvider(
                api_key=raw_key,
                base_url=base_url or "https://api.openai.com/v1",
                model=model if model != "auto" else "gpt-4o",
                custom_headers=headers,
            )

    def health_check(self, provider_id: str, owner_id: Optional[str] = None) -> Dict[str, Any]:
        """Perform live health check for specified provider."""
        record = self.store.get(provider_id, include_raw_key=True, owner_id=owner_id)
        if not record:
            return {
                "status": "NOT_FOUND",
                "latency_ms": 0.0,
                "diagnostics": f"Provider '{provider_id}' not found in registry.",
            }

        try:
            instance = self.build_instance(record)
            return instance.health_check()
        except Exception as exc:
            logger.error("Health check execution failed for %s: %s", provider_id, exc)
            return {
                "status": "INVALID_CONFIGURATION",
                "latency_ms": 0.0,
                "diagnostics": f"Failed to initialize provider: {exc}",
            }
