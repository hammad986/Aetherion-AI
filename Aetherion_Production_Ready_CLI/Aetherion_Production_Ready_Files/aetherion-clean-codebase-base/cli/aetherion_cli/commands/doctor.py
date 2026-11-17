"""
aetherion_cli/commands/doctor.py
Environment, connectivity, security, and authentication diagnostics.
"""
from __future__ import annotations

import datetime
import os
import platform
import stat
import sys
import time
import click
import httpx
from rich.table import Table

from ..auth.token_store import TokenStore
from ..config.settings import CONFIG_FILE, get_server_url
from ..output.formatters import print_output, stdout_console


@click.command("doctor")
@click.option("--json", "as_json", is_flag=True, help="Output doctor report as JSON.")
def doctor_cmd(as_json: bool) -> None:
    """Run diagnostics on local environment, credentials, connectivity, and server health."""
    server_url = get_server_url()
    checks = []

    # 1. CLI Version & Environment
    checks.append({
        "check": "CLI Version",
        "status": "PASS",
        "detail": f"v1.0.0 (Python {sys.version.split()[0]} on {platform.system()})",
    })

    # 2. Config File Permissions
    config_detail = f"Path: {CONFIG_FILE}"
    config_status = "PASS"
    if CONFIG_FILE.exists():
        if sys.platform != "win32":
            mode = stat.S_IMODE(os.stat(CONFIG_FILE).st_mode)
            if mode & 0o077:
                config_status = "WARN"
                config_detail += " (Permissions too open; run chmod 600)"
            else:
                config_detail += " (Secure 0600)"
        else:
            config_detail += " (Present)"
    else:
        config_detail += " (Not created yet; will be created on login)"
    checks.append({"check": "Configuration Store", "status": config_status, "detail": config_detail})

    # 3. Auth Storage Mechanism
    auth_status = TokenStore.get_status()
    checks.append({
        "check": "Credential Storage",
        "status": "PASS" if auth_status["keyring_available"] else "WARN",
        "detail": (
            f"Active: {auth_status['storage_kind'].upper()} "
            f"(OS Keyring: {'Available' if auth_status['keyring_available'] else 'Unavailable - Using file fallback'})"
        ),
    })

    # 4. Current Session (Zero token exposure)
    if auth_status["authenticated"]:
        claims = auth_status.get("claims") or {}
        user_display = claims.get("username") or claims.get("sub") or "authenticated user"
        checks.append({
            "check": "Authentication State",
            "status": "PASS",
            "detail": f"Logged in as {user_display} (Token: {auth_status['masked_token']})",
        })
    else:
        checks.append({
            "check": "Authentication State",
            "status": "WARN",
            "detail": "Not authenticated (Run `aetherion auth login`)",
        })

    # 5. Backend Server Reachability & Clock Skew
    backend_status = "FAIL"
    backend_detail = f"Could not reach {server_url}"
    server_health = None
    clock_skew = None

    try:
        t0 = time.time()
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{server_url}/api/system/health")
            if not resp.is_success:
                resp = client.get(f"{server_url}/api/health")
            t1 = time.time()

            if resp.is_success:
                backend_status = "PASS"
                server_health = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                rtt_ms = int((t1 - t0) * 1000)
                backend_detail = f"Connected to {server_url} (RTT: {rtt_ms}ms)"

                # Clock skew check
                date_header = resp.headers.get("Date")
                if date_header:
                    try:
                        server_time = datetime.datetime.strptime(date_header, "%a, %d %b %Y %H:%M:%S GMT")
                        server_ts = server_time.replace(tzinfo=datetime.timezone.utc).timestamp()
                        skew = abs(time.time() - server_ts)
                        clock_skew = f"{skew:.2f}s"
                    except Exception:
                        pass
            else:
                backend_status = "FAIL"
                backend_detail = f"Server returned HTTP {resp.status_code}"
    except Exception as e:
        backend_status = "FAIL"
        backend_detail = f"Connection failed: {e}"

    checks.append({"check": "Server Connectivity", "status": backend_status, "detail": backend_detail})

    if clock_skew:
        checks.append({
            "check": "Clock Synchronization",
            "status": "PASS" if float(clock_skew.rstrip("s")) < 30.0 else "WARN",
            "detail": f"Clock skew relative to server: {clock_skew}",
        })

    # 6. Runner Isolation Status (from server health)
    if server_health and isinstance(server_health, dict):
        runner_info = server_health.get("runner", {})
        active_runner = runner_info.get("active_runner", {})
        checks.append({
            "check": "Runner Isolation",
            "status": "PASS" if not runner_info.get("degraded") else "WARN",
            "detail": (
                f"Class: {active_runner.get('isolation_class', 'unknown')} | "
                f"Policy: {active_runner.get('network_policy', 'unknown')}"
            ),
        })

    # 7. Execution Fabric Control Plane & Worker Fleet
    fabric_data = {
        "status": "UNKNOWN",
        "store_type": "unknown",
        "coordinator_mode": "unknown",
        "registered_workers": 0,
        "active_queue_items": 0,
    }
    fabric_status = "PASS"
    fabric_detail = ""

    # Attempt to query live control plane endpoint first
    fabric_fetched = False
    try:
        with httpx.Client(timeout=3.0) as client:
            f_resp = client.get(f"{server_url}/api/health/control-plane")
            if f_resp.is_success:
                f_json = f_resp.json()
                fabric_data["status"] = f_json.get("status", "healthy")
                fabric_data["store_type"] = f_json.get("store", {}).get("type", "unknown")
                fabric_data["coordinator_mode"] = f_json.get("coordinator", {}).get("mode", "unknown")
                fabric_data["registered_workers"] = f_json.get("store", {}).get("registered_workers", 0)
                fabric_data["active_queue_items"] = f_json.get("store", {}).get("active_queue_items", 0)
                fabric_detail = (
                    f"Store: {fabric_data['store_type']} | "
                    f"Coord: {fabric_data['coordinator_mode']} | "
                    f"Workers: {fabric_data['registered_workers']} | "
                    f"Queue: {fabric_data['active_queue_items']}"
                )
                fabric_status = "PASS" if f_json.get("ok") else "WARN"
                fabric_fetched = True
    except Exception:
        pass

    # If server is not running, query local store / coordinator directly
    if not fabric_fetched:
        try:
            from backend.execution.fabric.store import get_fabric_store
            from backend.execution.fabric.coordination import FabricCoordinator
            l_store = get_fabric_store()
            l_coord = FabricCoordinator()
            l_workers = l_store.list_workers()
            l_items = l_store.count_global_active_items()

            fabric_data["status"] = "healthy"
            fabric_data["store_type"] = type(l_store).__name__
            fabric_data["coordinator_mode"] = "redis" if l_coord.is_distributed else "local_fallback"
            fabric_data["registered_workers"] = len(l_workers)
            fabric_data["active_queue_items"] = l_items

            fabric_detail = (
                f"Store: {fabric_data['store_type']} (local) | "
                f"Coord: {fabric_data['coordinator_mode']} | "
                f"Workers: {fabric_data['registered_workers']} | "
                f"Queue: {fabric_data['active_queue_items']}"
            )
            fabric_status = "PASS"
        except Exception as exc:
            fabric_status = "WARN"
            fabric_detail = f"Could not inspect local fabric: {exc}"

    checks.append({
        "check": "Execution Fabric",
        "status": fabric_status,
        "detail": fabric_detail,
    })

    overall_ok = not any(c["status"] == "FAIL" for c in checks)
    data = {
        "healthy": overall_ok,
        "server_url": server_url,
        "execution_fabric": fabric_data,
        "checks": checks,
    }

    if as_json:
        print_output(data, as_json=True)
    else:
        stdout_console.print("\n[bold]Aetherion System Doctor Diagnostic Report[/bold]\n")
        table = Table(title="System Health Checks", show_header=True, header_style="bold cyan")
        table.add_column("Status", width=8, justify="center")
        table.add_column("Component", width=24)
        table.add_column("Diagnostic Details", min_width=40)

        for c in checks:
            st = c["status"]
            color = "green" if st == "PASS" else ("yellow" if st == "WARN" else "red")
            table.add_row(f"[{color}]{st}[/{color}]", c["check"], c["detail"])

        stdout_console.print(table)
        if overall_ok:
            stdout_console.print("\n[bold green]✓ All critical preflight checks passed.[/bold green]\n")
        else:
            stdout_console.print("\n[bold red]✗ One or more diagnostics failed.[/bold red]\n")
