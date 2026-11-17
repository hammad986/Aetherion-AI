import sys
import io
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def main():
    with open('cli/aetherion_cli/main.py', encoding='utf-8') as f:
        content = f.read()

    idx_rat = content.find("def _run_agent_task(")
    if idx_rat != -1:
        print("=== _RUN_AGENT_TASK ===")
        print(content[idx_rat:idx_rat+2500])

if __name__ == "__main__":
    main()
