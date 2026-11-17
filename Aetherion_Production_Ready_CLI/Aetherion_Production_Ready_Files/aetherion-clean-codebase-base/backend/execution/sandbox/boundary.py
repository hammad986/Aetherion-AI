"""
backend/execution/sandbox/boundary.py — Workspace boundary and sandbox enforcement.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

logger = logging.getLogger("aetherion.sandbox.boundary")


class AgentRuntimeError(Exception):
    """Base exception for the autonomous agent runtime."""


class SandboxBoundaryException(AgentRuntimeError):
    """Raised when an action violates workspace sandboxing or path boundaries."""


class PolicyViolationException(AgentRuntimeError):
    """Raised when a command violates security execution policy."""


class ExecutionCancelledException(AgentRuntimeError):
    """Raised when task execution is cancelled by the operator or system."""


from ..tools.schemas import ToolResult


def translate_posix_fragment(cmd: str) -> str:
    """Translate a single POSIX command without pipes or &&."""
    cmd = cmd.strip()
    if not cmd:
        return cmd

    # 1. Directory and path commands
    if cmd in ("ls", "ls -la", "ls -l", "ls -a", "ls -al"):
        return "dir /b"
    if cmd.startswith("ls "):
        target = cmd[3:].strip()
        if (target.startswith('"') and target.endswith('"')) or (target.startswith("'") and target.endswith("'")):
            target = target[1:-1]
        return f'dir /b "{target}"'
    if cmd == "pwd":
        return "cd"
    if cmd.startswith("which "):
        target = cmd[6:].strip().strip('"\'')
        return f'where "{target}"'

    # 2. rm -rf, rm -r, rm -f, rm
    m_rm = re.match(r'^rm\s+(?:-(?:[rf]{1,2}|r\s+-f|f\s+-r)\s+)?["\']?([^"\']+)["\']?$', cmd)
    if m_rm:
        target = m_rm.group(1).strip().replace("'", "''")
        return f'powershell -NoProfile -ExecutionPolicy Bypass -Command "Remove-Item -Recurse -Force -ErrorAction SilentlyContinue -LiteralPath \'{target}\'"'

    # 3. touch
    m_touch = re.match(r'^touch\s+["\']?([^"\']+)["\']?$', cmd)
    if m_touch:
        target = m_touch.group(1).strip().replace("'", "''")
        return f'powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path -LiteralPath \'{target}\') {{ (Get-Item -LiteralPath \'{target}\').LastWriteTime = Get-Date }} else {{ $null = New-Item -ItemType File -Force -Path \'{target}\' }}"'

    # 4. head
    m_head = re.match(r'^head(?:\s+-n\s*(\d+)|\s+-(\d+))?\s+["\']?([^"\']+)["\']?$', cmd)
    if m_head:
        count = m_head.group(1) or m_head.group(2) or "10"
        target = m_head.group(3).strip().replace("'", "''")
        return f'powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath \'{target}\' -Head {count}"'

    # 5. tail
    m_tail = re.match(r'^tail(?:\s+-n\s*(\d+)|\s+-(\d+))?\s+["\']?([^"\']+)["\']?$', cmd)
    if m_tail:
        count = m_tail.group(1) or m_tail.group(2) or "10"
        target = m_tail.group(3).strip().replace("'", "''")
        return f'powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath \'{target}\' -Tail {count}"'

    # 6. cat
    m_cat = re.match(r'^cat(?:\s+["\']?([^"\'|>]+)["\']?)?(\s*(?:>>|>).*)?$', cmd)
    if m_cat and m_cat.group(1):
        target = m_cat.group(1).strip()
        redirect = m_cat.group(2) or ""
        return f'type "{target}"{redirect}'

    # 7. grep
    m_grep_pipe = re.match(r'^grep(?:\s+(-i))?\s+["\']([^"\']+)["\']$', cmd)
    if m_grep_pipe:
        flag = "/i " if m_grep_pipe.group(1) else ""
        pat = m_grep_pipe.group(2)
        return f'findstr {flag}"{pat}"'

    m_grep_file = re.match(r'^grep(?:\s+(-i))?\s+["\']([^"\']+)["\']\s+["\']?([^"\'|>]+)["\']?(\s*(?:>>|>).*)?$', cmd)
    if m_grep_file:
        flag = "/i " if m_grep_file.group(1) else ""
        pat = m_grep_file.group(2)
        target = m_grep_file.group(3).strip()
        redirect = m_grep_file.group(4) or ""
        return f'findstr {flag}"{pat}" "{target}"{redirect}'

    return cmd


def translate_windows_command(command: str, proc_env: Dict[str, str]) -> str:
    """Translate compound POSIX shell commands (with export, &&, |, ;) to Windows equivalents."""
    cmd = command.strip()

    # 1. Extract leading export VAR=val && or export VAR=val;
    while True:
        m = re.match(r'^export\s+([a-zA-Z_][a-zA-Z0-9_]*)=["\']?(.*?)["\']?\s*(?:&&|;)\s*(.*)$', cmd)
        if m:
            var_name, var_val, remainder = m.group(1), m.group(2), m.group(3).strip()
            proc_env[var_name] = var_val
            cmd = remainder
        else:
            break

    # If the remaining command is just 'export VAR=val'
    m_single = re.match(r'^export\s+([a-zA-Z_][a-zA-Z0-9_]*)=["\']?(.*?)["\']?$', cmd)
    if m_single:
        proc_env[m_single.group(1)] = m_single.group(2)
        return "echo " + m_single.group(1) + "=" + m_single.group(2)

    cmd = cmd.rstrip("; \t\r\n")

    # Handle && chains
    if "&&" in cmd:
        parts = [p.strip() for p in cmd.split("&&")]
        translated_parts = [translate_windows_command(p, proc_env) for p in parts]
        return " && ".join(translated_parts)

    # Handle pipes |
    if "|" in cmd:
        parts = [p.strip() for p in cmd.split("|")]
        translated_parts = [translate_posix_fragment(p) for p in parts]
        return " | ".join(translated_parts)

    # Handle unquoted semicolons
    if ";" in cmd and not ("'" in cmd or '"' in cmd):
        parts = [p.strip() for p in cmd.split(";")]
        translated_parts = [translate_windows_command(p, proc_env) for p in parts]
        return " & ".join(translated_parts)

    return translate_posix_fragment(cmd)



class WorkspaceBoundary:
    """Enforces directory confinement, traversal guards, and command policy."""

    FORBIDDEN_COMMAND_PATTERNS = [
        r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+/(?:[a-zA-Z0-9_./-]*|\s|$)",  # rm -rf / and root subpaths
        r"\bmkfs\b",                                     # filesystem formatting
        r"\bdd\s+if=",                                   # raw disk overwrite
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;",        # fork bomb
        r"\bshutdown\b",
        r"\breboot\b",
        r"\binit\s+0\b",
        r"\bformat\s+[a-zA-Z]:",                         # Windows disk format
        r"\b(?:rd|rmdir)\s+/[sS]\s+/[qQ]\s+[a-zA-Z]:",  # Windows recursive root delete
        r"\bdiskpart\b",                                 # Windows disk partition utility
        r"\bRemove-Item\b.*-[rR]ecurse.*-[fF]orce\s+[a-zA-Z]:",  # PowerShell root wipe
    ]

    DANGEROUS_PATTERNS = [
        r"\brm\s+-[a-zA-Z]*r",
        r"\bchmod\s+-[a-zA-Z]*R",
        r"\bchown\b",
        r"\bkill\s+-9\b",
        r"\bpkill\b",
    ]

    def __init__(self, workspace_dir: str | Path):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.is_docker_available = self._check_docker()
        if os.environ.get("AETHERION_REQUIRE_DOCKER") == "1" and not self.is_docker_available:
            raise SandboxBoundaryException(
                "Strict sandboxing failed: Docker daemon is required by policy (AETHERION_REQUIRE_DOCKER=1) but unreachable."
            )

    @staticmethod
    def _check_docker() -> bool:
        try:
            import docker
            client = docker.from_env()
            client.ping()
            return True
        except Exception:
            return False

    @property
    def sandbox_mode(self) -> str:
        return "docker_container" if self.is_docker_available else "host_process_degraded"

    def resolve_safe_path(self, target: str | Path) -> Path:
        """Resolve path relative to workspace, strictly preventing directory traversal."""
        p = Path(target)
        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (self.workspace_dir / p).resolve()

        # Confinement check
        try:
            resolved.relative_to(self.workspace_dir)
        except ValueError:
            logger.warning("Sandbox traversal violation attempted: %s outside %s", resolved, self.workspace_dir)
            raise SandboxBoundaryException(f"Path '{target}' is outside the permitted workspace sandbox.")

        # Protected paths check
        parts = [part.lower() for part in resolved.parts]
        if "reference only" in parts or ".git" in parts:
            raise SandboxBoundaryException(f"Modification of protected path '{target}' is prohibited.")

        return resolved

    def validate_command(self, cmd: str) -> None:
        """Validate shell command against forbidden execution patterns."""
        cmd_clean = cmd.strip()
        for pat in self.FORBIDDEN_COMMAND_PATTERNS:
            if re.search(pat, cmd_clean, re.IGNORECASE):
                logger.error("Command blocked by security policy: %s", cmd_clean)
                raise PolicyViolationException(f"Command execution blocked by security policy: '{cmd_clean}'")

    def is_dangerous(self, cmd: str) -> bool:
        """Check if command requires approval / is high risk."""
        cmd_clean = cmd.strip()
        for pat in self.DANGEROUS_PATTERNS:
            if re.search(pat, cmd_clean, re.IGNORECASE):
                return True
        return False

    def run_command(
        self,
        command: str,
        timeout: float = 60.0,
        cancel_event: Optional[threading.Event] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ToolResult:
        """Execute command strictly inside workspace root with timeout and process cleanup."""
        self.validate_command(command)
        t0 = time.time()

        proc_env = os.environ.copy()
        proc_env["WORKSPACE_DIR"] = str(self.workspace_dir)
        proc_env["PYTHONUNBUFFERED"] = "1"
        ws_str = str(self.workspace_dir)
        proc_env["PYTHONPATH"] = ws_str + (os.pathsep + proc_env["PYTHONPATH"] if "PYTHONPATH" in proc_env else "")
        if env:
            proc_env.update(env)

        shell_cmd = command
        if sys.platform == "win32" and isinstance(shell_cmd, str):
            shell_cmd = translate_windows_command(shell_cmd, proc_env)
        proc = None
        try:
            proc = subprocess.Popen(
                shell_cmd,
                shell=True,
                cwd=str(self.workspace_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=proc_env,
            )

            # ---------------------------------------------------------------------------
            # Bounded pipe draining: two daemon threads read stdout/stderr concurrently
            # in 64 KB chunks.  Each stream is capped at MAX_CAPTURE_BYTES_PER_STREAM.
            # After the cap is reached the thread continues reading and discarding so the
            # OS pipe never fills up and deadlocks the child process.
            # Head/tail retention: first HEAD_FRACTION of the budget is kept as-is;
            # once that is full the tail window slides (ring-buffer style) until EOF.
            # ---------------------------------------------------------------------------
            _MAX_CHUNK = 65536                               # read granularity
            _MAX_CAPTURE_BYTES_PER_STREAM = 5 * 1024 * 1024  # 5 MB per stream (10 MB total)
            _HEAD_FRACTION = 0.4                             # 40 % head / 60 % tail

            class _BoundedReader:
                """Drains a single pipe stream into a bounded head+tail buffer."""

                def __init__(self) -> None:
                    self._head: list[bytes] = []
                    self._tail: list[bytes] = []
                    self._head_bytes = 0
                    self._tail_bytes = 0
                    self._discarded_bytes = 0
                    self._done = False
                    self._exc: Optional[Exception] = None
                    self._head_cap = int(_MAX_CAPTURE_BYTES_PER_STREAM * _HEAD_FRACTION)
                    self._tail_cap = _MAX_CAPTURE_BYTES_PER_STREAM - self._head_cap

                def drain(self, stream) -> None:
                    try:
                        while True:
                            chunk = stream.read(_MAX_CHUNK)
                            if not chunk:
                                break
                            raw = chunk.encode("utf-8", errors="replace") if isinstance(chunk, str) else chunk
                            if self._head_bytes < self._head_cap:
                                # Still filling head
                                room = self._head_cap - self._head_bytes
                                take = raw[:room]
                                self._head.append(take)
                                self._head_bytes += len(take)
                                remainder = raw[room:]
                            else:
                                remainder = raw
                            if remainder:
                                if self._tail_bytes + len(remainder) <= self._tail_cap:
                                    self._tail.append(remainder)
                                    self._tail_bytes += len(remainder)
                                else:
                                    # Slide tail window: drop oldest to make room
                                    needed = len(remainder)
                                    freed = 0
                                    while self._tail and freed < needed - (self._tail_cap - self._tail_bytes):
                                        dropped = self._tail.pop(0)
                                        freed += len(dropped)
                                        self._discarded_bytes += len(dropped)
                                    self._tail_bytes = max(0, self._tail_bytes - freed)
                                    take_tail = remainder[max(0, len(remainder) - self._tail_cap):]
                                    if len(take_tail) < len(remainder):
                                        self._discarded_bytes += len(remainder) - len(take_tail)
                                    self._tail.append(take_tail)
                                    self._tail_bytes += len(take_tail)
                    except Exception as ex:
                        self._exc = ex
                    finally:
                        self._done = True

                def result(self) -> tuple[str, int, bool]:
                    """Return (text, discarded_bytes, was_truncated)."""
                    head_text = b"".join(self._head).decode("utf-8", errors="replace")
                    tail_text = b"".join(self._tail).decode("utf-8", errors="replace")
                    was_truncated = self._discarded_bytes > 0 or (
                        self._head_bytes >= self._head_cap and self._tail_bytes > 0
                    )
                    if was_truncated:
                        marker = (
                            f"\n... [{self._discarded_bytes:,} bytes omitted; output exceeded "
                            f"{_MAX_CAPTURE_BYTES_PER_STREAM // (1024 * 1024)} MB capture limit] ...\n"
                        )
                        text = head_text + marker + tail_text
                    else:
                        text = head_text + tail_text
                    return text, self._discarded_bytes, was_truncated

            stdout_reader = _BoundedReader()
            stderr_reader = _BoundedReader()

            def _drain_stdout() -> None:
                stdout_reader.drain(proc.stdout)

            def _drain_stderr() -> None:
                stderr_reader.drain(proc.stderr)

            t_out = threading.Thread(target=_drain_stdout, daemon=True)
            t_err = threading.Thread(target=_drain_stderr, daemon=True)
            t_out.start()
            t_err.start()

            deadline = time.time() + timeout
            while t_out.is_alive() or t_err.is_alive():
                if cancel_event and cancel_event.is_set():
                    self._terminate_proc(proc)
                    t_out.join(timeout=1.0)
                    t_err.join(timeout=1.0)
                    raise ExecutionCancelledException(f"Command '{command}' cancelled by operator.")

                now = time.time()
                if now >= deadline:
                    self._terminate_proc(proc)
                    t_out.join(timeout=1.0)
                    t_err.join(timeout=1.0)
                    stdout_text, _, _ = stdout_reader.result()
                    duration_ms = (now - t0) * 1000.0
                    return ToolResult(
                        ok=False,
                        stdout=stdout_text,
                        stderr=f"Execution timed out after {timeout} seconds.",
                        exit_code=-1,
                        duration_ms=duration_ms,
                        error=f"Command timed out after {timeout}s",
                    )
                t_out.join(timeout=0.05)
                if t_out.is_alive():
                    pass  # still draining; re-check stderr via loop
                t_err.join(timeout=0.0)  # non-blocking check

            # Wait for the process to be fully reaped now that both pipes are closed.
            # proc.communicate() normally does this; reader threads do not.
            try:
                ret = proc.wait(timeout=5.0)
            except Exception:
                ret = proc.returncode
            if ret is None:
                ret = 0  # process was terminated earlier (cancel/timeout path handled above)
            duration_ms = (time.time() - t0) * 1000.0
            stdout_text, stdout_disc, stdout_trunc = stdout_reader.result()
            stderr_text, stderr_disc, stderr_trunc = stderr_reader.result()
            return ToolResult(
                ok=(ret == 0),
                stdout=stdout_text,
                stderr=stderr_text,
                exit_code=ret,
                duration_ms=duration_ms,
                error=None if ret == 0 else f"Process exited with non-zero code {ret}",
            )

        except ExecutionCancelledException:
            raise
        except Exception as exc:
            if proc:
                self._terminate_proc(proc)
            duration_ms = (time.time() - t0) * 1000.0
            return ToolResult(
                ok=False,
                stdout="",
                stderr=str(exc),
                exit_code=1,
                duration_ms=duration_ms,
                error=str(exc),
            )

    def _terminate_proc(self, proc: subprocess.Popen) -> None:
        """Forcefully terminate process and child processes."""
        try:
            if sys.platform == "win32":
                subprocess.run(
                    f"taskkill /F /T /PID {proc.pid}",
                    shell=True,
                    capture_output=True,
                    check=False,
                )
            else:
                proc.kill()
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
