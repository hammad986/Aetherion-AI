"""
aetherion_cli/tui/screens/welcome.py
Welcome / Login screen.

Auth flow:
  1. TUI starts → silent_login() via stored refresh token → Dashboard (if ok)
  2. User clicks Login → device_request → browser opens → poll → complete_device_auth → Dashboard
  3. User clicks Continue Offline → offline mode

OAuth (Google / GitHub) flows use the same device poll endpoint because
the /activate page handles OAuth callbacks server-side and then calls
/api/auth/device/confirm with the resulting access token.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import webbrowser

from textual.app import ComposeResult
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Button, Label

from aetherion_cli.tui.gateway import gateway
from aetherion_cli.tui.state import Screen as AppScreen
from aetherion_cli.tui.state import state
from aetherion_cli.tui.theme import Colors, Glyphs


def _open_browser(url: str) -> bool:
    """Multi-tier browser launch ensuring automatic foreground browser open on all platforms."""
    opened = False
    if sys.platform == "win32":
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            opened = True
        except Exception:
            opened = False
        if not opened:
            try:
                subprocess.Popen(["cmd.exe", "/c", "start", "", url], shell=False)
                opened = True
            except Exception:
                pass
    elif sys.platform == "darwin":
        try:
            subprocess.Popen(["open", url])
            opened = True
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        try:
            subprocess.Popen(["xdg-open", url])
            opened = True
        except Exception:
            pass

    if not opened:
        try:
            opened = bool(webbrowser.open(url, new=2))
        except Exception:
            opened = False
    return opened


class WelcomeScreen(Widget):
    DEFAULT_CSS = """
    WelcomeScreen {
        width: 1fr;
        height: 1fr;
        align: center middle;
        background: #0a0c12;
    }
    #welcome-box {
        width: 62;
        height: auto;
        border: solid #3b82f6;
        padding: 2 4;
        background: #111827;
        align: center middle;
    }
    #welcome-title {
        text-style: bold;
        color: white;
        margin-bottom: 1;
        content-align: center middle;
        width: 100%;
    }
    #welcome-subtitle {
        color: #9ca3af;
        margin-bottom: 2;
        content-align: center middle;
        width: 100%;
    }
    Button {
        width: 100%;
        margin-bottom: 1;
    }
    #btn-login {
        background: #3b82f6;
        color: white;
        text-style: bold;
    }
    #btn-google {
        background: #dc2626;
        color: white;
    }
    #btn-github {
        background: #1f2937;
        color: white;
    }
    #btn-offline {
        background: #4b5563;
        color: white;
    }
    #btn-cancel {
        background: #b91c1c;
        color: white;
        display: none;
    }
    #btn-cancel.visible {
        display: block;
    }
    #auth-status {
        color: #f59e0b;
        content-align: center middle;
        margin-top: 1;
        height: auto;
        display: none;
    }
    #user-code-box {
        color: #a78bfa;
        text-style: bold;
        content-align: center middle;
        margin-top: 1;
        display: none;
        border: dashed #6d28d9;
        padding: 0 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="welcome-box"):
            yield Label(f"{Glyphs.AETHERION_LOGO} Aetherion", id="welcome-title")
            yield Label("Authenticate to access your cloud workspace", id="welcome-subtitle")
            yield Button(f"{Glyphs.OPEN_LOCK}  Login / Register", id="btn-login")
            yield Button("  Google OAuth", id="btn-google")
            yield Button("  GitHub OAuth", id="btn-github")
            yield Button(f"{Glyphs.DASH} Continue Offline", id="btn-offline")
            yield Button(f"{Glyphs.ERROR} Cancel Authorization", id="btn-cancel")
            yield Button(f"{Glyphs.ERROR} Exit", id="btn-exit")
            yield Label("", id="auth-status")
            yield Label("", id="user-code-box")

    def on_mount(self) -> None:
        """Try silent login from stored refresh token before showing buttons."""
        self.run_worker(self._try_silent_login(), exclusive=True, name="silent-login")

    async def _try_silent_login(self) -> None:
        """Attempt silent re-auth in background. Navigate to saved screen or chat on success."""
        self._set_status("Checking saved credentials…", show=True)
        ok = await asyncio.get_event_loop().run_in_executor(None, gateway.silent_login)
        if ok:
            self._set_status("")
            target = getattr(state, "saved_screen", None) or AppScreen.CHAT
            state.navigate(target)
        else:
            self._set_status("")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-exit":
            self.app.exit()
        elif btn_id == "btn-offline":
            state.set_offline_mode(True)
            target = getattr(state, "saved_screen", None) or AppScreen.CHAT
            state.navigate(target)
        elif btn_id == "btn-login":
            self._start_device_auth("login")
        elif btn_id == "btn-google":
            self._start_oauth("google")
        elif btn_id == "btn-github":
            self._start_oauth("github")
        elif btn_id == "btn-cancel":
            self._cancel_auth()

    # ── Device Auth Flow ──────────────────────────────────────────────────────

    def _set_auth_in_progress(self, active: bool) -> None:
        for bid in ("#btn-login", "#btn-google", "#btn-github", "#btn-offline"):
            try:
                self.query_one(bid, Button).disabled = active
            except Exception:
                pass
        try:
            cancel_btn = self.query_one("#btn-cancel", Button)
            if active:
                cancel_btn.add_class("visible")
                cancel_btn.disabled = False
            else:
                cancel_btn.remove_class("visible")
        except Exception:
            pass

    def _cancel_auth(self) -> None:
        try:
            for worker in self.workers:
                if worker.name in ("device-auth", "oauth-google", "oauth-github"):
                    worker.cancel()
        except Exception:
            pass
        self._set_auth_in_progress(False)
        self._set_user_code("")
        self._set_status("[yellow]Authorization cancelled.[/yellow]")

    def _set_status(self, msg: str, show: bool = True) -> None:
        try:
            lbl = self.query_one("#auth-status", Label)
            lbl.update(msg)
            lbl.styles.display = "block" if show and msg else "none"
        except Exception:
            pass

    def _set_user_code(self, code: str) -> None:
        try:
            box = self.query_one("#user-code-box", Label)
            if code:
                box.update(f"Your code: {code}")
                box.styles.display = "block"
            else:
                box.styles.display = "none"
        except Exception:
            pass

    def _start_device_auth(self, action: str = "login") -> None:
        self._set_auth_in_progress(True)
        self._set_status("Requesting authorization code…")
        self.run_worker(
            self._device_auth_worker(action),
            exclusive=True,
            name="device-auth"
        )

    def _start_oauth(self, provider: str) -> None:
        """
        Open browser directly to the OAuth initiate endpoint.
        The server handles the callback and confirms the device code.
        """
        self._set_auth_in_progress(True)
        self._set_status(f"Opening {provider.title()} OAuth…")
        self.run_worker(
            self._oauth_device_worker(provider),
            exclusive=True,
            name=f"oauth-{provider}"
        )

    def _format_auth_error(self, raw_err: str) -> str:
        from ..gateway import classify_network_error
        from aetherion_cli.config import get_server_url
        server_url = get_server_url().rstrip('/')
        rs, detail = classify_network_error(raw_err)
        err_lower = str(raw_err).lower()
        if rs.value == "OFFLINE" or "10061" in str(raw_err) or "connection refused" in err_lower or "target machine actively refused" in err_lower:
            return f"Cannot reach backend at {server_url}.\nEnsure backend is running (`python app.py`) or select 'Continue Offline'."
        return f"Error: {detail}"

    async def _oauth_device_worker(self, provider: str) -> None:
        """Request a device code, then open browser to OAuth provider."""
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, lambda: gateway.device_request("login"))
        if "error" in res:
            self._auth_failure(self._format_auth_error(res['error']))
            return

        device_code = res.get("device_code")
        user_code = res.get("user_code", "")
        interval = int(res.get("interval", 3))
        expires_in = int(res.get("expires_in", 300))

        # Build provider-specific URL through our backend redirect
        from aetherion_cli.config import get_server_url
        base = get_server_url().rstrip("/")
        oauth_url = f"{base}/api/auth/{provider}?device_code={device_code}"
        await loop.run_in_executor(None, _open_browser, oauth_url)

        self._set_status(
            f"Browser opened → authenticate with {provider.title()}.\n"
            f"Waiting for authorization…",
        )
        self._set_user_code(user_code)
        await self._poll_loop(device_code, interval, expires_in)

    async def _device_auth_worker(self, action: str) -> None:
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, lambda: gateway.device_request(action))
        if "error" in res:
            self._auth_failure(self._format_auth_error(res['error']))
            return

        verification_uri = res.get("verification_uri_complete") or res.get("verification_uri")
        device_code = res.get("device_code")
        user_code = res.get("user_code", "")
        interval = int(res.get("interval", 3))
        expires_in = int(res.get("expires_in", 300))

        if not verification_uri or not device_code:
            self._auth_failure("Invalid response from auth server.")
            return

        await loop.run_in_executor(None, _open_browser, verification_uri)

        self._set_status(
            f"Browser opened → complete login there.\n"
            f"Or visit the URL and enter your code below:",
        )
        self._set_user_code(user_code)
        await self._poll_loop(device_code, interval, expires_in)

    async def _poll_loop(self, device_code: str, interval: int, expires_in: int) -> None:
        import time
        loop = asyncio.get_event_loop()
        start = time.monotonic()
        consecutive_errors = 0
        try:
            while time.monotonic() - start < expires_in:
                poll = await loop.run_in_executor(None, lambda: gateway.device_poll(device_code))
                status = poll.get("status")

                if status == "authorized":
                    access_token = poll.get("access_token", "")
                    refresh_token = poll.get("refresh_token", "")
                    user = poll.get("user", {})
                    # Persist and update state
                    await loop.run_in_executor(
                        None,
                        lambda: gateway.complete_device_auth(access_token, refresh_token, user)
                    )
                    self._auth_success()
                    return
                elif status == "denied":
                    self._auth_failure("Authentication denied.")
                    return
                elif status in ("expired", "expired_token"):
                    self._auth_failure("Session expired. Try again.")
                    return
                elif status == "error":
                    consecutive_errors += 1
                    if consecutive_errors >= 5:
                        self._auth_failure(f"Auth error: {self._format_auth_error(poll.get('error', 'Unknown'))}")
                        return
                    await asyncio.sleep(interval)
                    continue

                consecutive_errors = 0
                # status == "pending" — keep polling
                await asyncio.sleep(interval)

            self._auth_failure("Authentication timed out.")
        except asyncio.CancelledError:
            pass

    def _auth_success(self) -> None:
        self._set_auth_in_progress(False)
        self._set_user_code("")
        self._set_status("")
        state.set_offline_mode(False)
        target = getattr(state, "saved_screen", None) or AppScreen.CHAT
        state.navigate(target)

    def _auth_failure(self, msg: str) -> None:
        self._set_auth_in_progress(False)
        self._set_user_code("")
        self._set_status(f"[red]{msg}[/red]")
