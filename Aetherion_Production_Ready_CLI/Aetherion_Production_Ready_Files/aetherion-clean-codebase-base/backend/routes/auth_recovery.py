"""
routes/auth_recovery.py — Password reset and email verification routes.
M3: split from auth_routes.py.  7 routes.
"""
import os
import logging
from flask import Blueprint, request, jsonify, redirect, g, render_template

from auth_system import token_required, execute_query
from routes.auth_helpers import logger

auth_recovery_bp = Blueprint("auth_recovery", __name__)


# ─── Password Reset Page ──────────────────────────────────────────────────────

@auth_recovery_bp.route("/reset-password")
def page_reset_password():
    return render_template("reset_password.html")


# ─── Forgot Password ──────────────────────────────────────────────────────────

@auth_recovery_bp.route("/api/auth/forgot-password", methods=["POST"])
def api_forgot_password():
    from security import _forgot_pw_limiter, get_client_ip
    from account_recovery import request_password_reset

    ip     = get_client_ip(request)
    ok, _  = _forgot_pw_limiter.check(ip)
    if not ok:
        # Return 200 — never reveal rate-limit on this endpoint
        return jsonify({"ok": True, "message": "If that email exists, a reset link has been sent."})

    data  = request.json or {}
    email = (data.get("email") or "").strip()

    base = request.host_url.rstrip("/")
    replit_domain = (
        os.environ.get("REPLIT_DEV_DOMAIN") or
        os.environ.get("REPLIT_DOMAINS", "").split(",")[0].strip()
    )
    if replit_domain:
        base = f"https://{replit_domain}"

    request_password_reset(email, base)
    return jsonify({"ok": True, "message": "If that email exists, a reset link has been sent."})


# ─── Validate Reset Token ─────────────────────────────────────────────────────

@auth_recovery_bp.route("/api/auth/validate-reset-token", methods=["POST"])
def api_validate_reset_token():
    from account_recovery import verify_reset_token
    data  = request.json or {}
    token = (data.get("token") or "").strip()
    valid, err, _ = verify_reset_token(token)
    if valid:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": err}), 400


# ─── Reset Password ───────────────────────────────────────────────────────────

@auth_recovery_bp.route("/api/auth/reset-password", methods=["POST"])
def api_reset_password():
    from account_recovery import do_password_reset
    data   = request.json or {}
    token  = (data.get("token") or "").strip()
    new_pw = (data.get("new_password") or data.get("password") or "").strip()

    success, msg = do_password_reset(token, new_pw)
    if success:
        return jsonify({"ok": True, "message": msg})
    return jsonify({"ok": False, "error": msg}), 400


# ─── Send Verification Email ──────────────────────────────────────────────────

@auth_recovery_bp.route("/api/auth/send-verification", methods=["POST"])
@token_required
def api_send_verification():
    from account_recovery import send_verification_email

    base = request.host_url.rstrip("/")
    replit_domain = (
        os.environ.get("REPLIT_DEV_DOMAIN") or
        os.environ.get("REPLIT_DOMAINS", "").split(",")[0].strip()
    )
    if replit_domain:
        base = f"https://{replit_domain}"

    rows = execute_query("SELECT id, email, name FROM users WHERE id = ?", (g.user_id,))
    if not rows:
        return jsonify({"ok": False, "error": "User not found"}), 404
    user_id, email, name = rows[0]
    send_verification_email(user_id, email, name or "", base)
    return jsonify({"ok": True, "message": "Verification email sent. Check your inbox."})


# ─── Verify Email (link click) ────────────────────────────────────────────────

@auth_recovery_bp.route("/api/auth/verify-email", methods=["GET"])
def api_verify_email():
    from account_recovery import do_verify_email
    token = request.args.get("token", "").strip()
    success, msg = do_verify_email(token)
    if success:
        return redirect("/?verified=1")
    from urllib.parse import quote
    return redirect(f"/?verify_error={quote(msg)}")


# ─── Verification Status ──────────────────────────────────────────────────────

@auth_recovery_bp.route("/api/auth/verification-status", methods=["GET"])
@token_required
def api_verification_status():
    from account_recovery import get_verification_status
    email_service_enabled = bool(os.environ.get("EMAIL_API_KEY", "").strip())
    if not email_service_enabled:
        return jsonify({"ok": True, "verified": True, "email_service_enabled": False})
    verified = get_verification_status(g.user_id)
    return jsonify({"ok": True, "verified": verified, "email_service_enabled": True})
