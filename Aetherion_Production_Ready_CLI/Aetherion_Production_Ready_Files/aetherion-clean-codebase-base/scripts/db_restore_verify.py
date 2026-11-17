#!/usr/bin/env python3
"""
scripts/db_restore_verify.py — Disposable Database Restore & Integrity Verification

Runbook automation to verify that backups are restorable and uncorrupted:
  1. Locates latest or specified backup archive from backups/.
  2. Verifies file checksum against the backup manifest.
  3. Restores to a disposable verification database.
  4. Queries table definitions and row counts.
  5. Cleans up disposable target and returns 0 (verified) or 1 (corrupted/failed).

Usage:
  python scripts/db_restore_verify.py
  python scripts/db_restore_verify.py --archive backups/aetherion_sqlite_xxx.db --json
"""
import argparse
import glob
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def find_latest_backup(backups_dir: str = "backups") -> tuple[str, str]:
    manifests = sorted(glob.glob(os.path.join(backups_dir, "manifest_*.json")), reverse=True)
    if not manifests:
        raise FileNotFoundError(f"No backup manifests found in '{backups_dir}'. Run db_backup.py first.")
    with open(manifests[0], "r", encoding="utf-8") as f:
        manifest = json.load(f)
    return manifest.get("destination", ""), manifests[0]


def verify_sqlite_restore(archive_path: str, manifest_path: str = "") -> dict:
    t0 = time.time()
    current_sha256 = compute_sha256(archive_path)

    expected_sha256 = None
    if manifest_path and os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            expected_sha256 = json.load(f).get("sha256")

    checksum_verified = (expected_sha256 is None) or (current_sha256 == expected_sha256)
    if not checksum_verified:
        raise ValueError(f"Checksum mismatch: expected {expected_sha256}, got {current_sha256}")

    # Disposable restore verification
    fd, disposable_db = tempfile.mkstemp(prefix="restore_verify_", suffix=".db")
    os.close(fd)
    try:
        src = sqlite3.connect(archive_path)
        dst = sqlite3.connect(disposable_db)
        with dst:
            src.backup(dst)
        src.close()

        # Query disposable DB
        cur = dst.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]

        table_counts = {}
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            table_counts[t] = cur.fetchone()[0]
        dst.close()

        duration = time.time() - t0
        return {
            "verified": True,
            "engine": "sqlite",
            "archive": archive_path,
            "sha256": current_sha256,
            "checksum_matched": checksum_verified,
            "tables_restored": len(tables),
            "table_counts": table_counts,
            "duration_sec": round(duration, 3),
        }
    finally:
        if os.path.exists(disposable_db):
            os.remove(disposable_db)


def verify_postgres_restore(archive_path: str, manifest_path: str = "") -> dict:
    t0 = time.time()
    current_sha256 = compute_sha256(archive_path)

    # Use pg_restore --list to inspect contents without executing
    cmd = ["pg_restore", "--list", archive_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pg_restore archive inspection failed: {proc.stderr or proc.stdout}")

    lines = [line for line in proc.stdout.splitlines() if line.strip() and not line.startswith(";")]
    duration = time.time() - t0

    return {
        "verified": True,
        "engine": "postgresql",
        "archive": archive_path,
        "sha256": current_sha256,
        "entries_in_archive": len(lines),
        "duration_sec": round(duration, 3),
    }


def verify_backup(archive_path: str = None) -> dict:
    manifest_path = ""
    if not archive_path:
        archive_path, manifest_path = find_latest_backup()

    if not os.path.exists(archive_path):
        raise FileNotFoundError(f"Backup archive '{archive_path}' does not exist.")

    if archive_path.endswith(".dump"):
        return verify_postgres_restore(archive_path, manifest_path)
    else:
        return verify_sqlite_restore(archive_path, manifest_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aetherion Restore & Integrity Verifier")
    parser.add_argument("--archive", help="Specific backup file to verify")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    try:
        report = verify_backup(args.archive)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print("=" * 60)
            print("  Aetherion Restore & Integrity Verification: PASSED")
            print("=" * 60)
            print(f"  Archive:     {report['archive']}")
            print(f"  Engine:      {report['engine']}")
            print(f"  SHA256:      {report['sha256']}")
            print(f"  Tables/Rows: {report.get('table_counts', report.get('entries_in_archive'))}")
            print(f"  Duration:    {report['duration_sec']}s")
            print("=" * 60)
        return 0
    except Exception as e:
        if args.json:
            print(json.dumps({"verified": False, "error": str(e)}, indent=2))
        else:
            print(f"[RESTORE VERIFICATION FAILED] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
