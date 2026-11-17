"""
backend/execution/extensions/container.py — Ephemeral Docker container sandbox for untrusted plugins.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .policy import ExtensionSecurityPolicy
from .protocol import ExtensionExecutionRequest, ExtensionExecutionResponse

logger = logging.getLogger("aetherion.extensions.container")

_DOCKER_AVAILABLE: Optional[bool] = None


def is_docker_available() -> bool:
    """Safely check if Docker daemon is responsive without crashing if absent."""
    global _DOCKER_AVAILABLE
    try:
        import docker
        client = docker.from_env()
        ping_ok = client.ping()
        _DOCKER_AVAILABLE = bool(ping_ok)
        return _DOCKER_AVAILABLE
    except Exception as exc:
        logger.debug("Docker not available: %s", exc)
        _DOCKER_AVAILABLE = False
        return False


class ContainerWorker:
    """Manages ephemeral container lifecycles for executing untrusted plugin tools."""

    def __init__(self, image_name: str = "python:3.11-slim"):
        self.image_name = image_name
        self.worker_script_path = Path(__file__).resolve().parent / "worker.py"

    def is_available(self) -> bool:
        return is_docker_available()

    def execute(
        self,
        request: ExtensionExecutionRequest,
        plugin_dir: Path,
        workspace_dir: Path,
        policy: ExtensionSecurityPolicy,
    ) -> ExtensionExecutionResponse:
        """Execute plugin tool request inside ephemeral isolated container."""
        if not self.is_available():
            return ExtensionExecutionResponse(
                request_id=request.request_id,
                ok=False,
                exit_code=1,
                error="SANDBOX_UNAVAILABLE: Docker daemon is unavailable for container isolation.",
                security_event=True,
            )

        import docker
        from docker.errors import DockerException

        client = docker.from_env()
        t0 = time.time()

        # Create two temporary directories:
        #   runtime_io  — rw for request.json / response.json exchange only
        #   runtime_bin — ro for worker.py (prevents container tampering of worker script)
        runtime_io = Path(tempfile.mkdtemp(prefix="aeth_cont_io_"))
        runtime_bin = Path(tempfile.mkdtemp(prefix="aeth_cont_bin_"))
        # Keep legacy alias so finally-block cleanup works on both dirs
        runtime_temp = runtime_io

        req_file = runtime_io / "request.json"
        resp_file = runtime_io / "response.json"
        worker_dest = runtime_bin / "worker.py"

        shutil.copy2(str(self.worker_script_path), str(worker_dest))
        with open(req_file, "w", encoding="utf-8") as f:
            json.dump(request.to_dict(), f)

        # Prepare mount map — worker script is mounted read-only from a separate dir
        workspace_mode = "rw" if policy.allow_workspace_write else "ro"
        volumes = {
            str(runtime_io.resolve()):  {"bind": "/runtime",     "mode": "rw"},  # I/O exchange
            str(runtime_bin.resolve()): {"bind": "/runtime_bin", "mode": "ro"},  # worker binary (read-only)
            str(plugin_dir.resolve()):  {"bind": "/plugin",      "mode": "ro"},
            str(workspace_dir.resolve()): {"bind": "/workspace", "mode": workspace_mode},
        }

        command = [
            "python",
            "/runtime_bin/worker.py",
            "--input", "/runtime/request.json",
            "--output", "/runtime/response.json",
            "--plugin-dir", "/plugin",
            "--workspace-dir", "/workspace",
        ]

        container = None
        try:
            container = client.containers.run(
                image=self.image_name,
                command=command,
                volumes=volumes,
                environment=policy.scrubbed_env,
                network_mode=policy.network_mode,
                mem_limit=policy.mem_limit,
                nano_cpus=policy.nano_cpus,
                pids_limit=policy.pids_limit,
                working_dir="/workspace",
                detach=True,
                # ── Explicit hardening ────────────────────────────────────────
                privileged=False,                  # never allow privileged mode
                cap_drop=["ALL"],                  # drop ALL Linux capabilities
                cap_add=[],                        # grant zero capabilities back
                user="nobody",                     # run as non-root unprivileged user
                read_only=True,                    # immutable root filesystem
                tmpfs={                            # /tmp still writable in-memory
                    "/tmp": "size=64m,mode=1777",
                },
                security_opt=["no-new-privileges:true"],  # prevent setuid escalation
            )

            # Wait for execution with policy timeout
            timeout_sec = policy.default_timeout_seconds
            try:
                wait_result = container.wait(timeout=timeout_sec)
                exit_code = wait_result.get("StatusCode", 0)
            except Exception as wait_err:
                logger.warning("Container execution timed out or errored: %s", wait_err)
                try:
                    container.kill()
                except Exception:
                    pass
                return ExtensionExecutionResponse(
                    request_id=request.request_id,
                    ok=False,
                    exit_code=124,
                    error=f"Execution timed out after {timeout_sec:.1f} seconds.",
                    stderr="Timeout exceeded in container sandbox.",
                    duration_ms=(time.time() - t0) * 1000.0,
                    security_event=True,
                )

            # Read result from response.json
            duration_ms = (time.time() - t0) * 1000.0
            if resp_file.exists():
                try:
                    with open(resp_file, "r", encoding="utf-8") as f:
                        resp_data = json.load(f)
                    resp = ExtensionExecutionResponse.from_dict(resp_data)
                    resp.duration_ms = duration_ms
                    return resp
                except Exception as read_err:
                    logs = ""
                    try:
                        logs = container.logs().decode(errors="replace")
                    except Exception:
                        pass
                    return ExtensionExecutionResponse(
                        request_id=request.request_id,
                        ok=False,
                        exit_code=exit_code,
                        error=f"Failed to parse worker response: {read_err}",
                        stderr=logs,
                        duration_ms=duration_ms,
                    )
            else:
                logs = ""
                try:
                    logs = container.logs().decode(errors="replace")
                except Exception:
                    pass
                return ExtensionExecutionResponse(
                    request_id=request.request_id,
                    ok=False,
                    exit_code=exit_code,
                    error="Worker exited without producing response.",
                    stderr=logs,
                    duration_ms=duration_ms,
                )

        except DockerException as de:
            logger.error("Docker container execution failure: %s", de)
            return ExtensionExecutionResponse(
                request_id=request.request_id,
                ok=False,
                exit_code=1,
                error=f"Container execution error: {de}",
                duration_ms=(time.time() - t0) * 1000.0,
            )
        finally:
            if container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            shutil.rmtree(runtime_io, ignore_errors=True)
            shutil.rmtree(runtime_bin, ignore_errors=True)
