"""
app.py — Aetherion Core Production Backend Entrypoint & Application Factory
=============================================================================
Provides the hardened API required for:
  - CLI & TUI execution, tasks, workspaces, logs, diffs, and artifacts
  - Device authorization flow (RFC 8628)
  - Minimal Authentication Website (login, signup, session recovery, device verify)
  - Strict fail-closed production boundary validation
"""
from __future__ import annotations

import os
import sys
import uuid
import logging
from pathlib import Path
from flask import Flask, jsonify, request, g
from dotenv import load_dotenv

# Ensure backend and root directory are in sys.path
_BACKEND_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

# Setup module identity unification between 'app' and 'backend.app'
# This ensures that both keys in sys.modules point to the exact same module object.
if "backend" not in sys.modules:
    import types
    _backend_pkg = types.ModuleType("backend")
    _backend_pkg.__path__ = [str(_BACKEND_DIR)]
    _backend_pkg.__file__ = str(_BACKEND_DIR / "__init__.py")
    sys.modules["backend"] = _backend_pkg

if __name__ == "app":
    sys.modules["backend.app"] = sys.modules["app"]
    try:
        import backend
        backend.app = sys.modules["app"]
    except Exception:
        pass
elif __name__ == "backend.app":
    sys.modules["app"] = sys.modules["backend.app"]

load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("aetherion.backend")

# Startup validation gate & database initialization (Fail-closed in production/staging)
_ALREADY_INITIALIZED = getattr(sys, "_AETHERION_BACKEND_APP_INITIALIZED", False)
if not _ALREADY_INITIALIZED:
    try:
        from auth_system import init_db
        init_db()
    except Exception as _e:
        logger.warning("Database pre-initialization notice: %s", _e)

    from core.startup_validator import validate_startup
    validate_startup()

from core.environment import get_environment_mode, is_production, is_staging


def create_app() -> Flask:
    """Application factory for Aetherion backend."""
    app = Flask(__name__)

    # Configure secrets
    secret_key = os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
    if not secret_key:
        if is_production() or is_staging():
            raise RuntimeError("SECRET_KEY must be configured in production/staging.")
        secret_key = "dev-insecure-secret-key-change-in-prod"
    app.config["SECRET_KEY"] = secret_key

    # Configure reverse proxy support (ProxyFix)
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    # Setup correlation ID & request tracking
    @app.before_request
    def before_request_hook():
        cid = (
            request.headers.get("X-Correlation-ID")
            or request.headers.get("X-Request-ID")
            or str(uuid.uuid4())
        )
        request.correlation_id = cid
        g.correlation_id = cid

    # Setup CORS & security response headers
    @app.after_request
    def after_request_hook(response):
        cid = getattr(g, "correlation_id", None) or str(uuid.uuid4())
        response.headers["X-Correlation-ID"] = cid
        
        origin = request.headers.get("Origin")
        allowed_origins = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
        allowed_origins = [o.strip() for o in allowed_origins if o.strip()]

        if "*" in allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin or "*"
        elif origin and origin in allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin

        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, X-Correlation-ID, X-Admin-Bootstrap, Idempotency-Key"
        )
        response.headers["Vary"] = "Origin"

        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"

        if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https" or is_production():
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return response

    # Root index & browser endpoints (prevents 405 Method Not Allowed)
    @app.route("/", methods=["GET"])
    def root_index():
        return jsonify({
            "ok": True,
            "service": "Aetherion Backend API",
            "version": "1.0.0",
            "status": "online",
            "health": "/api/system/health",
        }), 200

    @app.route("/favicon.ico", methods=["GET"])
    def favicon():
        return "", 204

    # Handle OPTIONS preflight requests cleanly without route collisions
    @app.before_request
    def handle_options_preflight():
        if request.method == "OPTIONS":
            return "", 200

    # Register Blueprints
    from routes import (
        artifacts_bp,
        auth_core_bp,
        auth_device_bp,
        auth_oauth_bp,
        auth_recovery_bp,
        diff_bp,
        health_bp,
        replay_bp,
        tasks_bp,
        workspace_bp,
        platform_bp,
        phase4_bp,
        d3_bp,
    )

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_core_bp)
    app.register_blueprint(auth_device_bp)
    app.register_blueprint(auth_oauth_bp)
    app.register_blueprint(auth_recovery_bp)
    app.register_blueprint(workspace_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(artifacts_bp)
    app.register_blueprint(diff_bp)
    app.register_blueprint(replay_bp)
    app.register_blueprint(platform_bp)
    app.register_blueprint(phase4_bp)
    app.register_blueprint(d3_bp)

    # Standard JSON Error Handlers
    @app.errorhandler(400)
    def bad_request(error):
        return jsonify({"ok": False, "error": str(error.description if hasattr(error, "description") else "Bad request")}), 400

    @app.errorhandler(401)
    def unauthorized(error):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    @app.errorhandler(403)
    def forbidden(error):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"ok": False, "error": "Resource not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(error):
        return jsonify({"ok": False, "error": "Method not allowed"}), 405

    @app.errorhandler(429)
    def rate_limited(error):
        return jsonify({"ok": False, "error": "Too many requests"}), 429

    @app.errorhandler(500)
    def internal_error(error):
        logger.exception("Internal server error: %s", error)
        return jsonify({"ok": False, "error": "Internal server error"}), 500

    return app


if _ALREADY_INITIALIZED:
    app = getattr(sys, "_AETHERION_BACKEND_APP_INSTANCE")
else:
    app = create_app()
    sys._AETHERION_BACKEND_APP_INITIALIZED = True
    sys._AETHERION_BACKEND_APP_INSTANCE = app
    sys._AETHERION_BACKEND_CREATE_APP = create_app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    explicit_host = os.environ.get("HOST", "").strip()
    if explicit_host:
        host = explicit_host
    else:
        is_container = os.environ.get("RUNNER_CONTAINER") == "1"
        raw_env = os.environ.get("ENVIRONMENT", "").strip().lower()
        host = "0.0.0.0" if (is_container or raw_env in ("production", "prod")) else "127.0.0.1"
    env_mode = get_environment_mode()
    app.run(host=host, port=port, debug=(env_mode.value == "local-development"), use_reloader=False)
