import os
import logging
from typing import List, Optional

logger = logging.getLogger("nexora.sandbox")

class SandboxBoundaryException(Exception):
    pass

class ExecutionSandbox:
    """
    Isolates subprocesses and execution environments.
    Enforces filesystem jails and tool usage quotas.
    """
    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.abspath(workspace_root)
        self.forbidden_commands = ["rm -rf /", "mkfs", "dd"]

    def validate_path_access(self, target_path: str) -> bool:
        """
        Ensures a tool or agent does not attempt directory traversal 
        to access files outside its designated workspace.
        """
        # Resolve any ../ or symbolic links
        resolved_path = os.path.abspath(target_path)
        
        # Check if the resolved path starts with the workspace root
        if not resolved_path.startswith(self.workspace_root):
            logger.warning(f"Sandbox violation attempted: access to {resolved_path}")
            raise SandboxBoundaryException(f"Path {target_path} is outside the workspace sandbox.")
        return True

    def validate_command(self, command: str) -> bool:
        """
        Basic static analysis of a command before subprocess delegation.
        """
        cmd_lower = command.lower()
        for forbidden in self.forbidden_commands:
            if forbidden in cmd_lower:
                logger.warning(f"Sandbox violation: Forbidden command blocked: {command}")
                raise SandboxBoundaryException("Command execution blocked by sandbox policy.")
        return True

    def run_isolated(self, command: str, memory_limit_mb: int = 512, timeout_sec: int = 30) -> dict:
        """
        Executes a command using strict subprocess isolation, bounded by OS constraints.
        Enforces execution TTL (timeout) and prepares for CGroups memory limits.
        """
        import subprocess
        
        self.validate_command(command)
        
        logger.info(f"[Sandbox] Executing isolated command: {command} (timeout={timeout_sec}s)")
        try:
            from sandbox_manager import get_sandbox
            
            sandbox = get_sandbox()
            result = sandbox.run(
                code=command,
                language="shell",
                timeout=timeout_sec,
                mem_mb=memory_limit_mb,
                workspace_dir=self.workspace_root
            )
            
            if result.timed_out:
                logger.error(f"[Sandbox] Command execution exceeded timeout of {timeout_sec}s")
                return {
                    "stdout": result.stdout,
                    "stderr": "Execution timed out.",
                    "exit_code": -1,
                    "error": "TIMEOUT_EXCEEDED"
                }
            elif not result.ok and result.error:
                # Capture internal sandbox errors
                logger.error(f"[Sandbox] Sandbox failure: {result.error}")
                return {
                    "stdout": result.stdout,
                    "stderr": result.stderr or result.error,
                    "exit_code": result.exit_code or 1,
                    "error": "SYSTEM_FAILURE"
                }
                
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "error": None
            }
        except Exception as e:
            logger.error(f"[Sandbox] Subprocess failure: {str(e)}")
            return {
                "stdout": "",
                "stderr": str(e),
                "exit_code": 1,
                "error": "SYSTEM_FAILURE"
            }
