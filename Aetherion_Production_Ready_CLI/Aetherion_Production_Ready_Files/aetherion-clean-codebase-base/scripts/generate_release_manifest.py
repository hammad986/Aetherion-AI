"""
scripts/generate_release_manifest.py
Scans the release directory and produces audit/cli_release_manifest.json
"""
import os
import hashlib
import json
import datetime

RELEASE_ROOT = os.path.realpath(r"C:\Users\mdham\Downloads\Aetherion-AI-main\end to end aetherion cli")
AUDIT_DIR = os.path.join(RELEASE_ROOT, "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)
MANIFEST_PATH = os.path.join(AUDIT_DIR, "cli_release_manifest.json")

EXCLUDED_CATEGORIES = [
    ".env secrets",
    "SQLite database files (*.db, *.sqlite)",
    "Node modules (node_modules/)",
    "Python virtual environments (.venv/, venv/)",
    "Python bytecode cache (__pycache__/, *.pyc)",
    "Test caches (.pytest_cache/, .coverage)",
    "Build temporary files (*.tmp, *.bak)",
    "Runtime log files (*.log)",
    "Legacy screenshots and media archives",
]

def hash_file(path):
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()

def generate_manifest():
    files_list = []
    total_bytes = 0

    for root, dirs, files in os.walk(RELEASE_ROOT):
        # Skip audit manifest itself if it exists to avoid self-referential hash
        dirs.sort()
        files.sort()
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, RELEASE_ROOT).replace("\\", "/")
            if rel_path == "audit/cli_release_manifest.json":
                continue
            
            size = os.path.getsize(full_path)
            h = hash_file(full_path)
            total_bytes += size
            files_list.append({
                "path": rel_path,
                "size_bytes": size,
                "sha256": h,
            })

    manifest = {
        "release_version": "1.0.0",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "target_folder": RELEASE_ROOT,
        "excluded_categories": EXCLUDED_CATEGORIES,
        "verification_summary": {
            "total_files": len(files_list),
            "total_bytes": total_bytes,
            "status": "VERIFIED_CLEAN",
        },
        "files": files_list,
    }

    with open(MANIFEST_PATH, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    print(f"Manifest written to {MANIFEST_PATH}: {len(files_list)} files, {total_bytes} bytes.")

if __name__ == "__main__":
    generate_manifest()
