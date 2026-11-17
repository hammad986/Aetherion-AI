"""
core/environment.py — Authoritative Environment Mode & Security Posture Definitions

Enforces four explicit, mutually exclusive runtime modes:
  1. local-development: Developer workstations. Permissive fallbacks (SQLite, in-process queues, host subprocess runner with warnings) are allowed.
  2. CI-test: Automated test runners / CI pipelines. Mock adapters, test databases, and in-memory caches allowed; LLM provider calls mocked.
  3. staging: Pre-production verification cluster. Fails closed on missing infrastructure (PostgreSQL mandatory, Redis mandatory, container runner mandatory).
  4. production: Production traffic serving. Strict fail-closed security posture (PostgreSQL mandatory, Redis mandatory, high-entropy independent secrets mandatory, container/microVM sandbox mandatory, zero secret leaks).
"""
from __future__ import annotations

import enum
import os
import sys
from typing import Optional


class EnvironmentMode(str, enum.Enum):
    LOCAL_DEVELOPMENT = "local-development"
    CI_TEST = "CI-test"
    STAGING = "staging"
    PRODUCTION = "production"
    UNKNOWN = "unknown"

    @classmethod
    def from_string(cls, value: Optional[str]) -> "EnvironmentMode":
        if not value:
            # Check CI / Testing flags
            if _detect_ci_or_testing():
                return cls.CI_TEST
            return cls.LOCAL_DEVELOPMENT

        normalized = value.strip().lower().replace("_", "-")
        if normalized in ("production", "prod"):
            return cls.PRODUCTION
        elif normalized in ("staging", "stage"):
            return cls.STAGING
        elif normalized in ("ci-test", "ci", "test", "testing", "pytest"):
            return cls.CI_TEST
        elif normalized in ("local-development", "development", "local", "dev"):
            return cls.LOCAL_DEVELOPMENT

        # Explicit non-empty unknown value: fail closed, never silently default to local-development
        return cls.UNKNOWN


def _detect_ci_or_testing() -> bool:
    """Return True if running in a CI or automated test environment."""
    return (
        os.getenv("CI", "").strip().lower() in ("1", "true", "yes")
        or os.getenv("GITHUB_ACTIONS", "").strip().lower() in ("1", "true", "yes")
        or os.getenv("TESTING", "").strip().lower() in ("1", "true", "yes")
        or os.getenv("FLASK_TESTING", "").strip().lower() in ("1", "true", "yes")
        or "pytest" in sys.modules
    )


def get_raw_environment_string() -> str:
    """Return the raw environment variable value from the process environment."""
    return (
        os.getenv("AETHERION_ENV")
        or os.getenv("ENVIRONMENT")
        or os.getenv("APP_ENV")
        or os.getenv("ENV")
        or os.getenv("FLASK_ENV")
        or ""
    ).strip()


def get_environment_mode() -> EnvironmentMode:
    """Resolve the authoritative environment mode from process environment variables."""
    raw_env = get_raw_environment_string()
    return EnvironmentMode.from_string(raw_env)


def validate_environment_identity() -> tuple[bool, EnvironmentMode, str]:
    """
    Validate that the environment identity is explicitly supported.
    Returns (is_valid, resolved_mode, error_message).
    """
    raw_env = get_raw_environment_string()
    mode = get_environment_mode()
    if mode == EnvironmentMode.UNKNOWN:
        return (
            False,
            mode,
            f"Unrecognized environment value '{raw_env}'. "
            "Explicit environment identity is required. "
            "Supported values: 'production', 'staging', 'local-development', 'CI-test'."
        )
    return (True, mode, "")


def is_production() -> bool:
    """True only when operating in production mode."""
    return get_environment_mode() == EnvironmentMode.PRODUCTION


def is_staging() -> bool:
    """True only when operating in staging mode."""
    return get_environment_mode() == EnvironmentMode.STAGING


def is_ci_test() -> bool:
    """True only when operating in CI / automated test mode."""
    return get_environment_mode() == EnvironmentMode.CI_TEST


def is_local_dev() -> bool:
    """True only when operating in local development mode."""
    return get_environment_mode() == EnvironmentMode.LOCAL_DEVELOPMENT


def is_fail_closed_environment() -> bool:
    """
    True for environments requiring a strict fail-closed security boundary
    (production, staging, and unrecognized/unknown environments).
    In these modes, degraded fallbacks (e.g. SQLite, in-process execution,
    memory queues) are strictly prohibited.
    """
    return get_environment_mode() in (
        EnvironmentMode.PRODUCTION,
        EnvironmentMode.STAGING,
        EnvironmentMode.UNKNOWN,
    )

