"""
backend/execution/orchestration/__init__.py — Phase 15/16 Model Orchestration Kernel.

Exports the authoritative orchestration surface for cost-aware, policy-governed
agent execution: task classification, model profile selection, RunBudget enforcement,
context compilation, role pipeline, escalation, usage accounting, and live budget
enforcement (Phase 16).
"""
from .task_classifier import (
    TaskClassification,
    TaskClassifier,
    TaskComplexity,
    TaskRisk,
    RepoImpact,
    VerificationDifficulty,
    ToolIntensity,
    ContextRequirement,
)
from .model_profile import (
    ModelProfile,
    ModelProfileRegistry,
    ModelRole,
    ProfileTier,
    RetryPolicy,
    get_default_registry,
)
from .run_budget import BudgetExhaustedError, RunBudget, RunBudgetUsage
from .context_compiler import CompiledContext, ContextCompiler
from .execution_policy import ExecutionPolicy, derive_execution_policy
from .escalation_policy import EscalationChain, EscalationPolicy, EscalationReason
from .usage_accounting import TurnUsage, UsageAccounting
from .budget_enforcer import BudgetEnforcer
from .repo_intelligence import (
    FileNode,
    RelevanceItem,
    RelevanceReason,
    RepositoryIntelligence,
    RepositoryMap,
    SymbolKind,
    SymbolNode,
    TaskRelevanceEngine,
)
from .adaptive_retriever import AdaptiveRetrievalResult, AdaptiveRetriever
from .governed_skills import (
    GovernedSkill,
    SkillAuditResult,
    SkillCandidate,
    SkillDiscoveryEngine,
    SkillLifecycleStatus,
    SkillPermissionAuditor,
    SkillRegistry,
    SkillSecurityPolicy,
    SkillSelectionReason,
    get_skill_registry,
    reset_skill_registry,
)
from .engineering_memory import (
    EngineeringMemoryConflict,
    EngineeringMemoryKernel,
    EngineeringMemoryRecord,
    MemoryScope,
    VerificationResultSummary,
    scrub_secrets,
)
from .execution_plan import (
    CycleDetectedError,
    ExecutionPlan,
    ImpossibleDependencyError,
    PlanRevision,
    PlanRiskLevel,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
    PlanValidationError,
    PlanValidationResult,
    PlanValidator,
)
from .execution_graph import (
    ExecutionGraph,
    ParallelExecutionPolicy,
)
from .adaptive_planner import (
    AdaptivePlanner,
    ReplanBudgetExhaustedError,
)
from .durable_supervisor import (
    DurableExecutionSupervisor,
    DurableSupervisorStore,
    DurableSupervisorError,
    StaleLeaseError,
    DuplicateClaimError,
    InvalidStepTransitionError,
    DuplicateWorkerExecutionError,
    TaskCancelledError,
    RecoveryFailedError,
    StepLease,
    RecoveryDecision,
    RecoveryEvaluation,
    ExecutionCheckpoint,
    DurableStepRecord,
    RecoveryDecisionEngine,
)



__all__ = [
    # Classification
    "TaskClassification",
    "TaskClassifier",
    "TaskComplexity",
    "TaskRisk",
    "RepoImpact",
    "VerificationDifficulty",
    "ToolIntensity",
    "ContextRequirement",
    # Model profile
    "ModelProfile",
    "ModelProfileRegistry",
    "ModelRole",
    "ProfileTier",
    "RetryPolicy",
    "get_default_registry",
    # Budget
    "BudgetExhaustedError",
    "RunBudget",
    "RunBudgetUsage",
    # Context
    "CompiledContext",
    "ContextCompiler",
    # Policy
    "ExecutionPolicy",
    "derive_execution_policy",
    # Escalation
    "EscalationChain",
    "EscalationPolicy",
    "EscalationReason",
    # Usage
    "TurnUsage",
    "UsageAccounting",
    # Phase 16 — Live Budget Enforcement
    "BudgetEnforcer",
    # Phase 17 — Repository Intelligence & Adaptive Context
    "FileNode",
    "RelevanceItem",
    "RelevanceReason",
    "RepositoryIntelligence",
    "RepositoryMap",
    "SymbolKind",
    "SymbolNode",
    "TaskRelevanceEngine",
    "AdaptiveRetrievalResult",
    "AdaptiveRetriever",
    # Phase 18 — Governed Skills & Engineering Memory
    "GovernedSkill",
    "SkillAuditResult",
    "SkillCandidate",
    "SkillDiscoveryEngine",
    "SkillLifecycleStatus",
    "SkillPermissionAuditor",
    "SkillRegistry",
    "SkillSecurityPolicy",
    "SkillSelectionReason",
    "get_skill_registry",
    "reset_skill_registry",
    "EngineeringMemoryConflict",
    "EngineeringMemoryKernel",
    "EngineeringMemoryRecord",
    "MemoryScope",
    "VerificationResultSummary",
    "scrub_secrets",
    # Phase 19 — Adaptive Planning & Execution Graph Kernel
    "PlanStep",
    "PlanStepStatus",
    "PlanRiskLevel",
    "PlanStatus",
    "PlanRevision",
    "ExecutionPlan",
    "PlanValidator",
    "PlanValidationResult",
    "PlanValidationError",
    "CycleDetectedError",
    "ImpossibleDependencyError",
    "ExecutionGraph",
    "ParallelExecutionPolicy",
    "AdaptivePlanner",
    "ReplanBudgetExhaustedError",
    # Phase 20 — Durable Execution Supervisor & Crash-Resilient Workflow Kernel
    "DurableExecutionSupervisor",
    "DurableSupervisorStore",
    "DurableSupervisorError",
    "StaleLeaseError",
    "DuplicateClaimError",
    "InvalidStepTransitionError",
    "DuplicateWorkerExecutionError",
    "TaskCancelledError",
    "RecoveryFailedError",
    "StepLease",
    "RecoveryDecision",
    "RecoveryEvaluation",
    "ExecutionCheckpoint",
    "DurableStepRecord",
    "RecoveryDecisionEngine",
]


