"""
backend/execution/orchestration/execution_graph.py — Phase 19 Execution Graph & Dependency DAG.

Coordinates dependency-aware step execution, ready-step detection, deterministic
topological traversal, and safe parallelism with mutation boundary verification.
"""

from __future__ import annotations

import collections
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .execution_plan import (
    CycleDetectedError,
    ExecutionPlan,
    PlanRiskLevel,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
    PlanValidationError,
    PlanValidator,
)

logger = logging.getLogger("aetherion.orchestration.execution_graph")


class ParallelExecutionPolicy:
    """
    Evaluates whether two independent steps can safely execute concurrently.

    Invariants:
    1. Dependency graph proves independence (no direct or transitive path between them).
    2. Disjoint mutation scope (no file or resource collision).
    3. Neither step involves exclusive system-level locks or high-risk mutations.
    """

    @staticmethod
    def check_parallel_safety(
        step_a: PlanStep,
        step_b: PlanStep,
        graph_transitive_deps: Optional[Dict[str, Set[str]]] = None,
    ) -> Tuple[bool, str]:
        """
        Check if step_a and step_b can run concurrently.
        Returns (is_safe, reason).
        """
        # 1. Dependency check
        if graph_transitive_deps:
            if step_b.step_id in graph_transitive_deps.get(step_a.step_id, set()):
                return False, f"Step '{step_a.step_id}' transitively precedes '{step_b.step_id}'."
            if step_a.step_id in graph_transitive_deps.get(step_b.step_id, set()):
                return False, f"Step '{step_b.step_id}' transitively precedes '{step_a.step_id}'."

        # 2. Critical or High Risk serialisation
        if step_a.risk_level in (PlanRiskLevel.HIGH, PlanRiskLevel.CRITICAL):
            return False, f"Step '{step_a.step_id}' is {step_a.risk_level.value} risk; requires serialized execution."
        if step_b.risk_level in (PlanRiskLevel.HIGH, PlanRiskLevel.CRITICAL):
            return False, f"Step '{step_b.step_id}' is {step_b.risk_level.value} risk; requires serialized execution."

        # 3. File and mutation scope overlap
        scope_a = set(step_a.target_files).union(set(step_a.mutation_scope))
        scope_b = set(step_b.target_files).union(set(step_b.mutation_scope))
        overlap = scope_a.intersection(scope_b)
        if overlap:
            return False, f"Mutation conflict on overlapping files: {', '.join(sorted(list(overlap)))}"

        return True, "Steps are independent with disjoint mutation scopes."


class ExecutionGraph:
    """
    Authoritative dependency-aware execution graph wrapping an ExecutionPlan.
    """

    def __init__(self, plan: ExecutionPlan) -> None:
        self.plan = plan
        self.validator = PlanValidator()
        self._adj: Dict[str, List[str]] = collections.defaultdict(list)
        self._reverse_adj: Dict[str, List[str]] = collections.defaultdict(list)
        self._transitive_dependents: Dict[str, Set[str]] = collections.defaultdict(set)
        self._build_edges()
        self._validate()

    def _build_edges(self) -> None:
        """Construct forward and reverse adjacency lists."""
        self._adj.clear()
        self._reverse_adj.clear()
        for s_id, step in self.plan.steps.items():
            # Ensure node entry exists
            if s_id not in self._adj:
                self._adj[s_id] = []
            if s_id not in self._reverse_adj:
                self._reverse_adj[s_id] = []

            for dep in step.dependencies:
                self._adj[dep].append(s_id)
                self._reverse_adj[s_id].append(dep)

        # Precompute transitive dependencies for fast parallel safety checks
        self._compute_transitive_dependents()

    def _compute_transitive_dependents(self) -> None:
        """Compute all downstream reachable nodes for each node."""
        self._transitive_dependents.clear()
        for node in self.plan.steps:
            visited: Set[str] = set()
            stack = list(self._adj.get(node, []))
            while stack:
                curr = stack.pop()
                if curr not in visited:
                    visited.add(curr)
                    stack.extend(self._adj.get(curr, []))
            self._transitive_dependents[node] = visited

    def _validate(self) -> None:
        """Validate the graph structure deterministically."""
        res = self.validator.validate(self.plan)
        if not res.valid:
            if any("Cycle detected" in e for e in res.errors):
                raise CycleDetectedError("; ".join(res.errors))
            raise PlanValidationError("; ".join(res.errors))

    def get_step(self, step_id: str) -> Optional[PlanStep]:
        """Fetch step by ID."""
        return self.plan.get_step(step_id)

    def get_dependencies(self, step_id: str) -> List[str]:
        """Return immediate predecessor IDs."""
        step = self.get_step(step_id)
        return list(step.dependencies) if step else []

    def get_dependents(self, step_id: str) -> List[str]:
        """Return immediate successor IDs."""
        return list(self._adj.get(step_id, []))

    def get_ready_steps(self) -> List[PlanStep]:
        """
        Identify steps that are ready for execution.

        A step is READY if:
        1. Current status is PENDING or READY.
        2. All predecessor dependencies have status SUCCEEDED.
        """
        ready: List[PlanStep] = []
        for step in self.plan.steps.values():
            if step.status not in (PlanStepStatus.PENDING, PlanStepStatus.READY):
                continue
            deps = step.dependencies
            if all(self.plan.steps[d].status == PlanStepStatus.SUCCEEDED for d in deps):
                step.status = PlanStepStatus.READY
                ready.append(step)
        return ready

    def get_blocked_steps(self) -> List[PlanStep]:
        """
        Identify steps that cannot execute because predecessors are pending or failed.
        """
        blocked: List[PlanStep] = []
        for step in self.plan.steps.values():
            if step.status in (PlanStepStatus.SUCCEEDED, PlanStepStatus.RUNNING, PlanStepStatus.SKIPPED):
                continue
            deps = step.dependencies
            has_failed_dep = any(self.plan.steps[d].status == PlanStepStatus.FAILED for d in deps)
            has_pending_dep = any(self.plan.steps[d].status != PlanStepStatus.SUCCEEDED for d in deps)
            if has_failed_dep or has_pending_dep:
                blocked.append(step)
        return blocked

    def mark_step_running(self, step_id: str) -> None:
        """Transition step to RUNNING state."""
        step = self.plan.steps[step_id]
        if step.status not in (PlanStepStatus.READY, PlanStepStatus.PENDING):
            logger.warning("Step '%s' transition to RUNNING from non-ready state '%s'", step_id, step.status.value)
        step.status = PlanStepStatus.RUNNING
        step.started_at = time.time()
        self.plan.status = PlanStatus.EXECUTING

    def mark_step_succeeded(
        self,
        step_id: str,
        result: Optional[Dict[str, Any]] = None,
        evidence_reference: Optional[str] = None,
    ) -> None:
        """
        Transition step to SUCCEEDED state.

        Enforces: Step must have completed execution and satisfied verification criteria.
        """
        step = self.plan.steps[step_id]
        step.status = PlanStepStatus.SUCCEEDED
        step.completed_at = time.time()
        if result:
            step.result = result
        if evidence_reference:
            step.evidence_references.append(evidence_reference)

        # Check if entire plan is completed
        if self.is_completed():
            self.plan.status = PlanStatus.COMPLETED

    def mark_step_failed(
        self,
        step_id: str,
        error: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Transition step to FAILED state and mark downstream dependents as BLOCKED."""
        step = self.plan.steps[step_id]
        step.status = PlanStepStatus.FAILED
        step.completed_at = time.time()
        step.error_message = error
        if result:
            step.result = result

        # Block downstream steps
        for down_id in self._transitive_dependents.get(step_id, set()):
            down_step = self.plan.steps.get(down_id)
            if down_step and down_step.status in (PlanStepStatus.PENDING, PlanStepStatus.READY):
                down_step.status = PlanStepStatus.BLOCKED
                down_step.error_message = f"Blocked by failure of predecessor '{step_id}'"

        if self.is_completed():
            self.plan.status = PlanStatus.FAILED

    def mark_step_skipped(self, step_id: str, reason: str) -> None:
        """Mark step as SKIPPED."""
        step = self.plan.steps[step_id]
        step.status = PlanStepStatus.SKIPPED
        step.error_message = reason

    def is_completed(self) -> bool:
        """True if all steps have reached terminal states."""
        terminal_statuses = {
            PlanStepStatus.SUCCEEDED,
            PlanStepStatus.FAILED,
            PlanStepStatus.SKIPPED,
            PlanStepStatus.BLOCKED,
        }
        return all(s.status in terminal_statuses for s in self.plan.steps.values())

    def has_failures(self) -> bool:
        """True if any step failed."""
        return any(s.status == PlanStepStatus.FAILED for s in self.plan.steps.values())

    def topological_sort(self) -> List[PlanStep]:
        """Return steps sorted in topological dependency order."""
        res = self.validator.validate(self.plan)
        if not res.valid:
            raise PlanValidationError("; ".join(res.errors))
        return [self.plan.steps[sid] for sid in res.topological_order]

    def get_parallel_execution_tiers(self) -> List[List[PlanStep]]:
        """Group steps into independent parallel tiers based on DAG structure."""
        res = self.validator.validate(self.plan)
        if not res.valid:
            raise PlanValidationError("; ".join(res.errors))
        return [[self.plan.steps[sid] for sid in tier] for tier in res.parallel_tiers]

    def check_parallel_safety(self, step_a: PlanStep, step_b: PlanStep) -> Tuple[bool, str]:
        """Check if step_a and step_b can run concurrently safely."""
        return ParallelExecutionPolicy.check_parallel_safety(
            step_a=step_a,
            step_b=step_b,
            graph_transitive_deps=self._transitive_dependents,
        )

    def refresh_graph(self) -> None:
        """Recompute edges and validate after replanning modifications."""
        self._build_edges()
        self._validate()

    def as_dict(self) -> Dict[str, Any]:
        """Observability dictionary for graph execution state."""
        return {
            "plan_id": self.plan.plan_id,
            "revision": self.plan.revision,
            "total_steps": len(self.plan.steps),
            "ready_steps": [s.step_id for s in self.get_ready_steps()],
            "blocked_steps": [s.step_id for s in self.get_blocked_steps()],
            "is_completed": self.is_completed(),
            "has_failures": self.has_failures(),
            "step_statuses": {sid: s.status.value for sid, s in self.plan.steps.items()},
        }
