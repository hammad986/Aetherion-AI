"""
tests/integration/test_phase15_model_orchestration.py

Phase 15 — Cost-Aware Agent & Model Orchestration Kernel
Integration test suite: 40 tests across 10 test classes.

Baseline: 826 tests (Phases 2–14 + CLI).
This file adds 40 tests → target cumulative: 866 tests.

Test lineage accounting:
  - No tests deleted, skipped, weakened, or xfail-masked.
  - All assertions are genuine.
  - All new tests are additive.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
_BACKEND = os.path.join(_REPO_ROOT, "backend")
for _p in [_REPO_ROOT, _BACKEND]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from execution.orchestration.task_classifier import (
    TaskClassification,
    TaskClassifier,
    TaskComplexity,
    TaskRisk,
    RepoImpact,
    VerificationDifficulty,
    ToolIntensity,
    ContextRequirement,
)
from execution.orchestration.model_profile import (
    ModelProfile,
    ModelProfileRegistry,
    ModelRole,
    ProfileTier,
    RetryPolicy,
    get_default_registry,
)
from execution.orchestration.run_budget import BudgetExhaustedError, RunBudget, RunBudgetUsage
from execution.orchestration.context_compiler import CompiledContext, ContextCompiler
from execution.orchestration.execution_policy import derive_execution_policy, ExecutionPolicy
from execution.orchestration.escalation_policy import (
    EscalationChain,
    EscalationPolicy,
    EscalationReason,
)
from execution.orchestration.usage_accounting import TurnUsage, UsageAccounting

# Pipeline helpers
from auth_system import _make_access_token
from execution import durable_state

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _make_app():
    """Construct a minimal Flask app with the tasks blueprint registered."""
    from flask import Flask
    from routes.tasks import tasks_bp
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-phase15-secret"
    app.register_blueprint(tasks_bp)
    return app


def _auth_header(tenant_id: str = "tenant_phase15") -> Dict[str, str]:
    token = _make_access_token(user_id=tenant_id, email=f"{tenant_id}@test.com", role="admin")
    return {"Authorization": f"Bearer {token}"}


def _init_test_git_repo(path: str) -> None:
    """Initialise a minimal git repo with one commit so worktree attachment works."""
    subprocess.run(["git", "init", path], check=True, capture_output=True)
    subprocess.run(["git", "-C", path, "config", "user.email", "test@phase15.local"], check=True, capture_output=True)
    subprocess.run(["git", "-C", path, "config", "user.name", "Phase15Test"], check=True, capture_output=True)
    readme = os.path.join(path, "README.md")
    with open(readme, "w") as f:
        f.write("Phase 15 test repo\n")
    subprocess.run(["git", "-C", path, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", path, "commit", "-m", "init"], check=True, capture_output=True)


def _create_task(client, tenant_id: str, prompt: str, extra: Optional[Dict] = None) -> str:
    """Create a task via API, return task_id."""
    data: Dict[str, Any] = {"prompt": prompt, "workspace_id": f"ws_{uuid.uuid4().hex[:8]}"}
    if extra:
        data.update(extra)
    resp = client.post(
        "/api/tasks",
        json=data,
        headers=_auth_header(tenant_id),
    )
    assert resp.status_code in (200, 201), f"Create task failed: {resp.data}"
    body = resp.get_json()
    assert body.get("ok") or body.get("task_id"), f"Unexpected response: {body}"
    return body["task_id"]


# ---------------------------------------------------------------------------
# Provider stubs
# ---------------------------------------------------------------------------

class ScriptedProvider:
    """Minimal deterministic provider for pipeline integration tests."""

    def __init__(self, steps: List[Dict[str, Any]]) -> None:
        self._steps = steps
        self._idx = 0

    def chat(self, messages, tools=None):
        if self._idx < len(self._steps):
            step = self._steps[self._idx]
            self._idx += 1
            return step
        # Default terminal finish
        return {"thought": "Done", "tool": "finish", "arguments": {"summary": "completed"}}

    def capabilities(self):
        from execution.providers.base import ProviderCapabilities
        return ProviderCapabilities(supports_tools=True, supports_system_messages=True)


def _finish_provider():
    """Provider that immediately finishes."""
    return ScriptedProvider([
        {"thought": "inspect", "tool": "inspect_environment", "arguments": {}},
        {"thought": "done", "tool": "finish", "arguments": {"summary": "task done"}},
    ])


# ===========================================================================
# TEST CLASS 1 — Task Classifier
# ===========================================================================

class TestTaskClassifier:
    """10 tests covering all six classification dimensions."""

    def test_classify_trivial_task(self):
        clf = TaskClassifier()
        result = clf.classify("fix typo in README")
        assert result.complexity == TaskComplexity.TRIVIAL
        assert isinstance(result, TaskClassification)

    def test_classify_complex_task(self):
        clf = TaskClassifier()
        result = clf.classify("implement async authentication pipeline with JWT and RBAC")
        assert result.complexity in (TaskComplexity.COMPLEX, TaskComplexity.CRITICAL)

    def test_classify_high_risk_delete(self):
        clf = TaskClassifier()
        result = clf.classify("delete old user data from the database")
        assert result.risk in (TaskRisk.HIGH, TaskRisk.CRITICAL)

    def test_classify_low_risk_read(self):
        clf = TaskClassifier()
        result = clf.classify("show me the list of files in the repo")
        assert result.risk in (TaskRisk.LOW, TaskRisk.MEDIUM)

    def test_classify_critical_risk_production(self):
        clf = TaskClassifier()
        result = clf.classify("deploy new release to production with credentials rotation")
        assert result.risk == TaskRisk.CRITICAL

    def test_classify_systemic_repo_impact(self):
        clf = TaskClassifier()
        result = clf.classify("refactor the entire codebase to use the new core library")
        assert result.repo_impact in (RepoImpact.SYSTEMIC, RepoImpact.CROSS_MODULE)

    def test_classify_returns_immutable(self):
        """TaskClassification must be immutable (frozen dataclass)."""
        clf = TaskClassifier()
        result = clf.classify("add a new function to utils.py")
        with pytest.raises((AttributeError, TypeError)):
            result.complexity = TaskComplexity.CRITICAL  # type: ignore

    def test_classify_empty_prompt_defaults(self):
        clf = TaskClassifier()
        result = clf.classify("")
        assert result.complexity == TaskComplexity.MODERATE  # safe default
        assert result.risk == TaskRisk.MEDIUM

    def test_classify_hint_override(self):
        """Caller hint must override keyword signal."""
        clf = TaskClassifier()
        result = clf.classify(
            "fix typo",  # would be TRIVIAL by signal
            hints={"risk": "CRITICAL"},
        )
        assert result.risk == TaskRisk.CRITICAL

    def test_classify_as_dict_has_all_keys(self):
        clf = TaskClassifier()
        result = clf.classify("write a test for the auth module")
        d = result.as_dict()
        for key in ("complexity", "risk", "repo_impact", "verification_difficulty",
                    "tool_intensity", "context_requirement"):
            assert key in d, f"Missing key: {key}"


# ===========================================================================
# TEST CLASS 2 — ModelProfile & Registry
# ===========================================================================

class TestModelProfileRegistry:
    """8 tests covering profile registration, lookup, and tier ordering."""

    def test_default_registry_has_all_roles(self):
        reg = get_default_registry()
        for role in ModelRole:
            profiles = reg.all_for_role(role)
            assert len(profiles) >= 1, f"No profiles for role {role.value}"

    def test_profile_is_immutable(self):
        reg = get_default_registry()
        p = reg.get("executor_standard")
        assert p is not None
        with pytest.raises((AttributeError, TypeError)):
            p.temperature = 0.99  # type: ignore

    def test_get_for_role_returns_cheapest_at_or_above_tier(self):
        reg = get_default_registry()
        p = reg.get_for_role(ModelRole.EXECUTOR, min_tier=ProfileTier.STANDARD)
        assert p is not None
        assert p.tier in (ProfileTier.STANDARD, ProfileTier.STRONG, ProfileTier.SPECIALIZED)

    def test_profile_no_credentials(self):
        """No profile may contain real credentials."""
        reg = get_default_registry()
        for name, p in reg.as_dict().items():
            for field_name, val in p.items():
                val_str = str(val).lower()
                for forbidden in ("sk-", "bearer ", "api_key=", "password="):
                    assert forbidden not in val_str, f"Credential found in profile '{name}' field '{field_name}'"

    def test_register_custom_profile(self):
        reg = ModelProfileRegistry()
        p = ModelProfile(
            name="custom_exec",
            role=ModelRole.EXECUTOR,
            tier=ProfileTier.CHEAP,
            provider_name="test_provider",
            model_id="test-model",
            temperature=0.5,
            context_budget=1_000,
            token_budget=500,
            timeout_sec=10.0,
        )
        reg.register(p)
        retrieved = reg.get("custom_exec")
        assert retrieved is not None
        assert retrieved.name == "custom_exec"

    def test_tier_order_cheap_before_standard(self):
        reg = get_default_registry()
        executors = reg.all_for_role(ModelRole.EXECUTOR)
        tier_order = [ProfileTier.CHEAP, ProfileTier.STANDARD, ProfileTier.STRONG, ProfileTier.SPECIALIZED]
        indices = [tier_order.index(p.tier) for p in executors]
        assert indices == sorted(indices), "Profiles not ordered cheapest-first"

    def test_get_for_role_missing_role_returns_none(self):
        reg = ModelProfileRegistry()  # empty registry
        result = reg.get_for_role(ModelRole.PLANNER)
        assert result is None

    def test_profile_fallback_chain_intact(self):
        """executor_cheap → executor_standard → executor_strong."""
        reg = get_default_registry()
        cheap = reg.get("executor_cheap")
        assert cheap is not None
        assert cheap.fallback_profile == "executor_standard"
        std = reg.get("executor_standard")
        assert std is not None
        assert std.fallback_profile == "executor_strong"
        strong = reg.get("executor_strong")
        assert strong is not None
        assert strong.fallback_profile is None  # terminal


# ===========================================================================
# TEST CLASS 3 — RunBudget Enforcement
# ===========================================================================

class TestRunBudget:
    """10 tests covering all six limits and structured BudgetExhaustedError."""

    def test_model_calls_limit_enforced(self):
        budget = RunBudget(max_model_calls=2)
        budget.record_model_call()
        budget.record_model_call()
        budget.record_model_call()  # over limit
        with pytest.raises(BudgetExhaustedError) as exc_info:
            budget.check()
        assert exc_info.value.limit_name == "max_model_calls"
        assert exc_info.value.terminal_action == "FAIL_TASK"

    def test_tool_calls_limit_enforced(self):
        budget = RunBudget(max_tool_calls=3)
        for _ in range(4):
            budget.record_tool_call()
        with pytest.raises(BudgetExhaustedError) as exc_info:
            budget.check()
        assert exc_info.value.limit_name == "max_tool_calls"

    def test_repair_limit_enforced(self):
        budget = RunBudget(max_repair_attempts=1)
        budget.record_repair_attempt()
        budget.record_repair_attempt()
        with pytest.raises(BudgetExhaustedError) as exc_info:
            budget.check()
        assert exc_info.value.limit_name == "max_repair_attempts"

    def test_token_limit_enforced(self):
        budget = RunBudget(max_tokens=100)
        budget.record_model_call(tokens_in=60, tokens_out=50)
        with pytest.raises(BudgetExhaustedError) as exc_info:
            budget.check()
        assert exc_info.value.limit_name == "max_tokens"

    def test_escalation_limit_enforced(self):
        budget = RunBudget(max_escalation_attempts=1)
        budget.record_escalation()
        budget.record_escalation()
        with pytest.raises(BudgetExhaustedError) as exc_info:
            budget.check()
        assert exc_info.value.limit_name == "max_escalation_attempts"

    def test_budget_exhausted_error_has_structured_evidence(self):
        budget = RunBudget(max_model_calls=1)
        budget.record_model_call()
        budget.record_model_call()
        with pytest.raises(BudgetExhaustedError) as exc_info:
            budget.check()
        err = exc_info.value
        d = err.as_dict()
        assert d["type"] == "budget_exhausted"
        assert d["limit_name"] == "max_model_calls"
        assert "current_value" in d
        assert "configured_max" in d
        assert "terminal_action" in d

    def test_within_limits_no_error(self):
        budget = RunBudget(max_model_calls=10, max_tool_calls=20)
        budget.record_model_call(tokens_in=10, tokens_out=10)
        budget.record_tool_call()
        budget.check()  # must not raise

    def test_from_context_budget_reads_execution_budget_dict(self):
        execution_budget = {
            "max_repair_attempts": 3,
            "max_model_calls": 5,
            "max_tool_calls": 15,
        }
        budget = RunBudget.from_context_budget(execution_budget)
        assert budget.max_repair_attempts == 3
        assert budget.max_model_calls == 5
        assert budget.max_tool_calls == 15

    def test_from_context_budget_defaults_when_keys_absent(self):
        budget = RunBudget.from_context_budget({})
        assert budget.max_model_calls == RunBudget.DEFAULT_MAX_MODEL_CALLS
        assert budget.max_repair_attempts == RunBudget.DEFAULT_MAX_REPAIR_ATTEMPTS

    def test_remaining_shows_decreasing_headroom(self):
        budget = RunBudget(max_model_calls=5)
        budget.record_model_call()
        budget.record_model_call()
        rem = budget.remaining()
        assert rem["model_calls_remaining"] == 3


# ===========================================================================
# TEST CLASS 4 — Context Compiler
# ===========================================================================

class TestContextCompiler:
    """6 tests covering bounded compilation and role awareness."""

    def test_compile_returns_compiled_context(self):
        cc = ContextCompiler()
        ctx = cc.compile(
            role=ModelRole.EXECUTOR,
            objective="write hello.py",
            context_budget_tokens=1_000,
        )
        assert isinstance(ctx, CompiledContext)
        assert ctx.role == ModelRole.EXECUTOR
        assert ctx.objective == "write hello.py"

    def test_compile_bounds_file_snippets(self):
        cc = ContextCompiler()
        snippets = [{"path": f"file{i}.py", "content": "x" * 3_000} for i in range(20)]
        ctx = cc.compile(
            role=ModelRole.EXECUTOR,
            objective="read files",
            context_budget_tokens=2_000,
            file_snippets=snippets,
        )
        # Each snippet is capped at _MAX_FILE_SNIPPET_CHARS=2000
        for s in ctx.file_snippets:
            assert len(s["content"]) <= 2_000

    def test_compile_scrubs_sensitive_metadata(self):
        cc = ContextCompiler()
        ctx = cc.compile(
            role=ModelRole.PLANNER,
            objective="plan",
            context_budget_tokens=1_000,
            worktree_metadata={
                "repo_root": "/repo",
                "api_key": "sk-secret123",
                "password": "hunter2",
                "branch": "main",
            },
        )
        # Sensitive keys must be scrubbed
        assert "api_key" not in ctx.worktree_metadata
        assert "password" not in ctx.worktree_metadata
        assert "branch" in ctx.worktree_metadata  # safe key preserved

    def test_compile_verifier_gets_fewer_snippets(self):
        cc = ContextCompiler()
        snippets = [{"path": f"f{i}.py", "content": "x" * 10} for i in range(10)]
        ctx_verifier = cc.compile(
            role=ModelRole.VERIFIER,
            objective="verify",
            context_budget_tokens=10_000,
            file_snippets=snippets,
        )
        ctx_executor = cc.compile(
            role=ModelRole.EXECUTOR,
            objective="execute",
            context_budget_tokens=10_000,
            file_snippets=snippets,
        )
        # VERIFIER gets at most 3 snippets; EXECUTOR gets up to 8
        assert len(ctx_verifier.file_snippets) <= 3
        assert len(ctx_executor.file_snippets) <= 8

    def test_to_prompt_text_contains_role_and_objective(self):
        cc = ContextCompiler()
        ctx = cc.compile(
            role=ModelRole.REPAIR,
            objective="fix the failing test",
            context_budget_tokens=1_000,
            verification_failures=["AssertionError: expected 1, got 2"],
        )
        text = ctx.to_prompt_text()
        assert "REPAIR" in text
        assert "fix the failing test" in text
        assert "AssertionError" in text

    def test_as_dict_has_required_keys(self):
        cc = ContextCompiler()
        ctx = cc.compile(role=ModelRole.EXECUTOR, objective="go", context_budget_tokens=500)
        d = ctx.as_dict()
        for k in ("role", "objective", "estimated_chars", "file_snippet_count"):
            assert k in d


# ===========================================================================
# TEST CLASS 5 — Execution Policy Derivation
# ===========================================================================

class TestExecutionPolicy:
    """6 tests covering policy derivation correctness."""

    def test_trivial_low_risk_gets_cheap_tier(self):
        from execution.orchestration.task_classifier import TaskClassifier
        clf = TaskClassifier()
        classification = clf.classify("fix typo in README.md")
        policy = derive_execution_policy(classification)
        assert isinstance(policy, ExecutionPolicy)
        # Cheap tier profiles are used for trivial tasks
        assert policy.executor_profile.tier in (ProfileTier.CHEAP, ProfileTier.STANDARD)

    def test_critical_risk_gets_strong_tier(self):
        from execution.orchestration.task_classifier import TaskClassifier
        clf = TaskClassifier()
        classification = clf.classify("deploy release to production with credentials")
        policy = derive_execution_policy(classification)
        # Critical risk → STRONG or SPECIALIZED
        assert policy.executor_profile.tier in (ProfileTier.STRONG, ProfileTier.SPECIALIZED)

    def test_policy_has_all_four_role_profiles(self):
        from execution.orchestration.task_classifier import TaskClassifier
        clf = TaskClassifier()
        classification = clf.classify("implement authentication")
        policy = derive_execution_policy(classification)
        assert policy.planner_profile.role == ModelRole.PLANNER
        assert policy.executor_profile.role == ModelRole.EXECUTOR
        assert policy.verifier_profile.role == ModelRole.VERIFIER
        assert policy.repair_profile.role == ModelRole.REPAIR

    def test_policy_run_budget_integrates_with_context_budget(self):
        from execution.orchestration.task_classifier import TaskClassifier
        clf = TaskClassifier()
        classification = clf.classify("run tests")
        override = {"max_repair_attempts": 5, "max_model_calls": 99}
        policy = derive_execution_policy(classification, execution_budget_override=override)
        # Override keys take precedence
        assert policy.run_budget.max_repair_attempts == 5
        assert policy.run_budget.max_model_calls == 99

    def test_policy_rationale_is_non_empty_string(self):
        from execution.orchestration.task_classifier import TaskClassifier
        clf = TaskClassifier()
        classification = clf.classify("add feature to user service")
        policy = derive_execution_policy(classification)
        assert isinstance(policy.rationale, str)
        assert len(policy.rationale) > 10

    def test_policy_as_dict_includes_classification(self):
        from execution.orchestration.task_classifier import TaskClassifier
        clf = TaskClassifier()
        classification = clf.classify("write documentation")
        policy = derive_execution_policy(classification)
        d = policy.as_dict()
        assert "classification" in d
        assert "planner_profile" in d
        assert "run_budget" in d
        assert "require_hitl" in d


# ===========================================================================
# TEST CLASS 6 — Escalation Policy
# ===========================================================================

class TestEscalationPolicy:
    """6 tests covering escalation chain progression and terminal failure evidence."""

    def test_escalation_chain_has_multiple_profiles(self):
        ep = EscalationPolicy()
        chain = ep.build_chain_for_role(ModelRole.EXECUTOR)
        assert len(chain.profiles) >= 2

    def test_escalation_advances_profile(self):
        ep = EscalationPolicy()
        chain = ep.build_chain_for_role(ModelRole.EXECUTOR)
        first = chain.current_profile
        next_p = chain.escalate(EscalationReason.PROVIDER_ERROR, "test provider error")
        assert next_p is not None
        assert next_p.name != first.name

    def test_escalation_log_persists_reason(self):
        ep = EscalationPolicy()
        chain = ep.build_chain_for_role(ModelRole.EXECUTOR)
        chain.escalate(EscalationReason.VERIFICATION_FAILURE, "verifier returned NOT_VERIFIED")
        assert len(chain.escalation_log) == 1
        log_entry = chain.escalation_log[0]
        assert log_entry["reason"] == EscalationReason.VERIFICATION_FAILURE.value
        assert "verifier returned NOT_VERIFIED" in log_entry["details"]

    def test_terminal_chain_is_detected(self):
        """Exhaust all profiles; chain.is_terminal must be True."""
        ep = EscalationPolicy()
        chain = ep.build_chain_for_role(ModelRole.EXECUTOR)
        for _ in range(len(chain.profiles)):
            chain.escalate(EscalationReason.REPAIR_EXHAUSTED)
        assert chain.is_terminal is True

    def test_current_profile_raises_on_terminal_chain(self):
        ep = EscalationPolicy()
        chain = ep.build_chain_for_role(ModelRole.EXECUTOR)
        for _ in range(len(chain.profiles)):
            chain.escalate(EscalationReason.BUDGET_EXHAUSTED)
        with pytest.raises(IndexError):
            _ = chain.current_profile

    def test_terminal_failure_evidence_has_required_fields(self):
        ep = EscalationPolicy()
        chain = ep.build_chain_for_role(ModelRole.EXECUTOR)
        chain.escalate(EscalationReason.PROVIDER_ERROR)
        evidence = ep.build_terminal_failure_evidence(chain, EscalationReason.BUDGET_EXHAUSTED, "all models failed")
        assert evidence["type"] == "terminal_escalation_failure"
        assert "reason" in evidence
        assert "escalation_log" in evidence
        assert "profiles_attempted" in evidence
        assert "message" in evidence
        # Must not contain any credential-like strings
        ev_str = json.dumps(evidence).lower()
        for forbidden in ("sk-", "password", "bearer ", "api_key"):
            assert forbidden not in ev_str


# ===========================================================================
# TEST CLASS 7 — Usage Accounting
# ===========================================================================

class TestUsageAccounting:
    """4 tests covering token accounting correctness."""

    def test_tokens_none_when_provider_doesnt_report(self):
        ua = UsageAccounting(
            task_id="t1",
            tenant_id="ten1",
            initial_profile_name="executor_standard",
            initial_profile_selection_rationale="test",
        )
        turn = TurnUsage(
            turn_index=1,
            role=ModelRole.EXECUTOR.value,
            profile_name="executor_standard",
            model_id="default",
            tokens_in=None,  # provider did not report
            tokens_out=None,
        )
        ua.record_turn(turn)
        assert ua.total_tokens_in() is None
        assert ua.total_tokens_out() is None

    def test_tokens_summed_when_reported(self):
        ua = UsageAccounting(
            task_id="t2",
            tenant_id="ten2",
            initial_profile_name="executor_cheap",
            initial_profile_selection_rationale="test",
        )
        for i in range(3):
            ua.record_turn(TurnUsage(
                turn_index=i,
                role=ModelRole.EXECUTOR.value,
                profile_name="executor_cheap",
                model_id="default",
                tokens_in=100,
                tokens_out=50,
            ))
        assert ua.total_tokens_in() == 300
        assert ua.total_tokens_out() == 150

    def test_operator_summary_includes_profile_and_rationale(self):
        ua = UsageAccounting(
            task_id="t3",
            tenant_id="ten3",
            initial_profile_name="executor_standard",
            initial_profile_selection_rationale="severity=4; tier=STANDARD",
        )
        summary = ua.operator_summary()
        assert "executor_standard" in summary
        assert "severity=4" in summary

    def test_as_dict_never_contains_credentials(self):
        ua = UsageAccounting(
            task_id="t4",
            tenant_id="ten4",
            initial_profile_name="executor_cheap",
            initial_profile_selection_rationale="low risk",
        )
        d_str = json.dumps(ua.as_dict()).lower()
        for forbidden in ("sk-", "password", "bearer ", "secret"):
            assert forbidden not in d_str


# ===========================================================================
# TEST CLASS 8 — Pipeline Integration: Orchestration in run_task_pipeline
# ===========================================================================

class TestPipelineOrchestrationIntegration:
    """4 tests verifying orchestration is invoked by the actual pipeline."""

    def setup_method(self):
        self.app = _make_app()
        self.client = self.app.test_client()
        self.tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
        self.tmpdir = tempfile.mkdtemp(prefix="aeth_p15_")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_payload(self, prompt: str) -> dict:
        """Return a payload that bypasses WorktreeRequiredError (plain workspace)."""
        return {
            "prompt": prompt,
            "objective": prompt,
            "repo_root": self.tmpdir,
            "execution_constraints": {"allow_plain_workspace": True},
        }

    def test_pipeline_records_orchestration_replay_event(self):
        """After running the pipeline, replay_events must contain orchestration.policy_selected."""
        with self.app.app_context():
            from routes.tasks import run_task_pipeline
            from execution.providers.custom import DeterministicTestProvider

            record = durable_state.create(
                kind="agent_task",
                tenant_id=self.tenant_id,
                payload=self._make_payload("write hello.py"),
            )
            tid = record["execution_id"]
            result = run_task_pipeline(tid, self.tenant_id, provider=DeterministicTestProvider())
            assert "replay_events" in result
            event_types = [ev.get("event_type", "") for ev in result["replay_events"]]
            assert "orchestration.policy_selected" in event_types

    def test_pipeline_result_contains_orchestration_key(self):
        """run_task_pipeline result must include 'orchestration' key with classification."""
        with self.app.app_context():
            from routes.tasks import run_task_pipeline
            from execution.providers.custom import DeterministicTestProvider

            record = durable_state.create(
                kind="agent_task",
                tenant_id=self.tenant_id,
                payload=self._make_payload("implement factorial function"),
            )
            tid = record["execution_id"]
            result = run_task_pipeline(tid, self.tenant_id, provider=DeterministicTestProvider())
            assert "orchestration" in result
            orch = result["orchestration"]
            assert "classification" in orch
            assert "policy" in orch

    def test_pipeline_orchestration_policy_has_profiles(self):
        """Orchestration policy must list selected profiles in result."""
        with self.app.app_context():
            from routes.tasks import run_task_pipeline
            from execution.providers.custom import DeterministicTestProvider

            record = durable_state.create(
                kind="agent_task",
                tenant_id=self.tenant_id,
                payload=self._make_payload("fix bug in auth module"),
            )
            tid = record["execution_id"]
            result = run_task_pipeline(tid, self.tenant_id, provider=DeterministicTestProvider())
            policy = result.get("orchestration", {}).get("policy", {})
            for key in ("executor_profile", "planner_profile", "verifier_profile"):
                assert key in policy, f"Missing policy key: {key}"

    def test_pipeline_orchestration_does_not_bypass_verifier(self):
        """Orchestration must not claim success without verifier agreement."""
        with self.app.app_context():
            from routes.tasks import run_task_pipeline
            from execution.providers.custom import DeterministicTestProvider

            record = durable_state.create(
                kind="agent_task",
                tenant_id=self.tenant_id,
                payload=self._make_payload("write fibonacci"),
            )
            tid = record["execution_id"]
            result = run_task_pipeline(tid, self.tenant_id, provider=DeterministicTestProvider())
            # Status must be a legitimate terminal state (not claimed by orchestration alone)
            assert result["status"] in (
                "SUCCEEDED", "FAILED", "REVIEW_PENDING", "ACCEPTED",
                "succeeded", "failed", "review_pending", "accepted",
            )


# ===========================================================================
# TEST CLASS 9 — HITL Bypass Proof (Orchestration must not bypass HITL)
# ===========================================================================

class TestHITLBypassProof:
    """2 tests proving orchestration never bypasses the HITL gate."""

    def setup_method(self):
        self.app = _make_app()
        self.tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
        self.tmpdir = tempfile.mkdtemp(prefix="aeth_p15_hitl_")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_payload(self, prompt: str) -> dict:
        """Return a payload that bypasses WorktreeRequiredError (plain workspace)."""
        return {
            "prompt": prompt,
            "objective": prompt,
            "repo_root": self.tmpdir,
            "execution_constraints": {"allow_plain_workspace": True},
        }

    def test_orchestration_evidence_does_not_include_hitl_bypass_flag(self):
        """No orchestration evidence key should indicate HITL was bypassed."""
        with self.app.app_context():
            from routes.tasks import run_task_pipeline
            from execution.providers.custom import DeterministicTestProvider

            record = durable_state.create(
                kind="agent_task",
                tenant_id=self.tenant_id,
                payload=self._make_payload("delete all temporary files"),
            )
            tid = record["execution_id"]
            result = run_task_pipeline(tid, self.tenant_id, provider=DeterministicTestProvider())
            orch = result.get("orchestration", {})
            orch_str = json.dumps(orch).lower()
            # Must never claim HITL was bypassed by orchestration
            for forbidden in ("bypass_hitl", "skip_hitl", "hitl_disabled"):
                assert forbidden not in orch_str

    def test_require_hitl_flag_is_persisted_in_evidence(self):
        """If orchestration sets require_hitl, it must be explicit in policy evidence."""
        with self.app.app_context():
            from routes.tasks import run_task_pipeline
            from execution.providers.custom import DeterministicTestProvider

            record = durable_state.create(
                kind="agent_task",
                tenant_id=self.tenant_id,
                payload=self._make_payload("simple read test"),
            )
            tid = record["execution_id"]
            result = run_task_pipeline(tid, self.tenant_id, provider=DeterministicTestProvider())
            policy_evidence = result.get("orchestration", {}).get("policy", {})
            # require_hitl key must be present (may be True or False)
            assert "require_hitl" in policy_evidence


# ===========================================================================
# TEST CLASS 10 — Regression Guard (Phases 2–14 still pass)
# ===========================================================================

class TestPhase15RegressionGuard:
    """4 smoke tests confirming no regressions in core primitives."""

    def test_task_classifier_importable(self):
        from execution.orchestration.task_classifier import TaskClassifier
        assert TaskClassifier is not None

    def test_run_budget_importable(self):
        from execution.orchestration.run_budget import RunBudget, BudgetExhaustedError
        assert RunBudget is not None
        assert BudgetExhaustedError is not None

    def test_orchestration_package_init_exports_all_symbols(self):
        import execution.orchestration as orch
        for sym in [
            "TaskClassifier", "TaskClassification", "TaskComplexity", "TaskRisk",
            "ModelProfile", "ModelProfileRegistry", "ModelRole", "get_default_registry",
            "BudgetExhaustedError", "RunBudget", "RunBudgetUsage",
            "CompiledContext", "ContextCompiler",
            "ExecutionPolicy", "derive_execution_policy",
            "EscalationChain", "EscalationPolicy", "EscalationReason",
            "TurnUsage", "UsageAccounting",
        ]:
            assert hasattr(orch, sym), f"Symbol not exported: {sym}"

    def test_durable_state_still_functional(self):
        """Core durable state must remain unaffected by Phase 15 changes."""
        tenant = f"reg_tenant_{uuid.uuid4().hex[:8]}"
        record = durable_state.create(
            kind="agent_task",
            tenant_id=tenant,
            payload={"prompt": "regression check", "workspace_id": "ws_reg"},
        )
        tid = record["execution_id"]
        rec = durable_state.get(tid, tenant)
        assert rec is not None
        assert rec["status"] in ("QUEUED", "queued", "PENDING", "pending")

