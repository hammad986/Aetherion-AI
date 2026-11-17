from functools import wraps
from flask import session, request, g
from middleware.api_response import error_response
from auth_system import token_required

def require_auth(f):
    """
    Decorator to enforce JWT authentication.
    Returns a canonical 401 JSON error if unauthorized.
    """
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        # Fully decoupled from Flask session; rely strictly on g.user_id set by token_required
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
            # Fetch user plan from DB using g.user_id
            from auth_system import execute_query
            user_plan = "basic"
            if hasattr(g, 'user_id'):
                row = execute_query("SELECT plan FROM users WHERE id=?", (g.user_id,), fetchone=True)
                if row and row[0]:
                    user_plan = row[0]
            
            # Map "free" to "basic" for tier comparison if needed
            user_plan = (user_plan or "basic").lower()
            if user_plan in ("free", "lite"):
                user_plan = "basic"
                
            plan_levels = {"basic": 1, "free": 1, "lite": 1, "pro": 2, "enterprise": 3, "elite": 3}
            req_level = plan_levels.get(min_tier.lower(), 99)
            user_level = plan_levels.get(user_plan, 0)
            
            # Admins always have access to operational surfaces
            if getattr(g, 'user_role', '') == 'admin':
                user_level = 999
            
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
            if not hasattr(g, 'user_id'):
                return error_response("Unauthorized", code="AUTH_REQUIRED", status=401)
            
            user_role = getattr(g, 'user_role', 'viewer')
            if user_role not in allowed_roles:
                return error_response(f"Insufficient privileges. Requires one of: {allowed_roles}", code="ROLE_RESTRICTED", status=403)
                
            return f(*args, **kwargs)
        return wrapper
    return decorator

def require_operator(f):
    """Shorthand for endpoints requiring at least 'operator' privileges."""
    return require_role(["operator", "admin"])(f)
