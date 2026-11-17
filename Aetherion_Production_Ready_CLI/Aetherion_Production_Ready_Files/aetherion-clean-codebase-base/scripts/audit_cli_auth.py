import os
import sys
import subprocess
import json
import psycopg2

sys.path.insert(0, os.path.abspath("."))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def run_cmd(cmd_list, env=None):
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = ".;backend;cli"
    if env:
        merged_env.update(env)
    res = subprocess.run(
        cmd_list,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged_env,
    )
    return res.returncode, res.stdout.strip(), res.stderr.strip()

try:
    from backend.db.test_target_policy import get_test_database_url
except ImportError:
    from db.test_target_policy import get_test_database_url


def main():
    print("=== STEP 1: AUTHENTICATING CLI ===")
    db_url = get_test_database_url()
    conn = psycopg2.connect(db_url)
    with conn.cursor() as cur:
        cur.execute("SELECT id, email FROM users ORDER BY id DESC LIMIT 5")
        users = cur.fetchall()
        print(f"Users in DB: {users}")

    # Let's register a dedicated CLI audit user or use an existing one
    import urllib.request
    import uuid

    audit_email = f"audit_user_{uuid.uuid4().hex[:6]}@example.com"
    audit_pass = "SecureAuditPass!2026"
    req_body = json.dumps({"name": "Audit User", "email": audit_email, "password": audit_pass}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:5000/api/auth/register",
        data=req_body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        reg_resp = json.loads(resp.read().decode("utf-8"))
        print(f"Registered audit user {audit_email}: {reg_resp}")

    # Now login via CLI
    code, stdout, stderr = run_cmd([
        sys.executable, "-m", "aetherion_cli", "login",
        "--email", audit_email,
        "--password", audit_pass,
        "--url", "http://127.0.0.1:5000"
    ])
    print(f"CLI login result: code={code}\n  stdout={stdout}\n  stderr={stderr}")

    # Verify whoami
    code, stdout, stderr = run_cmd([sys.executable, "-m", "aetherion_cli", "whoami"])
    print(f"CLI whoami: code={code}\n  stdout={stdout}\n  stderr={stderr}")

if __name__ == "__main__":
    main()
