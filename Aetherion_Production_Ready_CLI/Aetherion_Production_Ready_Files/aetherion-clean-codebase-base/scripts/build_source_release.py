#!/usr/bin/env python3
"""
scripts/build_source_release.py — Deterministic Source Release Packager for Aetherion
====================================================================================
Packages the authoritative working tree into a hermetic, reproducible, secret-free
release ZIP and cryptographic manifest.

Features:
  - Strict inclusion/exclusion policy
  - Normalized timestamps (pinned to 2026-01-01 00:00:00 UTC)
  - Normalized POSIX file permissions
  - Deterministic entry sorting (alphabetical POSIX paths)
  - Full cryptographic manifest with per-file SHA-256 and uncompressed byte sizes
  - Non-circular manifest generation (manifest describes ZIP; baseline report records manifest)
"""

import os
import sys
import json
import zipfile
import hashlib
import datetime
import argparse
from pathlib import Path

# Pinned timestamp for bitwise deterministic archive creation
DETERMINISTIC_DATETIME = (2026, 1, 1, 0, 0, 0)

# Release identification metadata
RELEASE_LABEL = "aetherion-source-release-2026-10-10"
RELEASE_VERSION = "1.0.0"
ZIP_FILENAME = "aetherion-source-release-2026-10-10.zip"
MANIFEST_FILENAME = "aetherion-source-release-2026-10-10.manifest.json"

# Explicit root files permitted in release
PERMITTED_ROOT_FILES = {
    "app.py",
    ".env.example",
    ".dockerignore",
    ".gitignore",
    "pytest.ini",
    "README.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "AETHERION_DOCKER_UAT_RELEASE_REPORT.md",
    "AETHERION_FRONTEND_BROWSER_UAT_REPORT.md",
    "AETHERION_POST_UAT_INDEPENDENT_AUDIT.md",
}

# Subsystems included in release
INCLUDED_SUBSYSTEMS = {
    "backend",
    "frontend-auth",
    "cli",
    "deploy",
    "scripts",
    "tests",
    "qa",
    "docs",
}

# Directories explicitly excluded from all packaging
EXCLUDED_DIR_NAMES = {
    ".git",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    ".aetherion_worktrees",
    "workspace",
    "temp_live_ws",
    "scratch",
    "audit",
    "reference only",
}

# File extensions explicitly excluded from all packaging
EXCLUDED_EXTENSIONS = {
    ".db",
    ".sqlite",
    ".pyc",
    ".pyo",
    ".pyd",
    ".log",
}

# Specific filenames explicitly excluded from all packaging
EXCLUDED_FILENAMES = {
    ".env",
    "audit_results.txt",
    "audit_results_safe.txt",
    "pytest_results.txt",
    "phase_prompt.txt",
    "phase_prompt_full.txt",
    "phase14_prompt_full.txt",
    "run_audits.ps1",
    "run_audits.py",
    "safe_runner.py",
}


def should_include_file(rel_path_posix: str) -> bool:
    """Evaluate whether a relative file path should be included in the release."""
    parts = rel_path_posix.split("/")
    filename = parts[-1]
    ext = os.path.splitext(filename)[1].lower()

    # 1. Directory exclusion check
    for part in parts[:-1]:
        if part in EXCLUDED_DIR_NAMES:
            return False

    # 2. Extension exclusion check
    if ext in EXCLUDED_EXTENSIONS:
        return False

    # 3. Filename exclusion check
    if filename in EXCLUDED_FILENAMES:
        return False

    # 4. Exclude historical phase reports and old release baseline/audit reports
    # (Stale report claims must not pollute the clean release source)
    if len(parts) == 1 and filename.endswith(".md"):
        if filename not in PERMITTED_ROOT_FILES:
            return False

    # 5. Root file inclusion check
    if len(parts) == 1:
        return filename in PERMITTED_ROOT_FILES

    # 6. Subsystem inclusion check
    subsystem = parts[0]
    if subsystem not in INCLUDED_SUBSYSTEMS:
        return False

    # 7. Exclude sensitive or temporary files inside subsystems
    if filename.startswith(".env") and not filename.endswith(".example"):
        return False

    return True


def collect_release_files(root_dir: Path) -> list[tuple[str, Path]]:
    """Scan the repository root and collect all files matching the inclusion policy."""
    collected = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Prune excluded directories in-place
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIR_NAMES]

        for fname in filenames:
            abs_path = Path(dirpath) / fname
            rel_path_posix = abs_path.relative_to(root_dir).as_posix()
            if should_include_file(rel_path_posix):
                collected.append((rel_path_posix, abs_path))

    # Sort files strictly alphabetically by relative POSIX path for determinism
    collected.sort(key=lambda item: item[0])
    return collected


def build_release_package(
    root_dir: Path,
    output_dir: Path,
    label: str = RELEASE_LABEL,
    version: str = RELEASE_VERSION,
    zip_filename: str = ZIP_FILENAME,
    manifest_filename: str = MANIFEST_FILENAME,
) -> dict:
    """Build the deterministic release ZIP and manifest in output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / zip_filename
    manifest_path = output_dir / manifest_filename

    files = collect_release_files(root_dir)
    print(f"Collected {len(files)} files for release packaging.")

    manifest_entries = []
    total_uncompressed_bytes = 0

    # Create deterministic ZIP archive
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel_posix, abs_path in files:
            data = abs_path.read_bytes()
            size = len(data)
            sha256 = hashlib.sha256(data).hexdigest()
            total_uncompressed_bytes += size

            # Normalize ZipInfo metadata for bitwise determinism
            zinfo = zipfile.ZipInfo(filename=rel_posix, date_time=DETERMINISTIC_DATETIME)
            zinfo.compress_type = zipfile.ZIP_DEFLATED
            zinfo.create_system = 3  # UNIX system attributes

            # Set normalized permissions: 0o755 for scripts/binaries, 0o644 for others
            if rel_posix.endswith(".sh") or rel_posix.startswith("scripts/") or rel_posix.startswith("cli/"):
                zinfo.external_attr = 0o755 << 16
            else:
                zinfo.external_attr = 0o644 << 16

            zf.writestr(zinfo, data)

            manifest_entries.append({
                "path": rel_posix,
                "size_bytes": size,
                "sha256": sha256
            })

    # Compute ZIP file checksum and size
    zip_bytes = zip_path.read_bytes()
    archive_sha256 = hashlib.sha256(zip_bytes).hexdigest()
    archive_size_bytes = len(zip_bytes)

    # Build manifest
    manifest_data = {
        "release_label": label,
        "created_at_utc": os.environ.get("SOURCE_DATE_EPOCH_ISO", "2026-10-10T18:00:00Z"),
        "version": version,
        "archive_filename": zip_filename,
        "archive_sha256": archive_sha256,
        "archive_size_bytes": archive_size_bytes,
        "total_uncompressed_bytes": total_uncompressed_bytes,
        "total_file_count": len(manifest_entries),
        "packaging_summary": {
            "deterministic_timestamp": "2026-01-01T00:00:00Z",
            "included_components": [
                "backend (Flask/Gunicorn API, database models, Alembic migrations, services, execution fabric)",
                "frontend-auth (React 19/Vite authentication identity portal, package-lock.json, configs)",
                "cli (Aetherion CLI/TUI engine, skill libraries, manifests, unit tests)",
                "deploy (Dockerfiles for backend, frontend, worker; Docker Compose stacks; K8s manifests; Nginx proxy configs)",
                "scripts (Deterministic packager, DB migrations, verification suites, preflights)",
                "tests (Integration, contract, authentication, and packaging suites)",
                "qa (QA automation and operator test scripts)",
                "docs (Production readiness architecture documentation)",
                "root_configuration (app.py, .env.example, .dockerignore, .gitignore, pytest.ini, README.md, SECURITY.md, CHANGELOG.md, authoritative UAT reports)"
            ],
            "excluded_categories": [
                ".git metadata and repository objects",
                ".env files containing local staging credentials",
                "Local SQLite database files (*.db, *.sqlite)",
                "Node modules (node_modules/) and frontend build distribution (dist/)",
                "Python bytecode caches (__pycache__/, *.pyc)",
                "Pytest caches (.pytest_cache/) and coverage data",
                "Runner task workspaces (workspace/, .aetherion_worktrees/, temp_live_ws/)",
                "Scratch experimental scripts and raw audit screenshot folders (scratch/, audit/)",
                "Transient prompt and test output text dumps (*.txt)",
                "Previous artifact-specific audit reports (to prevent stale claims in baseline source)",
                "Hermes read-only reference directory"
            ]
        },
        "files": manifest_entries
    }

    # Write manifest deterministically
    manifest_bytes = json.dumps(manifest_data, indent=2, ensure_ascii=False).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

    result = {
        "zip_path": str(zip_path),
        "manifest_path": str(manifest_path),
        "archive_sha256": archive_sha256,
        "archive_size_bytes": archive_size_bytes,
        "manifest_sha256": manifest_sha256,
        "manifest_size_bytes": len(manifest_bytes),
        "total_file_count": len(manifest_entries),
        "total_uncompressed_bytes": total_uncompressed_bytes,
    }

    print("Release package successfully generated:")
    print(f"  ZIP: {zip_path} ({archive_size_bytes} bytes, SHA-256: {archive_sha256})")
    print(f"  Manifest: {manifest_path} ({len(manifest_bytes)} bytes, SHA-256: {manifest_sha256})")
    print(f"  Files: {len(manifest_entries)} ({total_uncompressed_bytes} uncompressed bytes)")

    return result


def main():
    parser = argparse.ArgumentParser(description="Deterministic Source Release Packager for Aetherion")
    parser.add_argument("--root-dir", type=Path, default=Path(__file__).resolve().parent.parent,
                        help="Repository root directory")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Output directory (default: <root-dir>/dist)")
    parser.add_argument("--release-label", type=str, default=RELEASE_LABEL,
                        help="Release label (default: current release label)")
    parser.add_argument("--version", type=str, default=RELEASE_VERSION,
                        help="Release version (default: current version)")
    parser.add_argument("--zip-filename", type=str, default=ZIP_FILENAME,
                        help="Release ZIP filename")
    parser.add_argument("--manifest-filename", type=str, default=MANIFEST_FILENAME,
                        help="Release manifest filename")
    args = parser.parse_args()

    root_dir = args.root_dir.resolve()
    output_dir = args.output_dir.resolve() if args.output_dir else root_dir / "dist"

    build_release_package(
        root_dir,
        output_dir,
        label=args.release_label,
        version=args.version,
        zip_filename=args.zip_filename,
        manifest_filename=args.manifest_filename,
    )


if __name__ == "__main__":
    main()
