import threading
import json
import time

def run_clone_pipeline(safe_session_id, url):
    def _clone_pipeline():
        try:
            from docker_runtime import docker_rt
            from agent_state_machine import RedisSSEBridge

            def log_msg(msg):
                print(f"[ORCHESTRATOR] {json.dumps({'kind': 'state_change', 'agent_state': msg, 'timestamp': time.time()})}")
                RedisSSEBridge.broadcast_to_session(safe_session_id, "state_change", {"agent_state": msg, "timestamp": time.time()})

            log_msg(f"Initializing Docker Sandbox for {safe_session_id}...")
            docker_rt.ensure_container(safe_session_id)

            log_msg(f"Cloning repository: {url}")
            clone_cmd = f"git clone --depth 1 {url} ."
            res = docker_rt.execute_command(safe_session_id, clone_cmd)
            
            if not res["ok"]:
                log_msg(f"Clone failed: {res.get('stderr')}")
                return

            log_msg("Detecting dependencies...")
            ls_res = docker_rt.execute_command(safe_session_id, "ls -la")
            files = ls_res.get("stdout", "")

            if "package.json" in files:
                log_msg("Found package.json. Running npm install...")
                res = docker_rt.execute_command(safe_session_id, "npm install", timeout=300)
                if res["ok"]:
                    log_msg("npm install completed successfully.")
                else:
                    log_msg(f"npm install failed: {res.get('stderr')}")
                    
            elif "requirements.txt" in files:
                log_msg("Found requirements.txt. Running pip install...")
                res = docker_rt.execute_command(safe_session_id, "pip install -r requirements.txt", timeout=300)
                if res["ok"]:
                    log_msg("pip install completed successfully.")
                else:
                    log_msg(f"pip install failed: {res.get('stderr')}")
            
            log_msg("Workspace environment is ready.")

        except Exception as e:
            print(f"[ORCHESTRATOR ERROR] Pipeline failed: {e}")

    # Start pipeline asynchronously to prevent timeout
    t = threading.Thread(target=_clone_pipeline)
    t.start()
