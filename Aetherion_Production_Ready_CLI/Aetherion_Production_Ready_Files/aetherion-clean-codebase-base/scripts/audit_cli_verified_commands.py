import os
import sys
import subprocess
import json

sys.path.insert(0, os.path.abspath("."))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def run_cli(args, timeout=20):
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = ".;backend;cli"
    cmd = [sys.executable, "-m", "aetherion_cli"] + args
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=merged_env,
            timeout=timeout,
        )
        return {
            "cmd": "aetherion " + " ".join(args),
            "exit_code": res.returncode,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip(),
            "status": "PASS" if res.returncode == 0 else "FAIL",
        }
    except Exception as e:
        return {
            "cmd": "aetherion " + " ".join(args),
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "status": "ERROR",
        }

try:
    from backend.db.test_target_policy import get_test_database_url
except ImportError:
    from db.test_target_policy import get_test_database_url


def main():
    print("=== TESTING CORRECTED CLI COMMAND ARGUMENTS ===")
    
    # 1. Create a task to get a real TASK_ID
    task_res = run_cli(["task", "create", "Inspect repository structure"])
    print(f"[{task_res['status']}] {task_res['cmd']} (exit={task_res['exit_code']})")
    print(f"  stdout: {task_res['stdout'][:200]}")
    
    # Extract task id from stdout if available
    task_id = None
    for line in task_res['stdout'].splitlines():
        if "Task created:" in line or "task_" in line or "ID:" in line:
            parts = line.split()
            for p in parts:
                if p.startswith("task_") or p.startswith("agent_task_"):
                    task_id = p.strip(".,;:()")
                    break
    
    if not task_id:
        # Fallback to listing task via durable_state
        import psycopg2
        db_url = get_test_database_url()
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
            tables = [r[0] for r in cur.fetchall()]
            print("Postgres tables:", tables)
            for t in ["durable_executions", "tasks", "agent_tasks", "fabric_queue", "fabric_workers"]:
                if t in tables:
                    try:
                        cur.execute(f"SELECT * FROM {t} LIMIT 1")
                        print(f"Sample from {t} ok")
                    except Exception as te:
                        print(f"Error querying {t}: {te}")
    print(f"Using TASK_ID: {task_id}")

    test_commands = [
        ["skills", "local"],
        ["repo", "scan"],
        ["repo", "symbols"],
        ["kg", "list", "-s", "default"],
        ["kg", "search", "auth"],
        ["memory", "search", "architecture"],
        ["provider", "test", "mock"],
    ]

    if task_id:
        test_commands.extend([
            ["task", "status", task_id],
            ["diff", task_id],
            ["logs", task_id],
        ])

    for tc in test_commands:
        r = run_cli(tc)
        print(f"[{r['status']}] {r['cmd']} (exit={r['exit_code']})")
        if r['exit_code'] != 0:
            print(f"   stderr: {r['stderr'][:200]}")
            print(f"   stdout: {r['stdout'][:200]}")

if __name__ == "__main__":
    main()
