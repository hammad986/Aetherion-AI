"""
backend/execution/agent_runtime/__init__.py — Phase 2 Authoritative Compatibility Package.

Preserves 100% backward compatibility for all existing callers, CLI commands,
TUI components, and integration tests by re-exporting symbols from the decomposed
subpackages (sandbox, tools, verification, providers, agent_runtime).
"""
from __future__ import annotations

import logging

from ..providers.anthropic import AnthropicRuntimeProvider
from ..providers.base import (
    BaseModelProvider,
    ModelResponse,
    ProviderCapabilities,
    ProviderUnavailableException,
    ToolCall,
)
from ..providers.custom import (
    DeterministicTestProvider,
    GenericChatProvider,
    OfflineFallbackProvider,
)
from ..providers.openai import OpenAIRuntimeProvider
from ..providers.registry import ProviderRegistry
from ..providers.router import ModelRouter, get_provider, get_router
from ..providers.store import ProviderStore, decrypt_api_key, encrypt_api_key, mask_api_key
from ..sandbox.boundary import (
    AgentRuntimeError,
    ExecutionCancelledException,
    PolicyViolationException,
    SandboxBoundaryException,
    WorkspaceBoundary,
)
from ..tools.registry import AgentToolRegistry
from ..tools.schemas import (
    ANTHROPIC_TOOLS,
    ToolRequest,
    ToolResult,
    get_anthropic_tools,
    get_openai_tools,
)
from ..verification.engine import VerificationEngine
from .context import (
    ContextImmutableError,
    TaskExecutionContext,
    WorktreeRequiredError,
    attach_execution_worktree,
    cleanup_execution_worktree,
    get_execution_diff,
    promote_execution,
    reject_execution,
)
from .runtime import RealAgentRuntime
from .state import (
    NOT_VERIFIED,
    PARTIALLY_VERIFIED,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_EXECUTING,
    STATE_FAILED,
    STATE_INSPECTING,
    STATE_PLANNING,
    STATE_QUEUED,
    STATE_REPAIRING,
    STATE_REVIEW_PENDING,
    STATE_ACCEPTED,
    STATE_REJECTED,
    STATE_SUCCEEDED,
    STATE_VERIFYING,
    STATE_WAITING_FOR_APPROVAL,
    VERIFICATION_FAILED,
    VERIFIED,
)

logger = logging.getLogger("aetherion.agent_runtime")

__all__ = [
    # Exceptions
    "AgentRuntimeError",
    "SandboxBoundaryException",
    "PolicyViolationException",
    "ExecutionCancelledException",
    "ProviderUnavailableException",
    # Sandbox & Boundary
    "WorkspaceBoundary",
    # Tools
    "ToolRequest",
    "ToolResult",
    "AgentToolRegistry",
    "ANTHROPIC_TOOLS",
    "get_anthropic_tools",
    "get_openai_tools",
    # Verification
    "VerificationEngine",
    "VERIFIED",
    "PARTIALLY_VERIFIED",
    "NOT_VERIFIED",
    "VERIFICATION_FAILED",
    # Providers
    "BaseModelProvider",
    "ModelResponse",
    "ToolCall",
    "ProviderCapabilities",
    "DeterministicTestProvider",
    "OfflineFallbackProvider",
    "AnthropicRuntimeProvider",
    "OpenAIRuntimeProvider",
    "GenericChatProvider",
    "ProviderStore",
    "ProviderRegistry",
    "ModelRouter",
    "get_provider",
    "get_router",
    "mask_api_key",
    "encrypt_api_key",
    "decrypt_api_key",
    # Runtime & States
    "RealAgentRuntime",
    "TaskExecutionContext",
    "ContextImmutableError",
    "WorktreeRequiredError",
    "attach_execution_worktree",
    "cleanup_execution_worktree",
    "get_execution_diff",
    "promote_execution",
    "reject_execution",
    "STATE_QUEUED",
    "STATE_PLANNING",
    "STATE_INSPECTING",
    "STATE_EXECUTING",
    "STATE_WAITING_FOR_APPROVAL",
    "STATE_VERIFYING",
    "STATE_REPAIRING",
    "STATE_REVIEW_PENDING",
    "STATE_ACCEPTED",
    "STATE_REJECTED",
    "STATE_COMPLETED",
    "STATE_SUCCEEDED",
    "STATE_FAILED",
    "STATE_CANCELLED",
]
