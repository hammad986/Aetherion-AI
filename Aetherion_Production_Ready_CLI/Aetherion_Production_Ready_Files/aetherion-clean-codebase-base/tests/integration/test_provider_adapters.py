"""
tests/integration/test_provider_adapters.py
Verify AnthropicRuntimeProvider and OpenAIRuntimeProvider behavior,
tool schema structure, and provider error transitions.
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest
from unittest.mock import patch, MagicMock
from execution.agent_runtime import (
    AgentToolRegistry,
    AnthropicRuntimeProvider,
    OpenAIRuntimeProvider,
    ProviderUnavailableException,
    WorkspaceBoundary,
    get_provider,
)


def test_tool_schema_completeness(tmp_path):
    boundary = WorkspaceBoundary(tmp_path)
    tools = AgentToolRegistry(boundary)
    schema = tools.get_anthropic_tools()

    assert isinstance(schema, list)
    assert len(schema) >= 12

    names = {t["name"] for t in schema}
    expected = {
        "inspect_environment", "read_file", "write_file", "edit_file",
        "list_dir", "search_files", "delete_file", "run_command",
        "run_tests", "check_syntax", "git_status", "git_diff", "finish"
    }
    assert expected.issubset(names)

    # Check input_schema on each tool
    for t in schema:
        assert "input_schema" in t
        assert t["input_schema"]["type"] == "object"


def test_anthropic_provider_request_format():
    provider = AnthropicRuntimeProvider(
        api_key="test-key-mock",
        base_url="https://api.mock.anthropic.com",
        model="claude-3-5-sonnet-20241022",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "content": [
            {"type": "text", "text": "Writing code for hello.py"},
            {
                "type": "tool_use",
                "id": "toolu_01ABC",
                "name": "write_file",
                "input": {"path": "hello.py", "content": "print('hello')\n"},
            },
        ]
    }

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        res = provider.chat(
            messages=[
                {"role": "system", "content": "You are an agent."},
                {"role": "user", "content": "Create hello.py"},
            ],
            tools=[{"name": "write_file", "input_schema": {"type": "object"}}],
        )

        assert res["tool"] == "write_file"
        assert res["arguments"]["path"] == "hello.py"
        assert res["tool_call_id"] == "toolu_01ABC"
        assert "Writing code" in res["thought"]

        # Verify outgoing payload
        args, kwargs = mock_post.call_args
        assert kwargs["headers"]["x-api-key"] == "test-key-mock"
        payload = kwargs["json"]
        assert payload["model"] == "claude-3-5-sonnet-20241022"
        assert payload["tools"][0]["name"] == "write_file"


def test_anthropic_provider_401_error():
    provider = AnthropicRuntimeProvider(
        api_key="invalid-key",
        base_url="https://api.mock.anthropic.com",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = '{"error": {"message": "unauthorized client detected"}}'

    with patch("httpx.Client.post", return_value=mock_resp):
        with pytest.raises(ProviderUnavailableException) as exc_info:
            provider.chat(messages=[{"role": "user", "content": "hi"}], tools=[])
        assert "Provider HTTP 401" in str(exc_info.value)


def test_openai_provider_request_format():
    provider = OpenAIRuntimeProvider(
        api_key="test-key-oai",
        base_url="https://api.mock.openai.com/v1",
        model="gpt-4o",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "Executing command",
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {
                                "name": "run_command",
                                "arguments": '{"command": "python -m pytest"}',
                            },
                        }
                    ],
                }
            }
        ]
    }

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        res = provider.chat(
            messages=[{"role": "user", "content": "Run tests"}],
            tools=[{"name": "run_command", "input_schema": {"type": "object"}}],
        )

        assert res["tool"] == "run_command"
        assert res["arguments"]["command"] == "python -m pytest"
        assert res["tool_call_id"] == "call_123"
