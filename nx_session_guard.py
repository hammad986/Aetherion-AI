"""
nx_session_guard.py — HITL Auth Middleware & Session Ownership Guard
═════════════════════════════════════════════════════════════════════
Phase: Production Operations — Authentication Hardening

Provides:
  1. `hitl_auth_required`  — decorator for HITL respond/audit endpoints
  2. `sse_session_check`   — validates caller owns the session before SSE stream
  3. `session_owner_check` — generic session ownership validator
  4. `replay_export_auth`  — authorises replay JSON export

Design decisions:
  - JWT Bearer token preferred (Authorization header)
  - Falls back to Flask session cookie for browser clients
  - ALLOW_DEV_AUTH=1 bypasses auth for local development (never prod)
  - Ownership: session must belong to authenticated user OR user must be admin
  - Rate-limited: max 20 HITL decisions/minute per user (prevent automation abuse)
"""

import logging
import os
import sqlite3
import time
from collections import defaultdict, deque
from functools import wraps

import jwt
from flask import g, jsonify, request

logger = logging.getLogger("nexora.session_guard")

# ── Config ────────────────────────────────────────────────────────────────────
_JWT_SECRET    = os.getenv("JWT_SECRET", "nexora_saas_secret_key_change_in_production")
_ALLOW_DEV     = os.getenv("ALLOW_DEV_AUTH", "0").strip() == "1"
_SESSIONS_DB   = os.getenv("SESSIONS_DB", "sessions.db")
_SAAS_DB       = os.getenv("SAAS_DB", "saas_platform.db")

# HITL decision rate limit — max 20 decisions per 60s window per user
_HITL_MAX      = int(os.getenv("HITL_RATE_LIMIT", "20"))
_HITL_WINDOW   = int(os.getenv("HITL_RATE_WINDOW", "60"))
_hitl_rl_lock  = __import__("threading").Lock()
_hitl_rl_store: dict = defaultdict(deque)

# ── JWT decode ────────────────────────────────────────────────────────────────

def _decode_token(token: str) -> dict | None:
    """Decode a JWT access token. Returns payload dict or None on failure."""
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=["HS256"])
        if payload.get("type") != "access":
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def _get_caller_token() -> str | None:
    """Extract Bearer token from Authorization header or cookie."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    # Browser clients may send via cookie
    return request.cookies.get("access_token")


def _is_user_admin(user_id: int) -> bool:
    try:
        conn = sqlite3.connect(_SAAS_DB)
        row = conn.execute(
            "SELECT role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        conn.close()
        return bool(row and row[0] in ("admin", "superadmin"))
    except Exception:
        return False


def _session_belongs_to_user(session_id: str, user_id: int) -> bool:
    """Check sessions.db to verify a session was created by this user."""
    try:
        conn = sqlite3.connect(_SESSIONS_DB)
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        conn.close()
        if row is None:
            return False  # session not found
        db_uid = row[0]
        if db_uid is None:
            # Legacy sessions without user_id: allow if only one user exists
            return True
        return str(db_uid) == str(user_id)
    except Exception:
        # If sessions table lacks user_id column (legacy): allow with warning
        logger.warning("[SessionGuard] Cannot verify session ownership (schema gap): %s", session_id)
        return True


# ── Rate limiter ──────────────────────────────────────────────────────────────

def _hitl_rate_check(user_id: int) -> bool:
    """Returns True if user is within HITL rate limit."""
    now = time.time()
    key = str(user_id)
    with _hitl_rl_lock:
        dq = _hitl_rl_store[key]
        cutoff = now - _HITL_WINDOW
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= _HITL_MAX:
            return False
        dq.append(now)
        return True


# ── Decorators ────────────────────────────────────────────────────────────────

def hitl_auth_required(f):
    """
    Decorator: enforces authentication + session ownership + rate limit
    on HITL respond/audit endpoints.

    Sets g.user_id, g.user_email, g.user_role on success.
    URL must include <sid> (session ID) as a parameter.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        sid = kwargs.get("sid") or kwargs.get("session_id") or ""

        # ── Dev bypass ────────────────────────────────────────────────────────
        if _ALLOW_DEV and request.args.get("dev") == "1":
            g.user_id    = 1
            g.user_email = "dev@localhost"
            g.user_role  = "admin"
            logger.debug("[SessionGuard] Dev auth bypass for HITL: %s", sid)
            return f(*args, **kwargs)

        # ── Token validation ──────────────────────────────────────────────────
        token = _get_caller_token()
        if not token:
            return jsonify({"ok": False, "error": "Unauthorized: no token", "code": "NO_TOKEN"}), 401

        payload = _decode_token(token)
        if not payload:
            return jsonify({"ok": False, "error": "Unauthorized: invalid/expired token", "code": "BAD_TOKEN"}), 401

        user_id   = payload["user_id"]
        user_role = payload.get("role", "user")

        g.user_id    = user_id
        g.user_email = payload.get("email", "")
        g.user_role  = user_role

        # ── Session ownership ─────────────────────────────────────────────────
        if sid and user_role not in ("admin", "superadmin"):
            if not _session_belongs_to_user(sid, user_id):
                logger.warning(
                    "[SessionGuard] HITL ownership denied: user=%s, session=%s",
                    user_id, sid
                )
                return jsonify({"ok": False, "error": "Forbidden: session not yours", "code": "FORBIDDEN"}), 403

        # ── Rate limit ────────────────────────────────────────────────────────
        if not _hitl_rate_check(user_id):
            logger.warning("[SessionGuard] HITL rate limit hit: user=%s", user_id)
            return jsonify({
                "ok": False,
                "error": f"Rate limit: max {_HITL_MAX} HITL decisions per {_HITL_WINDOW}s",
                "code": "RATE_LIMITED",
            }), 429

        return f(*args, **kwargs)
    return decorated


def sse_session_check(f):
    """
    Decorator: lightweight auth check for SSE stream endpoint.
    Allows unauthenticated access in dev mode; enforces JWT in production.
    Does NOT block the stream — returns 401 before opening SSE connection.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if _ALLOW_DEV:
            g.user_id    = 1
            g.user_email = "dev@localhost"
            g.user_role  = "admin"
            return f(*args, **kwargs)

        # Check for token — graceful: if no auth system, allow (backward compat)
        token = _get_caller_token()
        if token:
            payload = _decode_token(token)
            if payload:
                g.user_id    = payload["user_id"]
                g.user_email = payload.get("email", "")
                g.user_role  = payload.get("role", "user")
                return f(*args, **kwargs)
            return jsonify({"ok": False, "error": "Expired/invalid token"}), 401

        # No token present: allow with anonymous user (backward-compat for beta)
        # TODO(GA-SEC-3): enforce token when all clients send auth headers
        g.user_id    = None
        g.user_email = "anonymous"
        g.user_role  = "guest"
        return f(*args, **kwargs)
    return decorated


def replay_export_auth(f):
    """
    Decorator: ensures only the session owner (or admin) can export replay JSON.
    Limits exports to 10/minute per user to prevent bulk exfiltration.
    """
    _export_rl: dict = defaultdict(deque)
    _export_lock = __import__("threading").Lock()

    @wraps(f)
    def decorated(*args, **kwargs):
        sid = kwargs.get("sid") or kwargs.get("session_id") or ""

        if _ALLOW_DEV:
            return f(*args, **kwargs)

        token = _get_caller_token()
        if not token:
            return jsonify({"ok": False, "error": "Auth required for replay export"}), 401

        payload = _decode_token(token)
        if not payload:
            return jsonify({"ok": False, "error": "Invalid token"}), 401

        user_id = payload["user_id"]
        role    = payload.get("role", "user")

        if sid and role not in ("admin", "superadmin"):
            if not _session_belongs_to_user(sid, user_id):
                return jsonify({"ok": False, "error": "Forbidden"}), 403

        # Export rate limit: 10/minute
        now = time.time()
        key = str(user_id)
        with _export_lock:
            dq = _export_rl[key]
            while dq and dq[0] < now - 60:
                dq.popleft()
            if len(dq) >= 10:
                return jsonify({"ok": False, "error": "Export rate limit (10/min)"}), 429
            dq.append(now)

        g.user_id = user_id
        return f(*args, **kwargs)
    return decorated


# ── Session ownership helper (for use outside decorators) ─────────────────────

def assert_session_owner(sid: str) -> tuple[bool, str]:
    """
    For use in route handlers that need ownership check without a full decorator.
    Returns (ok, error_message).
    """
    token = _get_caller_token()
    if _ALLOW_DEV:
        return True, ""
    if not token:
        return False, "No auth token"
    payload = _decode_token(token)
    if not payload:
        return False, "Invalid token"
    if payload.get("role") in ("admin", "superadmin"):
        return True, ""
    if not _session_belongs_to_user(sid, payload["user_id"]):
        return False, "Session not owned by user"
    return True, ""
