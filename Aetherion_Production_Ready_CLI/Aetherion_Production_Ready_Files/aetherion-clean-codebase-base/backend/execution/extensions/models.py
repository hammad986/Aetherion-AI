"""
backend/execution/extensions/models.py — Canonical Manifests, Permissions, and Extension Models.
"""
from __future__ import annotations

import dataclasses
import enum
import re
from typing import Any, Dict, List, Optional


class PermissionEnum(str, enum.Enum):
    """Explicit security permissions required by plugins or skills."""
    FILESYSTEM_READ = "FILESYSTEM_READ"
    FILESYSTEM_WRITE = "FILESYSTEM_WRITE"
    SHELL_EXECUTION = "SHELL_EXECUTION"
    NETWORK_ACCESS = "NETWORK_ACCESS"
    GIT_ACCESS = "GIT_ACCESS"
    PROCESS_EXECUTION = "PROCESS_EXECUTION"
    SECRET_ACCESS = "SECRET_ACCESS"
    MODEL_ACCESS = "MODEL_ACCESS"
    WORKSPACE_ACCESS = "WORKSPACE_ACCESS"


ALL_PERMISSIONS = {p.value for p in PermissionEnum}


class ExtensionTrustLevel(str, enum.Enum):
    """Trust tier assigned to an extension."""
    UNTRUSTED = "UNTRUSTED"
    TRUSTED_LOCAL = "TRUSTED_LOCAL"
    SYSTEM = "SYSTEM"


class ExtensionStatus(str, enum.Enum):
    """Lifecycle state of an extension."""
    PENDING_APPROVAL = "PENDING_APPROVAL"
    INSTALLED = "INSTALLED"
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


class HealthStatus(str, enum.Enum):
    """Health diagnostic status."""
    VALID = "VALID"
    INVALID = "INVALID"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INCOMPATIBLE = "INCOMPATIBLE"
    ERROR = "ERROR"
    DISABLED = "DISABLED"


ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{3,64}$")
SEMVER_REGEX = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9_.-]+)?$")


RESERVED_CORE_TOOLS = {
    "inspect_environment",
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "search_files",
    "delete_file",
    "run_command",
    "run_tests",
    "check_syntax",
    "git_status",
    "git_diff",
    "git_commit",
    "finish",
}


@dataclasses.dataclass
class PluginToolDefinition:
    """A tool provided by a plugin."""
    name: str
    description: str
    parameters: Dict[str, Any]
    required_permissions: List[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "required_permissions": self.required_permissions,
        }


@dataclasses.dataclass
class PluginManifest:
    """Canonical manifest for executable plugins."""
    id: str = ""
    name: str = ""
    version: str = ""
    description: str = ""
    author: str = "community"
    license: str = "MIT"
    entrypoint: str = "main.py:run"
    runtime: str = "python"
    repository: str = ""
    required_permissions: List[str] = dataclasses.field(default_factory=list)
    provided_tools: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    required_dependencies: List[str] = dataclasses.field(default_factory=list)
    compatible_aetherion_version: str = ">=2.0.0"
    homepage: str = ""
    documentation: str = ""
    icon: str = ""
    tags: List[str] = dataclasses.field(default_factory=list)
    configuration_schema: Dict[str, Any] = dataclasses.field(default_factory=dict)
    trust_level: str = ExtensionTrustLevel.UNTRUSTED.value

    def validate(self) -> None:
        """Validate required fields and format; raise ValueError on malformed data."""
        if not self.id:
            raise ValueError("id is required.")
        if not ID_REGEX.match(self.id):
            raise ValueError(f"Invalid plugin id: '{self.id}'. Must match [a-zA-Z0-9_-]{{3,64}}.")
        if not self.name or len(self.name.strip()) < 2:
            raise ValueError("Plugin name is required and must be at least 2 characters.")
        if not self.version:
            raise ValueError("version is required.")
        if not SEMVER_REGEX.match(self.version):
            raise ValueError(f"Invalid plugin version: '{self.version}'. Must follow semantic versioning (e.g. 1.0.0).")
        if not self.entrypoint:
            raise ValueError("Plugin entrypoint is required.")
        if self.trust_level not in {t.value for t in ExtensionTrustLevel}:
            raise ValueError(f"Invalid plugin trust_level: '{self.trust_level}'. Valid values: {[t.value for t in ExtensionTrustLevel]}")

        # Validate requested permissions
        for perm in self.required_permissions:
            if perm not in ALL_PERMISSIONS:
                raise ValueError(f"Unknown permission requested: '{perm}'. Valid permissions: {sorted(ALL_PERMISSIONS)}")

        # Validate provided tools against core collisions
        for tool in self.provided_tools:
            if not isinstance(tool, dict):
                raise ValueError("Each provided_tool entry must be a dictionary.")
            t_name = tool.get("name", "").strip()
            if not t_name:
                raise ValueError("Plugin tool definition is missing 'name'.")
            if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", t_name):
                raise ValueError(f"Invalid plugin tool name: '{t_name}'. Must match [a-zA-Z0-9_-]{{1,64}}.")
            if t_name in RESERVED_CORE_TOOLS:
                raise ValueError(f"Plugin tool name '{t_name}' collides with a built-in core tool and cannot be overridden.")

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PluginManifest:
        valid_fields = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        manifest = cls(**filtered)
        manifest.validate()
        return manifest


@dataclasses.dataclass
class SkillManifest:
    """Canonical manifest for reusable agent instruction packages/skills."""
    id: str = ""
    name: str = ""
    version: str = ""
    description: str = ""
    author: str = "community"
    instructions: str = ""
    required_tools: List[str] = dataclasses.field(default_factory=list)
    required_permissions: List[str] = dataclasses.field(default_factory=list)
    compatible_aetherion_version: str = ">=2.0.0"
    tags: List[str] = dataclasses.field(default_factory=list)
    examples: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    activation_triggers: List[str] = dataclasses.field(default_factory=list)
    model_requirements: Dict[str, Any] = dataclasses.field(default_factory=dict)
    input_schema: Dict[str, Any] = dataclasses.field(default_factory=dict)
    output_schema: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def validate(self) -> None:
        """Validate required skill fields."""
        if not self.id:
            raise ValueError("id is required.")
        if not ID_REGEX.match(self.id):
            raise ValueError(f"Invalid skill id: '{self.id}'. Must match [a-zA-Z0-9_-]{{3,64}}.")
        if not self.name or len(self.name.strip()) < 2:
            raise ValueError("Skill name is required and must be at least 2 characters.")
        if not self.version:
            raise ValueError("version is required.")
        if not SEMVER_REGEX.match(self.version):
            raise ValueError(f"Invalid skill version: '{self.version}'. Must follow semantic versioning (e.g. 1.0.0).")
        if not self.instructions or len(self.instructions.strip()) < 10:
            raise ValueError("Skill instructions are required (minimum 10 characters).")

        for perm in self.required_permissions:
            if perm not in ALL_PERMISSIONS:
                raise ValueError(f"Unknown permission requested: '{perm}'. Valid permissions: {sorted(ALL_PERMISSIONS)}")

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SkillManifest:
        valid_fields = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        manifest = cls(**filtered)
        manifest.validate()
        return manifest
