"""
tests/integration/test_phase19_planning_execution_graph.py — Phase 19 Tests.

Comprehensive integration test suite covering Adaptive Planning & Execution Graph Kernel:
  Class 1: TestPlanModelAndValidation (6 tests)
  Class 2: TestExecutionGraphAndDAG (6 tests)
  Class 3: TestParallelismAndSafety (5 tests)
  Class 4: TestAdaptivePlannerIntegration (5 tests)
  Class 5: TestStepExecutionAndVerificationGate (5 tests)
  Class 6: TestAdaptiveReplanningAndRecovery (5 tests)
  Class 7: TestGraphBudgetLimits (4 tests)
  Class 8: TestSecurityAndGovernanceInvariants (5 tests)
  Class 9: TestPipelineAndReplayIntegration (4 tests)

Total: 45 tests.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from execution import durable_state
from execution.agent_runtime.context import TaskExecutionContext
from execution.agent_runtime.runtime import RealAgentRuntime
from execution.orchestration import (
    AdaptivePlanner,
    CycleDetectedError,
    ExecutionGraph,
    ExecutionPlan,
    ImpossibleDependencyError,
    ParallelExecutionPolicy,
    PlanRevision,
    PlanRiskLevel,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
    PlanValidationError,
    PlanValidationResult,
    PlanValidator,
    ReplanBudgetExhaustedError,
    RepositoryIntelligence,
    SkillCandidate,
    SkillSelectionReason,
    TaskClassification,
    TaskClassifier,
    TaskComplexity,
    TaskRisk,
    get_skill_registry,
)
from execution.providers.base import BaseModelProvider


# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------

class FakePipelineProvider(BaseModelProvider):
    """Deterministic fake provider for Phase 19 testing."""

    def __init__(self, succeed: bool = True, text_response: str = "Plan execution step complete"):
        self.succeed = succeed
        self.text_response = text_response
        self.call_count = 0

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        self.call_count += 1
        if self.succeed:
            if self.call_count == 1:
                return {
                    "thought": "Writing planned module code",
                    "tool": "write_file",
                    "arguments": {"path": "payment.py", "content": "def process_payment(): pass\n"},
                    "usage": {"input_tokens": 20, "output_tokens": 15},
                }
            return {
                "thought": "Phase 19 task execution step completed successfully.",
                "tool": "finish",
                "arguments": {"summary": "Completed planned execution task", "verification": "VERIFIED"},
                "usage": {"input_tokens": 20, "output_tokens": 15},
            }
        else:
            return {
                "thought": "Task failed intentionally for test.",
                "tool": "run_command",
                "arguments": {"command": "exit 1"},
                "usage": {"input_tokens": 15, "output_tokens": 10},
            }

    def complete(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        return self.text_response

    def invoke(self, prompt: str, **kwargs: Any) -> Dict[str, Any]:
        self.call_count += 1
        return {
            "text": self.text_response,
            "tool_calls": [],
            "usage": {"prompt_tokens": 50, "completion_tokens": 50},
        }

    def stream(self, prompt: str, **kwargs: Any):
        yield {"type": "text", "content": self.text_response}
        yield {
            "type": "turn_complete",
            "result": {
                "summary": "Plan execution verified",
                "verification": "VERIFIED" if self.succeed else "FAILED",
                "files": ["src/service.py"],
            },
        }


# ===========================================================================
# Class 1: TestPlanModelAndValidation (6 tests)
# ===========================================================================

class TestPlanModelAndValidation:

    def test_valid_plan_step_creation(self):
        step = PlanStep(
            step_id="step_alpha",
            objective="Refactor auth token parser",
            description="Extract token validation into separate function",
            required_inputs=["token_string"],
            expected_outputs=["TokenPayload"],
            dependencies=[],
            allowed_skills=["refactor_safe"],
            allowed_tools=["read_file", "write_file", "run_tests"],
            risk_level=PlanRiskLevel.LOW,
        )
        assert step.step_id == "step_alpha"
        assert step.status == PlanStepStatus.PENDING
        assert step.can_retry() is True
        step.validate()

    def test_step_empty_objective_raises(self):
        step = PlanStep(step_id="s1", objective="")
        with pytest.raises(PlanValidationError, match="objective"):
            step.validate()

    def test_duplicate_step_id_raises(self):
        plan = ExecutionPlan(plan_id="p1", task_id="t1", tenant_id="tenant_1", objective="Do work")
        s1 = PlanStep(step_id="step_common", objective="Task A")
        s2 = PlanStep(step_id="step_common", objective="Task B")
        plan.add_step(s1)
        with pytest.raises(PlanValidationError, match="Duplicate step ID"):
            plan.add_step(s2)

    def test_missing_dependency_raises(self):
        plan = ExecutionPlan(plan_id="p1", task_id="t1", tenant_id="tenant_1", objective="Do work")
        s1 = PlanStep(step_id="step_1", objective="Task A", dependencies=["step_nonexistent"])
        plan.add_step(s1)
        validator = PlanValidator()
        res = validator.validate(plan)
        assert res.valid is False
        assert any("non-existent" in err for err in res.errors)

    def test_self_dependency_raises(self):
        step = PlanStep(step_id="step_self", objective="Loop", dependencies=["step_self"])
        with pytest.raises(ImpossibleDependencyError, match="cannot depend on itself"):
            step.validate()

    def test_plan_serialization_and_deserialization(self):
        plan = ExecutionPlan(
            plan_id="plan_serde",
            task_id="task_1",
            tenant_id="tenant_x",
            title="Refactor Session Architecture",
            objective="Migrate session store from memory to Redis",
        )
        s1 = PlanStep(step_id="step_1", objective="Create redis client")
        s2 = PlanStep(step_id="step_2", objective="Update session store", dependencies=["step_1"])
        plan.add_step(s1)
        plan.add_step(s2)
        plan.record_revision(
            reason="Initial validated plan",
            affected_steps=["step_1", "step_2"],
            preserved_steps=[],
            newly_created_steps=["step_1", "step_2"],
            removed_steps=[],
        )

        d = plan.as_dict()
        assert d["plan_id"] == "plan_serde"
        assert d["step_count"] == 2
        assert len(d["revision_history"]) == 1

        restored = ExecutionPlan.from_dict(d)
        assert restored.plan_id == "plan_serde"
        assert len(restored.steps) == 2
        assert restored.steps["step_2"].dependencies == ["step_1"]
        assert len(restored.revision_history) == 1


# ===========================================================================
# Class 2: TestExecutionGraphAndDAG (6 tests)
# ===========================================================================

class TestExecutionGraphAndDAG:

    def test_topological_sort_linear(self):
        plan = ExecutionPlan(plan_id="p_lin", task_id="t1", tenant_id="tenant_1", objective="Linear")
        s1 = PlanStep(step_id="A", objective="Step A")
        s2 = PlanStep(step_id="B", objective="Step B", dependencies=["A"])
        s3 = PlanStep(step_id="C", objective="Step C", dependencies=["B"])
        plan.add_step(s1)
        plan.add_step(s2)
        plan.add_step(s3)

        graph = ExecutionGraph(plan)
        topo = graph.topological_sort()
        ids = [s.step_id for s in topo]
        assert ids == ["A", "B", "C"]

    def test_topological_sort_diamond(self):
        # A -> B, A -> C, B -> D, C -> D
        plan = ExecutionPlan(plan_id="p_dia", task_id="t1", tenant_id="tenant_1", objective="Diamond")
        s_a = PlanStep(step_id="A", objective="Step A")
        s_b = PlanStep(step_id="B", objective="Step B", dependencies=["A"])
        s_c = PlanStep(step_id="C", objective="Step C", dependencies=["A"])
        s_d = PlanStep(step_id="D", objective="Step D", dependencies=["B", "C"])
        plan.add_step(s_a)
        plan.add_step(s_b)
        plan.add_step(s_c)
        plan.add_step(s_d)

        graph = ExecutionGraph(plan)
        topo = graph.topological_sort()
        ids = [s.step_id for s in topo]
        assert ids[0] == "A"
        assert ids[-1] == "D"
        assert set(ids[1:3]) == {"B", "C"}

    def test_cycle_detection_raises_cycle_error(self):
        plan = ExecutionPlan(plan_id="p_cyc", task_id="t1", tenant_id="tenant_1", objective="Cycle")
        s1 = PlanStep(step_id="A", objective="Step A", dependencies=["B"])
        s2 = PlanStep(step_id="B", objective="Step B", dependencies=["A"])
        plan.add_step(s1)
        plan.add_step(s2)

        with pytest.raises(CycleDetectedError, match="Cycle detected"):
            ExecutionGraph(plan)

    def test_ready_steps_initial(self):
        plan = ExecutionPlan(plan_id="p_rdy", task_id="t1", tenant_id="tenant_1", objective="Ready")
        s1 = PlanStep(step_id="A", objective="Root A")
        s2 = PlanStep(step_id="B", objective="Root B")
        s3 = PlanStep(step_id="C", objective="Child C", dependencies=["A"])
        plan.add_step(s1)
        plan.add_step(s2)
        plan.add_step(s3)

        graph = ExecutionGraph(plan)
        ready = graph.get_ready_steps()
        ready_ids = {s.step_id for s in ready}
        assert ready_ids == {"A", "B"}

    def test_step_success_unblocks_dependents(self):
        plan = ExecutionPlan(plan_id="p_unblk", task_id="t1", tenant_id="tenant_1", objective="Unblock")
        s1 = PlanStep(step_id="A", objective="Root A")
        s2 = PlanStep(step_id="B", objective="Child B", dependencies=["A"])
        plan.add_step(s1)
        plan.add_step(s2)

        graph = ExecutionGraph(plan)
        assert len(graph.get_ready_steps()) == 1
        assert graph.get_ready_steps()[0].step_id == "A"

        # Complete step A
        graph.mark_step_running("A")
        graph.mark_step_succeeded("A", result={"status": "OK"})

        # Step B should now be ready
        ready_now = graph.get_ready_steps()
        assert len(ready_now) == 1
        assert ready_now[0].step_id == "B"

    def test_step_failure_marks_downstream_blocked(self):
        plan = ExecutionPlan(plan_id="p_fail", task_id="t1", tenant_id="tenant_1", objective="Fail Block")
        s1 = PlanStep(step_id="A", objective="Root A")
        s2 = PlanStep(step_id="B", objective="Child B", dependencies=["A"])
        s3 = PlanStep(step_id="C", objective="Grandchild C", dependencies=["B"])
        plan.add_step(s1)
        plan.add_step(s2)
        plan.add_step(s3)

        graph = ExecutionGraph(plan)
        graph.mark_step_running("A")
        graph.mark_step_failed("A", error="Database connection error")

        assert s1.status == PlanStepStatus.FAILED
        assert s2.status == PlanStepStatus.BLOCKED
        assert s3.status == PlanStepStatus.BLOCKED
        assert graph.has_failures() is True


# ===========================================================================
# Class 3: TestParallelismAndSafety (5 tests)
# ===========================================================================

class TestParallelismAndSafety:

    def test_independent_steps_can_run_parallel(self):
        s1 = PlanStep(
            step_id="step_a",
            objective="Modify User model",
            target_files=["src/models/user.py"],
            risk_level=PlanRiskLevel.LOW,
        )
        s2 = PlanStep(
            step_id="step_b",
            objective="Modify Order model",
            target_files=["src/models/order.py"],
            risk_level=PlanRiskLevel.LOW,
        )
        can_run, reason = ParallelExecutionPolicy.check_parallel_safety(s1, s2)
        assert can_run is True
        assert "disjoint" in reason.lower()

    def test_overlapping_target_files_must_serialize(self):
        s1 = PlanStep(
            step_id="step_a",
            objective="Refactor Auth Token",
            target_files=["src/auth/token.py"],
            risk_level=PlanRiskLevel.LOW,
        )
        s2 = PlanStep(
            step_id="step_b",
            objective="Add Auth Refresh Token",
            target_files=["src/auth/token.py"],
            risk_level=PlanRiskLevel.LOW,
        )
        can_run, reason = ParallelExecutionPolicy.check_parallel_safety(s1, s2)
        assert can_run is False
        assert "overlapping files" in reason.lower()

    def test_high_risk_step_must_serialize(self):
        s1 = PlanStep(
            step_id="step_a",
            objective="Critical schema migration",
            target_files=["db/migration.sql"],
            risk_level=PlanRiskLevel.HIGH,
        )
        s2 = PlanStep(
            step_id="step_b",
            objective="Update documentation",
            target_files=["docs/readme.md"],
            risk_level=PlanRiskLevel.LOW,
        )
        can_run, reason = ParallelExecutionPolicy.check_parallel_safety(s1, s2)
        assert can_run is False
        assert "requires serialized execution" in reason.lower()

    def test_parallel_execution_tiers_grouping(self):
        plan = ExecutionPlan(plan_id="p_tier", task_id="t1", tenant_id="t", objective="Tiers")
        # Tier 0: A1, A2
        # Tier 1: B1 (deps: A1), B2 (deps: A2)
        # Tier 2: C (deps: B1, B2)
        s_a1 = PlanStep(step_id="A1", objective="A1")
        s_a2 = PlanStep(step_id="A2", objective="A2")
        s_b1 = PlanStep(step_id="B1", objective="B1", dependencies=["A1"])
        s_b2 = PlanStep(step_id="B2", objective="B2", dependencies=["A2"])
        s_c = PlanStep(step_id="C", objective="C", dependencies=["B1", "B2"])
        for s in (s_a1, s_a2, s_b1, s_b2, s_c):
            plan.add_step(s)

        graph = ExecutionGraph(plan)
        tiers = graph.get_parallel_execution_tiers()
        assert len(tiers) == 3
        tier0_ids = {s.step_id for s in tiers[0]}
        tier1_ids = {s.step_id for s in tiers[1]}
        tier2_ids = {s.step_id for s in tiers[2]}
        assert tier0_ids == {"A1", "A2"}
        assert tier1_ids == {"B1", "B2"}
        assert tier2_ids == {"C"}

    def test_dependent_steps_cannot_run_parallel(self):
        plan = ExecutionPlan(plan_id="p_dep", task_id="t1", tenant_id="t", objective="Dep")
        s1 = PlanStep(step_id="A", objective="Root A")
        s2 = PlanStep(step_id="B", objective="Child B", dependencies=["A"])
        plan.add_step(s1)
        plan.add_step(s2)

        graph = ExecutionGraph(plan)
        can_run, reason = graph.check_parallel_safety(s1, s2)
        assert can_run is False
        assert "precedes" in reason.lower()


# ===========================================================================
# Class 4: TestAdaptivePlannerIntegration (5 tests)
# ===========================================================================

class TestAdaptivePlannerIntegration:

    def test_planner_generates_simple_graph(self):
        planner = AdaptivePlanner()
        graph = planner.plan_task(
            task_id="task_simple",
            tenant_id="tenant_p",
            objective="Fix typo in README.md",
        )
        plan = graph.plan
        assert plan.step_count() == 2
        steps = plan.get_step_list()
        assert steps[0].step_id == "step_1_execute"
        assert steps[1].step_id == "step_2_verify"
        assert steps[1].dependencies == ["step_1_execute"]

    def test_planner_generates_complex_graph(self):
        planner = AdaptivePlanner()
        classifier = TaskClassifier()
        classification = classifier.classify("Refactor entire authentication subsystem and migrate session cookies")
        graph = planner.plan_task(
            task_id="task_complex",
            tenant_id="tenant_p",
            objective="Refactor entire authentication subsystem and migrate session cookies",
            classification=classification,
        )
        plan = graph.plan
        assert plan.step_count() == 4
        step_ids = [s.step_id for s in plan.get_step_list()]
        assert "step_1_analyze" in step_ids
        assert "step_2_implement" in step_ids
        assert "step_3_test" in step_ids
        assert "step_4_final_verify" in step_ids

    def test_planner_attaches_governed_skills(self):
        planner = AdaptivePlanner()
        skill = get_skill_registry().get_skill("refactor_safe")
        cand = SkillCandidate(skill=skill, score=0.9, reason=SkillSelectionReason.TRIGGER_MATCH, details="match")
        graph = planner.plan_task(
            task_id="task_skills",
            tenant_id="tenant_p",
            objective="Safe refactoring of data models",
            skills=[cand],
        )
        plan = graph.plan
        assert any("refactor_safe" in s.allowed_skills for s in plan.steps.values())

    def test_planner_respects_repo_intelligence_files(self):
        planner = AdaptivePlanner()
        with tempfile.TemporaryDirectory() as tmpdir:
            f1 = Path(tmpdir) / "app.py"
            f1.write_text("def run(): pass\n", encoding="utf-8")
            repo_intel = RepositoryIntelligence(tmpdir)
            graph = planner.plan_task(
                task_id="task_intel",
                tenant_id="tenant_p",
                objective="Refactor app runner",
                repo_intelligence=repo_intel,
            )
            plan = graph.plan
            # Target files should include app.py
            all_targets = [f for s in plan.steps.values() for f in s.target_files]
            assert any("app.py" in t for t in all_targets)

    def test_planner_empty_objective_raises(self):
        planner = AdaptivePlanner()
        with pytest.raises(PlanValidationError, match="objective cannot be empty"):
            planner.plan_task(task_id="t", tenant_id="t", objective="")


# ===========================================================================
# Class 5: TestStepExecutionAndVerificationGate (5 tests)
# ===========================================================================

class TestStepExecutionAndVerificationGate:

    def test_model_completion_alone_not_enough_for_success(self):
        runtime = RealAgentRuntime()
        step = PlanStep(
            step_id="step_must_verify",
            objective="Critical patch",
            verification_criteria={"status": "FAILED", "error": "Assertion failed: expected 200, got 500"},
        )
        res = runtime.execute_plan_step(step)
        assert res["status"] == "FAILED"
        assert step.status == PlanStepStatus.FAILED
        assert "500" in step.error_message

    def test_step_verification_success_transitions_succeeded(self):
        runtime = RealAgentRuntime()
        step = PlanStep(
            step_id="step_pass",
            objective="Working feature",
            verification_criteria={"type": "unit_tests", "pass": True},
        )
        res = runtime.execute_plan_step(step)
        assert res["status"] == "SUCCEEDED"
        assert step.status == PlanStepStatus.SUCCEEDED
        assert step.completed_at is not None

    def test_step_verification_failed(self):
        runtime = RealAgentRuntime()
        step = PlanStep(
            step_id="step_fail_ver",
            objective="Buggy feature",
            verification_criteria={"fail": True, "error": "SyntaxError on line 12"},
        )
        res = runtime.execute_plan_step(step)
        assert res["status"] == "FAILED"
        assert step.status == PlanStepStatus.FAILED
        assert "SyntaxError" in step.error_message

    def test_runtime_execute_plan_step_emits_replay_events(self):
        runtime = RealAgentRuntime()
        step = PlanStep(
            step_id="step_events",
            objective="Event logging verification",
            allowed_skills=["diagnostic_repair"],
        )
        runtime.execute_plan_step(step)
        event_types = [e["event_type"] for e in runtime.replay_events]
        assert "step.started" in event_types
        assert "step.verified" in event_types

    def test_step_verification_criteria_enforcement(self):
        runtime = RealAgentRuntime()
        step = PlanStep(step_id="s1", objective="Simple step")
        ok, msg = runtime.verify_plan_step(step)
        assert ok is True

        step_fail = PlanStep(step_id="s2", objective="Bad step", verification_criteria={"status": False, "error": "Rejected"})
        ok_fail, msg_fail = runtime.verify_plan_step(step_fail)
        assert ok_fail is False
        assert "Rejected" in msg_fail


# ===========================================================================
# Class 6: TestAdaptiveReplanningAndRecovery (5 tests)
# ===========================================================================

class TestAdaptiveReplanningAndRecovery:

    def test_step_failure_triggers_replan_retry(self):
        planner = AdaptivePlanner(max_revisions=3, max_step_retries=2)
        plan = ExecutionPlan(plan_id="p1", task_id="t1", tenant_id="tenant_1", objective="Test Replan")
        s1 = PlanStep(step_id="step_retry", objective="Flaky step", max_retries=2)
        plan.add_step(s1)
        graph = ExecutionGraph(plan)

        # Step fails attempt 1
        revised = planner.replan_step_failure(graph, failed_step_id="step_retry", failure_reason="Temporary timeout")
        assert revised.revision == 2
        assert s1.status == PlanStepStatus.READY
        assert s1.retries_attempted == 1

    def test_exhausted_retries_triggers_repair_step_injection(self):
        planner = AdaptivePlanner(max_revisions=3, max_step_retries=1)
        plan = ExecutionPlan(plan_id="p1", task_id="t1", tenant_id="tenant_1", objective="Repair Test")
        s1 = PlanStep(step_id="step_init", objective="Setup")
        s2 = PlanStep(step_id="step_work", objective="Work", dependencies=["step_init"], max_retries=0)
        s3 = PlanStep(step_id="step_verify", objective="Verify", dependencies=["step_work"])
        plan.add_step(s1)
        plan.add_step(s2)
        plan.add_step(s3)
        graph = ExecutionGraph(plan)

        # Mark s1 succeeded
        graph.mark_step_succeeded("step_init")

        # Step 2 fails with 0 retries remaining
        revised = planner.replan_step_failure(graph, failed_step_id="step_work", failure_reason="Compilation error in module X")
        assert s2.status == PlanStepStatus.FAILED
        assert revised.step_count() == 4
        # Injected repair step exists
        repair_step_id = f"repair_step_work_{revised.revision}"
        assert repair_step_id in revised.steps
        # Dependent step s3 rewired to depend on repair step
        assert repair_step_id in revised.steps["step_verify"].dependencies

    def test_preserved_steps_stay_preserved_during_replan(self):
        planner = AdaptivePlanner(max_revisions=3)
        plan = ExecutionPlan(plan_id="p_pres", task_id="t1", tenant_id="tenant_1", objective="Preserve")
        s1 = PlanStep(step_id="s1_done", objective="Completed Step")
        s2 = PlanStep(step_id="s2_fail", objective="Failing Step", dependencies=["s1_done"], max_retries=0)
        plan.add_step(s1)
        plan.add_step(s2)
        graph = ExecutionGraph(plan)

        graph.mark_step_succeeded("s1_done", result={"files": ["a.py"]})

        revised = planner.replan_step_failure(graph, failed_step_id="s2_fail", failure_reason="Unit test failure")
        assert revised.steps["s1_done"].status == PlanStepStatus.SUCCEEDED
        assert revised.steps["s1_done"].result == {"files": ["a.py"]}

    def test_revision_history_records_replan_details(self):
        planner = AdaptivePlanner(max_revisions=3)
        plan = ExecutionPlan(plan_id="p_hist", task_id="t1", tenant_id="tenant_1", objective="History")
        s1 = PlanStep(step_id="s1", objective="Step 1", max_retries=0)
        plan.add_step(s1)
        graph = ExecutionGraph(plan)

        revised = planner.replan_step_failure(graph, failed_step_id="s1", failure_reason="Crash in runtime")
        assert len(revised.revision_history) == 1
        rev = revised.revision_history[0]
        assert rev.revision_id == 2
        assert "Crash in runtime" in rev.reason
        assert "s1" in rev.affected_steps

    def test_replan_exhaustion_raises_budget_error(self):
        planner = AdaptivePlanner(max_revisions=2)
        plan = ExecutionPlan(plan_id="p_exh", task_id="t1", tenant_id="tenant_1", objective="Exhaust", revision=2)
        s1 = PlanStep(step_id="s1", objective="Step 1", max_retries=0)
        plan.add_step(s1)
        graph = ExecutionGraph(plan)

        with pytest.raises(ReplanBudgetExhaustedError, match="Maximum plan revisions"):
            planner.replan_step_failure(graph, failed_step_id="s1", failure_reason="Another failure")


# ===========================================================================
# Class 7: TestGraphBudgetLimits (4 tests)
# ===========================================================================

class TestGraphBudgetLimits:

    def test_graph_step_count_limit_enforced(self):
        validator = PlanValidator(max_steps=3)
        plan = ExecutionPlan(plan_id="p_lim", task_id="t1", tenant_id="t", objective="Limit")
        for i in range(5):
            plan.add_step(PlanStep(step_id=f"step_{i}", objective=f"Step {i}"))
        res = validator.validate(plan)
        assert res.valid is False
        assert any("exceeds configured ceiling" in e for e in res.errors)

    def test_graph_max_depth_limit_enforced(self):
        validator = PlanValidator(max_steps=10, max_depth=2)
        plan = ExecutionPlan(plan_id="p_dep_lim", task_id="t1", tenant_id="t", objective="Depth")
        s1 = PlanStep(step_id="D1", objective="D1")
        s2 = PlanStep(step_id="D2", objective="D2", dependencies=["D1"])
        s3 = PlanStep(step_id="D3", objective="D3", dependencies=["D2"])
        plan.add_step(s1)
        plan.add_step(s2)
        plan.add_step(s3)
        res = validator.validate(plan)
        assert res.valid is False
        assert any("exceeds maximum allowable depth" in e for e in res.errors)

    def test_step_retry_budget_enforced(self):
        step = PlanStep(step_id="s1", objective="Test retry budget", max_retries=1)
        assert step.can_retry() is True
        step.record_attempt(error="Err 1")
        assert step.can_retry() is False

    def test_max_revisions_limit_enforced(self):
        planner = AdaptivePlanner(max_revisions=1)
        plan = ExecutionPlan(plan_id="p", task_id="t", tenant_id="t", objective="Exh", revision=1)
        s1 = PlanStep(step_id="s1", objective="s1", max_retries=0)
        plan.add_step(s1)
        graph = ExecutionGraph(plan)

        with pytest.raises(ReplanBudgetExhaustedError):
            planner.replan_step_failure(graph, failed_step_id="s1", failure_reason="Exceeded")


# ===========================================================================
# Class 8: TestSecurityAndGovernanceInvariants (5 tests)
# ===========================================================================

class TestSecurityAndGovernanceInvariants:

    def test_high_risk_step_auto_requires_hitl(self):
        step_high = PlanStep(step_id="s_hi", objective="Deploy schema", risk_level=PlanRiskLevel.HIGH)
        assert step_high.requires_hitl is True

        step_crit = PlanStep(step_id="s_crit", objective="Wipe cache", risk_level=PlanRiskLevel.CRITICAL)
        assert step_crit.requires_hitl is True

        step_low = PlanStep(step_id="s_lo", objective="Read logs", risk_level=PlanRiskLevel.LOW)
        assert step_low.requires_hitl is False

    def test_plan_tenant_isolation(self):
        plan_a = ExecutionPlan(plan_id="p_a", task_id="t_a", tenant_id="tenant_A", objective="Secret A")
        plan_b = ExecutionPlan(plan_id="p_b", task_id="t_b", tenant_id="tenant_B", objective="Secret B")
        assert plan_a.tenant_id != plan_b.tenant_id
        assert plan_a.tenant_id == "tenant_A"

    def test_secret_scrubbing_in_plan_and_revisions(self):
        plan = ExecutionPlan(
            plan_id="p_sec",
            task_id="t_sec",
            tenant_id="t",
            title="Deploy sk-proj-1234567890abcdef1234567890abcdef",
            objective="Using Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 token",
        )
        step = PlanStep(
            step_id="s_sec",
            objective="Run with password=SuperSecretPassword123",
            description="API key is sk-proj-abcdefabcdefabcdefabcdef",
        )
        plan.add_step(step)
        plan.record_revision(
            reason="Updated password=AnotherSecretPassword456",
            affected_steps=["s_sec"],
            preserved_steps=[],
            newly_created_steps=[],
            removed_steps=[],
        )

        d = plan.as_dict()
        d_str = json.dumps(d)
        assert "sk-proj-1234567890abcdef1234567890abcdef" not in d_str
        assert "[REDACTED_API_KEY]" in d_str
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in d_str
        assert "[REDACTED_TOKEN]" in d_str
        assert "SuperSecretPassword123" not in d_str
        assert "[REDACTED_PASSWORD]" in d_str

    def test_memory_says_worked_does_not_skip_verifier(self):
        # Historical memory claims success, but step verification must still execute independently
        runtime = RealAgentRuntime()
        step = PlanStep(
            step_id="step_memory_check",
            objective="Verify invariant: Memory says worked does not bypass verifier",
            verification_criteria={"status": "FAILED", "error": "Fresh verifier failed"},
        )
        # Even if memory claimed it worked, step verification failure must fail the step
        res = runtime.execute_plan_step(step)
        assert res["status"] == "FAILED"
        assert step.status == PlanStepStatus.FAILED

    def test_target_files_path_normalization(self):
        step = PlanStep(
            step_id="s_path",
            objective="Path test",
            target_files=["src\\auth\\token.py", "tests\\test_auth.py"],
            mutation_scope=["config\\settings.json"],
        )
        assert "\\" not in step.target_files[0]
        assert step.target_files[0] == "src/auth/token.py"
        assert step.mutation_scope[0] == "config/settings.json"


# ===========================================================================
# Class 9: TestPipelineAndReplayIntegration (4 tests)
# ===========================================================================

class TestPipelineAndReplayIntegration:

    def test_pipeline_generates_plan_and_graph_in_evidence(self):
        from routes.tasks import run_task_pipeline

        record = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_p19_plan",
            payload={
                "prompt": "Refactor user authentication service",
                "execution_constraints": {"allow_plain_workspace": True},
            },
        )
        tid = record["execution_id"]
        provider = FakePipelineProvider(succeed=True)

        res = run_task_pipeline(task_id=tid, tenant_id="tenant_p19_plan", provider=provider)
        orch = res.get("orchestration", {})
        assert "execution_plan" in orch
        assert "execution_graph" in orch
        plan_dict = orch["execution_plan"]
        assert plan_dict["step_count"] >= 2

    def test_pipeline_records_plan_created_and_validated_events(self):
        from routes.tasks import run_task_pipeline

        record = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_p19_events",
            payload={
                "prompt": "Refactor payment processing module",
                "execution_constraints": {"allow_plain_workspace": True},
            },
        )
        tid = record["execution_id"]
        provider = FakePipelineProvider(succeed=True)

        res = run_task_pipeline(task_id=tid, tenant_id="tenant_p19_events", provider=provider)
        replay = res.get("replay_events", [])
        event_types = [e.get("event_type") for e in replay]

        assert "plan.created" in event_types
        assert "plan.validated" in event_types
        assert "plan.completed" in event_types

    def test_runtime_initializes_planner(self):
        runtime = RealAgentRuntime()
        assert hasattr(runtime, "planner")
        assert runtime.planner is not None

    def test_end_to_end_complex_engineering_graph_lifecycle(self):
        planner = AdaptivePlanner()
        graph = planner.plan_task(
            task_id="task_e2e",
            tenant_id="tenant_e2e",
            objective="Complex migration of database layer",
        )
        runtime = RealAgentRuntime()

        # Step through the graph
        while not graph.is_completed():
            ready = graph.get_ready_steps()
            if not ready:
                break
            for step in ready:
                graph.mark_step_running(step.step_id)
                res = runtime.execute_plan_step(step)
                if res["status"] == "SUCCEEDED":
                    graph.mark_step_succeeded(step.step_id, result=res)
                else:
                    graph.mark_step_failed(step.step_id, error=res.get("error", "Error"))

        assert graph.is_completed() is True
        assert graph.has_failures() is False
        assert graph.plan.status == PlanStatus.COMPLETED
