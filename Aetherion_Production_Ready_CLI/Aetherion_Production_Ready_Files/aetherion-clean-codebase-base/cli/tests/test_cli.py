"""
tests/test_cli.py
Automated tests for the Aetherion CLI.

Covers:
- All command groups (auth, agent, workspace, history, memory, plugins, providers, config, doctor)
- Auth error handling
- Offline error handling
- Exit codes
- Output formats (table, json)
- Streaming (basic smoke test with mocked response)
"""
from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

# We patch the config so tests never read/write real config files
from aetherion_cli.main import cli


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def mock_config(tmp_path, monkeypatch):
    """Redirect config file to a temp directory."""
    from aetherion_cli import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.json")
    # Pre-write a config with a fake token
    cfg_data = {
        "server_url": "http://testserver",
        "token": "test-token-abc123",
        "model": "test-model",
        "output_format": "table",
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg_data))
    # Ensure env vars don't bleed in
    monkeypatch.delenv("AETHERION_TOKEN", raising=False)
    monkeypatch.delenv("AETHERION_URL", raising=False)
    monkeypatch.delenv("AETHERION_MODEL", raising=False)


def _make_client(
    get_returns: dict | None = None,
    post_returns: dict | None = None,
    delete_returns: dict | None = None,
    health_returns: dict | None = None,
    stream_events: list | None = None,
    raise_on: str | None = None,
    exc: Exception | None = None,
):
    """Build a mock Client instance."""
    from aetherion_cli.client import AetherionClientError, AetherionOfflineError

    m = MagicMock()
    m.token = "test-token-abc123"
    m.base_url = "http://testserver"

    if raise_on == "get":
        m.get.side_effect = exc or AetherionClientError("test error")
    else:
        m.get.return_value = get_returns or {}

    if raise_on == "post":
        m.post.side_effect = exc or AetherionClientError("test error")
    else:
        m.post.return_value = post_returns or {"ok": True}

    m.delete.return_value = delete_returns or {"ok": True}
    m.health.return_value = health_returns or {"status": "ok", "version": "1.0.0"}

    if stream_events is not None:
        m.stream.return_value = iter(stream_events)
    else:
        m.stream.return_value = iter([
            {"type": "text", "text": "Hello from agent"},
            {"type": "done", "status": "done"},
        ])

    return m


# ---------------------------------------------------------------------------
# AUTH: login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_login_success(self, runner):
        client = _make_client(post_returns={"access_token": "new-token-xyz"})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["login", "--email", "test@test.com", "--password", "secret"])
        assert result.exit_code == 0
        assert "Signed in" in result.output

    def test_login_no_token_in_response(self, runner):
        client = _make_client(post_returns={"ok": True})  # no token field
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["login", "--email", "x@x.com", "--password", "pw"])
        assert result.exit_code == 1

    def test_login_server_error(self, runner):
        from aetherion_cli.client import AetherionClientError
        client = _make_client(raise_on="post", exc=AetherionClientError("Invalid credentials", 401))
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["login", "--email", "bad@bad.com", "--password", "bad"])
        assert result.exit_code == 1

    def test_login_offline(self, runner):
        from aetherion_cli.client import AetherionOfflineError
        client = _make_client(raise_on="post", exc=AetherionOfflineError("connection refused"))
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["login", "--email", "x@x.com", "--password", "pw"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# AUTH: logout
# ---------------------------------------------------------------------------

class TestLogout:
    def test_logout_success(self, runner):
        client = _make_client()
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["logout"])
        assert result.exit_code == 0
        assert "Signed out" in result.output

    def test_logout_ok_even_if_server_down(self, runner):
        from aetherion_cli.client import AetherionOfflineError
        client = _make_client(raise_on="post", exc=AetherionOfflineError("offline"))
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["logout"])
        # Should still exit 0 (best-effort server logout)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# AUTH: whoami
# ---------------------------------------------------------------------------

class TestWhoami:
    def test_whoami_success(self, runner):
        client = _make_client(get_returns={"email": "me@test.com", "role": "admin", "id": "u123"})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["whoami"])
        assert result.exit_code == 0
        assert "me@test.com" in result.output

    def test_whoami_not_authenticated(self, runner):
        client = _make_client()
        client.token = None
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["whoami"])
        assert result.exit_code == 1

    def test_whoami_json_output(self, runner):
        data = {"email": "me@test.com", "role": "user", "id": "u99"}
        client = _make_client(get_returns=data)
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["-o", "json", "whoami"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["email"] == "me@test.com"


# ---------------------------------------------------------------------------
# DOCTOR
# ---------------------------------------------------------------------------

class TestDoctor:
    def test_doctor_healthy(self, runner):
        client = _make_client(
            health_returns={"status": "ok", "version": "1.0.0"},
            get_returns={"email": "me@test.com"},
        )
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 0
        assert "reachable" in result.output

    def test_doctor_offline(self, runner):
        client = _make_client(health_returns={"ok": False, "error": "connection refused", "offline": True})
        # get() will raise if doctor tries to check auth after health fails — that's fine
        client.get.side_effect = Exception("offline")
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 1

    def test_doctor_no_token(self, runner):
        client = _make_client(health_returns={"status": "ok", "version": "1.0.0"})
        client.token = None
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["doctor"])
        assert "No token" in result.output or "login" in result.output.lower()


# ---------------------------------------------------------------------------
# CHAT / RUN
# ---------------------------------------------------------------------------

class TestChat:
    def test_chat_streams_output(self, runner):
        client = _make_client(stream_events=[
            {"type": "text", "text": "Building your app..."},
            {"type": "done", "status": "done"},
        ])
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["chat", "Build a REST API"])
        assert result.exit_code == 0
        assert "Building your app" in result.output

    def test_chat_requires_auth(self, runner):
        client = _make_client()
        client.token = None
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["chat", "do something"])
        assert result.exit_code == 1

    def test_chat_agent_error_event(self, runner):
        client = _make_client(stream_events=[
            {"type": "error", "error": "LLM quota exceeded"},
        ])
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["chat", "fail task"])
        assert result.exit_code == 1

    def test_run_no_task_exits_1(self, runner):
        client = _make_client()
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["run"])
        assert result.exit_code == 1

    def test_run_streams_output(self, runner):
        client = _make_client(stream_events=[
            {"type": "text", "text": "Running..."},
            {"type": "done", "status": "done"},
        ])
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["run", "test task"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# WORKSPACE
# ---------------------------------------------------------------------------

class TestWorkspace:
    def test_list_workspaces(self, runner):
        sessions = [
            {"id": "ws-1", "title": "Project Alpha", "status": "done", "created_at": 1700000000},
            {"id": "ws-2", "title": "Project Beta", "status": "running", "created_at": 1700000100},
        ]
        client = _make_client(get_returns={"sessions": sessions})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["workspace", "list"])
        assert result.exit_code == 0
        assert "ws-1" in result.output  # ID is always shown; title may be ellided at narrow widths

    def test_list_workspaces_empty(self, runner):
        client = _make_client(get_returns={"sessions": []})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["workspace", "list"])
        assert result.exit_code == 0
        assert "No workspaces" in result.output

    def test_create_workspace(self, runner):
        client = _make_client(post_returns={"id": "ws-new-123", "title": "My App"})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["workspace", "create", "My App"])
        assert result.exit_code == 0
        assert "created" in result.output.lower()

    def test_delete_workspace_with_yes(self, runner):
        client = _make_client()
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["workspace", "delete", "ws-123", "--yes"])
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()

    def test_list_json_output(self, runner):
        sessions = [{"id": "ws-1", "title": "Alpha", "status": "done"}]
        client = _make_client(get_returns={"sessions": sessions})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["-o", "json", "workspace", "list"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert parsed[0]["id"] == "ws-1"


# ---------------------------------------------------------------------------
# HISTORY
# ---------------------------------------------------------------------------

class TestHistory:
    def test_history_list(self, runner):
        sessions = [{"id": "s-1", "title": "Build API", "status": "done", "created_at": 1700000000}]
        client = _make_client(get_returns={"sessions": sessions})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["history", "list"])
        assert result.exit_code == 0
        assert "Build API" in result.output

    def test_history_show(self, runner):
        session = {
            "id": "s-abc",
            "title": "Test Task",
            "status": "done",
            "created_at": 1700000000,
            "result": "Build succeeded",
        }
        client = _make_client(get_returns=session)
        # The second get() call (for logs) can fail silently
        client.get.side_effect = [session, Exception("no logs")]
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["history", "show", "s-abc"])
        assert result.exit_code == 0
        assert "Test Task" in result.output


# ---------------------------------------------------------------------------
# MEMORY
# ---------------------------------------------------------------------------

class TestMemory:
    def test_memory_search(self, runner):
        results = [{"content": "FastAPI pattern", "score": 0.92, "created_at": 1700000000}]
        client = _make_client(get_returns={"results": results})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["memory", "search", "fastapi"])
        assert result.exit_code == 0
        assert "FastAPI" in result.output

    def test_memory_search_empty(self, runner):
        client = _make_client(get_returns={"results": []})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["memory", "search", "xyz"])
        assert result.exit_code == 0
        assert "No results" in result.output

    def test_memory_remember(self, runner):
        client = _make_client(post_returns={"ok": True})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["memory", "remember", "PostgreSQL is on port 5432"])
        assert result.exit_code == 0
        assert "Stored" in result.output

    def test_memory_clear_with_yes(self, runner):
        client = _make_client(post_returns={"ok": True})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["memory", "clear", "--yes"])
        assert result.exit_code == 0
        assert "cleared" in result.output.lower()

    def test_memory_list(self, runner):
        items = [{"id": "m1", "content": "Some fact", "importance": 0.8}]
        client = _make_client(get_returns={"memories": items})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["memory", "list"])
        assert result.exit_code == 0
        assert "Some fact" in result.output


# ---------------------------------------------------------------------------
# PLUGINS
# ---------------------------------------------------------------------------

class TestPlugins:
    def test_plugins_list(self, runner):
        items = [
            {"id": "github-tools", "name": "GitHub Tools", "category": "devops",
             "description": "GitHub integration", "installed": False},
        ]
        client = _make_client(get_returns={"plugins": items})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["plugins", "list"])
        assert result.exit_code == 0
        assert "GitHub Tools" in result.output

    def test_plugins_install(self, runner):
        client = _make_client(post_returns={"ok": True})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["plugins", "install", "github-tools"])
        assert result.exit_code == 0
        assert "installed" in result.output.lower()

    def test_plugins_uninstall_with_yes(self, runner):
        client = _make_client(post_returns={"ok": True})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["plugins", "uninstall", "github-tools", "--yes"])
        assert result.exit_code == 0
        assert "uninstalled" in result.output.lower()

    def test_plugins_info_not_found(self, runner):
        client = _make_client(get_returns={"plugins": []})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["plugins", "info", "nonexistent"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# PROVIDERS
# ---------------------------------------------------------------------------

class TestProviders:
    def test_providers_list(self, runner):
        prov = {
            "openai-gpt4o": {"provider": "openai", "model": "gpt-4o", "available": True, "quality": 5, "cost": 3},
        }
        client = _make_client(get_returns={"providers": prov})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["providers", "list"])
        assert result.exit_code == 0
        assert "openai" in result.output

    def test_providers_test_success(self, runner):
        client = _make_client(post_returns={
            "ok": True, "latency_ms": 250, "model": "gpt-4o", "response": "OK"
        })
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["providers", "test", "openai-gpt4o"])
        assert result.exit_code == 0
        assert "responding" in result.output

    def test_providers_test_failure(self, runner):
        client = _make_client(post_returns={"ok": False, "error": "API key missing"})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["providers", "test", "openai-gpt4o"])
        assert result.exit_code == 1

    def test_providers_add(self, runner):
        client = _make_client(post_returns={"ok": True, "provider": {"id": "prov_new_1", "name": "Custom Ollama"}})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["providers", "add", "--name", "Custom Ollama", "--url", "http://localhost:11434/v1"])
        assert result.exit_code == 0
        assert "Custom Ollama" in result.output

    def test_providers_remove(self, runner):
        client = _make_client(delete_returns={"ok": True, "deleted": "prov_new_1"})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["providers", "remove", "prov_new_1"])
        assert result.exit_code == 0
        assert "deleted" in result.output

    def test_providers_set_default(self, runner):
        client = _make_client(post_returns={"ok": True, "provider": {"id": "prov_new_1", "is_default": True}})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["providers", "set-default", "prov_new_1"])
        assert result.exit_code == 0
        assert "default" in result.output

    def test_providers_check(self, runner):
        client = _make_client(post_returns={"ok": True, "status": "CONNECTED", "latency_ms": 12.5, "diagnostics": "OK"})
        with patch("aetherion_cli.main._get_client", return_value=client):
            result = runner.invoke(cli, ["providers", "check", "prov_new_1"])
        assert result.exit_code == 0
        assert "CONNECTED" in result.output


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

class TestConfig:
    def test_config_show(self, runner):
        result = runner.invoke(cli, ["config", "--show"])
        assert result.exit_code == 0
        assert "server_url" in result.output.lower() or "Server URL" in result.output

    def test_config_set_url(self, runner):
        result = runner.invoke(cli, ["config", "--url", "https://api.aetherion.ai"])
        assert result.exit_code == 0
        assert "api.aetherion.ai" in result.output

    def test_config_set_model(self, runner):
        result = runner.invoke(cli, ["config", "--model", "claude-3-5-sonnet"])
        assert result.exit_code == 0
        assert "claude-3-5-sonnet" in result.output

    def test_config_json_output(self, runner):
        result = runner.invoke(cli, ["-o", "json", "config", "--show"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "server_url" in parsed
