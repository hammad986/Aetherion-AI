"""
backend/execution/providers/store.py — Persistent SQLite store for registered providers.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import platform
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aetherion.providers.store")

# Default database location
DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "providers.db"


def _derive_encryption_key() -> bytes:
    """Derive stable host-specific encryption key for API credentials."""
    machine_id = os.environ.get("AETHERION_MACHINE_KEY", "")
    if not machine_id:
        try:
            if platform.system() == "Windows":
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
                    machine_id, _ = winreg.QueryValueEx(k, "MachineGuid")
            else:
                for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                    if os.path.exists(p):
                        machine_id = Path(p).read_text().strip()
                        break
        except Exception:
            machine_id = ""

    if not machine_id:
        machine_id = platform.node() or "aetherion_host_node"

    raw = f"aetherion_secret_salt_v2_{machine_id}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest)


_CIPHER = None


def _get_cipher():
    global _CIPHER
    if _CIPHER is None:
        try:
            from cryptography.fernet import Fernet
            _CIPHER = Fernet(_derive_encryption_key())
        except Exception as exc:
            logger.warning("Fernet initialization failed: %s; using internal obfuscator", exc)
            _CIPHER = False
    return _CIPHER


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt an API key for persistent at-rest storage."""
    if not plaintext:
        return ""
    cipher = _get_cipher()
    if cipher:
        try:
            token = cipher.encrypt(plaintext.encode("utf-8"))
            return f"enc:{token.decode('utf-8')}"
        except Exception as exc:
            logger.error("Encryption failed: %s", exc)
    # Reversible obfuscation fallback
    raw = plaintext.encode("utf-8")
    salt = hashlib.sha256(_derive_encryption_key()).digest()
    xor_bytes = bytes([b ^ salt[i % len(salt)] for i, b in enumerate(raw)])
    return f"obf:{base64.b64encode(xor_bytes).decode('ascii')}"


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt an encrypted API key back to plaintext for HTTP dispatch only."""
    if not ciphertext:
        return ""
    cipher = _get_cipher()
    if ciphertext.startswith("enc:") and cipher:
        token = ciphertext[4:].encode("utf-8")
        try:
            return cipher.decrypt(token).decode("utf-8")
        except Exception as exc:
            logger.error("Decryption failed: %s", exc)
            return ""
    elif ciphertext.startswith("obf:"):
        try:
            raw = base64.b64decode(ciphertext[4:].encode("ascii"))
            salt = hashlib.sha256(_derive_encryption_key()).digest()
            dec_bytes = bytes([b ^ salt[i % len(salt)] for i, b in enumerate(raw)])
            return dec_bytes.decode("utf-8")
        except Exception as exc:
            logger.error("Obfuscation decode failed: %s", exc)
            return ""
    # Plain text fallback (e.g. legacy records)
    return ciphertext


def mask_api_key(key: str) -> str:
    """Mask API key for safe public display and logs (e.g. sk-...4a1b)."""
    if not key:
        return ""
    clean = key.strip()
    if len(clean) <= 6:
        return "sk-***"
    suffix = clean[-4:]
    return f"sk-...{suffix}"


class ProviderStore:
    """SQLite-backed persistent store for user-defined providers."""

    def __init__(self, db_path: Optional[Path | str] = None):
        if db_path is None:
            db_path = os.environ.get("AETHERION_PROVIDERS_DB", str(DEFAULT_DB_PATH))
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS registered_providers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    provider_type TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    api_key_encrypted TEXT NOT NULL DEFAULT '',
                    default_model TEXT NOT NULL,
                    available_models TEXT NOT NULL DEFAULT '[]',
                    is_default INTEGER NOT NULL DEFAULT 0,
                    is_enabled INTEGER NOT NULL DEFAULT 1,
                    custom_headers TEXT NOT NULL DEFAULT '{}',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    owner_id TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            cursor = conn.execute("PRAGMA table_info(registered_providers)")
            cols = [col[1] for col in cursor.fetchall()]
            if "owner_id" not in cols:
                conn.execute("ALTER TABLE registered_providers ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''")
            conn.commit()

    def insert(self, record: Dict[str, Any], owner_id: Optional[str] = None) -> Dict[str, Any]:
        """Insert a new provider record."""
        now = time.time()
        pid = record.get("id") or f"prov_{int(now * 1000)}"
        plain_key = record.get("api_key", "")
        enc_key = encrypt_api_key(plain_key) if plain_key else record.get("api_key_encrypted", "")
        owner = str(owner_id or record.get("owner_id") or "")

        is_def = 1 if record.get("is_default") else 0
        is_en = 1 if record.get("is_enabled", True) else 0

        models_json = json.dumps(record.get("available_models", []))
        headers_json = json.dumps(record.get("custom_headers", {}))
        meta_json = json.dumps(record.get("metadata", {}))

        with self._get_connection() as conn:
            if is_def == 1:
                if owner:
                    conn.execute("UPDATE registered_providers SET is_default = 0 WHERE owner_id = ?", (owner,))
                else:
                    conn.execute("UPDATE registered_providers SET is_default = 0")

            conn.execute("""
                INSERT INTO registered_providers (
                    id, name, provider_type, base_url, api_key_encrypted,
                    default_model, available_models, is_default, is_enabled,
                    custom_headers, metadata, owner_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pid,
                record["name"],
                record.get("provider_type", "OPENAI_COMPATIBLE"),
                record["base_url"].rstrip("/"),
                enc_key,
                record.get("default_model", ""),
                models_json,
                is_def,
                is_en,
                headers_json,
                meta_json,
                owner,
                now,
                now,
            ))
            conn.commit()

        return self.get(pid, include_raw_key=False, owner_id=owner)

    def get(self, provider_id: str, include_raw_key: bool = False, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieve provider record by ID, optionally scoped to owner."""
        with self._get_connection() as conn:
            if owner_id is not None:
                row = conn.execute(
                    "SELECT * FROM registered_providers WHERE id = ? AND (owner_id = ? OR owner_id = '')",
                    (provider_id, str(owner_id)),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM registered_providers WHERE id = ?", (provider_id,)
                ).fetchone()
            if not row:
                return None
            return self._row_to_dict(row, include_raw_key=include_raw_key)

    def get_by_name(self, name: str, include_raw_key: bool = False, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieve provider record by unique name, optionally scoped to owner."""
        with self._get_connection() as conn:
            if owner_id is not None:
                row = conn.execute(
                    "SELECT * FROM registered_providers WHERE name = ? AND (owner_id = ? OR owner_id = '')",
                    (name, str(owner_id)),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM registered_providers WHERE name = ?", (name,)
                ).fetchone()
            if not row:
                return None
            return self._row_to_dict(row, include_raw_key=include_raw_key)

    def list_all(self, include_raw_keys: bool = False, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all providers ordered by is_default DESC, name ASC."""
        with self._get_connection() as conn:
            if owner_id is not None:
                rows = conn.execute(
                    "SELECT * FROM registered_providers WHERE owner_id = ? OR owner_id = '' ORDER BY is_default DESC, name ASC",
                    (str(owner_id),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM registered_providers ORDER BY is_default DESC, name ASC"
                ).fetchall()
            return [self._row_to_dict(r, include_raw_key=include_raw_keys) for r in rows]

    def update(self, provider_id: str, updates: Dict[str, Any], owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Update provider record scoped to owner."""
        existing = self.get(provider_id, include_raw_key=True, owner_id=owner_id)
        if not existing:
            return None

        set_clauses: List[str] = []
        params: List[Any] = []

        if "name" in updates:
            set_clauses.append("name = ?")
            params.append(updates["name"])
        if "provider_type" in updates:
            set_clauses.append("provider_type = ?")
            params.append(updates["provider_type"])
        if "base_url" in updates:
            set_clauses.append("base_url = ?")
            params.append(updates["base_url"].rstrip("/"))
        if "api_key" in updates:
            set_clauses.append("api_key_encrypted = ?")
            params.append(encrypt_api_key(updates["api_key"]))
        if "default_model" in updates:
            set_clauses.append("default_model = ?")
            params.append(updates["default_model"])
        if "available_models" in updates:
            set_clauses.append("available_models = ?")
            params.append(json.dumps(updates["available_models"]))
        if "is_default" in updates:
            is_def = 1 if updates["is_default"] else 0
            if is_def == 1:
                with self._get_connection() as conn:
                    if owner_id is not None:
                        conn.execute("UPDATE registered_providers SET is_default = 0 WHERE owner_id = ?", (str(owner_id),))
                    else:
                        conn.execute("UPDATE registered_providers SET is_default = 0")
            set_clauses.append("is_default = ?")
            params.append(is_def)
        if "is_enabled" in updates:
            set_clauses.append("is_enabled = ?")
            params.append(1 if updates["is_enabled"] else 0)
        if "custom_headers" in updates:
            set_clauses.append("custom_headers = ?")
            params.append(json.dumps(updates["custom_headers"]))
        if "metadata" in updates:
            set_clauses.append("metadata = ?")
            params.append(json.dumps(updates["metadata"]))

        set_clauses.append("updated_at = ?")
        params.append(time.time())
        params.append(provider_id)

        where_clause = "WHERE id = ?"
        if owner_id is not None:
            where_clause += " AND (owner_id = ? OR owner_id = '')"
            params.append(str(owner_id))

        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE registered_providers SET {', '.join(set_clauses)} {where_clause}",
                params,
            )
            conn.commit()

        return self.get(provider_id, include_raw_key=False, owner_id=owner_id)

    def delete(self, provider_id: str, owner_id: Optional[str] = None) -> bool:
        """Delete provider record."""
        existing = self.get(provider_id, owner_id=owner_id)
        if not existing:
            return False
        with self._get_connection() as conn:
            if owner_id is not None:
                cur = conn.execute(
                    "DELETE FROM registered_providers WHERE id = ? AND (owner_id = ? OR owner_id = '')",
                    (provider_id, str(owner_id)),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM registered_providers WHERE id = ?", (provider_id,)
                )
            conn.commit()
            return cur.rowcount > 0

    def set_default(self, provider_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Set specified provider as default, unsetting any previous default."""
        existing = self.get(provider_id, owner_id=owner_id)
        if not existing:
            return None
        with self._get_connection() as conn:
            if owner_id is not None:
                conn.execute("UPDATE registered_providers SET is_default = 0 WHERE owner_id = ?", (str(owner_id),))
            else:
                conn.execute("UPDATE registered_providers SET is_default = 0")
            conn.execute(
                "UPDATE registered_providers SET is_default = 1, is_enabled = 1, updated_at = ? WHERE id = ?",
                (time.time(), provider_id),
            )
            conn.commit()
        return self.get(provider_id, owner_id=owner_id)

    def set_enabled(self, provider_id: str, enabled: bool, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Enable or disable provider."""
        existing = self.get(provider_id, owner_id=owner_id)
        if not existing:
            return None
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE registered_providers SET is_enabled = ?, updated_at = ? WHERE id = ?",
                (1 if enabled else 0, time.time(), provider_id),
            )
            conn.commit()
        return self.get(provider_id, owner_id=owner_id)

    def _row_to_dict(self, row: sqlite3.Row, include_raw_key: bool = False) -> Dict[str, Any]:
        enc_key = row["api_key_encrypted"]
        raw_key = decrypt_api_key(enc_key) if enc_key else ""
        masked_key = mask_api_key(raw_key)

        try:
            models = json.loads(row["available_models"])
        except Exception:
            models = []

        try:
            headers = json.loads(row["custom_headers"])
        except Exception:
            headers = {}

        try:
            meta = json.loads(row["metadata"])
        except Exception:
            meta = {}

        d = {
            "id": row["id"],
            "name": row["name"],
            "provider_type": row["provider_type"],
            "base_url": row["base_url"],
            "default_model": row["default_model"],
            "available_models": models,
            "is_default": bool(row["is_default"]),
            "is_enabled": bool(row["is_enabled"]),
            "custom_headers": headers,
            "metadata": meta,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "owner_id": row["owner_id"] if "owner_id" in row.keys() else "",
            "api_key_masked": masked_key,
        }
        if include_raw_key:
            d["api_key"] = raw_key
        return d
