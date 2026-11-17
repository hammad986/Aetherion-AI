#!/usr/bin/env python3
"""
scripts/db_backup.py — Database Backup Utility & Runbook Automation

Creates timestamped, verified backups of the authoritative database:
  - PostgreSQL: uses pg_dump with credentials securely injected from DATABASE_URL.
  - SQLite: uses online sqlite3 backup API with SHA256 integrity verification.

Usage:
  python scripts/db_backup.py --out-dir backups/
  python scripts/db_backup.py --json
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlparse

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
if os.path.isdir(_BACKEND_DIR) and _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from core.environment import get_environment_mode, is_fail_closed_environment


def sanitize_dsn(dsn: str) -> str:
    import re
    if not dsn:
        return ""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", dsn)


def compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def backup_sqlite(source_path: str, dest_path: str) -> dict:
    t0 = time.time()
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"Source SQLite database '{source_path}' does not exist.")

    # Use SQLite online backup API for crash-safe copy
    src = sqlite3.connect(source_path)
    dst = sqlite3.connect(dest_path)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()

    duration = time.time() - t0
    size_bytes = os.path.getsize(dest_path)
    checksum = compute_sha256(dest_path)

    return {
        "engine": "sqlite",
        "source": source_path,
        "destination": dest_path,
        "size_bytes": size_bytes,
        "sha256": checksum,
        "duration_sec": round(duration, 3),
    }


def backup_postgres(dsn: str, dest_path: str) -> dict:
    t0 = time.time()
    parsed = urlparse(dsn)

    env = dict(os.environ)
    if parsed.password:
        env["PGPASSWORD"] = parsed.password

    cmd = [
        "pg_dump",
        "-h", parsed.hostname or "localhost",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "postgres",
        "-F", "c",   # Custom format (compressed, suitable for pg_restore)
        "-f", dest_path,
        parsed.path.lstrip("/") or "postgres",
    ]

    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {proc.stderr or proc.stdout}")

    duration = time.time() - t0
    size_bytes = os.path.getsize(dest_path)
    checksum = compute_sha256(dest_path)

    return {
        "engine": "postgresql",
        "source": sanitize_dsn(dsn),
        "destination": dest_path,
        "size_bytes": size_bytes,
        "sha256": checksum,
        "duration_sec": round(duration, 3),
    }


def perform_backup(out_dir: str = "backups") -> dict:
    os.makedirs(out_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    db_url = os.getenv("DATABASE_URL", "").strip()
    fail_closed = is_fail_closed_environment()

    if db_url.startswith(("postgresql://", "postgres://")):
        dest_file = os.path.join(out_dir, f"aetherion_pg_{timestamp}.dump")
        res = backup_postgres(db_url, dest_file)
    else:
        if fail_closed:
            raise RuntimeError("Cannot perform production backup: DATABASE_URL is not PostgreSQL.")
        # Local SQLite backup
        sqlite_src = "sessions.db"
        if not os.path.exists(sqlite_src) and os.path.exists("test_aetherion.db"):
            sqlite_src = "test_aetherion.db"
        elif not os.path.exists(sqlite_src):
            # Create minimal source if completely empty in local dev
            with sqlite3.connect(sqlite_src) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS _schema_version (v INT)")
        dest_file = os.path.join(out_dir, f"aetherion_sqlite_{timestamp}.db")
        res = backup_sqlite(sqlite_src, dest_file)

    manifest_path = os.path.join(out_dir, f"manifest_{timestamp}.json")
    manifest = {
        "timestamp": timestamp,
        "mode": get_environment_mode().value,
        **res,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Aetherion Database Backup Utility")
    parser.add_argument("--out-dir", default="backups", help="Directory to store backup files")
    parser.add_argument("--json", action="store_true", help="Output summary as JSON")
    args = parser.parse_args()

    try:
        manifest = perform_backup(args.out_dir)
        if args.json:
            print(json.dumps(manifest, indent=2))
        else:
            print("=" * 60)
            print(f"  Backup Successful ({manifest['engine']})")
            print(f"  Destination: {manifest['destination']}")
            print(f"  Size:        {manifest['size_bytes']} bytes")
            print(f"  SHA256:      {manifest['sha256']}")
            print(f"  Duration:    {manifest['duration_sec']}s")
            print("=" * 60)
        return 0
    except Exception as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        else:
            print(f"[BACKUP FAILED] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
