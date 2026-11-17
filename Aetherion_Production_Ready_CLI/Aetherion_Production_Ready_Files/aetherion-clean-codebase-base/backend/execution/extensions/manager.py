"""
backend/execution/extensions/manager.py — Core lifecycle orchestrator for Plugins and Skills.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .fetcher import SourceFetcher
from .models import (
    ALL_PERMISSIONS,
    ExtensionStatus,
    ExtensionTrustLevel,
    HealthStatus,
    PermissionEnum,
    PluginManifest,
    SkillManifest,
)
from .security import (
    ExtensionSecurityException,
    analyze_permissions,
    calculate_content_hash,
)
from .store import ExtensionStore

logger = logging.getLogger("aetherion.extensions.manager")

DEFAULT_EXTENSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class ExtensionManager:
    """Manages secure lifecycle, isolation, validation, and execution of plugins and skills."""

    def __init__(
        self,
        store: Optional[ExtensionStore] = None,
        base_install_dir: Optional[Path | str] = None,
        fetcher: Optional[SourceFetcher] = None,
    ):
        self.store = store or ExtensionStore()
        self.base_dir = Path(base_install_dir or DEFAULT_EXTENSIONS_DIR).resolve()
        self.plugins_dir = self.base_dir / "plugins"
        self.skills_dir = self.base_dir / "skills"
        self.versions_dir = self.base_dir / "versions"
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self.fetcher = fetcher or SourceFetcher()
        self._pending_previews: Dict[str, Dict[str, Any]] = {}

    # ── Inspection & Pre-Install Preview ───────────────────────────────────────

    def inspect_plugin(self, source: str, owner_id: str) -> Dict[str, Any]:
        """Fetch, validate manifest, analyze permissions, and generate install preview."""
        source_dir, content_hash, raw_manifest = self.fetcher.fetch_source(source, extension_type="plugin")
        manifest = PluginManifest.from_dict(raw_manifest)
        perm_analysis = analyze_permissions(manifest.required_permissions)

        preview_id = f"prev_{uuid.uuid4().hex[:12]}"
        preview = {
            "preview_id": preview_id,
            "extension_type": "plugin",
            "source": source,
            "source_dir": str(source_dir),
            "content_hash": content_hash,
            "manifest": manifest.to_dict(),
            "permissions_requested": perm_analysis["valid_permissions"],
            "risk_level": perm_analysis["risk_level"],
            "requires_explicit_approval": perm_analysis["requires_explicit_approval"],
            "critical_flags": perm_analysis["critical_flags"],
            "owner_id": str(owner_id),
            "status": "READY_FOR_APPROVAL",
        }
        self._pending_previews[preview_id] = preview
        return preview

    def inspect_skill(self, source: str, owner_id: str) -> Dict[str, Any]:
        """Fetch, validate skill manifest, and generate install preview."""
        source_dir, content_hash, raw_manifest = self.fetcher.fetch_source(source, extension_type="skill")
        manifest = SkillManifest.from_dict(raw_manifest)
        perm_analysis = analyze_permissions(manifest.required_permissions)

        preview_id = f"prev_{uuid.uuid4().hex[:12]}"
        preview = {
            "preview_id": preview_id,
            "extension_type": "skill",
            "source": source,
            "source_dir": str(source_dir),
            "content_hash": content_hash,
            "manifest": manifest.to_dict(),
            "permissions_requested": perm_analysis["valid_permissions"],
            "risk_level": perm_analysis["risk_level"],
            "requires_explicit_approval": perm_analysis["requires_explicit_approval"],
            "critical_flags": perm_analysis["critical_flags"],
            "owner_id": str(owner_id),
            "status": "READY_FOR_APPROVAL",
        }
        self._pending_previews[preview_id] = preview
        return preview

    # ── Installation Pipeline ─────────────────────────────────────────────────

    def install_plugin(
        self,
        preview_id_or_source: str,
        owner_id: str,
        approved_permissions: Optional[List[str]] = None,
        auto_approve: bool = False,
        trust_level: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Install a validated plugin into isolated directory with permission granting."""
        if preview_id_or_source in self._pending_previews:
            preview = self._pending_previews[preview_id_or_source]
        else:
            preview = self.inspect_plugin(preview_id_or_source, owner_id)

        manifest_data = preview["manifest"]
        manifest = PluginManifest.from_dict(manifest_data)
        pid = manifest.id
        owner = str(owner_id)

        # Enforce approval for high/critical permissions
        if preview["requires_explicit_approval"] and not auto_approve and approved_permissions is None:
            raise ExtensionSecurityException(
                f"Plugin '{pid}' requests privileged permissions ({preview['critical_flags']}) "
                f"and requires explicit user approval before installation."
            )

        granted_perms = approved_permissions if approved_permissions is not None else manifest.required_permissions

        # ── Trust level resolution with ceiling enforcement ──────────────────
        # If the caller explicitly sets trust_level, honour it (CLI/API with authority).
        # If the caller does NOT set it, take the manifest's declared level BUT
        # cap it at TRUSTED_LOCAL — a remote manifest cannot self-escalate to SYSTEM.
        if trust_level is not None:
            effective_trust = trust_level
        else:
            manifest_trust = getattr(manifest, "trust_level", ExtensionTrustLevel.UNTRUSTED.value) or ExtensionTrustLevel.UNTRUSTED.value
            if manifest_trust == ExtensionTrustLevel.SYSTEM.value:
                logger.warning(
                    "Plugin '%s' self-declared trust_level=SYSTEM in manifest but caller did not "
                    "grant SYSTEM explicitly — downgrading to TRUSTED_LOCAL for safety.",
                    pid,
                )
                effective_trust = ExtensionTrustLevel.TRUSTED_LOCAL.value
            else:
                effective_trust = manifest_trust

        # Destination in isolated data/plugins/<owner_id>/<plugin_id>/
        plugin_install_dir = self.plugins_dir / owner / pid
        if plugin_install_dir.exists():
            shutil.rmtree(plugin_install_dir)
        plugin_install_dir.mkdir(parents=True, exist_ok=True)

        # Copy source contents safely into isolated installation dir
        source_dir = Path(preview["source_dir"])
        for item in source_dir.iterdir():
            dest = plugin_install_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

        # Persist manifest file in install dir
        manifest_dict = manifest.to_dict()
        manifest_dict["trust_level"] = effective_trust
        (plugin_install_dir / "plugin.json").write_text(json.dumps(manifest_dict, indent=2), encoding="utf-8")
        final_hash = calculate_content_hash(plugin_install_dir)

        # Archive version snapshot for faithful rollback restoration
        version_snapshot_dir = self.versions_dir / "plugins" / owner / pid / manifest.version
        if version_snapshot_dir.exists():
            shutil.rmtree(version_snapshot_dir)
        version_snapshot_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(plugin_install_dir, version_snapshot_dir, dirs_exist_ok=True)

        record = {
            "id": pid,
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "author": manifest.author,
            "license": manifest.license,
            "entrypoint": manifest.entrypoint,
            "runtime": manifest.runtime,
            "permissions": granted_perms,
            "provided_tools": manifest.provided_tools,
            "dependencies": manifest.required_dependencies,
            "install_path": str(plugin_install_dir),
            "content_hash": final_hash,
            "is_enabled": True,
            "trust_level": effective_trust,
            "manifest_json": manifest_dict,
        }
        saved = self.store.save_plugin(record, owner)
        self._pending_previews.pop(preview_id_or_source, None)
        return saved

    def install_skill(
        self,
        preview_id_or_source: str,
        owner_id: str,
        approved_permissions: Optional[List[str]] = None,
        auto_approve: bool = False,
    ) -> Dict[str, Any]:
        """Install a validated skill into isolated storage."""
        if preview_id_or_source in self._pending_previews:
            preview = self._pending_previews[preview_id_or_source]
        else:
            preview = self.inspect_skill(preview_id_or_source, owner_id)

        manifest_data = preview["manifest"]
        manifest = SkillManifest.from_dict(manifest_data)
        sid = manifest.id
        owner = str(owner_id)

        granted_perms = approved_permissions if approved_permissions is not None else manifest.required_permissions

        # Destination in isolated data/skills/<owner_id>/<skill_id>/
        skill_install_dir = self.skills_dir / owner / sid
        if skill_install_dir.exists():
            shutil.rmtree(skill_install_dir)
        skill_install_dir.mkdir(parents=True, exist_ok=True)

        (skill_install_dir / "skill.json").write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
        (skill_install_dir / "SKILL.md").write_text(manifest.instructions, encoding="utf-8")
        final_hash = calculate_content_hash(skill_install_dir)

        record = {
            "id": sid,
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "author": manifest.author,
            "instructions": manifest.instructions,
            "required_tools": manifest.required_tools,
            "permissions": granted_perms,
            "tags": manifest.tags,
            "triggers": manifest.activation_triggers,
            "is_enabled": True,
            "install_path": str(skill_install_dir),
            "content_hash": final_hash,
            "manifest_json": manifest.to_dict(),
        }
        saved = self.store.save_skill(record, owner)
        self._pending_previews.pop(preview_id_or_source, None)
        return saved

    # ── Enable / Disable Semantics ─────────────────────────────────────────────

    def enable_plugin(self, plugin_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        return self.store.set_plugin_enabled(plugin_id, True, owner_id)

    def disable_plugin(self, plugin_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        return self.store.set_plugin_enabled(plugin_id, False, owner_id)

    def enable_skill(self, skill_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        return self.store.set_skill_enabled(skill_id, True, owner_id)

    def disable_skill(self, skill_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        return self.store.set_skill_enabled(skill_id, False, owner_id)

    # ── Removal Safety ────────────────────────────────────────────────────────

    def remove_plugin(self, plugin_id: str, owner_id: str) -> bool:
        """Safely uninstall a plugin and remove its isolated directory."""
        existing = self.store.get_plugin(plugin_id, owner_id)
        if not existing:
            return False

        install_path = Path(existing["install_path"])
        if install_path.exists():
            try:
                shutil.rmtree(install_path)
            except Exception as exc:
                logger.warning("Failed to remove plugin directory %s: %s", install_path, exc)

        return self.store.delete_plugin(plugin_id, owner_id)

    def remove_skill(self, skill_id: str, owner_id: str) -> bool:
        """Safely uninstall a skill and clean up files."""
        existing = self.store.get_skill(skill_id, owner_id)
        if not existing:
            return False

        if existing.get("install_path"):
            p = Path(existing["install_path"])
            if p.exists():
                try:
                    shutil.rmtree(p)
                except Exception as exc:
                    logger.warning("Failed to remove skill directory %s: %s", p, exc)

        return self.store.delete_skill(skill_id, owner_id)

    # ── Update & Rollback ─────────────────────────────────────────────────────

    def update_plugin(self, plugin_id: str, new_source: str, owner_id: str, auto_approve: bool = False) -> Dict[str, Any]:
        """Update an existing plugin with rollback preservation on health check failure."""
        existing = self.store.get_plugin(plugin_id, owner_id)
        if not existing:
            raise ValueError(f"Plugin '{plugin_id}' not found.")

        old_version = existing["version"]
        preview = self.inspect_plugin(new_source, owner_id)
        new_manifest = PluginManifest.from_dict(preview["manifest"])

        if new_manifest.id != plugin_id:
            raise ValueError(f"Updated plugin ID '{new_manifest.id}' does not match existing ID '{plugin_id}'.")

        # Check for expanded permissions
        old_perms = set(existing.get("permissions", []))
        new_perms = set(new_manifest.required_permissions)
        expanded = new_perms - old_perms

        if expanded and not auto_approve:
            raise ExtensionSecurityException(
                f"Update requires expanded permissions: {sorted(expanded)}. Explicit approval required."
            )

        updated = self.install_plugin(preview["preview_id"], owner_id, auto_approve=True)

        # Health check validation
        health = self.health_check_plugin(plugin_id, owner_id)
        if health["status"] not in (HealthStatus.VALID.value, HealthStatus.DISABLED.value):
            logger.error("Updated plugin failed health check: %s. Rolling back to %s.", health, old_version)
            self.rollback_plugin(plugin_id, old_version, owner_id)
            raise ValueError(f"Update failed health check ({health.get('diagnostics')}). Rolled back to version {old_version}.")

        return updated

    def rollback_plugin(self, plugin_id: str, target_version: str, owner_id: str) -> Optional[Dict[str, Any]]:
        """Rollback a plugin to a prior version from the version snapshot log."""
        versions = self.store.get_plugin_versions(plugin_id, owner_id)
        target_snapshot = next((v for v in versions if v["version"] == target_version), None)
        if not target_snapshot:
            return None

        # Re-apply version in store
        manifest_data = json.loads(target_snapshot.get("manifest_json", "{}"))
        if manifest_data:
            manifest = PluginManifest.from_dict(manifest_data)
            inst_p = Path(target_snapshot["install_path"])

            # Restore physical files from version snapshot if available
            snapshot_dir = self.versions_dir / "plugins" / owner_id / plugin_id / target_version
            if snapshot_dir.exists():
                if inst_p.exists():
                    shutil.rmtree(inst_p)
                inst_p.mkdir(parents=True, exist_ok=True)
                shutil.copytree(snapshot_dir, inst_p, dirs_exist_ok=True)

            record = {
                "id": plugin_id,
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "author": manifest.author,
                "license": manifest.license,
                "entrypoint": manifest.entrypoint,
                "runtime": manifest.runtime,
                "permissions": manifest.required_permissions,
                "provided_tools": manifest.provided_tools,
                "dependencies": manifest.required_dependencies,
                "install_path": str(inst_p),
                "content_hash": target_snapshot["content_hash"],
                "is_enabled": True,
                "trust_level": getattr(manifest, "trust_level", ExtensionTrustLevel.UNTRUSTED.value),
                "manifest_json": manifest.to_dict(),
            }
            self.store.save_plugin(record, owner_id)
            self.store.log_audit(owner_id, "plugin", plugin_id, "ROLLBACK", f"Rolled back to {target_version}")
            return self.store.get_plugin(plugin_id, owner_id)
        return None

    def rollback_skill(self, skill_id: str, target_version: str, owner_id: str) -> Optional[Dict[str, Any]]:
        """Rollback a skill to a prior version from snapshot."""
        versions = self.store.get_skill_versions(skill_id, owner_id)
        target_snapshot = next((v for v in versions if v["version"] == target_version), None)
        if not target_snapshot:
            return None

        manifest_data = json.loads(target_snapshot.get("manifest_json", "{}"))
        if manifest_data:
            manifest = SkillManifest.from_dict(manifest_data)
            skill_dir = self.skills_dir / str(owner_id) / str(skill_id)
            if skill_dir.exists():
                (skill_dir / "skill.json").write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
                (skill_dir / "SKILL.md").write_text(manifest.instructions, encoding="utf-8")

            record = {
                "id": skill_id,
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "author": manifest.author,
                "instructions": manifest.instructions,
                "required_tools": manifest.required_tools,
                "permissions": manifest.required_permissions,
                "tags": manifest.tags,
                "triggers": manifest.activation_triggers,
                "is_enabled": True,
                "install_path": str(skill_dir) if skill_dir.exists() else "",
                "content_hash": target_snapshot["content_hash"],
                "manifest_json": manifest.to_dict(),
            }
            self.store.save_skill(record, owner_id)
            self.store.log_audit(owner_id, "skill", skill_id, "ROLLBACK", f"Rolled back to {target_version}")
            return self.store.get_skill(skill_id, owner_id)
        return None

    # ── Health Diagnostics ────────────────────────────────────────────────────

    def health_check_plugin(self, plugin_id: str, owner_id: str) -> Dict[str, Any]:
        """Perform diagnostic health check on installed plugin."""
        plugin = self.store.get_plugin(plugin_id, owner_id)
        if not plugin:
            return {"status": HealthStatus.INVALID.value, "diagnostics": f"Plugin '{plugin_id}' not found."}

        if not plugin.get("is_enabled"):
            return {"status": HealthStatus.DISABLED.value, "diagnostics": "Plugin is currently disabled."}

        install_path = Path(plugin["install_path"])
        if not install_path.exists():
            return {"status": HealthStatus.INVALID.value, "diagnostics": "Installation directory is missing."}

        entrypoint = plugin.get("entrypoint", "")
        entry_file = install_path / entrypoint.split(":")[0]
        if not entry_file.exists():
            return {
                "status": HealthStatus.INVALID.value,
                "diagnostics": f"Entrypoint file '{entry_file.name}' not found in installation path.",
            }

        # Check dependencies importability if declared
        deps = plugin.get("dependencies", [])
        missing = []
        for dep in deps:
            pkg_name = dep.split("==")[0].split(">=")[0].split("<=")[0].strip()
            if pkg_name and importlib.util.find_spec(pkg_name) is None:
                missing.append(pkg_name)

        if missing:
            return {
                "status": HealthStatus.MISSING_DEPENDENCY.value,
                "diagnostics": f"Missing python dependencies: {', '.join(missing)}",
                "missing": missing,
            }

        return {
            "status": HealthStatus.VALID.value,
            "diagnostics": f"Plugin '{plugin['name']}' v{plugin['version']} verified operational.",
            "tools_count": len(plugin.get("provided_tools", [])),
            "trust_level": plugin.get("trust_level", ExtensionTrustLevel.UNTRUSTED.value),
        }

    def health_check_skill(self, skill_id: str, owner_id: str) -> Dict[str, Any]:
        """Perform health check on installed skill."""
        skill = self.store.get_skill(skill_id, owner_id)
        if not skill:
            return {"status": HealthStatus.INVALID.value, "diagnostics": f"Skill '{skill_id}' not found."}

        if not skill.get("is_enabled"):
            return {"status": HealthStatus.DISABLED.value, "diagnostics": "Skill is disabled."}

        if not skill.get("instructions") or len(skill["instructions"].strip()) < 10:
            return {"status": HealthStatus.INVALID.value, "diagnostics": "Skill instructions are missing or empty."}

        return {
            "status": HealthStatus.VALID.value,
            "diagnostics": f"Skill '{skill['name']}' operational with {len(skill.get('instructions', ''))} bytes of guidance.",
        }

    # ── Retrieval & Search ────────────────────────────────────────────────────

    def get_plugin(self, plugin_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_plugin(plugin_id, owner_id)

    def list_plugins(self, owner_id: str, enabled_only: bool = False) -> List[Dict[str, Any]]:
        return self.store.list_plugins(owner_id, enabled_only=enabled_only)

    def get_skill(self, skill_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_skill(skill_id, owner_id)

    def list_skills(self, owner_id: str, enabled_only: bool = False) -> List[Dict[str, Any]]:
        return self.store.list_skills(owner_id, enabled_only=enabled_only)
