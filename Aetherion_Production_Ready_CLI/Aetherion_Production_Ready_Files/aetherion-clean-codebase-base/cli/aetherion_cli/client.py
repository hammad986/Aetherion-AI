"""
aetherion_cli/client.py
Production-grade HTTP client for the Aetherion CLI.

Provides:
- Retry logic with exponential backoff for transient errors (502, 503, 504, 429)
- Automatic token refresh on 401 (transparent to callers)
- Streaming SSE with proper buffering, reconnection, and cancellation
- Structured error hierarchy — never leaks stack traces to users
- Separated connect / read / stream timeouts
- Offline detection with friendly guidance
- DELETE / PUT / PATCH support
- Connection pooling via httpx
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Generator, Optional

import httpx

from . import config

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONNECT_TIMEOUT = 10.0    # seconds to establish TCP connection
READ_TIMEOUT    = 900.0   # seconds to wait for a response byte (long for reasoning)
STREAM_TIMEOUT  = 1800.0  # seconds for streaming (agent tasks can run for hours)
MAX_RETRIES     = 3
RETRY_BACKOFF_BASE = 1.5  # multiplied per retry: 1.5s, 2.25s, 3.375s
RETRY_STATUS_CODES = {429, 502, 503, 504}


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------

class AetherionClientError(Exception):
    """Raised for all client-visible API errors."""

    def __init__(self, message: str, status_code: int = 0, hint: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.hint = hint

    def __str__(self) -> str:
        base = super().__str__()
        if self.hint:
            return f"{base}\n  Hint: {self.hint}"
        return base


class AetherionOfflineError(AetherionClientError):
    """Raised when the server cannot be reached at all."""


class AetherionAuthError(AetherionClientError):
    """Raised for 401/403 responses."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class Client:
    """Thin, robust HTTP client for the Aetherion backend API."""

    def __init__(self):
        self.base_url = config.get_server_url().rstrip("/")
        self.token    = config.get_token()
        self._refresh_attempted = False  # guard against infinite refresh loops

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self, stream: bool = False, extra: Optional[dict] = None) -> dict:
        h: dict = {
            "Content-Type":  "application/json",
            "Accept":        "text/event-stream" if stream else "application/json",
            "User-Agent":    f"aetherion-cli/{_cli_version()}",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if extra:
            h.update(extra)
        return h

    def _url(self, path: str) -> str:
        # Normalise: strip double slashes, ensure leading /
        clean = "/" + path.lstrip("/")
        return f"{self.base_url}{clean}"

    def _raise_for_status(self, r: httpx.Response, path: str) -> None:
        if r.is_success:
            return

        # Try to extract a structured error message
        body_text = ""
        try:
            body = r.json()
            # Support both {"error": "..."} and {"error": {"message": "..."}}
            err = body.get("error") or body.get("message") or body.get("detail") or ""
            if isinstance(err, dict):
                body_text = err.get("message", str(err))
            else:
                body_text = str(err)
        except Exception:
            body_text = r.text[:300] if r.text else ""

        sc = r.status_code

        if sc == 401:
            raise AetherionAuthError(
                f"Authentication required (401): {body_text or 'invalid or expired token'}",
                status_code=sc,
                hint="Run `aetherion login` to authenticate.",
            )
        if sc == 403:
            raise AetherionAuthError(
                f"Access denied (403): {body_text or 'insufficient permissions'}",
                status_code=sc,
                hint="Your account may not have access to this resource.",
            )
        if sc == 404:
            raise AetherionClientError(
                f"Not found (404): {path}",
                status_code=sc,
                hint="Verify the server URL and that the resource exists.",
            )
        if sc == 422:
            raise AetherionClientError(
                f"Validation error (422): {body_text}",
                status_code=sc,
            )
        if sc == 429:
            retry_after = r.headers.get("Retry-After", "?")
            raise AetherionClientError(
                f"Rate limited (429): too many requests (retry after {retry_after}s)",
                status_code=sc,
                hint="Wait a moment before retrying.",
            )
        if sc >= 500:
            raise AetherionClientError(
                f"Server error ({sc}): {body_text or 'internal error'}",
                status_code=sc,
                hint="The backend returned an error. Check the server logs if you have access.",
            )

        raise AetherionClientError(
            f"HTTP {sc}: {body_text or getattr(r, 'reason_phrase', 'unknown')}",
            status_code=sc,
        )

    def _refresh_and_retry(self) -> bool:
        """Try to refresh the access token using the stored refresh token.
        Returns True if a new token was obtained, False otherwise.
        Guards against infinite loops.
        """
        if self._refresh_attempted:
            return False
        self._refresh_attempted = True

        rt = config.get_refresh_token()
        if not rt:
            return False

        try:
            with httpx.Client(timeout=10) as http:
                r = http.post(
                    f"{self.base_url}/api/auth/refresh",
                    json={"refresh_token": rt},
                )
                if r.is_success:
                    data = r.json()
                    new_token = data.get("access_token") or data.get("token")
                    if new_token:
                        config.set_token(new_token)
                        self.token = new_token
                        if data.get("refresh_token"):
                            config.set_refresh_token(data["refresh_token"])
                        return True
        except Exception:
            pass
        return False

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
        extra_headers: Optional[dict] = None,
        _auth_retry: bool = True,
    ) -> Any:
        url     = self._url(path)
        headers = self._headers(extra=extra_headers)
        last_exc: Exception = AetherionClientError("Unknown error")

        for attempt in range(MAX_RETRIES):
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(
                        connect=CONNECT_TIMEOUT,
                        read=READ_TIMEOUT,
                        write=READ_TIMEOUT,
                        pool=5.0,
                    )
                ) as http:
                    r = http.request(
                        method,
                        url,
                        json=json_body,
                        params=params,
                        headers=headers,
                    )

                # Retry on transient server errors
                if r.status_code in RETRY_STATUS_CODES and attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF_BASE ** attempt
                    log.debug(
                        "Retrying %s %s in %.1fs (attempt %d/%d, status %d)",
                        method, path, wait, attempt + 1, MAX_RETRIES, r.status_code,
                    )
                    time.sleep(wait)
                    continue

                # Transparent 401 → refresh → retry (once)
                if r.status_code == 401 and _auth_retry:
                    if self._refresh_and_retry():
                        headers = self._headers(extra=extra_headers)
                        return self._request_with_retry(
                            method, path,
                            json_body=json_body,
                            params=params,
                            extra_headers=extra_headers,
                            _auth_retry=False,  # don't loop
                        )

                self._raise_for_status(r, path)

                ct = r.headers.get("content-type", "")
                if "application/json" in ct:
                    return r.json()
                return r.text

            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError) as exc:
                last_exc = AetherionOfflineError(
                    f"Cannot reach {self.base_url}: {exc}",
                    hint="Check your internet connection and server URL (`aetherion config --show`).",
                )
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF_BASE ** attempt
                    log.debug("Connection failed, retrying in %.1fs", wait)
                    time.sleep(wait)
            except AetherionClientError:
                raise
            except httpx.TimeoutException:
                last_exc = AetherionClientError(
                    f"Request timed out after {READ_TIMEOUT}s",
                    hint="The server is slow. Try again or increase the timeout.",
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF_BASE ** attempt)
            except Exception as exc:
                raise AetherionClientError(f"Unexpected error: {exc}") from exc

        raise last_exc

    # ------------------------------------------------------------------
    # Public HTTP methods
    # ------------------------------------------------------------------

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        return self._request_with_retry("GET", path, params=params)

    def post(self, path: str, body: Optional[dict] = None, json_body: Optional[dict] = None) -> Any:
        payload = json_body if json_body is not None else body
        return self._request_with_retry("POST", path, json_body=payload or {})

    def put(self, path: str, body: Optional[dict] = None, json_body: Optional[dict] = None) -> Any:
        payload = json_body if json_body is not None else body
        return self._request_with_retry("PUT", path, json_body=payload or {})

    def patch(self, path: str, body: Optional[dict] = None, json_body: Optional[dict] = None) -> Any:
        payload = json_body if json_body is not None else body
        return self._request_with_retry("PATCH", path, json_body=payload or {})

    def delete(self, path: str) -> Any:
        return self._request_with_retry("DELETE", path)

    # ------------------------------------------------------------------
    # Streaming SSE  (Phase 4 — long-running stability)
    # ------------------------------------------------------------------

    def stream(
        self,
        path: str,
        body: Optional[dict] = None,
        *,
        max_reconnects: int = 3,
    ) -> Generator[dict, None, None]:
        """
        POST to *path* and yield parsed SSE data events.

        Handles:
        - Multi-line event blocks
        - Partial chunks (buffered)
        - Transparent reconnect up to `max_reconnects` on network drops
        - Memory-efficient: never accumulates the full response in RAM
        - Proper propagation of auth / network errors
        - KeyboardInterrupt re-raised to caller (maps to exit 130)
        """
        url     = self._url(path)
        reconnect_count = 0
        last_event_id   = ""

        def _decode_event(data_lines: list[str]) -> dict:
            """Decode one SSE event after joining its data fields per the SSE spec."""
            payload = "\n".join(data_lines)
            try:
                obj = json.loads(payload)
                if last_event_id and isinstance(obj, dict):
                    obj.setdefault("_last_id", last_event_id)
                return obj
            except json.JSONDecodeError:
                return {"type": "raw", "text": payload}

        while True:
            headers = self._headers(stream=True)
            if last_event_id:
                headers["Last-Event-ID"] = last_event_id

            try:
                with httpx.Client(
                    timeout=httpx.Timeout(
                        connect=CONNECT_TIMEOUT,
                        read=STREAM_TIMEOUT,
                        write=30.0,
                        pool=5.0,
                    )
                ) as http:
                    with http.stream(
                        "POST",
                        url,
                        json=body or {},
                        headers=headers,
                    ) as r:
                        if r.status_code == 401 and self._refresh_and_retry():
                            # Retry the whole stream with new token
                            continue
                        if not r.is_success:
                            r.read()
                            self._raise_for_status(r, path)

                        buffer      = ""
                        event_data: list[str] = []
                        event_type  = ""

                        for raw_chunk in r.iter_text():
                            buffer += raw_chunk

                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                line = line.rstrip("\r")

                                if line == "":
                                    # Empty line = end of event block
                                    if event_data:
                                        yield _decode_event(event_data)
                                        event_data = []
                                        event_type = ""
                                elif line.startswith("id:"):
                                    last_event_id = line[3:].strip()
                                elif line.startswith("data:"):
                                    payload = line[5:].lstrip(" ")
                                    if payload and payload != "[DONE]":
                                        event_data.append(payload)
                                elif line.startswith("event:"):
                                    event_type = line[6:].strip()
                                elif line.startswith("retry:"):
                                    pass  # Server-sent retry hint — not used
                                elif line.startswith(":"):
                                    pass  # SSE comment — keepalive

                        # Flush trailing buffer
                        if event_data:
                            yield _decode_event(event_data)

                # Stream completed without error
                return

            except KeyboardInterrupt:
                raise

            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError,
                    httpx.RemoteProtocolError) as exc:
                reconnect_count += 1
                if reconnect_count <= max_reconnects:
                    wait = RETRY_BACKOFF_BASE ** (reconnect_count - 1)
                    log.warning(
                        "Stream interrupted (%s). Reconnecting in %.1fs (attempt %d/%d)…",
                        exc, wait, reconnect_count, max_reconnects,
                    )
                    time.sleep(wait)
                    continue
                raise AetherionOfflineError(
                    f"Stream lost after {max_reconnects} reconnect attempts: {exc}",
                    hint="Check your internet connection and server URL.",
                ) from exc

            except AetherionClientError:
                raise
            except Exception as exc:
                raise AetherionClientError(f"Stream error: {exc}") from exc

    # ------------------------------------------------------------------
    # Health check (never raises — returns error dict)
    # ------------------------------------------------------------------

    def health(self) -> dict:
        """Return health dict. Never raises — always returns a dict."""
        # Try v1 endpoint first, fall back to legacy
        for endpoint in ("/api/v1/health", "/api/health"):
            try:
                result = self.get(endpoint)
                if isinstance(result, dict) and result.get("status") == "ok":
                    return result
                # If it returned something else, try to normalise
                if isinstance(result, dict):
                    return result
            except AetherionOfflineError as e:
                return {"ok": False, "error": str(e), "offline": True}
            except AetherionAuthError:
                # Health endpoint should not require auth — but if it does, server is up
                return {"status": "ok", "version": "?"}
            except Exception:
                pass
        return {"ok": False, "error": "Health check failed", "offline": True}


def _cli_version() -> str:
    try:
        from importlib.metadata import version
        return version("aetherion")
    except Exception:
        return "dev"
