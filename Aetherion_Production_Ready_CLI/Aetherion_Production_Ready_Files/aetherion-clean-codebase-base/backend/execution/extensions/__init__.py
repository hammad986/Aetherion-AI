"""
backend/execution/extensions — Secure Plugins & Skills Ecosystem for Aetherion.
"""
from __future__ import annotations

from .container import ContainerWorker, is_docker_available
from .fetcher import SourceFetcher
from .manager import ExtensionManager
from .models import (
    ALL_PERMISSIONS,
    ExtensionStatus,
    ExtensionTrustLevel,
    HealthStatus,
    PermissionEnum,
    PluginManifest,
    PluginToolDefinition,
    SkillManifest,
)
from .policy import ExtensionSecurityPolicy
from .protocol import ExtensionExecutionRequest, ExtensionExecutionResponse
from .runner import ExtensionRunner, SubprocessWorker
from .security import (
    ExtensionSecurityException,
    analyze_permissions,
    calculate_content_hash,
    scan_and_extract_archive,
)
from .skill_engine import SkillEngine
from .store import ExtensionStore
from .tool_bridge import PluginToolBridge

__all__ = [
    "ALL_PERMISSIONS",
    "ContainerWorker",
    "ExtensionExecutionRequest",
    "ExtensionExecutionResponse",
    "ExtensionManager",
    "ExtensionRunner",
    "ExtensionSecurityException",
    "ExtensionSecurityPolicy",
    "ExtensionStatus",
    "ExtensionStore",
    "ExtensionTrustLevel",
    "HealthStatus",
    "PermissionEnum",
    "PluginManifest",
    "PluginToolBridge",
    "PluginToolDefinition",
    "SkillEngine",
    "SkillManifest",
    "SourceFetcher",
    "SubprocessWorker",
    "analyze_permissions",
    "calculate_content_hash",
    "is_docker_available",
    "scan_and_extract_archive",
]
