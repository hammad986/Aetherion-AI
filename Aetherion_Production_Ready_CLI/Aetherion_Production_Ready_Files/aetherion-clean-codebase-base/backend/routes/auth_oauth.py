"""
routes/auth_oauth.py — OAuth 2.0 routes (Google + GitHub).
Hardened for production with CSRF state protection, session creation, and frontend redirect.
"""
from __future__ import annotations

import os
import logging
from urllib.parse import quote as _quote, urlencode
from flask import Blueprint, request, jsonify, redirect
import requests as _req

from auth_system import token_required, create_session, get_or_create_oauth_user
from routes.auth_helpers import (
    _oauth_states,
    _oauth_states_lock,
    _nx_set_refresh_cookie,
    _get_oauth_redirect_base,
    logger,
)

auth_oauth_bp = Blueprint("auth_oauth", __name__)


def _authorize_device_code(device_code: str, user_id: str, email: str, name: str, tokens: dict) -> None:
    if not device_code:
        return
    try:
        from routes.auth_device import _get_device_code, _save_device_code
        user_role = "user"
        try:
            from repositories.user_repository import UserRepository
            user_obj = UserRepository.get_by_id(user_id)
            if user_obj and hasattr(user_obj, "role"):
                user_role = user_obj.role
        except Exception:
            pass

        entry = _get_device_code(device_code)
        if entry:
            entry.update({
                "status": "authorized",
                "access_token": tokens.get("access_token"),
                "refresh_token": tokens.get("refresh_token", ""),
                "user_id": user_id,
                "email": email,
                "name": name,
                "role": user_role,
            })
            _save_device_code(device_code, entry)
            logger.info("[OAuth] Authorized device code %s for user %s", device_code, email)
    except Exception as exc:
        logger.error("[OAuth] Failed to authorize device code %s: %s", device_code, exc)


def _deny_device_code(device_code: str) -> None:
    if not device_code:
        return
    try:
        from routes.auth_device import _get_device_code, _save_device_code
        entry = _get_device_code(device_code)
        if entry:
            entry["status"] = "denied"
            _save_device_code(device_code, entry)
            logger.info("[OAuth] Marked device code %s as denied", device_code)
    except Exception as exc:
        logger.error("[OAuth] Failed to deny device code %s: %s", device_code, exc)


# ─── Google OAuth ─────────────────────────────────────────────────────────────

@auth_oauth_bp.route("/api/auth/google", methods=["GET", "POST"])
def api_auth_google():
    """Initiates Google OAuth 2.0 Authorization Flow."""
    import secrets as _sec
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    if not client_id:
        return jsonify({"ok": False, "error": "Google OAuth not configured (GOOGLE_CLIENT_ID missing)"}), 503

    device_code = (request.args.get("device_code") or "").strip()
    state = _sec.token_hex(16)
    with _oauth_states_lock:
        _oauth_states[state] = {"provider": "google", "device_code": device_code}

    base = _get_oauth_redirect_base()
    redirect_uri = f"{base}/api/auth/google/callback"

    params = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    })
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@auth_oauth_bp.route("/api/auth/google/callback", methods=["GET"])
def api_auth_google_callback():
    """Handles Google OAuth callback, exchanges authorization code for tokens, and creates session."""
    code = request.args.get("code", "").strip()
    state = request.args.get("state", "").strip()
    error = request.args.get("error", "").strip()

    frontend_url = os.environ.get("FRONTEND_URL", "").rstrip("/")

    state_info = None
    with _oauth_states_lock:
        if state and state in _oauth_states:
            state_info = _oauth_states.pop(state, None)

    device_code = ""
    if isinstance(state_info, dict):
        device_code = state_info.get("device_code", "")

    if error:
        logger.warning("[OAuth/Google] Google returned error: %s", error)
        if device_code:
            _deny_device_code(device_code)
        target = f"{frontend_url}/login?auth_error={_quote(error)}" if frontend_url else f"/login?auth_error={_quote(error)}"
        return redirect(target)

    if not state or state_info is None:
        logger.warning("[OAuth/Google] Invalid or expired state: %s", state)
        target = f"{frontend_url}/login?auth_error=invalid_state" if frontend_url else "/login?auth_error=invalid_state"
        return redirect(target)

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    base = _get_oauth_redirect_base()
    redirect_uri = f"{base}/api/auth/google/callback"

    try:
        token_resp = _req.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=15)
        
        token_data = token_resp.json()
        access_token = token_data.get("access_token", "")
        if not access_token:
            err_desc = token_data.get("error_description", "token_exchange_failed")
            logger.error("[OAuth/Google] Token exchange failed: %s", token_data)
            if device_code:
                _deny_device_code(device_code)
            target = f"{frontend_url}/login?auth_error={_quote(err_desc)}" if frontend_url else f"/login?auth_error={_quote(err_desc)}"
            return redirect(target)

        user_resp = _req.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        user_info = user_resp.json()
        email = user_info.get("email", "").strip()
        name = user_info.get("name", email) or email

        if not email:
            logger.error("[OAuth/Google] No email returned by userinfo endpoint")
            if device_code:
                _deny_device_code(device_code)
            target = f"{frontend_url}/login?auth_error=no_email" if frontend_url else "/login?auth_error=no_email"
            return redirect(target)

        user_id = get_or_create_oauth_user(email, name, "google")
        from security import get_client_ip
        ip = get_client_ip(request)
        device_info = request.headers.get("User-Agent", "")[:255]
        tokens = create_session(user_id, device_info=device_info, ip_address=ip)

        if device_code:
            _authorize_device_code(device_code, user_id, email, name, tokens)

        target = f"{frontend_url}/?oauth=success" if frontend_url else "/?oauth=success"
        resp = redirect(target)
        return _nx_set_refresh_cookie(resp, tokens["refresh_token"])
    except Exception as e:
        logger.error("[OAuth/Google] Callback exception: %s", e)
        if device_code:
            _deny_device_code(device_code)
        target = f"{frontend_url}/login?auth_error=google_failed" if frontend_url else "/login?auth_error=google_failed"
        return redirect(target)


# ─── GitHub OAuth ─────────────────────────────────────────────────────────────

@auth_oauth_bp.route("/api/auth/github", methods=["GET", "POST"])
def api_auth_github():
    """Initiates GitHub OAuth 2.0 Authorization Flow."""
    import secrets as _sec
    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    if not client_id:
        return jsonify({"ok": False, "error": "GitHub OAuth not configured (GITHUB_CLIENT_ID missing)"}), 503

    device_code = (request.args.get("device_code") or "").strip()
    state = _sec.token_hex(16)
    with _oauth_states_lock:
        _oauth_states[state] = {"provider": "github", "device_code": device_code}

    base = _get_oauth_redirect_base()
    redirect_uri = f"{base}/api/auth/github/callback"

    params = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "read:user user:email",
        "state": state,
    })
    return redirect(f"https://github.com/login/oauth/authorize?{params}")


@auth_oauth_bp.route("/api/auth/github/callback", methods=["GET"])
def api_auth_github_callback():
    """Handles GitHub OAuth callback, exchanges authorization code for tokens, and creates session."""
    code = request.args.get("code", "").strip()
    state = request.args.get("state", "").strip()
    error = request.args.get("error", "").strip()

    frontend_url = os.environ.get("FRONTEND_URL", "").rstrip("/")

    state_info = None
    with _oauth_states_lock:
        if state and state in _oauth_states:
            state_info = _oauth_states.pop(state, None)

    device_code = ""
    if isinstance(state_info, dict):
        device_code = state_info.get("device_code", "")

    if error:
        logger.warning("[OAuth/GitHub] GitHub returned error: %s", error)
        if device_code:
            _deny_device_code(device_code)
        target = f"{frontend_url}/login?auth_error={_quote(error)}" if frontend_url else f"/login?auth_error={_quote(error)}"
        return redirect(target)

    if not state or state_info is None:
        target = f"{frontend_url}/login?auth_error=invalid_state" if frontend_url else "/login?auth_error=invalid_state"
        return redirect(target)

    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "").strip()
    base = _get_oauth_redirect_base()
    redirect_uri = f"{base}/api/auth/github/callback"

    try:
        token_resp = _req.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        gh_access_token = token_resp.json().get("access_token", "")
        if not gh_access_token:
            if device_code:
                _deny_device_code(device_code)
            target = f"{frontend_url}/login?auth_error=github_token_failed" if frontend_url else "/login?auth_error=github_token_failed"
            return redirect(target)

        user_resp = _req.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {gh_access_token}"},
            timeout=15,
        )
        user_info = user_resp.json()

        email = user_info.get("email", "")
        if not email:
            emails_resp = _req.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"Bearer {gh_access_token}"},
                timeout=15,
            )
            for e in emails_resp.json():
                if isinstance(e, dict) and e.get("primary") and e.get("email"):
                    email = e["email"]
                    break

        if not email:
            if device_code:
                _deny_device_code(device_code)
            target = f"{frontend_url}/login?auth_error=no_email" if frontend_url else "/login?auth_error=no_email"
            return redirect(target)

        name = user_info.get("name") or user_info.get("login") or email
        user_id = get_or_create_oauth_user(email, name, "github")

        from security import get_client_ip
        ip = get_client_ip(request)
        device_info = request.headers.get("User-Agent", "")[:255]
        tokens = create_session(user_id, device_info=device_info, ip_address=ip)

        if device_code:
            _authorize_device_code(device_code, user_id, email, name, tokens)

        target = f"{frontend_url}/?oauth=success" if frontend_url else "/?oauth=success"
        resp = redirect(target)
        return _nx_set_refresh_cookie(resp, tokens["refresh_token"])
    except Exception as e:
        logger.error("[OAuth/GitHub] callback error: %s", e)
        if device_code:
            _deny_device_code(device_code)
        target = f"{frontend_url}/login?auth_error=github_failed" if frontend_url else "/login?auth_error=github_failed"
        return redirect(target)
