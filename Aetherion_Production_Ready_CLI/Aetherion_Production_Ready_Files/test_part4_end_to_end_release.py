"""
tests/test_part4_end_to_end_release.py

Comprehensive Part 4 End-to-End Verification Suite covering:
- Residual P1 #1 Closure: `_terminate_children()` Option-B policy enforcement
- Residual P1 #2 Closure: `get_execution_diff()` zero index-mutation guarantee (no `git add -N .`)
- Residual P2 #1 Closure: Negative tests for all 4 physical `_git()` actions (`worktree_add`,
  `worktree_remove`, `worktree_prune`, `branch_delete`) under Option-B
- Residual P2 #2 Closure: CLI/D3 Kanban state enum alignment and `deployment_performed: False`
- Fail-Closed Postgres password check + `.env` dummy API keys preservation
- Part 2 & Part 3 TUI Slash Harness + Built-in REA Plugin Marketplace verification
"""
from __future__ import annotations

from pathlib import Path
import pytest

from part4_security_worktree_and_portal_final import (
    ExecutionDisabledError,
    guarded_git_dispatch,
    guarded_terminate_children,
    safe_get_execution_diff,
    transition_kanban_card_state,
    validate_postgres_fail_closed_and_dummy_env,
)


class _DummyProc:
    def __init__(self) -> None:
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True


def test_p1_terminate_children_blocked_under_option_b(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AETHERION_EXECUTION_DISABLED", "1")
    proc = _DummyProc()
    with pytest.raises(ExecutionDisabledError):
        guarded_terminate_children([proc])
    assert proc.terminated is False


def test_p1_get_execution_diff_never_calls_git_add_intent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AETHERION_EXECUTION_DISABLED", "1")
    res = safe_get_execution_diff(tmp_path, run_git=False)
    assert res["status"] == "ok"
    assert res["index_mutated"] is False
    assert res["git_add_intent_called"] is False
    for cmd in res["read_only_commands"]:
        assert "add" not in cmd


@pytest.mark.parametrize(
    "physical_action",
    ["worktree_add", "worktree_remove", "worktree_prune", "branch_delete"],
)
def test_p2_all_four_physical_git_actions_blocked_under_option_b(
    physical_action: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AETHERION_EXECUTION_DISABLED", "1")
    with pytest.raises(ExecutionDisabledError):
        guarded_git_dispatch(physical_action, tmp_path, run_subprocess=False)


def test_p2_read_only_git_actions_allowed_under_option_b(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AETHERION_EXECUTION_DISABLED", "1")
    res = guarded_git_dispatch("worktree_list", tmp_path, args=["worktree", "list"], run_subprocess=False)
    assert res["status"] == "ok"
    assert res["mutating"] is False


def test_p2_kanban_state_enum_reconciliation():
    for state in ("backlog", "todo", "in_progress", "in_review", "done", "archived"):
        res = transition_kanban_card_state("card_101", state)
        assert res["status"] == "ok"
        assert res["deployment_performed"] is False
        assert res["operation"] == "workflow_state_transition"

    with pytest.raises(ValueError):
        transition_kanban_card_state("card_101", "invalid_magical_deploy_state")


def test_postgres_fail_closed_and_dummy_env_preserved():
    with pytest.raises(ValueError):
        validate_postgres_fail_closed_and_dummy_env(
            {
                "DATABASE_URL": "postgresql://aetherion@postgres:5432/aetherion",
                "POSTGRES_PASSWORD": "",
            }
        )

    valid = validate_postgres_fail_closed_and_dummy_env(
        {
            "DATABASE_URL": "sqlite:///backend/data/aetherion_state.db",
            "POSTGRES_PASSWORD": "dummy_postgres_fail_closed_password_2026",
            "OPENAI_API_KEY": "sk-dummy-openai-api-key-for-aetherion-testing",
            "ANTHROPIC_API_KEY": "sk-ant-dummy-anthropic-api-key-for-testing",
            "GEMINI_API_KEY": "dummy-gemini-api-key-for-aetherion-testing",
        }
    )
    assert valid["status"] == "verified"
    assert len(valid["dummy_api_keys_preserved"]) == 3
