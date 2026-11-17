"""
core/startup_validator.py — Production Infrastructure Hardening Gate

Rules
─────
• DEVELOPMENT (default): existing warning-only behaviour preserved.
  Fallbacks (in-process Redis, SQLite, no Docker) are allowed; boot continues.

• PRODUCTION (ENVIRONMENT=production): strict fail-fast mode.
  Any of the following cause sys.exit(1):
    - JWT_SECRET or SECRET_KEY missing or known-weak
    - JWT_SECRET == SECRET_KEY (must be independent)
    - DATABASE_URL absent, SQLite-backed, or unreachable
    - REDIS_URL absent or unreachable
    - Docker daemon unreachable  (override: REQUIRE_SANDBOX=false)
    - ALLOW_DEV_AUTH=1
    - Workspace filesystem read-only

• TESTING (TESTING=1 or pytest imported): same as dev, skip DB + LLM checks.
"""

import os
import sys
import logging
from sqlalchemy import create_engine, inspect

logger = logging.getLogger("aetherion.startup_validator")

# ── LLM provider env-key catalogue ───────────────────────────────────────────
_LLM_PROVIDER_KEYS = [
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OLLAMA_BASE_URL",
    "LLM_PROVIDER",
]

# ── Known placeholder / insecure secret values ────────────────────────────────
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
}


_core_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_core_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

try:
    from core.environment import (
        EnvironmentMode,
        get_environment_mode,
        is_production as env_is_production,
        is_staging as env_is_staging,
        is_ci_test as env_is_ci_test,
        is_local_dev as env_is_local_dev,
        is_fail_closed_environment,
    )
except ImportError:
    import importlib.util
    _env_path = os.path.join(_core_dir, "environment.py")
    _spec = importlib.util.spec_from_file_location("_aetherion_core_env", _env_path)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    EnvironmentMode = _mod.EnvironmentMode
    get_environment_mode = _mod.get_environment_mode
    env_is_production = _mod.is_production
    env_is_staging = _mod.is_staging
    env_is_ci_test = _mod.is_ci_test
    env_is_local_dev = _mod.is_local_dev
    is_fail_closed_environment = _mod.is_fail_closed_environment


# ── Preflight trust kernel import ─────────────────────────────────────────────
try:
    from core.preflight import (
        run_preflight,
        CheckStatus,
        CheckSeverity,
        PreflightReport,
        CheckResult,
        sanitize_dsn as _sanitize_dsn_fn,
        is_weak_secret as _is_weak_secret_fn,
    )
except ImportError:
    import importlib.util
    _preflight_path = os.path.join(_core_dir, "preflight.py")
    _spec_pf = importlib.util.spec_from_file_location("_aetherion_core_preflight", _preflight_path)
    _mod_pf = importlib.util.module_from_spec(_spec_pf)
    _spec_pf.loader.exec_module(_mod_pf)
    run_preflight = _mod_pf.run_preflight
    CheckStatus = _mod_pf.CheckStatus
    CheckSeverity = _mod_pf.CheckSeverity
    PreflightReport = _mod_pf.PreflightReport
    CheckResult = _mod_pf.CheckResult
    _sanitize_dsn_fn = _mod_pf.sanitize_dsn
    _is_weak_secret_fn = _mod_pf.is_weak_secret


# ── Mode helpers ──────────────────────────────────────────────────────────────

def _is_testing() -> bool:
    return env_is_ci_test()


def _sanitize_dsn(text: str) -> str:
    """Mask credentials in database/redis connection strings or error messages."""
    return _sanitize_dsn_fn(text)


def _is_production() -> bool:
    return is_fail_closed_environment()


def _is_gunicorn_worker() -> bool:
    """RCA Fix 4: Detect if we are running inside a Gunicorn worker process."""
    return os.environ.get("WORKER_PID", "") != ""


def _is_weak_secret(value: str) -> bool:
    """Return True if the value is absent, too short, or a known placeholder."""
    return _is_weak_secret_fn(value)


# ── Main gate ─────────────────────────────────────────────────────────────────

def validate_startup():
    testing = _is_testing()
    production = _is_production() and not testing
    gunicorn_worker = _is_gunicorn_worker()

    env_mode = get_environment_mode()
    mode_label = env_mode.value.upper()
    print(f"\n[STARTUP] Mode: {mode_label}{'  [WORKER — DB probe skipped]' if gunicorn_worker else ''}")

    # Run canonical preflight trust kernel
    report = run_preflight(probe_live=not gunicorn_worker)

    warnings = [c.detail for c in report.checks if c.status == CheckStatus.WARN]
    errors = [c.detail for c in report.checks if c.status == CheckStatus.FAIL and (c.required or production)]

    # ── Emit results ──────────────────────────────────────────────────────────
    if warnings:
        print("\n[STARTUP VALIDATION WARNINGS]")
        for w in warnings:
            print(f"  - {w}")
        if not production:
            print("Boot continuing with degraded features.\n")

    if errors:
        gate_label = (
            "[STARTUP VALIDATION FAILED — PRODUCTION SAFETY GATE]"
            if production else
            "[STARTUP VALIDATION FAILED]"
        )
        print(f"\n{gate_label}")
        for e in errors:
            print(f"  BLOCK: {e}")
        if production:
            print(
                "\nFix all BLOCK items above before deploying to production, "
                "or set ENVIRONMENT=development to run in degraded development mode.\n"
            )
        else:
            print("\nBoot aborted.\n")
        sys.exit(1)

    print(f"\n[STARTUP VALIDATION PASSED] — Mode: {mode_label}\n")
    return report


if __name__ == "__main__":
    validate_startup()

