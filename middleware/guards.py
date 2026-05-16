from functools import wraps
from flask import session, request
from middleware.api_response import error_response

def require_auth(f):
    """
    Decorator to enforce session authentication.
    Returns a canonical 401 JSON error if unauthorized.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # We check both session['user'] and session['google_id'] to support both legacy and OAuth flows.
        if not session.get('user') and not session.get('google_id'):
            return error_response(
                message="Authentication required.",
                code="UNAUTHORIZED",
                status=401
            )
        return f(*args, **kwargs)
    return decorated

def require_plan(min_tier="pro"):
    """
    Decorator to enforce plan restrictions.
    Returns a canonical 403 JSON error if the user's plan is insufficient.
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            # For now, default to basic if not explicitly set.
            user_plan = session.get('plan', 'basic')
            
            plan_levels = {"basic": 1, "pro": 2, "enterprise": 3}
            req_level = plan_levels.get(min_tier, 99)
            user_level = plan_levels.get(user_plan, 0)
            
            if user_level < req_level:
                return error_response(
                    message=f"This feature requires a {min_tier.capitalize()} plan or higher.",
                    code="PLAN_RESTRICTED",
                    details={"current_plan": user_plan, "required_plan": min_tier},
                    status=403
                )
            return f(*args, **kwargs)
        return decorated
    return decorator

def require_role(allowed_roles: list):
    """
    Enforces Role-Based Access Control (RBAC) on operational endpoints.
    Allows for separation of ['viewer', 'operator', 'admin'].
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not session.get('user') and not session.get('google_id'):
                return error_response("Unauthorized", code="AUTH_REQUIRED", status=401)
            
            user_role = session.get('role', 'viewer')
            if user_role not in allowed_roles:
                return error_response(f"Insufficient privileges. Requires one of: {allowed_roles}", code="ROLE_RESTRICTED", status=403)
                
            return f(*args, **kwargs)
        return wrapper
    return decorator

def require_operator(f):
    """Shorthand for endpoints requiring at least 'operator' privileges."""
    return require_role(["operator", "admin"])(f)
