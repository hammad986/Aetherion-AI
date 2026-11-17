"""
routes/auth_core.py — Core authentication routes (signup, login, refresh, logout,
password change, profile, settings, sessions, current user).
M3: split from auth_routes.py (980 lines → 4 focused modules).
"""
import os
import time
import threading
import logging
from db.store_factory import get_store

from flask import Blueprint, request, jsonify, g

from auth_system import (
    token_required, auth_signup, auth_login, get_dashboard,
    create_session, refresh_access_token, revoke_session, revoke_session_by_id,
    revoke_all_sessions, list_sessions, revoke_token,
)
from routes.auth_helpers import (
    _nx_set_refresh_cookie, _nx_clear_refresh_cookie, _nx_get_refresh_cookie, logger,
)

auth_core_bp = Blueprint("auth_core", __name__)


# ─── Signup ───────────────────────────────────────────────────────────────────

@auth_core_bp.route("/api/auth/signup", methods=["POST"])
@auth_core_bp.route("/api/auth/register", methods=["POST"])
def api_auth_signup():
    from security import check_rate_limit, _auth_limiter, get_client_ip
    ok, wait = check_rate_limit(_auth_limiter, request)
    if not ok:
        return jsonify({"ok": False, "error": f"Rate limited. Retry in {wait}s"}), 429

    data       = request.json or {}
    identifier = (data.get("email") or data.get("username") or "").strip()
    password   = data.get("password", "")
    name       = data.get("name", "").strip()

    bootstrap_secret = os.environ.get("ADMIN_BOOTSTRAP_SECRET")
    header_secret = request.headers.get("X-Admin-Bootstrap")
    role = "admin" if (bootstrap_secret and header_secret == bootstrap_secret) else "user"

    success, result = auth_signup(identifier, password, name=name)
    if not success:
        return jsonify({"ok": False, "error": result}), 400

    user_id     = result
    if role == "admin":
        from auth_system import execute_query
        execute_query("UPDATE users SET role = 'admin' WHERE id = ?", (user_id,), commit=True)
    ip          = get_client_ip(request)
    device_info = request.headers.get("User-Agent", "")[:255]
    tokens      = create_session(user_id, device_info=device_info, ip_address=ip)
    resp = jsonify({"ok": True, "message": "Account created",
                    "access_token": tokens["access_token"],
                    "expires_in":   tokens["expires_in"],
                    "session_id":   tokens["session_id"]})
    return _nx_set_refresh_cookie(resp, tokens["refresh_token"])


# ─── Login ────────────────────────────────────────────────────────────────────

@auth_core_bp.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    from security import check_rate_limit, _auth_limiter, get_client_ip
    ok, wait = check_rate_limit(_auth_limiter, request)
    if not ok:
        return jsonify({"ok": False, "error": f"Rate limited. Retry in {wait}s"}), 429

    data       = request.json or {}
    identifier = (data.get("email") or data.get("username") or "").strip()
    password   = data.get("password", "")
    from security import get_client_ip
    ip = get_client_ip(request)

    success, result = auth_login(identifier, password, ip=ip)
    if not success:
        return jsonify({"ok": False, "error": result}), 401

    user_id     = result
    device_info = request.headers.get("User-Agent", "")[:255]
    tokens      = create_session(user_id, device_info=device_info, ip_address=ip)
    user_info = {}
    try:
        from repositories.user_repository import UserRepository
        user_obj = UserRepository.get_by_id(user_id)
        if user_obj:
            user_info = {
                "id": str(user_obj.id),
                "email": user_obj.email,
                "name": getattr(user_obj, "name", ""),
                "plan": getattr(user_obj, "plan", "Lite"),
            }
    except Exception:
        pass

    resp = jsonify({
        "ok":            True,
        "access_token":  tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "expires_in":    tokens["expires_in"],
        "session_id":    tokens["session_id"],
        "user":          user_info,
    })
    return _nx_set_refresh_cookie(resp, tokens["refresh_token"])


# ─── Refresh ──────────────────────────────────────────────────────────────────

@auth_core_bp.route("/api/auth/refresh", methods=["POST"])
def api_auth_refresh():
    refresh_token = _nx_get_refresh_cookie()
    if not refresh_token:
        data = request.get_json(silent=True) or {}
        refresh_token = data.get("refresh_token", "").strip()
    if not refresh_token:
        return jsonify({"ok": False, "error": "No refresh token"}), 401

    from security import get_client_ip
    ip = get_client_ip(request)
    success, result = refresh_access_token(refresh_token, ip_address=ip)
    if not success:
        err_resp = jsonify({"ok": False, "error": result})
        return _nx_clear_refresh_cookie(err_resp), 401

    # Enrich response with user info and rolling refresh token for CLI clients
    user_info = {}
    try:
        from repositories.user_repository import UserRepository
        from auth_system import decode_token
        payload = decode_token(result["access_token"])
        uid = payload.get("user_id") or payload.get("sub")
        if uid:
            user_obj = UserRepository.get_by_id(uid)
            if user_obj:
                user_info = {
                    "id": str(user_obj.id),
                    "email": user_obj.email,
                    "name": getattr(user_obj, "name", ""),
                    "plan": getattr(user_obj, "plan", "Lite"),
                }
    except Exception:
        pass

    resp = jsonify({
        "ok":            True,
        "access_token":  result["access_token"],
        "refresh_token": result["refresh_token"],
        "expires_in":    result["expires_in"],
        "user":          user_info,
    })
    return _nx_set_refresh_cookie(resp, result["refresh_token"])


# ─── Logout ───────────────────────────────────────────────────────────────────

@auth_core_bp.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    # 1. Revoke access token if provided (OWASP ASVS 3.3.1 compliance)
    auth_header = request.headers.get("Authorization", "").strip()
    data = request.get_json(silent=True) or {}
    access_token = None
    if auth_header.startswith("Bearer "):
        access_token = auth_header[7:].strip()
    elif "access_token" in data:
        access_token = str(data.get("access_token") or "").strip()
    elif "token" in data:
        access_token = str(data.get("token") or "").strip()

    if access_token:
        try:
            revoke_token(access_token)
        except Exception as e:
            logger.warning("[AUTH] Failed to revoke access token: %s", e)

    # 2. Revoke refresh token
    refresh_token = _nx_get_refresh_cookie()
    if not refresh_token:
        refresh_token = data.get("refresh_token", "").strip()
    if refresh_token:
        try:
            revoke_session(refresh_token)
        except Exception as e:
            logger.warning("[AUTH] Failed to revoke session: %s", e)

    resp = jsonify({"ok": True, "message": "Logged out"})
    return _nx_clear_refresh_cookie(resp)


@auth_core_bp.route("/api/auth/logout_everywhere", methods=["POST"])
def api_auth_logout_everywhere():
    """Revoke ALL sessions for this user (logout from all devices)."""
    from auth_system import token_required, execute_query, revoke_token, revoke_all_sessions
    # Support both bearer token and cookie-based auth
    auth_header = request.headers.get("Authorization", "")
    user_id = None
    caller_token = None
    if auth_header.startswith("Bearer "):
        caller_token = auth_header[7:].strip()
        try:
            from auth_system import verify_token
            payload = verify_token(caller_token)
            if payload:
                user_id = payload.get("user_id") or payload.get("sub")
            revoke_token(caller_token)
        except Exception:
            pass
    if not user_id:
        # Try refresh token
        refresh_token = _nx_get_refresh_cookie()
        if not refresh_token:
            data = request.get_json(silent=True) or {}
            refresh_token = data.get("refresh_token", "")
        if refresh_token:
            try:
                row = execute_query(
                    "SELECT user_id FROM auth_sessions WHERE refresh_token = ?",
                    (refresh_token,), fetchone=True
                )
                if row:
                    user_id = row[0]
            except Exception:
                pass
    if user_id:
        try:
            revoke_all_sessions(user_id)
        except Exception as e:
            logger.warning("[AUTH] Failed to revoke all sessions for user %s: %s", user_id, e)
    if caller_token:
        try:
            revoke_token(caller_token)
        except Exception:
            pass
    resp = jsonify({"ok": True, "message": "All sessions revoked"})
    return _nx_clear_refresh_cookie(resp)


# ─── Change Password ──────────────────────────────────────────────────────────

@auth_core_bp.route("/api/auth/change-password", methods=["POST"])
@token_required
def api_auth_change_password():
    if getattr(g, "auth_method", "") == "api_key":
        return jsonify({"ok": False, "error": "API keys cannot change account passwords. Interactive session required."}), 403
    import bcrypt as _bcrypt
    from auth_system import execute_query

    data         = request.json or {}
    old_password = data.get("old_password", "")
    new_password = data.get("new_password", "")

    if not old_password or not new_password:
        return jsonify({"ok": False, "error": "Both current and new passwords are required"}), 400
    if len(new_password) < 8:
        return jsonify({"ok": False, "error": "New password must be at least 8 characters"}), 400

    row = execute_query("SELECT password FROM users WHERE id = ?", (g.user_id,), fetchone=True)
    if not row or not row[0]:
        return jsonify({"ok": False, "error": "Password change is not available for OAuth accounts"}), 400

    if not _bcrypt.checkpw(old_password.encode("utf-8"), row[0].encode("utf-8")):
        return jsonify({"ok": False, "error": "Current password is incorrect"}), 401

    new_hash = _bcrypt.hashpw(new_password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
    now = time.time()
    execute_query("UPDATE users SET password = ?, token_revoked_at = ? WHERE id = ?", (new_hash, now, g.user_id), commit=True)

    current_refresh = _nx_get_refresh_cookie()
    if current_refresh:
        execute_query(
            "DELETE FROM auth_sessions WHERE user_id = ? AND refresh_token != ?",
            (g.user_id, current_refresh), commit=True
        )
    else:
        execute_query("DELETE FROM auth_sessions WHERE user_id = ?", (g.user_id,), commit=True)

    return jsonify({"ok": True, "message": "Password updated. All other devices have been signed out."})


# ─── Update Profile ───────────────────────────────────────────────────────────

@auth_core_bp.route("/api/auth/profile", methods=["PUT"])
@token_required
def api_auth_update_profile():
    from auth_system import execute_query
    data = request.json or {}
    name = data.get("display_name", "").strip()
    if not name:
        name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Name cannot be empty"}), 400
    try:
        execute_query("UPDATE users SET username = ? WHERE id = ?", (name, g.user_id), commit=True)
    except Exception as e:
        return jsonify({"ok": False, "error": "Username taken or invalid"}), 400
    return jsonify({"ok": True, "message": "Profile updated successfully"})

# ─── Settings ─────────────────────────────────────────────────────────────────

@auth_core_bp.route("/api/auth/settings", methods=["GET"])
@token_required
def api_auth_get_settings():
    from auth_system import get_user_settings
    settings = get_user_settings(g.user_id)
    return jsonify({"ok": True, "settings": settings})


@auth_core_bp.route("/api/auth/settings", methods=["PUT"])
@token_required
def api_auth_update_settings():
    from auth_system import update_user_settings
    data = request.json or {}
    update_user_settings(g.user_id, data)
    return jsonify({"ok": True, "message": "Settings updated"})


# ─── Delete Account ───────────────────────────────────────────────────────────

@auth_core_bp.route("/api/auth/delete-account", methods=["POST"])
@token_required
def api_auth_delete_account():
    import bcrypt as _bcrypt
    import sqlite3 as _sqlite3

    data     = request.json or {}
    password = data.get("password", "")
    if not password:
        return jsonify({"ok": False, "error": "Password confirmation required"}), 400

    conn = get_store("saas_platform.db")
    row = conn.fetchone("SELECT password, provider FROM users WHERE id = ?", (g.user_id,))
    if not row:
        conn.close()
        return jsonify({"ok": False, "error": "User not found"}), 404

    pw_hash, provider = row.get("password"), (row.get("provider") or "local")
    if provider == "local":
        if not pw_hash or not _bcrypt.checkpw(password.encode("utf-8"), pw_hash.encode("utf-8")):
            conn.close()
            return jsonify({"ok": False, "error": "Incorrect password"}), 401
    else:
        if password.strip().upper() != "DELETE":
            conn.close()
            return jsonify({"ok": False, "error": 'Type DELETE to confirm'}), 400

    uid = g.user_id
    conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (uid,))
    for tbl in ("password_resets", "email_verifications"):
        try:
            conn.execute(f"DELETE FROM {tbl} WHERE user_id = ?", (uid,))
        except Exception:
            pass
    conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    conn.commit()
    conn.close()

    try:
        sconn = get_store("sessions.db")
        sconn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
        sconn.commit()
        sconn.close()
    except Exception:
        logger.error("Swallowed exception intercepted", exc_info=True)

    resp = jsonify({"ok": True, "message": "Account permanently deleted"})
    return _nx_clear_refresh_cookie(resp)


# ─── Logout All ───────────────────────────────────────────────────────────────

@auth_core_bp.route("/api/auth/logout-all", methods=["POST"])
@token_required
def api_auth_logout_all():
    revoke_all_sessions(g.user_id)
    return jsonify({"ok": True, "message": "All sessions revoked"})


# ─── Session management ───────────────────────────────────────────────────────

@auth_core_bp.route("/api/auth/sessions", methods=["GET"])
@token_required
def api_auth_sessions():
    sessions = list_sessions(g.user_id)
    return jsonify({"ok": True, "sessions": sessions})


@auth_core_bp.route("/api/auth/sessions/<session_id>", methods=["DELETE"])
@token_required
def api_auth_revoke_session(session_id):
    revoke_session_by_id(session_id, g.user_id)
    return jsonify({"ok": True, "message": "Session revoked"})


# ─── Current User ─────────────────────────────────────────────────────────────

@auth_core_bp.route("/api/auth/me", methods=["GET"])
@token_required
def api_auth_me():
    from auth_system import get_dashboard
    data = get_dashboard(g.user_id)
    if not data or not data.get("user_id"):
        return jsonify({"ok": False, "error": "User no longer exists", "code": "USER_NOT_FOUND"}), 401
        
    return jsonify({
        "ok": True,
        "user": data,
        "data": data,
        "id": data.get("user_id"),
        "email": data.get("email"),
        "name": data.get("name"),
    })


@auth_core_bp.route("/api/user/dashboard", methods=["GET"])
@token_required
def api_user_dashboard():
    data = get_dashboard(g.user_id)
    return jsonify({"ok": True, "data": data})


# ─── Personal API Keys ────────────────────────────────────────────────────────

@auth_core_bp.route("/api/auth/api-keys", methods=["GET"])
@token_required
def api_auth_list_api_keys():
    from auth_system import list_api_keys
    keys = list_api_keys(g.user_id)
    return jsonify({"ok": True, "api_keys": keys})


@auth_core_bp.route("/api/auth/api-keys", methods=["POST"])
@token_required
def api_auth_create_api_key():
    if getattr(g, "auth_method", "") == "api_key":
        return jsonify({"ok": False, "error": "API keys cannot be used to issue new API keys. Interactive session required."}), 403

    from auth_system import create_api_key
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or data.get("label") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "API key label is required"}), 400

    ok, res = create_api_key(g.user_id, name)
    if not ok:
        return jsonify({"ok": False, "error": res}), 400

    return jsonify({"ok": True, "api_key": res}), 201


@auth_core_bp.route("/api/auth/api-keys/<key_id>", methods=["DELETE", "POST"])
@token_required
def api_auth_revoke_api_key(key_id: str):
    if getattr(g, "auth_method", "") == "api_key":
        return jsonify({"ok": False, "error": "API keys cannot modify or revoke credentials. Interactive session required."}), 403

    from auth_system import revoke_api_key
    ok = revoke_api_key(g.user_id, key_id)
    if not ok:
        return jsonify({"ok": False, "error": "API key not found or already revoked"}), 404

    return jsonify({"ok": True, "message": f"API key {key_id} revoked"})
