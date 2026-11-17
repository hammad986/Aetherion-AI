from __future__ import annotations

from contextlib import contextmanager

from aetherion_cli.client import Client
from aetherion_cli.tui.gateway import Gateway


class _Response:
    status_code = 200
    is_success = True
    headers: dict[str, str] = {}

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def iter_text(self):
        yield from self._chunks


class _HttpClient:
    def __init__(self, response: _Response, **_kwargs) -> None:
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    @contextmanager
    def stream(self, *_args, **_kwargs):
        yield self._response


def test_stream_joins_multiline_sse_data_before_decoding(monkeypatch) -> None:
    """SSE's repeated data fields form one event payload, not separate events."""
    response = _Response(
        [
            'id: 42\nevent: message\ndata: {"type":"text",\n'
            'data: "text":"hello"}\n\n'
        ]
    )
    monkeypatch.setattr(
        "aetherion_cli.client.httpx.Client",
        lambda **kwargs: _HttpClient(response, **kwargs),
    )

    assert list(Client().stream("/api/v1/agent/invoke")) == [
        {"type": "text", "text": "hello", "_last_id": "42"}
    ]


def test_tui_stream_joins_multiline_sse_data_before_decoding() -> None:
    response = _Response(
        [
            'id: 43\nevent: message\ndata: {"type":"text",\n'
            'data: "text":"hello"}\n\n'
        ]
    )

    assert list(Gateway()._sse_stream(response)) == [
        {"type": "text", "text": "hello", "_last_id": "43"}
    ]
