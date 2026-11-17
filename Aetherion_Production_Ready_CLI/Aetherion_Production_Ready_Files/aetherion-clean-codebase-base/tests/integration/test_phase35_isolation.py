"""
tests/integration/test_phase35_isolation.py — Phase 3.5 Isolated Extension Runtime Integration Suite.

28 comprehensive verification scenarios testing:
  1. Container Isolated Filesystem: Host paths outside /workspace and /plugin cannot be reached.
  2. Container Scrubbed Environment: Zero host environment variables or secrets leaked to container.
  3. Container sys.modules Isolation: Host Python runtime and modules are completely isolated.
  4. Container Network Mode None: Untrusted plugin without NETWORK_ACCESS cannot open sockets.
  5. Container Network Mode Allowed: Plugin with NETWORK_ACCESS receives network_mode=bridge.
  6. Container Read-Only Plugin Mount: Untrusted plugin cannot modify its own installation directory.
  7. Container Workspace Confinement Read-Only: Plugin without FILESYSTEM_WRITE cannot write to /workspace.
  8. Container Workspace Write Allowed: Plugin with FILESYSTEM_WRITE successfully creates files in /workspace.
  9. Container Memory Limit: mem_limit='256m' strictly configured in policy.
  10. Container CPU & PIDs Limits: nano_cpus and pids_limit=64 configured against resource exhaustion.
  11. Container Timeout Enforcement: Infinite loop plugin terminated by execution timeout with exit code 124.
  12. Container Ephemeral Cleanup: Docker container cleaned up and removed after tool completion.
  13. Container Error Capture: Unhandled Python exception captured into structured stderr and error fields.
  14. Trust Model: Untrusted plugin defaults to container isolation.
  15. Fail-Closed Fallback: Untrusted plugin strictly FAILS CLOSED when Docker is unavailable.
  16. Trusted Local without Dev Flag: FAILS CLOSED when Docker is unavailable and dev flag is not set.
  17. Trusted Local with Dev Flag: Executes via SubprocessWorker when AETHERION_TRUSTED_PLUGIN_MODE=1.
  18. Subprocess Worker Scrubbed Env: Strips credentials, API keys, and secrets from subprocess.
  19. System Trust Level: SYSTEM plugins execute safely.
  20. Invalid Trust Level: Invalid trust level rejected by manifest validation and runner.
  21. Protocol Request Serialization: Round-trip JSON serialization of ExtensionExecutionRequest.
  22. Protocol Response Serialization: Round-trip JSON serialization of ExtensionExecutionResponse.
  23. Worker CLI Contract: Standalone worker executes via CLI --input and --output flags.
  24. Worker Output Handling: Worker properly serializes dict, primitives, and string results.
  25. Multi-Tenant Workspace Isolation: Tenant A's tool execution cannot access Tenant B's workspace.
  26. Malicious Subprocess Fork Contained: OS-level subprocesses confined to container boundaries.
  27. Malicious DB Tampering Blocked: Host SQLite database files unreachable from container.
  28. Static Health Check Isolation: Malicious plugin verified without in-process execution.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from execution.extensions import (
    ALL_PERMISSIONS,
    ContainerWorker,
    ExtensionExecutionRequest,
    ExtensionExecutionResponse,
    ExtensionManager,
    ExtensionRunner,
    ExtensionSecurityException,
    ExtensionSecurityPolicy,
    ExtensionStatus,
    ExtensionStore,
    ExtensionTrustLevel,
    HealthStatus,
    PermissionEnum,
    PluginManifest,
    PluginToolBridge,
    SubprocessWorker,
    is_docker_available,
)
from execution.sandbox.boundary import WorkspaceBoundary
from execution.tools.schemas import ToolRequest, ToolResult


@pytest.fixture
def clean_manager(tmp_path):
    """Provides an isolated ExtensionManager with temporary database."""
    db_file = tmp_path / "phase35_extensions.db"
    store = ExtensionStore(db_path=db_file)
    manager = ExtensionManager(store=store, base_install_dir=tmp_path / "data")
    return manager


@pytest.fixture
def sample_plugin_helper(tmp_path):
    """Helper factory to create and install plugins with specified code and permissions."""
    def _create(
        plugin_id: str,
        code: str,
        permissions: list[str] | None = None,
        trust_level: str = ExtensionTrustLevel.UNTRUSTED.value,
        func_name: str = "run_tool",
    ) -> Path:
        p_dir = tmp_path / f"plugin_{plugin_id}"
        p_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "id": plugin_id,
            "name": f"Plugin {plugin_id}",
            "version": "1.0.0",
            "entrypoint": f"main.py:{func_name}",
            "trust_level": trust_level,
            "required_permissions": permissions or [],
            "provided_tools": [{
                "name": func_name,
                "description": f"Tool {func_name}",
                "parameters": {"type": "object", "properties": {}},
                "required_permissions": permissions or [],
            }],
        }
        (p_dir / "plugin.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (p_dir / "main.py").write_text(code, encoding="utf-8")
        return p_dir

    return _create


# ── 1. Container Isolated Filesystem ──────────────────────────────────────────
def test_01_container_isolated_filesystem(clean_manager, sample_plugin_helper, tmp_path):
    """Untrusted plugin attempting to read host root/system paths cannot locate host files."""
    code = """
import os

def run_tool(**kwargs):
    # Try accessing Windows host system files or root
    win_ini = os.path.exists("C:\\\\Windows\\\\System32")
    user_home = os.path.exists("C:\\\\Users")
    return {
        "ok": True,
        "host_win_visible": win_ini,
        "host_user_visible": user_home,
        "linux_fs": os.path.exists("/bin/sh")
    }
"""
    p_dir = sample_plugin_helper("fs-probe", code, permissions=["FILESYSTEM_READ"])
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "workspace_alice"
    ws.mkdir()
    boundary = WorkspaceBoundary(ws)
    bridge = PluginToolBridge(clean_manager, boundary)

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t1")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    data = res.data if isinstance(res.data, dict) else {}
    assert data.get("host_win_visible") is False
    assert data.get("host_user_visible") is False
    assert data.get("linux_fs") is True


# ── 2. Container Scrubbed Environment ─────────────────────────────────────────
def test_02_container_scrubbed_environment(clean_manager, sample_plugin_helper, tmp_path):
    """Untrusted plugin receives zero host environment variables or API secrets."""
    code = """
import os

def run_tool(**kwargs):
    # Probe environment
    keys = list(os.environ.keys())
    has_host_user = "USERNAME" in os.environ or "USER" in os.environ and os.environ.get("USER") != "root"
    has_host_path = "USERPROFILE" in os.environ or "APPDATA" in os.environ
    return {
        "ok": True,
        "env_keys": keys,
        "has_host_user": has_host_user,
        "has_host_path": has_host_path,
        "pythonpath": os.environ.get("PYTHONPATH"),
    }
"""
    # Seed host environment with dummy sensitive keys
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-secret123", "AETHERION_JWT_SECRET": "jwt-sec"}):
        p_dir = sample_plugin_helper("env-probe", code)
        clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

        ws = tmp_path / "workspace_alice"
        ws.mkdir()
        bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

        req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t2")
        res = bridge.execute_plugin_tool(req, owner_id="alice")

        assert res.ok is True
        data = res.data if isinstance(res.data, dict) else {}
        keys = data.get("env_keys", [])
        assert "OPENAI_API_KEY" not in keys
        assert "AETHERION_JWT_SECRET" not in keys
        assert data.get("has_host_path") is False
        assert data.get("pythonpath") == "/plugin"


# ── 3. Container sys.modules Isolation ────────────────────────────────────────
def test_03_container_sys_modules_isolation(clean_manager, sample_plugin_helper, tmp_path):
    """Untrusted plugin running in container cannot inspect or access host Python modules."""
    code = """
import sys

def run_tool(**kwargs):
    mods = list(sys.modules.keys())
    has_aetherion = any("aetherion" in m or "execution" in m for m in mods)
    return {
        "ok": True,
        "has_host_runtime_modules": has_aetherion,
    }
"""
    p_dir = sample_plugin_helper("mod-probe", code)
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "workspace_alice"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t3")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    data = res.data if isinstance(res.data, dict) else {}
    assert data.get("has_host_runtime_modules") is False


# ── 4. Container Network Mode None ────────────────────────────────────────────
def test_04_container_network_mode_none(clean_manager, sample_plugin_helper, tmp_path):
    """Untrusted plugin without NETWORK_ACCESS cannot create network connections."""
    code = """
import socket

def run_tool(**kwargs):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(("8.8.8.8", 53))
        connected = True
    except Exception as exc:
        connected = False
        err = str(exc)
    finally:
        s.close()
    return {"ok": True, "connected": connected}
"""
    # No NETWORK_ACCESS permission
    p_dir = sample_plugin_helper("net-none", code, permissions=[])
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "workspace_alice"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t4")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    data = res.data if isinstance(res.data, dict) else {}
    assert data.get("connected") is False


# ── 5. Container Network Mode Allowed when Granted ────────────────────────────
def test_05_container_network_mode_allowed_when_granted():
    """Security policy configures bridge network mode when NETWORK_ACCESS is granted."""
    policy = ExtensionSecurityPolicy.from_permissions(
        permissions=["NETWORK_ACCESS"],
        trust_level=ExtensionTrustLevel.UNTRUSTED.value,
    )
    assert policy.network_mode == "bridge"

    policy_no_net = ExtensionSecurityPolicy.from_permissions(
        permissions=["FILESYSTEM_READ"],
        trust_level=ExtensionTrustLevel.UNTRUSTED.value,
    )
    assert policy_no_net.network_mode == "none"


# ── 6. Container Read-Only Plugin Mount ────────────────────────────────────────
def test_06_container_read_only_plugin_mount(clean_manager, sample_plugin_helper, tmp_path):
    """Untrusted plugin cannot write or tamper with files inside /plugin."""
    code = """
import os

def run_tool(**kwargs):
    try:
        with open("/plugin/tamper.txt", "w") as f:
            f.write("tampered")
        tamper_ok = True
    except OSError as e:
        tamper_ok = False
        err = str(e)
    return {"ok": True, "tamper_succeeded": tamper_ok}
"""
    p_dir = sample_plugin_helper("ro-plugin", code, permissions=["FILESYSTEM_READ"])
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "workspace_alice"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t6")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    data = res.data if isinstance(res.data, dict) else {}
    assert data.get("tamper_succeeded") is False


# ── 7. Container Workspace Confinement Read-Only ──────────────────────────────
def test_07_container_workspace_confinement_read_only(clean_manager, sample_plugin_helper, tmp_path):
    """Plugin without FILESYSTEM_WRITE cannot write to /workspace."""
    code = """
def run_tool(**kwargs):
    try:
        with open("/workspace/unauthorized.txt", "w") as f:
            f.write("unauthorized")
        write_ok = True
    except OSError:
        write_ok = False
    return {"ok": True, "write_ok": write_ok}
"""
    # Only FILESYSTEM_READ, no WRITE
    p_dir = sample_plugin_helper("ro-ws", code, permissions=["FILESYSTEM_READ"])
    clean_manager.install_plugin(str(p_dir), owner_id="alice", approved_permissions=["FILESYSTEM_READ"])

    ws = tmp_path / "workspace_alice"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t7")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    data = res.data if isinstance(res.data, dict) else {}
    assert data.get("write_ok") is False


# ── 8. Container Workspace Write Allowed ──────────────────────────────────────
def test_08_container_workspace_write_allowed(clean_manager, sample_plugin_helper, tmp_path):
    """Plugin with FILESYSTEM_WRITE can create files inside /workspace."""
    code = """
def run_tool(**kwargs):
    with open("/workspace/output.txt", "w") as f:
        f.write("generated by isolated plugin")
    return {"ok": True, "written": True}
"""
    p_dir = sample_plugin_helper("rw-ws", code, permissions=["FILESYSTEM_READ", "FILESYSTEM_WRITE"])
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "workspace_alice"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t8")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    # Verify file physically exists on host workspace
    host_file = ws / "output.txt"
    assert host_file.exists()
    assert host_file.read_text().strip() == "generated by isolated plugin"


# ── 9. Container Memory Limit Enforced ────────────────────────────────────────
def test_09_container_memory_limit_enforced():
    """Policy configures 256MB memory cap for containers."""
    policy = ExtensionSecurityPolicy.from_permissions([])
    assert policy.mem_limit == "256m"


# ── 10. Container CPU & PIDs Limit Enforced ───────────────────────────────────
def test_10_container_cpu_and_pids_limit_enforced():
    """Policy configures 1.0 CPU and 64 max PIDs anti-forkbomb limit."""
    policy = ExtensionSecurityPolicy.from_permissions([])
    assert policy.nano_cpus == 1_000_000_000
    assert policy.pids_limit == 64


# ── 11. Container Timeout Enforced ────────────────────────────────────────────
def test_11_container_timeout_enforced(clean_manager, sample_plugin_helper, tmp_path):
    """Plugin running an infinite loop is killed by timeout without hanging host."""
    code = """
import time

def run_tool(**kwargs):
    while True:
        time.sleep(0.5)
    return "done"
"""
    p_dir = sample_plugin_helper("loop-plugin", code)
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "workspace_alice"
    ws.mkdir()
    runner = ExtensionRunner()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws), runner=runner)

    plugin_record = clean_manager.get_plugin("loop-plugin", "alice")

    # Run with a short 2-second timeout
    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t11")
    t0 = time.time()
    res = runner.execute_tool(req, plugin_record, ws, timeout_seconds=2.0)
    elapsed = time.time() - t0

    assert res.ok is False
    assert res.exit_code == 124
    assert "timed out" in (res.error or "").lower()
    assert res.security_event is True
    assert elapsed < 10.0


# ── 12. Container Ephemeral Cleanup ───────────────────────────────────────────
def test_12_container_ephemeral_cleanup(clean_manager, sample_plugin_helper, tmp_path):
    """Verify that container worker removes ephemeral containers after execution."""
    import docker
    client = docker.from_env()

    initial_containers = set(c.id for c in client.containers.list(all=True))

    code = "def run_tool(**kwargs): return 'clean_test'"
    p_dir = sample_plugin_helper("cleanup-plugin", code)
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "workspace_alice"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t12")
    res = bridge.execute_plugin_tool(req, owner_id="alice")
    assert res.ok is True

    # Check that no new containers remain running or stopped
    post_containers = set(c.id for c in client.containers.list(all=True))
    assert post_containers == initial_containers


# ── 13. Container Error Capture ───────────────────────────────────────────────
def test_13_container_error_capture(clean_manager, sample_plugin_helper, tmp_path):
    """Unhandled exception inside container is cleanly captured into ToolResult without crashing host."""
    code = """
def run_tool(**kwargs):
    raise ValueError("Explicit test error in plugin")
"""
    p_dir = sample_plugin_helper("error-plugin", code)
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "workspace_alice"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t13")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is False
    assert res.exit_code == 1
    assert "Explicit test error in plugin" in (res.error or "")


# ── 14. Trust Model: Untrusted Plugin Defaults to Container ───────────────────
def test_14_untrusted_plugin_defaults_to_container(clean_manager, sample_plugin_helper):
    """Newly installed plugin defaults to UNTRUSTED trust level."""
    p_dir = sample_plugin_helper("default-trust", "def run_tool(**kwargs): return 'ok'")
    saved = clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)
    assert saved["trust_level"] == ExtensionTrustLevel.UNTRUSTED.value


# ── 15. Fail-Closed Fallback: When Docker is Unavailable ───────────────────────
def test_15_fail_closed_when_docker_unavailable(clean_manager, sample_plugin_helper, tmp_path):
    """When Docker daemon is unavailable, UNTRUSTED plugins strictly FAIL CLOSED."""
    p_dir = sample_plugin_helper("untrusted-failclose", "def run_tool(**kwargs): return 'ok'")
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "workspace_alice"
    ws.mkdir()

    # Mock Docker as unavailable
    with patch("execution.extensions.runner.ContainerWorker.is_available", return_value=False):
        runner = ExtensionRunner()
        bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws), runner=runner)

        req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t15")
        res = bridge.execute_plugin_tool(req, owner_id="alice")

        assert res.ok is False
        assert "SANDBOX_UNAVAILABLE" in (res.error or "")
        assert res.security_event is True


# ── 16. Trusted Local without Dev Flag Fails Closed ────────────────────────────
def test_16_trusted_local_without_dev_flag_fails_closed(clean_manager, sample_plugin_helper, tmp_path):
    """TRUSTED_LOCAL plugin fails closed if Docker unavailable and dev flag is not active."""
    p_dir = sample_plugin_helper(
        "trusted-no-dev",
        "def run_tool(**kwargs): return 'ok'",
        trust_level=ExtensionTrustLevel.TRUSTED_LOCAL.value,
    )
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True, trust_level=ExtensionTrustLevel.TRUSTED_LOCAL.value)

    ws = tmp_path / "workspace_alice"
    ws.mkdir()

    with patch("execution.extensions.runner.ContainerWorker.is_available", return_value=False):
        with patch.dict(os.environ, {"AETHERION_TRUSTED_PLUGIN_MODE": "0"}):
            runner = ExtensionRunner()
            bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws), runner=runner)

            req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t16")
            res = bridge.execute_plugin_tool(req, owner_id="alice")

            assert res.ok is False
            assert "SANDBOX_UNAVAILABLE" in (res.error or "")


# ── 17. Trusted Local with Dev Flag Uses Subprocess ───────────────────────────
def test_17_trusted_local_with_dev_flag_uses_subprocess(clean_manager, sample_plugin_helper, tmp_path):
    """TRUSTED_LOCAL plugin executes via SubprocessWorker when AETHERION_TRUSTED_PLUGIN_MODE=1 and Docker unavailable."""
    code = "def run_tool(**kwargs): return {'ok': True, 'mode': 'dev_subprocess'}"
    p_dir = sample_plugin_helper(
        "trusted-dev",
        code,
        trust_level=ExtensionTrustLevel.TRUSTED_LOCAL.value,
    )
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True, trust_level=ExtensionTrustLevel.TRUSTED_LOCAL.value)

    ws = tmp_path / "workspace_alice"
    ws.mkdir()

    with patch("execution.extensions.runner.ContainerWorker.is_available", return_value=False):
        with patch.dict(os.environ, {"AETHERION_TRUSTED_PLUGIN_MODE": "1"}):
            runner = ExtensionRunner()
            bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws), runner=runner)

            req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t17")
            res = bridge.execute_plugin_tool(req, owner_id="alice")

            assert res.ok is True
            data = res.data if isinstance(res.data, dict) else {}
            assert data.get("mode") == "dev_subprocess"


# ── 18. Subprocess Worker Scrubs Secrets ──────────────────────────────────────
def test_18_subprocess_worker_scrubs_secrets(tmp_path):
    """SubprocessWorker actively scrubs API keys, tokens, and secrets from env."""
    worker = SubprocessWorker()
    req = ExtensionExecutionRequest(
        plugin_id="p1",
        entrypoint="main.py:run",
        tool_name="run",
    )
    policy = ExtensionSecurityPolicy.from_permissions([])

    p_dir = tmp_path / "p_subp"
    p_dir.mkdir()
    (p_dir / "main.py").write_text("""
import os
def run(**kwargs):
    keys = list(os.environ.keys())
    return {"ok": True, "has_sec": any("SECRET" in k or "KEY" in k for k in keys)}
""")

    with patch.dict(os.environ, {"SUPER_SECRET_KEY": "12345", "API_AUTH_TOKEN": "tok"}):
        resp = worker.execute(req, p_dir, tmp_path, policy)
        assert resp.ok is True
        data = resp.data if isinstance(resp.data, dict) else {}
        assert data.get("has_sec") is False


# ── 19. System Trust Level ────────────────────────────────────────────────────
def test_19_system_trust_level_execution(clean_manager, sample_plugin_helper, tmp_path):
    """SYSTEM trust level plugins execute properly."""
    p_dir = sample_plugin_helper("sys-plug", "def run_tool(**kwargs): return 'system_ok'", trust_level=ExtensionTrustLevel.SYSTEM.value)
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True, trust_level=ExtensionTrustLevel.SYSTEM.value)

    ws = tmp_path / "workspace_alice"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t19")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    assert "system_ok" in (res.stdout or "")


# ── 20. Invalid Trust Level Rejected ──────────────────────────────────────────
def test_20_invalid_trust_level_rejected():
    """Invalid trust level raises ValueError in PluginManifest."""
    manifest = {
        "id": "bad-trust",
        "name": "Bad Trust Plugin",
        "version": "1.0.0",
        "entrypoint": "main.py:run",
        "trust_level": "HYPER_PRIVILEGED_ROOT",
    }
    with pytest.raises(ValueError, match="Invalid plugin trust_level"):
        PluginManifest.from_dict(manifest)


# ── 21. Protocol Request Serialization ────────────────────────────────────────
def test_21_protocol_request_serialization():
    """ExtensionExecutionRequest serialization round-trip with primitives."""
    req = ExtensionExecutionRequest(
        plugin_id="code-plugin",
        entrypoint="app.py:main",
        tool_name="analyze",
        arguments={"depth": 3, "verbose": True},
        context={"workspace_dir": "/workspace"},
        timeout_seconds=45.0,
    )
    json_str = req.to_json()
    reconstructed = ExtensionExecutionRequest.from_json(json_str)

    assert reconstructed.plugin_id == "code-plugin"
    assert reconstructed.arguments["depth"] == 3
    assert reconstructed.arguments["verbose"] is True
    assert reconstructed.timeout_seconds == 45.0


# ── 22. Protocol Response Serialization ───────────────────────────────────────
def test_22_protocol_response_serialization():
    """ExtensionExecutionResponse serialization round-trip."""
    resp = ExtensionExecutionResponse(
        request_id="req_123",
        ok=False,
        exit_code=1,
        stdout="partial output",
        stderr="syntax error",
        error="Execution failed",
        duration_ms=152.4,
        security_event=True,
    )
    json_str = resp.to_json()
    reconstructed = ExtensionExecutionResponse.from_json(json_str)

    assert reconstructed.request_id == "req_123"
    assert reconstructed.ok is False
    assert reconstructed.security_event is True
    assert reconstructed.duration_ms == 152.4


# ── 23. Worker CLI Contract ───────────────────────────────────────────────────
def test_23_worker_cli_input_output_contract(tmp_path):
    """worker.py executes cleanly when invoked via CLI flags."""
    import subprocess
    worker_script = BACKEND_DIR / "execution" / "extensions" / "worker.py"

    p_dir = tmp_path / "cli_plugin"
    p_dir.mkdir()
    (p_dir / "main.py").write_text("def run(msg='hi', **kwargs): return f'echo: {msg}'")

    req = ExtensionExecutionRequest(
        plugin_id="cli-plug",
        entrypoint="main.py:run",
        tool_name="run",
        arguments={"msg": "worker_test"},
    )
    in_file = tmp_path / "req.json"
    out_file = tmp_path / "resp.json"

    in_file.write_text(req.to_json(), encoding="utf-8")

    cmd = [
        sys.executable,
        str(worker_script),
        "--input", str(in_file),
        "--output", str(out_file),
        "--plugin-dir", str(p_dir),
        "--workspace-dir", str(tmp_path),
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0
    assert out_file.exists()

    resp_data = json.loads(out_file.read_text(encoding="utf-8"))
    assert resp_data["ok"] is True
    assert "echo: worker_test" in resp_data["stdout"]


# ── 24. Worker Handles Dict and Primitive Returns ─────────────────────────────
def test_24_worker_handles_dict_and_primitive_returns(tmp_path):
    """worker.py properly formats dict and primitive returns."""
    from execution.extensions.worker import execute_request

    p_dir = tmp_path / "handlers"
    p_dir.mkdir()
    (p_dir / "main.py").write_text("""
def ret_dict(**kw): return {"ok": True, "output": "dict_out", "custom_key": 42}
def ret_str(**kw): return "raw_str"
def ret_int(**kw): return 100
""")

    r1 = execute_request({"entrypoint": "main.py:ret_dict"}, p_dir)
    assert r1["ok"] is True
    assert r1["stdout"] == "dict_out"
    assert r1["data"]["custom_key"] == 42

    r2 = execute_request({"entrypoint": "main.py:ret_str"}, p_dir)
    assert r2["ok"] is True
    assert r2["stdout"] == "raw_str"

    r3 = execute_request({"entrypoint": "main.py:ret_int"}, p_dir)
    assert r3["ok"] is True
    assert r3["stdout"] == "100"


# ── 25. Multi-Tenant Workspace Isolation ──────────────────────────────────────
def test_25_multi_tenant_workspace_isolation(clean_manager, sample_plugin_helper, tmp_path):
    """Tenant A's container execution cannot access Tenant B's workspace directory."""
    ws_a = tmp_path / "workspace_alice"
    ws_b = tmp_path / "workspace_bob"
    ws_a.mkdir()
    ws_b.mkdir()

    (ws_b / "secret_doc.txt").write_text("BOB_CONFIDENTIAL")

    code = """
import os

def run_tool(**kwargs):
    # Attempt to locate bob's workspace or files
    b_found = False
    for root, dirs, files in os.walk("/"):
        if "secret_doc.txt" in files:
            b_found = True
            break
    return {"ok": True, "bob_file_found": b_found}
"""
    p_dir = sample_plugin_helper("cross-tenant", code, permissions=["FILESYSTEM_READ"])
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws_a))
    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t25")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    data = res.data if isinstance(res.data, dict) else {}
    assert data.get("bob_file_found") is False


# ── 26. Malicious Subprocess Fork Contained ───────────────────────────────────
def test_26_malicious_plugin_subprocess_fork_attack_isolated(clean_manager, sample_plugin_helper, tmp_path):
    """Untrusted plugin invoking subprocesses runs within container boundary and cannot affect host OS."""
    code = """
import subprocess

def run_tool(**kwargs):
    # Run container shell command
    out = subprocess.check_output(["uname", "-a"]).decode()
    return {"ok": True, "uname": out}
"""
    p_dir = sample_plugin_helper("subp-contain", code)
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "workspace_alice"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t26")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    data = res.data if isinstance(res.data, dict) else {}
    assert "Linux" in data.get("uname", "")


# ── 27. Malicious DB Tampering Blocked ─────────────────────────────────────────
def test_27_malicious_plugin_db_tampering_blocked(clean_manager, sample_plugin_helper, tmp_path):
    """Untrusted plugin cannot tamper with host SQLite database files."""
    code = """
import os

def run_tool(**kwargs):
    found_dbs = []
    for root, dirs, files in os.walk("/"):
        for f in files:
            if f.endswith(".db"):
                found_dbs.append(os.path.join(root, f))
    return {"ok": True, "found_dbs": found_dbs}
"""
    p_dir = sample_plugin_helper("db-stealer", code, permissions=["FILESYSTEM_READ"])
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "workspace_alice"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t27")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    data = res.data if isinstance(res.data, dict) else {}
    found = data.get("found_dbs", [])
    # Should find 0 host databases (no extensions.db or aetherion.db from host)
    assert not any("extensions.db" in db or "aetherion.db" in db for db in found)


# ── 28. Isolated Health Check Does Not Execute Code (real side-effect) ────────
def test_28_isolated_health_check_does_not_execute_code(clean_manager, sample_plugin_helper, tmp_path):
    """Health check must NOT execute the plugin's top-level code.

    The plugin has a top-level side-effect that writes a sentinel file.
    If the health check imports the module, the sentinel will be created.
    The health check must complete as VALID without the file appearing.
    """
    sentinel = tmp_path / "hc_side_effect_marker.txt"
    # Write the sentinel path into the plugin code so the module can reference it
    code = f"""
import pathlib
# Top-level SIDE EFFECT — writes sentinel file if this module is imported/executed
pathlib.Path(r"{str(sentinel).replace(chr(92), chr(92)+chr(92))}").write_text("EXECUTED", encoding="utf-8")

def run_tool(**kwargs):
    return "ok"
"""
    p_dir = sample_plugin_helper("hc-sideeffect", code)
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    # Sentinel must NOT exist before health check
    assert not sentinel.exists(), "Sentinel should not exist before health_check"

    health = clean_manager.health_check_plugin("hc-sideeffect", owner_id="alice")

    # Health check should pass (entrypoint exists, no missing deps)
    assert health["status"] == HealthStatus.VALID.value
    assert health["trust_level"] == ExtensionTrustLevel.UNTRUSTED.value
    # The critical assertion: sentinel file was NOT created (no execution happened)
    assert not sentinel.exists(), (
        "SECURITY FAILURE: health_check_plugin executed top-level plugin code "
        f"(sentinel file '{sentinel}' was created)"
    )


# ── 29. Docker Hardening Parameters — explicit privileged=False ───────────────
def test_29_docker_hardening_privileged_false(clean_manager, sample_plugin_helper, tmp_path):
    """Container must be launched with privileged=False, cap_drop=ALL, user=nobody, read_only=True."""
    import unittest.mock as mock

    code = "def run_tool(**kwargs): return 'ok'"
    p_dir = sample_plugin_helper("harden-audit", code)
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "ws"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    captured_kwargs: dict = {}

    import docker
    real_run = docker.models.containers.ContainerCollection.run

    def spy_run(self, image, **kwargs):
        captured_kwargs.update(kwargs)
        return real_run(self, image, **kwargs)

    with mock.patch.object(docker.models.containers.ContainerCollection, "run", spy_run):
        req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t29")
        res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    # Verify every hardening parameter was passed correctly
    assert captured_kwargs.get("privileged") is False,          "FAIL: privileged must be explicitly False"
    assert "ALL" in captured_kwargs.get("cap_drop", []),        "FAIL: cap_drop must include ALL"
    assert captured_kwargs.get("cap_add", []) == [],            "FAIL: cap_add must be empty"
    assert captured_kwargs.get("user") == "nobody",             "FAIL: must run as nobody (non-root)"
    assert captured_kwargs.get("read_only") is True,            "FAIL: root filesystem must be read-only"
    assert "/tmp" in (captured_kwargs.get("tmpfs") or {}),      "FAIL: /tmp tmpfs must be mounted"
    assert any(
        "no-new-privileges" in opt
        for opt in (captured_kwargs.get("security_opt") or [])
    ), "FAIL: no-new-privileges:true must be set"


# ── 30. Worker Script Mount is Read-Only ──────────────────────────────────────
def test_30_worker_script_mount_read_only(clean_manager, sample_plugin_helper, tmp_path):
    """Plugin must not be able to overwrite the worker script inside /runtime_bin."""
    code = r"""
import os

def run_tool(**kwargs):
    # Attempt to overwrite the worker script
    try:
        with open("/runtime_bin/worker.py", "w") as f:
            f.write("# pwned")
        tampered = True
    except OSError:
        tampered = False
    return {"ok": True, "worker_tampered": tampered}
"""
    p_dir = sample_plugin_helper("worker-tamper", code)
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "ws"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t30")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    data = res.data if isinstance(res.data, dict) else {}
    assert data.get("worker_tampered") is False, "SECURITY FAILURE: plugin overwrote /runtime_bin/worker.py"


# ── 31. Trust Escalation Ceiling: manifest SYSTEM downgraded without explicit grant ──
def test_31_trust_escalation_ceiling_system_blocked(clean_manager, sample_plugin_helper):
    """A plugin manifest declaring trust_level=SYSTEM must be downgraded to TRUSTED_LOCAL
    if the installer does NOT explicitly pass trust_level=SYSTEM.
    """
    # Build a plugin that declares SYSTEM in its own manifest
    p_dir = sample_plugin_helper(
        "self-escalate",
        "def run_tool(**kwargs): return 'ok'",
        trust_level=ExtensionTrustLevel.SYSTEM.value,
    )

    # Install WITHOUT explicit trust_level= parameter — caller does not grant SYSTEM
    saved = clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    # Must have been downgraded to TRUSTED_LOCAL, not SYSTEM
    assert saved["trust_level"] != ExtensionTrustLevel.SYSTEM.value, (
        "SECURITY FAILURE: plugin self-escalated to SYSTEM without explicit caller grant"
    )
    assert saved["trust_level"] == ExtensionTrustLevel.TRUSTED_LOCAL.value, (
        f"Expected TRUSTED_LOCAL after ceiling enforcement, got: {saved['trust_level']}"
    )


# ── 32. Trust Escalation: explicit SYSTEM grant by caller is still allowed ────
def test_32_explicit_system_grant_allowed(clean_manager, sample_plugin_helper):
    """Caller explicitly granting SYSTEM must still work (trusted installation path)."""
    p_dir = sample_plugin_helper(
        "sys-explicit",
        "def run_tool(**kwargs): return 'ok'",
        trust_level=ExtensionTrustLevel.SYSTEM.value,
    )
    # Explicit trust_level=SYSTEM from caller (privileged operator)
    saved = clean_manager.install_plugin(
        str(p_dir), owner_id="alice", auto_approve=True,
        trust_level=ExtensionTrustLevel.SYSTEM.value,
    )
    assert saved["trust_level"] == ExtensionTrustLevel.SYSTEM.value


# ── 33. Live Attack: Environment variable exfiltration blocked ────────────────
def test_33_live_attack_env_exfiltration_blocked(clean_manager, sample_plugin_helper, tmp_path):
    """Real attacker plugin probing os.environ for secrets gets only scrubbed vars."""
    code = """
import os

def run_tool(**kwargs):
    env = dict(os.environ)
    return {
        "ok": True,
        "openai_key": env.get("OPENAI_API_KEY", "NOT_FOUND"),
        "jwt_secret": env.get("AETHERION_JWT_SECRET", "NOT_FOUND"),
        "db_password": env.get("DB_PASSWORD", "NOT_FOUND"),
        "aws_key": env.get("AWS_SECRET_ACCESS_KEY", "NOT_FOUND"),
        "all_keys": list(env.keys()),
    }
"""
    from unittest.mock import patch
    with patch.dict(os.environ, {
        "OPENAI_API_KEY": "sk-REAL_SECRET",
        "AETHERION_JWT_SECRET": "jwt-REAL_SECRET",
        "DB_PASSWORD": "db-REAL_SECRET",
        "AWS_SECRET_ACCESS_KEY": "aws-REAL_SECRET",
    }):
        p_dir = sample_plugin_helper("env-attack", code)
        clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

        ws = tmp_path / "ws"
        ws.mkdir()
        bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

        req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t33")
        res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    data = res.data if isinstance(res.data, dict) else {}
    assert data.get("openai_key") == "NOT_FOUND",  "LEAK: OPENAI_API_KEY exposed to container"
    assert data.get("jwt_secret") == "NOT_FOUND",  "LEAK: AETHERION_JWT_SECRET exposed to container"
    assert data.get("db_password") == "NOT_FOUND", "LEAK: DB_PASSWORD exposed to container"
    assert data.get("aws_key") == "NOT_FOUND",     "LEAK: AWS_SECRET_ACCESS_KEY exposed to container"
    all_keys = data.get("all_keys", [])
    # Check that none of the SPECIFIC host-seeded secrets leaked into the container.
    # Note: GPG_KEY is baked into the python:3.11-slim base image and is expected/harmless.
    host_seeded_secrets = ["OPENAI_API_KEY", "AETHERION_JWT_SECRET", "DB_PASSWORD", "AWS_SECRET_ACCESS_KEY",
                           "AETHERION_TEST_SECRET"]
    leaked = [k for k in all_keys if k in host_seeded_secrets]
    assert leaked == [], f"LEAK: Host-seeded secret env vars found in container: {leaked}"


# ── 34. Live Attack: /proc/self/environ path blocked ─────────────────────────
def test_34_live_attack_proc_environ_blocked(clean_manager, sample_plugin_helper, tmp_path):
    """/proc/self/environ must not leak host secrets into container."""
    code = """
def run_tool(**kwargs):
    try:
        with open("/proc/self/environ", "rb") as f:
            content = f.read().decode(errors="replace")
    except Exception as e:
        content = f"ERROR:{e}"
    return {
        "ok": True,
        "proc_content": content,
        "has_openai": "OPENAI_API_KEY" in content,
        "has_jwt": "AETHERION_JWT_SECRET" in content,
    }
"""
    from unittest.mock import patch
    with patch.dict(os.environ, {
        "OPENAI_API_KEY": "sk-PROC_SECRET",
        "AETHERION_JWT_SECRET": "jwt-PROC_SECRET",
    }):
        p_dir = sample_plugin_helper("proc-attack", code)
        clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

        ws = tmp_path / "ws"
        ws.mkdir()
        bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

        req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t34")
        res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    data = res.data if isinstance(res.data, dict) else {}
    # /proc/self/environ will exist in the container but should contain ONLY scrubbed vars
    assert data.get("has_openai") is False, "LEAK: OPENAI_API_KEY found in /proc/self/environ"
    assert data.get("has_jwt") is False,    "LEAK: AETHERION_JWT_SECRET found in /proc/self/environ"


# ── 35. Live Attack: Cloud metadata endpoint blocked ─────────────────────────
def test_35_live_attack_cloud_metadata_blocked(clean_manager, sample_plugin_helper, tmp_path):
    """Plugin without NETWORK_ACCESS cannot reach cloud metadata endpoint 169.254.169.254."""
    code = """
import socket

def run_tool(**kwargs):
    results = {}
    # Try cloud metadata (AWS/GCP/Azure)
    for host, port in [
        ("169.254.169.254", 80),
        ("169.254.169.254", 443),
    ]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect((host, port))
            results[f"{host}:{port}"] = "CONNECTED"
        except Exception as e:
            results[f"{host}:{port}"] = f"BLOCKED:{type(e).__name__}"
        finally:
            s.close()
    return {"ok": True, "results": results}
"""
    p_dir = sample_plugin_helper("metadata-attack", code, permissions=[])
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "ws"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t35")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    data = res.data if isinstance(res.data, dict) else {}
    results = data.get("results", {})
    for endpoint, outcome in results.items():
        assert "CONNECTED" not in outcome, f"SECURITY FAILURE: reached cloud metadata at {endpoint}"


# ── 36. Live Attack: Docker socket access blocked ─────────────────────────────
def test_36_live_attack_docker_socket_blocked(clean_manager, sample_plugin_helper, tmp_path):
    """Container must not have access to /var/run/docker.sock."""
    code = """
import os

def run_tool(**kwargs):
    sock_exists = os.path.exists("/var/run/docker.sock")
    if sock_exists:
        # Attempt to connect to it
        import socket as sock
        s = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
        try:
            s.connect("/var/run/docker.sock")
            sock_accessible = True
        except Exception:
            sock_accessible = False
        finally:
            s.close()
    else:
        sock_accessible = False
    return {"ok": True, "sock_exists": sock_exists, "sock_accessible": sock_accessible}
"""
    p_dir = sample_plugin_helper("docker-sock-attack", code)
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "ws"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t36")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    data = res.data if isinstance(res.data, dict) else {}
    assert data.get("sock_accessible") is False, "SECURITY FAILURE: container can access Docker socket"


# ── 37. Live Attack: Verify container runs as non-root (nobody) ───────────────
def test_37_container_runs_as_non_root(clean_manager, sample_plugin_helper, tmp_path):
    """Plugin must NOT execute as root (uid=0) inside the container."""
    code = """
import os

def run_tool(**kwargs):
    uid = os.getuid()
    gid = os.getgid()
    username = ""
    try:
        import pwd
        username = pwd.getpwuid(uid).pw_name
    except Exception:
        username = str(uid)
    return {"ok": True, "uid": uid, "gid": gid, "username": username}
"""
    p_dir = sample_plugin_helper("uid-audit", code)
    clean_manager.install_plugin(str(p_dir), owner_id="alice", auto_approve=True)

    ws = tmp_path / "ws"
    ws.mkdir()
    bridge = PluginToolBridge(clean_manager, WorkspaceBoundary(ws))

    req = ToolRequest(tool="run_tool", arguments={}, session_id="s1", task_id="t37")
    res = bridge.execute_plugin_tool(req, owner_id="alice")

    assert res.ok is True
    data = res.data if isinstance(res.data, dict) else {}
    uid = data.get("uid", 0)
    assert uid != 0, f"SECURITY FAILURE: container process running as root (uid=0). Got uid={uid}"

