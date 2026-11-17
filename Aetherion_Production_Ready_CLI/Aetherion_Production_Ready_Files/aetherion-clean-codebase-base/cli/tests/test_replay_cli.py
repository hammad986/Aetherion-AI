"""
tests/test_replay_cli.py
Tests for the `aetherion replay` command group (Phase 7.3).

Covers the seven read commands, both output formats, the auth guard, and the
error paths. The mocked client returns the shapes `routes/replay_bp.py` actually
returns — a fixture that drifts from the blueprint would make these tests pass
while the CLI is broken, so the response bodies here are kept deliberately close
to the real ones.

Two behaviours are worth more than their line count:

  * `replay export` writes a file in table mode but *not* in `-o json` mode,
    because piping to jq should not also litter the working directory.
  * `replay export` sanitizes the execution id before using it as a file name.
    The id comes from server data rather than a text field, but it still becomes
    a path component.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from aetherion_cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def mock_config(tmp_path, monkeypatch):
    """Redirect config to a temp dir so tests never touch a real one."""
    from aetherion_cli import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({
        "server_url": "http://testserver",
        "token": "test-token-abc123",
        "output_format": "table",
    }))
    monkeypatch.delenv("AETHERION_TOKEN", raising=False)
    monkeypatch.delenv("AETHERION_URL", raising=False)


_INTEGRATION = {"store": True, "events": True, "ledger": True}

_FRAME = {
    "seq": 2,
    "event_id": "evt_3",
    "event_type": "tool.completed",
    "kind": "tool",
    "timestamp": 1_700_000_002.0,
    "elapsed_ms": 2000,
    "session_id": "sess_1",
    "correlation_id": "corr_1",
    "payload": {"tool": "file_read", "api_key": "***"},
    "state": {"status": "running", "tools_called": 1, "files_touched": ["main.py"]},
}

_TIMELINE = {
    "ok": True,
    "execution_id": "exec_1",
    "session_id": "sess_1",
    "summary": {
        "event_count": 5,
        "frame_count": 5,
        "final_status": "succeeded",
        "ledger_status": "succeeded",
        "status_agrees": True,
        "terminal": True,
        "verifier_backed": True,
        "duration_ms": 4200,
        "tools_called": 1,
        "tools_completed": 1,
        "tools_failed": 0,
        "files_modified": 1,
        "commands_run": 0,
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "cost_usd": 0.0012,
    },
    "integration": _INTEGRATION,
    "frames": [_FRAME],
    "frame_count": 1,
    "total_frames": 5,
    "truncated": False,
}

_BUNDLE = {
    "ok": True,
    "bundle": {
        "schema_version": "replay/v1",
        "kind": "aetherion.replay.bundle",
        "execution_id": "exec_1",
        "session_id": "sess_1",
        "summary": {"event_count": 5, "final_status": "succeeded"},
        "integration": _INTEGRATION,
        "frames": [_FRAME],
        "frame_count": 1,
        "total_frames": 5,
        "transcript": "hello",
    },
}


def _client(get_returns=None, raise_on=None, exc=None):
    from aetherion_cli.client import AetherionClientError

    m = MagicMock()
    m.token = "test-token-abc123"
    m.base_url = "http://testserver"
    if raise_on == "get":
        m.get.side_effect = exc or AetherionClientError("test error")
    else:
        m.get.return_value = get_returns or {}
    return m


class TestReplayStatus:
    def test_status_reports_availability(self, runner):
        client = _client({
            "ok": True, "available": True, "integration": _INTEGRATION,
            "projection": "fold over ExecutionStore.event_log (append-only)",
            "persistence": "none",
        })
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "status"])
        assert result.exit_code == 0
        assert "True" in result.output

    def test_status_warns_on_dead_read_edges(self, runner):
        """A blank counter must be diagnosable from the response itself."""
        client = _client({
            "ok": True, "available": False,
            "integration": {"store": False, "events": True, "ledger": False},
        })
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "status"])
        assert result.exit_code == 0
        assert "dead read edges" in result.output
        assert "store" in result.output

    def test_status_json(self, runner):
        client = _client({"ok": True, "available": True, "integration": _INTEGRATION})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["-o", "json", "replay", "status"])
        assert result.exit_code == 0
        assert "available" in result.output

    def test_requires_auth(self, runner):
        client = _client({"ok": True})
        client.token = ""
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "status"])
        assert result.exit_code == 1
        assert "Not authenticated" in result.output


class TestReplayList:
    def test_list_renders_rows(self, runner):
        client = _client({
            "ok": True,
            "executions": [{
                "execution_id": "exec_abcdef123456",
                "session_id": "sess_1",
                "status": "succeeded",
                "event_count": 12,
                "terminal": True,
                "updated_at": 1_700_000_000.0,
            }],
            "count": 1,
        })
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "list", "-s", "sess_1"])
        assert result.exit_code == 0
        assert "exec_abcdef" in result.output
        assert "succeeded" in result.output

    def test_list_empty(self, runner):
        client = _client({"ok": True, "executions": [], "count": 0})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "list", "-s", "sess_1"])
        assert result.exit_code == 0
        assert "No replayable executions" in result.output

    def test_list_passes_session_scope(self, runner):
        """
        The API refuses an unscoped list for non-admins, so the flag must reach
        the query string rather than being dropped.
        """
        client = _client({"ok": True, "executions": [], "count": 0})
        with patch("aetherion_cli.main._get_client", return_value=client):
            runner.invoke(cli, ["replay", "list", "-s", "sess_xyz"])
        assert "session_id=sess_xyz" in client.get.call_args[0][0]

    def test_list_error_exits_1(self, runner):
        from aetherion_cli.client import AetherionClientError
        client = _client(raise_on="get", exc=AetherionClientError("boom", 500))
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "list", "-s", "s"])
        assert result.exit_code == 1


class TestReplayShow:
    def test_show_renders_summary_and_frames(self, runner):
        client = _client(_TIMELINE)
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "show", "exec_1"])
        assert result.exit_code == 0
        assert "succeeded" in result.output
        assert "tool.completed" in result.output

    def test_show_no_frames_flag_hides_the_table(self, runner):
        client = _client(_TIMELINE)
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "show", "exec_1", "--no-frames"])
        assert result.exit_code == 0
        assert "tool.completed" not in result.output

    def test_show_warns_when_replay_disagrees_with_ledger(self, runner):
        """
        status_agrees is a real defect signal — the fold and truth_kernel read
        the same rows, so a mismatch means precedence has drifted.
        """
        body = json.loads(json.dumps(_TIMELINE))
        body["summary"]["status_agrees"] = False
        body["summary"]["ledger_status"] = "failed"
        client = _client(body)
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "show", "exec_1"])
        assert result.exit_code == 0
        assert "disagrees" in result.output

    def test_show_passes_kinds_filter(self, runner):
        client = _client(_TIMELINE)
        with patch("aetherion_cli.main._get_client", return_value=client):
            runner.invoke(cli, ["replay", "show", "exec_1", "--kinds", "tool,filesystem"])
        assert "kinds=" in client.get.call_args[0][0]

    def test_show_json(self, runner):
        client = _client(_TIMELINE)
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["-o", "json", "replay", "show", "exec_1"])
        assert result.exit_code == 0
        assert "execution_id" in result.output


class TestReplayFrame:
    def test_frame_renders_state_and_payload(self, runner):
        client = _client({"ok": True, "frame": _FRAME})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "frame", "exec_1", "2"])
        assert result.exit_code == 0
        assert "tool.completed" in result.output
        assert "main.py" in result.output

    def test_frame_missing(self, runner):
        client = _client({"ok": True, "frame": {}})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "frame", "exec_1", "99"])
        assert result.exit_code == 0
        assert "No frame at seq 99" in result.output

    def test_frame_requires_an_integer_seq(self, runner):
        client = _client({"ok": True, "frame": _FRAME})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "frame", "exec_1", "not-a-number"])
        assert result.exit_code != 0


class TestReplaySeek:
    def test_seek_defaults_to_final_frame(self, runner):
        client = _client({
            "ok": True, "found": True, "seq": 4, "event_type": "task.completed",
            "elapsed_ms": 4200, "state": {"status": "succeeded"},
            "transcript": "", "integration": _INTEGRATION,
        })
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "seek", "exec_1"])
        assert result.exit_code == 0
        assert "succeeded" in result.output
        # No addressing args ⇒ no query string.
        assert "seek" in client.get.call_args[0][0]

    def test_seek_by_seq_wins_over_timestamp(self, runner):
        """seq is exact; a timestamp can tie inside a millisecond."""
        client = _client({"ok": True, "found": True, "seq": 1, "state": {}})
        with patch("aetherion_cli.main._get_client", return_value=client):
            runner.invoke(cli, ["replay", "seek", "exec_1", "--seq", "1",
                                "--timestamp", "1700000009"])
        url = client.get.call_args[0][0]
        assert "seq=1" in url
        assert "timestamp" not in url

    def test_seek_by_timestamp(self, runner):
        client = _client({"ok": True, "found": True, "seq": 2, "state": {}})
        with patch("aetherion_cli.main._get_client", return_value=client):
            runner.invoke(cli, ["replay", "seek", "exec_1", "--timestamp", "1700000002"])
        assert "timestamp=1700000002" in client.get.call_args[0][0]

    def test_seek_transcript_flag(self, runner):
        client = _client({
            "ok": True, "found": True, "seq": 3, "state": {},
            "transcript": "partial output here",
        })
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "seek", "exec_1", "--transcript"])
        assert "partial output here" in result.output


class TestReplayDiff:
    def test_diff_renders_deltas(self, runner):
        client = _client({
            "ok": True, "found": True, "from_seq": 0, "to_seq": 4,
            "from_status": "running", "to_status": "succeeded",
            "status_changed": True, "elapsed_ms": 4200, "events_between": 4,
            "changes": {"tools_called": 1, "files_modified": 1},
            "files_added": ["main.py"], "tools_added": ["file_read"],
            "integration": _INTEGRATION,
        })
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "diff", "exec_1", "--from", "0", "--to", "4"])
        assert result.exit_code == 0
        assert "tools_called" in result.output
        assert "main.py" in result.output

    def test_diff_not_found(self, runner):
        client = _client({"ok": True, "found": False, "changes": {}})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "diff", "exec_1"])
        assert result.exit_code == 0
        assert "Nothing to diff" in result.output

    def test_diff_defaults_omit_to(self, runner):
        """Omitting --to lets the API default to the last frame."""
        client = _client({"ok": True, "found": True, "from_seq": 0, "to_seq": 9,
                          "changes": {}, "integration": _INTEGRATION})
        with patch("aetherion_cli.main._get_client", return_value=client):
            runner.invoke(cli, ["replay", "diff", "exec_1"])
        url = client.get.call_args[0][0]
        assert "from=0" in url
        assert "to=" not in url


class TestReplayExport:
    def test_export_writes_a_file(self, runner):
        client = _client(_BUNDLE)
        with runner.isolated_filesystem():
            with patch("aetherion_cli.main._get_client", return_value=client):
                result = runner.invoke(cli, ["replay", "export", "exec_1"])
            assert result.exit_code == 0
            with open("replay-exec_1.json", encoding="utf-8") as fh:
                written = json.load(fh)
        assert written["kind"] == "aetherion.replay.bundle"
        assert written["execution_id"] == "exec_1"

    def test_export_honours_output_path(self, runner):
        client = _client(_BUNDLE)
        with runner.isolated_filesystem():
            with patch("aetherion_cli.main._get_client", return_value=client):
                result = runner.invoke(cli, ["replay", "export", "exec_1", "-f", "custom.json"])
            assert result.exit_code == 0
            with open("custom.json", encoding="utf-8") as fh:
                assert json.load(fh)["execution_id"] == "exec_1"

    def test_export_json_mode_writes_no_file(self, runner):
        """
        `-o json` means "give me the bundle on stdout". Piping to jq should not
        also litter the working directory.
        """
        import os
        client = _client(_BUNDLE)
        with runner.isolated_filesystem():
            with patch("aetherion_cli.main._get_client", return_value=client):
                result = runner.invoke(cli, ["-o", "json", "replay", "export", "exec_1"])
            assert result.exit_code == 0
            assert not os.path.exists("replay-exec_1.json")

    def test_export_sanitizes_the_filename(self, runner):
        """
        The id becomes a path component. A traversal-shaped id must degrade to a
        harmless name rather than escaping the working directory.
        """
        import os
        body = json.loads(json.dumps(_BUNDLE))
        body["bundle"]["execution_id"] = "../../etc/passwd"
        client = _client(body)
        with runner.isolated_filesystem():
            with patch("aetherion_cli.main._get_client", return_value=client):
                result = runner.invoke(cli, ["replay", "export", "../../etc/passwd"])
            assert result.exit_code == 0
            written = os.listdir(".")
        assert len(written) == 1
        assert ".." not in written[0]
        assert "/" not in written[0]

    def test_export_no_frames_flag(self, runner):
        client = _client(_BUNDLE)
        with runner.isolated_filesystem():
            with patch("aetherion_cli.main._get_client", return_value=client):
                runner.invoke(cli, ["replay", "export", "exec_1", "--no-frames"])
        assert "frames=false" in client.get.call_args[0][0]

    def test_export_error_exits_1(self, runner):
        from aetherion_cli.client import AetherionClientError
        client = _client(raise_on="get", exc=AetherionClientError("nope", 404))
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["replay", "export", "exec_1"])
        assert result.exit_code == 1


class TestReplayGroupWiring:
    def test_group_is_registered(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert "replay" in result.output

    def test_all_seven_commands_are_reachable(self, runner):
        result = runner.invoke(cli, ["replay", "--help"])
        for cmd in ("status", "list", "show", "frame", "seek", "diff", "export"):
            assert cmd in result.output, cmd
