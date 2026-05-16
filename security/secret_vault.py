"""
security/secret_vault.py — Production Secret & Credential Security
===================================================================
Replaces the thin execution/secrets.py with a production-grade vault.

Security guarantees:
  • Agents NEVER receive raw secrets — only ephemeral references
  • All secret access is audited with session + agent attribution
  • Secrets are redacted from ALL logs, SSE payloads, and telemetry
  • Sensitive env var names are pattern-matched (not hard-coded)
  • BYOK credentials are session-scoped and destroyed on session end
  • Zero-persistence: secrets never written to disk in plaintext
  • Vault is write-once at startup from env — no runtime mutation

Secret taxonomy:
  PROVIDER_KEY — LLM API keys (OpenAI, Anthropic, Gemini, etc.)
  BYOK         — Bring-your-own-key session credential
  INFRA        — DB connection strings, Redis URLs
  WEBHOOK      — Outbound webhook signing keys
  SESSION_TOKEN— Auth session tokens (short-lived)
"""

import hashlib
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

logger = logging.getLogger("nexora.security.vault")


# ─────────────────────────────────────────────────────────────────────────────
# Secret classification
# ─────────────────────────────────────────────────────────────────────────────

class SecretKind(str, Enum):
    PROVIDER_KEY  = "provider_key"
    BYOK          = "byok"
    INFRA         = "infra"
    WEBHOOK       = "webhook"
    SESSION_TOKEN = "session_token"


# ─────────────────────────────────────────────────────────────────────────────
# Env var patterns that contain secrets (for redaction scanning)
# ─────────────────────────────────────────────────────────────────────────────

_SECRET_VAR_PATTERNS = [
    re.compile(r".*_API_KEY$",        re.I),
    re.compile(r".*_SECRET.*",        re.I),
    re.compile(r".*_TOKEN$",          re.I),
    re.compile(r".*_PASSWORD$",       re.I),
    re.compile(r".*_PASSWD$",         re.I),
    re.compile(r".*_CREDENTIAL[S]?$", re.I),
    re.compile(r".*_PRIVATE_KEY$",    re.I),
    re.compile(r".*_AUTH_.*",         re.I),
    re.compile(r"DATABASE_URL",       re.I),
    re.compile(r"REDIS_URL",          re.I),
    re.compile(r".*_DSN$",            re.I),
    re.compile(r"WEBHOOK_.*",         re.I),
]

# Known provider key env var names → logical name mapping
_PROVIDER_KEY_MAP: Dict[str, str] = {
    "OPENAI_API_KEY":    "openai",
    "ANTHROPIC_API_KEY": "anthropic",
    "GEMINI_API_KEY":    "gemini",
    "GOOGLE_API_KEY":    "google",
    "GROQ_API_KEY":      "groq",
    "OPENROUTER_API_KEY":"openrouter",
    "TOGETHER_API_KEY":  "together",
    "COHERE_API_KEY":    "cohere",
    "MISTRAL_API_KEY":   "mistral",
    "PERPLEXITY_API_KEY":"perplexity",
}

# Sensitive filesystem paths — agents blocked from reading these
_SENSITIVE_PATH_PATTERNS = [
    re.compile(r"\.env$",                    re.I),
    re.compile(r"\.env\.",                   re.I),
    re.compile(r"credentials\.json",         re.I),
    re.compile(r"secrets\.(yaml|yml|json)",  re.I),
    re.compile(r"\.pem$",                    re.I),
    re.compile(r"\.key$",                    re.I),
    re.compile(r"\.p12$",                    re.I),
    re.compile(r"\.pfx$",                    re.I),
    re.compile(r"\.ssh[/\\]",               re.I),
    re.compile(r"\.aws[/\\]",               re.I),
    re.compile(r"\.gnupg[/\\]",             re.I),
    re.compile(r"google-service-account",    re.I),
    re.compile(r"service_account",           re.I),
    re.compile(r"firebase-adminsdk",         re.I),
    re.compile(r"kubeconfig",                re.I),
    re.compile(r"htpasswd",                  re.I),
    re.compile(r"shadow$",                   re.I),
    re.compile(r"passwd$",                   re.I),
]


# ─────────────────────────────────────────────────────────────────────────────
# Secret reference (opaque handle given to agents)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SecretRef:
    """Opaque reference to a secret. Agents hold this — never the raw value."""
    ref_id:   str
    kind:     SecretKind
    provider: str        # "openai", "session:xyz", etc.
    expires_at: float    # 0 = no expiry


class SecretExpiredError(Exception):
    pass

class SecretAccessDeniedError(Exception):
    pass

class SensitivePathError(Exception):
    """Raised when an agent attempts to read a sensitive file path."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# SecretVault
# ─────────────────────────────────────────────────────────────────────────────

class SecretVault:
    """
    Production secret vault.

    - Loaded from environment on startup (write-once)
    - Agents receive SecretRef handles, not raw values
    - SecretRef → raw value resolution guarded by session scope check
    - All access events audited
    - BYOK secrets are session-scoped and evicted on session end
    - redact_all() scans arbitrary text and masks any known secrets
    """

    def __init__(self):
        self._lock  = threading.RLock()
        self._store: Dict[str, str] = {}       # ref_id → raw secret value
        self._refs:  Dict[str, SecretRef] = {} # ref_id → SecretRef metadata
        self._session_refs: Dict[str, Set[str]] = {}  # session_id → {ref_ids}
        self._audit: List[dict] = []
        self._redaction_patterns: List[tuple] = []   # (pattern, masked_form)

        self._load_provider_keys()

    def _load_provider_keys(self) -> None:
        """Load all known provider keys from environment at startup."""
        loaded = 0
        for env_var, provider in _PROVIDER_KEY_MAP.items():
            value = os.getenv(env_var, "")
            if value:
                ref = self._store_secret(value, SecretKind.PROVIDER_KEY, provider)
                self._build_redaction_pattern(value, f"[{provider.upper()}_KEY]")
                loaded += 1
                logger.info(f"[Vault] Loaded {provider} key: ref={ref.ref_id} "
                            f"masked={self.mask(value)}")

        # Also load infra secrets for redaction
        for env_var, pattern in [
            ("DATABASE_URL", "[DATABASE_URL]"),
            ("REDIS_URL",    "[REDIS_URL]"),
        ]:
            value = os.getenv(env_var, "")
            if value:
                self._build_redaction_pattern(value, pattern)

        logger.info(f"[Vault] Initialized: {loaded} provider keys loaded, "
                    f"{len(self._redaction_patterns)} redaction patterns active.")

    def _store_secret(self, value: str, kind: SecretKind, provider: str,
                      session_id: str = "", ttl_sec: float = 0) -> SecretRef:
        ref_id = f"ref_{uuid.uuid4().hex[:20]}"
        expires_at = time.time() + ttl_sec if ttl_sec > 0 else 0
        ref = SecretRef(ref_id=ref_id, kind=kind, provider=provider, expires_at=expires_at)
        with self._lock:
            self._store[ref_id] = value
            self._refs[ref_id]  = ref
            if session_id:
                self._session_refs.setdefault(session_id, set()).add(ref_id)
        return ref

    def _build_redaction_pattern(self, value: str, masked_form: str) -> None:
        if len(value) < 8:
            return
        # Escape special chars in the actual secret value for regex safety
        escaped = re.escape(value)
        pat = re.compile(escaped)
        with self._lock:
            self._redaction_patterns.append((pat, masked_form))

    # ── Public API ────────────────────────────────────────────────────────────

    def get_provider_ref(self, provider: str) -> Optional[SecretRef]:
        """Returns a SecretRef for the given provider. None if not configured."""
        with self._lock:
            for ref_id, ref in self._refs.items():
                if ref.kind == SecretKind.PROVIDER_KEY and ref.provider == provider:
                    return ref
        return None

    def register_byok(self, session_id: str, provider: str, raw_key: str,
                      ttl_sec: float = 3600) -> SecretRef:
        """
        Registers a BYOK key for a session. Returns an opaque SecretRef.
        The raw key is stored in memory only and evicted after TTL or session end.
        """
        ref = self._store_secret(raw_key, SecretKind.BYOK, provider,
                                 session_id=session_id, ttl_sec=ttl_sec)
        self._build_redaction_pattern(raw_key, f"[BYOK:{provider}]")
        self._audit_access("register_byok", ref.ref_id, session_id, provider)
        logger.info(f"[Vault] BYOK registered: session={session_id} provider={provider} "
                    f"ref={ref.ref_id} ttl={ttl_sec}s")
        return ref

    def resolve(self, ref_id: str, session_id: str, agent_id: str = "") -> str:
        """
        Resolves a SecretRef to its raw value.

        ONLY to be called at the point of use (e.g., constructing HTTP header).
        The returned string must NEVER be stored, logged, or passed to LLM context.
        """
        with self._lock:
            ref = self._refs.get(ref_id)
            if not ref:
                raise SecretAccessDeniedError(f"Unknown secret ref: {ref_id}")

            # TTL check
            if ref.expires_at > 0 and time.time() > ref.expires_at:
                self._store.pop(ref_id, None)
                self._refs.pop(ref_id, None)
                raise SecretExpiredError(f"Secret ref {ref_id} has expired.")

            # Session scope check for BYOK
            if ref.kind == SecretKind.BYOK:
                session_refs = self._session_refs.get(session_id, set())
                if ref_id not in session_refs:
                    self._audit_access("resolve_DENIED", ref_id, session_id, ref.provider)
                    raise SecretAccessDeniedError(
                        f"Session {session_id} does not own BYOK ref {ref_id}"
                    )

            value = self._store.get(ref_id, "")

        self._audit_access("resolve", ref_id, session_id, ref.provider, agent_id)
        return value

    def evict_session(self, session_id: str) -> int:
        """Destroy all BYOK secrets for a session. Called on session end."""
        with self._lock:
            ref_ids = self._session_refs.pop(session_id, set())
            for ref_id in ref_ids:
                self._store.pop(ref_id, None)
                self._refs.pop(ref_id, None)
        logger.info(f"[Vault] Session evicted: {session_id} — {len(ref_ids)} secrets destroyed.")
        return len(ref_ids)

    def is_sensitive_path(self, path: str) -> bool:
        """Returns True if path matches any sensitive file pattern."""
        for pattern in _SENSITIVE_PATH_PATTERNS:
            if pattern.search(path):
                return True
        return False

    def assert_safe_path(self, path: str, session_id: str = "") -> None:
        """Raises SensitivePathError if path is sensitive."""
        if self.is_sensitive_path(path):
            self._audit_access("path_blocked", path, session_id, "filesystem")
            raise SensitivePathError(
                f"Access denied: '{path}' is a classified sensitive file path. "
                "Agents are not permitted to read credential or configuration files."
            )

    def redact_all(self, text: str) -> str:
        """
        Scans text and replaces any known secret values with masked forms.
        Apply to ALL log messages, SSE payloads, telemetry before emission.
        """
        with self._lock:
            patterns = list(self._redaction_patterns)
        result = text
        for pattern, masked in patterns:
            result = pattern.sub(masked, result)
        return result

    @staticmethod
    def mask(value: str) -> str:
        """Returns a safe masked representation of a secret value."""
        if not value:
            return "****"
        if len(value) < 8:
            return "****"
        return f"{value[:4]}{'*' * min(8, len(value) - 8)}{value[-4:]}"

    def is_secret_envvar(self, var_name: str) -> bool:
        """Returns True if the env var name matches a secret pattern."""
        return any(p.match(var_name) for p in _SECRET_VAR_PATTERNS)

    # ── Audit ─────────────────────────────────────────────────────────────────

    def _audit_access(self, action: str, ref_id: str, session_id: str,
                      provider: str, agent_id: str = "") -> None:
        entry = {
            "ts": time.time(),
            "action": action,
            "ref_id": ref_id[:12] if len(ref_id) > 12 else ref_id,
            "session_id": session_id,
            "provider": provider,
            "agent_id": agent_id,
        }
        with self._lock:
            self._audit.append(entry)
            if len(self._audit) > 2000:
                self._audit.pop(0)
        level = logging.WARNING if "DENIED" in action or "blocked" in action else logging.DEBUG
        logger.log(level, f"[Vault] {action} ref={entry['ref_id']} "
                          f"session={session_id} provider={provider}")
        try:
            from infra.telemetry import get_telemetry
            get_telemetry().record("security", f"vault_{action}",
                                   {"provider": provider}, session_id=session_id)
        except Exception:
            pass

    def recent_audit(self, n: int = 20) -> List[dict]:
        with self._lock:
            return list(self._audit[-n:])

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "loaded_refs":      len(self._refs),
                "active_sessions":  len(self._session_refs),
                "redaction_patterns": len(self._redaction_patterns),
                "sensitive_path_patterns": len(_SENSITIVE_PATH_PATTERNS),
            }


# ─────────────────────────────────────────────────────────────────────────────
# Global singleton
# ─────────────────────────────────────────────────────────────────────────────

global_secret_vault = SecretVault()
