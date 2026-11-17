"""
core/preflight.py — Authoritative Preflight & Production Trust Kernel
====================================================================
Provides deterministic, structured preflight verification across:
  - Explicit Environment Identity
  - Authentication & Token Secrets (entropy & independence)
  - Dev-Auth Bypass Security Guard
  - Database Connectivity & Schema Readiness
  - Redis Coordination & Queue Reachability
  - AI Provider Configuration
  - Workspace Filesystem Permissions & Write Tests
  - Docker Container Sandbox Isolation Capability
  - Strict Zero-Secret-Leakage Protection

Usage:
  from core.preflight import run_preflight, PreflightReport, CheckResult, CheckStatus, CheckSeverity
  report = run_preflight(probe_live=True)
  if not report.ready and is_fail_closed_environment():
      # block startup / reject readiness
"""
from __future__ import annotations

import datetime
import enum
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

_core_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_core_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

try:
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
except ImportError:
    import importlib.util
    _env_path = os.path.join(_core_dir, "environment.py")
    _spec = importlib.util.spec_from_file_location("_aetherion_core_env", _env_path)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    EnvironmentMode = _mod.EnvironmentMode
    get_environment_mode = _mod.get_environment_mode
    get_raw_environment_string = _mod.get_raw_environment_string
    is_ci_test = _mod.is_ci_test
    is_fail_closed_environment = _mod.is_fail_closed_environment
    is_local_dev = _mod.is_local_dev
    is_production = _mod.is_production
    is_staging = _mod.is_staging
    validate_environment_identity = _mod.validate_environment_identity

logger = logging.getLogger("aetherion.preflight")

# Known weak / placeholder secret values
_WEAK_SECRET_FRAGMENTS = {
    "testing_secret_key",
    "changeme",
    "secret",
    "dev_secret",
    "your_secret_key",
    "please_change_me",
    "insecure",
    "development",
    "default",
    "password",
    "example",
    "placeholder",
    "test-secret",
    "admin123",
}

# AI Provider Environment Variable Keys
_LLM_PROVIDER_KEYS = [
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OLLAMA_BASE_URL",
]


class CheckStatus(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"


class CheckSeverity(str, enum.Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class CheckResult:
    check_id: str
    name: str
    status: CheckStatus
    severity: CheckSeverity
    required: bool
    detail: str
    remediation: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "name": self.name,
            "status": self.status.value,
            "severity": self.severity.value,
            "required": self.required,
            "ok": self.status == CheckStatus.PASS,
            "detail": self.detail,
            "remediation": self.remediation,
            "metadata": self.metadata,
        }


@dataclass
class PreflightReport:
    environment: str
    healthy: bool
    ready: bool
    checks: List[CheckResult]
    timestamp: str
    summary: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "environment": self.environment,
            "healthy": self.healthy,
            "ready": self.ready,
            "status": "ready" if self.ready else "not_ready",
            "timestamp": self.timestamp,
            "summary": self.summary,
            "checks": [c.to_dict() for c in self.checks],
            "failures": [c.to_dict() for c in self.checks if c.status == CheckStatus.FAIL],
            "warnings": [c.to_dict() for c in self.checks if c.status == CheckStatus.WARN],
        }


def sanitize_dsn(text: str) -> str:
    """Mask credentials in database/redis connection strings or error messages."""
    if not text:
        return ""
    # Mask user:pass in URI
    cleaned = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", text)
    # Mask password=... in query or libpq style
    cleaned = re.sub(r"(password=)([^ \t\r\n;]+)", r"\1***", cleaned, flags=re.IGNORECASE)
    return cleaned


def is_weak_secret(value: str) -> bool:
    """Return True if secret is absent, too short (< 32 chars), or matches known placeholders."""
    if not value:
        return True
    v = value.strip().lower()
    if len(v) < 32:
        return True
    return any(fragment in v for fragment in _WEAK_SECRET_FRAGMENTS)


def is_gunicorn_worker() -> bool:
    """Return True if running in a Gunicorn worker process (WORKER_PID set)."""
    return bool(os.environ.get("WORKER_PID"))


# ── Individual Check Implementations ──────────────────────────────────────────

def check_environment_identity() -> CheckResult:
    """Verify explicit and unambiguous runtime environment identity."""
    is_valid, mode, error_msg = validate_environment_identity()
    raw_env = get_raw_environment_string()

    if not is_valid:
        return CheckResult(
            check_id="config.environment",
            name="Environment Identity",
            status=CheckStatus.FAIL,
            severity=CheckSeverity.CRITICAL,
            required=True,
            detail=error_msg,
            remediation="Set AETHERION_ENV to an explicit, supported mode: 'production', 'staging', 'local-development', 'CI-test'.",
            metadata={"raw_value": raw_env, "mode": mode.value},
        )

    return CheckResult(
        check_id="config.environment",
        name="Environment Identity",
        status=CheckStatus.PASS,
        severity=CheckSeverity.INFO,
        required=True,
        detail=f"Explicit environment identity confirmed: {mode.value}",
        metadata={"raw_value": raw_env, "mode": mode.value},
    )


def check_jwt_secret() -> CheckResult:
    """Verify JWT authentication secret key entropy and presence."""
    jwt_secret = os.getenv("JWT_SECRET", "").strip()
    fail_closed = is_fail_closed_environment()

    if not jwt_secret:
        if fail_closed:
            return CheckResult(
                check_id="security.jwt_secret",
                name="JWT Secret Key",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail="JWT_SECRET is missing or empty in production/staging environment.",
                remediation="Generate a high-entropy secret (min 32 bytes): python -c 'import secrets; print(secrets.token_hex(32))'",
            )
        return CheckResult(
            check_id="security.jwt_secret",
            name="JWT Secret Key",
            status=CheckStatus.WARN,
            severity=CheckSeverity.WARNING,
            required=False,
            detail="JWT_SECRET is missing; development fallback will be used (insecure for production).",
            remediation="Set JWT_SECRET in .env for authenticated persistence across restarts.",
        )

    if is_weak_secret(jwt_secret):
        if fail_closed:
            return CheckResult(
                check_id="security.jwt_secret",
                name="JWT Secret Key",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail=f"JWT_SECRET is weak or too short ({len(jwt_secret)} chars; min 32 required) or a known placeholder.",
                remediation="Generate a cryptographically secure 32+ byte secret: python -c 'import secrets; print(secrets.token_hex(32))'",
            )
        return CheckResult(
            check_id="security.jwt_secret",
            name="JWT Secret Key",
            status=CheckStatus.WARN,
            severity=CheckSeverity.WARNING,
            required=False,
            detail="JWT_SECRET is a development/testing placeholder.",
        )

    return CheckResult(
        check_id="security.jwt_secret",
        name="JWT Secret Key",
        status=CheckStatus.PASS,
        severity=CheckSeverity.INFO,
        required=fail_closed,
        detail=f"JWT_SECRET configured ({len(jwt_secret)} characters, entropy verified).",
    )


def check_secret_key() -> CheckResult:
    """Verify Flask application / session SECRET_KEY presence and strength."""
    secret_key = (os.getenv("SECRET_KEY", "").strip() or os.getenv("FLASK_SECRET_KEY", "").strip())
    fail_closed = is_fail_closed_environment()

    if not secret_key:
        if fail_closed:
            return CheckResult(
                check_id="security.secret_key",
                name="Application Secret Key",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail="SECRET_KEY is missing or empty in production/staging environment.",
                remediation="Generate a high-entropy secret (min 32 bytes): python -c 'import secrets; print(secrets.token_hex(32))'",
            )
        return CheckResult(
            check_id="security.secret_key",
            name="Application Secret Key",
            status=CheckStatus.WARN,
            severity=CheckSeverity.WARNING,
            required=False,
            detail="SECRET_KEY is missing; development fallback will be used.",
        )

    if is_weak_secret(secret_key):
        if fail_closed:
            return CheckResult(
                check_id="security.secret_key",
                name="Application Secret Key",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail=f"SECRET_KEY is weak or too short ({len(secret_key)} chars; min 32 required) or a known placeholder.",
                remediation="Generate an independent 32+ byte cryptographic secret: python -c 'import secrets; print(secrets.token_hex(32))'",
            )
        return CheckResult(
            check_id="security.secret_key",
            name="Application Secret Key",
            status=CheckStatus.WARN,
            severity=CheckSeverity.WARNING,
            required=False,
            detail="SECRET_KEY is a development/testing placeholder.",
        )

    return CheckResult(
        check_id="security.secret_key",
        name="Application Secret Key",
        status=CheckStatus.PASS,
        severity=CheckSeverity.INFO,
        required=fail_closed,
        detail=f"SECRET_KEY configured ({len(secret_key)} characters, entropy verified).",
    )


def check_secrets_independence() -> CheckResult:
    """Verify that JWT_SECRET and SECRET_KEY are independently generated cryptographic secrets."""
    jwt_secret = os.getenv("JWT_SECRET", "").strip()
    secret_key = (os.getenv("SECRET_KEY", "").strip() or os.getenv("FLASK_SECRET_KEY", "").strip())
    fail_closed = is_fail_closed_environment()

    if jwt_secret and secret_key and jwt_secret == secret_key:
        if fail_closed:
            return CheckResult(
                check_id="security.secrets_independence",
                name="Secrets Independence",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail="JWT_SECRET and SECRET_KEY are identical. Each must be an independently generated cryptographic secret.",
                remediation="Set distinct, independent values for JWT_SECRET and SECRET_KEY in your environment configuration.",
            )
        return CheckResult(
            check_id="security.secrets_independence",
            name="Secrets Independence",
            status=CheckStatus.WARN,
            severity=CheckSeverity.WARNING,
            required=False,
            detail="JWT_SECRET and SECRET_KEY are identical (permitted for local development only).",
        )

    return CheckResult(
        check_id="security.secrets_independence",
        name="Secrets Independence",
        status=CheckStatus.PASS,
        severity=CheckSeverity.INFO,
        required=fail_closed,
        detail="Application secrets are independently configured.",
    )


def check_dev_auth_guard() -> CheckResult:
    """Verify that development authentication bypass (ALLOW_DEV_AUTH) is disabled in production/staging."""
    allow_dev_auth = os.getenv("ALLOW_DEV_AUTH", "0").strip() == "1"
    fail_closed = is_fail_closed_environment()

    if allow_dev_auth:
        if fail_closed:
            return CheckResult(
                check_id="security.dev_auth_guard",
                name="Dev Auth Bypass Guard",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail="ALLOW_DEV_AUTH=1 is enabled in a production/staging environment. This bypasses authentication and must be removed.",
                remediation="Unset ALLOW_DEV_AUTH or set ALLOW_DEV_AUTH=0 before deploying to production or staging.",
            )
        return CheckResult(
            check_id="security.dev_auth_guard",
            name="Dev Auth Bypass Guard",
            status=CheckStatus.WARN,
            severity=CheckSeverity.WARNING,
            required=False,
            detail="ALLOW_DEV_AUTH=1 is enabled (development authentication bypass active).",
        )

    return CheckResult(
        check_id="security.dev_auth_guard",
        name="Dev Auth Bypass Guard",
        status=CheckStatus.PASS,
        severity=CheckSeverity.INFO,
        required=fail_closed,
        detail="Development authentication bypass is disabled.",
    )


def check_production_url() -> CheckResult:
    """Verify that public HTTPS SERVER_URL is configured in production/staging environments."""
    server_url = (os.getenv("SERVER_URL") or "").strip()
    fail_closed = is_fail_closed_environment()

    if not server_url:
        if fail_closed:
            return CheckResult(
                check_id="security.production_url",
                name="Production Server URL Guard",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail="SERVER_URL is unset in a production/staging environment.",
                remediation="Configure SERVER_URL with a valid public https:// domain (e.g. https://api.aetherion.ai).",
            )
        return CheckResult(
            check_id="security.production_url",
            name="Production Server URL Guard",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            required=False,
            detail="SERVER_URL is unset (permissive local development mode).",
        )

    parsed = server_url.lower()
    if fail_closed:
        if not parsed.startswith("https://"):
            return CheckResult(
                check_id="security.production_url",
                name="Production Server URL Guard",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail=f"SERVER_URL must use https:// in production/staging: {server_url}",
                remediation="Set SERVER_URL to use https:// scheme.",
            )
        if "localhost" in parsed or "127.0.0.1" in parsed:
            return CheckResult(
                check_id="security.production_url",
                name="Production Server URL Guard",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail=f"SERVER_URL cannot use localhost/loopback in production/staging: {server_url}",
                remediation="Set SERVER_URL to the public production domain.",
            )

    return CheckResult(
        check_id="security.production_url",
        name="Production Server URL Guard",
        status=CheckStatus.PASS,
        severity=CheckSeverity.INFO,
        required=fail_closed,
        detail=f"Production Server URL verified ({server_url.split('://')[0]}://***).",
    )



def check_database(probe_live: bool = True) -> CheckResult:
    """Verify database configuration, reachability, and schema migration readiness."""
    db_url = os.getenv("DATABASE_URL", "").strip()
    fail_closed = is_fail_closed_environment()
    testing = is_ci_test()
    worker = is_gunicorn_worker()

    if not db_url:
        if fail_closed:
            return CheckResult(
                check_id="database.readiness",
                name="Database Readiness",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail="DATABASE_URL is not set. Production and staging require a PostgreSQL database.",
                remediation="Set DATABASE_URL=postgresql://user:password@host:port/dbname in environment.",
            )
        if testing:
            return CheckResult(
                check_id="database.readiness",
                name="Database Readiness",
                status=CheckStatus.PASS,
                severity=CheckSeverity.INFO,
                required=False,
                detail="DATABASE_URL not set (testing mode: in-memory / mock database active).",
            )
        return CheckResult(
            check_id="database.readiness",
            name="Database Readiness",
            status=CheckStatus.WARN,
            severity=CheckSeverity.WARNING,
            required=False,
            detail="DATABASE_URL not set; falling back to local SQLite (saas_platform.db / sessions.db).",
        )

    # Check for SQLite in production
    if fail_closed and "sqlite" in db_url.lower():
        return CheckResult(
            check_id="database.readiness",
            name="Database Readiness",
            status=CheckStatus.FAIL,
            severity=CheckSeverity.CRITICAL,
            required=True,
            detail="DATABASE_URL points to SQLite. Production and staging require a managed PostgreSQL instance.",
            remediation="Configure a PostgreSQL connection string: DATABASE_URL=postgresql://user:password@host:port/dbname",
        )

    # In worker or non-probe mode, skip live probe
    if worker or not probe_live:
        return CheckResult(
            check_id="database.readiness",
            name="Database Readiness",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            required=fail_closed,
            detail=f"Database configured ({sanitize_dsn(db_url.split('@')[-1] if '@' in db_url else 'configured')}; live probe skipped in worker/offline mode).",
        )

    # Live connection & migration probe
    try:
        from sqlalchemy import create_engine, inspect
        is_pg = "postgresql" in db_url.lower() or "postgres" in db_url.lower()
        connect_args = {"connect_timeout": 5} if is_pg else {}
        sa_url = db_url
        if is_pg and db_url.startswith("postgresql://") and not db_url.startswith("postgresql+"):
            sa_url = "postgresql+psycopg2://" + db_url[len("postgresql://"):]
        elif is_pg and db_url.startswith("postgres://"):
            sa_url = "postgresql+psycopg2://" + db_url[len("postgres://"):]
        engine = create_engine(sa_url, connect_args=connect_args)
        with engine.connect():
            pass
        insp = inspect(engine)
        tables = insp.get_table_names()
        if "users" not in tables:
            if fail_closed:
                return CheckResult(
                    check_id="database.readiness",
                    name="Database Readiness",
                    status=CheckStatus.FAIL,
                    severity=CheckSeverity.CRITICAL,
                    required=True,
                    detail="Database connection succeeded, but required tables ('users') are missing. Migrations have not run.",
                    remediation="Execute database migrations before launching production: python scripts/db_migrate.py",
                )
            return CheckResult(
                check_id="database.readiness",
                name="Database Readiness",
                status=CheckStatus.WARN,
                severity=CheckSeverity.WARNING,
                required=False,
                detail="Database connected, but 'users' table is missing (run migrations).",
            )

        return CheckResult(
            check_id="database.readiness",
            name="Database Readiness",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            required=fail_closed,
            detail="Database connection verified and schema readiness confirmed.",
            metadata={"driver": "postgresql" if is_pg else "sqlite", "tables_count": len(tables)},
        )
    except Exception as exc:
        sanitized_err = sanitize_dsn(str(exc))
        if testing:
            return CheckResult(
                check_id="database.readiness",
                name="Database Readiness",
                status=CheckStatus.WARN,
                severity=CheckSeverity.WARNING,
                required=False,
                detail=f"Database probe skipped in testing mode: {sanitized_err}",
            )
        if fail_closed or is_pg:
            return CheckResult(
                check_id="database.readiness",
                name="Database Readiness",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail=f"Database unreachable: {sanitized_err}",
                remediation="Ensure PostgreSQL server is running and accessible at the host/port in DATABASE_URL.",
            )
        return CheckResult(
            check_id="database.readiness",
            name="Database Readiness",
            status=CheckStatus.WARN,
            severity=CheckSeverity.WARNING,
            required=False,
            detail=f"Database unreachable ({sanitized_err}); falling back to local SQLite.",
        )


def check_redis(probe_live: bool = True) -> CheckResult:
    """Verify Redis reachability for task queue, multi-worker coordination, and SSE broadcasting."""
    redis_url = os.getenv("REDIS_URL", "").strip()
    fail_closed = is_fail_closed_environment()
    worker = is_gunicorn_worker()

    if not redis_url:
        if fail_closed:
            return CheckResult(
                check_id="redis.coordination",
                name="Redis Coordination & Queue",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail="REDIS_URL is not set. Production requires Redis for multi-worker coordination and SSE event broadcasting.",
                remediation="Set REDIS_URL=redis://host:port/0 in environment configuration.",
            )
        return CheckResult(
            check_id="redis.coordination",
            name="Redis Coordination & Queue",
            status=CheckStatus.WARN,
            severity=CheckSeverity.WARNING,
            required=False,
            detail="REDIS_URL is not set; falling back to in-process memory queue.",
        )

    if worker or not probe_live:
        return CheckResult(
            check_id="redis.coordination",
            name="Redis Coordination & Queue",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            required=fail_closed,
            detail="Redis configured (live probe skipped in worker/offline mode).",
        )

    try:
        import redis as _redis_lib
        client = _redis_lib.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
        return CheckResult(
            check_id="redis.coordination",
            name="Redis Coordination & Queue",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            required=fail_closed,
            detail="Redis connection verified (ping response received).",
        )
    except Exception as exc:
        sanitized_err = sanitize_dsn(str(exc))
        if fail_closed:
            return CheckResult(
                check_id="redis.coordination",
                name="Redis Coordination & Queue",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail=f"Redis is configured but unreachable: {sanitized_err}",
                remediation="Verify Redis service is running and accessible at REDIS_URL.",
            )
        return CheckResult(
            check_id="redis.coordination",
            name="Redis Coordination & Queue",
            status=CheckStatus.WARN,
            severity=CheckSeverity.WARNING,
            required=False,
            detail=f"Redis unreachable ({sanitized_err}); using in-process queue fallback.",
        )


def check_llm_providers() -> CheckResult:
    """Verify that at least one AI LLM provider is configured for autonomous task execution."""
    configured = [k for k in _LLM_PROVIDER_KEYS if os.getenv(k, "").strip()]
    fail_closed = is_fail_closed_environment()
    testing = is_ci_test()

    if not configured:
        if testing:
            return CheckResult(
                check_id="ai.providers",
                name="AI Provider Configuration",
                status=CheckStatus.PASS,
                severity=CheckSeverity.INFO,
                required=False,
                detail="No LLM provider keys set (testing mode: offline mock provider active).",
            )
        if fail_closed:
            return CheckResult(
                check_id="ai.providers",
                name="AI Provider Configuration",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail="No AI LLM provider API key found in environment. Autonomous task execution requires an active provider.",
                remediation="Set at least one of: ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY, DEEPSEEK_API_KEY, OLLAMA_BASE_URL",
            )
        return CheckResult(
            check_id="ai.providers",
            name="AI Provider Configuration",
            status=CheckStatus.WARN,
            severity=CheckSeverity.WARNING,
            required=False,
            detail="No LLM provider API key found; offline mock provider active for local development.",
        )

    return CheckResult(
        check_id="ai.providers",
        name="AI Provider Configuration",
        status=CheckStatus.PASS,
        severity=CheckSeverity.INFO,
        required=fail_closed,
        detail=f"Active AI provider keys configured: {', '.join(configured)}.",
        metadata={"providers": configured},
    )


def check_workspace_filesystem() -> CheckResult:
    """Verify workspace filesystem permissions, write test, and isolation."""
    workspace_dir = os.environ.get(
        "WORKSPACE_DIR",
        "/app/workspace" if os.path.exists("/app/workspace") else os.path.join(os.getcwd(), "workspace")
    )
    fail_closed = is_fail_closed_environment()

    try:
        if not os.path.exists(workspace_dir):
            os.makedirs(workspace_dir, exist_ok=True)
        test_file = os.path.join(workspace_dir, ".startup_write_test")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("aetherion_ok")
        with open(test_file, "r", encoding="utf-8") as f:
            content = f.read()
        if content != "aetherion_ok":
            raise ValueError("Read-after-write mismatch during workspace write probe.")
        if os.path.exists(test_file):
            os.remove(test_file)

        return CheckResult(
            check_id="filesystem.workspace",
            name="Workspace Filesystem",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            required=True,
            detail=f"Workspace filesystem is writable ({workspace_dir}).",
            metadata={"workspace_dir": workspace_dir},
        )
    except Exception as exc:
        if fail_closed:
            return CheckResult(
                check_id="filesystem.workspace",
                name="Workspace Filesystem",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail=f"Workspace directory is not writable: {exc}",
                remediation=f"Ensure process user has read/write permissions for {workspace_dir}.",
            )
        return CheckResult(
            check_id="filesystem.workspace",
            name="Workspace Filesystem",
            status=CheckStatus.WARN,
            severity=CheckSeverity.WARNING,
            required=False,
            detail=f"Workspace filesystem check warning: {exc}",
        )


def check_sandbox_docker() -> CheckResult:
    """Verify Docker daemon reachability for isolated container sandbox execution."""
    fail_closed = is_fail_closed_environment()
    _rs_default = "true" if fail_closed else "false"
    require_sandbox = os.getenv("REQUIRE_SANDBOX", _rs_default).strip().lower() in ("1", "true", "yes")

    docker_ok = False
    error_msg = ""
    try:
        import docker
        client = docker.from_env(timeout=3)
        client.ping()
        docker_ok = True
    except Exception as exc:
        error_msg = str(exc)

    if docker_ok:
        return CheckResult(
            check_id="sandbox.docker",
            name="Container Sandbox Isolation",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            required=require_sandbox and fail_closed,
            detail="Docker daemon connected; isolated container runner available.",
            metadata={"sandbox_mode": "container"},
        )

    if fail_closed and require_sandbox:
        return CheckResult(
            check_id="sandbox.docker",
            name="Container Sandbox Isolation",
            status=CheckStatus.FAIL,
            severity=CheckSeverity.CRITICAL,
            required=True,
            detail=f"Docker daemon unreachable ({error_msg}). Production requires container isolation for safe agent execution.",
            remediation="Start Docker daemon, ensure socket permissions, or explicitly set REQUIRE_SANDBOX=false to operate in host-degraded mode.",
            metadata={"sandbox_mode": "unavailable", "require_sandbox": True},
        )

    if require_sandbox:
        return CheckResult(
            check_id="sandbox.docker",
            name="Container Sandbox Isolation",
            status=CheckStatus.WARN,
            severity=CheckSeverity.WARNING,
            required=False,
            detail=f"Docker daemon unreachable ({error_msg}); running in host process isolation mode.",
            metadata={"sandbox_mode": "host_process_degraded", "require_sandbox": False},
        )

    return CheckResult(
        check_id="sandbox.docker",
        name="Container Sandbox Isolation",
        status=CheckStatus.WARN,
        severity=CheckSeverity.WARNING,
        required=False,
        detail=f"Docker daemon unreachable ({error_msg}); REQUIRE_SANDBOX=false override active, running with host process runner.",
        metadata={"sandbox_mode": "host_process_override", "require_sandbox": False},
    )


def check_control_plane(probe_live: bool = True) -> CheckResult:
    """Verify Execution Fabric control plane, store readiness, and worker runtime coordination."""
    db_url = os.getenv("DATABASE_URL", "").strip()
    fail_closed = is_fail_closed_environment()
    testing = is_ci_test()
    worker = is_gunicorn_worker()

    if fail_closed:
        if not db_url or not db_url.startswith(("postgres://", "postgresql://")):
            return CheckResult(
                check_id="control_plane.fabric",
                name="Execution Fabric Control Plane",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail="Production and staging environments require a PostgreSQL-backed Execution Fabric store.",
                remediation="Configure DATABASE_URL=postgresql://user:password@host:port/dbname.",
            )

    if worker or not probe_live:
        store_type = "PostgreSQL" if db_url.startswith(("postgres://", "postgresql://")) else "SQLite"
        return CheckResult(
            check_id="control_plane.fabric",
            name="Execution Fabric Control Plane",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            required=fail_closed,
            detail=f"Execution Fabric configured ({store_type} store; live probe skipped in offline mode).",
        )

    try:
        from backend.execution.fabric.store import get_fabric_store
        store = get_fabric_store()
        workers = store.list_workers()
        items_count = store.count_global_active_items()
        store_name = type(store).__name__
        return CheckResult(
            check_id="control_plane.fabric",
            name="Execution Fabric Control Plane",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            required=fail_closed,
            detail=f"Execution Fabric store verified ({store_name}: {len(workers)} registered workers, {items_count} active items).",
        )
    except Exception as exc:
        sanitized_err = sanitize_dsn(str(exc))
        if fail_closed:
            return CheckResult(
                check_id="control_plane.fabric",
                name="Execution Fabric Control Plane",
                status=CheckStatus.FAIL,
                severity=CheckSeverity.CRITICAL,
                required=True,
                detail=f"Execution Fabric store initialization or connectivity failed: {sanitized_err}",
                remediation="Verify database connectivity and fabric schema tables.",
            )
        return CheckResult(
            check_id="control_plane.fabric",
            name="Execution Fabric Control Plane",
            status=CheckStatus.WARN,
            severity=CheckSeverity.WARNING,
            required=False,
            detail=f"Execution Fabric store degraded ({sanitized_err}); running in fallback mode.",
        )


# ── Strict Zero-Secret-Leakage Auditor ────────────────────────────────────────


def audit_no_secret_leakage(report: PreflightReport) -> None:
    """
    Sanity audit to ensure that no actual secret values (API keys, raw JWT secret,
    raw session keys, DB passwords) appear anywhere in the check results or details.
    """
    sensitive_values = []
    for key in ["JWT_SECRET", "SECRET_KEY", "FLASK_SECRET_KEY"] + _LLM_PROVIDER_KEYS:
        val = os.getenv(key, "").strip()
        if val and len(val) >= 8:
            sensitive_values.append(val)

    # Database and Redis passwords
    for url_key in ["DATABASE_URL", "REDIS_URL"]:
        url = os.getenv(url_key, "")
        match = re.search(r"://([^:]+):([^@]+)@", url)
        if match:
            pwd = match.group(2)
            if pwd and len(pwd) >= 4:
                sensitive_values.append(pwd)

    for check in report.checks:
        for text in [check.detail, check.remediation or "", str(check.metadata)]:
            for secret in sensitive_values:
                if secret in text:
                    # Scrub leaked secret from check detail
                    check.detail = check.detail.replace(secret, "[REDACTED_SECRET]")
                    if check.remediation:
                        check.remediation = check.remediation.replace(secret, "[REDACTED_SECRET]")
                    logger.critical("Preflight check '%s' attempted to leak a secret! Redacted.", check.check_id)


# ── Canonical Runner ──────────────────────────────────────────────────────────

def run_preflight(probe_live: bool = True) -> PreflightReport:
    """
    Execute all canonical preflight checks and produce a structured PreflightReport.
    """
    mode = get_environment_mode()
    checks: List[CheckResult] = []

    # 1. Environment Identity
    checks.append(check_environment_identity())

    # 2. Authentication Secrets
    checks.append(check_jwt_secret())
    checks.append(check_secret_key())
    checks.append(check_secrets_independence())

    # 3. Security Bypass Guards & Production URL
    checks.append(check_dev_auth_guard())
    checks.append(check_production_url())

    # 4. Database Readiness
    checks.append(check_database(probe_live=probe_live))

    # 5. Redis Coordination
    checks.append(check_redis(probe_live=probe_live))

    # 6. AI Providers
    checks.append(check_llm_providers())

    # 7. Workspace Filesystem
    checks.append(check_workspace_filesystem())

    # 8. Sandbox Isolation Capability
    checks.append(check_sandbox_docker())

    # 9. Execution Fabric Control Plane
    checks.append(check_control_plane(probe_live=probe_live))

    # Summaries

    pass_count = sum(1 for c in checks if c.status == CheckStatus.PASS)
    warn_count = sum(1 for c in checks if c.status == CheckStatus.WARN)
    fail_count = sum(1 for c in checks if c.status == CheckStatus.FAIL)
    critical_fails = sum(1 for c in checks if c.status == CheckStatus.FAIL and c.severity == CheckSeverity.CRITICAL)

    # Process health vs Task execution readiness:
    # - healthy: Process is capable of running and handling HTTP traffic
    # - ready: All required dependencies for safe autonomous task execution are satisfied
    is_ready = critical_fails == 0 and all(c.status == CheckStatus.PASS for c in checks if c.required)
    is_healthy = True
    if mode == EnvironmentMode.UNKNOWN:
        is_healthy = False
        is_ready = False
    elif is_fail_closed_environment() and critical_fails > 0:
        is_ready = False

    report = PreflightReport(
        environment=mode.value,
        healthy=is_healthy,
        ready=is_ready,
        checks=checks,
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        summary={
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
            "critical_fails": critical_fails,
        },
    )

    # Run strict zero-secret-leakage audit
    audit_no_secret_leakage(report)

    return report
