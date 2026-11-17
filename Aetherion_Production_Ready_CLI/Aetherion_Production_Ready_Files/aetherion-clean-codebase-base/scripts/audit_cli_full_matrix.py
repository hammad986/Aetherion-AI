import os
import sys
import subprocess
import json

sys.path.insert(0, os.path.abspath("."))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def run_cli(args, timeout=15):
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
            "error": None,
        }
    except subprocess.TimeoutExpired:
        return {
            "cmd": "aetherion " + " ".join(args),
            "exit_code": -1,
            "stdout": "",
            "stderr": "TIMEOUT",
            "status": "TIMEOUT",
            "error": f"Timed out after {timeout}s",
        }
    except Exception as e:
        return {
            "cmd": "aetherion " + " ".join(args),
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "status": "ERROR",
            "error": str(e),
        }

def main():
    print("=== EXECUTING COMPLETE CLI COMMAND AUDIT MATRIX ===")
    test_cases = [
        # 1. Version & Whoami & Doctor
        ["version"],
        ["whoami"],
        ["doctor"],
        
        # 2. Config
        ["config"],
        ["config", "show"],
        ["config", "get", "api_url"],
        
        # 3. Provider & Providers
        ["provider", "list"],
        ["providers", "list"],
        ["provider", "active"],
        ["provider", "test"],
        
        # 4. Worker
        ["worker", "status"],
        ["worker", "list"],
        
        # 5. D3 Features: Kanban
        ["kanban", "board"],
        ["kanban", "list"],
        
        # 6. D3 Features: Automation
        ["automation", "list"],
        ["automation", "status"],
        
        # 7. D3 Features: Knowledge Graph
        ["kg", "list"],
        ["kg", "stats"],
        ["kg", "search", "--query", "auth"],
        
        # 8. D3 Features: Worktree & Git
        ["worktree", "list"],
        ["git", "status"],
        
        # 9. Workspace
        ["workspace", "list"],
        
        # 10. Memory
        ["memory", "list"],
        ["memory", "search", "--query", "architecture"],
        
        # 11. Sessions & History
        ["sessions", "list"],
        ["history", "list"],
        
        # 12. Jobs & Task
        ["jobs", "list"],
        ["task", "list"],
        
        # 13. Plugins & Skills
        ["plugins", "list"],
        ["skills", "list"],
        
        # 14. Repo
        ["repo", "overview"],
        
        # 15. Diff & Logs & Replay & Artifacts
        ["diff"],
        ["logs", "--limit", "10"],
        ["replay", "list"],
        ["artifacts", "list"],
        
        # 16. Plan (dry run)
        ["plan", "Build a simple healthcheck route"],
    ]

    results = []
    for tc in test_cases:
        r = run_cli(tc)
        print(f"[{r['status']}] {r['cmd']} (exit={r['exit_code']})")
        if r["exit_code"] != 0:
            print(f"   stderr: {r['stderr'][:200]}")
            print(f"   stdout: {r['stdout'][:200]}")
        results.append(r)

    # Summary
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] in ("FAIL", "ERROR", "TIMEOUT"))
    print(f"\nMatrix Complete: {passed} PASSED, {failed} FAILED out of {len(results)} commands tested.")

    with open("audit/cli_matrix_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    os.makedirs("audit", exist_ok=True)
    main()
