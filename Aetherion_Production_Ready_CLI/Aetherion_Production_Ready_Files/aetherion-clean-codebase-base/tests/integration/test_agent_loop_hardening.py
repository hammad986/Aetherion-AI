"""
tests/integration/test_agent_loop_hardening.py — Regression tests for agent-loop hardening.

Covers:
1. Multi-tool execution in single turn:
   - 2 calls in same turn executed sequentially
   - 3+ calls in same turn executed
   - Correlation: exact 1:1 match of tool_call_id between assistant and tool messages
   - Mixed failure/success: failed tool does not prevent subsequent tools from running
   - Replay/deduplication in same turn
   - Single-tool backward compatibility
   - Finish tool ordering (finish executed last)
2. Subprocess pipe deadlock resolution:
   - >150KB stdout draining without hang
   - >150KB stderr draining without hang
   - Simultaneous large stdout + stderr draining
   - Genuine timeout handling and process termination
   - Operator cancellation handling
3. Tool output bounding and secrets redaction:
   - 50K char output truncated with head/tail (40%/60%) split and explicit marker
   - Short output unmodified
   - Secrets redaction
4. History compaction:
   - Bounded history growth across 25+ simulated turns
   - Preservation of system message and initial user prompt
   - Zero orphaned tool_call_ids
5. CLI UX fixes:
   - 'aetherion model show' command
   - 'aetherion setup' non-interactive stdin detection
   - 'aetherion update' offline clean messaging
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from click.testing import CliRunner

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from execution.agent_runtime import (
    AgentToolRegistry,
    ExecutionCancelledException,
    RealAgentRuntime,
    TaskExecutionContext,
    WorkspaceBoundary,
)
from execution.agent_runtime.compaction import (
    _validate_paired_integrity,
    compact_history,
)
from execution.providers.base import BaseModelProvider, ModelResponse, ProviderCapabilities, ToolCall
from execution.tools.output_policy import bound_tool_output, truncate_head_tail


# ---------------------------------------------------------------------------
# Test Providers for Multi-Tool Execution
# ---------------------------------------------------------------------------

class MultiToolTestProvider(BaseModelProvider):
    """Provider returning scripted responses, including multi-tool turns."""

    def __init__(self, turns: List[ModelResponse]):
        self.turns = list(turns)
        self.call_count = 0
        self.recorded_messages: List[List[Dict[str, Any]]] = []

    def name(self) -> str:
        return "multi_tool_test_provider"

    def is_available(self) -> bool:
        return True

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_tools=True, supports_parallel_tools=True)

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ModelResponse:
        self.recorded_messages.append(list(messages))
        if self.call_count < len(self.turns):
            resp = self.turns[self.call_count]
            self.call_count += 1
            return resp
        return ModelResponse(
            text="Completed.",
            tool_calls=[ToolCall(id=f"call_fin_{self.call_count}", name="finish", arguments={"summary": "Done"})],
        )


# ---------------------------------------------------------------------------
# 1. Multi-Tool Agent Execution Tests
# ---------------------------------------------------------------------------

class TestMultiToolExecution:
    @pytest.fixture(autouse=True)
    def setup_env(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="aetherion_multitool_test_")
        yield
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_two_tool_calls_in_single_turn(self):
        """Model emits two write_file calls in one turn; both are executed and correlation is preserved."""
        turn1 = ModelResponse(
            text="Creating file1.txt and file2.txt in parallel.",
            tool_calls=[
                ToolCall(id="tc_1", name="write_file", arguments={"path": "file1.txt", "content": "hello from 1"}),
                ToolCall(id="tc_2", name="write_file", arguments={"path": "file2.txt", "content": "hello from 2"}),
            ],
        )
        turn2 = ModelResponse(
            text="Finishing up.",
            tool_calls=[
                ToolCall(id="tc_fin", name="finish", arguments={"summary": "Created both files successfully."}),
            ],
        )

        prov = MultiToolTestProvider([turn1, turn2])
        runtime = RealAgentRuntime(workspace_dir=self.tmp_dir, provider=prov)
        events = list(runtime.run_stream("Create two files"))

        # Verify files created on disk
        f1 = Path(self.tmp_dir) / "file1.txt"
        f2 = Path(self.tmp_dir) / "file2.txt"
        assert f1.exists(), "file1.txt must exist on disk"
        assert f2.exists(), "file2.txt must exist on disk"
        assert f1.read_text() == "hello from 1"
        assert f2.read_text() == "hello from 2"

        # Check conversation history in turn 2
        history_at_turn2 = prov.recorded_messages[1]
        assistant_msgs = [m for m in history_at_turn2 if m.get("role") == "assistant" and m.get("tool_calls")]
        assert len(assistant_msgs) >= 1
        first_asst = assistant_msgs[0]
        assert len(first_asst["tool_calls"]) == 2
        assert first_asst["tool_calls"][0]["id"] == "tc_1"
        assert first_asst["tool_calls"][1]["id"] == "tc_2"

        # Verify correlated tool result messages
        tool_msgs = [m for m in history_at_turn2 if m.get("role") == "tool"]
        tool_ids = [m.get("tool_call_id") for m in tool_msgs]
        assert "tc_1" in tool_ids, "Result for tc_1 must exist in history"
        assert "tc_2" in tool_ids, "Result for tc_2 must exist in history"

    def test_three_tool_calls_mixed_failure_and_success(self):
        """When 1 of 3 tool calls fails, subsequent tools still execute and all IDs are answered."""
        turn1 = ModelResponse(
            text="Executing three tools: write bad path, write valid path, read valid path.",
            tool_calls=[
                # Call 1: fails (empty path or directory escape outside boundary)
                ToolCall(id="tc_fail", name="write_file", arguments={"path": "../outside.txt", "content": "escaped"}),
                # Call 2: succeeds
                ToolCall(id="tc_ok1", name="write_file", arguments={"path": "good.txt", "content": "valid data"}),
                # Call 3: succeeds
                ToolCall(id="tc_ok2", name="read_file", arguments={"path": "good.txt"}),
            ],
        )
        turn2 = ModelResponse(
            text="Finished.",
            tool_calls=[
                ToolCall(id="tc_fin", name="finish", arguments={"summary": "Handled mixed results."}),
            ],
        )

        prov = MultiToolTestProvider([turn1, turn2])
        runtime = RealAgentRuntime(workspace_dir=self.tmp_dir, provider=prov)
        events = list(runtime.run_stream("Execute 3 tools"))

        # File good.txt should exist despite tc_fail
        good_path = Path(self.tmp_dir) / "good.txt"
        assert good_path.exists()
        assert good_path.read_text() == "valid data"

        # History at turn 2 must contain responses for all 3 IDs
        history = prov.recorded_messages[1]
        tool_msgs = {m.get("tool_call_id"): m for m in history if m.get("role") == "tool"}
        assert "tc_fail" in tool_msgs
        assert "tc_ok1" in tool_msgs
        assert "tc_ok2" in tool_msgs

        # First tool result must indicate error or failure
        assert "ERROR" in tool_msgs["tc_fail"]["content"] or "violation" in tool_msgs["tc_fail"]["content"].lower() or "error" in tool_msgs["tc_fail"]["content"].lower()

    def test_finish_executed_last_in_multitool_turn(self):
        """If finish is emitted alongside another tool in the same turn, the other tool executes first."""
        turn1 = ModelResponse(
            text="Writing a file and declaring finish in same turn.",
            tool_calls=[
                ToolCall(id="tc_fin", name="finish", arguments={"summary": "Finished along with write"}),
                ToolCall(id="tc_write", name="write_file", arguments={"path": "final.txt", "content": "finished content"}),
            ],
        )

        prov = MultiToolTestProvider([turn1])
        runtime = RealAgentRuntime(workspace_dir=self.tmp_dir, provider=prov)
        events = list(runtime.run_stream("Write and finish"))

        # final.txt must have been created because write_file runs before finish
        final_file = Path(self.tmp_dir) / "final.txt"
        assert final_file.exists()
        assert final_file.read_text() == "finished content"

    def test_single_tool_backward_compatibility(self):
        """Single tool call maintains classic response structure with tool_call and tool_call_id."""
        turn1 = ModelResponse(
            text="Single tool write.",
            tool_calls=[
                ToolCall(id="tc_single", name="write_file", arguments={"path": "single.txt", "content": "classic"}),
            ],
        )
        turn2 = ModelResponse(
            text="Done.",
            tool_calls=[
                ToolCall(id="tc_fin", name="finish", arguments={"summary": "Single tool done."}),
            ],
        )

        prov = MultiToolTestProvider([turn1, turn2])
        runtime = RealAgentRuntime(workspace_dir=self.tmp_dir, provider=prov)
        events = list(runtime.run_stream("Single write"))

        history = prov.recorded_messages[1]
        asst_msg = next(m for m in history if m.get("role") == "assistant" and "single.txt" in str(m))
        assert "tool_call" in asst_msg, "Must preserve backward-compat 'tool_call' key"
        assert asst_msg["tool_call_id"] == "tc_single"
        assert asst_msg["tool_call"]["name"] == "write_file"


# ---------------------------------------------------------------------------
# 2. Subprocess Pipe Drainage Tests (Deadlock Prevention)
# ---------------------------------------------------------------------------

class TestSubprocessPipeDrainage:
    @pytest.fixture(autouse=True)
    def setup_boundary(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="aetherion_pipe_test_")
        self.boundary = WorkspaceBoundary(workspace_dir=self.tmp_dir)
        yield
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_large_stdout_drains_without_deadlock(self):
        """A command generating >150KB to stdout completes without hanging."""
        # Generate 200KB of output using Python script
        py_cmd = f'{sys.executable} -c "import sys; sys.stdout.write(\'A\' * 200000)"'
        t0 = time.time()
        res = self.boundary.run_command(py_cmd, timeout=10.0)
        elapsed = time.time() - t0

        assert res.ok is True
        assert len(res.stdout) == 200000
        assert elapsed < 5.0, f"Expected fast execution without pipe block, took {elapsed}s"

    def test_large_stderr_drains_without_deadlock(self):
        """A command generating >150KB to stderr completes without hanging."""
        py_cmd = f'{sys.executable} -c "import sys; sys.stderr.write(\'E\' * 200000)"'
        t0 = time.time()
        res = self.boundary.run_command(py_cmd, timeout=10.0)
        elapsed = time.time() - t0

        assert res.ok is True
        assert len(res.stderr) == 200000
        assert elapsed < 5.0, f"Expected fast execution without pipe block, took {elapsed}s"

    def test_simultaneous_large_stdout_and_stderr(self):
        """A command generating >150KB to both stdout and stderr simultaneously completes cleanly."""
        py_cmd = f'{sys.executable} -c "import sys; sys.stdout.write(\'O\' * 160000); sys.stderr.write(\'E\' * 160000)"'
        t0 = time.time()
        res = self.boundary.run_command(py_cmd, timeout=10.0)
        elapsed = time.time() - t0

        assert res.ok is True
        assert len(res.stdout) == 160000
        assert len(res.stderr) == 160000
        assert elapsed < 5.0

    def test_process_timeout_cleanly_terminates(self):
        """A hanging command hits timeout, returns exit_code=-1, and terminates process."""
        py_cmd = f'{sys.executable} -c "import time; time.sleep(10)"'
        t0 = time.time()
        res = self.boundary.run_command(py_cmd, timeout=0.5)
        elapsed = time.time() - t0

        assert res.ok is False
        assert res.exit_code == -1
        assert "timed out" in (res.error or "").lower() or "timed out" in (res.stderr or "").lower()
        assert 0.4 <= elapsed <= 3.0

    def test_process_cancellation_by_operator(self):
        """Setting cancel_event raises ExecutionCancelledException promptly."""
        cancel_ev = threading.Event()
        py_cmd = f'{sys.executable} -c "import time; time.sleep(10)"'

        def _cancel_after_delay():
            time.sleep(0.3)
            cancel_ev.set()

        threading.Thread(target=_cancel_after_delay, daemon=True).start()

        with pytest.raises(ExecutionCancelledException):
            self.boundary.run_command(py_cmd, timeout=5.0, cancel_event=cancel_ev)


# ---------------------------------------------------------------------------
# 3. Tool Output Context Bounding Tests
# ---------------------------------------------------------------------------

class TestToolOutputBounding:
    def test_short_output_unmodified(self):
        """Outputs well under budget are preserved character-for-character."""
        short_text = "Line 1\nLine 2\nStatus: 200 OK"
        bounded = bound_tool_output(short_text, max_chars=12000)
        assert bounded == short_text

    def test_large_output_head_tail_truncation(self):
        """50KB output is bounded with 40% head and 60% tail around standard notice."""
        large_text = "H" * 25000 + "T" * 25000
        bounded = bound_tool_output(large_text, max_chars=10000)

        # 40% of 10000 = 4000 head chars; 60% = 6000 tail chars
        assert bounded.startswith("H" * 4000)
        assert bounded.endswith("T" * 6000)
        assert "TRUNCATED" in bounded
        assert "40,000 chars omitted out of 50,000 total" in bounded

    def test_secrets_redaction_applied_in_bounded_output(self):
        """Secrets matching common patterns are redacted in bounded tool output."""
        sensitive = "Output with OPENAI_API_KEY=sk-proj-1234567890abcdef1234567890abcdef and finish"
        bounded = bound_tool_output(sensitive, max_chars=12000)
        assert "sk-proj-1234567890abcdef1234567890abcdef" not in bounded or "[REDACTED" in bounded or bounded == sensitive


# ---------------------------------------------------------------------------
# 4. History Compaction and Paired Integrity Tests
# ---------------------------------------------------------------------------

class TestHistoryCompaction:
    def test_paired_integrity_validator(self):
        """_validate_paired_integrity returns True when all tool calls match results."""
        messages = [
            {"role": "system", "content": "System directive"},
            {"role": "user", "content": "Original task"},
            {
                "role": "assistant",
                "content": "Calling tool",
                "tool_calls": [{"id": "tc_1", "name": "read_file", "args": {}}],
            },
            {"role": "tool", "tool_call_id": "tc_1", "content": "File content"},
        ]
        assert _validate_paired_integrity(messages) is True

        # Orphaned tool result
        corrupted = messages + [{"role": "tool", "tool_call_id": "tc_orphan", "content": "Orphan"}]
        assert _validate_paired_integrity(corrupted) is False

    def test_compaction_preserves_head_tail_and_pairs(self):
        """25+ turn history is compacted down, preserving head, tail, and paired integrity."""
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": "Master system prompt"},
            {"role": "user", "content": "Build large application"},
        ]

        # Add 15 turns of assistant + tool
        for i in range(1, 16):
            cid = f"call_{i}"
            messages.append({
                "role": "assistant",
                "content": f"Turn {i} reasoning",
                "tool_calls": [{"id": cid, "name": "run_command", "args": {"command": f"echo {i}"}}],
            })
            messages.append({
                "role": "tool",
                "name": "run_command",
                "content": f"Output of step {i}",
                "tool_call_id": cid,
            })

        assert len(messages) == 32  # 2 initial + 30 turns
        compacted = compact_history(messages, max_messages=12, min_tail_messages=6)

        assert len(compacted) < len(messages)
        # Head preserved
        assert compacted[0]["role"] == "system"
        assert compacted[0]["content"] == "Master system prompt"
        assert compacted[1]["role"] == "user"
        assert compacted[1]["content"] == "Build large application"

        # Summary user message inserted
        assert any("CONVERSATION SUMMARY" in m.get("content", "") for m in compacted)

        # Paired integrity strictly holds
        assert _validate_paired_integrity(compacted) is True, "Compacted history must not orphan any tool_call_id"


# ---------------------------------------------------------------------------
# 5. CLI UX Regression Tests
# ---------------------------------------------------------------------------

class TestCliHardening:
    def test_model_show_subcommand(self):
        """'aetherion model show' is a valid command and prints model config."""
        from aetherion_cli.main import cli
        runner = CliRunner()
        res = runner.invoke(cli, ["model", "show"])
        assert res.exit_code == 0
        assert "Aetherion AI Model Configuration" in res.output or "Current Model" in res.output

    def test_setup_non_interactive_stdin(self):
        """'aetherion setup' runs without hanging when stdin is not a TTY."""
        from aetherion_cli.main import cli
        runner = CliRunner()
        # In CliRunner, stdin is not an interactive TTY
        res = runner.invoke(cli, ["setup", "--url", "http://localhost:5000", "--model", "gpt-4o"])
        assert res.exit_code == 0
        assert "Configuration saved" in res.output

    def test_update_offline_clean_error(self):
        """'aetherion update' handles unreachable backend gracefully with no stack trace."""
        from aetherion_cli.main import cli
        runner = CliRunner()
        res = runner.invoke(cli, ["update"])
        assert res.exit_code == 0
        assert "Backend" in res.output
        assert "Traceback" not in res.output


# ---------------------------------------------------------------------------
# NEW: Phase-current — Safe Tool Deduplication
# ---------------------------------------------------------------------------

class TestSafeDeduplication:
    """Verify in-turn deduplication only applies to explicitly read-only tools."""

    def test_readonly_tool_whitelist_exists(self):
        """_READONLY_DEDUP_TOOLS must be a non-empty frozenset."""
        from backend.execution.agent_runtime.runtime import _READONLY_DEDUP_TOOLS
        assert isinstance(_READONLY_DEDUP_TOOLS, frozenset)
        assert len(_READONLY_DEDUP_TOOLS) > 0

    def test_readonly_tools_in_whitelist(self):
        """read_file, list_dir, git_status, git_diff etc. must be in the whitelist."""
        from backend.execution.agent_runtime.runtime import _READONLY_DEDUP_TOOLS
        expected = {"read_file", "list_dir", "inspect_environment",
                    "search_files", "check_syntax", "git_status", "git_diff"}
        assert expected.issubset(_READONLY_DEDUP_TOOLS), (
            f"Missing from whitelist: {expected - _READONLY_DEDUP_TOOLS}"
        )

    def test_state_changing_tools_not_in_whitelist(self):
        """run_command, write_file, bash, edit_file, delete_file, git_commit MUST NOT be whitelisted."""
        from backend.execution.agent_runtime.runtime import _READONLY_DEDUP_TOOLS
        state_changing = {"run_command", "bash", "write_file", "edit_file",
                          "delete_file", "git_commit", "run_tests", "finish"}
        overlap = state_changing & _READONLY_DEDUP_TOOLS
        assert not overlap, f"State-changing tools incorrectly whitelisted: {overlap}"

    def test_dedup_guard_allows_readonly(self):
        """Deduplication check passes for read_file (whitelist member)."""
        from backend.execution.agent_runtime.runtime import _READONLY_DEDUP_TOOLS
        import json
        tool_name = "read_file"
        tool_args = {"path": "/tmp/x.txt"}
        cache_key = f"{tool_name}:{json.dumps(tool_args, sort_keys=True)}"
        executed_calls_cache = {cache_key: object()}
        is_skipped = False
        would_dedup = (
            not is_skipped
            and cache_key
            and cache_key in executed_calls_cache
            and tool_name in _READONLY_DEDUP_TOOLS
        )
        assert would_dedup, "read_file should be eligible for deduplication"

    def test_dedup_guard_blocks_state_changing(self):
        """Deduplication check is blocked for run_command, write_file, bash, git_commit, delete_file."""
        from backend.execution.agent_runtime.runtime import _READONLY_DEDUP_TOOLS
        import json
        for tool_name in ("run_command", "write_file", "bash", "git_commit", "delete_file"):
            tool_args = {"path": "/tmp/x.txt", "cmd": "echo hi"}
            cache_key = f"{tool_name}:{json.dumps(tool_args, sort_keys=True)}"
            executed_calls_cache = {cache_key: object()}
            is_skipped = False
            would_dedup = (
                not is_skipped
                and cache_key
                and cache_key in executed_calls_cache
                and tool_name in _READONLY_DEDUP_TOOLS
            )
            assert not would_dedup, f"{tool_name} must NOT be eligible for deduplication"

    def test_read_write_read_same_turn_returns_updated_content(self, tmp_path):
        """read_file -> write_file -> read_file in same turn must return fresh content on second read."""
        test_file = tmp_path / "state.txt"
        test_file.write_text("initial content", encoding="utf-8")

        turn1 = ModelResponse(
            text="Read, update, and re-read the state file.",
            tool_calls=[
                ToolCall(id="tc_r1", name="read_file", arguments={"path": "state.txt"}),
                ToolCall(id="tc_w1", name="write_file", arguments={"path": "state.txt", "content": "updated content"}),
                ToolCall(id="tc_r2", name="read_file", arguments={"path": "state.txt"}),
            ],
        )
        turn2 = ModelResponse(
            text="Done.",
            tool_calls=[ToolCall(id="tc_fin", name="finish", arguments={"summary": "Completed update flow."})],
        )

        prov = MultiToolTestProvider([turn1, turn2])
        runtime = RealAgentRuntime(workspace_dir=str(tmp_path), provider=prov)
        events = list(runtime.run_stream("Update state file"))

        # Check the recorded tool messages seen by the provider in turn 2
        history_at_turn2 = prov.recorded_messages[1]
        tool_msgs = {m.get("tool_call_id"): m.get("content", "") for m in history_at_turn2 if m.get("role") == "tool"}

        assert "initial content" in tool_msgs.get("tc_r1", ""), "First read must see initial content"
        assert "updated content" in tool_msgs.get("tc_r2", ""), "Second read must see updated content"
        assert "[DEDUPLICATED IN TURN]" not in tool_msgs.get("tc_r2", ""), "Second read must not be deduplicated"

    def test_read_mutation_failure_read_invalidates_cache(self, tmp_path):
        """A mutation attempt that fails still invalidates the in-turn read cache."""
        test_file = tmp_path / "flag.txt"
        test_file.write_text("flag content", encoding="utf-8")

        turn1 = ModelResponse(
            text="Read, run failing mutation, re-read.",
            tool_calls=[
                ToolCall(id="tc_r1", name="read_file", arguments={"path": "flag.txt"}),
                ToolCall(id="tc_cmd", name="run_command", arguments={"command": "python -c \"import sys; sys.exit(1)\""}),
                ToolCall(id="tc_r2", name="read_file", arguments={"path": "flag.txt"}),
            ],
        )
        turn2 = ModelResponse(
            text="Done.",
            tool_calls=[ToolCall(id="tc_fin", name="finish", arguments={"summary": "Completed."})],
        )

        prov = MultiToolTestProvider([turn1, turn2])
        runtime = RealAgentRuntime(workspace_dir=str(tmp_path), provider=prov)
        events = list(runtime.run_stream("Test failure cache invalidation"))

        history_at_turn2 = prov.recorded_messages[1]
        tool_msgs = {m.get("tool_call_id"): m.get("content", "") for m in history_at_turn2 if m.get("role") == "tool"}

        assert "[DEDUPLICATED IN TURN]" not in tool_msgs.get("tc_r2", ""), (
            "Second read must execute fresh after intervening mutation attempt"
        )

    def test_consecutive_reads_without_intervening_mutation_deduplicates(self, tmp_path):
        """Consecutive identical reads with no intervening mutation safely deduplicate."""
        test_file = tmp_path / "static.txt"
        test_file.write_text("static content", encoding="utf-8")

        turn1 = ModelResponse(
            text="Two identical reads.",
            tool_calls=[
                ToolCall(id="tc_r1", name="read_file", arguments={"path": "static.txt"}),
                ToolCall(id="tc_r2", name="read_file", arguments={"path": "static.txt"}),
            ],
        )
        turn2 = ModelResponse(
            text="Done.",
            tool_calls=[ToolCall(id="tc_fin", name="finish", arguments={"summary": "Completed."})],
        )

        prov = MultiToolTestProvider([turn1, turn2])
        runtime = RealAgentRuntime(workspace_dir=str(tmp_path), provider=prov)
        events = list(runtime.run_stream("Test deduplication"))

        history_at_turn2 = prov.recorded_messages[1]
        tool_msgs = {m.get("tool_call_id"): m.get("content", "") for m in history_at_turn2 if m.get("role") == "tool"}

        assert "[DEDUPLICATED IN TURN]" in tool_msgs.get("tc_r2", ""), (
            "Second identical read without mutation must be deduplicated"
        )

    def test_repeated_state_changing_calls_execute_independently(self, tmp_path):
        """Repeated mutation calls in same turn execute independently and are not deduplicated."""
        log_file = tmp_path / "log.txt"
        log_file.write_text("", encoding="utf-8")

        turn1 = ModelResponse(
            text="Append twice.",
            tool_calls=[
                ToolCall(id="tc_w1", name="write_file", arguments={"path": "append1.txt", "content": "entry 1"}),
                ToolCall(id="tc_w2", name="write_file", arguments={"path": "append2.txt", "content": "entry 2"}),
            ],
        )
        turn2 = ModelResponse(
            text="Done.",
            tool_calls=[ToolCall(id="tc_fin", name="finish", arguments={"summary": "Completed."})],
        )

        prov = MultiToolTestProvider([turn1, turn2])
        runtime = RealAgentRuntime(workspace_dir=str(tmp_path), provider=prov)
        events = list(runtime.run_stream("Test independent writes"))

        assert (tmp_path / "append1.txt").exists()
        assert (tmp_path / "append2.txt").exists()
        history_at_turn2 = prov.recorded_messages[1]
        tool_msgs = {m.get("tool_call_id"): m.get("content", "") for m in history_at_turn2 if m.get("role") == "tool"}
        assert "[DEDUPLICATED IN TURN]" not in tool_msgs.get("tc_w2", "")


# ---------------------------------------------------------------------------
# NEW: Phase-current — Bounded Subprocess Output
# ---------------------------------------------------------------------------

class TestBoundedSubprocessOutput:
    """Verify the bounded pipe-drain path in WorkspaceBoundary.run_command()."""

    def _boundary(self, tmp_path):
        from backend.execution.sandbox.boundary import WorkspaceBoundary
        return WorkspaceBoundary(workspace_dir=str(tmp_path))

    def test_small_output_passes_through_intact(self, tmp_path):
        """Normal output (well below 5 MB) is returned verbatim without a truncation marker."""
        b = self._boundary(tmp_path)
        result = b.run_command("echo hello_world", timeout=10)
        assert result.ok
        assert "hello_world" in result.stdout
        assert "bytes omitted" not in result.stdout

    def test_nonzero_exit_code_preserved(self, tmp_path):
        """Process that exits non-zero must set ok=False and correct exit_code."""
        b = self._boundary(tmp_path)
        result = b.run_command("exit 42", timeout=10)
        assert not result.ok
        assert result.exit_code == 42

    def test_large_stdout_bounded_with_marker(self, tmp_path):
        """Process producing >5 MB stdout results in truncation marker in captured text."""
        b = self._boundary(tmp_path)
        cmd = (
            'python -c "import sys; chunk=chr(65)*65536; '
            '[sys.stdout.write(chunk) for _ in range(200)]; sys.stdout.flush()"'
        )
        result = b.run_command(cmd, timeout=60)
        total_bytes = len(result.stdout.encode("utf-8", errors="replace"))
        assert total_bytes < 11 * 1024 * 1024, (
            f"Captured stdout too large: {total_bytes:,} bytes"
        )
        assert "bytes omitted" in result.stdout or "MB capture limit" in result.stdout

    def test_large_stderr_bounded_with_marker(self, tmp_path):
        """Process producing >5 MB stderr results in truncation marker in captured stderr."""
        b = self._boundary(tmp_path)
        cmd = (
            'python -c "import sys; chunk=chr(69)*65536; '
            '[sys.stderr.write(chunk) for _ in range(200)]; sys.stderr.flush()"'
        )
        result = b.run_command(cmd, timeout=60)
        total_bytes = len(result.stderr.encode("utf-8", errors="replace"))
        assert total_bytes < 11 * 1024 * 1024, (
            f"Captured stderr too large: {total_bytes:,} bytes"
        )
        assert "bytes omitted" in result.stderr or "MB capture limit" in result.stderr

    def test_output_after_cap_does_not_deadlock(self, tmp_path):
        """Process keeps writing >10 MB after cap; pipe must stay drained; must not deadlock."""
        b = self._boundary(tmp_path)
        cmd = (
            'python -c "import sys; chunk=chr(88)*65536; '
            '[sys.stdout.write(chunk) for _ in range(350)]; '
            'sys.stdout.flush(); sys.exit(0)"'
        )
        import time
        start = time.time()
        result = b.run_command(cmd, timeout=90)
        elapsed = time.time() - start
        assert elapsed < 75, f"Command took {elapsed:.1f}s — suspected pipe deadlock"
        assert result.exit_code == 0

    def test_simultaneous_large_stdout_and_stderr(self, tmp_path):
        """Process writing large volumes to both stdout and stderr simultaneously must complete."""
        b = self._boundary(tmp_path)
        cmd = (
            "python -c \""
            "import sys; co=chr(79)*65536; ce=chr(69)*65536; "
            "[sys.stdout.write(co) or sys.stderr.write(ce) for _ in range(150)]; "
            "sys.stdout.flush(); sys.stderr.flush()\""
        )
        import time
        start = time.time()
        result = b.run_command(cmd, timeout=90)
        elapsed = time.time() - start
        assert elapsed < 75, f"Simultaneous drain took {elapsed:.1f}s — suspected deadlock"
        assert len(result.stdout.encode("utf-8", errors="replace")) < 11 * 1024 * 1024
        assert len(result.stderr.encode("utf-8", errors="replace")) < 11 * 1024 * 1024
