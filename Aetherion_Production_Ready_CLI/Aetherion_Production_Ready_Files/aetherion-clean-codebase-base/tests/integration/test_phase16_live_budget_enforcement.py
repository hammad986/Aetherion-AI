"""
tests/integration/test_phase16_live_budget_enforcement.py — Phase 16 Tests.

Tests Live Budget Enforcement & Adaptive Escalation:
  Class 1: TestBudgetEnforcerLifecycle (9 tests)
  Class 2: TestLiveModelCallEnforcement (5 tests)
  Class 3: TestLiveToolCallEnforcement (4 tests)
  Class 4: TestLiveRepairEnforcement (4 tests)
  Class 5: TestLiveEscalationEnforcement (4 tests)
  Class 6: TestRuntimeBudgetEnforcement (5 tests)
  Class 7: TestPipelineBudgetIntegration (5 tests)
  Class 8: TestAdaptiveEscalation (4 tests)

Total: 40 tests.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from execution import durable_state
from execution.agent_runtime.context import TaskExecutionContext
from execution.agent_runtime.runtime import RealAgentRuntime
from execution.orchestration import (
    BudgetEnforcer,
    BudgetExhaustedError,
    EscalationChain,
    EscalationPolicy,
    EscalationReason,
    ModelProfile,
    ModelRole,
    ProfileTier,
    RunBudget,
    UsageAccounting,
)
from execution.providers.base import BaseModelProvider


# ---------------------------------------------------------------------------
# Test Providers & Helpers
# ---------------------------------------------------------------------------

class FakeBudgetProvider(BaseModelProvider):
    """Deterministic fake provider for budget testing."""

    def __init__(self, responses: Optional[List[Dict[str, Any]]] = None) -> None:
        self.responses = list(responses or [])
        self.call_count = 0

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        self.call_count += 1
        if self.responses:
            idx = min(self.call_count - 1, len(self.responses) - 1)
            return dict(self.responses[idx])
        return {
            "thought": f"Iteration {self.call_count}",
            "tool": "finish",
            "arguments": {"summary": f"Completed at step {self.call_count}"},
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

    def complete(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        return "fake completion"


def _make_context(
    tmpdir: str,
    execution_budget: Optional[Dict[str, Any]] = None,
    execution_constraints: Optional[Dict[str, Any]] = None,
) -> TaskExecutionContext:
    return TaskExecutionContext(
        task_id=f"p16_task_{os.urandom(4).hex()}",
        objective="Verify live budget enforcement",
        workspace_dir=tmpdir,
        tenant_id="tenant_p16",
        session_id=f"sess_{os.urandom(4).hex()}",
        repo_root=tmpdir,
        execution_constraints=execution_constraints or {"allow_plain_workspace": True, "max_iterations": 10},
        execution_budget=execution_budget or {},
    )


# ---------------------------------------------------------------------------
# Class 1: TestBudgetEnforcerLifecycle (9 tests)
# ---------------------------------------------------------------------------

class TestBudgetEnforcerLifecycle:
    """Test BudgetEnforcer initialization, tracking, and serialization."""

    def test_enforcer_init_from_empty_budget_uses_defaults(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={},
            task_id="t1",
            tenant_id="ten1",
            profile_name="standard",
            rationale="default_test",
        )
        assert enforcer.run_budget.max_model_calls == RunBudget.DEFAULT_MAX_MODEL_CALLS
        assert enforcer.run_budget.max_tool_calls == RunBudget.DEFAULT_MAX_TOOL_CALLS
        assert enforcer.run_budget.max_repair_attempts == RunBudget.DEFAULT_MAX_REPAIR_ATTEMPTS
        assert enforcer.run_budget.max_tokens == RunBudget.DEFAULT_MAX_TOKENS
        assert enforcer.total_model_calls == 0
        assert enforcer.total_tool_calls == 0

    def test_enforcer_init_from_custom_budget(self):
        custom = {
            "max_model_calls": 5,
            "max_tool_calls": 12,
            "max_repair_attempts": 1,
            "max_tokens": 1000,
            "max_escalation_attempts": 3,
            "max_execution_time_sec": 60.0,
        }
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget=custom,
            task_id="t2",
            tenant_id="ten1",
            profile_name="mini",
            rationale="custom_test",
        )
        assert enforcer.run_budget.max_model_calls == 5
        assert enforcer.run_budget.max_tool_calls == 12
        assert enforcer.run_budget.max_repair_attempts == 1
        assert enforcer.run_budget.max_tokens == 1000

    def test_enforcer_start_turn_and_after_model_call(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_model_calls": 10},
            task_id="t3",
            tenant_id="ten1",
            profile_name="standard",
            rationale="turn_test",
        )
        enforcer.start_turn()
        time.sleep(0.01)
        enforcer.after_model_call(tokens_in=100, tokens_out=50, role="executor")

        assert enforcer.total_model_calls == 1
        assert enforcer.total_tokens_used == 150
        assert len(enforcer.usage_accounting.turns) == 1
        turn = enforcer.usage_accounting.turns[0]
        assert turn.tokens_in == 100
        assert turn.tokens_out == 50
        assert turn.elapsed_sec is not None and turn.elapsed_sec >= 0.005

    def test_enforcer_after_tool_call(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_tool_calls": 10},
            task_id="t4",
            tenant_id="ten1",
            profile_name="standard",
            rationale="tool_test",
        )
        enforcer.after_tool_call("write_file", ok=True)
        enforcer.after_tool_call("run_tests", ok=False)
        assert enforcer.total_tool_calls == 2

    def test_enforcer_after_repair_attempt(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_repair_attempts": 2},
            task_id="t5",
            tenant_id="ten1",
            profile_name="standard",
            rationale="repair_test",
        )
        enforcer.after_repair_attempt()
        assert enforcer.total_repair_attempts == 1

    def test_enforcer_after_escalation(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_escalation_attempts": 2},
            task_id="t6",
            tenant_id="ten1",
            profile_name="standard",
            rationale="esc_test",
        )
        enforcer.after_escalation("BUDGET_EXHAUSTED")
        assert enforcer.total_escalations == 1
        assert "BUDGET_EXHAUSTED" in enforcer.usage_accounting.escalation_reasons

    def test_enforcer_remaining_calculation(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_model_calls": 10, "max_tokens": 500},
            task_id="t7",
            tenant_id="ten1",
            profile_name="standard",
            rationale="remaining_test",
        )
        enforcer.after_model_call(tokens_in=100, tokens_out=50)
        rem = enforcer.remaining()
        assert rem["model_calls_remaining"] == 9
        assert rem["tokens_remaining"] == 350

    def test_enforcer_budget_summary(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_model_calls": 8},
            task_id="t8",
            tenant_id="ten1",
            profile_name="standard",
            rationale="summary_test",
        )
        summary = enforcer.budget_summary()
        assert "limits" in summary
        assert "usage" in summary
        assert "remaining" in summary
        assert summary["limits"]["max_model_calls"] == 8

    def test_enforcer_final_evidence_structure_and_no_secrets(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_model_calls": 5},
            task_id="t9",
            tenant_id="ten1",
            profile_name="standard",
            rationale="evidence_test",
        )
        enforcer.after_model_call(tokens_in=50, tokens_out=25)
        ev = enforcer.final_evidence()
        assert ev["type"] == "budget_enforcement_final"
        assert ev["task_id"] == "t9"
        assert ev["tenant_id"] == "ten1"
        assert ev["initial_profile"] == "standard"
        assert "usage" in ev
        assert "budget" in ev
        # Verify no secret leakage
        s = json.dumps(ev)
        assert "api_key" not in s
        assert "bearer" not in s.lower()


# ---------------------------------------------------------------------------
# Class 2: TestLiveModelCallEnforcement (5 tests)
# ---------------------------------------------------------------------------

class TestLiveModelCallEnforcement:
    """Test model call limit and token limit enforcement."""

    def test_model_calls_within_limit_passes(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_model_calls": 3},
            task_id="mc1",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        for _ in range(3):
            enforcer.after_model_call(tokens_in=10, tokens_out=10)
            enforcer.check()  # should not raise
        assert enforcer.total_model_calls == 3

    def test_model_calls_exceeding_limit_raises_budget_exhausted(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_model_calls": 2},
            task_id="mc2",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_model_call()
        enforcer.check()
        enforcer.after_model_call()
        enforcer.check()

        # 3rd call exceeds max_model_calls = 2
        enforcer.after_model_call()
        with pytest.raises(BudgetExhaustedError) as exc_info:
            enforcer.check()
        err = exc_info.value
        assert err.limit_name == "max_model_calls"
        assert err.current_value == 3
        assert err.configured_max == 2

    def test_budget_exhausted_error_as_dict(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_model_calls": 1},
            task_id="mc3",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_model_call()
        enforcer.after_model_call()
        with pytest.raises(BudgetExhaustedError) as exc_info:
            enforcer.check()
        d = exc_info.value.as_dict()
        assert d["type"] == "budget_exhausted"
        assert d["limit_name"] == "max_model_calls"
        assert d["current_value"] == 2
        assert d["configured_max"] == 1

    def test_token_limit_within_bounds_passes(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_tokens": 100},
            task_id="mc4",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_model_call(tokens_in=40, tokens_out=40)
        enforcer.check()  # 80 <= 100
        assert enforcer.total_tokens_used == 80

    def test_token_limit_exceeded_raises_budget_exhausted(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_tokens": 100},
            task_id="mc5",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_model_call(tokens_in=60, tokens_out=60)  # 120 > 100
        with pytest.raises(BudgetExhaustedError) as exc_info:
            enforcer.check()
        assert exc_info.value.limit_name == "max_tokens"
        assert exc_info.value.current_value == 120


# ---------------------------------------------------------------------------
# Class 3: TestLiveToolCallEnforcement (4 tests)
# ---------------------------------------------------------------------------

class TestLiveToolCallEnforcement:
    """Test tool call limits and tracking."""

    def test_tool_calls_within_limit_passes(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_tool_calls": 3},
            task_id="tc1",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        for i in range(3):
            enforcer.after_tool_call(f"tool_{i}")
            enforcer.check()
        assert enforcer.total_tool_calls == 3

    def test_tool_calls_exceeding_limit_raises(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_tool_calls": 2},
            task_id="tc2",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_tool_call("tool_1")
        enforcer.after_tool_call("tool_2")
        enforcer.after_tool_call("tool_3")
        with pytest.raises(BudgetExhaustedError) as exc_info:
            enforcer.check()
        assert exc_info.value.limit_name == "max_tool_calls"
        assert exc_info.value.current_value == 3

    def test_tool_failure_still_records_tool_call(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_tool_calls": 5},
            task_id="tc3",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_tool_call("failing_cmd", ok=False)
        assert enforcer.total_tool_calls == 1

    def test_exhaustion_event_recorded_in_enforcer(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_tool_calls": 1},
            task_id="tc4",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_tool_call("t1")
        enforcer.after_tool_call("t2")
        with pytest.raises(BudgetExhaustedError):
            enforcer.check()
        assert len(enforcer.run_budget.exhaustion_events) == 1
        ev = enforcer.run_budget.exhaustion_events[0]
        assert ev["limit_name"] == "max_tool_calls"


# ---------------------------------------------------------------------------
# Class 4: TestLiveRepairEnforcement (4 tests)
# ---------------------------------------------------------------------------

class TestLiveRepairEnforcement:
    """Test repair attempt limits and check_repair."""

    def test_repair_attempts_within_limit_passes(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_repair_attempts": 2},
            task_id="rp1",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_repair_attempt()
        enforcer.after_repair_attempt()
        enforcer.check_repair()  # 2 <= 2, passes
        assert enforcer.total_repair_attempts == 2

    def test_repair_attempts_exceeding_limit_raises(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_repair_attempts": 1},
            task_id="rp2",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_repair_attempt()
        enforcer.after_repair_attempt()
        with pytest.raises(BudgetExhaustedError) as exc_info:
            enforcer.check_repair()
        assert exc_info.value.limit_name == "max_repair_attempts"

    def test_check_repair_does_not_fail_on_other_limits(self):
        # max_model_calls=0 but check_repair only checks repairs
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_model_calls": 0, "max_repair_attempts": 2},
            task_id="rp3",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_model_call()
        enforcer.after_repair_attempt()
        enforcer.check_repair()  # should pass because repairs (1) <= max (2)

    def test_repair_turns_marked_in_usage_accounting(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_repair_attempts": 3},
            task_id="rp4",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_model_call(tokens_in=10, tokens_out=10, was_repair=True)
        assert enforcer.usage_accounting.total_repair_attempts == 1
        turn = enforcer.usage_accounting.turns[0]
        assert turn.was_repair is True


# ---------------------------------------------------------------------------
# Class 5: TestLiveEscalationEnforcement (4 tests)
# ---------------------------------------------------------------------------

class TestLiveEscalationEnforcement:
    """Test escalation limits and tracking."""

    def test_escalation_within_limit_passes(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_escalation_attempts": 2},
            task_id="esc1",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_escalation("REASON_1")
        enforcer.after_escalation("REASON_2")
        enforcer.check_escalation()  # 2 <= 2 passes
        assert enforcer.total_escalations == 2

    def test_escalation_exceeding_limit_raises(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_escalation_attempts": 1},
            task_id="esc2",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_escalation("REASON_1")
        enforcer.after_escalation("REASON_2")
        with pytest.raises(BudgetExhaustedError) as exc_info:
            enforcer.check_escalation()
        assert exc_info.value.limit_name == "max_escalation_attempts"

    def test_check_escalation_targeted(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_tool_calls": 0, "max_escalation_attempts": 2},
            task_id="esc3",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_tool_call("t1")
        enforcer.after_escalation("REASON_A")
        enforcer.check_escalation()  # checks only escalations (1 <= 2)

    def test_escalation_turn_flag_tracked(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_escalation_attempts": 2},
            task_id="esc4",
            tenant_id="ten1",
            profile_name="standard",
            rationale="test",
        )
        enforcer.after_model_call(tokens_in=20, tokens_out=20, was_escalation=True)
        assert enforcer.usage_accounting.total_escalations == 1
        assert enforcer.usage_accounting.turns[0].was_escalation is True


# ---------------------------------------------------------------------------
# Class 6: TestRuntimeBudgetEnforcement (5 tests)
# ---------------------------------------------------------------------------

class TestRuntimeBudgetEnforcement:
    """Test RealAgentRuntime live budget enforcement integration."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp(prefix="aeth_p16_rt_")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_runtime_normal_run_creates_budget_enforcer_and_usage_final(self):
        ctx = _make_context(
            self.tmpdir,
            execution_budget={"max_model_calls": 5},
        )
        # Create durable execution record
        durable_state.create(
            kind="agent_task",
            tenant_id=ctx.tenant_id,
            payload={"prompt": "test", "repo_root": self.tmpdir},
        )
        provider = FakeBudgetProvider()
        runtime = RealAgentRuntime(context=ctx, provider=provider)

        events = list(runtime.run_stream("complete immediately"))
        assert runtime.budget_enforcer is not None
        assert runtime.budget_enforcer.total_model_calls == 1

        # Check replay events for orchestration.usage_final
        usage_events = [e for e in runtime.replay_events if e.get("event_type") == "orchestration.usage_final"]
        assert len(usage_events) >= 1
        payload = usage_events[0]["payload"]
        assert payload["type"] == "budget_enforcement_final"
        assert payload["task_id"] == ctx.task_id

    def test_runtime_aborts_when_model_budget_exhausted(self):
        # max_model_calls = 1, but provider does not finish on first step
        ctx = _make_context(
            self.tmpdir,
            execution_budget={"max_model_calls": 1},
            execution_constraints={"allow_plain_workspace": True, "max_iterations": 10},
        )
        durable_state.create(
            kind="agent_task",
            tenant_id=ctx.tenant_id,
            payload={"prompt": "test", "repo_root": self.tmpdir},
        )
        # Provider never finishes — returns bash command
        provider = FakeBudgetProvider(responses=[
            {"thought": "step 1", "tool": "run_command", "arguments": {"command": "echo 1"}},
            {"thought": "step 2", "tool": "run_command", "arguments": {"command": "echo 2"}},
        ])
        runtime = RealAgentRuntime(context=ctx, provider=provider)

        events = list(runtime.run_stream("run commands"))
        # Should have emitted a budget_exhausted event or halted with failed done event
        exhausted_events = [e for e in events if e.get("type") == "budget_exhausted"]
        done_events = [e for e in events if e.get("type") == "done"]

        # In second iteration, budget check raises BudgetExhaustedError
        assert len(exhausted_events) >= 1 or any(e.get("failure_class") == "budget_exhausted" for e in done_events)

        # Check replay events for budget.exhausted
        bex_replay = [e for e in runtime.replay_events if e.get("event_type") == "budget.exhausted"]
        assert len(bex_replay) >= 1
        assert bex_replay[0]["payload"]["limit_name"] == "max_model_calls"

    def test_runtime_records_tool_calls_in_budget_enforcer(self):
        ctx = _make_context(
            self.tmpdir,
            execution_budget={"max_model_calls": 5, "max_tool_calls": 10},
        )
        durable_state.create(
            kind="agent_task",
            tenant_id=ctx.tenant_id,
            payload={"prompt": "test", "repo_root": self.tmpdir},
        )
        # Tool call then finish
        provider = FakeBudgetProvider(responses=[
            {"thought": "write", "tool": "write_file", "arguments": {"path": "out.txt", "content": "hello"}},
            {"thought": "done", "tool": "finish", "arguments": {"summary": "done"}},
        ])
        runtime = RealAgentRuntime(context=ctx, provider=provider)
        list(runtime.run_stream("write and finish"))

        assert runtime.budget_enforcer is not None
        assert runtime.budget_enforcer.total_tool_calls >= 1

    def test_runtime_records_tokens_from_provider_usage(self):
        ctx = _make_context(
            self.tmpdir,
            execution_budget={"max_model_calls": 5},
        )
        durable_state.create(
            kind="agent_task",
            tenant_id=ctx.tenant_id,
            payload={"prompt": "test", "repo_root": self.tmpdir},
        )
        provider = FakeBudgetProvider(responses=[
            {
                "thought": "finish",
                "tool": "finish",
                "arguments": {"summary": "done"},
                "usage": {"input_tokens": 120, "output_tokens": 45},
            }
        ])
        runtime = RealAgentRuntime(context=ctx, provider=provider)
        list(runtime.run_stream("token check"))

        assert runtime.budget_enforcer is not None
        assert runtime.budget_enforcer.total_tokens_used == 165

    def test_runtime_budget_enforcer_evidence_in_done_event(self):
        ctx = _make_context(self.tmpdir)
        durable_state.create(
            kind="agent_task",
            tenant_id=ctx.tenant_id,
            payload={"prompt": "test", "repo_root": self.tmpdir},
        )
        provider = FakeBudgetProvider()
        runtime = RealAgentRuntime(context=ctx, provider=provider)
        events = list(runtime.run_stream("simple task"))
        done_events = [e for e in events if e.get("type") == "done"]
        assert len(done_events) >= 1


# ---------------------------------------------------------------------------
# Class 7: TestPipelineBudgetIntegration (5 tests)
# ---------------------------------------------------------------------------

class TestPipelineBudgetIntegration:
    """Test full pipeline integration in backend.routes.tasks."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp(prefix="aeth_p16_pipe_")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_payload(self, budget_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "prompt": "Create a simple Python utility",
            "objective": "Create a simple Python utility",
            "repo_root": self.tmpdir,
            "execution_constraints": {"allow_plain_workspace": True, "max_iterations": 5},
            "execution_budget": budget_override or {},
        }

    def test_pipeline_populates_usage_final_in_orch_evidence(self):
        from routes.tasks import run_task_pipeline

        record = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_p16_pipe",
            payload=self._make_payload({"max_model_calls": 5}),
        )
        tid = record["execution_id"]
        provider = FakeBudgetProvider()

        result = run_task_pipeline(
            task_id=tid,
            tenant_id="tenant_p16_pipe",
            provider=provider,
        )

        assert result["ok"] is True
        orch = result.get("orchestration", {})
        assert "usage_final" in orch
        final_usage = orch["usage_final"]
        assert final_usage["type"] == "budget_enforcement_final"
        assert final_usage["task_id"] == tid

    def test_pipeline_custom_budget_respected_by_runtime(self):
        from routes.tasks import run_task_pipeline

        custom_budget = {"max_model_calls": 3, "max_tool_calls": 7}
        record = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_p16_pipe",
            payload=self._make_payload(custom_budget),
        )
        tid = record["execution_id"]
        provider = FakeBudgetProvider()

        result = run_task_pipeline(
            task_id=tid,
            tenant_id="tenant_p16_pipe",
            provider=provider,
        )

        orch = result.get("orchestration", {})
        limits = orch.get("usage_final", {}).get("budget", {}).get("limits", {})
        assert limits.get("max_model_calls") == 3
        assert limits.get("max_tool_calls") == 7

    def test_pipeline_records_usage_final_replay_event(self):
        from routes.tasks import run_task_pipeline

        record = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_p16_pipe",
            payload=self._make_payload(),
        )
        tid = record["execution_id"]
        provider = FakeBudgetProvider()

        result = run_task_pipeline(
            task_id=tid,
            tenant_id="tenant_p16_pipe",
            provider=provider,
        )

        replay = result.get("replay_events", [])
        types = [e.get("event_type") for e in replay]
        assert "orchestration.usage_final" in types

    def test_pipeline_aborts_on_budget_exhaustion_without_crash(self):
        from routes.tasks import run_task_pipeline

        # max_model_calls=1, provider wants to loop
        provider = FakeBudgetProvider(responses=[
            {"thought": "step 1", "tool": "run_command", "arguments": {"command": "echo 1"}},
            {"thought": "step 2", "tool": "run_command", "arguments": {"command": "echo 2"}},
        ])

        payload = self._make_payload({"max_model_calls": 1})
        record = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_p16_pipe",
            payload=payload,
        )
        tid = record["execution_id"]

        result = run_task_pipeline(
            task_id=tid,
            tenant_id="tenant_p16_pipe",
            provider=provider,
        )

        # Pipeline returns cleanly (ok=True) but execution status is failed
        assert result["ok"] is True
        assert result["status"] == durable_state.STATUS_FAILED

    def test_pipeline_preserves_policy_selected_and_usage_final_in_replay(self):
        from routes.tasks import run_task_pipeline

        record = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_p16_pipe",
            payload=self._make_payload(),
        )
        tid = record["execution_id"]
        provider = FakeBudgetProvider()

        result = run_task_pipeline(
            task_id=tid,
            tenant_id="tenant_p16_pipe",
            provider=provider,
        )

        replay_types = [e.get("event_type") for e in result.get("replay_events", [])]
        assert "orchestration.policy_selected" in replay_types
        assert "orchestration.usage_final" in replay_types
        # policy_selected must appear before usage_final
        idx_policy = replay_types.index("orchestration.policy_selected")
        idx_usage = replay_types.index("orchestration.usage_final")
        assert idx_policy < idx_usage


# ---------------------------------------------------------------------------
# Class 8: TestAdaptiveEscalation (4 tests)
# ---------------------------------------------------------------------------

class TestAdaptiveEscalation:
    """Test EscalationChain advancement and budget-aware escalation."""

    def test_escalation_chain_on_budget_exhausted(self):
        policy = EscalationPolicy()
        chain = policy.build_chain_for_role(ModelRole.EXECUTOR)
        initial_name = chain.current_profile.name

        emitted = []
        next_prof = chain.escalate(
            reason=EscalationReason.BUDGET_EXHAUSTED,
            details="model_calls exceeded",
            replay_emit_fn=lambda ev: emitted.append(ev),
        )

        assert next_prof is not None
        assert next_prof.name != initial_name
        assert len(emitted) == 1
        assert emitted[0]["reason"] == "BUDGET_EXHAUSTED"
        assert emitted[0]["from_profile"] == initial_name
        assert emitted[0]["to_profile"] == next_prof.name

    def test_escalation_chain_exhaustion_produces_terminal_failure(self):
        policy = EscalationPolicy()
        chain = policy.build_chain_for_role(ModelRole.VERIFIER)

        # Exhaust the chain
        while not chain.is_terminal:
            chain.escalate(reason=EscalationReason.VERIFICATION_FAILURE)

        evidence = policy.build_terminal_failure_evidence(
            chain=chain,
            reason=EscalationReason.BUDGET_EXHAUSTED,
            details="All profiles exhausted",
        )

        assert evidence["type"] == "terminal_escalation_failure"
        assert evidence["reason"] == "BUDGET_EXHAUSTED"
        assert len(evidence["profiles_attempted"]) >= 1

    def test_budget_enforcer_tracks_escalation_reason_and_turns(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_escalation_attempts": 3},
            task_id="ae1",
            tenant_id="ten1",
            profile_name="standard",
            rationale="adaptive_test",
        )
        enforcer.after_escalation(EscalationReason.BUDGET_EXHAUSTED.value)
        enforcer.after_model_call(
            tokens_in=50,
            tokens_out=25,
            profile_name="strong",
            was_escalation=True,
        )

        assert enforcer.total_escalations == 1
        ev = enforcer.final_evidence()
        assert ev["usage"]["total_escalations"] == 1
        assert "BUDGET_EXHAUSTED" in ev["usage"]["escalation_reasons"]
        assert ev["usage"]["final_profile_name"] == "strong"

    def test_enforcer_check_escalation_blocks_runaway_escalations(self):
        enforcer = BudgetEnforcer.from_execution_budget(
            execution_budget={"max_escalation_attempts": 2},
            task_id="ae2",
            tenant_id="ten1",
            profile_name="standard",
            rationale="runaway_test",
        )
        enforcer.after_escalation("REASON_1")
        enforcer.after_escalation("REASON_2")
        enforcer.after_escalation("REASON_3")  # 3 > 2

        with pytest.raises(BudgetExhaustedError) as exc_info:
            enforcer.check_escalation()
        assert exc_info.value.limit_name == "max_escalation_attempts"
        assert exc_info.value.current_value == 3
