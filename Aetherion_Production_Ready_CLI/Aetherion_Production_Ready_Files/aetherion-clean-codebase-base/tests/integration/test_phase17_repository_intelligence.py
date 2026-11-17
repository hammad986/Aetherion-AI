"""
tests/integration/test_phase17_repository_intelligence.py — Phase 17 Tests.

Comprehensive test suite covering Repository Intelligence & Adaptive Context Kernel:
  Class 1: TestRepositoryMap (6 tests)
  Class 2: TestTaskRelevanceEngine (6 tests)
  Class 3: TestContextCompilerBudgets (5 tests)
  Class 4: TestRoleSpecificContext (5 tests)
  Class 5: TestAdaptiveRetrieval (5 tests)
  Class 6: TestPipelineAndRuntimeIntegration (5 tests)
  Class 7: TestSecurityAndBoundaryIsolation (5 tests)
  Class 8: TestRepositoryContextBenchmarks (7 tests)

Total: 44 tests.
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
    AdaptiveRetrievalResult,
    AdaptiveRetriever,
    CompiledContext,
    ContextCompiler,
    FileNode,
    ModelRole,
    RelevanceItem,
    RelevanceReason,
    RepositoryIntelligence,
    RepositoryMap,
    SymbolKind,
    SymbolNode,
    TaskRelevanceEngine,
)
from execution.providers.base import BaseModelProvider


# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------

class FakeIntelligenceProvider(BaseModelProvider):
    """Deterministic fake provider for Phase 17 pipeline testing."""

    def __init__(self, responses: Optional[List[Dict[str, Any]]] = None) -> None:
        self.responses = list(responses or [])
        self.call_count = 0

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        self.call_count += 1
        if self.responses:
            idx = min(self.call_count - 1, len(self.responses) - 1)
            return dict(self.responses[idx])
        return {
            "thought": "Phase 17 test completed",
            "tool": "finish",
            "arguments": {"summary": "Execution complete"},
            "usage": {"input_tokens": 15, "output_tokens": 10},
        }

    def complete(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        return "fake completion"


def _create_sample_repo(root: Path) -> None:
    """Create a structured sample repository for intelligence and benchmark tests."""
    # Module A: core engine
    core_dir = root / "src" / "core"
    core_dir.mkdir(parents=True, exist_ok=True)
    (core_dir / "__init__.py").write_text("", encoding="utf-8")
    (core_dir / "engine.py").write_text(
        '''"""Core workflow engine module."""
from src.core.utils import format_result

class WorkflowEngine:
    """Coordinates core workflows."""
    def __init__(self, name: str = "default"):
        self.name = name

    def process(self, data: str) -> str:
        return format_result(data)

def start_engine() -> WorkflowEngine:
    return WorkflowEngine()
''',
        encoding="utf-8",
    )

    # Module B: utils
    (core_dir / "utils.py").write_text(
        '''"""Helper utilities."""
def format_result(val: str) -> str:
    """Format output value."""
    return f"RESULT: {val.strip()}"

def sanitize(input_str: str) -> str:
    return input_str.strip()
''',
        encoding="utf-8",
    )

    # Module C: config
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.json").write_text(
        '{"timeout": 30, "env": "testing", "retries": 3}', encoding="utf-8"
    )

    # Module D: tests
    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_engine.py").write_text(
        '''"""Tests for workflow engine."""
import pytest
from src.core.engine import WorkflowEngine, start_engine

def test_engine_process():
    eng = start_engine()
    assert eng.process("abc") == "RESULT: abc"
''',
        encoding="utf-8",
    )
    (tests_dir / "test_utils.py").write_text(
        '''"""Tests for utilities."""
from src.core.utils import format_result, sanitize

def test_sanitize():
    assert sanitize("  x  ") == "x"
''',
        encoding="utf-8",
    )

    # Module E: unrelated module
    misc_dir = root / "docs"
    misc_dir.mkdir(parents=True, exist_ok=True)
    (misc_dir / "readme.md").write_text("# Project Documentation\nEverything here.", encoding="utf-8")


# ---------------------------------------------------------------------------
# Class 1: TestRepositoryMap (6 tests)
# ---------------------------------------------------------------------------

class TestRepositoryMap:
    """Test repository file and symbol discovery, AST parsing, and incremental updates."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="aeth_p17_map_"))
        _create_sample_repo(self.tmpdir)
        self.repo_map = RepositoryMap(self.tmpdir)
        self.repo_map.build_map()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_file_discovery_and_metadata(self):
        summary = self.repo_map.as_dict()
        assert summary["file_count"] >= 5
        assert summary["python_files"] >= 4
        assert summary["test_files"] >= 2
        assert summary["config_files"] >= 1
        assert "src/core/engine.py" in self.repo_map.files
        fnode = self.repo_map.files["src/core/engine.py"]
        assert fnode.language == "python"
        assert fnode.size_bytes > 0
        assert fnode.is_test is False

    def test_symbol_discovery_classes_and_functions(self):
        symbols = self.repo_map.find_symbol("WorkflowEngine")
        assert len(symbols) >= 1
        sym = symbols[0]
        assert sym.name == "WorkflowEngine"
        assert sym.kind == SymbolKind.CLASS
        assert sym.file_path == "src/core/engine.py"
        assert "WorkflowEngine" in sym.signature

        # Function check
        start_fn = self.repo_map.find_symbol("start_engine")
        assert len(start_fn) >= 1
        assert start_fn[0].kind == SymbolKind.FUNCTION
        assert start_fn[0].docstring is None or isinstance(start_fn[0].docstring, str)

    def test_method_discovery_and_qualified_names(self):
        methods = self.repo_map.find_symbol("process")
        assert len(methods) >= 1
        m = methods[0]
        assert m.kind == SymbolKind.METHOD
        assert m.qualified_name == "WorkflowEngine.process"
        assert m.file_path == "src/core/engine.py"

    def test_import_and_dependency_resolution(self):
        deps = self.repo_map.get_dependencies("src/core/engine.py")
        assert any("utils.py" in d for d in deps)

        dependents = self.repo_map.get_dependents("src/core/utils.py")
        assert any("engine.py" in d for d in dependents)

    def test_related_test_discovery(self):
        tests = self.repo_map.get_related_tests("src/core/engine.py")
        assert len(tests) >= 1
        assert any("test_engine.py" in t for t in tests)

    def test_incremental_update_on_file_change(self):
        engine_file = self.tmpdir / "src" / "core" / "engine.py"
        engine_file.write_text(
            '''"""Updated engine module."""
class NewWorkflowCoordinator:
    def execute_all(self):
        pass
''',
            encoding="utf-8",
        )
        self.repo_map.update_file("src/core/engine.py")

        # Old symbols removed
        assert len(self.repo_map.find_symbol("WorkflowEngine")) == 0
        assert len(self.repo_map.find_symbol("start_engine")) == 0

        # New symbols present
        new_syms = self.repo_map.find_symbol("NewWorkflowCoordinator")
        assert len(new_syms) == 1
        assert new_syms[0].file_path == "src/core/engine.py"


# ---------------------------------------------------------------------------
# Class 2: TestTaskRelevanceEngine (6 tests)
# ---------------------------------------------------------------------------

class TestTaskRelevanceEngine:
    """Test explainable task relevance ranking with verifiable reasons."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="aeth_p17_rel_"))
        _create_sample_repo(self.tmpdir)
        self.intel = RepositoryIntelligence(self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_direct_symbol_match_relevance(self):
        items = self.intel.get_task_context("Fix bug in WorkflowEngine process logic")
        assert len(items) >= 1
        top = items[0]
        assert "engine.py" in top.path
        assert top.reason in (RelevanceReason.SYMBOL_MATCH, RelevanceReason.DIRECT_KEYWORD_MATCH)
        assert "WorkflowEngine" in top.details or "engine" in top.details

    def test_user_specified_file_highest_priority(self):
        items = self.intel.get_task_context(
            "General maintenance task",
            user_files=["src/core/utils.py"],
        )
        assert len(items) >= 1
        assert items[0].path == "src/core/utils.py"
        assert items[0].reason == RelevanceReason.USER_SPECIFIED_FILE
        assert items[0].score == 1.0

    def test_related_test_selection(self):
        items = self.intel.get_task_context("Refactor engine component")
        test_items = [i for i in items if i.reason == RelevanceReason.RELATED_TEST]
        assert len(test_items) >= 1
        assert any("test_engine.py" in t.path for t in test_items)

    def test_configuration_file_relevance(self):
        items = self.intel.get_task_context("Change timeout settings in configuration")
        config_items = [i for i in items if i.reason == RelevanceReason.CONFIGURATION]
        assert len(config_items) >= 1
        assert any("settings.json" in c.path for c in config_items)

    def test_dependency_chain_relevance(self):
        items = self.intel.get_task_context("Update WorkflowEngine processing pipeline")
        paths = [i.path for i in items]
        # engine.py should pull in utils.py via call dependency
        assert any("engine.py" in p for p in paths)
        assert any("utils.py" in p for p in paths)

    def test_unrelated_files_rejected(self):
        items = self.intel.get_task_context("Fix error in format_result utility function")
        paths = [i.path for i in items]
        assert not any("readme.md" in p for p in paths)


# ---------------------------------------------------------------------------
# Class 3: TestContextCompilerBudgets (5 tests)
# ---------------------------------------------------------------------------

class TestContextCompilerBudgets:
    """Test context budget enforcement, pruning, and compression."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="aeth_p17_cmp_"))
        _create_sample_repo(self.tmpdir)
        self.intel = RepositoryIntelligence(self.tmpdir)
        self.compiler = ContextCompiler()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_normal_budget_compilation_succeeds(self):
        ctx = self.compiler.compile(
            role=ModelRole.PLANNER,
            objective="Inspect WorkflowEngine",
            context_budget_tokens=4000,
            repo_intelligence=self.intel,
        )
        assert ctx.role == ModelRole.PLANNER
        assert len(ctx.selected_files) >= 1
        assert ctx.estimated_chars > 0
        assert ctx.pruned_items_count == 0
        assert ctx.compression_applied is False

    def test_tight_budget_triggers_pruning(self):
        # Very tight budget: 100 tokens = 400 chars
        ctx = self.compiler.compile(
            role=ModelRole.EXECUTOR,
            objective="Update engine",
            context_budget_tokens=100,
            repo_intelligence=self.intel,
            file_snippets=[
                {"path": f"file_{i}.py", "content": "x" * 200} for i in range(10)
            ],
        )
        assert ctx.pruned_items_count > 0
        # Total chars are kept small
        assert len(ctx.file_snippets) <= 2

    def test_compression_applied_on_oversized_content(self):
        # Snippets with lots of blank lines
        fat_snippet = "line 1\n\n\n\n\nline 2\n\n\n\n\nline 3" * 50
        ctx = self.compiler.compile(
            role=ModelRole.EXECUTOR,
            objective="Test compression",
            context_budget_tokens=300,
            file_snippets=[{"path": "fat.py", "content": fat_snippet}],
        )
        assert ctx.compression_applied is True

    def test_deterministic_output_for_same_inputs(self):
        ctx1 = self.compiler.compile(
            role=ModelRole.PLANNER,
            objective="Consistent test",
            context_budget_tokens=2000,
            repo_intelligence=self.intel,
        )
        ctx2 = self.compiler.compile(
            role=ModelRole.PLANNER,
            objective="Consistent test",
            context_budget_tokens=2000,
            repo_intelligence=self.intel,
        )
        assert ctx1.to_prompt_text() == ctx2.to_prompt_text()
        assert ctx1.estimated_chars == ctx2.estimated_chars

    def test_sensitive_metadata_scrubbed_from_context(self):
        ctx = self.compiler.compile(
            role=ModelRole.EXECUTOR,
            objective="Check secrets",
            context_budget_tokens=2000,
            worktree_metadata={
                "repo_root": "/safe/repo",
                "api_key": "SECRET_KEY_123",
                "auth_token": "BEARER_456",
            },
        )
        prompt = ctx.to_prompt_text()
        assert "SECRET_KEY_123" not in prompt
        assert "BEARER_456" not in prompt
        assert "api_key" not in ctx.worktree_metadata


# ---------------------------------------------------------------------------
# Class 4: TestRoleSpecificContext (5 tests)
# ---------------------------------------------------------------------------

class TestRoleSpecificContext:
    """Test role-specific context compilation for Planner, Executor, Verifier, and Repair."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="aeth_p17_role_"))
        _create_sample_repo(self.tmpdir)
        self.intel = RepositoryIntelligence(self.tmpdir)
        self.compiler = ContextCompiler()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_planner_context_includes_summary_and_candidates(self):
        ctx = self.compiler.compile(
            role=ModelRole.PLANNER,
            objective="Plan engine improvements",
            context_budget_tokens=4000,
            repo_intelligence=self.intel,
        )
        text = ctx.to_prompt_text()
        assert "[ROLE: planner]" in text
        assert "[REPOSITORY SUMMARY]" in text
        assert "[RELEVANT REPOSITORY ITEMS]" in text

    def test_executor_context_includes_target_files_and_content(self):
        ctx = self.compiler.compile(
            role=ModelRole.EXECUTOR,
            objective="Fix format_result utility function",
            context_budget_tokens=4000,
            repo_intelligence=self.intel,
        )
        text = ctx.to_prompt_text()
        assert "[ROLE: executor]" in text
        assert "[RELEVANT FILES]" in text
        assert any("utils.py" in s.get("path", "") for s in ctx.file_snippets)

    def test_verifier_context_includes_criteria_and_tests(self):
        ctx = self.compiler.compile(
            role=ModelRole.VERIFIER,
            objective="Verify changes to WorkflowEngine",
            context_budget_tokens=4000,
            repo_intelligence=self.intel,
            verification_criteria={"pytest_command": "pytest tests/test_engine.py"},
        )
        text = ctx.to_prompt_text()
        assert "[ROLE: verifier]" in text
        assert "[VERIFICATION CRITERIA]" in text
        assert "pytest tests/test_engine.py" in text

    def test_repair_context_includes_failure_diagnostics(self):
        ctx = self.compiler.compile(
            role=ModelRole.REPAIR,
            objective="Repair failing test",
            context_budget_tokens=4000,
            verification_failures=["AssertionError: assert 'bad' == 'RESULT: abc'"],
            repair_history=[{"attempt": 1, "status": "failed", "reason": "Test assertion mismatch"}],
        )
        text = ctx.to_prompt_text()
        assert "[ROLE: repair]" in text
        assert "[VERIFICATION FAILURES]" in text
        assert "AssertionError" in text
        assert "[REPAIR HISTORY]" in text

    def test_verifier_gets_fewer_snippets_than_executor(self):
        snippets = [{"path": f"f_{i}.py", "content": f"content {i}"} for i in range(10)]
        v_ctx = self.compiler.compile(
            role=ModelRole.VERIFIER,
            objective="verify",
            context_budget_tokens=4000,
            file_snippets=snippets,
        )
        e_ctx = self.compiler.compile(
            role=ModelRole.EXECUTOR,
            objective="execute",
            context_budget_tokens=4000,
            file_snippets=snippets,
        )
        assert len(v_ctx.file_snippets) <= 3
        assert len(e_ctx.file_snippets) > len(v_ctx.file_snippets)


# ---------------------------------------------------------------------------
# Class 5: TestAdaptiveRetrieval (5 tests)
# ---------------------------------------------------------------------------

class TestAdaptiveRetrieval:
    """Test dynamic on-demand retrieval reacting to runtime errors."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="aeth_p17_adapt_"))
        _create_sample_repo(self.tmpdir)
        self.intel = RepositoryIntelligence(self.tmpdir)
        self.retriever = AdaptiveRetriever(self.intel, max_retrievals=3)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_missing_symbol_trigger_retrieves_definition(self):
        error_text = "AttributeError: 'WorkflowEngine' object has no attribute 'format_result'"
        res = self.retriever.retrieve_on_evidence(error_text)
        assert res is not None
        assert res.success is True
        assert res.target_identifier == "format_result"
        assert "utils.py" in res.file_path
        assert res.content_snippet is not None
        assert "def format_result" in res.content_snippet

    def test_missing_module_trigger_retrieves_file(self):
        error_text = "ModuleNotFoundError: No module named 'utils'"
        res = self.retriever.retrieve_on_evidence(error_text)
        assert res is not None
        assert res.success is True
        assert "utils.py" in res.file_path

    def test_retrieval_ceiling_enforced_strictly(self):
        # Max retrievals is 3
        self.retriever.retrieve_on_evidence("NameError: name 'format_result' is not defined")
        self.retriever.retrieve_on_evidence("ModuleNotFoundError: No module named 'engine'")
        self.retriever.retrieve_on_evidence("NameError: name 'sanitize' is not defined")

        assert self.retriever.is_ceiling_reached is True
        assert self.retriever.remaining_retrievals == 0

        # 4th retrieval is rejected
        fourth = self.retriever.retrieve_on_evidence("NameError: name 'WorkflowEngine' is not defined")
        assert fourth is None
        assert len(self.retriever.retrieval_history) == 3

    def test_deduplication_prevents_re_retrieving_same_file(self):
        res1 = self.retriever.retrieve_on_evidence("NameError: name 'format_result' is not defined")
        assert res1 is not None and res1.success is True

        # Second trigger for same symbol/file
        res2 = self.retriever.retrieve_on_evidence("NameError: name 'format_result' is not defined")
        # Already retrieved; returns None or handles deduplication cleanly
        assert res2 is None or res2.success is False

    def test_as_dict_exposes_structured_audit_trail(self):
        self.retriever.retrieve_on_evidence("ModuleNotFoundError: No module named 'utils'")
        summary = self.retriever.as_dict()
        assert summary["max_retrievals"] == 3
        assert summary["retrieval_count"] == 1
        assert summary["remaining_retrievals"] == 2
        assert len(summary["history"]) == 1
        assert summary["history"][0]["trigger"] == "missing_module"


# ---------------------------------------------------------------------------
# Class 6: TestPipelineAndRuntimeIntegration (5 tests)
# ---------------------------------------------------------------------------

class TestPipelineAndRuntimeIntegration:
    """Test full integration with backend.routes.tasks and RealAgentRuntime."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="aeth_p17_pipe_"))
        _create_sample_repo(self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_payload(self) -> Dict[str, Any]:
        return {
            "prompt": "Refactor WorkflowEngine to use updated utils",
            "objective": "Refactor WorkflowEngine to use updated utils",
            "repo_root": str(self.tmpdir),
            "execution_constraints": {"allow_plain_workspace": True, "max_iterations": 5},
            "execution_budget": {"max_model_calls": 5},
        }

    def test_pipeline_populates_repo_intelligence_in_orch_evidence(self):
        from routes.tasks import run_task_pipeline

        record = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_p17",
            payload=self._make_payload(),
        )
        tid = record["execution_id"]
        provider = FakeIntelligenceProvider()

        result = run_task_pipeline(task_id=tid, tenant_id="tenant_p17", provider=provider)
        assert result["ok"] is True
        orch = result.get("orchestration", {})
        assert "repo_intelligence" in orch
        assert orch["repo_intelligence"]["file_count"] >= 4
        assert "task_relevance" in orch
        assert len(orch["task_relevance"]) >= 1

    def test_pipeline_records_context_compiled_replay_event(self):
        from routes.tasks import run_task_pipeline

        record = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_p17",
            payload=self._make_payload(),
        )
        tid = record["execution_id"]
        provider = FakeIntelligenceProvider()

        result = run_task_pipeline(task_id=tid, tenant_id="tenant_p17", provider=provider)
        replay = result.get("replay_events", [])
        types = [e.get("event_type") for e in replay]
        assert "context.compiled" in types
        comp_event = [e for e in replay if e.get("event_type") == "context.compiled"][0]
        assert "selected_files" in comp_event["payload"]

    def test_runtime_instantiates_adaptive_retriever(self):
        ctx = TaskExecutionContext(
            task_id="p17_rt_1",
            objective="test",
            workspace_dir=str(self.tmpdir),
            tenant_id="tenant_p17",
            session_id="sess_1",
            repo_root=str(self.tmpdir),
            execution_constraints={"allow_plain_workspace": True},
        )
        durable_state.create(kind="agent_task", tenant_id="tenant_p17", payload={"repo_root": str(self.tmpdir)})
        runtime = RealAgentRuntime(context=ctx, provider=FakeIntelligenceProvider())
        assert runtime.repo_intelligence is not None
        assert runtime.adaptive_retriever is not None

    def test_runtime_triggers_adaptive_retrieval_on_tool_error(self):
        ctx = TaskExecutionContext(
            task_id="p17_rt_2",
            objective="test adaptive retrieval",
            workspace_dir=str(self.tmpdir),
            tenant_id="tenant_p17",
            session_id="sess_2",
            repo_root=str(self.tmpdir),
            execution_constraints={"allow_plain_workspace": True},
        )
        durable_state.create(kind="agent_task", tenant_id="tenant_p17", payload={"repo_root": str(self.tmpdir)})

        # Provider triggers a failing command referencing missing symbol
        provider = FakeIntelligenceProvider(responses=[
            {
                "thought": "run failing script",
                "tool": "run_command",
                "arguments": {"command": "python -c \"import sys; sys.stderr.write('ModuleNotFoundError: No module named \\'utils\\'\\n'); sys.exit(1)\""},
            },
            {
                "thought": "finish",
                "tool": "finish",
                "arguments": {"summary": "done"},
            },
        ])
        runtime = RealAgentRuntime(context=ctx, provider=provider)
        events = list(runtime.run_stream("run command"))

        # Replay event recorded
        adapt_events = [e for e in runtime.replay_events if e.get("event_type") == "context.adaptive_retrieval"]
        assert len(adapt_events) >= 1
        assert "utils.py" in adapt_events[0]["payload"]["file_path"]

    def test_runtime_records_adaptive_retrievals_in_durable_result(self):
        ctx = TaskExecutionContext(
            task_id="p17_rt_3",
            objective="test durable result",
            workspace_dir=str(self.tmpdir),
            tenant_id="tenant_p17",
            session_id="sess_3",
            repo_root=str(self.tmpdir),
            execution_constraints={"allow_plain_workspace": True},
        )
        durable_state.create(kind="agent_task", tenant_id="tenant_p17", payload={"repo_root": str(self.tmpdir)})
        provider = FakeIntelligenceProvider()
        runtime = RealAgentRuntime(context=ctx, provider=provider)
        list(runtime.run_stream("simple task"))

        # Verify durable state contains adaptive_retrievals
        rec = durable_state.get(ctx.task_id, ctx.tenant_id)
        assert rec is not None
        res_json = json.loads(rec.get("result_json") or "{}")
        assert "adaptive_retrievals" in res_json


# ---------------------------------------------------------------------------
# Class 7: TestSecurityAndBoundaryIsolation (5 tests)
# ---------------------------------------------------------------------------

class TestSecurityAndBoundaryIsolation:
    """Test workspace boundary confinement, tenant isolation, and secret scrubbing."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="aeth_p17_sec_"))
        _create_sample_repo(self.tmpdir)
        self.intel = RepositoryIntelligence(self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_directory_traversal_read_fails_closed(self):
        # Attempt to read outside repo root
        out = self.intel.read_file_safe("../../etc/passwd")
        assert out is None

        out_win = self.intel.read_file_safe("..\\..\\windows\\system.ini")
        assert out_win is None

    def test_absolute_path_outside_repo_fails_closed(self):
        out = self.intel.read_file_safe(str(Path.home()))
        assert out is None

    def test_no_secrets_in_repo_intelligence_dict(self):
        d = self.intel.as_dict()
        s = json.dumps(d)
        assert "api_key" not in s
        assert "secret" not in s.lower()
        assert "token" not in s.lower()

    def test_context_compiler_scrubs_bearer_and_passwords(self):
        compiler = ContextCompiler()
        ctx = compiler.compile(
            role=ModelRole.EXECUTOR,
            objective="Task with secrets",
            context_budget_tokens=2000,
            worktree_metadata={"bearer_token": "ey12345", "db_password": "supersecretpassword"},
            verification_criteria={"secret_token": "tok_xyz"},
        )
        text = ctx.to_prompt_text()
        assert "ey12345" not in text
        assert "supersecretpassword" not in text
        assert "tok_xyz" not in text

    def test_tenant_isolation_in_durable_state_evidence(self):
        from routes.tasks import run_task_pipeline

        record = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_alpha",
            payload={"prompt": "alpha task", "repo_root": str(self.tmpdir), "execution_constraints": {"allow_plain_workspace": True}},
        )
        tid = record["execution_id"]
        run_task_pipeline(task_id=tid, tenant_id="tenant_alpha", provider=FakeIntelligenceProvider())

        # Attempt to access from tenant_beta
        record_beta = durable_state.get(tid, "tenant_beta")
        assert record_beta is None


# ---------------------------------------------------------------------------
# Class 8: TestRepositoryContextBenchmarks (7 tests)
# ---------------------------------------------------------------------------

class TestRepositoryContextBenchmarks:
    """
    Focused benchmark measuring relevance accuracy, latency, and context budget across 7 task archetypes.
    """

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="aeth_p17_bench_"))
        _create_sample_repo(self.tmpdir)
        self.intel = RepositoryIntelligence(self.tmpdir)
        self.compiler = ContextCompiler()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_benchmark_1_single_file_change(self):
        t0 = time.perf_counter()
        items = self.intel.get_task_context("Fix typo in sanitize function")
        latency_ms = (time.perf_counter() - t0) * 1000

        assert latency_ms < 100.0  # Fast retrieval SLA
        assert len(items) >= 1
        assert "utils.py" in items[0].path
        assert not any("engine.py" in i.path and i.score > 0.8 for i in items)

    def test_benchmark_2_cross_file_change(self):
        items = self.intel.get_task_context("Update WorkflowEngine and format_result communication")
        paths = [i.path for i in items]
        assert any("engine.py" in p for p in paths)
        assert any("utils.py" in p for p in paths)

    def test_benchmark_3_symbol_rename_and_reference_lookup(self):
        refs = self.intel.find_references("format_result")
        assert len(refs) >= 1
        # engine.py calls format_result
        assert any("engine.py" in r["file_path"] for r in refs)

    def test_benchmark_4_bug_fix_with_failing_test(self):
        items = self.intel.get_task_context(
            "Fix AssertionError in test_engine_process",
            recent_evidence={"files": ["src/core/engine.py"]},
        )
        paths = [i.path for i in items]
        assert any("engine.py" in p for p in paths)
        assert any("test_engine.py" in p for p in paths)

    def test_benchmark_5_configuration_driven_change(self):
        items = self.intel.get_task_context("Increase retry limit in configuration settings")
        config_items = [i for i in items if i.reason == RelevanceReason.CONFIGURATION]
        assert len(config_items) >= 1
        assert any("settings.json" in c.path for c in config_items)

    def test_benchmark_6_dependency_call_chain_change(self):
        affected = self.intel.get_affected_areas(["src/core/utils.py"])
        # engine.py depends on utils.py
        assert any("engine.py" in d for d in affected["affected_dependents"])
        # test_utils.py tests utils.py
        assert any("test_utils.py" in t for t in affected["related_tests"])

    def test_benchmark_7_repair_after_verifier_failure(self):
        ctx = self.compiler.compile(
            role=ModelRole.REPAIR,
            objective="Repair after verifier failure",
            context_budget_tokens=2000,
            verification_failures=["FAILED tests/test_engine.py::test_engine_process - AssertionError"],
            repair_history=[{"attempt": 1, "status": "failed", "reason": "pytest exited code 1"}],
            repo_intelligence=self.intel,
        )
        text = ctx.to_prompt_text()
        assert "[ROLE: repair]" in text
        assert "AssertionError" in text
        assert "tests/test_engine.py" in text
        # Bounded context check
        assert len(text) < 8000
