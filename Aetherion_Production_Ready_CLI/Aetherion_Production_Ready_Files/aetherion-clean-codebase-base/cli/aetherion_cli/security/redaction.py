"""
aetherion_cli/security/redaction.py
Utilities to prevent sensitive credentials, tokens, and keys from ever appearing in logs, errors, or CLI outputs.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Union

SENSITIVE_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "secret_key",
    "jwt_secret",
    "api_key",
    "authorization",
    "cookie",
    "private_key",
    "webhook_secret",
}

DSN_PATTERN = re.compile(r"://([^:]+):([^@]+)@")
BEARER_PATTERN = re.compile(r"Bearer\s+([A-Za-z0-9_\-\.]+)", re.IGNORECASE)


def redact_token(token: str | None) -> str:
    """Mask a token, showing only prefix and suffix if long enough."""
    if not token:
        return ""
    if len(token) <= 8:
        return "********"
    return f"{token[:4]}...{token[-4:]}"


def sanitize_dsn(dsn: str) -> str:
    """Mask credentials in database connection strings (e.g. postgresql://user:pass@host)."""
    if not dsn:
        return ""
    return DSN_PATTERN.sub(r"://\1:***@", dsn)


def redact_secrets(data: Any) -> Any:
    """Recursively redact sensitive keys and values from dicts, lists, or strings."""
    if isinstance(data, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in SENSITIVE_KEYS):
                if isinstance(v, str):
                    cleaned[k] = redact_token(v)
                else:
                    cleaned[k] = "********"
            else:
                cleaned[k] = redact_secrets(v)
        return cleaned
    elif isinstance(data, (list, tuple)):
        return [redact_secrets(item) for item in data]
    elif isinstance(data, str):
        # Redact Bearer tokens in headers or log lines
        s = BEARER_PATTERN.sub("Bearer [REDACTED]", data)
        # Redact DSNs in error strings
        s = sanitize_dsn(s)
        return s
    return data
