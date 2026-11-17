"""
tests/integration/test_phase15_closure.py
Verification suite for Phase 1.5 closure:
  1. Clean-machine git commit without preconfigured global identity
  2. Sandbox transparency and fail-closed policy enforcement
  3. Destructive command blocking policy
  4. Real provider failure handling without fake completion
"""
import os
import sys
import tempfile
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest
from execution.agent_runtime import (
    AgentToolRegistry,
    BaseModelProvider,
    PolicyViolationException,
    ProviderUnavailableException,
    RealAgentRuntime,
    STATE_FAILED,
    SandboxBoundaryException,
    ToolRequest,
    WorkspaceBoundary,
)


def test_clean_machine_git_commit(tmp_path):
    """Verify git_commit succeeds on a machine with no user git identity configured."""
    boundary = WorkspaceBoundary(tmp_path)
    tools = AgentToolRegistry(boundary)

    # Create a test file
    test_file = tmp_path / "code.py"
    test_file.write_text("print('hello')\n", encoding="utf-8")

    # Run git commit with custom agent identity
    res = tools.git_commit("Initial commit by Aetherion")
    assert res.ok, f"git_commit failed: {res.stderr or res.error}"

    # Verify commit log contains Aetherion Agent
    log_res = boundary.run_command('git log -1 --format="%an <%ae>"')
    assert "Aetherion Agent" in log_res.stdout
    assert "agent@aetherion.ai" in log_res.stdout


def test_sandbox_transparency(tmp_path):
    """Verify inspect_environment accurately reflects sandbox status without falsifying containerization."""
    boundary = WorkspaceBoundary(tmp_path)
    tools = AgentToolRegistry(boundary)

    env_res = tools.inspect_environment()
    assert env_res.ok
    data = env_res.data
    assert "sandbox_mode" in data
    assert "container_isolated" in data

    if not boundary.is_docker_available:
        assert data["sandbox_mode"] == "host_process_degraded"
        assert data["container_isolated"] is False
        assert "host_process_degraded" in env_res.stdout


def test_fail_closed_docker_requirement(tmp_path, monkeypatch):
    """Verify AETHERION_REQUIRE_DOCKER=1 fails closed when Docker is unreachable."""
    monkeypatch.setenv("AETHERION_REQUIRE_DOCKER", "1")
    # If Docker is not running in this environment, it must raise SandboxBoundaryException
    try:
        import docker
        client = docker.from_env()
        client.ping()
        docker_up = True
    except Exception:
        docker_up = False

    if not docker_up:
        with pytest.raises(SandboxBoundaryException) as exc_info:
            WorkspaceBoundary(tmp_path)
        assert "Docker daemon is required by policy" in str(exc_info.value)


def test_destructive_command_blocking(tmp_path):
    """Verify destructive host commands are blocked by security policy."""
    boundary = WorkspaceBoundary(tmp_path)

    destructive_commands = [
        "rm -rf /",
        "rm -rf /etc",
        "format C:",
        "rd /s /q C:",
        "diskpart",
        "mkfs.ext4 /dev/sda",
        ":(){ :|:& };",
    ]

    for cmd in destructive_commands:
        with pytest.raises(PolicyViolationException):
            boundary.validate_command(cmd)


class FailingTestProvider(BaseModelProvider):
    """Mock provider simulating network or 401 unauthenticated failure."""
    def name(self) -> str:
        return "failing_mock"

    def is_available(self) -> bool:
        return True

    def chat(self, messages, tools):
        raise ProviderUnavailableException("Provider HTTP 401: Unauthorized client detected")


def test_provider_failure_does_not_fake_success(tmp_path):
    """Verify provider failure transitions task to FAILED with zero file fabrication."""
    runtime = RealAgentRuntime(
        workspace_dir=tmp_path,
        session_id="test-fail-prov",
        provider=FailingTestProvider(),
    )

    events = list(runtime.run_stream("Create critical_file.py"))
    event_types = [e.get("type") for e in events]

    assert "error" in event_types
    assert "done" in event_types

    done_event = [e for e in events if e.get("type") == "done"][0]
    assert done_event["status"] == "failed"
    assert runtime.current_state == STATE_FAILED

    # Zero files fabricated
    created_files = list(tmp_path.glob("*.py"))
    assert len(created_files) == 0, "Files were fabricated despite provider failure"
