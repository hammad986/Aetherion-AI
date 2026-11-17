"""
aetherion_cli/auth/token_store.py
Secure token storage leveraging OS keyring with permission-checked file fallback.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import stat
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..security.redaction import redact_token

log = logging.getLogger(__name__)

KEYRING_SERVICE = "aetherion-cli"
KEYRING_ACCESS_KEY = "token"
KEYRING_REFRESH_KEY = "refresh_token"

CONFIG_DIR = Path.home() / ".aetherion"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _check_keyring_available() -> bool:
    try:
        import keyring
        from keyring.backends import fail
        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return False
        return True
    except Exception:
        return False


def _ensure_config_permissions(file_path: Path) -> None:
    """Enforce owner-only read/write permissions on POSIX systems."""
    if sys.platform != "win32" and file_path.exists():
        try:
            current_mode = stat.S_IMODE(os.stat(file_path).st_mode)
            if current_mode & 0o077:  # group or other permissions present
                os.chmod(file_path, 0o600)
        except Exception as e:
            log.warning("Could not set strict permissions on %s: %s", file_path, e)


def _load_config_file() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_config_file(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    _ensure_config_permissions(tmp)
    tmp.replace(CONFIG_FILE)
    _ensure_config_permissions(CONFIG_FILE)


class TokenStore:
    """Unified token storage abstraction."""

    @classmethod
    def get_access_token(cls) -> Optional[str]:
        # 1. Environment variable override
        env = os.environ.get("AETHERION_TOKEN", "").strip()
        if env:
            return env

        # 2. OS Keyring
        if _check_keyring_available():
            try:
                import keyring
                tok = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCESS_KEY)
                if tok:
                    return tok
            except Exception:
                pass

        # 3. File fallback
        cfg = _load_config_file()
        return cfg.get("token") or cfg.get("access_token") or None

    @classmethod
    def set_access_token(cls, token: str) -> str:
        """Store token, returning storage mechanism used ('keyring' or 'file_fallback')."""
        if _check_keyring_available():
            try:
                import keyring
                keyring.set_password(KEYRING_SERVICE, KEYRING_ACCESS_KEY, token)
                cfg = _load_config_file()
                if "token" in cfg or "access_token" in cfg:
                    cfg.pop("token", None)
                    cfg.pop("access_token", None)
                    _save_config_file(cfg)
                return "keyring"
            except Exception:
                pass

        # File fallback with warning
        cfg = _load_config_file()
        cfg["token"] = token
        cfg.pop("access_token", None)
        _save_config_file(cfg)
        return "file_fallback"

    @classmethod
    def get_refresh_token(cls) -> Optional[str]:
        env = os.environ.get("AETHERION_REFRESH_TOKEN", "").strip()
        if env:
            return env

        if _check_keyring_available():
            try:
                import keyring
                rt = keyring.get_password(KEYRING_SERVICE, KEYRING_REFRESH_KEY)
                if rt:
                    return rt
            except Exception:
                pass

        return _load_config_file().get("refresh_token") or None

    @classmethod
    def set_refresh_token(cls, refresh_token: str) -> str:
        if _check_keyring_available():
            try:
                import keyring
                keyring.set_password(KEYRING_SERVICE, KEYRING_REFRESH_KEY, refresh_token)
                cfg = _load_config_file()
                if "refresh_token" in cfg:
                    cfg.pop("refresh_token", None)
                    _save_config_file(cfg)
                return "keyring"
            except Exception:
                pass

        cfg = _load_config_file()
        cfg["refresh_token"] = refresh_token
        _save_config_file(cfg)
        return "file_fallback"

    @classmethod
    def clear_tokens(cls) -> None:
        """Purge both access and refresh tokens from keyring and config file."""
        if _check_keyring_available():
            try:
                import keyring
                try:
                    keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCESS_KEY)
                except Exception:
                    pass
                try:
                    keyring.delete_password(KEYRING_SERVICE, KEYRING_REFRESH_KEY)
                except Exception:
                    pass
            except Exception:
                pass

        cfg = _load_config_file()
        cfg.pop("token", None)
        cfg.pop("access_token", None)
        cfg.pop("refresh_token", None)
        _save_config_file(cfg)

    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        """Inspect authentication status safely without exposing secrets."""
        token = cls.get_access_token()
        rt = cls.get_refresh_token()
        keyring_active = _check_keyring_available()

        storage_kind = "none"
        if token:
            storage_kind = "keyring" if keyring_active else "file_fallback"

        # Check for JWT expiration payload if well-formed
        claims = {}
        is_expired = False
        if token and token.count(".") == 2:
            try:
                payload_part = token.split(".")[1]
                padded = payload_part + "=" * (-len(payload_part) % 4)
                claims = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
                exp = claims.get("exp")
                if exp is not None and isinstance(exp, (int, float)):
                    import time
                    if exp <= time.time():
                        is_expired = True
            except Exception:
                pass

        return {
            "authenticated": bool(token and not is_expired),
            "expired": is_expired,
            "storage_kind": storage_kind,
            "keyring_available": keyring_active,
            "masked_token": redact_token(token),
            "has_refresh_token": bool(rt),
            "claims": {
                "sub": claims.get("sub"),
                "username": claims.get("username") or claims.get("name"),
                "exp": claims.get("exp"),
                "role": claims.get("role"),
            } if claims else None,
        }
