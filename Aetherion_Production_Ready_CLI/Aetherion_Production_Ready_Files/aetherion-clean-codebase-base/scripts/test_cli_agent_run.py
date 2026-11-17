import os
import sys
import subprocess

sys.path.insert(0, os.path.abspath("."))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def main():
    print("=== TESTING CLI AGENT RUN ===")
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = ".;backend;cli"
    
    # Run a simple task via aetherion run
    cmd = [sys.executable, "-m", "aetherion_cli", "run", "Write a fibonacci function in math_utils.py"]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged_env,
        timeout=120,
    )
    print(f"Exit code: {res.returncode}")
    print(f"STDOUT:\n{res.stdout}")
    print(f"STDERR:\n{res.stderr}")

if __name__ == "__main__":
    main()
