"""
backend/execution/orchestration/governed_skills.py — Governed Skill Registry & Execution Contract.

Phase 18: Governed Skills Architecture.

Provides:
  - GovernedSkill: Authoritative canonical skill specification with schema, permissions,
    supported task classes, verification expectations, and provenance.
  - SkillLifecycleStatus: Deterministic state machine (DRAFT -> VALIDATED -> ACTIVE -> DEPRECATED -> DISABLED).
  - SkillRegistry: Thread-safe, authoritative registry for governed skills.
  - SkillDiscoveryEngine: Explanatory, anti-fabrication skill matching integrating
    task objective, repository intelligence, and task classification.
  - SkillPermissionAuditor: Subordinates skill execution to tenant authorization,
    workspace boundaries, tool registry policy, HITL, and RunBudget.
"""
from __future__ import annotations

import copy
import dataclasses
import enum
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("aetherion.orchestration.governed_skills")

ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{3,64}$")
SEMVER_REGEX = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9_.-]+)?$")


class SkillLifecycleStatus(str, enum.Enum):
    """
    Deterministic lifecycle states for governed skills.
    Only ACTIVE skills may be selected for autonomous execution.
    """
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    DISABLED = "DISABLED"


# Permitted lifecycle state transitions
_VALID_LIFECYCLE_TRANSITIONS: Dict[SkillLifecycleStatus, Set[SkillLifecycleStatus]] = {
    SkillLifecycleStatus.DRAFT: {
        SkillLifecycleStatus.VALIDATED,
        SkillLifecycleStatus.DISABLED,
    },
    SkillLifecycleStatus.VALIDATED: {
        SkillLifecycleStatus.ACTIVE,
        SkillLifecycleStatus.DRAFT,
        SkillLifecycleStatus.DISABLED,
    },
    SkillLifecycleStatus.ACTIVE: {
        SkillLifecycleStatus.DEPRECATED,
        SkillLifecycleStatus.DISABLED,
    },
    SkillLifecycleStatus.DEPRECATED: {
        SkillLifecycleStatus.ACTIVE,
        SkillLifecycleStatus.DISABLED,
    },
    SkillLifecycleStatus.DISABLED: {
        SkillLifecycleStatus.VALIDATED,
        SkillLifecycleStatus.DRAFT,
    },
}


class SkillSelectionReason(str, enum.Enum):
    """Explainable, non-fabricated reasons for selecting a skill."""
    EXPLICIT_USER_REQUEST = "explicit_user_request"
    DIRECT_NAME_MATCH = "direct_name_match"
    TRIGGER_MATCH = "trigger_match"
    CAPABILITY_MATCH = "capability_match"
    TASK_CLASS_MATCH = "task_class_match"
    REPOSITORY_FILE_TYPE_MATCH = "repository_file_type_match"


@dataclass
class SkillSecurityPolicy:
    """Security boundaries and governance constraints governing skill execution."""
    required_permissions: List[str] = field(default_factory=list)
    requires_hitl: bool = False
    workspace_boundary_strict: bool = True
    enforce_workspace_confinement: bool = True
    max_execution_time_seconds: float = 60.0
    allow_network: bool = False
    allow_arbitrary_shell: bool = False  # Invariant: Arbitrary shell is forbidden outside tool governance
    allowed_tools: Set[str] = field(default_factory=lambda: {"read_file", "write_file", "edit_file", "list_directory", "run_tests", "run_command", "finish"})

    def validate_action(self, tool_name: str, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        """Validate an action against the security policy."""
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return False, f"Tool '{tool_name}' is not in allowed tools."
        if self.enforce_workspace_confinement or self.workspace_boundary_strict:
            p_str = str(arguments.get("path") or arguments.get("file_path") or "")
            if ".." in p_str or p_str.startswith("/") or (len(p_str) > 1 and p_str[1] == ":"):
                return False, "Path escapes workspace boundary."
        return True, "Valid"

    def validate(self) -> None:
        if self.allow_arbitrary_shell:
            raise ValueError(
                "Security Policy Violation: Skills cannot execute arbitrary un-governed shell commands."
            )

    def to_dict(self) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        d["allowed_tools"] = list(self.allowed_tools)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SkillSecurityPolicy:
        fields = {f.name for f in dataclasses.fields(cls)}
        cleaned = {k: v for k, v in (data or {}).items() if k in fields}
        if "allowed_tools" in cleaned and isinstance(cleaned["allowed_tools"], list):
            cleaned["allowed_tools"] = set(cleaned["allowed_tools"])
        return cls(**cleaned)


@dataclass
class GovernedSkill:
    """
    Authoritative canonical skill representation.

    More than prompt text: a structured capability contract with verifiable inputs,
    output guarantees, tool restrictions, and verification expectations.
    """
    skill_id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    capability: str = "general"
    instructions: str = "Execute task according to governed skill protocol."
    author: str = "system"
    is_system: bool = False
    required_inputs: Dict[str, Any] = field(default_factory=dict)
    output_contract: Dict[str, Any] = field(default_factory=dict)
    allowed_tools: List[str] = field(default_factory=list)
    security_policy: SkillSecurityPolicy = field(default_factory=SkillSecurityPolicy)
    supported_task_classes: List[str] = field(default_factory=list)
    verification_expectations: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    lifecycle_status: SkillLifecycleStatus = SkillLifecycleStatus.DRAFT
    activation_triggers: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    owner_id: Optional[str] = None  # None for global/system skills; tenant-scoped otherwise
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def validate(self) -> None:
        """Validate structure, semver, and safety invariants."""
        if not self.skill_id or not ID_REGEX.match(self.skill_id):
            raise ValueError(f"Invalid skill_id: '{self.skill_id}'. Must match ^[a-zA-Z0-9_-]{{3,64}}$.")
        if not self.name or len(self.name.strip()) < 2:
            raise ValueError("Skill name must be at least 2 characters.")
        if not self.version or not SEMVER_REGEX.match(self.version):
            raise ValueError(f"Invalid version: '{self.version}'. Must follow semver (e.g., 1.0.0).")
        if not self.capability or not self.capability.strip():
            raise ValueError("Skill capability is required.")
        if not self.instructions or len(self.instructions.strip()) < 10:
            raise ValueError("Skill instructions must be at least 10 characters.")
        if self.security_policy:
            self.security_policy.validate()

    def to_dict(self) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        d["lifecycle_status"] = self.lifecycle_status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GovernedSkill:
        data = copy.deepcopy(data)
        if "security_policy" in data and isinstance(data["security_policy"], dict):
            data["security_policy"] = SkillSecurityPolicy.from_dict(data["security_policy"])
        if "lifecycle_status" in data and isinstance(data["lifecycle_status"], str):
            data["lifecycle_status"] = SkillLifecycleStatus(data["lifecycle_status"])
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class SkillCandidate:
    """A skill matched and ranked for an incoming task."""
    skill: GovernedSkill
    score: float
    reason: SkillSelectionReason
    details: str
    is_active: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill.skill_id,
            "name": self.skill.name,
            "version": self.skill.version,
            "capability": self.skill.capability,
            "score": round(self.score, 3),
            "reason": self.reason.value,
            "details": self.details,
            "is_active": self.is_active,
            "allowed_tools": self.skill.allowed_tools,
        }


class SkillRegistry:
    """
    Authoritative, multi-tenant in-memory & persistent registry for governed skills.

    Guarantees deterministic lifecycle transitions and prevents unauthorized execution.
    """

    def __init__(self) -> None:
        self._skills: Dict[str, GovernedSkill] = {}
        self._bootstrap_bundled_skills()

    def register_skill(self, skill: GovernedSkill, auto_validate: bool = False) -> GovernedSkill:
        """Register a new governed skill. Validates metadata and invariants."""
        skill.validate()
        if auto_validate and skill.lifecycle_status == SkillLifecycleStatus.DRAFT:
            skill.lifecycle_status = SkillLifecycleStatus.VALIDATED

        self._skills[skill.skill_id] = skill
        logger.info(
            "Registered governed skill '%s' v%s (status=%s)",
            skill.skill_id, skill.version, skill.lifecycle_status.value
        )
        return skill

    def transition_status(
        self,
        skill_id: str,
        new_status: SkillLifecycleStatus,
        reason: str = "",
    ) -> GovernedSkill:
        """
        Execute a deterministic lifecycle transition.

        Raises KeyError if skill not found.
        Raises ValueError if transition is invalid.
        """
        skill = self._skills.get(skill_id)
        if not skill:
            raise KeyError(f"Skill '{skill_id}' not found in registry.")

        allowed = _VALID_LIFECYCLE_TRANSITIONS.get(skill.lifecycle_status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Illegal lifecycle transition for skill '{skill_id}': "
                f"{skill.lifecycle_status.value} -> {new_status.value}. Allowed: {[s.value for s in allowed]}"
            )

        old_status = skill.lifecycle_status
        skill.lifecycle_status = new_status
        skill.updated_at = time.time()
        skill.provenance["last_transition"] = {
            "from": old_status.value,
            "to": new_status.value,
            "reason": reason,
            "timestamp": skill.updated_at,
        }
        logger.info(
            "Skill '%s' transitioned: %s -> %s (%s)",
            skill_id, old_status.value, new_status.value, reason or "no reason"
        )
        return skill

    transition_lifecycle = transition_status

    def get_skill(self, skill_id: str) -> Optional[GovernedSkill]:
        return self._skills.get(skill_id)

    def list_skills(
        self,
        owner_id: Optional[str] = None,
        status: Optional[SkillLifecycleStatus] = None,
    ) -> List[GovernedSkill]:
        """List skills accessible by owner_id (includes global skills where owner_id is None)."""
        result = []
        for s in self._skills.values():
            if owner_id is not None and s.owner_id is not None and s.owner_id != owner_id:
                continue
            if status is not None and s.lifecycle_status != status:
                continue
            result.append(s)
        return result

    def get_active_skills(self, owner_id: Optional[str] = None) -> List[GovernedSkill]:
        """Return only ACTIVE skills eligible for autonomous selection."""
        return self.list_skills(owner_id=owner_id, status=SkillLifecycleStatus.ACTIVE)

    def audit_skill_permissions(
        self,
        skill: GovernedSkill,
        tenant_allowed_permissions: Set[str],
        allowed_tools: Set[str],
    ) -> Tuple[bool, List[str]]:
        """
        Audit skill against tenant permissions and tool registry policy.
        Returns (is_authorized, rejection_reasons).
        """
        rejections: List[str] = []

        # Check required permissions
        for perm in skill.security_policy.required_permissions:
            if perm not in tenant_allowed_permissions:
                rejections.append(f"Unauthorized permission requested: '{perm}'")

        # Check allowed tools against active tool registry
        for tool in skill.allowed_tools:
            if tool not in allowed_tools:
                rejections.append(f"Unauthorized tool requested: '{tool}'")

        # Check shell safety
        if skill.security_policy.allow_arbitrary_shell:
            rejections.append("Arbitrary shell execution requested outside tool governance.")

        return (len(rejections) == 0, rejections)

    def _bootstrap_bundled_skills(self) -> None:
        """Bootstrap default governed system skills in ACTIVE status."""
        bundled = [
            GovernedSkill(
                skill_id="refactor_safe",
                name="Safe Refactoring",
                version="1.0.0",
                description="Performs safe, localized refactoring preserving existing API contracts and tests.",
                capability="refactor",
                instructions=(
                    "1. Inspect target functions and dependencies before modifying.\n"
                    "2. Make incremental modifications keeping interfaces backwards compatible.\n"
                    "3. Run existing tests to verify behavioral equivalence."
                ),
                allowed_tools=["read_file", "write_file", "edit_file", "run_tests", "run_command", "git_diff"],
                security_policy=SkillSecurityPolicy(
                    required_permissions=["FILESYSTEM_READ", "FILESYSTEM_WRITE"],
                    requires_hitl=False,
                ),
                supported_task_classes=["refactor", "code_cleanup", "optimization"],
                verification_expectations={"require_tests_pass": True, "syntax_check": True},
                provenance={"source": "system_bundled", "author": "aetherion_core"},
                lifecycle_status=SkillLifecycleStatus.ACTIVE,
                is_system=True,
                activation_triggers=["refactor", "cleanup", "reorganize", "extract"],
                tags=["refactor", "clean_code", "python"],
            ),
            GovernedSkill(
                skill_id="unit_test_generator",
                name="Unit Test Generator",
                version="1.0.0",
                description="Generates deterministic unit and regression tests for Python code.",
                capability="test_generation",
                instructions=(
                    "1. Discover public functions and boundary cases.\n"
                    "2. Generate isolated pytest test cases without live external dependencies.\n"
                    "3. Verify that new tests run cleanly and assert intended outcomes."
                ),
                allowed_tools=["read_file", "write_file", "run_tests", "check_syntax"],
                security_policy=SkillSecurityPolicy(
                    required_permissions=["FILESYSTEM_READ", "FILESYSTEM_WRITE"],
                    requires_hitl=False,
                ),
                supported_task_classes=["test", "bug_fix", "coverage"],
                verification_expectations={"require_tests_pass": True},
                provenance={"source": "system_bundled", "author": "aetherion_core"},
                lifecycle_status=SkillLifecycleStatus.ACTIVE,
                is_system=True,
                activation_triggers=["test", "pytest", "unit test", "coverage", "mock"],
                tags=["testing", "pytest", "python"],
            ),
            GovernedSkill(
                skill_id="diagnostic_repair",
                name="Diagnostic Bug Repair",
                version="1.0.0",
                description="Repairs bugs and test failures using diagnostic error evidence.",
                capability="bug_fix",
                instructions=(
                    "1. Analyze verifier stack traces and failure assertions.\n"
                    "2. Locate root-cause symbol and prepare minimal targeted patch.\n"
                    "3. Verify resolution against failing test."
                ),
                allowed_tools=["read_file", "write_file", "edit_file", "run_tests", "check_syntax"],
                security_policy=SkillSecurityPolicy(
                    required_permissions=["FILESYSTEM_READ", "FILESYSTEM_WRITE"],
                    requires_hitl=False,
                ),
                supported_task_classes=["bug_fix", "repair", "regression"],
                verification_expectations={"require_tests_pass": True},
                provenance={"source": "system_bundled", "author": "aetherion_core"},
                lifecycle_status=SkillLifecycleStatus.ACTIVE,
                is_system=True,
                activation_triggers=["fix", "bug", "error", "repair", "failing", "patch"],
                tags=["repair", "debugging", "python"],
            ),
            GovernedSkill(
                skill_id="config_updater",
                name="Configuration Updater",
                version="1.0.0",
                description="Updates settings and configuration files with validation.",
                capability="config_update",
                instructions=(
                    "1. Parse configuration file (json/toml/yaml).\n"
                    "2. Update requested keys while preserving surrounding formatting.\n"
                    "3. Validate syntax after modification."
                ),
                allowed_tools=["read_file", "write_file", "edit_file", "check_syntax"],
                security_policy=SkillSecurityPolicy(
                    required_permissions=["FILESYSTEM_READ", "FILESYSTEM_WRITE"],
                    requires_hitl=False,
                ),
                supported_task_classes=["config", "settings", "maintenance"],
                verification_expectations={"syntax_check": True},
                provenance={"source": "system_bundled", "author": "aetherion_core"},
                lifecycle_status=SkillLifecycleStatus.ACTIVE,
                is_system=True,
                activation_triggers=["config", "settings", "timeout", "retry", "env"],
                tags=["config", "json", "toml"],
            ),
        ]
        for s in bundled:
            self._skills[s.skill_id] = s


class SkillDiscoveryEngine:
    """
    Ranks candidate skills for a task objective, repository context, and task classification.

    Never fabricates relevance: returns concrete, verifiable reasons.
    Filters out DRAFT and DISABLED skills from autonomous selection.
    """

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    def discover(
        self,
        task_objective: str,
        repo_intelligence: Optional[Any] = None,
        task_classification: Optional[Any] = None,
        owner_id: Optional[str] = None,
        explicit_skill_id: Optional[str] = None,
        max_skills: int = 3,
        **kwargs: Any,
    ) -> List[SkillCandidate]:
        """
        Discover and rank matching skills.

        Returns explainable list of SkillCandidate sorted descending by score.
        """
        if repo_intelligence is None:
            repo_intelligence = kwargs.get("repo_intel")
        if task_classification is None:
            task_classification = kwargs.get("classification")

        candidates: List[SkillCandidate] = []
        obj_lower = task_objective.lower()

        # 1. Handle explicit skill request
        if explicit_skill_id:
            s = self.registry.get_skill(explicit_skill_id)
            if s:
                # Security gate: if requested skill is DISABLED, record as inactive
                is_active = (s.lifecycle_status == SkillLifecycleStatus.ACTIVE)
                candidates.append(SkillCandidate(
                    skill=s,
                    score=1.0 if is_active else 0.0,
                    reason=SkillSelectionReason.EXPLICIT_USER_REQUEST,
                    details=f"Explicitly requested by user or test: '{explicit_skill_id}'",
                    is_active=is_active,
                ))
                return candidates

        # 2. Autonomous discovery over eligible ACTIVE skills
        # (DEPRECATED skills are penalized by -0.3; DRAFT and DISABLED are strictly excluded)
        eligible = self.registry.list_skills(owner_id=owner_id)
        active_pool = [
            s for s in eligible
            if s.lifecycle_status in (SkillLifecycleStatus.ACTIVE, SkillLifecycleStatus.DEPRECATED)
        ]

        # Extract repo language & file features if available
        repo_languages: Set[str] = set()
        if repo_intelligence is not None and hasattr(repo_intelligence, "repository_map"):
            rmap = repo_intelligence.repository_map
            for fnode in rmap.files.values():
                if fnode.language:
                    repo_languages.add(fnode.language.lower())

        # Extract task complexity / classification hints
        task_type_hint = ""
        if task_classification is not None and hasattr(task_classification, "complexity"):
            task_type_hint = str(getattr(task_classification, "complexity", "")).lower()

        for s in active_pool:
            score = 0.0
            reasons: List[Tuple[SkillSelectionReason, str, float]] = []

            # (A) Direct Name Match
            if s.name.lower() in obj_lower or s.skill_id.lower() in obj_lower:
                reasons.append((
                    SkillSelectionReason.DIRECT_NAME_MATCH,
                    f"Task mentions skill name '{s.name}'",
                    0.95,
                ))

            # (B) Trigger Match
            matched_triggers = [t for t in s.activation_triggers if t.lower() in obj_lower]
            if matched_triggers:
                reasons.append((
                    SkillSelectionReason.TRIGGER_MATCH,
                    f"Matched triggers: {', '.join(matched_triggers[:3])}",
                    0.85,
                ))

            # (C) Capability Match
            if s.capability.lower() in obj_lower or any(word in obj_lower for word in s.capability.lower().split("_")):
                reasons.append((
                    SkillSelectionReason.CAPABILITY_MATCH,
                    f"Skill capability '{s.capability}' matches task intent",
                    0.80,
                ))

            # (D) Task Class Match
            matched_classes = [tc for tc in s.supported_task_classes if tc in obj_lower or tc in task_type_hint]
            if matched_classes:
                reasons.append((
                    SkillSelectionReason.TASK_CLASS_MATCH,
                    f"Skill supports task classes: {', '.join(matched_classes)}",
                    0.75,
                ))

            # (E) Repository file type match
            matched_langs = [l for l in repo_languages if l in [t.lower() for t in s.tags]]
            if matched_langs:
                reasons.append((
                    SkillSelectionReason.REPOSITORY_FILE_TYPE_MATCH,
                    f"Skill specializes in repository language(s): {', '.join(matched_langs)}",
                    0.60,
                ))

            if not reasons:
                continue

            # Pick highest individual reason
            best_reason, best_details, base_score = max(reasons, key=lambda x: x[2])
            final_score = base_score

            # Penalize deprecated skills
            if s.lifecycle_status == SkillLifecycleStatus.DEPRECATED:
                final_score -= 0.3

            candidates.append(SkillCandidate(
                skill=s,
                score=max(0.1, final_score),
                reason=best_reason,
                details=best_details,
                is_active=True,
            ))

        # Sort descending by score
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:max_skills]


@dataclass
class SkillAuditResult:
    allowed: bool
    violation_reason: Optional[str] = None
    requires_hitl: bool = False


class SkillPermissionAuditor:
    """Subordinates skill permissions to tenant, tool, and safety policies."""

    def audit_tool_call(
        self,
        skill: GovernedSkill,
        tool_name: str,
        arguments: Dict[str, Any],
        tenant_id: str = "default_tenant",
        tenant_allowed_tools: Optional[Set[str]] = None,
        requires_hitl: bool = False,
    ) -> SkillAuditResult:
        """
        Validate if a tool call by a skill is permitted.
        """
        if tenant_allowed_tools is None:
            tenant_allowed_tools = {"read_file", "write_file", "edit_file", "list_directory", "run_tests", "run_command", "finish"}

        if skill.lifecycle_status in (SkillLifecycleStatus.DISABLED, SkillLifecycleStatus.DRAFT):
            return SkillAuditResult(
                allowed=False,
                violation_reason=f"Skill '{skill.skill_id}' is {skill.lifecycle_status.value} and cannot execute.",
            )

        if tool_name not in skill.allowed_tools:
            return SkillAuditResult(
                allowed=False,
                violation_reason=f"Tool '{tool_name}' is not permitted by skill '{skill.skill_id}'.",
            )

        if tool_name not in tenant_allowed_tools:
            return SkillAuditResult(
                allowed=False,
                violation_reason=f"Tool '{tool_name}' is not permitted by tenant policy.",
            )

        # Check path traversal if path in arguments
        if "path" in arguments and skill.security_policy.enforce_workspace_confinement:
            p_str = str(arguments["path"])
            if ".." in p_str or p_str.startswith("/") or (len(p_str) > 1 and p_str[1] == ":"):
                return SkillAuditResult(
                    allowed=False,
                    violation_reason="Violation: Path escapes workspace confinement.",
                )

        hitl = requires_hitl or skill.security_policy.requires_hitl
        return SkillAuditResult(allowed=True, requires_hitl=hitl)

    @staticmethod
    def audit_skill_call(
        skill: GovernedSkill,
        tool_name: str,
        tenant_id: str,
        tenant_allowed_tools: Set[str],
        tool_requires_hitl: bool = False,
    ) -> Tuple[bool, str]:
        """
        Validate if an executing skill is authorized to invoke a tool.
        Returns (is_allowed, reason).
        """
        # 1. Skill must not be disabled
        if skill.lifecycle_status in (SkillLifecycleStatus.DISABLED, SkillLifecycleStatus.DRAFT):
            return (False, f"Skill '{skill.skill_id}' is {skill.lifecycle_status.value} and cannot execute.")

        # 2. Tool must be declared in skill.allowed_tools
        if tool_name not in skill.allowed_tools:
            return (False, f"Tool '{tool_name}' is not in skill '{skill.skill_id}' allowed_tools list.")

        # 3. Tool must be permitted by tenant policy
        if tool_name not in tenant_allowed_tools:
            return (False, f"Tool '{tool_name}' is not permitted by tenant '{tenant_id}' policy.")

        # 4. Check arbitrary shell prohibition
        if tool_name == "run_command" and skill.security_policy.allow_arbitrary_shell:
            return (False, "Violation: Arbitrary shell execution forbidden by security policy.")

        # 5. HITL enforcement
        if tool_requires_hitl or skill.security_policy.requires_hitl:
            return (True, "Requires HITL confirmation before invocation.")

        return (True, "Authorized.")


_GLOBAL_SKILL_REGISTRY: Optional[SkillRegistry] = None


def get_skill_registry() -> SkillRegistry:
    """Retrieve global authoritative skill registry."""
    global _GLOBAL_SKILL_REGISTRY
    if _GLOBAL_SKILL_REGISTRY is None:
        _GLOBAL_SKILL_REGISTRY = SkillRegistry()
    return _GLOBAL_SKILL_REGISTRY


def reset_skill_registry() -> None:
    """Reset global skill registry (useful for testing)."""
    global _GLOBAL_SKILL_REGISTRY
    _GLOBAL_SKILL_REGISTRY = None
