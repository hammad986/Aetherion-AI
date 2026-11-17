"""
backend/execution/extensions/fetcher.py — Normalized source fetcher for plugins and skills.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

from .models import PluginManifest, SkillManifest
from .security import (
    ExtensionSecurityException,
    calculate_bytes_hash,
    calculate_content_hash,
    scan_and_extract_archive,
)

logger = logging.getLogger("aetherion.extensions.fetcher")


class SourceFetcher:
    """Fetches and normalizes extension source into a validated inspection directory."""

    def __init__(self, temp_dir: Optional[Path | str] = None, timeout: float = 30.0):
        self.temp_root = Path(temp_dir or tempfile.gettempdir()) / "aetherion_fetch"
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    def fetch_source(
        self,
        source: str,
        extension_type: str = "plugin",
    ) -> Tuple[Path, str, Dict[str, Any]]:
        """Fetch source from path/URL, extract safely if needed, and discover manifest.

        Returns:
            Tuple of (source_directory, content_hash, raw_manifest_dict)
        """
        source_clean = source.strip()
        work_dir = Path(tempfile.mkdtemp(dir=str(self.temp_root)))

        # 1. Local Directory
        local_p = Path(source_clean).resolve()
        if local_p.is_dir():
            file_count = 0
            for root, dirs, files in os.walk(local_p):
                for d in dirs:
                    dp = Path(root) / d
                    if dp.is_symlink():
                        raise ExtensionSecurityException(f"Symlinks are prohibited for security: '{dp.name}'")
                for f in files:
                    fp = Path(root) / f
                    if fp.is_symlink():
                        raise ExtensionSecurityException(f"Symlinks are prohibited for security: '{fp.name}'")
                    file_count += 1
                    if file_count > 500:
                        raise ExtensionSecurityException(f"Source directory file count ({file_count}) exceeds maximum allowed limit of 500 files.")

            content_hash = calculate_content_hash(local_p)
            manifest = self._discover_manifest(local_p, extension_type)
            return local_p, content_hash, manifest

        # 2. Local ZIP file
        if local_p.is_file() and (source_clean.endswith(".zip") or zipfile_sniff(local_p)):
            content_hash = calculate_content_hash(local_p)
            extract_dir = work_dir / "extracted"
            scan_and_extract_archive(local_p, extract_dir)
            manifest = self._discover_manifest(extract_dir, extension_type)
            return extract_dir, content_hash, manifest

        # 3. Local Markdown file (for skills)
        if local_p.is_file() and (source_clean.endswith(".md") or extension_type == "skill"):
            content_hash = calculate_content_hash(local_p)
            skill_manifest = self._parse_markdown_skill(local_p)
            return local_p.parent, content_hash, skill_manifest

        # 4. Remote HTTP/HTTPS URL or GitHub repo
        if source_clean.startswith("http://") or source_clean.startswith("https://"):
            return self._fetch_remote(source_clean, work_dir, extension_type)

        raise ValueError(f"Unrecognized extension source: '{source}'. Must be a directory, ZIP, markdown file, or HTTPS URL.")

    MAX_REMOTE_BYTES = 50 * 1024 * 1024  # 50 MB

    @classmethod
    def _validate_remote_url(cls, url: str) -> str:
        """Validate URL against SSRF, private IP, and localhost targets."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Unsupported URL scheme: '{parsed.scheme}'. Only HTTP and HTTPS are permitted.")

        host = parsed.hostname or ""
        if not host:
            raise ExtensionSecurityException("Invalid remote URL: missing hostname.")

        # Block localhost and local names
        if host.lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "local"):
            raise ExtensionSecurityException(f"SSRF violation: fetching from local target '{host}' is prohibited.")

        # Resolve host to IP and check private/loopback/link-local ranges
        try:
            addr_info = socket.getaddrinfo(host, None)
            for item in addr_info:
                ip_str = item[4][0]
                ip_obj = ipaddress.ip_address(ip_str)
                if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local or ip_obj.is_reserved:
                    raise ExtensionSecurityException(
                        f"SSRF violation: target hostname '{host}' resolves to restricted address {ip_str}."
                    )
        except socket.gaierror as ge:
            raise ExtensionSecurityException(f"Failed to resolve host '{host}': {ge}")

        return url

    def _fetch_remote(
        self,
        url: str,
        work_dir: Path,
        extension_type: str,
    ) -> Tuple[Path, str, Dict[str, Any]]:
        """Fetch remote resource via HTTP/HTTPS safely with SSRF protection and bounded streaming."""
        self._validate_remote_url(url)
        parsed = urllib.parse.urlparse(url)

        # Normalize GitHub repo URLs to zipball if raw repo url provided
        download_url = url
        if "github.com" in parsed.netloc and not url.endswith(".zip") and not url.endswith(".tar.gz"):
            path_parts = [p for p in parsed.path.strip("/").split("/") if p]
            if len(path_parts) == 2:
                download_url = f"https://github.com/{path_parts[0]}/{path_parts[1]}/archive/refs/heads/main.zip"

        headers = {"User-Agent": "Aetherion-Plugin-Fetcher/2.0"}
        raw_chunks = []
        total_downloaded = 0

        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                with client.stream("GET", download_url, headers=headers) as resp:
                    if resp.status_code != 200:
                        raise ExtensionSecurityException(f"Remote fetch failed with HTTP {resp.status_code} from {download_url}")
                    # Validate redirect target against SSRF
                    if str(resp.url) != download_url:
                        self._validate_remote_url(str(resp.url))

                    for chunk in resp.iter_bytes(chunk_size=65536):
                        total_downloaded += len(chunk)
                        if total_downloaded > self.MAX_REMOTE_BYTES:
                            raise ExtensionSecurityException(
                                f"Remote download exceeded maximum size limit of {self.MAX_REMOTE_BYTES} bytes."
                            )
                        raw_chunks.append(chunk)

            raw_bytes = b"".join(raw_chunks)
        except Exception as exc:
            if isinstance(exc, ExtensionSecurityException):
                raise
            raise ExtensionSecurityException(f"Failed to fetch remote source from {download_url}: {exc}")

        content_hash = calculate_bytes_hash(raw_bytes)

        # If downloaded content is a ZIP archive
        if raw_bytes.startswith(b"PK\x03\x04"):
            zip_dest = work_dir / "downloaded.zip"
            zip_dest.write_bytes(raw_bytes)
            extract_dir = work_dir / "extracted"
            scan_and_extract_archive(zip_dest, extract_dir)
            manifest = self._discover_manifest(extract_dir, extension_type)
            return extract_dir, content_hash, manifest

        # If downloaded content is Markdown (for skills)
        if extension_type == "skill" or download_url.endswith(".md"):
            text_content = raw_bytes.decode("utf-8", errors="replace")
            md_file = work_dir / "SKILL.md"
            md_file.write_text(text_content, encoding="utf-8")
            manifest = self._parse_markdown_skill(md_file)
            return work_dir, content_hash, manifest

        # If downloaded content is JSON manifest
        try:
            manifest_dict = json.loads(raw_bytes.decode("utf-8"))
            return work_dir, content_hash, manifest_dict
        except Exception:
            raise ExtensionSecurityException(f"Remote download from {url} is neither a ZIP archive, Markdown, nor JSON manifest.")

    def _discover_manifest(self, base_dir: Path, extension_type: str) -> Dict[str, Any]:
        """Locate and load plugin.json / skill.json / manifest.json / SKILL.md in directory."""
        # Check subdirectories if zip had a single root container directory (e.g. repo-main/)
        candidate_dirs = [base_dir]
        subdirs = [p for p in base_dir.iterdir() if p.is_dir()]
        if len(subdirs) == 1:
            candidate_dirs.append(subdirs[0])

        for cdir in candidate_dirs:
            if extension_type == "plugin":
                for name in ("plugin.json", "manifest.json"):
                    fp = cdir / name
                    if fp.is_file():
                        try:
                            return json.loads(fp.read_text(encoding="utf-8"))
                        except Exception as exc:
                            raise ValueError(f"Malformed plugin manifest in '{fp}': {exc}")
            else:
                for name in ("skill.json", "manifest.json"):
                    fp = cdir / name
                    if fp.is_file():
                        try:
                            return json.loads(fp.read_text(encoding="utf-8"))
                        except Exception as exc:
                            raise ValueError(f"Malformed skill manifest in '{fp}': {exc}")
                # Check for SKILL.md
                skill_md = cdir / "SKILL.md"
                if skill_md.is_file():
                    return self._parse_markdown_skill(skill_md)

        raise ValueError(
            f"No valid {extension_type} manifest found in source. "
            f"Expected {'plugin.json or manifest.json' if extension_type == 'plugin' else 'skill.json, manifest.json, or SKILL.md'}."
        )

    def _parse_markdown_skill(self, md_path: Path) -> Dict[str, Any]:
        """Parse frontmatter or title/description from a Markdown skill document."""
        content = md_path.read_text(encoding="utf-8", errors="replace")
        default_name = md_path.stem.replace("_", "-").replace(" ", "-").lower()
        if default_name == "skill":
            default_name = md_path.parent.name.replace("_", "-").replace(" ", "-").lower() or "custom-skill"

        parsed_fm: Dict[str, Any] = {}
        instructions = content.strip()

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                fm_raw = parts[1]
                instructions = parts[2].strip()
                try:
                    import yaml
                    loaded = yaml.safe_load(fm_raw)
                    if isinstance(loaded, dict):
                        parsed_fm = loaded
                except Exception as exc:
                    logger.debug("PyYAML parse failed, falling back to manual parse: %s", exc)

        skill_id = parsed_fm.get("id") or parsed_fm.get("name") or default_name
        skill_name = parsed_fm.get("name") or default_name
        version = parsed_fm.get("version") or "1.0.0"
        description = parsed_fm.get("description") or f"Instruction skill imported from {md_path.name}"
        author = parsed_fm.get("author") or "community"

        required_tools = parsed_fm.get("required_tools") or parsed_fm.get("tools") or []
        if isinstance(required_tools, str):
            required_tools = [t.strip() for t in required_tools.split(",") if t.strip()]

        triggers = parsed_fm.get("activation_triggers") or parsed_fm.get("triggers") or []
        if isinstance(triggers, str):
            triggers = [t.strip() for t in triggers.split(",") if t.strip()]

        tags = parsed_fm.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        permissions = parsed_fm.get("required_permissions") or parsed_fm.get("permissions") or []
        if isinstance(permissions, str):
            permissions = [p.strip() for p in permissions.split(",") if p.strip()]

        # Ensure slug ID matches format
        slug_id = re.sub(r"[^a-zA-Z0-9_-]", "-", str(skill_id)).strip("-")[:40] or "custom-skill"
        if len(slug_id) < 3:
            slug_id = f"skill-{slug_id}"

        return {
            "id": slug_id,
            "name": str(skill_name),
            "version": str(version),
            "description": str(description),
            "author": str(author),
            "instructions": instructions,
            "required_tools": list(required_tools),
            "required_permissions": list(permissions),
            "activation_triggers": list(triggers),
            "tags": list(tags),
            "compatible_aetherion_version": ">=2.0.0",
        }


def zipfile_sniff(path: Path) -> bool:
    """Sniff if a file starts with the ZIP magic bytes."""
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"PK\x03\x04"
    except Exception:
        return False
