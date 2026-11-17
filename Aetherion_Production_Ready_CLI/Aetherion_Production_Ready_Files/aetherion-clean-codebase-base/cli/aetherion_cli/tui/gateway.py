"""
aetherion_cli/tui/gateway.py
Aetherion TUI Gateway — complete async bridge to the unified backend.

Owns all network I/O. The UI thread never touches HTTP.

Supports:
- SSE agent streaming with reconnect budget (3 retries, exponential backoff)
- Health polling every 30s with latency measurement
- Dashboard metrics, timeline, session browser
- Memory search/list, plugin marketplace, skills, providers
- Background jobs, notifications stream
- Git status, scheduler tasks
- Crash hooks writing to ~/.aetherion/logs/tui_crash.log
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional

import httpx

from ..config import get_server_url, get_token
from .state import (
    AgentStatus,
    ApprovalRequest,
    BackgroundJob,
    DashboardMetrics,
    MemoryEntry,
    Message,
    NotifLevel,
    Notification,
    Plugin,
    Provider,
    RuntimeState,
    Session,
    Screen,
    Skill,
    TimelineEvent,
    state,
)

log = logging.getLogger("aetherion.tui.gateway")

def classify_network_error(exc_or_resp: Any) -> tuple[RuntimeState, str]:
    """
    Classify an exception or HTTP response into (RuntimeState, user_facing_explanation).
    Never exposes raw OS socket/DNS traces (e.g. Errno 11001) as primary UX.
    """
    if isinstance(exc_or_resp, httpx.Response):
        status = exc_or_resp.status_code
        if status == 401:
            return RuntimeState.AUTH_REQUIRED, "Authentication required or token expired. Please log in."
        elif status == 403:
            return RuntimeState.AUTH_REQUIRED, "Access forbidden. Your account lacks permission for this action."
        elif status in (500, 502, 503, 504):
            return RuntimeState.ERROR, f"Server error (HTTP {status}). The service is currently degraded or unavailable."
        else:
            return RuntimeState.DEGRADED, f"Server returned unexpected HTTP status {status}."

    exc = exc_or_resp
    err_str = str(exc).lower()

    # DNS resolution failure (Windows Errno 11001 or socket.gaierror)
    if "11001" in err_str or "getaddrinfo" in err_str or "nameresolutionerror" in err_str:
        return RuntimeState.OFFLINE, "Unable to resolve server host. Backend may be offline or DNS is unreachable."

    # Connection refused / Connect timeout / Connect error
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)) or "connection refused" in err_str or "connecterror" in err_str or "10061" in err_str:
        return RuntimeState.OFFLINE, "Cannot connect to server. Ensure the Aetherion backend is running on the configured port."

    # Read / Write timeout
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)) or "timeout" in err_str:
        return RuntimeState.DEGRADED, "Request timed out waiting for server response."

    # HTTPStatusError
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return RuntimeState.AUTH_REQUIRED, "Authentication required or token expired. Please log in."
        elif status == 403:
            return RuntimeState.AUTH_REQUIRED, "Access forbidden. Insufficient permissions."
        elif status in (500, 502, 503, 504):
            return RuntimeState.ERROR, f"Server error (HTTP {status})."
        return RuntimeState.DEGRADED, f"HTTP error {status}."

    # General network error
    if isinstance(exc, httpx.NetworkError):
        return RuntimeState.DEGRADED, "Network communication error. Check your connection."

    return RuntimeState.ERROR, f"Communication error: {str(exc)[:120]}"

# ── Crash logging ─────────────────────────────────────────────────────────────
_CRASH_LOG = Path.home() / ".aetherion" / "logs" / "tui_crash.log"


def _install_crash_hooks() -> None:
    orig_hook = sys.excepthook

    def _panic(exc_type, exc_val, exc_tb):
        import traceback
        trace = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
        try:
            _CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
            with _CRASH_LOG.open("a", encoding="utf-8") as f:
                f.write(f"\n=== crash · {time.strftime('%Y-%m-%dT%H:%M:%S')} ===\n{trace}")
        except Exception:
            pass
        first = str(exc_val).strip().splitlines()[0] if str(exc_val).strip() else exc_type.__name__
        state.set_error(f"[crash] {exc_type.__name__}: {first}")
        orig_hook(exc_type, exc_val, exc_tb)

    sys.excepthook = _panic

    orig_thread_hook = getattr(threading, "excepthook", None)

    def _thread_panic(args):
        import traceback
        trace = "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback))
        try:
            _CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
            with _CRASH_LOG.open("a", encoding="utf-8") as f:
                f.write(f"\n=== thread crash · {time.strftime('%Y-%m-%dT%H:%M:%S')} "
                        f"· {args.thread.name} ===\n{trace}")
        except Exception:
            pass
        first = (str(args.exc_value).strip().splitlines()[0]
                 if str(args.exc_value).strip() else args.exc_type.__name__)
        state.set_error(f"[thread] {args.exc_type.__name__}: {first}")
        if orig_thread_hook:
            orig_thread_hook(args)

    threading.excepthook = _thread_panic


# ── Constants ─────────────────────────────────────────────────────────────────
CONNECT_TIMEOUT  = 8.0
READ_TIMEOUT     = 900.0
STREAM_TIMEOUT   = 1200.0
HEALTH_INTERVAL  = 30.0
RECONNECT_BACKOFF = [2.0, 4.0, 8.0]
MAX_RETRIES       = 3


def _daemon(target: Callable, name: str, *args, **kwargs) -> threading.Thread:
    t = threading.Thread(target=target, args=args, kwargs=kwargs,
                         daemon=True, name=name)
    t.start()
    return t


class Gateway:
    """Single gateway instance for the TUI process."""

    def __init__(self) -> None:
        self.base_url       = get_server_url().rstrip("/")
        self._lock          = threading.RLock()
        self._token: Optional[str] = get_token()
        self._cancel_stream = threading.Event()
        self._started       = False

    # ── Bootstrap ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        _install_crash_hooks()
        _daemon(self._health_loop, "aetherion-health")
        _daemon(self._notifications_loop, "aetherion-notifications")

    def refresh_token(self) -> None:
        """Reload access token from keyring/config into memory."""
        with self._lock:
            self._token = get_token()

    # ── Device Authorization Flow ─────────────────────────────────────────────

    def device_request(self, action: str = "login") -> dict:
        """
        Request a device code from the backend.
        Returns {device_code, user_code, verification_uri, expires_in, interval}.
        Synchronous — called from a Textual worker thread.
        """
        try:
            with self._client(read=15.0) as http:
                r = http.post(
                    self._url("/api/auth/device/request"),
                    json={"action": action},
                    headers={"Content-Type": "application/json", "User-Agent": "aetherion-tui/2.0"},
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            return {"error": str(exc)}

    def device_poll(self, device_code: str) -> dict:
        """
        Poll authorization status.
        Returns {status, access_token?, refresh_token?, user?}.
        status in: pending | authorized | denied | expired
        """
        try:
            with self._client(read=10.0) as http:
                r = http.get(
                    self._url("/api/auth/device/poll"),
                    params={"device_code": device_code},
                    headers={"User-Agent": "aetherion-tui/2.0"},
                )
                if r.status_code == 400:
                    try:
                        err_body = r.json()
                        err_code = err_body.get("error", "")
                        if err_code in ("authorization_pending", "slow_down"):
                            return {"status": "pending"}
                    except Exception:
                        pass
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    def complete_device_auth(self, access_token: str, refresh_token: str, user: dict) -> None:
        """
        Called after successful device poll — persists tokens and updates state.
        """
        from ..config import set_token, set_refresh_token
        set_token(access_token)
        if refresh_token:
            set_refresh_token(refresh_token)
        with self._lock:
            self._token = access_token
        state.set_user(
            email=user.get("email", ""),
            plan=user.get("plan", "Lite"),
            user_id=str(user.get("id", "")),
        )
        state.set_connected(True)

    def silent_login(self) -> bool:
        """
        Attempt silent re-authentication using stored access token or refresh token.
        Returns True if authenticated, False otherwise.
        Called at TUI startup before deciding which screen to show.
        """
        from ..config import get_token, get_refresh_token, set_token, set_refresh_token

        # 1. First attempt: check if existing access token is still valid
        token = get_token()
        token_is_expired = False
        if token and token.count(".") == 2:
            try:
                import base64
                payload_part = token.split(".")[1]
                padded = payload_part + "=" * (-len(payload_part) % 4)
                claims = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
                exp = claims.get("exp")
                if exp is not None and isinstance(exp, (int, float)) and exp <= time.time():
                    token_is_expired = True
            except Exception:
                pass

        if token and not token_is_expired:
            with self._lock:
                self._token = token
            try:
                with self._client(read=10.0) as http:
                    r = http.get(
                        self._url("/api/auth/me"),
                        headers={"Authorization": f"Bearer {token}", "User-Agent": "aetherion-tui/2.0"},
                    )
                    if r.is_success:
                        data = r.json()
                        user = data.get("user") or data.get("data") or data
                        state.set_user(
                            email=user.get("email", ""),
                            plan=user.get("plan", "Lite"),
                            user_id=str(user.get("id", user.get("user_id", ""))),
                        )
                        state.set_connected(True)
                        return True
            except Exception as exc:
                log.debug("Silent token verification failed: %s", exc)

        # 2. Second attempt: refresh token
        rt = get_refresh_token()
        if not rt:
            return False
        try:
            with self._client(read=15.0) as http:
                r = http.post(
                    self._url("/api/auth/refresh"),
                    json={"refresh_token": rt},
                    headers={"Content-Type": "application/json", "User-Agent": "aetherion-tui/2.0"},
                )
                if r.status_code in (401, 403):
                    from ..config import clear_token, clear_refresh_token
                    clear_token()
                    clear_refresh_token()
                    return False
                r.raise_for_status()
                data = r.json()
                new_at = data.get("access_token")
                new_rt = data.get("refresh_token", rt)  # rolling refresh
                if not new_at:
                    return False
                set_token(new_at)
                set_refresh_token(new_rt)
                with self._lock:
                    self._token = new_at
                user = data.get("user") or data.get("data") or data
                state.set_user(
                    email=user.get("email", ""),
                    plan=user.get("plan", "Lite"),
                    user_id=str(user.get("id", user.get("user_id", ""))),
                )
                state.set_connected(True)
                return True
        except Exception as exc:
            log.debug("Silent login failed: %s", exc)
            return False

    def logout(self) -> None:
        """Revoke current session server-side and clear local credentials."""
        def _do():
            try:
                self._post("/api/auth/logout", {})
            except Exception:
                pass
            from ..config import clear_token, clear_refresh_token
            clear_token()
            clear_refresh_token()
            with self._lock:
                self._token = None
            state.set_user(None)
            state.navigate(Screen.WELCOME)
            state.push_notification("Logged out", NotifLevel.SUCCESS)
        _daemon(_do, "aetherion-logout")

    def logout_everywhere(self) -> None:
        """Revoke all sessions server-side and clear local credentials."""
        def _do():
            try:
                self._post("/api/auth/logout_everywhere", {})
            except Exception:
                pass
            from ..config import clear_token, clear_refresh_token
            clear_token()
            clear_refresh_token()
            with self._lock:
                self._token = None
            state.set_user(None)
            state.navigate(Screen.WELCOME)
            state.push_notification("Logged out from all devices", NotifLevel.SUCCESS)
        _daemon(_do, "aetherion-logout")

    # ── Internal HTTP helpers ──────────────────────────────────────────────────

    def _headers(self, stream: bool = False) -> Dict[str, str]:
        with self._lock:
            token = self._token
        if not token:
            from ..config import get_token
            token = get_token()
            if token:
                with self._lock:
                    self._token = token
        h: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": "aetherion-tui/2.0",
        }
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _client(self, read: float = READ_TIMEOUT) -> httpx.Client:
        return httpx.Client(timeout=httpx.Timeout(
            connect=CONNECT_TIMEOUT, read=read, write=30.0, pool=5.0
        ))

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        if state.offline_mode:
            return {}
        with self._client() as http:
            r = http.get(self._url(path), params=params, headers=self._headers())
            if r.status_code == 401 and state.current_screen != Screen.WELCOME:
                state.set_user(None)
                state.navigate(Screen.WELCOME)
            r.raise_for_status()
            return r.json()

    def _post(self, path: str, body: Optional[dict] = None) -> Any:
        if state.offline_mode:
            return {}
        with self._client() as http:
            r = http.post(self._url(path), json=body or {}, headers=self._headers())
            if r.status_code == 401 and state.current_screen != Screen.WELCOME:
                state.set_user(None)
                state.navigate(Screen.WELCOME)
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            return r.json() if "application/json" in ct else r.text

    def _put(self, path: str, body: Optional[dict] = None) -> Any:
        if state.offline_mode:
            return {}
        with self._client() as http:
            r = http.put(self._url(path), json=body or {}, headers=self._headers())
            if r.status_code == 401 and state.current_screen != Screen.WELCOME:
                state.set_user(None)
                state.navigate(Screen.WELCOME)
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            return r.json() if "application/json" in ct else r.text

    def _delete(self, path: str) -> Any:
        if state.offline_mode:
            return {}
        with self._client() as http:
            r = http.delete(self._url(path), headers=self._headers())
            if r.status_code == 401 and state.current_screen != Screen.WELCOME:
                state.set_user(None)
                state.navigate(Screen.WELCOME)
            r.raise_for_status()
            return r.json() if r.content else {}

    def _sse_stream(self, r: httpx.Response) -> Generator[dict, None, None]:
        """Parse SSE response into event dicts."""
        buf = ""
        parts: List[str] = []
        last_event_id = ""

        def _decode_event(data_lines: List[str]) -> dict:
            """Decode one SSE event after joining its data fields per the SSE spec."""
            payload = "\n".join(data_lines)
            try:
                event = json.loads(payload)
                if last_event_id and isinstance(event, dict):
                    event.setdefault("_last_id", last_event_id)
                return event
            except json.JSONDecodeError:
                return {"type": "raw", "text": payload}

        for chunk in r.iter_text():
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.rstrip("\r")
                if line == "":
                    if parts:
                        yield _decode_event(parts)
                    parts = []
                elif line.startswith("id:"):
                    last_event_id = line[3:].strip()
                elif line.startswith("data:"):
                    payload = line[5:].lstrip(" ")
                    if payload and payload != "[DONE]":
                        parts.append(payload)
        if parts:
            yield _decode_event(parts)

    # ── Health polling ─────────────────────────────────────────────────────────

    def _health_loop(self) -> None:
        while True:
            try:
                t0 = time.monotonic()
                with self._client(read=10.0) as http:
                    r = http.get(self._url("/api/health"), headers=self._headers())
                lat = int((time.monotonic() - t0) * 1000)
                if r.is_success:
                    data = r.json()
                    state.set_connected(True, lat)
                    state.set_runtime_state(RuntimeState.ONLINE, f"Healthy · {lat}ms")
                    state.set_error(None)
                    # Try to pull user info from health response
                    if data.get("user"):
                        state.set_user(
                            email=data["user"].get("email"),
                            plan=data["user"].get("plan", "Lite"),
                            user_id=str(data["user"].get("id", "")),
                        )
                else:
                    rs, detail = classify_network_error(r)
                    state.set_runtime_state(rs, detail)
                    state.set_connected(False)
            except Exception as exc:
                rs, detail = classify_network_error(exc)
                state.set_runtime_state(rs, detail)
                state.set_connected(False)
                log.debug("Health check failed: %s", exc)
            time.sleep(HEALTH_INTERVAL)

    # ── Notifications SSE ──────────────────────────────────────────────────────

    def _notifications_loop(self) -> None:
        """Subscribe to /api/notifications/stream and push to state."""
        while True:
            try:
                with self._client(read=STREAM_TIMEOUT) as http:
                    with http.stream("GET", self._url("/api/notifications/stream"),
                                     headers=self._headers(stream=True)) as r:
                        if not r.is_success:
                            time.sleep(30)
                            continue
                        for event in self._sse_stream(r):
                            msg = event.get("message", "")
                            level_str = event.get("level", "info")
                            level = {
                                "success": NotifLevel.SUCCESS,
                                "warning": NotifLevel.WARNING,
                                "error": NotifLevel.ERROR,
                            }.get(level_str, NotifLevel.INFO)
                            if msg:
                                state.push_notification(msg, level)
            except Exception as exc:
                log.debug("Notifications stream disconnected: %s", exc)
            time.sleep(10)

    # ── Agent streaming ────────────────────────────────────────────────────────

    def start_stream(self, task: str, session_id: Optional[str] = None,
                     model: Optional[str] = None) -> None:
        """Launch a background agent task. Non-blocking."""
        with self._lock:
            self._cancel_stream.set()
            cancel_event = threading.Event()
            self._cancel_stream = cancel_event
        sid = session_id or state.active_session_id or f"tui-{uuid.uuid4().hex[:12]}"
        
        # Ensure local SQLite session exists and persist the user task
        self._ensure_local_session(sid, model or state.active_model)
        self._persist_message(sid, "user", task)
        
        state.set_session(sid)
        state.set_status(AgentStatus.RUNNING)
        state.set_error(None)
        # streaming placeholder
        state.append_message(Message.assistant("", streaming=True))
        _daemon(self._stream_worker, "aetherion-stream",
                task, sid, model or state.active_model, cancel_event)

    def cancel_stream(self) -> None:
        with self._lock:
            self._cancel_stream.set()

    def _stream_worker(
        self,
        task: str,
        session_id: str,
        model: str,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        cancel_event = cancel_event or self._cancel_stream
        payload = {
            "task": task,
            "session_id": session_id,
            "model": model,
        }
        url = self._url("/api/v1/agent/invoke")
        retries = 0
        last_event_id = ""

        while retries <= MAX_RETRIES:
            if cancel_event.is_set():
                state.finalize_streaming_message()
                state.set_status(AgentStatus.IDLE)
                return
            try:
                terminal_event = False
                headers = self._headers(stream=True)
                if last_event_id:
                    headers["Last-Event-ID"] = last_event_id

                with self._client(read=STREAM_TIMEOUT) as http:
                    with http.stream("POST", url, json=payload,
                                     headers=headers) as r:
                        if not r.is_success:
                            r.read()
                            body = ""
                            try:
                                body = r.json().get("error", r.text[:200])
                            except Exception:
                                body = r.text[:200]
                            state.set_error(f"HTTP {r.status_code}: {body}")
                            state.finalize_streaming_message()
                            state.set_status(AgentStatus.ERROR)
                            return

                        for event in self._sse_stream(r):
                            if cancel_event.is_set():
                                break
                            if event.get("_last_id"):
                                last_event_id = str(event["_last_id"])
                            self._handle_stream_event(event)
                            if event.get("type") in ("done", "error"):
                                terminal_event = True

                if cancel_event.is_set():
                    state.finalize_streaming_message()
                    state.set_status(AgentStatus.IDLE)
                    return

                if terminal_event:
                    if state.status not in (
                        AgentStatus.ERROR,
                        AgentStatus.WAITING_APPROVAL,
                    ):
                        state.set_status(AgentStatus.DONE)
                    self.list_local_sessions()
                    return

                raise httpx.RemoteProtocolError(
                    "SSE stream ended before a terminal event"
                )

            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
            ) as exc:
                if retries < MAX_RETRIES:
                    backoff = RECONNECT_BACKOFF[min(retries, len(RECONNECT_BACKOFF) - 1)]
                    log.warning("Stream disconnected, retry %d/%d in %.1fs", retries + 1, MAX_RETRIES, backoff)
                    retries += 1
                    if cancel_event.wait(backoff):
                        state.finalize_streaming_message()
                        state.set_status(AgentStatus.IDLE)
                        return
                    continue
                state.set_error(f"Connection lost after {MAX_RETRIES} retries: {exc}")
                state.finalize_streaming_message()
                state.set_status(AgentStatus.ERROR)
                return
            except Exception as exc:
                state.set_error(f"Stream error: {exc}")
                log.exception("Unexpected stream error")
                state.finalize_streaming_message()
                state.set_status(AgentStatus.ERROR)
                return

    def _handle_stream_event(self, event: dict) -> None:
        ev = event.get("type", "")

        if ev == "queued":
            if state.status not in (
                AgentStatus.STREAMING,
                AgentStatus.WAITING_APPROVAL,
            ):
                state.set_status(AgentStatus.RUNNING)

        elif ev in ("text", "delta"):
            state.update_last_assistant_message(event.get("text", ""))
            if state.status != AgentStatus.STREAMING:
                state.set_status(AgentStatus.STREAMING)

        elif ev == "raw":
            state.update_last_assistant_message(event.get("text", ""))

        elif ev == "tool_call":
            tool = event.get("tool", "unknown")
            args_summary = str(event.get("args", ""))[:80]
            state.append_message(Message.tool_call(tool, f"{tool}({args_summary})"))

        elif ev == "state_change":
            agent_state = event.get("agent_state", "")
            if agent_state:
                state.append_message(Message.system(f"● {agent_state}"))

        elif ev == "usage":
            in_tok  = event.get("inputTokens", 0) or event.get("input_tokens", 0)
            out_tok = event.get("outputTokens", 0) or event.get("output_tokens", 0)
            state.update_context(in_tok, out_tok)

        elif ev == "approval":
            req = ApprovalRequest(
                request_id  = event.get("request_id", uuid.uuid4().hex[:8]),
                tool_name   = event.get("tool", "unknown"),
                description = event.get("description", "Agent requests permission"),
                risk_level  = event.get("risk", "low"),
                details     = event.get("details", {}),
            )
            state.set_approval(req)
            state.set_status(AgentStatus.WAITING_APPROVAL)

        elif ev == "job":
            # Background job update
            job = BackgroundJob(
                id      = event.get("job_id", uuid.uuid4().hex[:8]),
                name    = event.get("name", "Background task"),
                status  = event.get("status", "running"),
                progress= event.get("progress", 0),
            )
            state.upsert_job(job)

        elif ev == "notification":
            level_str = event.get("level", "info")
            level = {
                "success": NotifLevel.SUCCESS,
                "warning": NotifLevel.WARNING,
                "error":   NotifLevel.ERROR,
            }.get(level_str, NotifLevel.INFO)
            state.push_notification(event.get("message", ""), level)

        elif ev == "error":
            state.set_error(event.get("error", "Unknown agent error"))
            state.finalize_streaming_message()
            state.set_status(AgentStatus.ERROR)

        elif ev == "done":
            in_tok  = event.get("inputTokens", 0) or event.get("input_tokens", 0)
            out_tok = event.get("outputTokens", 0) or event.get("output_tokens", 0)
            summary = event.get("summary", "")
            if in_tok or out_tok:
                state.update_context(in_tok, out_tok)

            with state._lock:
                for m in reversed(state.messages):
                    if m.role == "assistant":
                        if not m.content.strip() and summary:
                            m.content = summary
                        break

            state.finalize_streaming_message(token_count=out_tok)
            
            # Persist completed assistant message
            with state._lock:
                for m in reversed(state.messages):
                    if m.role == "assistant" and not m.is_streaming:
                        if state.active_session_id:
                            self._persist_message(state.active_session_id, "assistant", m.content, tokens=out_tok)
                        break
                        
            status = event.get("status", "done")
            failure_class = event.get("failure_class", "")
            ver_status = event.get("verification", "")
            err_msg = event.get("error")

            if status in ("error", "failed") or failure_class in ("budget_exhausted", "verifier_failure", "blocked"):
                if failure_class == "budget_exhausted":
                    err_msg = err_msg or "Task halted: Execution budget exhausted."
                    state.set_error(err_msg)
                    state.set_status(AgentStatus.BUDGET_EXHAUSTED)
                elif failure_class == "blocked" or status == "blocked":
                    err_msg = err_msg or "Task blocked: Policy or permission constraint."
                    state.set_error(err_msg)
                    state.set_status(AgentStatus.BLOCKED)
                elif failure_class == "verifier_failure" or ver_status == "VERIFICATION_FAILED":
                    err_msg = err_msg or f"Task verification failed ({ver_status}). Repair attempts exhausted."
                    state.set_error(err_msg)
                    state.set_status(AgentStatus.FAILED)
                else:
                    err_msg = err_msg or "Task failed."
                    state.set_error(err_msg)
                    state.set_status(AgentStatus.FAILED)
            elif status in ("cancelled", "canceled", "stopped"):
                state.set_status(AgentStatus.CANCELLED)
            else:
                state.set_status(AgentStatus.SUCCEEDED)

    # ── Approval ──────────────────────────────────────────────────────────────

    def approve_tool(self, request_id: str, decision: str) -> None:
        def _send():
            try:
                payload = {}
                if decision in ("always", "session"):
                    payload["duration"] = decision

                self._post("/api/z29/governance/approve/" + request_id
                           if decision in ("allow", "always", "session")
                           else "/api/z29/governance/reject/" + request_id, payload)
            except Exception as exc:
                log.warning("Approval send failed: %s", exc)
            finally:
                state.set_approval(None)
                state.set_status(AgentStatus.RUNNING if decision in ("allow", "always", "session") else AgentStatus.IDLE)

        _daemon(_send, "aetherion-approval")

    # ── Dashboard ─────────────────────────────────────────────────────────────

    def load_dashboard(self) -> None:
        def _fetch():
            try:
                data = self._get("/api/dashboard/metrics")
                m = DashboardMetrics(
                    sessions_today = data.get("sessions_today", 0),
                    tasks_done     = data.get("tasks_done", 0),
                    tokens_used    = data.get("tokens_used", 0),
                    cost_usd       = float(data.get("cost_usd", 0)),
                    uptime_pct     = float(data.get("uptime_pct", 100)),
                    active_agents  = data.get("active_agents", 0),
                    memory_entries = data.get("memory_entries", 0),
                    plugins_active = data.get("plugins_active", 0),
                )
                state.set_dashboard_metrics(m)
            except Exception as exc:
                log.debug("Dashboard metrics failed: %s", exc)
                state.set_dashboard_metrics(DashboardMetrics())

            # Load timeline
            try:
                tdata = self._get("/api/dashboard/timeline")
                events = [
                    TimelineEvent(
                        id     = str(e.get("id", uuid.uuid4().hex[:8])),
                        ts     = float(e.get("ts", time.time())),
                        event  = str(e.get("event", "?")),
                        detail = str(e.get("detail", "")),
                        level  = str(e.get("level", "info")),
                    )
                    for e in (tdata if isinstance(tdata, list) else tdata.get("events", []))
                ]
                state.set_timeline(events)
            except Exception as exc:
                log.debug("Timeline load failed: %s", exc)

        _daemon(_fetch, "aetherion-dashboard")

    # ── Sessions ──────────────────────────────────────────────────────────────

    def load_sessions(self, limit: int = 30) -> None:
        state.set_sessions_loading(True)

        def _fetch():
            try:
                res = self._get("/api/sessions", params={"limit": str(limit)})
                items = res if isinstance(res, list) else res.get("sessions", [])
                sessions = [
                    Session(
                        id         = str(s.get("id", "")),
                        title      = str(s.get("title") or s.get("task") or "Untitled")[:60],
                        status     = str(s.get("status", "?")),
                        model      = s.get("model"),
                        created_at = s.get("created_at"),
                        updated_at = s.get("updated_at"),
                        task       = s.get("task"),
                        tags       = s.get("tags", []),
                    )
                    for s in items
                ]
                state.set_sessions(sessions)
            except Exception as exc:
                state.set_sessions([], error=str(exc))

        _daemon(_fetch, "aetherion-sessions")

    def create_session(self, title: str = "New session") -> None:
        def _create():
            try:
                res = self._post("/api/sessions/create", {"title": title})
                sid = res.get("id") or res.get("session_id")
                if sid:
                    state.set_session(str(sid))
                    state.clear_messages()
                    state.push_notification(f"Session created: {sid[:16]}", NotifLevel.SUCCESS)
                    self.list_local_sessions()
            except Exception as exc:
                state.set_error(f"Failed to create session: {exc}")

        _daemon(_create, "aetherion-create-session")

    def rename_session(self, session_id: str, new_name: str) -> bool:
        """Rename an existing session locally and in remote backend if available."""
        from ..session_store import update_session_title
        ok = update_session_title(session_id, new_name)

        # Update in-memory state
        with state._lock:
            for s in state.sessions:
                if s.id == session_id:
                    s.title = new_name
                    break
        state._notify("sessions")

        # If online, optionally notify backend (best-effort)
        if state.connected and not state.offline_mode:
            def _patch():
                try:
                    with self._client(read=5.0) as http:
                        http.patch(
                            self._url(f"/api/sessions/{session_id}"),
                            json={"title": new_name},
                            headers={"Authorization": f"Bearer {self._token}"},
                        )
                except Exception:
                    pass
            _daemon(_patch, "aetherion-rename-session")

        return ok

    def delete_session(self, session_id: str) -> None:
        def _del():
            try:
                self._post(f"/api/session/{session_id}/stop", {})
                self.load_sessions()
                state.push_notification("Session deleted", NotifLevel.SUCCESS)
            except Exception as exc:
                state.set_error(f"Failed to delete session: {exc}")

        _daemon(_del, "aetherion-del-session")

    def load_session_messages(self, session_id: str) -> None:
        def _fetch():
            try:
                res = self._get(f"/api/session/{session_id}/steps")
                steps = res if isinstance(res, list) else res.get("steps", [])
                state.clear_messages()
                for s in steps:
                    role = s.get("role", s.get("type", "system"))
                    content = s.get("content", s.get("text", ""))
                    if role in ("user", "human"):
                        state.append_message(Message.user(content))
                    elif role in ("assistant", "ai", "agent"):
                        state.append_message(Message.assistant(content))
                    elif role == "tool":
                        state.append_message(Message.tool_call(
                            s.get("tool_name", "tool"), content
                        ))
                    else:
                        state.append_message(Message.system(content))
            except Exception as exc:
                state.append_message(Message.system(f"Could not load history: {exc}"))

        _daemon(_fetch, "aetherion-load-history")

    # ── Memory ────────────────────────────────────────────────────────────────

    def load_memory(self, query: str = "", limit: int = 50) -> None:
        state.set_memory_loading(True)

        def _fetch():
            try:
                if query:
                    res = self._post("/api/ltm/search", {"query": query, "limit": limit})
                else:
                    res = self._get("/api/memory/recent", params={"limit": str(limit)})
                items = res if isinstance(res, list) else res.get("results", res.get("entries", []))
                entries = [
                    MemoryEntry(
                        id         = str(e.get("id", uuid.uuid4().hex[:8])),
                        content    = str(e.get("content", e.get("text", ""))),
                        importance = float(e.get("importance", e.get("score", 0.5))),
                        created_at = e.get("created_at"),
                        tags       = e.get("tags", []),
                    )
                    for e in items
                ]
                state.set_memory(entries)
            except Exception as exc:
                state.set_memory([], error=str(exc))

        _daemon(_fetch, "aetherion-memory")

    def add_memory(self, content: str, on_success: Optional[Callable[[], None]] = None) -> None:
        def _do_post():
            if state.runtime_state == RuntimeState.OFFLINE or state.offline_mode:
                msg = "Offline: local offline queue is not configured; entry was not persisted."
                state.set_error(msg)
                state.set_failed_action("Save memory", _do_post)
                return

            try:
                self._post("/api/ltm/remember", {"content": content, "importance": 0.7})
                state.push_notification("Memory saved successfully", NotifLevel.SUCCESS)
                state.set_error(None)
                state.set_failed_action(None, None)
                if on_success:
                    on_success()
                self.load_memory()
            except Exception as exc:
                rs, detail = classify_network_error(exc)
                state.set_runtime_state(rs, detail)
                user_msg = f"Memory save failed: {detail} (local offline queue is not configured; entry was not persisted)"
                state.set_error(user_msg)
                state.set_failed_action("Save memory", _do_post)

        _daemon(_do_post, "aetherion-add-memory")

    # ── Plugins ───────────────────────────────────────────────────────────────

    def load_plugins(self) -> None:
        def _fetch():
            try:
                # First load installed plugins from our real endpoint
                installed_res = self._get("/api/plugins")
                installed_items = installed_res.get("plugins", []) if isinstance(installed_res, dict) else []

                try:
                    marketplace = self._get("/api/marketplace/plugins")
                    mkt_items = marketplace if isinstance(marketplace, list) else marketplace.get("plugins", [])
                except Exception:
                    mkt_items = []

                seen = set()
                all_plugins: List[Plugin] = []

                for p in installed_items:
                    pid = str(p.get("id", p.get("name", "?")))
                    seen.add(pid)
                    all_plugins.append(Plugin(
                        id          = pid,
                        name        = str(p.get("name", pid)),
                        description = str(p.get("description", "")),
                        category    = str(p.get("category", "general")),
                        version     = str(p.get("version", "1.0")),
                        installed   = True,
                        enabled     = bool(p.get("enabled", True)),
                        author      = str(p.get("author", "")),
                    ))

                for p in mkt_items:
                    pid = str(p.get("id", p.get("name", "?")))
                    if pid not in seen:
                        all_plugins.append(Plugin(
                            id          = pid,
                            name        = str(p.get("name", pid)),
                            description = str(p.get("description", "")),
                            category    = str(p.get("category", "general")),
                            version     = str(p.get("version", "1.0")),
                            installed   = False,
                            author      = str(p.get("author", "")),
                        ))

                state.set_plugins(all_plugins)
            except Exception as exc:
                state.set_plugins([], error=str(exc))

        _daemon(_fetch, "aetherion-plugins")

    def install_plugin(self, plugin_id: str, plugin_name: str) -> None:
        """Install a plugin from the marketplace."""
        def _do():
            try:
                self._post("/api/plugins/install", {"name": plugin_id})
                state.push_notification(f"Plugin '{plugin_name}' installed", NotifLevel.SUCCESS)
                self.load_plugins()
            except Exception as exc:
                state.push_notification(f"Install failed: {exc}", NotifLevel.ERROR)
        _daemon(_do, "aetherion-plugin-install")

    def uninstall_plugin(self, plugin_id: str, plugin_name: str) -> None:
        """Uninstall a plugin."""
        def _do():
            try:
                self._delete(f"/api/plugins/{plugin_id}")
                state.push_notification(f"Plugin '{plugin_name}' uninstalled", NotifLevel.SUCCESS)
                self.load_plugins()
            except Exception as exc:
                state.push_notification(f"Uninstall failed: {exc}", NotifLevel.ERROR)
        _daemon(_do, "aetherion-plugin-uninstall")

    def toggle_plugin(self, plugin_id: str, plugin_name: str, enable: bool) -> None:
        """Enable or disable a plugin."""
        def _do():
            try:
                action = "enable" if enable else "disable"
                self._post(f"/api/plugins/{plugin_id}/{action}", {})
                verb = "enabled" if enable else "disabled"
                state.push_notification(f"Plugin '{plugin_name}' {verb}", NotifLevel.SUCCESS)
                self.load_plugins()
            except Exception as exc:
                state.push_notification(f"Toggle failed: {exc}", NotifLevel.ERROR)
        _daemon(_do, "aetherion-plugin-toggle")

    # ── Skills ────────────────────────────────────────────────────────────────

    def load_skills(self) -> None:
        def _fetch():
            try:
                from .skill_creator import get_skills_state, list_local_skills
                local_states = get_skills_state()

                # Load from real /api/skills endpoint if available
                skill_items = []
                try:
                    res = self._get("/api/skills")
                    skill_items = res.get("skills", []) if isinstance(res, dict) else []
                except Exception:
                    pass

                # Also merge local skills from ~/.aetherion/skills
                local = list_local_skills()
                seen = {s.get("name", "") for s in skill_items}
                for ls in local:
                    if ls["name"] not in seen:
                        skill_items.append(ls)

                skills = []
                for s in skill_items:
                    s_name = str(s.get("name", "?"))
                    # Local persistent state overrides or defaults to manifest
                    enabled_val = local_states.get(s_name, bool(s.get("enabled", s.get("is_enabled", True))))
                    skills.append(
                        Skill(
                            name        = s_name,
                            description = str(s.get("description", "")),
                            version     = str(s.get("version", "1.0")),
                            enabled     = enabled_val,
                            builtin     = bool(s.get("builtin", False)),
                        )
                    )
                state.set_skills(skills)
            except Exception as exc:
                state.set_skills([])

        _daemon(_fetch, "aetherion-skills")

    def enable_skill(self, name: str) -> None:
        """Enable a skill both in local persistent state and backend."""
        def _do():
            try:
                from .skill_creator import set_skill_enabled
                set_skill_enabled(name, True)
                try:
                    self._post(f"/api/skills/{name}/enable", {})
                except Exception:
                    pass
                state.push_notification(f"Skill '{name}' enabled", NotifLevel.SUCCESS)
                self.load_skills()
            except Exception as exc:
                state.push_notification(f"Failed to enable skill: {exc}", NotifLevel.ERROR)
        _daemon(_do, "aetherion-skill-enable")

    def disable_skill(self, name: str) -> None:
        """Disable a skill both in local persistent state and backend."""
        def _do():
            try:
                from .skill_creator import set_skill_enabled
                set_skill_enabled(name, False)
                try:
                    self._post(f"/api/skills/{name}/disable", {})
                except Exception:
                    pass
                state.push_notification(f"Skill '{name}' disabled", NotifLevel.SUCCESS)
                self.load_skills()
            except Exception as exc:
                state.push_notification(f"Failed to disable skill: {exc}", NotifLevel.ERROR)
        _daemon(_do, "aetherion-skill-disable")

    def install_skill(self, name: str) -> None:
        """Register an existing skill with the backend."""
        def _do():
            try:
                self._post("/api/skills/install", {"name": name})
                state.push_notification(f"Skill '{name}' registered", NotifLevel.SUCCESS)
                self.load_skills()
            except Exception as exc:
                state.push_notification(f"Skill registration failed: {exc}", NotifLevel.ERROR)
        _daemon(_do, "aetherion-skill-install")

    # ── Providers ─────────────────────────────────────────────────────────────

    def load_providers(self) -> None:
        def _fetch():
            try:
                # 1. Fetch user-configured providers from /api/providers
                user_provs = []
                try:
                    res = self._get("/api/providers")
                    user_provs = res.get("providers", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
                except Exception as exc:
                    log.debug("User providers fetch failed: %s", exc)

                # 2. Fetch default system providers from /api/p9/providers
                system_provs = []
                try:
                    res2 = self._get("/api/p9/providers")
                    system_provs = res2.get("providers", []) if isinstance(res2, dict) else (res2 if isinstance(res2, list) else [])
                except Exception as exc:
                    log.debug("System providers fetch failed: %s", exc)

                providers: List[Provider] = []
                seen_keys = set()

                for p in user_provs:
                    pid = str(p.get("id") or p.get("key") or p.get("name", ""))
                    seen_keys.add(pid)
                    seen_keys.add(p.get("name", ""))
                    providers.append(Provider(
                        key            = str(p.get("key") or pid),
                        name           = str(p.get("name", pid)),
                        model          = str(p.get("default_model") or p.get("model", "")),
                        available      = bool(p.get("is_enabled", True)),
                        latency        = p.get("latency_ms"),
                        quality        = str(p.get("quality", "A")),
                        cost           = str(p.get("cost", "$")),
                        error          = p.get("error"),
                        id             = str(p.get("id", pid)),
                        provider_type  = str(p.get("provider_type", "OPENAI_COMPATIBLE")),
                        base_url       = str(p.get("base_url", "")),
                        api_key_masked = str(p.get("api_key_masked") or p.get("api_key", "")),
                        is_default     = bool(p.get("is_default", False)),
                        is_enabled     = bool(p.get("is_enabled", True)),
                        configured     = True,
                    ))

                for p in system_provs:
                    pkey = str(p.get("key", p.get("provider", "?")))
                    pname = str(p.get("name", pkey))
                    if pkey not in seen_keys and pname not in seen_keys:
                        providers.append(Provider(
                            key            = pkey,
                            name           = pname,
                            model          = str(p.get("model", p.get("default_model", "?"))),
                            available      = bool(p.get("available", p.get("healthy", False))),
                            latency        = p.get("latency_ms"),
                            quality        = str(p.get("quality", "?")),
                            cost           = str(p.get("cost", "?")),
                            error          = p.get("error"),
                            id             = pkey,
                            provider_type  = "OPENAI_COMPATIBLE" if "openai" in pkey.lower() else "ANTHROPIC_COMPATIBLE" if "anthropic" in pkey.lower() else "CHAT_COMPATIBLE",
                            base_url       = str(p.get("base_url", "")),
                            api_key_masked = "",
                            is_default     = False,
                            is_enabled     = True,
                            configured     = False,
                        ))

                state.set_providers(providers)
            except Exception as exc:
                state.set_providers([])

        _daemon(_fetch, "aetherion-providers")

    def save_provider(self, data: dict, on_success: Optional[Callable[[], None]] = None) -> None:
        """Create or update a provider."""
        def _do():
            try:
                pid = data.get("id")
                if pid and not pid.startswith("new_") and not pid.startswith("prov_temp"):
                    # Update
                    res = self._put(f"/api/providers/{pid}", data)
                    state.push_notification(f"Provider '{data.get('name')}' updated", NotifLevel.SUCCESS)
                else:
                    # Create
                    payload = dict(data)
                    payload.pop("id", None)
                    res = self._post("/api/providers", payload)
                    state.push_notification(f"Provider '{data.get('name')}' created", NotifLevel.SUCCESS)
                if on_success:
                    on_success()
                self.load_providers()
            except Exception as exc:
                state.push_notification(f"Failed to save provider: {exc}", NotifLevel.ERROR)
        _daemon(_do, "aetherion-save-provider")

    def delete_provider(self, provider_id: str) -> None:
        """Delete a configured provider."""
        def _do():
            try:
                self._delete(f"/api/providers/{provider_id}")
                state.push_notification("Provider deleted", NotifLevel.SUCCESS)
                self.load_providers()
            except Exception as exc:
                state.push_notification(f"Failed to delete provider: {exc}", NotifLevel.ERROR)
        _daemon(_do, "aetherion-delete-provider")

    def test_provider(self, provider_id: str, on_result: Optional[Callable[[dict], None]] = None) -> None:
        """Run connection diagnostic test against a specific provider."""
        def _do():
            try:
                res = self._post(f"/api/providers/{provider_id}/test", {})
                ok = res.get("ok", False) or res.get("status") == "CONNECTED"
                msg = f"Provider test {'PASSED' if ok else 'FAILED'}: {res.get('status', 'ERROR')}"
                if res.get("latency_ms"):
                    msg += f" ({res['latency_ms']}ms)"
                state.push_notification(msg, NotifLevel.SUCCESS if ok else NotifLevel.ERROR)
                if on_result:
                    on_result(res)
                self.load_providers()
            except Exception as exc:
                state.push_notification(f"Provider test error: {exc}", NotifLevel.ERROR)
        _daemon(_do, "aetherion-test-provider")

    def set_default_provider(self, provider_id: str) -> None:
        """Mark a provider as default."""
        def _do():
            try:
                self._post(f"/api/providers/{provider_id}/default", {})
                state.push_notification("Default provider updated", NotifLevel.SUCCESS)
                self.load_providers()
            except Exception as exc:
                state.push_notification(f"Failed to set default provider: {exc}", NotifLevel.ERROR)
        _daemon(_do, "aetherion-default-provider")

    def toggle_provider(self, provider_id: str, enabled: bool) -> None:
        """Enable or disable a provider."""
        def _do():
            try:
                action = "enable" if enabled else "disable"
                self._post(f"/api/providers/{provider_id}/{action}", {})
                verb = "enabled" if enabled else "disabled"
                state.push_notification(f"Provider {verb}", NotifLevel.SUCCESS)
                self.load_providers()
            except Exception as exc:
                state.push_notification(f"Failed to toggle provider: {exc}", NotifLevel.ERROR)
        _daemon(_do, "aetherion-toggle-provider")

    # ── Background Jobs ───────────────────────────────────────────────────────

    def load_jobs(self) -> None:
        def _fetch():
            try:
                res = self._get("/api/scheduler/tasks")
                items = res if isinstance(res, list) else res.get("tasks", [])
                jobs = [
                    BackgroundJob(
                        id         = str(j.get("id", uuid.uuid4().hex[:8])),
                        name       = str(j.get("name", j.get("description", "Task"))),
                        status     = str(j.get("status", "?")),
                        created_at = float(j.get("created_at", time.time())),
                        updated_at = float(j.get("updated_at", time.time())),
                        progress   = int(j.get("progress", 0)),
                        result     = j.get("result"),
                    )
                    for j in items
                ]
                state.set_jobs(jobs)
            except Exception as exc:
                state.set_jobs([])

        _daemon(_fetch, "aetherion-jobs")

    # ── Git ───────────────────────────────────────────────────────────────────

    def load_git_status(self) -> None:
        """Load git status for current workspace."""
        def _fetch():
            try:
                res = self._get("/api/github/analyze")
                if res:
                    state.append_message(Message.system(
                        f"Git: {res.get('branch', '?')} "
                        f"({res.get('commits_ahead', 0)} ahead, "
                        f"{res.get('commits_behind', 0)} behind)"
                    ))
            except Exception:
                pass  # Git is optional

        _daemon(_fetch, "aetherion-git")

    # ── User profile ──────────────────────────────────────────────────────────

    def load_user_profile(self) -> None:
        def _fetch():
            try:
                res = self._get("/api/auth/me")
                state.set_user(
                    email   = res.get("email"),
                    plan    = res.get("plan", "Lite"),
                    user_id = str(res.get("id", "")),
                )
            except Exception:
                pass

        _daemon(_fetch, "aetherion-user")

    def update_profile(self, display_name: str, on_success: Optional[Callable[[], None]] = None) -> None:
        def _do():
            try:
                res = self._put("/api/auth/profile", {"display_name": display_name})
                if res.get("ok"):
                    state.push_notification("Profile updated successfully", NotifLevel.SUCCESS)
                    self.load_user_profile()
                    if on_success:
                        on_success()
                else:
                    err = res.get("error", "Profile update failed")
                    state.push_notification(err, NotifLevel.ERROR)
            except Exception as e:
                st, err_str = classify_network_error(e)
                state.set_runtime_state(st)
                state.push_notification(f"Profile update error: {err_str}", NotifLevel.ERROR)
        _daemon(_do, "aetherion-profile-update")

    # ── Personal API keys ──────────────────────────────────────────────────────

    def list_personal_api_keys(self) -> List[Dict[str, Any]]:
        """Fetch authoritative API keys for authenticated user from backend."""
        with self._lock:
            token = self._token
        if not token:
            from ..config import get_token
            token = get_token()

        if token and not state.offline_mode:
            try:
                with self._client(read=10.0) as http:
                    r = http.get(
                        self._url("/api/auth/api-keys"),
                        headers=self._headers(),
                    )
                    if r.is_success:
                        data = r.json()
                        keys = data.get("api_keys", [])
                        for k in keys:
                            k["type"] = "Server Key"
                        try:
                            key_file = Path.home() / ".aetherion" / "api_keys.json"
                            key_file.parent.mkdir(parents=True, exist_ok=True)
                            key_file.write_text(json.dumps(keys, indent=2), encoding="utf-8")
                        except Exception:
                            pass
                        return keys
            except Exception as exc:
                log.debug("Backend list_personal_api_keys failed: %s", exc)

        key_file = Path.home() / ".aetherion" / "api_keys.json"
        if not key_file.exists():
            return []
        try:
            raw_keys = json.loads(key_file.read_text(encoding="utf-8"))
            for k in raw_keys:
                if not k.get("type"):
                    kid = str(k.get("id", ""))
                    k["type"] = "Local / Offline" if (kid.startswith("dev_") or kid == "default") else "Server Key"
            return raw_keys
        except Exception:
            return []

    def generate_personal_api_key(self, label: str) -> Dict[str, Any]:
        """Issue an authoritative personal API key from backend."""
        with self._lock:
            token = self._token
        if not token:
            from ..config import get_token
            token = get_token()

        if token and not state.offline_mode:
            try:
                with self._client(read=10.0) as http:
                    r = http.post(
                        self._url("/api/auth/api-keys"),
                        json={"name": label},
                        headers=self._headers(),
                    )
                    if r.is_success:
                        data = r.json()
                        entry = data.get("api_key", {})
                        entry["type"] = "Server Key"
                        try:
                            key_file = Path.home() / ".aetherion" / "api_keys.json"
                            keys = []
                            if key_file.exists():
                                keys = json.loads(key_file.read_text(encoding="utf-8"))
                            keys.append({
                                "id": entry.get("id"),
                                "type": "Server Key",
                                "name": entry.get("name"),
                                "created": entry.get("created"),
                                "last_used": entry.get("last_used", "Never"),
                                "prefix": entry.get("prefix"),
                            })
                            key_file.parent.mkdir(parents=True, exist_ok=True)
                            key_file.write_text(json.dumps(keys, indent=2), encoding="utf-8")
                        except Exception:
                            pass
                        return entry
            except Exception as exc:
                log.debug("Backend generate_personal_api_key failed: %s", exc)

        key_file = Path.home() / ".aetherion" / "api_keys.json"
        keys = []
        if key_file.exists():
            try:
                keys = json.loads(key_file.read_text(encoding="utf-8"))
            except Exception:
                keys = []
        raw_token = f"aeth_live_{uuid.uuid4().hex}"
        prefix = f"aeth_{raw_token[10:14]}****"
        created_str = time.strftime("%Y-%m-%d %H:%M")
        entry = {
            "id": f"dev_{str(uuid.uuid4())[:8]}",
            "type": "Local / Offline",
            "name": label or "unnamed",
            "created": created_str,
            "last_used": "Never",
            "prefix": prefix,
            "key": raw_token
        }
        keys.append(entry)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(json.dumps(keys, indent=2), encoding="utf-8")
        return entry

    def revoke_personal_api_key(self, key_id_or_name: str) -> bool:
        """Revoke API key on backend and update local cache."""
        with self._lock:
            token = self._token
        if not token:
            from ..config import get_token
            token = get_token()

        revoked = False
        if token and not state.offline_mode:
            try:
                with self._client(read=10.0) as http:
                    r = http.delete(
                        self._url(f"/api/auth/api-keys/{key_id_or_name}"),
                        headers=self._headers(),
                    )
                    if r.is_success:
                        revoked = True
            except Exception as exc:
                log.debug("Backend revoke_personal_api_key failed: %s", exc)

        key_file = Path.home() / ".aetherion" / "api_keys.json"
        if key_file.exists():
            try:
                keys = json.loads(key_file.read_text(encoding="utf-8"))
                new_keys = [k for k in keys if k.get("id") != key_id_or_name and k.get("name") != key_id_or_name]
                if len(new_keys) != len(keys):
                    key_file.write_text(json.dumps(new_keys, indent=2), encoding="utf-8")
                    return True
            except Exception:
                pass
        return revoked


    # ── Doctor ────────────────────────────────────────────────────────────────

    def run_doctor(self, callback: Callable[[str], None]) -> None:
        """Run health checks and call callback with result string."""
        from ..session_store import get_store_status

        def _check():
            lines = [f"Aetherion Doctor — {self.base_url}\n{'─'*40}"]
            # Backend health
            try:
                t0 = time.monotonic()
                r  = self._get("/api/health")
                lat = int((time.monotonic() - t0) * 1000)
                lines.append(f"✓ Backend reachable  ({lat}ms)  version={r.get('version', '?')}")
            except Exception as e:
                lines.append(f"✗ Backend unreachable: {e}")

            # Auth
            token = get_token()
            lines.append("✓ Token configured" if token else "✗ No token — run `aetherion login`")

            # User
            try:
                u = self._get("/api/auth/me")
                lines.append(f"✓ Logged in as: {u.get('email', '?')}  plan={u.get('plan', '?')}")
            except Exception:
                lines.append("✗ Not authenticated (token may be expired)")

            # Providers
            try:
                ps = self._get("/api/models/status")
                ok = sum(1 for p in (ps if isinstance(ps, list) else ps.get("providers", [])) if p.get("available"))
                lines.append(f"✓ Providers: {ok} available")
            except Exception:
                lines.append("? Provider status unavailable")

            # Local session store
            try:
                ss = get_store_status()
                if ss["available"]:
                    lines.append(
                        f"✓ Local session store: {ss['session_count']} sessions, "
                        f"{ss['message_count']} messages, "
                        f"journal={ss['journal_mode']}  ({ss['path']})"
                    )
                else:
                    lines.append(f"✗ Session store unavailable: {ss.get('error', '?')}")
            except Exception as exc:
                lines.append(f"? Session store: {exc}")

            lines.append(f"✓ Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
            callback("\n".join(lines))

        _daemon(_check, "aetherion-doctor")

    # ── Context compression ────────────────────────────────────────────────────

    def compress_context(self, session_id: Optional[str] = None) -> None:
        """Request context compression from the backend."""
        sid = session_id or state.active_session_id
        if not sid:
            state.push_notification("No active session to compress", "warning")
            return

        def _do():
            try:
                self._post(f"/api/sessions/{sid}/compress")
                state.push_notification("Context compressed successfully", "success")
            except Exception as exc:
                state.push_notification(f"Compression failed: {exc}", "error")

        _daemon(_do, "aetherion-compress")

    def branch_session(self, session_id: Optional[str] = None, branch_name: str = "") -> None:
        """Branch the current session."""
        sid = session_id or state.active_session_id
        if not sid:
            state.push_notification("No active session to branch", "warning")
            return
        if not branch_name:
            state.push_notification("Branch name required", "warning")
            return

        def _do():
            try:
                res = self._post(f"/api/sessions/{sid}/branch", {"name": branch_name})
                new_sid = res.get("session_id")
                if new_sid:
                    state.push_notification(f"Session branched successfully", "success")
                    self.load_session(new_sid)
                else:
                    state.push_notification("Branch succeeded but no new ID returned", "warning")
            except Exception as exc:
                state.push_notification(f"Branch failed: {exc}", "error")

        _daemon(_do, "aetherion-branch")

    # ── Session persistence integration ───────────────────────────────────────

    def _persist_message(self, session_id: str, role: str, content: str,
                         tokens: int = 0) -> None:
        """Persist a message to the local SQLite session store (best-effort)."""
        try:
            from ..session_store import save_message
            save_message(session_id, role, content, tokens=tokens)
        except Exception as exc:
            log.debug("Failed to persist message: %s", exc)

    def _ensure_local_session(self, session_id: str, model: str) -> None:
        """Create local session record if it doesn't exist yet."""
        try:
            from ..session_store import create_session, get_session
            if not get_session(session_id):
                create_session(
                    session_id=session_id,
                    title=f"Session {session_id[:8]}",
                    model=model,
                )
        except Exception as exc:
            log.debug("Failed to create local session: %s", exc)

    # ── Session resume ────────────────────────────────────────────────────────

    def resume_session(self, session_id: str) -> None:
        """
        Resume a session from local storage into the TUI conversation view.

        Loads messages from the local SQLite store and replays them into state.
        The backend session state is NOT re-executed — this is display-only resume.
        """
        def _do():
            try:
                from ..session_store import get_messages, get_session
                meta = get_session(session_id)
                if not meta:
                    state.push_notification(
                        f"Session {session_id[:8]} not found in local store", "warning"
                    )
                    return

                msgs = get_messages(session_id, limit=200)
                if not msgs:
                    state.push_notification("Session has no messages to restore", "info")
                    return

                state.clear_messages()
                state.set_session(session_id)
                if meta.get("model"):
                    state.set_model(meta["model"])

                for m in msgs:
                    role    = m["role"]
                    content = m["content"]
                    ts      = m.get("ts", time.time())
                    tokens  = m.get("tokens", 0)
                    msg = Message(
                        id=uuid.uuid4().hex[:8],
                        role=role,
                        content=content,
                        timestamp=ts,
                        token_count=tokens or None,
                    )
                    state.append_message(msg)

                state.push_notification(
                    f"Resumed session {session_id[:8]} — {len(msgs)} messages restored",
                    "success"
                )
            except Exception as exc:
                log.error("resume_session failed: %s", exc)
                state.push_notification(f"Resume failed: {exc}", "error")

        _daemon(_do, "aetherion-resume")

    def list_local_sessions(self, query: Optional[str] = None) -> None:
        """Load local sessions from SQLite into state.sessions."""
        def _do():
            try:
                from ..session_store import list_sessions
                rows = list_sessions(limit=50, query=query)
                sessions = [
                    Session(
                        id=r["id"],
                        title=r.get("title") or f"Session {r['id'][:8]}",
                        status="done",
                        model=r.get("model", ""),
                        created_at=r.get("created_at"),
                        updated_at=r.get("updated_at"),
                    )
                    for r in rows
                ]
                state.set_sessions(sessions)
            except Exception as exc:
                state.set_sessions([], error=str(exc))

        _daemon(_do, "aetherion-local-sessions")


# Module-level singleton
gateway = Gateway()
