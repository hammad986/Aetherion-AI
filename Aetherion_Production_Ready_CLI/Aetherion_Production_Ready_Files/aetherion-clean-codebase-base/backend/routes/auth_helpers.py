"""
routes/auth_helpers.py — Shared cookie, state, and logging helpers for auth sub-modules.
M3: extracted from auth_routes.py so all sub-blueprints can import without circular deps.
"""
import os
import threading
import logging

from flask import request

# ── Module-level shared state ──────────────────────────────────────────────────
try:
    from runtime.state import _oauth_states
except Exception:
    _oauth_states: dict = {}

_oauth_states_lock = threading.Lock()
logger = logging.getLogger("auth_routes")

# Phase 2 (P2-RUN-02): deletion requests now persist in execution.durable_state
# (kind="account_deletion") so they survive restarts and are visible to all workers.
# The old in-process _deletion_requests dict and _deletion_lock have been removed.

# ── Cookie constants ───────────────────────────────────────────────────────────
_NX_COOKIE      = "nx_refresh"
_NX_COOKIE_DAYS = int(os.environ.get("REFRESH_TOKEN_DAYS", "30"))


def _nx_cookie_secure() -> bool:
    configured = os.environ.get("REFRESH_COOKIE_SECURE")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return os.environ.get("FLASK_DEBUG", "0").strip() != "1"


def _nx_cookie_samesite() -> str:
    configured = os.environ.get("REFRESH_COOKIE_SAMESITE", "Lax").strip().title()
    if configured not in {"Lax", "Strict", "None"}:
        logger.warning("Invalid REFRESH_COOKIE_SAMESITE=%r; using Lax", configured)
        return "Lax"
    if configured == "None" and not _nx_cookie_secure():
        logger.warning("SameSite=None requires a Secure refresh cookie; using Lax")
        return "Lax"
    return configured


def _nx_set_refresh_cookie(resp, refresh_token: str):
    resp.set_cookie(
        _NX_COOKIE, refresh_token,
        max_age=_NX_COOKIE_DAYS * 24 * 3600,
        httponly=True,
        secure=_nx_cookie_secure(),
        samesite=_nx_cookie_samesite(),
        path="/",
    )
    return resp


def _nx_clear_refresh_cookie(resp):
    resp.delete_cookie(
        _NX_COOKIE,
        path="/",
        httponly=True,
        secure=_nx_cookie_secure(),
        samesite=_nx_cookie_samesite(),
    )
    return resp


def _nx_get_refresh_cookie() -> str:
    return request.cookies.get(_NX_COOKIE, "").strip()


def _get_oauth_redirect_base() -> str:
    domain = os.environ.get("REPLIT_DEV_DOMAIN", "")
    if domain:
        return f"https://{domain}"
    server_url = os.environ.get("SERVER_URL", "").rstrip("/")
    try:
        from core.environment import is_fail_closed_environment
        if is_fail_closed_environment():
            if not server_url:
                raise RuntimeError("SERVER_URL must be configured in production for OAuth redirects.")
            low = server_url.lower()
            if not low.startswith("https://") or "localhost" in low or "127.0.0.1" in low:
                raise RuntimeError(f"SERVER_URL must be a public HTTPS URL in production, got: {server_url}")
    except ImportError:
        pass

    if server_url:
        return server_url
    return request.host_url.rstrip("/")
