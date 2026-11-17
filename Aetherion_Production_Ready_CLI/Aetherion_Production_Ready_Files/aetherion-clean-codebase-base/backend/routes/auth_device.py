"""
routes/auth_device.py  Device Authorization Flow (GitHub CLI / Vercel style)
=============================================================================
POST /api/auth/device/request   -> issues device_code + user_code
GET  /api/auth/device/poll      -> polls authorization status
POST /api/auth/device/confirm   -> browser page calls this after user logs in
POST /api/auth/device/deny      -> user denies in browser
"""
from __future__ import annotations
import logging, os, secrets, time, json
from typing import Any, Dict, Optional
from flask import Blueprint, jsonify, request
from redis_layer import get_nx_redis

log = logging.getLogger(__name__)
auth_device_bp = Blueprint("auth_device", __name__, url_prefix="/api/auth/device")

_DEVICE_CODE_TTL = int(os.environ.get("DEVICE_CODE_TTL_SECS", "900"))
_REDIS_PREFIX = "device_auth:"

def _make_user_code() -> str:
    """Generate a human-friendly code like ABCD-1234 (no ambiguous chars)."""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    part1 = "".join(secrets.choice(chars) for _ in range(4))
    part2 = "".join(secrets.choice(chars) for _ in range(4))
    return f"{part1}-{part2}"

def _save_device_code(device_code: str, entry: dict):
    redis_client = get_nx_redis()
    redis_client.set(f"{_REDIS_PREFIX}{device_code}", json.dumps(entry), ex=_DEVICE_CODE_TTL)
    uc = entry.get("user_code")
    if uc:
        redis_client.set(f"{_REDIS_PREFIX}code:{uc.upper()}", device_code, ex=_DEVICE_CODE_TTL)

def _get_device_code(device_code: str) -> Optional[dict]:
    redis_client = get_nx_redis()
    val = redis_client.get(f"{_REDIS_PREFIX}{device_code}")
    if val:
        return json.loads(val)
    return None

def _delete_device_code(device_code: str):
    redis_client = get_nx_redis()
    entry = _get_device_code(device_code)
    if entry and entry.get("user_code"):
        redis_client.delete(f"{_REDIS_PREFIX}code:{entry['user_code'].upper()}")
    redis_client.delete(f"{_REDIS_PREFIX}{device_code}")


@auth_device_bp.post("/request")
def device_request():
    """Issue a device_code + user_code pair for browser auth."""
    body = request.get_json(silent=True) or {}
    action = body.get("action", "login")
    if action not in ("login", "register"):
        action = "login"

    device_code = secrets.token_hex(32)
    user_code = _make_user_code()
    expires_at = time.time() + _DEVICE_CODE_TTL

    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")
    verification_uri = f"{frontend_url}/login?user_code={user_code}&device_code={device_code}&action={action}"
    verification_uri_complete = f"{frontend_url}/login?user_code={user_code}&device_code={device_code}&action={action}"

    entry = {
        "user_code": user_code, "action": action, "status": "pending",
        "access_token": None, "refresh_token": None,
        "user_id": None, "email": None, "name": None, "role": None,
        "created_at": time.time(), "expires_at": expires_at,
    }
    
    _save_device_code(device_code, entry)

    log.info("[DeviceAuth] Issued device authorization request action=%s", action)
    return jsonify({
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": verification_uri,
        "verification_uri_complete": verification_uri_complete,
        "expires_in": _DEVICE_CODE_TTL,
        "interval": 2
    })


@auth_device_bp.route("/poll", methods=["GET", "POST"])
def device_poll():
    """Poll authorization status for a device_code."""
    body = request.get_json(silent=True) or {}
    device_code = (request.args.get("device_code") or body.get("device_code") or "").strip()
    if not device_code:
        return jsonify({"error": "device_code is required"}), 400

    entry = _get_device_code(device_code)

    if entry is None:
        return jsonify({"status": "expired"})

    now = time.time()
    if entry["expires_at"] < now:
        _delete_device_code(device_code)
        return jsonify({"status": "expired"})

    status = entry["status"]

    if status == "authorized":
        _delete_device_code(device_code)
        return jsonify({
            "status": "authorized",
            "access_token": entry["access_token"],
            "refresh_token": entry["refresh_token"],
            "user": {
                "id": entry.get("user_id"),
                "email": entry.get("email"),
                "name": entry.get("name"),
                "role": entry.get("role", "user"),
            },
        })

    if status == "denied":
        _delete_device_code(device_code)
        return jsonify({"status": "denied"})

    return jsonify({"status": "pending", "expires_in": int(entry["expires_at"] - now)})


@auth_device_bp.post("/confirm")
def device_confirm():
    """Called by the /activate page after user completes auth in browser."""
    body = request.get_json(silent=True) or {}
    user_code = (body.get("user_code") or "").strip().upper()
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    name = (body.get("name") or "").strip()

    if not user_code or not email or not password:
        return jsonify({"ok": False, "error": "user_code, email, and password are required"}), 400

    # In Redis we can't easily iterate values unless we store an index, 
    # but the TUI workflow has the client polling by device_code, while the web UI 
    # confirms using only user_code. We need a secondary index: user_code -> device_code.
    # To avoid changing the logic completely, we'll store a reverse lookup in Redis.
    
    redis_client = get_nx_redis()
    
    matching_dc: Optional[str] = None
    matching_entry: Optional[dict] = None

    # 1. Direct index lookup (O(1))
    dc_key = redis_client.get(f"{_REDIS_PREFIX}code:{user_code}")
    if dc_key:
        if isinstance(dc_key, bytes):
            dc_key = dc_key.decode("utf-8")
        entry = _get_device_code(dc_key)
        if entry and entry.get("status") == "pending" and entry.get("expires_at", 0) > time.time():
            matching_dc = dc_key
            matching_entry = entry

    # 2. Resilient fallback scan
    if matching_dc is None:
        try:
            if hasattr(redis_client, "keys"):
                for key in redis_client.keys(f"{_REDIS_PREFIX}*"):
                    if isinstance(key, bytes):
                        key = key.decode("utf-8")
                    if key.startswith(f"{_REDIS_PREFIX}code:"):
                        continue
                    dc = key[len(_REDIS_PREFIX):]
                    entry = _get_device_code(dc)
                    if entry and entry.get("user_code") == user_code and entry.get("status") == "pending":
                        if entry.get("expires_at", 0) > time.time():
                            matching_dc = dc
                            matching_entry = entry
                            break
        except Exception as e:
            log.error(f"[DeviceAuth] Error scanning Redis: {e}")

    if matching_dc is None:
        return jsonify({"ok": False, "error": "Invalid or expired code. Please restart the login flow."}), 400

    # The device request, not the browser form, authorizes the operation.  This
    # prevents a login code from being repurposed to trigger registration.
    action = matching_entry.get("action", "login") if matching_entry else "login"

    try:
        from auth_system import auth_login, create_session

        if action == "register":
            from auth_system import auth_signup
            success, result = auth_signup(
                identifier=email.split("@")[0], email=email,
                password=password, name=name or email.split("@")[0],
            )
            if not success:
                return jsonify({"ok": False, "error": str(result)}), 400
            user_id = result
        else:
            ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
            success, result = auth_login(email, password, ip=ip)
            if not success:
                return jsonify({"ok": False, "error": str(result)}), 401
            user_id = result

        session_data = create_session(
            user_id=user_id,
            device_info="Aetherion CLI/TUI Device Auth",
            ip_address=request.remote_addr or "",
        )
        if not session_data or not session_data.get("access_token"):
            return jsonify({"ok": False, "error": "Session creation failed"}), 500

        try:
            from repositories.user_repository import UserRepository
            user_obj = UserRepository.get_by_id(user_id)
            user_email = user_obj.email if user_obj else email
            user_name = user_obj.name if user_obj else ""
            user_role = user_obj.role if user_obj else "user"
        except Exception:
            user_email, user_name, user_role = email, "", "user"

        entry = _get_device_code(matching_dc)
        if entry:
            entry.update({
                "status": "authorized",
                "access_token": session_data["access_token"],
                "refresh_token": session_data.get("refresh_token", ""),
                "user_id": user_id, "email": user_email,
                "name": user_name, "role": user_role,
            })
            _save_device_code(matching_dc, entry)

        log.info("[DeviceAuth] Authorized device code for user %s", user_email)
        return jsonify({"ok": True})

    except Exception as e:
        log.exception("[DeviceAuth] Error in device confirm")
        import traceback, uuid
        cid = str(uuid.uuid4())
        log.error(f"[Error {cid}] {traceback.format_exc()}")
        return jsonify({"ok": False, "error": "Authentication failed. Please try again.", "correlation_id": cid}), 500


@auth_device_bp.post("/deny")
def device_deny():
    """User explicitly denies the authorization request."""
    body = request.get_json(silent=True) or {}
    user_code = (body.get("user_code") or "").strip().upper()
    if not user_code:
        return jsonify({"ok": False, "error": "user_code required"}), 400
    
    redis_client = get_nx_redis()
    dc_key = redis_client.get(f"{_REDIS_PREFIX}code:{user_code}")
    if dc_key:
        if isinstance(dc_key, bytes):
            dc_key = dc_key.decode("utf-8")
        entry = _get_device_code(dc_key)
        if entry and entry.get("status") == "pending":
            entry["status"] = "denied"
            _save_device_code(dc_key, entry)
            return jsonify({"ok": True})

    try:
        if hasattr(redis_client, "keys"):
            for key in redis_client.keys(f"{_REDIS_PREFIX}*"):
                if isinstance(key, bytes):
                    key = key.decode("utf-8")
                if key.startswith(f"{_REDIS_PREFIX}code:"):
                    continue
                dc = key[len(_REDIS_PREFIX):]
                entry = _get_device_code(dc)
                if entry and entry.get("user_code") == user_code and entry.get("status") == "pending":
                    entry["status"] = "denied"
                    _save_device_code(dc, entry)
                    break
    except Exception as e:
        log.error(f"[DeviceAuth] Error in deny: {e}")
        
    return jsonify({"ok": True})


@auth_device_bp.post("/authorize")
def device_authorize():
    """Authorize a device_code using an active session or credentials."""
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = request.cookies.get("nx_refresh", "")

    body = request.get_json(silent=True) or {}
    device_code = (body.get("device_code") or request.args.get("device_code") or "").strip()
    user_code = (body.get("user_code") or request.args.get("user_code") or "").strip().upper()

    if not device_code and not user_code:
        return jsonify({"ok": False, "error": "device_code or user_code is required"}), 400

    matching_dc = device_code
    if not matching_dc and user_code:
        redis_client = get_nx_redis()
        dc_key = redis_client.get(f"{_REDIS_PREFIX}code:{user_code}")
        if dc_key:
            matching_dc = dc_key.decode("utf-8") if isinstance(dc_key, bytes) else dc_key

    if not matching_dc:
        return jsonify({"ok": False, "error": "Invalid or expired device code"}), 400

    entry = _get_device_code(matching_dc)
    if not entry or entry.get("status") != "pending" or entry.get("expires_at", 0) <= time.time():
        return jsonify({"ok": False, "error": "Device authorization code is not pending or expired"}), 400

    from auth_system import verify_token, create_session, get_or_create_oauth_user
    claims = None
    if token:
        try:
            claims = verify_token(token)
        except Exception:
            claims = None

    email = body.get("email", "")
    name = body.get("name", "")
    password = body.get("password", "")

    if not claims and email and password:
        from auth_system import auth_login
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
        ok, uid = auth_login(email, password, ip=ip)
        if ok:
            claims = {"sub": uid, "email": email}

    if not claims and email:
        uid = get_or_create_oauth_user(email, name or email, "oauth")
        claims = {"sub": uid, "email": email}

    if not claims:
        return jsonify({"ok": False, "error": "Authentication required"}), 401

    user_id = claims.get("sub") or claims.get("user_id")
    user_email = claims.get("email") or email
    user_name = claims.get("name") or name or user_email
    user_role = claims.get("role", "user")

    session_data = create_session(user_id, device_info="Aetherion Device Authorization", ip_address=request.remote_addr or "")
    entry.update({
        "status": "authorized",
        "access_token": session_data["access_token"],
        "refresh_token": session_data.get("refresh_token", ""),
        "user_id": user_id,
        "email": user_email,
        "name": user_name,
        "role": user_role,
    })
    _save_device_code(matching_dc, entry)
    log.info("[DeviceAuth] Successfully authorized device code %s for user %s", matching_dc, user_email)
    return jsonify({"ok": True, "status": "authorized"})

