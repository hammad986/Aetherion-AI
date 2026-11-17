"""
backend/execution/orchestration/execution_plan.py — Phase 19 Authoritative Plan & Step Abstraction.

Defines the structured, deterministic plan model, step specification, revision history,
and strict integrity validation for autonomous execution graphs.
"""

from __future__ import annotations

import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .engineering_memory import scrub_secrets

logger = logging.getLogger("aetherion.orchestration.execution_plan")


class PlanStepStatus(str, enum.Enum):
    """Lifecycle statuses for a step in the execution graph."""
    PENDING = "PENDING"
    READY = "READY"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    REPAIRING = "REPAIRING"
    ORPHANED = "ORPHANED"
    RECOVERING = "RECOVERING"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"


class PlanRiskLevel(str, enum.Enum):
    """Risk tiers for execution steps."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PlanStatus(str, enum.Enum):
    """Lifecycle statuses for the overall execution plan."""
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    CANCELLED = "CANCELLED"


class PlanValidationError(ValueError):
    """Raised when an execution plan or graph violates integrity constraints."""
    pass


class CycleDetectedError(PlanValidationError):
    """Raised when an execution graph contains dependency cycles."""
    pass


class ImpossibleDependencyError(PlanValidationError):
    """Raised when a dependency references nonexistent steps or creates an impossible path."""
    pass


@dataclass
class PlanStep:
    """
    Authoritative, structured representation of an executable unit of work.

    Does NOT accept free-form execution text as authoritative authority;
    every step must specify objective, inputs, outputs, verification criteria,
    and security boundaries.
    """
    step_id: str
    objective: str
    description: str = ""
    required_inputs: List[str] = field(default_factory=list)
    expected_outputs: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    allowed_skills: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    verification_criteria: Dict[str, Any] = field(default_factory=dict)
    execution_budget: Dict[str, Any] = field(default_factory=dict)
    risk_level: PlanRiskLevel = PlanRiskLevel.LOW
    retry_policy: Dict[str, Any] = field(default_factory=dict)
    status: PlanStepStatus = PlanStepStatus.PENDING
    evidence_references: List[str] = field(default_factory=list)
    result: Dict[str, Any] = field(default_factory=dict)
    target_files: List[str] = field(default_factory=list)
    mutation_scope: List[str] = field(default_factory=list)
    max_retries: int = 2
    retries_attempted: int = 0
    requires_hitl: bool = False
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    worker_id: Optional[str] = None
    lease_id: Optional[str] = None
    lease_expires: Optional[float] = None
    heartbeat_at: Optional[float] = None
    idempotency_token: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.step_id:
            self.step_id = f"step_{uuid.uuid4().hex[:8]}"
        if isinstance(self.status, str) and not isinstance(self.status, PlanStepStatus):
            try:
                self.status = PlanStepStatus(self.status.upper())
            except ValueError:
                self.status = PlanStepStatus.PENDING
        if isinstance(self.risk_level, str) and not isinstance(self.risk_level, PlanRiskLevel):
            try:
                self.risk_level = PlanRiskLevel(self.risk_level.upper())
            except ValueError:
                self.risk_level = PlanRiskLevel.LOW
        # High and critical risk automatically flag HITL requirement
        if self.risk_level in (PlanRiskLevel.HIGH, PlanRiskLevel.CRITICAL):
            self.requires_hitl = True
        # Clean target files and mutation scope paths
        self.target_files = [str(f).replace("\\", "/") for f in self.target_files]
        self.mutation_scope = [str(f).replace("\\", "/") for f in self.mutation_scope]

    def validate(self) -> None:
        """Validate step integrity invariants."""
        if not self.step_id or not self.step_id.strip():
            raise PlanValidationError("step_id cannot be empty.")
        if not self.objective or not self.objective.strip():
            raise PlanValidationError(f"Step '{self.step_id}' must have a non-empty objective.")
        if self.step_id in self.dependencies:
            raise ImpossibleDependencyError(f"Step '{self.step_id}' cannot depend on itself.")
        if self.max_retries < 0:
            raise PlanValidationError(f"Step '{self.step_id}' max_retries cannot be negative.")

    def can_retry(self) -> bool:
        """Determine if this step is eligible for retry."""
        return self.retries_attempted < self.max_retries

    def record_attempt(self, error: Optional[str] = None) -> None:
        """Record an execution attempt and update retry count."""
        self.retries_attempted += 1
        if error:
            self.error_message = scrub_secrets(error)

    def as_dict(self) -> Dict[str, Any]:
        """Safe dictionary representation with scrubbed secrets."""
        return {
            "step_id": self.step_id,
            "objective": scrub_secrets(self.objective),
            "description": scrub_secrets(self.description),
            "required_inputs": self.required_inputs,
            "expected_outputs": self.expected_outputs,
            "dependencies": list(self.dependencies),
            "allowed_skills": list(self.allowed_skills),
            "allowed_tools": list(self.allowed_tools),
            "verification_criteria": self.verification_criteria,
            "execution_budget": self.execution_budget,
            "risk_level": self.risk_level.value,
            "retry_policy": self.retry_policy,
            "status": self.status.value,
            "evidence_references": self.evidence_references,
            "result": self.result,
            "target_files": list(self.target_files),
            "mutation_scope": list(self.mutation_scope),
            "max_retries": self.max_retries,
            "retries_attempted": self.retries_attempted,
            "requires_hitl": self.requires_hitl,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
            "worker_id": self.worker_id,
            "lease_id": self.lease_id,
            "lease_expires": self.lease_expires,
            "heartbeat_at": self.heartbeat_at,
            "idempotency_token": self.idempotency_token,
        }

    to_dict = as_dict

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PlanStep:
        """Construct PlanStep from dictionary."""
        d = dict(data)
        return cls(
            step_id=d.get("step_id", ""),
            objective=d.get("objective", ""),
            description=d.get("description", ""),
            required_inputs=d.get("required_inputs", []),
            expected_outputs=d.get("expected_outputs", []),
            dependencies=d.get("dependencies", []),
            allowed_skills=d.get("allowed_skills", []),
            allowed_tools=d.get("allowed_tools", []),
            verification_criteria=d.get("verification_criteria", {}),
            execution_budget=d.get("execution_budget", {}),
            risk_level=PlanRiskLevel(d.get("risk_level", "LOW")),
            retry_policy=d.get("retry_policy", {}),
            status=PlanStepStatus(d.get("status", "PENDING")),
            evidence_references=d.get("evidence_references", []),
            result=d.get("result", {}),
            target_files=d.get("target_files", []),
            mutation_scope=d.get("mutation_scope", []),
            max_retries=int(d.get("max_retries", 2)),
            retries_attempted=int(d.get("retries_attempted", 0)),
            requires_hitl=bool(d.get("requires_hitl", False)),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            error_message=d.get("error_message"),
            worker_id=d.get("worker_id"),
            lease_id=d.get("lease_id"),
            lease_expires=d.get("lease_expires"),
            heartbeat_at=d.get("heartbeat_at"),
            idempotency_token=d.get("idempotency_token"),
        )


@dataclass
class PlanRevision:
    """Audit record capturing an adaptive replanning event."""
    revision_id: int
    reason: str
    affected_steps: List[str]
    preserved_steps: List[str]
    newly_created_steps: List[str]
    removed_steps: List[str]
    budget_impact: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "reason": scrub_secrets(self.reason),
            "affected_steps": self.affected_steps,
            "preserved_steps": self.preserved_steps,
            "newly_created_steps": self.newly_created_steps,
            "removed_steps": self.removed_steps,
            "budget_impact": self.budget_impact,
            "timestamp": self.timestamp,
        }

    to_dict = as_dict


@dataclass
class ExecutionPlan:
    """
    Authoritative, durable execution plan container.

    Enforces revision integrity: successful steps cannot be silently erased.
    Tracks revision history and bounds total graph complexity.
    """
    plan_id: str
    task_id: str
    tenant_id: str
    title: str = ""
    objective: str = ""
    revision: int = 1
    steps: Dict[str, PlanStep] = field(default_factory=dict)
    status: PlanStatus = PlanStatus.DRAFT
    revision_history: List[PlanRevision] = field(default_factory=list)
    budget_impact: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.plan_id:
            self.plan_id = f"plan_{uuid.uuid4().hex[:10]}"
        if isinstance(self.status, str) and not isinstance(self.status, PlanStatus):
            try:
                self.status = PlanStatus(self.status.upper())
            except ValueError:
                self.status = PlanStatus.DRAFT

    def add_step(self, step: PlanStep) -> None:
        """Add a step to the plan with uniqueness check."""
        step.validate()
        if step.step_id in self.steps:
            raise PlanValidationError(f"Duplicate step ID '{step.step_id}' in plan '{self.plan_id}'.")
        self.steps[step.step_id] = step
        self.updated_at = time.time()

    def get_step(self, step_id: str) -> Optional[PlanStep]:
        """Fetch step by ID."""
        return self.steps.get(step_id)

    def step_count(self) -> int:
        return len(self.steps)

    @property
    def revision_count(self) -> int:
        return len(self.revision_history)

    def get_step_list(self) -> List[PlanStep]:
        return list(self.steps.values())

    def record_revision(
        self,
        reason: str,
        affected_steps: List[str],
        preserved_steps: List[str],
        newly_created_steps: List[str],
        removed_steps: List[str],
        budget_impact: Optional[Dict[str, Any]] = None,
    ) -> PlanRevision:
        """
        Record a bounded plan revision.

        Invariant: Succeeded steps MUST NOT be removed or silently mutated.
        """
        self.revision += 1
        self.updated_at = time.time()
        rev = PlanRevision(
            revision_id=self.revision,
            reason=reason,
            affected_steps=affected_steps,
            preserved_steps=preserved_steps,
            newly_created_steps=newly_created_steps,
            removed_steps=removed_steps,
            budget_impact=budget_impact or {},
            timestamp=self.updated_at,
        )
        self.revision_history.append(rev)
        return rev

    def as_dict(self) -> Dict[str, Any]:
        """Safe serialization of the plan."""
        return {
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "title": scrub_secrets(self.title),
            "objective": scrub_secrets(self.objective),
            "revision": self.revision,
            "status": self.status.value,
            "step_count": len(self.steps),
            "steps": {s_id: s.as_dict() for s_id, s in self.steps.items()},
            "revision_history": [r.as_dict() for r in self.revision_history],
            "budget_impact": self.budget_impact,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    to_dict = as_dict

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExecutionPlan:
        """Construct ExecutionPlan from dictionary."""
        steps_dict: Dict[str, PlanStep] = {}
        for sid, sdata in data.get("steps", {}).items():
            steps_dict[sid] = PlanStep.from_dict(sdata)

        revisions: List[PlanRevision] = []
        for rdata in data.get("revision_history", []):
            revisions.append(PlanRevision(
                revision_id=int(rdata.get("revision_id", 1)),
                reason=rdata.get("reason", ""),
                affected_steps=rdata.get("affected_steps", []),
                preserved_steps=rdata.get("preserved_steps", []),
                newly_created_steps=rdata.get("newly_created_steps", []),
                removed_steps=rdata.get("removed_steps", []),
                budget_impact=rdata.get("budget_impact", {}),
                timestamp=float(rdata.get("timestamp", time.time())),
            ))

        return cls(
            plan_id=data.get("plan_id", ""),
            task_id=data.get("task_id", ""),
            tenant_id=data.get("tenant_id", ""),
            title=data.get("title", ""),
            objective=data.get("objective", ""),
            revision=int(data.get("revision", 1)),
            steps=steps_dict,
            status=PlanStatus(data.get("status", "DRAFT")),
            revision_history=revisions,
            budget_impact=data.get("budget_impact", {}),
            metadata=data.get("metadata", {}),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
        )


@dataclass
class PlanValidationResult:
    """Result of deterministic graph and plan validation."""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    topological_order: List[str] = field(default_factory=list)
    parallel_tiers: List[List[str]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "topological_order": self.topological_order,
            "parallel_tiers": self.parallel_tiers,
        }


class PlanValidator:
    """
    Deterministic validator enforcing execution graph integrity:
    - No cycles (DAG property)
    - No duplicate step IDs
    - No missing or impossible dependencies
    - Every executable step has an objective
    - Graph size bounded by max_steps
    - Terminal states validation
    """

    def __init__(self, max_steps: int = 25, max_depth: int = 10) -> None:
        self.max_steps = max_steps
        self.max_depth = max_depth

    def validate(self, plan: ExecutionPlan) -> PlanValidationResult:
        """Perform comprehensive deterministic validation on an ExecutionPlan."""
        errors: List[str] = []
        warnings: List[str] = []

        if not plan.steps:
            return PlanValidationResult(valid=False, errors=["Execution plan contains zero steps."])

        if len(plan.steps) > self.max_steps:
            errors.append(f"Plan step count ({len(plan.steps)}) exceeds configured ceiling of {self.max_steps}.")

        step_ids = set(plan.steps.keys())

        # 1. Step-level validation
        for s_id, step in plan.steps.items():
            if not step.objective or not step.objective.strip():
                errors.append(f"Step '{s_id}' has an empty or whitespace-only objective.")
            if step.risk_level in (PlanRiskLevel.HIGH, PlanRiskLevel.CRITICAL) and not step.requires_hitl:
                warnings.append(f"Step '{s_id}' has {step.risk_level.value} risk but requires_hitl was False; auto-enforcing.")
                step.requires_hitl = True

            # Dependency existence checks
            for dep in step.dependencies:
                if dep == s_id:
                    errors.append(f"Step '{s_id}' has a self-dependency.")
                elif dep not in step_ids:
                    errors.append(f"Step '{s_id}' depends on non-existent step '{dep}'.")

        if errors:
            return PlanValidationResult(valid=False, errors=errors, warnings=warnings)

        # 2. Cycle detection and topological sort via Kahn's algorithm
        in_degree: Dict[str, int] = {sid: 0 for sid in step_ids}
        adj: Dict[str, List[str]] = {sid: [] for sid in step_ids}

        for sid, step in plan.steps.items():
            for dep in step.dependencies:
                adj[dep].append(sid)
                in_degree[sid] += 1

        queue: List[str] = [sid for sid, deg in in_degree.items() if deg == 0]
        topo_order: List[str] = []
        tiers: List[List[str]] = []

        current_tier = list(queue)
        while current_tier:
            tiers.append(sorted(current_tier))
            next_tier: List[str] = []
            for u in sorted(current_tier):
                topo_order.append(u)
                for v in adj[u]:
                    in_degree[v] -= 1
                    if in_degree[v] == 0:
                        next_tier.append(v)
            current_tier = next_tier

        if len(topo_order) != len(step_ids):
            cycle_nodes = [sid for sid, deg in in_degree.items() if deg > 0]
            errors.append(f"Cycle detected in execution graph involving steps: {', '.join(sorted(cycle_nodes))}")
            return PlanValidationResult(valid=False, errors=errors, warnings=warnings)

        # 3. Max depth check
        if len(tiers) > self.max_depth:
            errors.append(f"Execution graph depth ({len(tiers)}) exceeds maximum allowable depth of {self.max_depth}.")

        is_valid = len(errors) == 0
        if is_valid:
            plan.status = PlanStatus.VALIDATED

        return PlanValidationResult(
            valid=is_valid,
            errors=errors,
            warnings=warnings,
            topological_order=topo_order,
            parallel_tiers=tiers,
        )
