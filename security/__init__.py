"""
security/__init__.py — Aetherion Zero-Trust Security Package
=============================================================
Exports the canonical singletons for all security enforcement subsystems.

Import order: threat_model → command_policy → injection_guard →
              secret_vault → browser_policy → security_telemetry → rate_governor

COMPATIBILITY LAYER:
Also re-exports all symbols from the legacy security.py flat module so that
existing `from security import _general_limiter, ...` calls in web_app.py
continue to work even though the security/ package now shadows security.py.
"""

import importlib.util as _ilu
import os as _os

# ── Load the legacy security.py flat module as _security_core ─────────────────
_security_py = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "security.py")
_spec = _ilu.spec_from_file_location("_security_core", _security_py)
_security_core = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_security_core)

# Re-export everything web_app.py needs from the flat module
RateLimiter             = _security_core.RateLimiter
_general_limiter        = _security_core._general_limiter
_task_limiter           = _security_core._task_limiter
_auth_limiter           = _security_core._auth_limiter
_scheduler_limiter      = _security_core._scheduler_limiter
_forgot_pw_limiter      = _security_core._forgot_pw_limiter
_sse_conn_limiter       = _security_core._sse_conn_limiter
_replay_limiter         = _security_core._replay_limiter
_hitl_limiter           = _security_core._hitl_limiter
_deletion_limiter       = _security_core._deletion_limiter
check_rate_limit        = _security_core.check_rate_limit
get_client_ip           = _security_core.get_client_ip
sanitise_text           = _security_core.sanitise_text
sanitise_prompt         = _security_core.sanitise_prompt
sanitise_task_name      = _security_core.sanitise_task_name
validate_file_path      = _security_core.validate_file_path
MAX_CONCURRENT_SESSIONS = _security_core.MAX_CONCURRENT_SESSIONS
MAX_TOKENS_PER_REQUEST  = _security_core.MAX_TOKENS_PER_REQUEST
is_kill_switch_active   = _security_core.is_kill_switch_active
get_app_secret_key      = _security_core.get_app_secret_key
apply_cors_headers      = _security_core.apply_cors_headers
is_production           = _security_core.is_production
ALLOWED_ORIGINS         = _security_core.ALLOWED_ORIGINS

# ── Package-level governance subsystem imports ────────────────────────────────
from security.threat_model       import ThreatSurface, attack_surface_audit
from security.command_policy     import CommandPolicyEngine, CommandDecision, global_command_policy
from security.injection_guard    import PromptInjectionGuard, InjectionVerdict, global_injection_guard
from security.secret_vault       import SecretVault, global_secret_vault
from security.browser_policy     import BrowserPolicyEngine, global_browser_policy
from security.security_telemetry import SecurityTelemetry, get_security_telemetry
from security.rate_governor      import AbuseRateGovernor, global_rate_governor

__all__ = [
    # Legacy flat-module compat
    "RateLimiter",
    "_general_limiter", "_task_limiter", "_auth_limiter",
    "_scheduler_limiter", "_forgot_pw_limiter",
    "_sse_conn_limiter", "_replay_limiter", "_hitl_limiter", "_deletion_limiter",
    "check_rate_limit", "get_client_ip",
    "sanitise_text", "sanitise_prompt", "sanitise_task_name",
    "validate_file_path", "MAX_CONCURRENT_SESSIONS", "MAX_TOKENS_PER_REQUEST",
    "is_kill_switch_active", "get_app_secret_key",
    "apply_cors_headers", "is_production", "ALLOWED_ORIGINS",
    # Governance subsystems
    "ThreatSurface", "attack_surface_audit",
    "CommandPolicyEngine", "CommandDecision", "global_command_policy",
    "PromptInjectionGuard", "InjectionVerdict", "global_injection_guard",
    "SecretVault", "global_secret_vault",
    "BrowserPolicyEngine", "global_browser_policy",
    "SecurityTelemetry", "get_security_telemetry",
    "AbuseRateGovernor", "global_rate_governor",
]
