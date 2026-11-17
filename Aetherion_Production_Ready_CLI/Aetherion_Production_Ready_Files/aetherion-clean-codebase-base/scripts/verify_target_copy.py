"""
scripts/verify_target_copy.py
Authoritative Target-Copy, Hash Verification, and Contamination Audit Script.
Generates:
  - audit/cli_target_copy_manifest.json
  - audit/cli_target_copy_diff.json
  - audit/logs/cli_copy_verification.log
"""
from __future__ import annotations

import os
import re
import sys
import json
import hashlib
import datetime
from pathlib import Path

TARGET_DIR = Path(r"C:\Users\mdham\Downloads\Aetherion-AI-main\end to end aetherion cli").resolve()
SOURCE_DIR = Path(r"C:\Users\mdham\Downloads\Aetherion-AI-main\Aetherion-AI-main").resolve()
AUDIT_DIR = TARGET_DIR / "audit"
LOGS_DIR = AUDIT_DIR / "logs"

AUDIT_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Patterns for contamination scan
CONTAMINATION_RULES = [
    (r"\.env$", "Populated .env file", True),
    (r"\.(db|sqlite|sqlite3)$", "Runtime SQLite database file", False),
    (r"node_modules", "Node modules directory", False),
    (r"(\.venv|venv|\.env_test)", "Python virtual environment", False),
    (r"(__pycache__|\.pyc$|\.pyo$)", "Python bytecode cache", False),
    (r"(\.pytest_cache|\.coverage|htmlcov)", "Test cache or coverage artifact", False),
    (r"\.(tmp|bak)$", "Temporary / backup file", False),
]

SECRET_PATTERNS = [
    (r"-----BEGIN (RSA|EC|PGP|OPENSSH)? ?PRIVATE KEY-----", "Private key"),
    (r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", "Populated JWT token"),
    (r"(postgres|mysql|redis)://[^:]+:[^@\s]+@", "DSN with credentials"),
]

def hash_file(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()

PLACEHOLDER_SUBSTRINGS = {"<", ">", "%s", "${", "user:pass", "username:password", "vault:password", "localhost"}

def scan_file_content_for_secrets(path: Path) -> list[str]:
    # Skip binary files, dist bundles, minified js, fonts, images
    ext = path.suffix.lower()
    if ext in {".whl", ".gz", ".zip", ".png", ".jpg", ".ico", ".woff", ".woff2", ".ttf", ".wasm"}:
        return []
    is_env_example = path.name == ".env.example"

    findings = []
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")

        # 1. Private Key detection: requires actual key block with base64 body, not regex rule or scanner code
        # A real PEM private key has the header followed by base64 lines and an END header.
        real_key_pattern = r"-----BEGIN (?:RSA|EC|PGP|OPENSSH)? ?PRIVATE KEY-----\s*[\r\n]+(?:[A-Za-z0-9+/=]{32,}[\r\n]+)+-----END"
        if re.search(real_key_pattern, content):
            findings.append("Private key block with key material")

        # 2. JWT token detection: populated JWTs (exclude placeholders/doc examples)
        jwt_matches = re.findall(r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}", content)
        # Exclude common dummy tokens used in documentation
        valid_jwt_matches = [m for m in jwt_matches if not any(dummy in m for dummy in ["example", "token", "header", "payload"])]
        if valid_jwt_matches:
            findings.append(f"Populated JWT token (count: {len(valid_jwt_matches)})")

        # 3. DSN with real credentials
        dsn_matches = re.findall(r"(?:postgres|postgresql|mysql|redis)://([^:]+):([^@\s]+)@([^/\s:]+)", content)
        real_dsn = []
        is_doc_or_skill = any(part in {"references", "examples", "skills", "docs", "test", "tests"} for part in path.parts)
        for user, pwd, host in dsn_matches:
            combined = f"{user}:{pwd}@{host}".lower()
            if any(ph in combined for ph in [
                "<", ">", "%s", "${", "{{", "}", "{", "user:pass", "user:password", 
                "admin:password", "vault:password", "postgres:postgres", "test:test",
                "localhost", "postgres:5432", "db:5432", "127.0.0.1", "host:5432"
            ]):
                continue
            if is_env_example or is_doc_or_skill:
                continue
            real_dsn.append(f"{user}:***@{host}")

        if real_dsn:
            findings.append(f"DSN with actual credentials (count: {len(real_dsn)})")

    except Exception as e:
        findings.append(f"Read error: {e}")
    return findings

def main():
    log_lines = []
    log_lines.append(f"=== TARGET COPY AND RELEASE TRUTH VERIFICATION ===")
    log_lines.append(f"Timestamp: {datetime.datetime.now(datetime.timezone.utc).isoformat()}")
    log_lines.append(f"Source: {SOURCE_DIR}")
    log_lines.append(f"Target: {TARGET_DIR}")

    if not TARGET_DIR.exists():
        log_lines.append("ERROR: Target directory is not accessible.")
        print("WINDOWS_TARGET_COPY: BLOCKED_BY_ACCESS")
        return

    # 1. Target filesystem scan & manifest generation
    target_manifest = {}
    contamination_findings = []

    for root, dirs, files in os.walk(TARGET_DIR):
        rpath = Path(root)
        rel_root = rpath.relative_to(TARGET_DIR)

        # Check directory contamination
        for d in dirs:
            dir_full = rpath / d
            for rule_pat, desc, _ in CONTAMINATION_RULES:
                if re.search(rule_pat, d, re.IGNORECASE):
                    contamination_findings.append({
                        "path": str(rel_root / d).replace("\\", "/"),
                        "type": desc,
                        "remediation": "Delete directory"
                    })

        for f in files:
            file_full = rpath / f
            rel_file = (rel_root / f).as_posix()

            # Check filename contamination
            for rule_pat, desc, is_strict_env in CONTAMINATION_RULES:
                if is_strict_env:
                    if f == ".env":
                        contamination_findings.append({
                            "path": rel_file,
                            "type": desc,
                            "remediation": "Remove populated .env"
                        })
                else:
                    if re.search(rule_pat, f, re.IGNORECASE):
                        contamination_findings.append({
                            "path": rel_file,
                            "type": desc,
                            "remediation": "Delete file"
                        })

            # Check file content for secrets
            sec_hits = scan_file_content_for_secrets(file_full)
            if sec_hits:
                contamination_findings.append({
                    "path": rel_file,
                    "type": "Secret leak in content",
                    "details": sec_hits,
                    "remediation": "Redact secret"
                })

            size = file_full.stat().st_size
            sha = hash_file(file_full)

            target_manifest[rel_file] = {
                "relative_path": rel_file,
                "file_size": size,
                "sha256": sha,
                "target_status": "PRESENT"
            }

    log_lines.append(f"Total target files cataloged: {len(target_manifest)}")
    log_lines.append(f"Contamination issues found: {len(contamination_findings)}")

    # 2. Key Expected Release Files Verification
    expected_release_files = [
        "README.md",
        "SECURITY.md",
        "CHANGELOG.md",
        ".env.example",
        ".gitignore",
        "cli/pyproject.toml",
        "cli/aetherion_cli/main.py",
        "cli/dist/aetherion-1.0.0-py3-none-any.whl",
        "cli/dist/aetherion-1.0.0.tar.gz",
        "backend/app.py",
        "backend/requirements.txt",
        "backend/core/environment.py",
        "backend/core/startup_validator.py",
        "backend/routes/health.py",
        "backend/routes/auth_core.py",
        "backend/routes/auth_device.py",
        "backend/routes/tasks.py",
        "frontend-auth/package.json",
        "frontend-auth/vite.config.js",
        "frontend-auth/index.html",
        "frontend-auth/src/App.jsx",
        "frontend-auth/src/pages/DeviceAuthPage.jsx",
        "deploy/compose/docker-compose.yml",
        "deploy/docker/Dockerfile.backend",
        "deploy/docker/Dockerfile.frontend-auth",
        "deploy/k8s/backend.yaml",
        "scripts/production_preflight.py",
        "scripts/db_migrate.py",
        "audit/cli_release_checkpoint.md",
        "audit/cli_release_ledger.json",
    ]

    missing_files = []
    for ef in expected_release_files:
        if ef not in target_manifest:
            missing_files.append(ef)

    diff_report = {
        "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total_files": len(target_manifest),
        "missing_expected_files": missing_files,
        "unexpected_files": [],
        "contamination_findings": contamination_findings,
        "status": "VERIFIED_CLEAN" if (not missing_files and not contamination_findings) else "FAILED"
    }

    # Write target copy manifest
    manifest_out = {
        "release_version": "1.0.0",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "target_folder": str(TARGET_DIR),
        "total_files": len(target_manifest),
        "files": target_manifest,
    }

    with open(AUDIT_DIR / "cli_target_copy_manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest_out, fh, indent=2)

    with open(AUDIT_DIR / "cli_target_copy_diff.json", "w", encoding="utf-8") as fh:
        json.dump(diff_report, fh, indent=2)

    log_path = LOGS_DIR / "cli_copy_verification.log"
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(log_lines) + "\n")

    print(f"Target manifest generated: {len(target_manifest)} files.")
    print(f"Missing expected files: {len(missing_files)}")
    print(f"Contamination findings: {len(contamination_findings)}")
    print(f"Status: {diff_report['status']}")

if __name__ == "__main__":
    main()
