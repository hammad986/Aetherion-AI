import os
import sys
import subprocess

sys.path.insert(0, os.path.abspath("."))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def main():
    cmds = [
        ['task', 'status', 'agent_task_6f9a2a6caa67'],
        ['logs', 'agent_task_6f9a2a6caa67'],
        ['diff', 'agent_task_6f9a2a6caa67'],
        ['repo', 'scan', '-s', 'default'],
        ['repo', 'symbols', '-s', 'default'],
        ['kg', 'search', 'auth', '-s', 'default'],
    ]
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = ".;backend;cli"
    for cmd in cmds:
        res = subprocess.run(
            [sys.executable, "-m", "aetherion_cli"] + cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=merged_env,
        )
        status = 'PASS' if res.returncode == 0 else f'FAIL ({res.returncode})'
        print(f"[{status}] aetherion {' '.join(cmd)}")
        if res.returncode != 0:
            print(f"  stderr: {res.stderr.strip()[:200]}")
        else:
            first_line = res.stdout.strip().splitlines()[0] if res.stdout.strip() else "(empty)"
            print(f"  stdout: {first_line}")

if __name__ == "__main__":
    main()
