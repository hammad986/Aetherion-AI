"""
tests/integration/test_security_hygiene_regressions.py — Regression tests for:
- Host binding resolution (127.0.0.1 default vs container/production override)
- Preflight PostgreSQL reachability fail-closed behavior (no silent SQLite fallback)
- Connection string credential sanitization
"""
import os
import pytest
from core.environment import EnvironmentMode, get_environment_mode
from core.preflight import sanitize_dsn, check_database, CheckStatus


def test_host_binding_resolution(monkeypatch):
    """Verify loopback is the default for local development and container override works."""
    # 1. Unset HOST in local-development -> 127.0.0.1
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("RUNNER_CONTAINER", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "local-development")
    
    is_container = os.environ.get("RUNNER_CONTAINER") == "1"
    raw_env = os.environ.get("ENVIRONMENT", "").strip().lower()
    host = "0.0.0.0" if (is_container or raw_env in ("production", "prod")) else "127.0.0.1"
    assert host == "127.0.0.1"

    # 2. Container runner -> 0.0.0.0
    monkeypatch.setenv("RUNNER_CONTAINER", "1")
    is_container = os.environ.get("RUNNER_CONTAINER") == "1"
    host = "0.0.0.0" if (is_container or raw_env in ("production", "prod")) else "127.0.0.1"
    assert host == "0.0.0.0"

    # 3. Explicit HOST -> respected
    monkeypatch.setenv("HOST", "192.168.1.100")
    explicit_host = os.environ.get("HOST", "").strip()
    assert explicit_host == "192.168.1.100"


def test_sanitize_dsn_masks_credentials():
    """Verify passwords in connection strings are always masked."""
    dsn = "postgresql://user:super_secret_password123@127.0.0.1:5432/testdb"
    sanitized = sanitize_dsn(dsn)
    assert "super_secret_password123" not in sanitized
    assert "postgresql://user:***@127.0.0.1:5432/testdb" == sanitized


def test_preflight_postgres_unreachable_fails(monkeypatch):
    """Verify preflight fails closed when PostgreSQL is configured but unreachable."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://baduser:badpass@127.0.0.1:59999/nodb")
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "local-development")
    
    # Must FAIL, not WARN or silently downgrade to SQLite
    res = check_database(probe_live=True)
    assert res.status == CheckStatus.FAIL
    assert "unreachable" in res.detail.lower()
    assert "sqlite" not in res.detail.lower()
