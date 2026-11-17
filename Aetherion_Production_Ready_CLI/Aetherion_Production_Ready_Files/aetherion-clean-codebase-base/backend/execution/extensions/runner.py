"""
backend/execution/extensions/runner.py — Isolated extension execution runner routing by trust level.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..tools.schemas import ToolRequest, ToolResult
from .container import ContainerWorker, is_docker_available
from .policy import ExtensionSecurityPolicy, ExtensionTrustLevel
from .protocol import ExtensionExecutionRequest, ExtensionExecutionResponse

logger = logging.getLogger("aetherion.extensions.runner")


class SubprocessWorker:
    """Executes extension tools in a separate host subprocess with a scrubbed environment.
    Only permitted for TRUSTED_LOCAL extensions when AETHERION_TRUSTED_PLUGIN_MODE=1.
    """

    def __init__(self):
        self.worker_script_path = Path(__file__).resolve().parent / "worker.py"

    def execute(
        self,
        request: ExtensionExecutionRequest,
        plugin_dir: Path,
        workspace_dir: Path,
        policy: ExtensionSecurityPolicy,
    ) -> ExtensionExecutionResponse:
        t0 = time.time()
        runtime_temp = Path(tempfile.mkdtemp(prefix="aeth_subp_runtime_"))
        req_file = runtime_temp / "request.json"
        resp_file = runtime_temp / "response.json"

        with open(req_file, "w", encoding="utf-8") as f:
            json.dump(request.to_dict(), f)

        # Build scrubbed environment for subprocess
        clean_env: Dict[str, str] = {
            "PYTHONPATH": str(plugin_dir.resolve()),
            "PYTHONUNBUFFERED": "1",
            "PATH": os.environ.get("PATH", ""),
        }
        for sys_key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"):
            if sys_key in os.environ:
                clean_env[sys_key] = os.environ[sys_key]

        # Strip any credential / API key patterns from environment
        for k in list(clean_env.keys()):
            upper_k = k.upper()
            if any(term in upper_k for term in ("KEY", "SECRET", "TOKEN", "PASSWORD", "AUTH", "CRED")):
                clean_env.pop(k, None)

        cmd = [
            sys.executable,
            str(self.worker_script_path),
            "--input", str(req_file),
            "--output", str(resp_file),
            "--plugin-dir", str(plugin_dir.resolve()),
            "--workspace-dir", str(workspace_dir.resolve()),
        ]

        try:
            proc = subprocess.run(
                cmd,
                env=clean_env,
                cwd=str(workspace_dir.resolve()),
                capture_output=True,
                text=True,
                timeout=policy.default_timeout_seconds,
            )

            duration_ms = (time.time() - t0) * 1000.0

            if resp_file.exists():
                try:
                    with open(resp_file, "r", encoding="utf-8") as f:
                        resp_data = json.load(f)
                    resp = ExtensionExecutionResponse.from_dict(resp_data)
                    resp.duration_ms = duration_ms
                    return resp
                except Exception as parse_err:
                    return ExtensionExecutionResponse(
                        request_id=request.request_id,
                        ok=False,
                        exit_code=proc.returncode,
                        error=f"Failed to parse subprocess worker response: {parse_err}",
                        stdout=proc.stdout,
                        stderr=proc.stderr,
                        duration_ms=duration_ms,
                    )
            else:
                return ExtensionExecutionResponse(
                    request_id=request.request_id,
                    ok=False,
                    exit_code=proc.returncode,
                    error="Subprocess worker exited without producing response.",
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    duration_ms=duration_ms,
                )

        except subprocess.TimeoutExpired:
            return ExtensionExecutionResponse(
                request_id=request.request_id,
                ok=False,
                exit_code=124,
                error=f"Execution timed out after {policy.default_timeout_seconds:.1f} seconds.",
                stderr="Timeout expired in subprocess sandbox.",
                duration_ms=(time.time() - t0) * 1000.0,
                security_event=True,
            )
        except Exception as exc:
            return ExtensionExecutionResponse(
                request_id=request.request_id,
                ok=False,
                exit_code=1,
                error=f"Subprocess worker execution error: {exc}",
                duration_ms=(time.time() - t0) * 1000.0,
            )
        finally:
            shutil.rmtree(runtime_temp, ignore_errors=True)


class ExtensionRunner:
    """Coordinates isolated execution of plugin tools based on trust level and security policy."""

    def __init__(
        self,
        container_worker: Optional[ContainerWorker] = None,
        subprocess_worker: Optional[SubprocessWorker] = None,
    ):
        self.container_worker = container_worker or ContainerWorker()
        self.subprocess_worker = subprocess_worker or SubprocessWorker()

    def execute_tool(
        self,
        req: ToolRequest,
        plugin_record: Dict[str, Any],
        workspace_dir: Path,
        timeout_seconds: Optional[float] = None,
    ) -> ToolResult:
        """Dispatch tool execution to appropriate isolated worker according to trust tier."""
        tool_name = getattr(req, "tool", None) or getattr(req, "name", None) or (req.get("tool") if isinstance(req, dict) else "") or ""
        arguments = getattr(req, "arguments", {}) or (req.get("arguments") if isinstance(req, dict) else {}) or {}
        req_id = getattr(req, "task_id", "") or f"req_{int(time.time() * 1000)}"

        trust_level = plugin_record.get("trust_level") or ExtensionTrustLevel.UNTRUSTED.value
        permissions = plugin_record.get("permissions", [])
        install_path = Path(plugin_record.get("install_path", ""))
        entrypoint = plugin_record.get("entrypoint", "main.py:run")

        policy = ExtensionSecurityPolicy.from_permissions(
            permissions=permissions,
            trust_level=trust_level,
            timeout_seconds=timeout_seconds,
        )

        exec_req = ExtensionExecutionRequest(
            request_id=req_id,
            plugin_id=plugin_record.get("id", ""),
            entrypoint=entrypoint,
            tool_name=tool_name,
            arguments=arguments,
            context={
                "workspace_dir": str(workspace_dir.resolve()),
                "permissions": list(permissions),
                "plugin_id": plugin_record.get("id", ""),
            },
            timeout_seconds=policy.default_timeout_seconds,
        )

        # ── Trust Tier Routing ────────────────────────────────────────────────
        if trust_level == ExtensionTrustLevel.UNTRUSTED.value:
            # Untrusted: Container isolation is MANDATORY
            if not self.container_worker.is_available():
                logger.error("Untrusted plugin '%s' rejected: Docker sandbox unavailable.", plugin_record.get("id"))
                return ToolResult(
                    ok=False,
                    exit_code=1,
                    error="SANDBOX_UNAVAILABLE: Untrusted plugins require isolated container execution, but Docker runtime is not available.",
                    security_event=True,
                )
            resp = self.container_worker.execute(exec_req, install_path, workspace_dir, policy)

        elif trust_level == ExtensionTrustLevel.TRUSTED_LOCAL.value:
            # Trusted Local: Container preferred; subprocess allowed if developer mode is explicitly active
            trusted_dev_mode = os.environ.get("AETHERION_TRUSTED_PLUGIN_MODE") == "1"
            if self.container_worker.is_available():
                resp = self.container_worker.execute(exec_req, install_path, workspace_dir, policy)
            elif trusted_dev_mode:
                logger.info("Executing TRUSTED_LOCAL plugin '%s' via scrubbed subprocess worker.", plugin_record.get("id"))
                resp = self.subprocess_worker.execute(exec_req, install_path, workspace_dir, policy)
            else:
                return ToolResult(
                    ok=False,
                    exit_code=1,
                    error="SANDBOX_UNAVAILABLE: Docker runtime unavailable and AETHERION_TRUSTED_PLUGIN_MODE is not enabled.",
                    security_event=True,
                )

        elif trust_level == ExtensionTrustLevel.SYSTEM.value:
            # System built-in plugin: Subprocess or Container
            if self.container_worker.is_available():
                resp = self.container_worker.execute(exec_req, install_path, workspace_dir, policy)
            else:
                resp = self.subprocess_worker.execute(exec_req, install_path, workspace_dir, policy)

        else:
            return ToolResult(
                ok=False,
                exit_code=1,
                error=f"Invalid extension trust level: '{trust_level}'",
                security_event=True,
            )

        return ToolResult(
            ok=resp.ok,
            exit_code=resp.exit_code,
            stdout=resp.stdout,
            stderr=resp.stderr,
            error=resp.error,
            data=resp.data,
            duration_ms=resp.duration_ms,
            security_event=resp.security_event,
        )
