from contextlib import contextmanager
from unittest.mock import MagicMock

import httpx
import pytest

import aetherion_cli.tui.gateway as gateway_module
from aetherion_cli.tui.gateway import Gateway
from aetherion_cli.tui.state import AgentStatus, Message, state


class FakeResponse:
    def __init__(self, chunks, error=None):
        self._chunks = chunks
        self._error = error
        self.is_success = True
        self.status_code = 200

    def iter_text(self):
        yield from self._chunks
        if self._error:
            raise self._error


class FakeHttp:
    def __init__(self, response, calls):
        self.response = response
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    @contextmanager
    def stream(self, method, url, json, headers):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
            }
        )
        yield self.response


@pytest.fixture(autouse=True)
def reset_tui_state():
    with state._lock:
        state.messages = []
        state.status = AgentStatus.IDLE
        state.error_message = None
        state.active_session_id = "tui-session"
    yield
    with state._lock:
        state.messages = []
        state.status = AgentStatus.IDLE
        state.error_message = None
        state.active_session_id = None


def test_sse_parser_preserves_event_ids(monkeypatch):
    gateway = Gateway()
    response = FakeResponse(
        [
            'id: 7\ndata: {"type":"text","text":"hello"}\n\n',
            'id: 8\ndata: {"type":"done","status":"success"}\n\n',
        ]
    )

    events = list(gateway._sse_stream(response))

    assert events == [
        {"type": "text", "text": "hello", "_last_id": "7"},
        {"type": "done", "status": "success", "_last_id": "8"},
    ]


def test_stream_reconnects_canonical_post_without_duplicate_text(monkeypatch):
    gateway = Gateway()
    gateway.base_url = "https://aetherion.test"
    gateway._token = "test-token"
    gateway.list_local_sessions = MagicMock()
    gateway._persist_message = MagicMock()

    calls = []
    responses = [
        FakeResponse(
            [
                'id: 0\ndata: {"type":"queued","task_id":"tui-session"}\n\n',
                'id: 1\ndata: {"type":"text","text":"hello "}\n\n',
            ],
            error=httpx.RemoteProtocolError("disconnect"),
        ),
        FakeResponse(
            [
                'id: 2\ndata: {"type":"text","text":"world"}\n\n',
                'id: 3\ndata: {"type":"done","status":"success"}\n\n',
            ]
        ),
    ]
    clients = iter(FakeHttp(response, calls) for response in responses)
    monkeypatch.setattr(gateway, "_client", lambda **_kwargs: next(clients))
    monkeypatch.setattr(gateway_module, "RECONNECT_BACKOFF", [0.0, 0.0, 0.0])

    state.append_message(Message.assistant("", streaming=True))
    state.set_status(AgentStatus.RUNNING)

    gateway._stream_worker(
        "test task",
        "tui-session",
        "default",
    )

    assert len(calls) == 2
    assert all(call["method"] == "POST" for call in calls)
    assert all(
        call["url"] == "https://aetherion.test/api/v1/agent/invoke"
        for call in calls
    )
    assert "Last-Event-ID" not in calls[0]["headers"]
    assert calls[1]["headers"]["Last-Event-ID"] == "1"
    assert state.messages[-1].content == "hello world"
    assert state.messages[-1].is_streaming is False
    assert state.status == AgentStatus.DONE
    gateway.list_local_sessions.assert_called_once_with()


def test_stream_cancellation_does_not_report_completion(monkeypatch):
    gateway = Gateway()
    gateway._client = MagicMock()
    cancel_event = gateway._cancel_stream
    cancel_event.set()

    state.append_message(Message.assistant("partial", streaming=True))
    state.set_status(AgentStatus.STREAMING)

    gateway._stream_worker(
        "test task",
        "tui-session",
        "default",
        cancel_event,
    )

    gateway._client.assert_not_called()
    assert state.messages[-1].is_streaming is False
    assert state.status == AgentStatus.IDLE
