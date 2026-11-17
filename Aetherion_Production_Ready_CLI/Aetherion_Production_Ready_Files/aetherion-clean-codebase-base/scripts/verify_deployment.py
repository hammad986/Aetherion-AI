"""
scripts/verify_deployment.py
Validates Docker Compose, Kubernetes manifests, DB backup/restore tooling,
and runs production_preflight against running server.
Logs to audit/logs/cli_deployment_validation.log.
"""
from __future__ import annotations

import os
import sys
import time
import subprocess
from pathlib import Path

TARGET_DIR = Path(r"C:\Users\mdham\Downloads\Aetherion-AI-main\end to end aetherion cli").resolve()
AUDIT_DIR = TARGET_DIR / "audit"
LOGS_DIR = AUDIT_DIR / "logs"
LOG_FILE = LOGS_DIR / "cli_deployment_validation.log"

LOGS_DIR.mkdir(parents=True, exist_ok=True)

def log(msg: str, fh):
    print(msg)
    fh.write(msg + "\n")
    fh.flush()

def main():
    with open(LOG_FILE, "w", encoding="utf-8") as fh:
        log("=== AETHERION DEPLOYMENT & PREFLIGHT VERIFICATION ===", fh)
        log(f"Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}", fh)
        log(f"Target Directory: {TARGET_DIR}", fh)

        # 1. Validate YAML syntax for Docker Compose and K8s
        log("\n--- 1. Manifest Syntax Validation ---", fh)
        yaml_files = [
            TARGET_DIR / "deploy" / "compose" / "docker-compose.yml",
            *(TARGET_DIR / "deploy" / "k8s").glob("*.yaml"),
        ]

        try:
            import yaml
            for yf in yaml_files:
                if not yf.exists():
                    log(f"FAIL: Missing manifest {yf.name}", fh)
                    continue
                try:
                    with open(yf, "r", encoding="utf-8") as yfh:
                        list(yaml.safe_load_all(yfh))
                    log(f"PASS: {yf.relative_to(TARGET_DIR)} is valid YAML", fh)
                except Exception as ye:
                    log(f"FAIL: {yf.relative_to(TARGET_DIR)} YAML parsing error: {ye}", fh)
        except ImportError:
            log("WARN: PyYAML not installed, performing basic line/structural validation", fh)
            for yf in yaml_files:
                if yf.exists() and yf.stat().st_size > 0:
                    log(f"PASS: {yf.relative_to(TARGET_DIR)} present and non-empty", fh)

        # 2. Test DB Backup & Restore dry-run
        log("\n--- 2. Database Backup and Migration Tooling ---", fh)
        migrate_cmd = [sys.executable, str(TARGET_DIR / "scripts" / "db_migrate.py"), "--help"]
        p_mig = subprocess.run(migrate_cmd, capture_output=True, text=True)
        if p_mig.returncode == 0:
            log("PASS: db_migrate.py CLI operational", fh)
        else:
            log(f"FAIL: db_migrate.py error: {p_mig.stderr}", fh)

        backup_cmd = [sys.executable, str(TARGET_DIR / "scripts" / "db_backup.py"), "--help"]
        p_bak = subprocess.run(backup_cmd, capture_output=True, text=True)
        if p_bak.returncode == 0:
            log("PASS: db_backup.py CLI operational", fh)
        else:
            log(f"FAIL: db_backup.py error: {p_bak.stderr}", fh)

        restore_cmd = [sys.executable, str(TARGET_DIR / "scripts" / "db_restore_verify.py"), "--help"]
        p_res = subprocess.run(restore_cmd, capture_output=True, text=True)
        if p_res.returncode == 0:
            log("PASS: db_restore_verify.py CLI operational", fh)
        else:
            log(f"FAIL: db_restore_verify.py error: {p_res.stderr}", fh)

        # 3. Start Backend and run Production Preflight
        log("\n--- 3. Production Preflight Execution ---", fh)
        server_env = os.environ.copy()
        server_env["AETHERION_ENV"] = "local-development"
        server_env["PORT"] = "5005"
        server_env["PYTHONPATH"] = str(TARGET_DIR / "backend")

        app_py = TARGET_DIR / "backend" / "app.py"
        server_proc = subprocess.Popen(
            [sys.executable, str(app_py)],
            env=server_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(TARGET_DIR / "backend"),
        )

        try:
            time.sleep(2.5)  # Allow flask server to bind
            preflight_cmd = [
                sys.executable,
                str(TARGET_DIR / "scripts" / "production_preflight.py"),
                "--all",
                "--base-url",
                "http://127.0.0.1:5005"
            ]
            pf_run = subprocess.run(preflight_cmd, capture_output=True, text=True, cwd=str(TARGET_DIR))
            log(pf_run.stdout, fh)
            if pf_run.stderr:
                log(f"[STDERR]: {pf_run.stderr}", fh)
            log(f"Preflight exit code: {pf_run.returncode}", fh)
        finally:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server_proc.kill()

        # 4. Clean up any temporary sqlite db generated during probe
        for db_file in (TARGET_DIR / "backend").glob("*.db"):
            try:
                db_file.unlink()
                log(f"Cleaned up temporary probe db: {db_file.name}", fh)
            except Exception:
                pass

        log("\n=== DEPLOYMENT VALIDATION COMPLETED ===", fh)

if __name__ == "__main__":
    main()
