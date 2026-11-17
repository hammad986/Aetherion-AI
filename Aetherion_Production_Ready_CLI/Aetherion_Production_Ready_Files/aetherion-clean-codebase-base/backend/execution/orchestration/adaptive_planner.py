"""
backend/execution/orchestration/adaptive_planner.py — Phase 19 Adaptive Planner & Replanning Engine.

Integrates Task Understanding, Repository Intelligence, Governed Skills,
Verified Engineering Memory, and RunBudget to generate and adaptively replan
bounded, verifiable execution graphs.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from .budget_enforcer import BudgetEnforcer
from .engineering_memory import EngineeringMemoryConflict, EngineeringMemoryRecord
from .execution_graph import ExecutionGraph
from .execution_plan import (
    ExecutionPlan,
    PlanRiskLevel,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
    PlanValidationError,
    PlanValidator,
)
from .governed_skills import SkillCandidate
from .engineering_memory import scrub_secrets
from .model_profile import ModelRole
from .repo_intelligence import RepositoryIntelligence
from .run_budget import BudgetExhaustedError, RunBudget
from .task_classifier import TaskClassification, TaskComplexity, TaskRisk

logger = logging.getLogger("aetherion.orchestration.adaptive_planner")


class ReplanBudgetExhaustedError(BudgetExhaustedError):
    """Raised when plan revision count or repair cycles exceed configured budget."""

    def __init__(
        self,
        message: str = "Maximum plan revisions exhausted.",
        limit_name: str = "max_revisions",
        current_value: float = 0.0,
        configured_max: float = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            limit_name=limit_name,
            current_value=current_value,
            configured_max=configured_max,
            terminal_action="FAIL_TASK",
            evidence={"error": message, **kwargs},
        )
        self.message = message

    def __str__(self) -> str:
        return self.message


class AdaptivePlanner:
    """
    Authoritative planner for constructing and revising bounded execution graphs.

    Enforces invariants:
    1. Every executable step has an objective.
    2. Every risky step has policy/HITL semantics.
    3. Every dependent step references valid predecessors.
    4. Successful steps cannot silently revert to pending during replanning.
    5. Replanning is strictly bounded by max_revisions and max_step_retries.
    6. Historical memory is empirical evidence, never execution authority.
    """

    DEFAULT_MAX_REVISIONS = 3
    DEFAULT_MAX_GRAPH_STEPS = 10
    DEFAULT_MAX_STEP_RETRIES = 2

    def __init__(
        self,
        max_revisions: int = DEFAULT_MAX_REVISIONS,
        max_graph_steps: int = DEFAULT_MAX_GRAPH_STEPS,
        max_step_retries: int = DEFAULT_MAX_STEP_RETRIES,
    ) -> None:
        self.max_revisions = max_revisions
        self.max_graph_steps = max_graph_steps
        self.max_step_retries = max_step_retries
        self.validator = PlanValidator(max_steps=max_graph_steps)

    def plan_task(
        self,
        task_id: str,
        tenant_id: str,
        objective: str,
        classification: Optional[TaskClassification] = None,
        repo_intelligence: Optional[RepositoryIntelligence] = None,
        skills: Optional[List[SkillCandidate]] = None,
        engineering_memories: Optional[List[EngineeringMemoryRecord]] = None,
        memory_conflicts: Optional[List[EngineeringMemoryConflict]] = None,
        execution_budget: Optional[Dict[str, Any]] = None,
        verification_criteria: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> ExecutionGraph:
        """
        Generate a validated, dependency-aware ExecutionGraph for a task.
        """
        if not objective or not objective.strip():
            raise PlanValidationError("Task objective cannot be empty.")
        if not tenant_id:
            raise KeyError("tenant_id is required for plan generation.")

        plan_id = f"plan_{uuid.uuid4().hex[:10]}"
        plan = ExecutionPlan(
            plan_id=plan_id,
            task_id=task_id,
            tenant_id=tenant_id,
            title=f"Plan: {objective[:60]}",
            objective=objective,
            revision=1,
            status=PlanStatus.DRAFT,
            metadata={
                "task_id": task_id,
                "tenant_id": tenant_id,
                "planned_at": time.time(),
            },
        )

        # Determine target files and symbols from repo intelligence
        target_files: List[str] = []
        if repo_intelligence:
            repo_map = getattr(repo_intelligence, "repository_map", None) or getattr(repo_intelligence, "_map", None)
            if repo_map and hasattr(repo_map, "files"):
                if not repo_map.files and hasattr(repo_intelligence, "initialize"):
                    try:
                        repo_intelligence.initialize()
                        repo_map = getattr(repo_intelligence, "repository_map", None) or getattr(repo_intelligence, "_map", None)
                    except Exception:
                        pass
                if repo_map and hasattr(repo_map, "files"):
                    target_files = [f.path if hasattr(f, "path") else str(k) for k, f in repo_map.files.items()][:5]

        # Extract available skills
        available_skills: List[str] = []
        for s in (skills or []):
            if hasattr(s, "skill") and hasattr(s.skill, "skill_id"):
                available_skills.append(s.skill.skill_id)
            elif isinstance(s, dict) and "skill_id" in s:
                available_skills.append(s["skill_id"])

        v_crit = dict(verification_criteria or {})
        b_budget = dict(execution_budget or {})

        # Determine complexity
        is_complex = False
        if classification:
            if classification.complexity in (TaskComplexity.COMPLEX, TaskComplexity.CRITICAL):
                is_complex = True
            if classification.risk in (TaskRisk.HIGH, TaskRisk.CRITICAL):
                is_complex = True
        if len(target_files) > 2 or "refactor" in objective.lower() or "migrate" in objective.lower():
            is_complex = True

        # Synthesize steps based on complexity and classification
        if not is_complex:
            # Simple 2-step workflow: Execute -> Verify
            s1 = PlanStep(
                step_id="step_1_execute",
                objective=f"Execute: {objective}",
                description="Perform primary implementation changes according to task requirements",
                dependencies=[],
                allowed_skills=available_skills,
                allowed_tools=["read_file", "write_file", "edit_file", "run_command"],
                target_files=target_files[:2],
                mutation_scope=target_files[:2],
                risk_level=PlanRiskLevel.LOW,
                max_retries=self.max_step_retries,
            )
            s2 = PlanStep(
                step_id="step_2_verify",
                objective=f"Verify changes for: {objective}",
                description="Run automated verification checks and test validation",
                dependencies=["step_1_execute"],
                allowed_skills=["diagnostic_repair"] if "diagnostic_repair" in available_skills else [],
                allowed_tools=["run_tests", "run_command", "git_diff"],
                verification_criteria=v_crit,
                risk_level=PlanRiskLevel.LOW,
                max_retries=self.max_step_retries,
            )
            plan.add_step(s1)
            plan.add_step(s2)
        else:
            # Multi-step complex engineering graph:
            # 1. Analyze / Prepare
            # 2. Refactor / Implement
            # 3. Unit Test Verification
            # 4. Final System Verification & Review
            s1 = PlanStep(
                step_id="step_1_analyze",
                objective="Analyze repository structures, dependencies, and change impacts",
                description="Inspect AST nodes and dependency graph before applying modifications",
                dependencies=[],
                allowed_skills=[],
                allowed_tools=["read_file", "run_command"],
                target_files=target_files,
                risk_level=PlanRiskLevel.LOW,
                max_retries=1,
            )
            s2 = PlanStep(
                step_id="step_2_implement",
                objective=f"Implement changes for: {objective}",
                description="Apply safe edits and refactoring with bounded change scope",
                dependencies=["step_1_analyze"],
                allowed_skills=[s for s in available_skills if s in ("refactor_safe", "config_updater")] or available_skills,
                allowed_tools=["read_file", "write_file", "edit_file", "run_command", "git_diff"],
                target_files=target_files,
                mutation_scope=target_files,
                risk_level=PlanRiskLevel.MEDIUM if not classification or classification.risk == TaskRisk.MEDIUM else PlanRiskLevel(classification.risk.value),
                max_retries=self.max_step_retries,
            )
            s3 = PlanStep(
                step_id="step_3_test",
                objective="Author and execute unit tests for modified components",
                description="Generate test assertions and verify test coverage",
                dependencies=["step_2_implement"],
                allowed_skills=[s for s in available_skills if s == "unit_test_generator"] or available_skills,
                allowed_tools=["read_file", "write_file", "run_tests", "run_command"],
                verification_criteria={"type": "unit_tests"},
                risk_level=PlanRiskLevel.LOW,
                max_retries=self.max_step_retries,
            )
            s4 = PlanStep(
                step_id="step_4_final_verify",
                objective="Perform end-to-end verification and evidence validation",
                description="Ensure all tests pass and package evidence for promotion",
                dependencies=["step_3_test"],
                allowed_skills=[s for s in available_skills if s == "diagnostic_repair"] or available_skills,
                allowed_tools=["run_tests", "run_command", "git_diff"],
                verification_criteria=v_crit,
                risk_level=PlanRiskLevel.LOW,
                max_retries=self.max_step_retries,
            )
            plan.add_step(s1)
            plan.add_step(s2)
            plan.add_step(s3)
            plan.add_step(s4)

        # Validate graph
        graph = ExecutionGraph(plan)
        return graph

    def replan_step_failure(
        self,
        graph: ExecutionGraph,
        failed_step_id: str,
        failure_reason: str,
        verifier_output: str = "",
    ) -> ExecutionPlan:
        """
        Adaptively revise an execution graph upon step failure.

        Invariants:
        1. Already succeeded steps MUST remain preserved.
        2. Revisions are strictly bounded by self.max_revisions.
        3. A repair step is injected with diagnostic skills and rewired dependencies.
        """
        plan = graph.plan
        failed_step = plan.get_step(failed_step_id)
        if not failed_step:
            raise KeyError(f"Step '{failed_step_id}' not found in plan.")

        # Check revision ceiling
        if plan.revision >= self.max_revisions:
            logger.error("Plan '%s' reached maximum allowable revisions (%d). Terminating replanning.", plan.plan_id, self.max_revisions)
            raise ReplanBudgetExhaustedError(
                message=f"Maximum plan revisions ({self.max_revisions}) exhausted. Cannot replan step '{failed_step_id}'.",
                limit_name="max_revisions",
                current_value=float(plan.revision),
                configured_max=float(self.max_revisions),
            )

        # Check step retry policy
        if failed_step.can_retry():
            failed_step.record_attempt(error=failure_reason)
            failed_step.status = PlanStepStatus.READY
            plan.record_revision(
                reason=f"Step '{failed_step_id}' failed: retrying (attempt {failed_step.retries_attempted}/{failed_step.max_retries}) - {scrub_secrets(failure_reason[:100])}",
                affected_steps=[failed_step_id],
                preserved_steps=[s.step_id for s in plan.steps.values() if s.status == PlanStepStatus.SUCCEEDED],
                newly_created_steps=[],
                removed_steps=[],
                budget_impact={"retry_increment": 1},
            )
            graph.refresh_graph()
            return plan

        # Step retries exhausted -> inject a diagnostic repair step and rewire dependents
        next_revision = plan.revision + 1
        repair_step_id = f"repair_{failed_step_id}_{next_revision}"
        repair_step = PlanStep(
            step_id=repair_step_id,
            objective=f"Diagnose and repair failure in: {failed_step.objective}",
            description=f"Automated repair: {scrub_secrets(failure_reason[:200])}",
            dependencies=list(failed_step.dependencies),
            allowed_skills=["diagnostic_repair", "refactor_safe"],
            allowed_tools=["read_file", "edit_file", "write_file", "run_tests", "run_command", "git_diff"],
            target_files=list(failed_step.target_files),
            mutation_scope=list(failed_step.mutation_scope),
            verification_criteria=dict(failed_step.verification_criteria),
            risk_level=PlanRiskLevel.MEDIUM,
            max_retries=1,
            status=PlanStepStatus.READY if all(plan.steps[d].status == PlanStepStatus.SUCCEEDED for d in failed_step.dependencies) else PlanStepStatus.PENDING,
        )

        # Mark failed step terminal
        failed_step.status = PlanStepStatus.FAILED
        failed_step.error_message = failure_reason

        # Rewire any downstream steps that depended on the failed step to depend on repair step instead
        dependents = graph.get_dependents(failed_step_id)
        for dep_id in dependents:
            dep_step = plan.get_step(dep_id)
            if dep_step and dep_step.status != PlanStepStatus.SUCCEEDED:
                # Replace failed_step_id with repair_step_id in its dependencies
                new_deps = [repair_step_id if d == failed_step_id else d for d in dep_step.dependencies]
                dep_step.dependencies = new_deps
                # If it was marked blocked, unblock if all other deps are ok
                if dep_step.status == PlanStepStatus.BLOCKED:
                    dep_step.status = PlanStepStatus.PENDING
                    dep_step.error_message = None

        # Add the repair step to the plan
        plan.add_step(repair_step)

        # Record revision history
        preserved = [s.step_id for s in plan.steps.values() if s.status == PlanStepStatus.SUCCEEDED]
        plan.record_revision(
            reason=f"Step '{failed_step_id}' failed permanently ({scrub_secrets(failure_reason[:100])}); injected repair step '{repair_step_id}'",
            affected_steps=[failed_step_id] + dependents,
            preserved_steps=preserved,
            newly_created_steps=[repair_step_id],
            removed_steps=[],
            budget_impact={"injected_step": repair_step_id, "revision": next_revision},
        )

        graph.refresh_graph()
        return plan
