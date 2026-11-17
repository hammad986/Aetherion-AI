"""
backend/execution/providers/__init__.py — Provider subsystem package.
"""
from .anthropic import AnthropicRuntimeProvider
from .base import (
    BaseModelProvider,
    ModelResponse,
    ProviderCapabilities,
    ProviderUnavailableException,
    ToolCall,
)
from .custom import (
    DeterministicTestProvider,
    GenericChatProvider,
    OfflineFallbackProvider,
)
from .openai import OpenAIRuntimeProvider
from .registry import ProviderRegistry
from .router import ModelRouter, get_provider, get_router
from .store import (
    ProviderStore,
    decrypt_api_key,
    encrypt_api_key,
    mask_api_key,
)

__all__ = [
    "BaseModelProvider",
    "ModelResponse",
    "ToolCall",
    "ProviderCapabilities",
    "ProviderUnavailableException",
    "AnthropicRuntimeProvider",
    "OpenAIRuntimeProvider",
    "DeterministicTestProvider",
    "OfflineFallbackProvider",
    "GenericChatProvider",
    "ProviderStore",
    "ProviderRegistry",
    "ModelRouter",
    "get_provider",
    "get_router",
    "mask_api_key",
    "encrypt_api_key",
    "decrypt_api_key",
]
