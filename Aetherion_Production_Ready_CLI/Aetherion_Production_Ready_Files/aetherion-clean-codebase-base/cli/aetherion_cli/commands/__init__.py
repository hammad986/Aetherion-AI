"""
aetherion_cli.commands package.
Central registry of converged CLI subcommands.
"""
from .artifacts import artifacts_group
from .auth import auth_group
from .config import config_group
from .diff import diff_cmd
from .doctor import doctor_cmd
from .logs import logs_cmd
from .task import task_group
from .version import version_cmd
from .worker import worker_group
from .workspace import workspace_group

__all__ = [
    "artifacts_group",
    "auth_group",
    "config_group",
    "diff_cmd",
    "doctor_cmd",
    "logs_cmd",
    "task_group",
    "version_cmd",
    "worker_group",
    "workspace_group",
]

