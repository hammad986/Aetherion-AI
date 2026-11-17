"""
backend/execution/orchestration/task_classifier.py — Deterministic Task Classification Layer.

Classifies a task prompt along six orthogonal dimensions used by the execution policy
to select the appropriate model profile and RunBudget configuration.

Dimensions:
  - complexity   : TRIVIAL | MODERATE | COMPLEX | CRITICAL
  - risk         : LOW | MEDIUM | HIGH | CRITICAL
  - repo_impact  : NONE | LOCAL | MODULE | CROSS_MODULE | SYSTEMIC
  - verification_difficulty : EASY | MODERATE | HARD | MANUAL
  - tool_intensity : LIGHT | MODERATE | HEAVY
  - context_requirement : MINIMAL | STANDARD | EXTENDED | MAXIMUM
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger("aetherion.orchestration.classifier")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TaskComplexity(str, Enum):
    TRIVIAL = "TRIVIAL"
    MODERATE = "MODERATE"
    COMPLEX = "COMPLEX"
    CRITICAL = "CRITICAL"


class TaskRisk(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RepoImpact(str, Enum):
    NONE = "NONE"
    LOCAL = "LOCAL"
    MODULE = "MODULE"
    CROSS_MODULE = "CROSS_MODULE"
    SYSTEMIC = "SYSTEMIC"


class VerificationDifficulty(str, Enum):
    EASY = "EASY"
    MODERATE = "MODERATE"
    HARD = "HARD"
    MANUAL = "MANUAL"


class ToolIntensity(str, Enum):
    LIGHT = "LIGHT"
    MODERATE = "MODERATE"
    HEAVY = "HEAVY"


class ContextRequirement(str, Enum):
    MINIMAL = "MINIMAL"
    STANDARD = "STANDARD"
    EXTENDED = "EXTENDED"
    MAXIMUM = "MAXIMUM"


# ---------------------------------------------------------------------------
# Classification result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TaskClassification:
    """Immutable task classification result — all six dimensions plus rationale."""
    complexity: TaskComplexity
    risk: TaskRisk
    repo_impact: RepoImpact
    verification_difficulty: VerificationDifficulty
    tool_intensity: ToolIntensity
    context_requirement: ContextRequirement
    rationale: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, str]:
        return {
            "complexity": self.complexity.value,
            "risk": self.risk.value,
            "repo_impact": self.repo_impact.value,
            "verification_difficulty": self.verification_difficulty.value,
            "tool_intensity": self.tool_intensity.value,
            "context_requirement": self.context_requirement.value,
        }

    @property
    def overall_severity(self) -> int:
        """Scalar severity 0-12 derived from dimensions; used for profile selection."""
        complexity_score = {"TRIVIAL": 0, "MODERATE": 1, "COMPLEX": 2, "CRITICAL": 3}
        risk_score = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
        impact_score = {"NONE": 0, "LOCAL": 1, "MODULE": 2, "CROSS_MODULE": 3, "SYSTEMIC": 3}
        return (
            complexity_score.get(self.complexity.value, 1)
            + risk_score.get(self.risk.value, 1)
            + impact_score.get(self.repo_impact.value, 1)
        )


# ---------------------------------------------------------------------------
# Signal patterns (deterministic keyword heuristics)
# ---------------------------------------------------------------------------

_COMPLEXITY_SIGNALS: Dict[TaskComplexity, List[str]] = {
    TaskComplexity.CRITICAL: [
        "refactor entire", "rewrite", "migration", "architecture", "redesign",
        "overhaul", "complete", "from scratch", "systematic",
    ],
    TaskComplexity.COMPLEX: [
        "implement", "integrate", "multi-step", "pipeline", "orchestrat",
        "async", "concurrent", "distributed", "authentication", "authorization",
        "secure", "encrypt", "api endpoint", "database schema", "search",
        "algorithm", "optimize", "performance",
    ],
    TaskComplexity.MODERATE: [
        "add", "extend", "update", "modify", "change", "improve",
        "feature", "endpoint", "function", "class", "method", "test",
    ],
    TaskComplexity.TRIVIAL: [
        "fix typo", "rename", "format", "lint", "comment", "docstring",
        "print", "hello world", "simple", "basic", "trivial",
    ],
}

_RISK_SIGNALS: Dict[TaskRisk, List[str]] = {
    TaskRisk.CRITICAL: [
        "delete all", "drop database", "rm -rf", "wipe", "destroy",
        "production", "deploy", "release", "credentials", "secret",
        "password", "api key", "token", "privilege",
    ],
    TaskRisk.HIGH: [
        "delete", "remove", "truncate", "migrate", "schema change",
        "breaking change", "auth", "permission", "role", "security",
        "database", "sql", "payment", "money", "user data",
    ],
    TaskRisk.MEDIUM: [
        "modify", "update config", "change setting", "refactor",
        "api", "endpoint", "external", "service", "integration",
    ],
    TaskRisk.LOW: [
        "read", "display", "print", "log", "test", "check", "verify",
        "list", "show", "get", "fetch", "hello", "example",
    ],
}

_TOOL_INTENSITY_SIGNALS: Dict[ToolIntensity, List[str]] = {
    ToolIntensity.HEAVY: [
        "run tests", "benchmark", "profile", "build", "compile", "lint all",
        "search codebase", "grep", "find all", "scan", "analyze entire",
    ],
    ToolIntensity.MODERATE: [
        "test", "run", "execute", "read file", "write file", "edit",
        "create file", "check", "verify output",
    ],
    ToolIntensity.LIGHT: [
        "explain", "describe", "summarize", "plan", "propose", "list",
        "show me", "what is", "how to", "review",
    ],
}

_REPO_IMPACT_SIGNALS: Dict[RepoImpact, List[str]] = {
    RepoImpact.SYSTEMIC: [
        "all files", "entire codebase", "all modules", "global", "root",
        "base class", "foundation", "core",
    ],
    RepoImpact.CROSS_MODULE: [
        "multiple modules", "cross-module", "shared", "common", "library",
        "interface", "api contract", "protocol",
    ],
    RepoImpact.MODULE: [
        "module", "package", "component", "service", "handler", "middleware",
    ],
    RepoImpact.LOCAL: [
        "function", "method", "class", "file", "line", "specific",
    ],
    RepoImpact.NONE: [
        "explain", "summarize", "review", "analyse", "describe", "read only",
    ],
}


def _match_signals(
    prompt_lower: str,
    signals: Dict,
    default,
    ordered_keys: Sequence,
):
    """Return the highest-priority key whose signals appear in the prompt.

    Keys are evaluated in *ordered_keys* order (most severe first).
    """
    for key in ordered_keys:
        for signal in signals.get(key, []):
            if signal in prompt_lower:
                return key
    return default


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class TaskClassifier:
    """Deterministic, keyword-driven task classifier.

    All classification is rule-based and reproducible for a given prompt.
    No model call is made during classification.
    """

    def classify(
        self,
        prompt: str,
        hints: Optional[Dict[str, str]] = None,
    ) -> TaskClassification:
        """Classify the task and return an immutable TaskClassification.

        Args:
            prompt: The raw task prompt.
            hints: Optional caller-supplied overrides for any dimension
                   (e.g., {"risk": "HIGH"}).  Each override must be a valid
                   enum value string.
        """
        if not prompt or not str(prompt).strip():
            return self._default_classification()

        pl = prompt.lower()
        hints = hints or {}
        rationale: Dict[str, str] = {}

        # --- Complexity ---
        complexity_order = [
            TaskComplexity.CRITICAL,
            TaskComplexity.COMPLEX,
            TaskComplexity.MODERATE,
            TaskComplexity.TRIVIAL,
        ]
        complexity = _match_signals(pl, _COMPLEXITY_SIGNALS, TaskComplexity.MODERATE, complexity_order)
        rationale["complexity"] = f"keyword-match → {complexity.value}"
        if "complexity" in hints:
            try:
                complexity = TaskComplexity(hints["complexity"].upper())
                rationale["complexity"] = f"caller-hint → {complexity.value}"
            except ValueError:
                pass

        # --- Risk ---
        risk_order = [TaskRisk.CRITICAL, TaskRisk.HIGH, TaskRisk.MEDIUM, TaskRisk.LOW]
        risk = _match_signals(pl, _RISK_SIGNALS, TaskRisk.MEDIUM, risk_order)
        rationale["risk"] = f"keyword-match → {risk.value}"
        if "risk" in hints:
            try:
                risk = TaskRisk(hints["risk"].upper())
                rationale["risk"] = f"caller-hint → {risk.value}"
            except ValueError:
                pass

        # --- Repo Impact ---
        impact_order = [
            RepoImpact.SYSTEMIC, RepoImpact.CROSS_MODULE,
            RepoImpact.MODULE, RepoImpact.LOCAL, RepoImpact.NONE,
        ]
        repo_impact = _match_signals(pl, _REPO_IMPACT_SIGNALS, RepoImpact.LOCAL, impact_order)
        rationale["repo_impact"] = f"keyword-match → {repo_impact.value}"

        # --- Verification Difficulty ---
        ver_diff = self._classify_verification(pl, complexity, risk)
        rationale["verification_difficulty"] = f"derived from complexity={complexity.value}, risk={risk.value}"

        # --- Tool Intensity ---
        tool_order = [ToolIntensity.HEAVY, ToolIntensity.MODERATE, ToolIntensity.LIGHT]
        tool_intensity = _match_signals(pl, _TOOL_INTENSITY_SIGNALS, ToolIntensity.MODERATE, tool_order)
        rationale["tool_intensity"] = f"keyword-match → {tool_intensity.value}"

        # --- Context Requirement ---
        ctx_req = self._classify_context_requirement(complexity, repo_impact, tool_intensity)
        rationale["context_requirement"] = f"derived → {ctx_req.value}"

        return TaskClassification(
            complexity=complexity,
            risk=risk,
            repo_impact=repo_impact,
            verification_difficulty=ver_diff,
            tool_intensity=tool_intensity,
            context_requirement=ctx_req,
            rationale=rationale,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_verification(
        prompt_lower: str,
        complexity: TaskComplexity,
        risk: TaskRisk,
    ) -> VerificationDifficulty:
        if risk in (TaskRisk.CRITICAL,) or complexity in (TaskComplexity.CRITICAL,):
            return VerificationDifficulty.MANUAL
        if complexity == TaskComplexity.COMPLEX or risk == TaskRisk.HIGH:
            return VerificationDifficulty.HARD
        if complexity == TaskComplexity.MODERATE or risk == TaskRisk.MEDIUM:
            return VerificationDifficulty.MODERATE
        return VerificationDifficulty.EASY

    @staticmethod
    def _classify_context_requirement(
        complexity: TaskComplexity,
        repo_impact: RepoImpact,
        tool_intensity: ToolIntensity,
    ) -> ContextRequirement:
        score = 0
        score += {"TRIVIAL": 0, "MODERATE": 1, "COMPLEX": 2, "CRITICAL": 3}.get(complexity.value, 1)
        score += {"NONE": 0, "LOCAL": 0, "MODULE": 1, "CROSS_MODULE": 2, "SYSTEMIC": 3}.get(repo_impact.value, 0)
        score += {"LIGHT": 0, "MODERATE": 1, "HEAVY": 2}.get(tool_intensity.value, 1)
        if score <= 1:
            return ContextRequirement.MINIMAL
        if score <= 3:
            return ContextRequirement.STANDARD
        if score <= 5:
            return ContextRequirement.EXTENDED
        return ContextRequirement.MAXIMUM

    @staticmethod
    def _default_classification() -> TaskClassification:
        return TaskClassification(
            complexity=TaskComplexity.MODERATE,
            risk=TaskRisk.MEDIUM,
            repo_impact=RepoImpact.LOCAL,
            verification_difficulty=VerificationDifficulty.MODERATE,
            tool_intensity=ToolIntensity.MODERATE,
            context_requirement=ContextRequirement.STANDARD,
            rationale={"all": "default — empty prompt"},
        )
