"""
backend/routes package
Exports all converged route blueprints.
"""
from .artifacts import artifacts_bp
from .auth_core import auth_core_bp
from .auth_device import auth_device_bp
from .auth_recovery import auth_recovery_bp
from .diff import diff_bp
from .health import health_bp
from .replay import replay_bp
from .tasks import tasks_bp
from .workspace import workspace_bp
from .platform_extensions import platform_bp
from .phase4_routes import phase4_bp
from .d3_features import d3_bp

from .auth_oauth import auth_oauth_bp

__all__ = [
    "artifacts_bp",
    "auth_core_bp",
    "auth_device_bp",
    "auth_oauth_bp",
    "auth_recovery_bp",
    "diff_bp",
    "health_bp",
    "replay_bp",
    "tasks_bp",
    "workspace_bp",
    "platform_bp",
    "phase4_bp",
    "d3_bp",
]

