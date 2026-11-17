"""
aetherion_cli/auth/device_flow.py
Terminal-friendly Device Authorization Flow (RFC 8628).
"""
from __future__ import annotations

import time
import webbrowser
from typing import Any, Dict, Optional

import httpx

from .token_store import TokenStore
from ..output.formatters import stderr_console, stdout_console


class DeviceAuthFlow:
    """Manages the OAuth 2.0 / RFC 8628 device authorization exchange."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def initiate(self, client_id: str = "aetherion-cli") -> Dict[str, Any]:
        """Request a new device and user verification code."""
        url = f"{self.base_url}/api/auth/device/request"
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json={"client_id": client_id})
            if not resp.is_success:
                raise RuntimeError(f"Device auth request failed ({resp.status_code}): {resp.text}")
            return resp.json()

    def poll(
        self,
        device_code: str,
        interval: int = 5,
        expires_in: int = 300,
        quiet: bool = False,
    ) -> Dict[str, Any]:
        """Poll the server until user authorizes the code or it expires."""
        url = f"{self.base_url}/api/auth/device/poll"
        deadline = time.time() + expires_in
        poll_interval = max(interval, 2)

        while time.time() < deadline:
            time.sleep(poll_interval)
            try:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(url, json={"device_code": device_code})
                    if resp.status_code == 200:
                        data = resp.json()
                        token = data.get("access_token") or data.get("token")
                        if token:
                            storage = TokenStore.set_access_token(token)
                            rt = data.get("refresh_token")
                            if rt:
                                TokenStore.set_refresh_token(rt)
                            return {
                                "success": True,
                                "storage": storage,
                                "user": data.get("user"),
                            }
                        elif data.get("status") in ("pending", "authorization_pending"):
                            continue
                        elif data.get("status") == "denied":
                            return {"success": False, "error": "Authorization was declined by the user."}
                        elif data.get("status") == "expired":
                            return {"success": False, "error": "Device authorization code has expired."}
                    elif resp.status_code in (400, 428):
                        # Still pending authorization
                        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                        err = body.get("error") or ""
                        if err == "authorization_declined":
                            return {"success": False, "error": "Authorization was declined by the user."}
                        elif err == "expired_token":
                            return {"success": False, "error": "Device authorization code has expired."}
                        # Otherwise authorization_pending; continue polling
                        continue
                    elif resp.status_code == 429:
                        poll_interval += 2
                        continue
                    else:
                        return {"success": False, "error": f"Polling error ({resp.status_code}): {resp.text}"}
            except Exception as e:
                if not quiet:
                    stderr_console.print(f"[dim]Poll attempt failed: {e}; retrying...[/dim]")

        return {"success": False, "error": "Device authorization timed out. Please run login again."}
