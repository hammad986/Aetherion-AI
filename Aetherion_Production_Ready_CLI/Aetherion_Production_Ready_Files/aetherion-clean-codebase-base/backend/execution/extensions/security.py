"""
backend/execution/extensions/security.py — Archive safety, content integrity, and permission analysis.
"""
from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from .models import ALL_PERMISSIONS, PermissionEnum


class ExtensionSecurityException(Exception):
    """Raised when an extension archive or operation violates security policy."""


MAX_ARCHIVE_BYTES = 50 * 1024 * 1024  # 50 MB max uncompressed size
MAX_ARCHIVE_FILES = 500                # 500 files max


def calculate_content_hash(target_path: Path | str) -> str:
    """Compute deterministic SHA-256 digest of a file or directory tree."""
    p = Path(target_path)
    hasher = hashlib.sha256()

    if p.is_file():
        hasher.update(p.read_bytes())
        return hasher.hexdigest()

    if p.is_dir():
        for root, _, files in sorted(os.walk(p)):
            for fn in sorted(files):
                fp = Path(root) / fn
                rel = fp.relative_to(p).as_posix()
                hasher.update(rel.encode("utf-8"))
                try:
                    hasher.update(fp.read_bytes())
                except Exception:
                    pass
        return hasher.hexdigest()

    return ""


def calculate_bytes_hash(data: bytes) -> str:
    """Compute SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def scan_and_extract_archive(
    archive_path: Path | str,
    target_extract_dir: Path | str,
    max_bytes: int = MAX_ARCHIVE_BYTES,
    max_files: int = MAX_ARCHIVE_FILES,
) -> Path:
    """Safely validate and extract a ZIP archive with strict Zip Slip, symlink, and resource limits.

    Raises:
        ExtensionSecurityException if any path traversal, symlink escape, or size limit is breached.
    """
    archive_path = Path(archive_path)
    extract_root = Path(target_extract_dir).resolve()
    extract_root.mkdir(parents=True, exist_ok=True)

    if not zipfile.is_zipfile(archive_path):
        raise ExtensionSecurityException(f"Invalid ZIP archive: {archive_path}")

    total_uncompressed_bytes = 0
    total_files = 0

    with zipfile.ZipFile(archive_path, "r") as zf:
        infolist = zf.infolist()
        if len(infolist) > max_files:
            raise ExtensionSecurityException(
                f"Archive file count ({len(infolist)}) exceeds security limit of {max_files} files."
            )

        # First pass: pre-validation of all member paths and cumulative size
        for info in infolist:
            total_files += 1
            total_uncompressed_bytes += info.file_size
            if total_uncompressed_bytes > max_bytes:
                raise ExtensionSecurityException(
                    f"Archive uncompressed size ({total_uncompressed_bytes} bytes) exceeds limit of {max_bytes} bytes."
                )

            # Zip Slip Defense: reject absolute paths, drive letters/streams, and parent traversals
            name = info.filename
            if os.path.isabs(name) or name.startswith("/") or name.startswith("\\"):
                raise ExtensionSecurityException(f"Zip Slip attack detected: absolute path '{name}'")

            if ":" in name:
                raise ExtensionSecurityException(f"Zip Slip attack detected: illegal colon/stream '{name}'")

            parts = Path(name).parts
            if ".." in parts:
                raise ExtensionSecurityException(f"Zip Slip attack detected: path traversal '{name}'")

            dest_path = (extract_root / name).resolve()
            try:
                dest_path.relative_to(extract_root)
            except ValueError:
                raise ExtensionSecurityException(f"Zip Slip attack detected: path escapes root '{name}'")

            # Symlink escape defense: check unix mode bits (0o120000 = S_IFLNK)
            unix_mode = info.external_attr >> 16
            if (unix_mode & 0o120000) == 0o120000:
                raise ExtensionSecurityException(f"Symlink rejected for security: '{name}'")

        # Second pass: safe extraction
        for info in infolist:
            dest_path = (extract_root / info.filename).resolve()
            if info.is_dir():
                dest_path.mkdir(parents=True, exist_ok=True)
            else:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(dest_path, "wb") as dst:
                    dst.write(src.read())

    return extract_root


def analyze_permissions(requested: List[str]) -> Dict[str, Any]:
    """Analyze requested permissions and determine risk classification."""
    valid_req = [p for p in requested if p in ALL_PERMISSIONS]
    unknown = [p for p in requested if p not in ALL_PERMISSIONS]

    critical_perms = {
        PermissionEnum.SECRET_ACCESS.value,
        PermissionEnum.SHELL_EXECUTION.value,
        PermissionEnum.PROCESS_EXECUTION.value,
    }
    high_perms = {
        PermissionEnum.FILESYSTEM_WRITE.value,
        PermissionEnum.NETWORK_ACCESS.value,
        PermissionEnum.GIT_ACCESS.value,
    }
    medium_perms = {
        PermissionEnum.FILESYSTEM_READ.value,
        PermissionEnum.WORKSPACE_ACCESS.value,
        PermissionEnum.MODEL_ACCESS.value,
    }

    has_critical = any(p in critical_perms for p in valid_req)
    has_high = any(p in high_perms for p in valid_req)
    has_medium = any(p in medium_perms for p in valid_req)

    if has_critical or unknown:
        risk_level = "CRITICAL"
    elif has_high:
        risk_level = "HIGH"
    elif has_medium:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "risk_level": risk_level,
        "valid_permissions": sorted(valid_req),
        "unknown_permissions": sorted(unknown),
        "requires_explicit_approval": risk_level in ("HIGH", "CRITICAL"),
        "critical_flags": [p for p in valid_req if p in critical_perms],
    }
