"""
tests/integration/test_phase18_skills_memory.py — Phase 18 Tests.

Comprehensive integration test suite covering Governed Skills & Persistent Engineering Memory Kernel:
  Class 1: TestSkillRegistryAndLifecycle (6 tests)
  Class 2: TestSkillDiscoveryAndPermissions (6 tests)
  Class 3: TestEngineeringMemoryContract (6 tests)
  Class 4: TestMemoryScopingAndTenantIsolation (6 tests)
  Class 5: TestMemoryRankingAndConflictHandling (6 tests)
  Class 6: TestContextCompilerSkillsMemoryIntegration (5 tests)
  Class 7: TestPipelineAndRuntimeLearningLoop (5 tests)
  Class 8: TestSecurityAndGovernanceInvariants (5 tests)

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
    CompiledContext,
    ContextCompiler,
    EngineeringMemoryConflict,
    EngineeringMemoryKernel,
    EngineeringMemoryRecord,
    GovernedSkill,
    MemoryScope,
    ModelRole,
    RepositoryIntelligence,
    SkillCandidate,
    SkillDiscoveryEngine,
    SkillLifecycleStatus,
    SkillPermissionAuditor,
    SkillSecurityPolicy,
    SkillSelectionReason,
    VerificationResultSummary,
    get_skill_registry,
    reset_skill_registry,
)
from execution.providers.base import BaseModelProvider


# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------

class FakePipelineProvider(BaseModelProvider):
    """Deterministic fake provider for Phase 18 testing."""

    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed
        self.call_count = 0

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        self.call_count += 1
        if self.succeed:
            if self.call_count == 1:
                return {
                    "thought": "Writing updated service",
                    "tool": "write_file",
                    "arguments": {"path": "service.py", "content": "def user_service(): pass\n"},
                    "usage": {"input_tokens": 20, "output_tokens": 15},
                }
            return {
                "thought": "Phase 18 task execution completed successfully.",
                "tool": "finish",
                "arguments": {"summary": "Completed governed skill task"},
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
        return "Fake completion for Phase 18"


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure clean global skill registry per test."""
    reset_skill_registry()
    yield
    reset_skill_registry()


# ===========================================================================
# Class 1: TestSkillRegistryAndLifecycle (6 tests)
# ===========================================================================

class TestSkillRegistryAndLifecycle:

    def test_skill_registration_valid(self):
        registry = get_skill_registry()
        skill = GovernedSkill(
            skill_id="test_custom_skill",
            name="Custom Skill",
            version="1.0.0",
            description="A test custom skill",
            allowed_tools=["read_file", "write_file"],
            activation_triggers=["custom", "test"],
            author="tester",
        )
        registry.register_skill(skill)
        retrieved = registry.get_skill("test_custom_skill")
        assert retrieved is not None
        assert retrieved.name == "Custom Skill"
        assert retrieved.version == "1.0.0"
        assert retrieved.lifecycle_status == SkillLifecycleStatus.DRAFT

    def test_lifecycle_state_transitions(self):
        registry = get_skill_registry()
        skill = GovernedSkill(
            skill_id="test_lifecycle_skill",
            name="Lifecycle Skill",
            version="1.0.0",
            description="Testing states",
        )
        registry.register_skill(skill)
        assert skill.lifecycle_status == SkillLifecycleStatus.DRAFT

        # DRAFT -> VALIDATED
        registry.transition_lifecycle("test_lifecycle_skill", SkillLifecycleStatus.VALIDATED)
        assert skill.lifecycle_status == SkillLifecycleStatus.VALIDATED

        # VALIDATED -> ACTIVE
        registry.transition_lifecycle("test_lifecycle_skill", SkillLifecycleStatus.ACTIVE)
        assert skill.lifecycle_status == SkillLifecycleStatus.ACTIVE

        # ACTIVE -> DEPRECATED
        registry.transition_lifecycle("test_lifecycle_skill", SkillLifecycleStatus.DEPRECATED)
        assert skill.lifecycle_status == SkillLifecycleStatus.DEPRECATED

        # DEPRECATED -> DISABLED
        registry.transition_lifecycle("test_lifecycle_skill", SkillLifecycleStatus.DISABLED)
        assert skill.lifecycle_status == SkillLifecycleStatus.DISABLED

    def test_invalid_lifecycle_transition_raises(self):
        registry = get_skill_registry()
        skill = GovernedSkill(
            skill_id="test_invalid_transition",
            name="Invalid Transition Skill",
            version="1.0.0",
            description="Invalid state test",
        )
        registry.register_skill(skill)

        # Illegal: DRAFT -> ACTIVE without VALIDATED
        with pytest.raises(ValueError, match="Illegal lifecycle transition"):
            registry.transition_lifecycle("test_invalid_transition", SkillLifecycleStatus.ACTIVE)

        # Transition to VALIDATED then DISABLED
        registry.transition_lifecycle("test_invalid_transition", SkillLifecycleStatus.VALIDATED)
        registry.transition_lifecycle("test_invalid_transition", SkillLifecycleStatus.DISABLED)

        # Illegal: DISABLED -> ACTIVE directly
        with pytest.raises(ValueError, match="Illegal lifecycle transition"):
            registry.transition_lifecycle("test_invalid_transition", SkillLifecycleStatus.ACTIVE)

    def test_disabled_skill_excluded_from_discovery(self):
        registry = get_skill_registry()
        skill = GovernedSkill(
            skill_id="test_disabled_skill",
            name="Disabled Skill",
            version="1.0.0",
            description="Should not be discovered",
            activation_triggers=["discoverable"],
        )
        registry.register_skill(skill)
        registry.transition_lifecycle("test_disabled_skill", SkillLifecycleStatus.VALIDATED)
        registry.transition_lifecycle("test_disabled_skill", SkillLifecycleStatus.ACTIVE)
        registry.transition_lifecycle("test_disabled_skill", SkillLifecycleStatus.DISABLED)

        discovery = SkillDiscoveryEngine(registry)
        candidates = discovery.discover("Task needing discoverable skill")
        skill_ids = [c.skill.skill_id for c in candidates]
        assert "test_disabled_skill" not in skill_ids

    def test_deprecated_skill_penalized_in_ranking(self):
        registry = get_skill_registry()
        active_skill = GovernedSkill(
            skill_id="skill_active",
            name="Database Optimizer",
            version="1.0.0",
            description="Optimizes queries",
            activation_triggers=["optimize queries"],
        )
        deprecated_skill = GovernedSkill(
            skill_id="skill_dep",
            name="Database Optimizer Legacy",
            version="0.9.0",
            description="Optimizes queries",
            activation_triggers=["optimize queries"],
        )
        registry.register_skill(active_skill)
        registry.register_skill(deprecated_skill)

        registry.transition_lifecycle("skill_active", SkillLifecycleStatus.VALIDATED)
        registry.transition_lifecycle("skill_active", SkillLifecycleStatus.ACTIVE)

        registry.transition_lifecycle("skill_dep", SkillLifecycleStatus.VALIDATED)
        registry.transition_lifecycle("skill_dep", SkillLifecycleStatus.ACTIVE)
        registry.transition_lifecycle("skill_dep", SkillLifecycleStatus.DEPRECATED)

        discovery = SkillDiscoveryEngine(registry)
        candidates = discovery.discover("Please optimize queries for database")
        scores = {c.skill.skill_id: c.score for c in candidates}

        assert "skill_active" in scores
        assert "skill_dep" in scores
        # Deprecated skill must receive penalty
        assert scores["skill_active"] > scores["skill_dep"]

    def test_preinstalled_bundled_skills(self):
        registry = get_skill_registry()
        bundled_ids = ["refactor_safe", "unit_test_generator", "diagnostic_repair", "config_updater"]
        for sid in bundled_ids:
            skill = registry.get_skill(sid)
            assert skill is not None, f"Bundled skill {sid} missing"
            assert skill.lifecycle_status == SkillLifecycleStatus.ACTIVE
            assert skill.is_system is True
            assert len(skill.allowed_tools) > 0


# ===========================================================================
# Class 2: TestSkillDiscoveryAndPermissions (6 tests)
# ===========================================================================

class TestSkillDiscoveryAndPermissions:

    def test_discovery_by_task_name_and_trigger(self):
        registry = get_skill_registry()
        discovery = SkillDiscoveryEngine(registry)

        # Match refactor_safe by trigger
        candidates = discovery.discover("Please refactor this legacy module safely")
        assert len(candidates) > 0
        top = candidates[0]
        assert top.skill.skill_id == "refactor_safe"
        assert top.reason in (SkillSelectionReason.TRIGGER_MATCH, SkillSelectionReason.DIRECT_NAME_MATCH)

    def test_explainable_discovery_reasons(self):
        registry = get_skill_registry()
        discovery = SkillDiscoveryEngine(registry)

        candidates = discovery.discover("Generate unit tests for test suite")
        assert len(candidates) > 0
        top = candidates[0]
        assert top.reason == SkillSelectionReason.TRIGGER_MATCH or top.reason == SkillSelectionReason.DIRECT_NAME_MATCH
        assert top.details is not None
        assert len(top.details) > 0
        assert top.score > 0.0

    def test_explicit_skill_request(self):
        registry = get_skill_registry()
        discovery = SkillDiscoveryEngine(registry)

        candidates = discovery.discover(
            "General task description",
            explicit_skill_id="diagnostic_repair",
        )
        assert len(candidates) == 1
        assert candidates[0].skill.skill_id == "diagnostic_repair"
        assert candidates[0].reason == SkillSelectionReason.EXPLICIT_USER_REQUEST
        assert candidates[0].score == 1.0

    def test_permission_auditor_allows_valid_tools(self):
        registry = get_skill_registry()
        skill = registry.get_skill("refactor_safe")
        auditor = SkillPermissionAuditor()

        # Tools allowed in refactor_safe
        audit_res = auditor.audit_tool_call(skill, "read_file", {})
        assert audit_res.allowed is True
        assert audit_res.violation_reason is None

    def test_permission_auditor_flags_hitl_and_unauthorized(self):
        registry = get_skill_registry()
        skill = registry.get_skill("refactor_safe")
        auditor = SkillPermissionAuditor()

        # Unauthorized tool: deploy_production is not in allowed_tools
        audit_unauthorized = auditor.audit_tool_call(skill, "deploy_production", {})
        assert audit_unauthorized.allowed is False
        assert "not permitted" in audit_unauthorized.violation_reason.lower()

        # Tool requiring HITL
        audit_hitl = auditor.audit_tool_call(skill, "run_command", {"command": "rm -rf /"}, requires_hitl=True)
        assert audit_hitl.requires_hitl is True

    def test_skill_security_policy_enforcement(self):
        policy = SkillSecurityPolicy(
            allow_arbitrary_shell=False,
            enforce_workspace_confinement=True,
            allowed_tools={"read_file", "write_file"},
        )
        is_valid, reason = policy.validate_action("read_file", {"path": "src/main.py"})
        assert is_valid is True

        # Tool outside allowed list
        is_valid, reason = policy.validate_action("run_command", {"command": "ls"})
        assert is_valid is False
        assert "not in allowed tools" in reason

        # Path traversal violation
        is_valid, reason = policy.validate_action("read_file", {"path": "../../etc/passwd"})
        assert is_valid is False
        assert "escapes workspace" in reason


# ===========================================================================
# Class 3: TestEngineeringMemoryContract (6 tests)
# ===========================================================================

class TestEngineeringMemoryContract:

    def test_record_creation_and_provenance(self):
        record = EngineeringMemoryRecord(
            tenant_id="tenant_alpha",
            task_type="refactor",
            problem_summary="Circular import between auth and users",
            solution_summary="Extracted common interfaces to auth_types",
            scope=MemoryScope.REPOSITORY,
            repository_id="repo_123",
            source_task_id="task_abc",
            source_execution_id="exec_xyz",
            author_type="agent",
            symbols_involved=["UserAuth", "Token"],
            files_involved=["auth.py", "users.py"],
        )
        assert record.record_id.startswith("em_")
        assert record.source_task_id == "task_abc"
        assert record.source_execution_id == "exec_xyz"
        assert record.author_type == "agent"
        assert record.created_at > 0

    def test_secret_scrubbing(self):
        raw_problem = "Encountered error using secret API key sk-proj-1234567890abcdef1234567890abcdef in auth"
        raw_solution = "Set Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-ID headers correctly with password=SuperSecretPassword123"
        record = EngineeringMemoryRecord(
            tenant_id="tenant_alpha",
            task_type="auth_fix",
            problem_summary=raw_problem,
            solution_summary=raw_solution,
        )
        # Verify secrets are scrubbed
        assert "sk-proj-1234567890abcdef1234567890abcdef" not in record.problem_summary
        assert "[REDACTED_API_KEY]" in record.problem_summary
        assert "Bearer eyJhbGci" not in record.solution_summary
        assert "[REDACTED_TOKEN]" in record.solution_summary
        assert "SuperSecretPassword123" not in record.solution_summary

    def test_verified_vs_unverified_record(self):
        v_rec = EngineeringMemoryRecord(
            tenant_id="tenant_alpha",
            task_type="test",
            problem_summary="Passing problem",
            solution_summary="Passing solution",
            is_verified=True,
            confidence=0.95,
        )
        assert v_rec.is_verified is True
        assert v_rec.confidence == 0.95

        unv_rec = EngineeringMemoryRecord(
            tenant_id="tenant_alpha",
            task_type="test",
            problem_summary="Unverified problem",
            solution_summary="Unverified solution",
            is_verified=False,
            confidence=0.3,
        )
        assert unv_rec.is_verified is False

    def test_diagnostic_failure_record(self):
        fail_rec = EngineeringMemoryRecord(
            tenant_id="tenant_alpha",
            task_type="build_error",
            problem_summary="Type error during build",
            solution_summary="Known compiler bug in dependency X",
            is_verified=False,
            is_diagnostic_failure=True,
            confidence=0.8,
        )
        assert fail_rec.is_diagnostic_failure is True
        # Diagnostic failures represent known failure patterns, not successful fixes

    def test_serialization_and_deserialization(self):
        record = EngineeringMemoryRecord(
            tenant_id="tenant_alpha",
            task_type="refactor",
            problem_summary="Problem statement",
            solution_summary="Solution statement",
            symbols_involved=["ClassA"],
            files_involved=["a.py"],
            skills_used=["refactor_safe"],
        )
        d = record.as_dict()
        assert isinstance(d, dict)
        assert d["record_id"] == record.record_id
        assert d["tenant_id"] == "tenant_alpha"

        restored = EngineeringMemoryRecord.from_dict(d)
        assert restored.record_id == record.record_id
        assert restored.symbols_involved == ["ClassA"]
        assert restored.skills_used == ["refactor_safe"]

    def test_memory_scope_enum_validations(self):
        assert MemoryScope.GLOBAL.value == "GLOBAL"
        assert MemoryScope.TENANT.value == "TENANT"
        assert MemoryScope.REPOSITORY.value == "REPOSITORY"
        assert MemoryScope.TASK_TYPE.value == "TASK_TYPE"
        assert MemoryScope.SKILL.value == "SKILL"


# ===========================================================================
# Class 4: TestMemoryScopingAndTenantIsolation (6 tests)
# ===========================================================================

class TestMemoryScopingAndTenantIsolation:

    def test_strict_tenant_isolation(self):
        kernel = EngineeringMemoryKernel()
        rec_a = EngineeringMemoryRecord(
            tenant_id="tenant_A",
            task_type="refactor",
            problem_summary="Secret architecture for tenant A",
            solution_summary="Proprietary algorithm implementation",
            scope=MemoryScope.TENANT,
        )
        kernel.store(rec_a)

        # Tenant B queries
        results_b, _ = kernel.retrieve_relevant(
            tenant_id="tenant_B",
            task_objective="Proprietary algorithm implementation",
        )
        assert len(results_b) == 0

        # Tenant A queries
        results_a, _ = kernel.retrieve_relevant(
            tenant_id="tenant_A",
            task_objective="Proprietary algorithm implementation",
        )
        assert len(results_a) == 1
        assert results_a[0].record_id == rec_a.record_id

    def test_global_scope_visible_across_tenants(self):
        kernel = EngineeringMemoryKernel()
        rec_global = EngineeringMemoryRecord(
            tenant_id="system",
            task_type="python_standard",
            problem_summary="Python package build failure",
            solution_summary="Upgrade pip and setuptools before wheel compile",
            scope=MemoryScope.GLOBAL,
            is_verified=True,
        )
        kernel.store(rec_global)

        # Both tenants should retrieve this global memory
        res_a, _ = kernel.retrieve_relevant(tenant_id="tenant_A", task_objective="Python package build failure")
        res_b, _ = kernel.retrieve_relevant(tenant_id="tenant_B", task_objective="Python package build failure")
        assert len(res_a) == 1
        assert len(res_b) == 1
        assert res_a[0].record_id == rec_global.record_id
        assert res_b[0].record_id == rec_global.record_id

    def test_repository_scoping(self):
        kernel = EngineeringMemoryKernel()
        rec_repo1 = EngineeringMemoryRecord(
            tenant_id="tenant_A",
            task_type="refactor",
            problem_summary="Config file location",
            solution_summary="Move to settings.yaml",
            scope=MemoryScope.REPOSITORY,
            repository_id="repo_1",
            is_verified=True,
        )
        kernel.store(rec_repo1)

        # Query for repo_2 should NOT retrieve repo_1 memory
        res_diff_repo, _ = kernel.retrieve_relevant(
            tenant_id="tenant_A",
            repository_id="repo_2",
            task_objective="Config file location",
        )
        assert len(res_diff_repo) == 0

        # Query for repo_1 matches
        res_same_repo, _ = kernel.retrieve_relevant(
            tenant_id="tenant_A",
            repository_id="repo_1",
            task_objective="Config file location",
        )
        assert len(res_same_repo) == 1

    def test_tenant_missing_raises_key_error(self):
        kernel = EngineeringMemoryKernel()
        with pytest.raises(KeyError, match="tenant_id"):
            kernel.retrieve_relevant(tenant_id="", task_objective="test")

    def test_sqlite_persistence_across_kernel_instances(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "test_mem.db"

            # Instance 1: Store memory
            k1 = EngineeringMemoryKernel(db_path=db_file)
            rec = EngineeringMemoryRecord(
                tenant_id="tenant_persist",
                task_type="fix",
                problem_summary="Persistent problem statement",
                solution_summary="Persistent solution fix",
                is_verified=True,
            )
            k1.store(rec)

            # Instance 2: Load from same DB
            k2 = EngineeringMemoryKernel(db_path=db_file)
            results, _ = k2.retrieve_relevant(
                tenant_id="tenant_persist",
                task_objective="Persistent problem statement",
            )
            assert len(results) == 1
            assert results[0].record_id == rec.record_id
            assert results[0].problem_summary == "Persistent problem statement"

    def test_memory_expiration(self):
        kernel = EngineeringMemoryKernel()
        expired_rec = EngineeringMemoryRecord(
            tenant_id="tenant_exp",
            task_type="temp",
            problem_summary="Expired issue",
            solution_summary="Old solution",
            expires_at=time.time() - 100,  # Expired in past
        )
        kernel.store(expired_rec)

        res, _ = kernel.retrieve_relevant(tenant_id="tenant_exp", task_objective="Expired issue")
        assert len(res) == 0


# ===========================================================================
# Class 5: TestMemoryRankingAndConflictHandling (6 tests)
# ===========================================================================

class TestMemoryRankingAndConflictHandling:

    def test_hybrid_ranking_weights(self):
        kernel = EngineeringMemoryKernel()
        # High match: matches repo + task_type + symbol + verified
        high_rec = EngineeringMemoryRecord(
            tenant_id="tenant_rank",
            task_type="refactor",
            problem_summary="Refactor class TokenManager",
            solution_summary="Split TokenManager into Issuer and Validator",
            repository_id="repo_target",
            symbols_involved=["TokenManager"],
            is_verified=True,
            confidence=0.9,
        )
        # Low match: generic, unverified
        low_rec = EngineeringMemoryRecord(
            tenant_id="tenant_rank",
            task_type="docs",
            problem_summary="Write docs for TokenManager",
            solution_summary="Updated markdown",
            repository_id="other_repo",
            is_verified=False,
            confidence=0.4,
        )
        kernel.store(high_rec)
        kernel.store(low_rec)

        results, _ = kernel.retrieve_relevant(
            tenant_id="tenant_rank",
            repository_id="repo_target",
            task_objective="Refactor class TokenManager",
            task_type="refactor",
            symbols=["TokenManager"],
        )
        assert len(results) >= 1
        assert results[0].record_id == high_rec.record_id

    def test_conflict_detection_opposing_solutions(self):
        kernel = EngineeringMemoryKernel()
        rec_1 = EngineeringMemoryRecord(
            tenant_id="tenant_conf",
            task_type="architecture",
            problem_summary="Session storage approach",
            solution_summary="Store sessions exclusively in Redis in-memory cache",
            symbols_involved=["SessionStore"],
            diff_summary="+ class RedisSessionStore(BaseStore): ...",
            is_verified=True,
        )
        rec_2 = EngineeringMemoryRecord(
            tenant_id="tenant_conf",
            task_type="architecture",
            problem_summary="Session storage approach",
            solution_summary="Store sessions exclusively in PostgreSQL database with durable transactions",
            symbols_involved=["SessionStore"],
            diff_summary="+ class PostgresSessionStore(BaseStore): ...",
            is_verified=True,
        )
        kernel.store(rec_1)
        kernel.store(rec_2)

        results, conflicts = kernel.retrieve_relevant(
            tenant_id="tenant_conf",
            task_objective="Session storage approach",
            symbols=["SessionStore"],
        )
        assert len(conflicts) > 0
        conflict = conflicts[0]
        assert "SessionStore" in conflict.divergent_symbols or "Session" in conflict.conflict_description
        assert conflict.resolution_guidance is not None

    def test_conflict_guidance_for_planner(self):
        kernel = EngineeringMemoryKernel()
        c = EngineeringMemoryConflict(
            conflict_id="conf_1",
            conflicting_record_ids=["em_1", "em_2"],
            divergent_symbols=["AuthProvider"],
            conflict_description="Two historical solutions contradict each other",
            resolution_guidance="Verify current repository dependencies before selecting JWT vs Session cookies.",
        )
        d = c.as_dict()
        assert d["conflict_id"] == "conf_1"
        assert "JWT vs Session" in d["resolution_guidance"]

    def test_no_conflict_when_solutions_harmonious(self):
        kernel = EngineeringMemoryKernel()
        rec_1 = EngineeringMemoryRecord(
            tenant_id="tenant_harm",
            task_type="test",
            problem_summary="Add unit tests for ModuleA",
            solution_summary="Added unit tests in test_a.py",
            symbols_involved=["ModuleA"],
            is_verified=True,
        )
        rec_2 = EngineeringMemoryRecord(
            tenant_id="tenant_harm",
            task_type="docs",
            problem_summary="Document ModuleB",
            solution_summary="Added docstrings to ModuleB",
            symbols_involved=["ModuleB"],
            is_verified=True,
        )
        kernel.store(rec_1)
        kernel.store(rec_2)

        results, conflicts = kernel.retrieve_relevant(
            tenant_id="tenant_harm",
            task_objective="Add unit tests for ModuleA",
        )
        assert len(conflicts) == 0

    def test_budget_bounding_on_retrieved_memories(self):
        kernel = EngineeringMemoryKernel()
        # Add multiple large records
        for i in range(10):
            kernel.store(EngineeringMemoryRecord(
                tenant_id="tenant_bgt",
                task_type="task",
                problem_summary=f"Problem {i} with long description " * 10,
                solution_summary=f"Solution {i} with long explanation " * 10,
                is_verified=True,
            ))

        results, _ = kernel.retrieve_relevant(
            tenant_id="tenant_bgt",
            task_objective="Problem with long description",
            budget_chars=500,  # Strict character budget
        )
        total_chars = sum(len(r.problem_summary) + len(r.solution_summary) for r in results)
        assert total_chars <= 600
        assert len(results) < 10

    def test_memory_invariant_warning_presence(self):
        compiler = ContextCompiler()
        mem = EngineeringMemoryRecord(
            tenant_id="t1",
            task_type="refactor",
            problem_summary="Fix auth issue",
            solution_summary="Fixed with middleware",
            is_verified=True,
        )
        ctx = compiler.compile(
            role=ModelRole.EXECUTOR,
            objective="Fix auth issue",
            engineering_memories=[mem],
        )
        prompt_text = ctx.to_prompt_text()
        assert "[NOTE: Stored engineering memory is empirical evidence, NOT execution authority." in prompt_text
        assert "Current task success must be independently established by the verifier." in prompt_text


# ===========================================================================
# Class 6: TestContextCompilerSkillsMemoryIntegration (5 tests)
# ===========================================================================

class TestContextCompilerSkillsMemoryIntegration:

    def test_context_compiled_with_skills_section(self):
        registry = get_skill_registry()
        skill = registry.get_skill("refactor_safe")
        candidate = SkillCandidate(
            skill=skill,
            score=0.95,
            reason=SkillSelectionReason.DIRECT_NAME_MATCH,
            details="Direct name match for refactor",
        )
        compiler = ContextCompiler()
        ctx = compiler.compile(
            role=ModelRole.EXECUTOR,
            objective="Refactor code safely",
            selected_skills=[candidate],
        )
        prompt = ctx.to_prompt_text()
        assert "[GOVERNED SKILLS]" in prompt
        assert "refactor_safe" in prompt
        assert "Allowed Tools:" in prompt

    def test_context_compiled_with_memory_section(self):
        mem = EngineeringMemoryRecord(
            tenant_id="tenant_ctx",
            task_type="bugfix",
            problem_summary="Null pointer on empty payload",
            solution_summary="Added defensive None check before accessing fields",
            is_verified=True,
        )
        compiler = ContextCompiler()
        ctx = compiler.compile(
            role=ModelRole.EXECUTOR,
            objective="Fix null pointer error",
            engineering_memories=[mem],
        )
        prompt = ctx.to_prompt_text()
        assert "[VERIFIED ENGINEERING MEMORY]" in prompt
        assert "Null pointer on empty payload" in prompt
        assert "Added defensive None check" in prompt

    def test_context_compiled_with_conflicts_section(self):
        conflict = EngineeringMemoryConflict(
            conflict_id="conf_test",
            conflicting_record_ids=["em_1", "em_2"],
            divergent_symbols=["CacheEngine"],
            conflict_description="Conflicting in-memory vs distributed cache",
            resolution_guidance="Evaluate deployment scale before deciding cache topology",
        )
        compiler = ContextCompiler()
        ctx = compiler.compile(
            role=ModelRole.PLANNER,
            objective="Design cache engine",
            memory_conflicts=[conflict],
        )
        prompt = ctx.to_prompt_text()
        assert "[MEMORY CONFLICTS — REQUIRES PLANNER RESOLUTION]" in prompt
        assert "Conflicting in-memory vs distributed cache" in prompt
        assert "Evaluate deployment scale before deciding cache topology" in prompt

    def test_context_budget_respects_token_limits(self):
        compiler = ContextCompiler()
        memories = [
            EngineeringMemoryRecord(
                tenant_id="tenant_t",
                task_type="big",
                problem_summary="Large problem " * 50,
                solution_summary="Large solution " * 50,
                is_verified=True,
            ) for _ in range(5)
        ]
        # Low budget of 500 tokens
        ctx = compiler.compile(
            role=ModelRole.EXECUTOR,
            objective="Handle big task",
            context_budget_tokens=500,
            engineering_memories=memories,
        )
        prompt = ctx.to_prompt_text()
        assert len(prompt) > 0
        assert ctx.tokens_used <= 600

    def test_role_adaptation_skills_and_memory(self):
        compiler = ContextCompiler()
        skill = get_skill_registry().get_skill("diagnostic_repair")
        candidate = SkillCandidate(
            skill=skill,
            score=0.9,
            reason=SkillSelectionReason.TRIGGER_MATCH,
            details="Trigger match for repair",
        )
        mem = EngineeringMemoryRecord(
            tenant_id="t1",
            task_type="repair",
            problem_summary="Broken unit test",
            solution_summary="Fixed assertion",
            is_verified=True,
        )
        for role in (ModelRole.PLANNER, ModelRole.EXECUTOR, ModelRole.REPAIR):
            ctx = compiler.compile(
                role=role,
                objective="Repair failing test",
                selected_skills=[candidate],
                engineering_memories=[mem],
            )
            prompt = ctx.to_prompt_text()
            assert f"[ROLE: {role.value.lower()}]" in prompt
            assert "diagnostic_repair" in prompt


# ===========================================================================
# Class 7: TestPipelineAndRuntimeLearningLoop (5 tests)
# ===========================================================================

class TestPipelineAndRuntimeLearningLoop:

    def test_pipeline_discovers_skills_and_retrieves_memory(self):
        from routes.tasks import run_task_pipeline

        record = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_loop_1",
            payload={
                "prompt": "Please refactor this service safely and run tests",
                "execution_constraints": {"allow_plain_workspace": True},
            },
        )
        tid = record["execution_id"]
        provider = FakePipelineProvider(succeed=True)

        res = run_task_pipeline(task_id=tid, tenant_id="tenant_loop_1", provider=provider)
        replay = res.get("replay_events", [])
        event_types = [e.get("event_type") for e in replay]

        assert "skill.discovered" in event_types
        assert "memory.retrieved" in event_types
        assert "context.compiled" in event_types

    def test_pipeline_promotes_memory_on_verified_success(self):
        from routes.tasks import run_task_pipeline

        record = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_loop_promote",
            payload={
                "prompt": "Safe refactor of user service",
                "execution_constraints": {"allow_plain_workspace": True},
            },
        )
        tid = record["execution_id"]
        provider = FakePipelineProvider(succeed=True)

        res = run_task_pipeline(task_id=tid, tenant_id="tenant_loop_promote", provider=provider)
        replay = res.get("replay_events", [])
        event_types = [e.get("event_type") for e in replay]

        assert "task.completed" in event_types
        assert "verifier.passed" in event_types
        assert "memory.persisted" in event_types

    def test_pipeline_does_not_promote_memory_on_task_failure(self):
        from routes.tasks import run_task_pipeline

        record = durable_state.create(
            kind="agent_task",
            tenant_id="tenant_loop_fail",
            payload={
                "prompt": "Unachievable task with failing command",
                "execution_constraints": {"allow_plain_workspace": True, "max_iterations": 2},
                "execution_budget": {"max_repair_attempts": 0},
            },
        )
        tid = record["execution_id"]
        provider = FakePipelineProvider(succeed=False)

        res = run_task_pipeline(task_id=tid, tenant_id="tenant_loop_fail", provider=provider)
        replay = res.get("replay_events", [])
        event_types = [e.get("event_type") for e in replay]

        # Memory MUST NOT be promoted on failure
        assert "memory.persisted" not in event_types

    def test_runtime_initializes_skill_and_memory_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = TaskExecutionContext(
                task_id="task_fields",
                objective="Check fields",
                workspace_dir=tmpdir,
                tenant_id="tenant_fields",
            )
            runtime = RealAgentRuntime(context=ctx, provider=FakePipelineProvider())
            assert hasattr(runtime, "skill_registry")
            assert hasattr(runtime, "skill_discovery")
            assert hasattr(runtime, "memory_kernel")
            assert runtime.skill_registry is not None
            assert runtime.skill_discovery is not None
            assert runtime.memory_kernel is not None

    def test_end_to_end_learning_cycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "shared_memory.db"
            kernel = EngineeringMemoryKernel(db_path=db_path)

            # Step 1: Promote outcome from Task 1
            promoted = kernel.promote_task_outcome(
                task_id="task_1",
                tenant_id="tenant_learn",
                task_type="refactor",
                task_objective="Resolve memory leak in ImageProcessor",
                summary="Replaced circular self-references with weakref.ref",
                diff="""--- a/img.py
+++ b/img.py
@@ -10,1 +10,1 @@
- self._cache = self
+ self._cache = weakref.ref(self)""",
                files_changed=["img.py"],
                verification_summary={"status": "VERIFIED", "details": "100% test pass", "score": 1.0},
                symbols_involved=["ImageProcessor"],
                skills_used=["refactor_safe"],
            )
            assert promoted is not None

            # Step 2: In Task 2, query for same problem
            retrieved, _ = kernel.retrieve_relevant(
                tenant_id="tenant_learn",
                task_objective="Fix memory leak in ImageProcessor",
                symbols=["ImageProcessor"],
            )
            assert len(retrieved) >= 1
            assert retrieved[0].record_id == promoted.record_id
            assert "weakref" in retrieved[0].solution_summary


# ===========================================================================
# Class 8: TestSecurityAndGovernanceInvariants (5 tests)
# ===========================================================================

class TestSecurityAndGovernanceInvariants:

    def test_skill_cannot_bypass_hitl(self):
        registry = get_skill_registry()
        skill = registry.get_skill("refactor_safe")
        auditor = SkillPermissionAuditor()

        # Danger action flagged for HITL gate
        audit_res = auditor.audit_tool_call(
            skill,
            "run_command",
            {"command": "rm -rf /data"},
            requires_hitl=True,
        )
        assert audit_res.requires_hitl is True

    def test_memory_says_worked_does_not_bypass_verifier(self):
        """Invariant: MEMORY SAYS 'THIS WORKED' != CURRENT TASK SUCCESS."""
        kernel = EngineeringMemoryKernel()
        # Stored memory says this exact task worked before
        rec = EngineeringMemoryRecord(
            tenant_id="tenant_inv",
            task_type="math",
            problem_summary="Compute sum 2+2",
            solution_summary="Return 4",
            is_verified=True,
        )
        kernel.store(rec)

        compiler = ContextCompiler()
        ctx = compiler.compile(
            role=ModelRole.EXECUTOR,
            objective="Compute sum 2+2",
            engineering_memories=[rec],
        )
        prompt = ctx.to_prompt_text()

        # The prompt explicitly warns the model that memory is not execution authority
        assert "NOT execution authority" in prompt
        assert "independently established by the verifier" in prompt

    def test_skill_cannot_escape_workspace_boundary(self):
        policy = SkillSecurityPolicy(
            enforce_workspace_confinement=True,
            allowed_tools={"write_file"},
        )
        # Attempt traversal outside workspace
        is_valid, reason = policy.validate_action("write_file", {"path": "../../../sensitive.txt"})
        assert is_valid is False
        assert "escapes workspace" in reason

    def test_run_budget_enforced_with_skills_and_memory(self):
        from execution.orchestration.run_budget import RunBudget, BudgetExhaustedError
        budget = RunBudget(max_model_calls=1, max_tool_calls=2)

        # Consume model call
        budget.consume_model_call(tokens=100)
        assert budget.model_calls_consumed == 1

        # Next model call exceeds budget
        with pytest.raises(BudgetExhaustedError, match="max_model_calls"):
            budget.consume_model_call(tokens=50)

    def test_scrubbing_prevents_secret_leak_in_compiled_prompt(self):
        mem = EngineeringMemoryRecord(
            tenant_id="tenant_scrub",
            task_type="api_connect",
            problem_summary="Failed to connect with secret key: sk-ant-api03-1234567890abcdef1234567890abcdef",
            solution_summary="Rotated token Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret123.sig",
            is_verified=True,
        )
        compiler = ContextCompiler()
        ctx = compiler.compile(
            role=ModelRole.EXECUTOR,
            objective="Connect to API",
            engineering_memories=[mem],
        )
        prompt = ctx.to_prompt_text()
        # Compiled prompt must be clean of secrets
        assert "sk-ant-api03-1234567890abcdef1234567890abcdef" not in prompt
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret123" not in prompt
        assert "[REDACTED_API_KEY]" in prompt
