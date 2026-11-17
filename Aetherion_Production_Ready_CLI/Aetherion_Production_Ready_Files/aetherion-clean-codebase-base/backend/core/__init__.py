"""
backend/core package
Exports environment definitions and startup validator.
"""
from .environment import (
    EnvironmentMode,
    get_environment_mode,
    get_raw_environment_string,
    is_ci_test,
    is_fail_closed_environment,
    is_local_dev,
    is_production,
    is_staging,
    validate_environment_identity,
)
from .startup_validator import validate_startup

__all__ = [
    "EnvironmentMode",
    "get_environment_mode",
    "get_raw_environment_string",
    "is_ci_test",
    "is_fail_closed_environment",
    "is_local_dev",
    "is_production",
    "is_staging",
    "validate_environment_identity",
    "validate_startup",
]
