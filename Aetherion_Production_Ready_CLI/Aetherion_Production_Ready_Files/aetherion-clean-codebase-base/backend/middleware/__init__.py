"""
middleware — Cross-cutting concerns for the Aetherion AI Flask application.

Public API
----------
Response helpers (api_response):
    json_response      : Canonical { ok, data, error, meta } response builder.
    success_response   : Convenience wrapper for 2xx responses.
    error_response     : Convenience wrapper for 4xx/5xx error responses.

Access-control decorators (guards):
    require_auth       : Enforce JWT authentication on a route.
    require_plan       : Enforce minimum subscription tier on a route.
    require_role       : Enforce RBAC role allowlist on a route.
    require_operator   : Shorthand for ``require_role(["operator", "admin"])``.

Observability (observability):
    setup_observability : Register before/after-request hooks for structured
                          logging and tracing header injection.

Usage
-----
    from middleware import require_auth, error_response, setup_observability

    setup_observability(app)

    @app.route("/secure")
    @require_auth
    def secure_endpoint():
        return success_response({"status": "ok"})
"""

from middleware.api_response import json_response, success_response, error_response
from middleware.guards import require_auth, require_plan, require_role, require_operator
from middleware.observability import setup_observability

__all__ = [
    # Response helpers
    "json_response",
    "success_response",
    "error_response",
    # Auth / RBAC decorators
    "require_auth",
    "require_plan",
    "require_role",
    "require_operator",
    # Observability
    "setup_observability",
]
