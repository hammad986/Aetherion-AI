"""
backend/execution/extensions/store.py — Persistent SQLite store for plugins, skills, versions, and audit logs.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aetherion.extensions.store")

DEFAULT_EXT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "extensions.db"


class ExtensionStore:
    """Multi-tenant SQLite store for installed plugins, skills, and historical versions."""

    def __init__(self, db_path: Optional[Path | str] = None):
        if db_path is None:
            db_path = os.environ.get("AETHERION_EXTENSIONS_DB", str(DEFAULT_EXT_DB_PATH))
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
                CREATE TABLE IF NOT EXISTS installed_plugins (
                    id TEXT NOT NULL,
                    owner_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    description TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    license TEXT NOT NULL DEFAULT '',
                    entrypoint TEXT NOT NULL,
                    runtime TEXT NOT NULL DEFAULT 'python',
                    permissions TEXT NOT NULL DEFAULT '[]',
                    provided_tools TEXT NOT NULL DEFAULT '[]',
                    dependencies TEXT NOT NULL DEFAULT '[]',
                    install_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    is_enabled INTEGER NOT NULL DEFAULT 1,
                    manifest_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    trust_level TEXT NOT NULL DEFAULT 'UNTRUSTED',
                    PRIMARY KEY (id, owner_id)
                )
            """)

            try:
                conn.execute("ALTER TABLE installed_plugins ADD COLUMN trust_level TEXT NOT NULL DEFAULT 'UNTRUSTED'")
            except Exception:
                pass

            conn.execute("""
                CREATE TABLE IF NOT EXISTS plugin_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plugin_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL DEFAULT '',
                    version TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    install_path TEXT NOT NULL,
                    manifest_json TEXT NOT NULL DEFAULT '{}',
                    installed_at REAL NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS installed_skills (
                    id TEXT NOT NULL,
                    owner_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    description TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    instructions TEXT NOT NULL,
                    required_tools TEXT NOT NULL DEFAULT '[]',
                    permissions TEXT NOT NULL DEFAULT '[]',
                    tags TEXT NOT NULL DEFAULT '[]',
                    triggers TEXT NOT NULL DEFAULT '[]',
                    is_enabled INTEGER NOT NULL DEFAULT 1,
                    install_path TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL,
                    manifest_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (id, owner_id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS skill_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL DEFAULT '',
                    version TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    manifest_json TEXT NOT NULL DEFAULT '{}',
                    installed_at REAL NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS extension_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL DEFAULT '',
                    extension_type TEXT NOT NULL,
                    extension_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)
            conn.commit()

    def log_audit(self, owner_id: str, ext_type: str, ext_id: str, action: str, details: str = "") -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO extension_audit_log (owner_id, extension_type, extension_id, action, details, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(owner_id), ext_type, ext_id, action, details, time.time()),
            )
            conn.commit()

    # ── Plugins Store ─────────────────────────────────────────────────────────

    def save_plugin(self, record: Dict[str, Any], owner_id: str) -> Dict[str, Any]:
        now = time.time()
        pid = record["id"]
        owner = str(owner_id)

        perms_json = json.dumps(record.get("permissions", []))
        tools_json = json.dumps(record.get("provided_tools", []))
        deps_json = json.dumps(record.get("dependencies", []))
        manifest_json = json.dumps(record.get("manifest_json", {}))

        trust_level = str(record.get("trust_level") or record.get("manifest_json", {}).get("trust_level") or "UNTRUSTED")

        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT * FROM installed_plugins WHERE id = ? AND owner_id = ?", (pid, owner)
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE installed_plugins SET
                        name = ?, version = ?, description = ?, author = ?, license = ?,
                        entrypoint = ?, runtime = ?, permissions = ?, provided_tools = ?,
                        dependencies = ?, install_path = ?, content_hash = ?, is_enabled = ?,
                        manifest_json = ?, updated_at = ?, trust_level = ?
                    WHERE id = ? AND owner_id = ?
                """, (
                    record["name"], record["version"], record["description"],
                    record.get("author", ""), record.get("license", ""),
                    record["entrypoint"], record.get("runtime", "python"),
                    perms_json, tools_json, deps_json,
                    str(record["install_path"]), record["content_hash"],
                    1 if record.get("is_enabled", True) else 0,
                    manifest_json, now, trust_level, pid, owner,
                ))
            else:
                conn.execute("""
                    INSERT INTO installed_plugins (
                        id, owner_id, name, version, description, author, license,
                        entrypoint, runtime, permissions, provided_tools, dependencies,
                        install_path, content_hash, is_enabled, manifest_json, created_at, updated_at, trust_level
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pid, owner, record["name"], record["version"], record["description"],
                    record.get("author", ""), record.get("license", ""),
                    record["entrypoint"], record.get("runtime", "python"),
                    perms_json, tools_json, deps_json,
                    str(record["install_path"]), record["content_hash"],
                    1 if record.get("is_enabled", True) else 0,
                    manifest_json, now, now, trust_level,
                ))

            # Record version snapshot
            conn.execute("""
                INSERT INTO plugin_versions (plugin_id, owner_id, version, content_hash, install_path, manifest_json, installed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                pid, owner, record["version"], record["content_hash"],
                str(record["install_path"]), manifest_json, now,
            ))
            conn.commit()

        self.log_audit(owner, "plugin", pid, "INSTALL_OR_UPDATE", f"Version {record['version']}")
        return self.get_plugin(pid, owner)

    def get_plugin(self, plugin_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM installed_plugins WHERE id = ? AND (owner_id = ? OR owner_id = '')",
                (plugin_id, str(owner_id)),
            ).fetchone()
            if not row:
                return None
            return self._row_to_plugin_dict(row)

    def list_plugins(self, owner_id: str, enabled_only: bool = False) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            query = "SELECT * FROM installed_plugins WHERE (owner_id = ? OR owner_id = '')"
            params: List[Any] = [str(owner_id)]
            if enabled_only:
                query += " AND is_enabled = 1"
            query += " ORDER BY name ASC"
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_plugin_dict(r) for r in rows]

    def set_plugin_enabled(self, plugin_id: str, enabled: bool, owner_id: str) -> Optional[Dict[str, Any]]:
        existing = self.get_plugin(plugin_id, owner_id)
        if not existing:
            return None
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE installed_plugins SET is_enabled = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                (1 if enabled else 0, time.time(), plugin_id, str(owner_id)),
            )
            conn.commit()
        action = "ENABLE" if enabled else "DISABLE"
        self.log_audit(str(owner_id), "plugin", plugin_id, action)
        return self.get_plugin(plugin_id, owner_id)

    def delete_plugin(self, plugin_id: str, owner_id: str) -> bool:
        existing = self.get_plugin(plugin_id, owner_id)
        if not existing:
            return False
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM installed_plugins WHERE id = ? AND owner_id = ?", (plugin_id, str(owner_id))
            )
            conn.commit()
        self.log_audit(str(owner_id), "plugin", plugin_id, "REMOVE")
        return True

    def get_plugin_versions(self, plugin_id: str, owner_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM plugin_versions WHERE plugin_id = ? AND (owner_id = ? OR owner_id = '') ORDER BY installed_at DESC",
                (plugin_id, str(owner_id)),
            ).fetchall()
            return [{
                "version": r["version"],
                "content_hash": r["content_hash"],
                "install_path": r["install_path"],
                "manifest_json": r["manifest_json"],
                "installed_at": r["installed_at"],
            } for r in rows]

    # ── Skills Store ──────────────────────────────────────────────────────────

    def save_skill(self, record: Dict[str, Any], owner_id: str) -> Dict[str, Any]:
        now = time.time()
        sid = record["id"]
        owner = str(owner_id)

        tools_json = json.dumps(record.get("required_tools", []))
        perms_json = json.dumps(record.get("permissions", []))
        tags_json = json.dumps(record.get("tags", []))
        triggers_json = json.dumps(record.get("triggers", []))
        manifest_json = json.dumps(record.get("manifest_json", {}))

        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT * FROM installed_skills WHERE id = ? AND owner_id = ?", (sid, owner)
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE installed_skills SET
                        name = ?, version = ?, description = ?, author = ?, instructions = ?,
                        required_tools = ?, permissions = ?, tags = ?, triggers = ?,
                        is_enabled = ?, install_path = ?, content_hash = ?, manifest_json = ?, updated_at = ?
                    WHERE id = ? AND owner_id = ?
                """, (
                    record["name"], record["version"], record["description"],
                    record.get("author", ""), record["instructions"],
                    tools_json, perms_json, tags_json, triggers_json,
                    1 if record.get("is_enabled", True) else 0,
                    str(record.get("install_path", "")), record["content_hash"],
                    manifest_json, now, sid, owner,
                ))
            else:
                conn.execute("""
                    INSERT INTO installed_skills (
                        id, owner_id, name, version, description, author, instructions,
                        required_tools, permissions, tags, triggers, is_enabled,
                        install_path, content_hash, manifest_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sid, owner, record["name"], record["version"], record["description"],
                    record.get("author", ""), record["instructions"],
                    tools_json, perms_json, tags_json, triggers_json,
                    1 if record.get("is_enabled", True) else 0,
                    str(record.get("install_path", "")), record["content_hash"],
                    manifest_json, now, now,
                ))

            conn.execute("""
                INSERT INTO skill_versions (skill_id, owner_id, version, content_hash, instructions, manifest_json, installed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                sid, owner, record["version"], record["content_hash"],
                record["instructions"], manifest_json, now,
            ))
            conn.commit()

        self.log_audit(owner, "skill", sid, "INSTALL_OR_UPDATE", f"Version {record['version']}")
        return self.get_skill(sid, owner)

    def get_skill(self, skill_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM installed_skills WHERE id = ? AND (owner_id = ? OR owner_id = '')",
                (skill_id, str(owner_id)),
            ).fetchone()
            if not row:
                return None
            return self._row_to_skill_dict(row)

    def list_skills(self, owner_id: str, enabled_only: bool = False) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            query = "SELECT * FROM installed_skills WHERE (owner_id = ? OR owner_id = '')"
            params: List[Any] = [str(owner_id)]
            if enabled_only:
                query += " AND is_enabled = 1"
            query += " ORDER BY name ASC"
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_skill_dict(r) for r in rows]

    def set_skill_enabled(self, skill_id: str, enabled: bool, owner_id: str) -> Optional[Dict[str, Any]]:
        existing = self.get_skill(skill_id, owner_id)
        if not existing:
            return None
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE installed_skills SET is_enabled = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                (1 if enabled else 0, time.time(), skill_id, str(owner_id)),
            )
            conn.commit()
        action = "ENABLE" if enabled else "DISABLE"
        self.log_audit(str(owner_id), "skill", skill_id, action)
        return self.get_skill(skill_id, owner_id)

    def delete_skill(self, skill_id: str, owner_id: str) -> bool:
        existing = self.get_skill(skill_id, owner_id)
        if not existing:
            return False
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM installed_skills WHERE id = ? AND owner_id = ?", (skill_id, str(owner_id))
            )
            conn.commit()
        self.log_audit(str(owner_id), "skill", skill_id, "REMOVE")
        return True

    def get_skill_versions(self, skill_id: str, owner_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM skill_versions WHERE skill_id = ? AND (owner_id = ? OR owner_id = '') ORDER BY installed_at DESC",
                (skill_id, str(owner_id)),
            ).fetchall()
            return [{
                "version": r["version"],
                "content_hash": r["content_hash"],
                "instructions": r["instructions"],
                "manifest_json": r["manifest_json"],
                "installed_at": r["installed_at"],
            } for r in rows]

    # ── Converters ────────────────────────────────────────────────────────────

    def _row_to_plugin_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        try: perms = json.loads(row["permissions"])
        except Exception: perms = []
        try: tools = json.loads(row["provided_tools"])
        except Exception: tools = []
        try: deps = json.loads(row["dependencies"])
        except Exception: deps = []
        try: meta = json.loads(row["manifest_json"])
        except Exception: meta = {}

        trust_lvl = "UNTRUSTED"
        if "trust_level" in row.keys() and row["trust_level"]:
            trust_lvl = row["trust_level"]
        elif meta.get("trust_level"):
            trust_lvl = meta["trust_level"]

        return {
            "id": row["id"],
            "owner_id": row["owner_id"],
            "name": row["name"],
            "version": row["version"],
            "description": row["description"],
            "author": row["author"],
            "license": row["license"],
            "entrypoint": row["entrypoint"],
            "runtime": row["runtime"],
            "permissions": perms,
            "provided_tools": tools,
            "dependencies": deps,
            "install_path": row["install_path"],
            "content_hash": row["content_hash"],
            "is_enabled": bool(row["is_enabled"]),
            "trust_level": trust_lvl,
            "manifest_json": meta,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _row_to_skill_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        try: tools = json.loads(row["required_tools"])
        except Exception: tools = []
        try: perms = json.loads(row["permissions"])
        except Exception: perms = []
        try: tags = json.loads(row["tags"])
        except Exception: tags = []
        try: trigs = json.loads(row["triggers"])
        except Exception: trigs = []
        try: meta = json.loads(row["manifest_json"])
        except Exception: meta = {}

        return {
            "id": row["id"],
            "owner_id": row["owner_id"],
            "name": row["name"],
            "version": row["version"],
            "description": row["description"],
            "author": row["author"],
            "instructions": row["instructions"],
            "required_tools": tools,
            "permissions": perms,
            "tags": tags,
            "triggers": trigs,
            "is_enabled": bool(row["is_enabled"]),
            "install_path": row["install_path"],
            "content_hash": row["content_hash"],
            "manifest_json": meta,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
