"""
tests/test_database_isolation.py — Regression Test Suite for Test Target Policy & Durable State
=================================================================================================
Verifies that:
  1. All 5 operational test and audit scripts require AETHERION_TEST_DATABASE_URL and reject DATABASE_URL.
  2. Test database isolation requires explicit, independent target allowlists (HOSTS & DBS).
  3. Default behavior fails closed when allowlists are missing or empty.
  4. Unapproved hosts or databases fail closed before any connection is attempted.
  5. Wildcards in allowlists are strictly forbidden.
  6. Passwords, credentials, and full connection strings are never leaked in error messages.
  7. durable_state harmonizes with test configuration when in test mode and never silently falls back to SQLite.
  8. Normal application runtime does not inherit test-only database configuration.
"""
from __future__ import annotations

import os
import sys
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

TARGET_SCRIPTS = [
    REPO_ROOT / "scripts" / "audit_cli_auth.py",
    REPO_ROOT / "scripts" / "audit_cli_verified_commands.py",
    REPO_ROOT / "scripts" / "audit_fault_tolerance.py",
    REPO_ROOT / "scripts" / "browser_acceptance_suite.py",
    REPO_ROOT / "scripts" / "test_hitl_verification.py",
]


def load_module_from_path(script_path: Path):
    """Dynamically load script module without executing main()."""
    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    module = importlib.util.module_from_spec(spec)
    orig_argv = sys.argv
    sys.argv = [str(script_path)]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = orig_argv
    return module


# ===========================================================================
# 1. PARAMETRIZED OPERATIONAL SCRIPT TESTS (5 SCRIPTS)
# ===========================================================================

@pytest.mark.parametrize("script_path", TARGET_SCRIPTS, ids=lambda p: p.name)
def test_missing_test_database_url_fails_closed(script_path, monkeypatch):
    """Verify that omitting AETHERION_TEST_DATABASE_URL raises RuntimeError/TestTargetPolicyError."""
    monkeypatch.delenv("AETHERION_TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_HOSTS", "127.0.0.1,localhost")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_DBS", "test_db")

    mod = load_module_from_path(script_path)
    assert hasattr(mod, "get_test_database_url"), f"{script_path.name} missing get_test_database_url()"

    with pytest.raises(RuntimeError, match="Isolated test database URL not configured"):
        mod.get_test_database_url()


@pytest.mark.parametrize("script_path", TARGET_SCRIPTS, ids=lambda p: p.name)
def test_empty_test_database_url_fails_closed(script_path, monkeypatch):
    """Verify that empty/whitespace AETHERION_TEST_DATABASE_URL raises error."""
    monkeypatch.setenv("AETHERION_TEST_DATABASE_URL", "   ")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_HOSTS", "127.0.0.1,localhost")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_DBS", "test_db")

    mod = load_module_from_path(script_path)
    with pytest.raises(RuntimeError, match="Isolated test database URL not configured"):
        mod.get_test_database_url()


@pytest.mark.parametrize("script_path", TARGET_SCRIPTS, ids=lambda p: p.name)
def test_generic_database_url_is_strictly_rejected(script_path, monkeypatch):
    """Verify that generic DATABASE_URL alone is rejected and never used as a fallback."""
    monkeypatch.delenv("AETHERION_TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod_user:secret@prod-db.internal:5432/prod_database")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_HOSTS", "prod-db.internal")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_DBS", "prod_database")

    mod = load_module_from_path(script_path)
    with pytest.raises(RuntimeError, match="Generic DATABASE_URL is not accepted"):
        mod.get_test_database_url()


@pytest.mark.parametrize("script_path", TARGET_SCRIPTS, ids=lambda p: p.name)
def test_missing_allowed_hosts_fails_closed(script_path, monkeypatch):
    """Verify that omitting AETHERION_TEST_ALLOWED_HOSTS fails closed even with test URL."""
    monkeypatch.setenv("AETHERION_TEST_DATABASE_URL", "postgresql://tester:pass@127.0.0.1:5433/test_db")
    monkeypatch.delenv("AETHERION_TEST_ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_DBS", "test_db")

    mod = load_module_from_path(script_path)
    with pytest.raises(RuntimeError, match="AETHERION_TEST_ALLOWED_HOSTS is not configured"):
        mod.get_test_database_url()


@pytest.mark.parametrize("script_path", TARGET_SCRIPTS, ids=lambda p: p.name)
def test_missing_allowed_dbs_fails_closed(script_path, monkeypatch):
    """Verify that omitting AETHERION_TEST_ALLOWED_DBS fails closed even with test URL."""
    monkeypatch.setenv("AETHERION_TEST_DATABASE_URL", "postgresql://tester:pass@127.0.0.1:5433/test_db")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.delenv("AETHERION_TEST_ALLOWED_DBS", raising=False)

    mod = load_module_from_path(script_path)
    with pytest.raises(RuntimeError, match="AETHERION_TEST_ALLOWED_DBS is not configured"):
        mod.get_test_database_url()


@pytest.mark.parametrize("script_path", TARGET_SCRIPTS, ids=lambda p: p.name)
def test_unapproved_host_fails_closed(script_path, monkeypatch):
    """Verify that a host not in AETHERION_TEST_ALLOWED_HOSTS is rejected."""
    monkeypatch.setenv("AETHERION_TEST_DATABASE_URL", "postgresql://user:secret@unapproved-host.internal:5432/test_db")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_HOSTS", "127.0.0.1,localhost")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_DBS", "test_db")

    mod = load_module_from_path(script_path)
    with pytest.raises(RuntimeError, match="is not in approved AETHERION_TEST_ALLOWED_HOSTS"):
        mod.get_test_database_url()


@pytest.mark.parametrize("script_path", TARGET_SCRIPTS, ids=lambda p: p.name)
def test_unapproved_database_fails_closed(script_path, monkeypatch):
    """Verify that a database not in AETHERION_TEST_ALLOWED_DBS is rejected."""
    monkeypatch.setenv("AETHERION_TEST_DATABASE_URL", "postgresql://user:secret@127.0.0.1:5433/unapproved_prod_db")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_HOSTS", "127.0.0.1,localhost")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_DBS", "aetherion_isolated_test")

    mod = load_module_from_path(script_path)
    with pytest.raises(RuntimeError, match="is not in approved AETHERION_TEST_ALLOWED_DBS"):
        mod.get_test_database_url()


@pytest.mark.parametrize("script_path", TARGET_SCRIPTS, ids=lambda p: p.name)
def test_production_target_via_test_url_is_strictly_rejected(script_path, monkeypatch):
    """Verify that passing production target via AETHERION_TEST_DATABASE_URL fails target validation."""
    monkeypatch.setenv("AETHERION_TEST_DATABASE_URL", "postgresql://prod_admin:RealSecretPassword123@prod-cluster.internal:5432/prod_db")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_HOSTS", "127.0.0.1,localhost")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_DBS", "aetherion_test")

    mod = load_module_from_path(script_path)
    with pytest.raises(RuntimeError, match="is not in approved AETHERION_TEST_ALLOWED_HOSTS"):
        mod.get_test_database_url()


@pytest.mark.parametrize("script_path", TARGET_SCRIPTS, ids=lambda p: p.name)
def test_legitimate_approved_test_target_accepted(script_path, monkeypatch):
    """Verify that an explicitly approved isolated test target succeeds."""
    valid_dsn = "postgresql://isolated_tester:testpass@127.0.0.1:5433/isolated_test_db"
    monkeypatch.setenv("AETHERION_TEST_DATABASE_URL", valid_dsn)
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_HOSTS", "127.0.0.1,localhost")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_DBS", "isolated_test_db,aetherion_test")

    mod = load_module_from_path(script_path)
    assert mod.get_test_database_url() == valid_dsn


@pytest.mark.parametrize("script_path", TARGET_SCRIPTS, ids=lambda p: p.name)
def test_no_hardcoded_database_credentials_in_file(script_path):
    """Static assertion: verify file contains no literal credential-bearing postgres URLs."""
    content = script_path.read_text(encoding="utf-8")
    assert "uat_secret_pw" not in content, f"Hardcoded password found in {script_path.name}"
    import re
    literal_pg_uri = re.search(r"postgres(?:ql)?://[^:\s]+:[^@\s]+@", content)
    assert literal_pg_uri is None, f"Literal postgres URI with credentials found in {script_path.name}"


# ===========================================================================
# 2. TARGET POLICY SPECIFIC UNIT TESTS
# ===========================================================================

def test_wildcard_host_allowlist_rejected(monkeypatch):
    """Wildcard '*' in AETHERION_TEST_ALLOWED_HOSTS is strictly forbidden."""
    from backend.db.test_target_policy import validate_and_get_test_database_url, TestTargetPolicyError
    monkeypatch.setenv("AETHERION_TEST_DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/testdb")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_HOSTS", "*")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_DBS", "testdb")

    with pytest.raises(TestTargetPolicyError, match="Wildcard '\\*' is strictly forbidden"):
        validate_and_get_test_database_url()


def test_wildcard_db_allowlist_rejected(monkeypatch):
    """Wildcard '*' in AETHERION_TEST_ALLOWED_DBS is strictly forbidden."""
    from backend.db.test_target_policy import validate_and_get_test_database_url, TestTargetPolicyError
    monkeypatch.setenv("AETHERION_TEST_DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/testdb")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_DBS", "*")

    with pytest.raises(TestTargetPolicyError, match="Wildcard '\\*' is strictly forbidden"):
        validate_and_get_test_database_url()


def test_unsupported_database_scheme_rejected(monkeypatch):
    """Non-postgres schemes (mysql, sqlite, http) are rejected."""
    from backend.db.test_target_policy import validate_and_get_test_database_url, TestTargetPolicyError
    monkeypatch.setenv("AETHERION_TEST_DATABASE_URL", "mysql://u:p@127.0.0.1:3306/testdb")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_DBS", "testdb")

    with pytest.raises(TestTargetPolicyError, match="Unsupported database scheme 'mysql'"):
        validate_and_get_test_database_url()


def test_error_message_redacts_credentials(monkeypatch):
    """Verify that policy errors NEVER print passwords or complete connection strings."""
    from backend.db.test_target_policy import validate_and_get_test_database_url, TestTargetPolicyError
    secret_pass = "SuperSecretPlainTextPassword999"
    monkeypatch.setenv("AETHERION_TEST_DATABASE_URL", f"postgresql://admin:{secret_pass}@forbidden-prod.internal:5432/prod_database")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_DBS", "testdb")

    try:
        validate_and_get_test_database_url()
        pytest.fail("Should have raised TestTargetPolicyError")
    except TestTargetPolicyError as e:
        msg = str(e)
        assert secret_pass not in msg, "Password was leaked in error message!"
        assert "postgresql://" not in msg, "Connection string was leaked in error message!"
        assert "forbidden-prod.internal" in msg


# ===========================================================================
# 3. DURABLE STATE CONSISTENCY & SPLIT-BRAIN PREVENTION TESTS
# ===========================================================================

def test_durable_state_missing_test_target_fails_closed_no_sqlite_fallback(monkeypatch):
    """When test mode is active, missing test target fails closed and NEVER falls back to SQLite."""
    from backend.execution import durable_state
    from backend.db.test_target_policy import TestTargetPolicyError

    durable_state.reset_store()
    monkeypatch.setenv("AETHERION_TEST_MODE", "1")
    monkeypatch.delenv("AETHERION_TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(TestTargetPolicyError, match="Isolated test database URL not configured"):
        durable_state.default_store()

    durable_state.reset_store()


def test_durable_state_rejected_target_creates_no_connection(monkeypatch):
    """Rejected target in test mode fails validation before Postgres store constructor is called."""
    from backend.execution import durable_state
    from backend.db.test_target_policy import TestTargetPolicyError

    durable_state.reset_store()
    monkeypatch.setenv("AETHERION_TEST_MODE", "1")
    monkeypatch.setenv("AETHERION_TEST_DATABASE_URL", "postgresql://u:p@unapproved-host:5432/unapproved_db")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_DBS", "approved_db")

    # Ensure no connection is attempted
    with pytest.raises(TestTargetPolicyError, match="is not in approved AETHERION_TEST_ALLOWED_HOSTS"):
        durable_state.default_store()

    durable_state.reset_store()


def test_durable_state_configure_test_store_harmonizes_state(monkeypatch):
    """configure_test_store validates target and injects the store singleton."""
    from backend.execution import durable_state

    durable_state.reset_store()
    test_dsn = "postgresql://testuser:testpass@127.0.0.1:5433/aetherion_test"
    monkeypatch.setenv("AETHERION_TEST_DATABASE_URL", test_dsn)
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setenv("AETHERION_TEST_ALLOWED_DBS", "aetherion_test")

    mock_pg_store = MagicMock()
    with patch("execution.durable_state_postgres.PostgresDurableExecutionStore", return_value=mock_pg_store) as mock_cls:
        store = durable_state.configure_test_store(test_dsn)
        assert store is mock_pg_store
        assert durable_state.get_store() is mock_pg_store
        mock_cls.assert_called_once_with(test_dsn)


    durable_state.reset_store()


def test_durable_state_normal_runtime_does_not_inherit_test_config(monkeypatch):
    """In standard non-test runtime, durable_state ignores test URL and uses normal DATABASE_URL/SQLite."""
    from backend.execution import durable_state

    durable_state.reset_store()
    monkeypatch.delenv("AETHERION_TEST_MODE", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    # Provide a test URL that would otherwise be rejected
    monkeypatch.setenv("AETHERION_TEST_DATABASE_URL", "postgresql://test:pass@test-host:5432/test_db")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    store = durable_state.default_store()
    # In non-test local development without DATABASE_URL, normal behavior is SqliteDurableExecutionStore
    assert isinstance(store, durable_state.SqliteDurableExecutionStore)

    durable_state.reset_store()
