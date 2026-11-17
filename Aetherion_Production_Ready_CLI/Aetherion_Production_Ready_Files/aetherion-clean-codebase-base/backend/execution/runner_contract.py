"""
execution/runner_contract.py — Phase 19 Workstream C: isolated execution runner.

Single runner interface for Aetherion code execution. Production MUST use an
approved isolation backend (gVisor, Kata, microVM, or hardened container runner).
This module FAILS CLOSED: if no approved backend is configured or reachable, the
factory raises RunnerUnavailableError — there is no automatic fallback to the
host, to Docker-socket containers, or to privileged execution.

Adapter implementors: subclass RunnerAdapter. Never embed host privileges in the
web/worker; execution is delegated to this boundary only.
"""
from __future__ import annotations

import abc
import dataclasses
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger("aetherion.execution.runner")


class RunnerError(Exception):
    """Base error for the runner boundary."""


class RunnerUnavailableError(RunnerError):
    """No approved isolation backend is configured/reachable — fail closed."""


class RunnerDeniedError(RunnerError):
    """Policy denied the job (path/command/network/resource/size/unauthorized backend)."""


@dataclasses.dataclass(frozen=True)
class ResourcePolicy:
    max_cpu: float = 1.0
    max_memory_mb: int = 512
    max_time_sec: int = 60
    max_processes: int = 64
    network_enabled: bool = False          # default-deny egress
    allowed_egress: tuple = ()             # explicit allowlist (host:port)
    read_only_fs: bool = True
    writable_paths: tuple = ("/workspace", "/tmp")


@dataclasses.dataclass(frozen=True)
class JobSpec:
    job_id: str
    tenant_id: str
    workspace_id: str
    language: str                     # python | node | shell(banned) | ...
    command: str
    cwd: str = "/workspace"
    env: tuple = ()                   # (NAME,) keys only — values injected by runner secret store
    resource: ResourcePolicy = dataclasses.field(default_factory=ResourcePolicy)


@dataclasses.dataclass
class JobResult:
    job_id: str
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    failure_class: Optional[str] = None     # timeout|cancel|sandbox|policy|internal
    duration_ms: int = 0
    artifacts: tuple = ()


# ── redaction ──────────────────────────────────────────────────────────────────
_REDACT_PATTERNS = (
    re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{6,}"),
    re.compile(r"(?i)((?:api[_-]?key|secret|token|password|authorization|jwt))\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{6,}"),
)

def redact(text: Optional[str]) -> str:
    """Redact credential-looking fragments from logs/results before persistence."""
    if not text:
        return text or ""
    out = text
    for pat in _REDACT_PATTERNS:
        out = pat.sub(lambda m: m.group(1) + "<redacted>", out)
    return out


# ── Security Boundary Constants ────────────────────────────────────────────────
_FORBIDDEN_MOUNTS = frozenset({
    "/",
    "/var/run/docker.sock",
    "/run/docker.sock",
    "//./pipe/docker_engine",
    "/proc",
    "/sys",
    "/dev",
    "/root",
    "/home",
    "/etc",
    "/bin",
    "/sbin",
    "/usr",
    "/lib",
    "/lib64",
})

APPROVED_PRODUCTION_BACKENDS = frozenset({
    "hardened_container",
    "docker",
    "gvisor",
    "runsc",
    "kata",
    "firecracker",
    "microvm",
})


def _is_production_or_staging() -> bool:
    try:
        from core.environment import is_fail_closed_environment
        return is_fail_closed_environment()
    except ImportError:
        env = (
            os.getenv("ENVIRONMENT")
            or os.getenv("ENV")
            or os.getenv("APP_ENV")
            or os.getenv("FLASK_ENV")
            or ""
        ).strip().lower()
        return env in ("production", "staging", "prod")


def check_mount_safety(paths: Sequence[Any]) -> None:
    """Verify that no forbidden host sockets or root filesystem mounts are requested.

    Enforces platform-agnostic canonicalization:
    - Normalizes separators and Windows device/pipe prefixes.
    - Strips extended-length prefixes (//?/).
    - Evaluates both lexical normalized path and filesystem realpath (symlinks/junctions).
    - Resolves relative paths against root to prevent escaping traversal.
    - Normalizes case to defend against case-insensitive host bypasses.
    - Preserves access to benign non-colliding paths (e.g. /workspace, /tmp, /etcetera).
    """
    for p in paths:
        raw = str(p).strip()
        if not raw:
            continue

        # Canonicalize slashes and handle special device prefixes
        if raw.startswith(("\\\\.\\", "//./", "\\\\?\\", "//?/")):
            is_device = (".\\" in raw[:4] or "./" in raw[:4])
            prefix = "//./" if is_device else "//?/"
            cleaned = prefix + re.sub(r"[\\/]+", "/", raw[4:])
        else:
            cleaned = re.sub(r"[\\/]+", "/", raw)

        # Strip extended length prefix if present for lexical resolution
        to_norm = cleaned[4:] if cleaned.startswith("//?/") else cleaned
        norm = os.path.normpath(to_norm).replace("\\", "/")

        try:
            real = os.path.normpath(os.path.realpath(raw)).replace("\\", "/")
        except Exception:
            real = norm

        rooted = os.path.normpath("/" + norm.lstrip("/")).replace("\\", "/")
        candidates = {cleaned, norm, real, rooted}

        for candidate in candidates:
            c_lower = candidate.lower()
            drive, rest = os.path.splitdrive(candidate)
            rest_lower = rest.lower() if rest else ""

            # Explicit check for bare root and Windows drive roots (e.g. "/", "C:/", "C:\", "C:")
            if c_lower in ("/", "\\") or (drive and rest in ("", "/", "\\")):
                raise RunnerDeniedError(
                    f"Mount of privileged/sensitive path '{p}' is strictly prohibited."
                )

            for forbidden in _FORBIDDEN_MOUNTS:
                f_lower = forbidden.lower()
                if f_lower == "/":
                    if c_lower in ("/", "\\") or rest_lower in ("/", "\\"):
                        raise RunnerDeniedError(
                            f"Mount of privileged/sensitive path '{p}' is strictly prohibited."
                        )
                else:
                    # Direct match or subpath match on candidate
                    if c_lower == f_lower or c_lower.startswith(f_lower + "/"):
                        raise RunnerDeniedError(
                            f"Mount of privileged/sensitive path '{p}' is strictly prohibited."
                        )
                    # Direct match or subpath match on rest (after Windows drive split)
                    if rest_lower and (rest_lower == f_lower or rest_lower.startswith(f_lower + "/")):
                        raise RunnerDeniedError(
                            f"Mount of privileged/sensitive path '{p}' is strictly prohibited."
                        )


class RunnerAdapter(abc.ABC):
    """Implement for gVisor/Kata/microVM/hardened-container backends."""

    backend_name: str = "abstract"
    isolation_class: str = "unknown"
    network_policy: str = "default_deny"

    @abc.abstractmethod
    def is_available(self) -> bool:
        """True only when the isolation backend is reachable and approved."""

    @abc.abstractmethod
    def submit(self, spec: JobSpec) -> JobResult:
        """Run one job under the resource/network/fs policy. Fail closed on any violation."""

    def capability_metadata(self) -> Dict[str, Any]:
        """Return standardized capability and health metadata."""
        return {
            "runner_id": f"runner-{self.backend_name}",
            "backend_name": self.backend_name,
            "isolation_class": getattr(self, "isolation_class", "unknown"),
            "network_policy": getattr(self, "network_policy", "deny_egress"),
            "available": self.is_available(),
            "resource_limits": {
                "max_cpu": 1.0,
                "max_memory_mb": 512,
                "max_time_sec": 60,
                "max_processes": 64,
                "read_only_fs": True,
            },
        }


class _NoneBackend(RunnerAdapter):
    """Placeholder: proves the factory fails closed when nothing is configured."""

    backend_name = "none"
    isolation_class = "none"
    network_policy = "none"

    def is_available(self) -> bool:
        return False

    def submit(self, spec: JobSpec) -> JobResult:
        raise RunnerUnavailableError("no isolation backend configured — failing closed")


class HardenedContainerRunner(RunnerAdapter):
    """Hardened ephemeral container runner backend.

    Guarantees:
      - Dropped capabilities: ALL
      - Non-root user: UID/GID 10001:10001
      - Read-only root filesystem
      - Tmpfs /tmp with noexec, nosuid
      - No host docker.sock or sensitive mounts
      - Default deny egress (--network=none)
      - Explicit resource caps (CPU, RAM, max PIDs)
      - Support for gVisor (runsc) and Kata Containers isolation runtimes
    """

    backend_name: str = "hardened_container"
    isolation_class: str = "container_hardened"
    network_policy: str = "deny_egress"

    def __init__(
        self,
        backend_name: str = "hardened_container",
        runtime: Optional[str] = None,
        image: Optional[str] = None,
    ):
        self.backend_name = backend_name
        self.runtime = runtime
        self.image = image or os.environ.get("SANDBOX_IMAGE", "python:3.11-slim")
        if runtime in ("runsc", "gvisor"):
            self.isolation_class = "gvisor"
        elif runtime in ("kata", "kata-qemu"):
            self.isolation_class = "kata"
        else:
            self.isolation_class = "container_hardened"

    def is_available(self) -> bool:
        try:
            import docker
            client = docker.from_env(timeout=2)
            client.ping()
            if self.runtime:
                info = client.info()
                runtimes = info.get("Runtimes", {})
                if self.runtime not in runtimes:
                    return False
            return True
        except Exception:
            return False

    def submit(self, spec: JobSpec) -> JobResult:
        if not self.is_available():
            raise RunnerUnavailableError(
                f"Container runner backend '{self.backend_name}' (runtime={self.runtime}) is unavailable"
            )
        check_mount_safety(spec.resource.writable_paths)

        import docker
        client = docker.from_env(timeout=5)
        t0 = time.time()

        container_kwargs: Dict[str, Any] = {
            "image": self.image,
            "user": "10001:10001",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "network_mode": "bridge" if spec.resource.network_enabled else "none",
            "pids_limit": spec.resource.max_processes or 64,
            "mem_limit": f"{spec.resource.max_memory_mb}m",
            "nano_cpus": int(spec.resource.max_cpu * 1e9),
            "read_only": spec.resource.read_only_fs,
            "tmpfs": {
                "/tmp": "size=64m,noexec,nosuid",
                "/workspace": "size=256m,nosuid",
            },
            "working_dir": spec.cwd or "/workspace",
            "detach": False,
            "remove": True,
        }
        if self.runtime:
            container_kwargs["runtime"] = self.runtime

        ext = {"python": "py", "javascript": "js", "shell": "sh", "bash": "sh"}.get(
            spec.language, "py"
        )
        runner_cmd = {
            "python": f"python -c {spec.command!r}",
            "javascript": f"node -e {spec.command!r}",
            "bash": f"bash -c {spec.command!r}",
            "shell": f"sh -c {spec.command!r}",
        }.get(spec.language, f"sh -c {spec.command!r}")

        container_kwargs["command"] = ["sh", "-c", runner_cmd]

        try:
            raw_output = client.containers.run(**container_kwargs)
            duration_ms = int((time.time() - t0) * 1000)
            decoded = raw_output.decode("utf-8", errors="replace") if isinstance(raw_output, bytes) else str(raw_output)
            return JobResult(
                job_id=spec.job_id,
                ok=True,
                exit_code=0,
                stdout=redact(decoded),
                stderr="",
                duration_ms=duration_ms,
            )
        except docker.errors.ContainerError as exc:
            duration_ms = int((time.time() - t0) * 1000)
            err_output = exc.stderr or exc.args[0] if hasattr(exc, "stderr") else str(exc)
            decoded_err = err_output.decode("utf-8", errors="replace") if isinstance(err_output, bytes) else str(err_output)
            return JobResult(
                job_id=spec.job_id,
                ok=False,
                exit_code=exc.exit_status,
                stdout="",
                stderr=redact(decoded_err),
                failure_class="sandbox",
                duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = int((time.time() - t0) * 1000)
            return JobResult(
                job_id=spec.job_id,
                ok=False,
                exit_code=-1,
                stdout="",
                stderr=redact(str(exc)),
                failure_class="internal",
                duration_ms=duration_ms,
            )


class FirecrackerRunner(RunnerAdapter):
    """Firecracker / microVM runner backend."""

    backend_name: str = "firecracker"
    isolation_class: str = "microvm_firecracker"
    network_policy: str = "deny_egress"

    def __init__(self, backend_name: str = "firecracker"):
        self.backend_name = backend_name

    def is_available(self) -> bool:
        has_bin = bool(
            shutil.which("firectl")
            or shutil.which("ignite")
            or shutil.which("firecracker")
        )
        if not has_bin:
            return False
        if sys.platform.startswith("linux"):
            return os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.R_OK | os.W_OK)
        return False

    def submit(self, spec: JobSpec) -> JobResult:
        if not self.is_available():
            raise RunnerUnavailableError(
                f"Firecracker microVM runner '{self.backend_name}' is unavailable"
            )
        check_mount_safety(spec.resource.writable_paths)
        raise RunnerUnavailableError("Firecracker microVM runtime execution failed closed")


class LocalSubprocessRunner(RunnerAdapter):
    """Local subprocess runner restricted to development / test harnesses.

    STRICTLY FORBIDDEN in production and staging environments.
    Requires TESTING=1 or ALLOW_UNSAFE_LOCAL_RUNNER=true in dev mode.
    """

    backend_name: str = "local"
    isolation_class: str = "unisolated_host_subprocess"
    network_policy: str = "host_network"

    def __init__(self, backend_name: str = "local"):
        self.backend_name = backend_name

    def is_available(self) -> bool:
        if _is_production_or_staging():
            return False
        return (
            os.getenv("TESTING", "").strip() in ("1", "true", "yes")
            or os.getenv("FLASK_TESTING", "").strip() in ("1", "true", "yes")
            or os.getenv("ALLOW_UNSAFE_LOCAL_RUNNER", "").strip().lower() in ("1", "true", "yes")
            or "pytest" in sys.modules
        )

    def submit(self, spec: JobSpec) -> JobResult:
        if _is_production_or_staging():
            raise RunnerDeniedError(
                "FAIL CLOSED: LocalSubprocessRunner is strictly forbidden in production/staging environments."
            )
        if not self.is_available():
            raise RunnerUnavailableError(
                "LocalSubprocessRunner requires TESTING=1 or ALLOW_UNSAFE_LOCAL_RUNNER=true in development."
            )

        logger.warning(
            "[SECURITY WARNING] UNSAFE_LOCAL_RUNNER is executing on the host. This is prohibited in production."
        )

        t0 = time.time()
        tmpdir = tempfile.mkdtemp(prefix="local_runner_")
        try:
            ext = {"python": "py", "bash": "sh", "javascript": "js", "shell": "sh"}.get(
                spec.language, "py"
            )
            script_path = os.path.join(tmpdir, f"script.{ext}")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(spec.command)

            interp = {
                "python": sys.executable,
                "bash": "bash",
                "shell": "sh",
                "javascript": "node",
            }.get(spec.language, sys.executable)

            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": "",
                "HOME": tmpdir,
                "TMPDIR": tmpdir,
            }

            proc = subprocess.Popen(
                [interp, script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=tmpdir,
                env=env,
                text=True,
            )
            try:
                stdout, stderr = proc.communicate(timeout=spec.resource.max_time_sec)
                dur_ms = int((time.time() - t0) * 1000)
                return JobResult(
                    job_id=spec.job_id,
                    ok=(proc.returncode == 0),
                    exit_code=proc.returncode,
                    stdout=redact(stdout),
                    stderr=redact(stderr),
                    duration_ms=dur_ms,
                )
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                dur_ms = int((time.time() - t0) * 1000)
                return JobResult(
                    job_id=spec.job_id,
                    ok=False,
                    exit_code=-1,
                    stdout="",
                    stderr=f"Timed out after {spec.resource.max_time_sec}s",
                    failure_class="timeout",
                    duration_ms=dur_ms,
                )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── Registry & Factory ────────────────────────────────────────────────────────

def _create_default_backends() -> Dict[str, RunnerAdapter]:
    return {
        "none": _NoneBackend(),
        "hardened_container": HardenedContainerRunner(backend_name="hardened_container"),
        "docker": HardenedContainerRunner(backend_name="docker"),
        "gvisor": HardenedContainerRunner(runtime="runsc", backend_name="gvisor"),
        "runsc": HardenedContainerRunner(runtime="runsc", backend_name="runsc"),
        "kata": HardenedContainerRunner(runtime="kata-qemu", backend_name="kata"),
        "firecracker": FirecrackerRunner(backend_name="firecracker"),
        "microvm": FirecrackerRunner(backend_name="microvm"),
        "local": LocalSubprocessRunner(backend_name="local"),
        "mock": LocalSubprocessRunner(backend_name="mock"),
    }


_BACKENDS: Dict[str, RunnerAdapter] = _create_default_backends()


def reset_backends() -> None:
    """Reset the registry to default state (useful in test suites)."""
    global _BACKENDS
    _BACKENDS = _create_default_backends()


def register_backend(adapter: RunnerAdapter) -> None:
    """Register an approved runner adapter into the factory."""
    _BACKENDS[adapter.backend_name] = adapter


def get_runner(backend_name: Optional[str] = None) -> RunnerAdapter:
    """Fail-closed runner selector.

    In production/staging:
      - Only approved backends (gVisor, Kata, Firecracker, Hardened Container) can be selected.
      - If no approved backend is reachable, raises RunnerUnavailableError.
      - Local runner / mock / host subprocess execution is strictly denied.
    In dev/test:
      - Prefers hardened container if available.
      - Allows LocalSubprocessRunner only when TESTING=1 or ALLOW_UNSAFE_LOCAL_RUNNER=true.
    """
    name = (
        backend_name
        or os.environ.get("AETHERION_RUNNER_BACKEND")
        or ""
    ).strip().lower()
    is_prod = _is_production_or_staging()

    if is_prod:
        if not name or name in ("", "none"):
            # Try default hardened_container if available, else fail closed
            candidate = _BACKENDS.get("hardened_container")
            if candidate and candidate.is_available():
                return candidate
            raise RunnerUnavailableError(
                "AETHERION_RUNNER_BACKEND unset or unavailable in production/staging. "
                "Refusing to execute without an approved isolated runner (gVisor/Kata/microVM/hardened_container). "
                "No host fallback."
            )
        if name not in APPROVED_PRODUCTION_BACKENDS:
            raise RunnerDeniedError(
                f"Runner backend '{name}' is not in the approved production backends list "
                f"({sorted(list(APPROVED_PRODUCTION_BACKENDS))}). Failing closed."
            )
    else:
        if not name or name in ("", "none"):
            candidate = _BACKENDS.get("hardened_container")
            if candidate and candidate.is_available():
                return candidate
            local = _BACKENDS.get("local")
            if local and local.is_available():
                return local
            raise RunnerUnavailableError(
                "No runner backend available. Set AETHERION_RUNNER_BACKEND or enable TESTING=1 / ALLOW_UNSAFE_LOCAL_RUNNER=true in dev."
            )

    backend = _BACKENDS.get(name)
    if backend is None or not backend.is_available():
        raise RunnerUnavailableError(
            f"Approved runner backend '{name}' is not registered or unavailable — failing closed"
        )
    return backend


def get_runner_status() -> Dict[str, Any]:
    """Returns comprehensive runner capability status for health endpoints and UI."""
    is_prod = _is_production_or_staging()
    active_name = "none"
    available = False
    active_meta = {
        "runner_id": "runner-none",
        "backend_name": "none",
        "isolation_class": "none",
        "network_policy": "none",
        "available": False,
    }

    try:
        active_runner = get_runner()
        active_meta = active_runner.capability_metadata()
        active_name = active_runner.backend_name
        available = active_runner.is_available()
    except Exception as e:
        active_meta["error"] = str(e)

    matrix = {
        name: adapter.capability_metadata()
        for name, adapter in _BACKENDS.items()
    }

    return {
        "active_backend": active_name,
        "available": available,
        "isolated": active_meta.get("isolation_class") in ("container_hardened", "gvisor", "kata", "microvm_firecracker"),
        "degraded": not available or active_meta.get("isolation_class") == "unisolated_host_subprocess",
        "fail_closed_mode": is_prod,
        "active_runner": active_meta,
        "backends": matrix,
    }

