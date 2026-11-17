"""
tests/integration/test_phase12_production_trust.py
===================================================
Authoritative Phase 12 verification test suite covering:
  1. Explicit Environment Identity & Configuration Invariants
  2. Fail-Closed Trust Kernel & Mandatory Prerequisites
  3. Preflight Diagnostic Engine & Structured Reports
  4. Zero-Secret-Leakage Guarantees
  5. Livez / Readyz Probe Separation & Task Readiness Contracts
  6. CLI Doctor Human-Readable & Machine-Readable Contracts
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from aetherion_cli.main import cli
from core.environment import (
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
from core.preflight import (
    CheckResult,
    CheckSeverity,
    CheckStatus,
    PreflightReport,
    audit_no_secret_leakage,
    is_weak_secret,
    run_preflight,
    sanitize_dsn,
)


# ---------------------------------------------------------------------------
# 1. Configuration & Explicit Environment Identity Tests
# ---------------------------------------------------------------------------

class TestEnvironmentAndConfiguration:
    def test_explicit_development_configuration(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "development")
        assert get_environment_mode() == EnvironmentMode.LOCAL_DEVELOPMENT
        assert is_local_dev() is True
        assert is_production() is False
        assert is_staging() is False
        assert is_fail_closed_environment() is False
        valid, mode, err = validate_environment_identity()
        assert valid is True
        assert mode == EnvironmentMode.LOCAL_DEVELOPMENT
        assert err == ""

    def test_explicit_staging_configuration(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "staging")
        assert get_environment_mode() == EnvironmentMode.STAGING
        assert is_staging() is True
        assert is_production() is False
        assert is_fail_closed_environment() is True
        valid, mode, err = validate_environment_identity()
        assert valid is True
        assert mode == EnvironmentMode.STAGING

    def test_explicit_production_configuration(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "production")
        assert get_environment_mode() == EnvironmentMode.PRODUCTION
        assert is_production() is True
        assert is_staging() is False
        assert is_fail_closed_environment() is True
        valid, mode, err = validate_environment_identity()
        assert valid is True
        assert mode == EnvironmentMode.PRODUCTION

    def test_explicit_ci_test_configuration(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "ci-test")
        assert get_environment_mode() == EnvironmentMode.CI_TEST
        assert is_ci_test() is True
        assert is_fail_closed_environment() is False
        valid, mode, _ = validate_environment_identity()
        assert valid is True
        assert mode == EnvironmentMode.CI_TEST

    def test_unknown_environment_fails_closed(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "prduction_typo")
        mode = get_environment_mode()
        assert mode == EnvironmentMode.UNKNOWN
        assert is_fail_closed_environment() is True
        valid, mode, err = validate_environment_identity()
        assert valid is False
        assert mode == EnvironmentMode.UNKNOWN
        assert "Unrecognized environment value" in err

    def test_missing_production_secret_fails_closed(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.delenv("JWT_SECRET", raising=False)
        report = run_preflight(probe_live=False)
        jwt_check = next(c for c in report.checks if c.check_id == "security.jwt_secret")
        assert jwt_check.status == CheckStatus.FAIL
        assert jwt_check.severity == CheckSeverity.CRITICAL
        assert jwt_check.required is True
        assert report.ready is False

    def test_weak_production_secret_fails_closed(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.setenv("JWT_SECRET", "changeme_short_and_insecure")
        report = run_preflight(probe_live=False)
        jwt_check = next(c for c in report.checks if c.check_id == "security.jwt_secret")
        assert jwt_check.status == CheckStatus.FAIL
        assert jwt_check.severity == CheckSeverity.CRITICAL
        assert report.ready is False

    def test_identical_production_secrets_fails_closed(self, monkeypatch):
        shared_secret = "a" * 40
        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.setenv("JWT_SECRET", shared_secret)
        monkeypatch.setenv("SECRET_KEY", shared_secret)
        report = run_preflight(probe_live=False)
        indep_check = next(c for c in report.checks if c.check_id == "security.secrets_independence")
        assert indep_check.status == CheckStatus.FAIL
        assert indep_check.severity == CheckSeverity.CRITICAL
        assert report.ready is False

    def test_missing_required_database_in_production(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        report = run_preflight(probe_live=False)
        db_check = next(c for c in report.checks if c.check_id == "database.readiness")
        assert db_check.status == CheckStatus.FAIL
        assert db_check.severity == CheckSeverity.CRITICAL
        assert db_check.required is True
        assert report.ready is False

    def test_sqlite_in_production_fails_closed(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
        report = run_preflight(probe_live=False)
        db_check = next(c for c in report.checks if c.check_id == "database.readiness")
        assert db_check.status == CheckStatus.FAIL
        assert db_check.severity == CheckSeverity.CRITICAL
        assert "SQLite" in db_check.detail
        assert report.ready is False

    def test_missing_required_redis_in_production(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.delenv("REDIS_URL", raising=False)
        report = run_preflight(probe_live=False)
        redis_check = next(c for c in report.checks if c.check_id == "redis.coordination")
        assert redis_check.status == CheckStatus.FAIL
        assert redis_check.severity == CheckSeverity.CRITICAL
        assert report.ready is False

    def test_unsafe_dev_auth_fallback_blocked_in_production(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.setenv("ALLOW_DEV_AUTH", "1")
        report = run_preflight(probe_live=False)
        dev_auth_check = next(c for c in report.checks if c.check_id == "security.dev_auth_guard")
        assert dev_auth_check.status == CheckStatus.FAIL
        assert dev_auth_check.severity == CheckSeverity.CRITICAL
        assert report.ready is False

    def test_missing_sandbox_docker_capability_in_production(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.setenv("REQUIRE_SANDBOX", "true")
        with patch("docker.from_env", side_effect=Exception("daemon offline")):
            report = run_preflight(probe_live=False)
            docker_check = next(c for c in report.checks if c.check_id == "sandbox.docker")
            assert docker_check.status == CheckStatus.FAIL
            assert docker_check.severity == CheckSeverity.CRITICAL
            assert report.ready is False

        # When override REQUIRE_SANDBOX=false is explicitly set:
        monkeypatch.setenv("REQUIRE_SANDBOX", "false")
        with patch("docker.from_env", side_effect=Exception("daemon offline")):
            report = run_preflight(probe_live=False)
            docker_check = next(c for c in report.checks if c.check_id == "sandbox.docker")
            assert docker_check.status == CheckStatus.WARN
            assert docker_check.severity == CheckSeverity.WARNING

    def test_valid_production_configuration(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.setenv("SERVER_URL", "https://api.aetherion.ai")
        monkeypatch.setenv("JWT_SECRET", "1" * 32)
        monkeypatch.setenv("SECRET_KEY", "2" * 32)
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/aetherion")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-testkey1234567890")
        monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "ws"))
        monkeypatch.setenv("REQUIRE_SANDBOX", "false")  # Host degraded mode without Docker

        report = run_preflight(probe_live=False)
        assert report.environment == "production"
        assert report.healthy is True
        assert report.ready is True
        assert report.summary["critical_fails"] == 0


# ---------------------------------------------------------------------------
# 2. Preflight Trust Kernel & Structured Diagnostic Tests
# ---------------------------------------------------------------------------

class TestPreflightTrustKernel:
    def test_preflight_all_checks_pass_structured(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AETHERION_ENV", "local-development")
        monkeypatch.setenv("JWT_SECRET", "k" * 32)
        monkeypatch.setenv("SECRET_KEY", "s" * 32)
        monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "ws"))

        report = run_preflight(probe_live=False)
        assert isinstance(report, PreflightReport)
        assert report.healthy is True
        assert report.ready is True
        assert len(report.checks) >= 9

    def test_preflight_single_critical_dependency_fails(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.setenv("JWT_SECRET", "k" * 32)
        monkeypatch.setenv("SECRET_KEY", "s" * 32)
        monkeypatch.delenv("DATABASE_URL", raising=False)  # Missing DB

        report = run_preflight(probe_live=False)
        assert report.ready is False
        assert report.summary["critical_fails"] >= 1
        fails = [c for c in report.checks if c.status == CheckStatus.FAIL]
        assert any(c.check_id == "database.readiness" for c in fails)

    def test_preflight_multiple_critical_dependencies_fail(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.delenv("JWT_SECRET", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)

        report = run_preflight(probe_live=False)
        assert report.ready is False
        assert report.summary["critical_fails"] >= 4

    def test_preflight_optional_dependency_unavailable(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "local-development")
        monkeypatch.delenv("REDIS_URL", raising=False)

        report = run_preflight(probe_live=False)
        redis_check = next(c for c in report.checks if c.check_id == "redis.coordination")
        assert redis_check.status == CheckStatus.WARN
        assert redis_check.required is False
        assert report.ready is True

    def test_preflight_structured_result_correctness(self, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "local-development")
        report = run_preflight(probe_live=False)
        data = report.to_dict()

        assert "environment" in data
        assert "healthy" in data
        assert "ready" in data
        assert "checks" in data
        assert "summary" in data
        assert isinstance(data["checks"], list)
        for check in data["checks"]:
            assert "check_id" in check
            assert "name" in check
            assert check["status"] in ("PASS", "FAIL", "WARN")
            assert check["severity"] in ("CRITICAL", "WARNING", "INFO")
            assert "ok" in check
            assert "detail" in check

    def test_secret_values_never_appear_in_diagnostics(self, monkeypatch):
        raw_jwt = "super_confidential_jwt_secret_token_12345"
        raw_key = "super_confidential_flask_session_key_98765"
        raw_db_pass = "super_secret_db_pass_xyz987"
        raw_api_key = "sk-ant-live-super-secret-production-api-key"

        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.setenv("JWT_SECRET", raw_jwt)
        monkeypatch.setenv("SECRET_KEY", raw_key)
        monkeypatch.setenv("DATABASE_URL", f"postgresql://admin:{raw_db_pass}@pg.internal:5432/aetherion")
        monkeypatch.setenv("ANTHROPIC_API_KEY", raw_api_key)

        report = run_preflight(probe_live=False)
        report_json = json.dumps(report.to_dict())

        # Assert no sensitive values leaked in serialized diagnostics
        assert raw_jwt not in report_json
        assert raw_key not in report_json
        assert raw_db_pass not in report_json
        assert raw_api_key not in report_json


# ---------------------------------------------------------------------------
# 3. Startup, Readiness, and Liveness Probe Tests
# ---------------------------------------------------------------------------

class TestReadinessAndHealthProbes:
    @pytest.fixture
    def client(self):
        from app import create_app
        app = create_app()
        app.testing = True
        return app.test_client()

    def test_livez_independent_of_database_outage(self, client, monkeypatch):
        # Even if DB is unreachable or misconfigured, livez must return 200
        monkeypatch.setenv("DATABASE_URL", "postgresql://unreachable.host:5432/db")
        resp = client.get("/livez")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "pid" in data
        assert "uptime_seconds" in data

    def test_readyz_returns_200_when_ready(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("AETHERION_ENV", "local-development")
        monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "ws"))
        resp = client.get("/readyz")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ready"
        assert data["ready"] is True
        assert "checks" in data
        assert "database" in data["checks"]
        assert "environment" in data["checks"]

    def test_readyz_returns_503_when_not_ready(self, client, monkeypatch):
        monkeypatch.setenv("AETHERION_ENV", "production")
        monkeypatch.delenv("DATABASE_URL", raising=False)  # Missing DB in prod
        resp = client.get("/readyz")
        assert resp.status_code == 503
        data = resp.get_json()
        assert data["status"] == "not_ready"
        assert data["ready"] is False
        assert "checks" in data

    def test_deterministic_readiness_payload_schema(self, client):
        resp = client.get("/readyz")
        assert resp.status_code in (200, 503)
        data = resp.get_json()
        assert set(data.keys()) >= {"status", "ready", "environment", "checks", "timestamp", "summary"}


# ---------------------------------------------------------------------------
# 4. CLI Doctor Verification & Preflight Tests
# ---------------------------------------------------------------------------

class TestDoctorCLI:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_doctor_human_readable_output(self, runner):
        mock_client = MagicMock()
        mock_client.base_url = "http://localhost:5000"
        mock_client.token = "test-token"
        mock_client.health.return_value = {"status": "ok", "version": "1.0.0"}
        mock_client.get.return_value = {"status": "ready", "ready": True}

        with patch("aetherion_cli.main._get_client", return_value=mock_client):
            result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 0
        assert "Backend reachable" in result.output
        assert "Python" in result.output

    def test_doctor_machine_readable_json_output(self, runner):
        mock_client = MagicMock()
        mock_client.base_url = "http://localhost:5000"
        mock_client.token = "test-token"
        mock_client.health.return_value = {"status": "ok", "version": "1.0.0"}
        mock_client.get.return_value = {"status": "ready", "ready": True}

        with patch("aetherion_cli.main._get_client", return_value=mock_client):
            result = runner.invoke(cli, ["doctor", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["healthy"] is True
        assert "checks" in data
        assert isinstance(data["checks"], list)

    def test_doctor_nonzero_exit_code_on_critical_failure(self, runner):
        mock_client = MagicMock()
        mock_client.base_url = "http://localhost:5000"
        mock_client.token = None
        mock_client.health.return_value = {"status": "offline", "error": "Connection refused", "offline": True}

        with patch("aetherion_cli.main._get_client", return_value=mock_client):
            result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 1

    def test_doctor_zero_exit_code_on_valid_configuration(self, runner):
        mock_client = MagicMock()
        mock_client.base_url = "http://localhost:5000"
        mock_client.token = "valid-token"
        mock_client.health.return_value = {"status": "ok", "version": "1.0.0"}
        mock_client.get.return_value = {"status": "ready", "ready": True}

        with patch("aetherion_cli.main._get_client", return_value=mock_client):
            result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 0

    def test_no_duplicate_command_registration(self):
        command_names = list(cli.commands.keys())
        assert len(command_names) == len(set(command_names)), "Duplicate command registrations found on CLI!"
        assert "doctor" in command_names
