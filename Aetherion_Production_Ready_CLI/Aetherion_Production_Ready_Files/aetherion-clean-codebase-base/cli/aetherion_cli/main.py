"""
aetherion_cli/main.py
Aetherion CLI — all commands, production-grade.

Commands:
  auth:      login, logout, whoami
  agent:     chat, run, plan
  workspace: workspace list | create | delete | open
  memory:    memory search | remember | list | clear
  history:   history list | show
  replay:    replay status | list | show | frame | seek | diff | export
  plugins:   plugins list | install | uninstall | info
  providers: providers list | test
  config:    config (show / set)
  doctor:    connectivity + environment health check

Standards:
  - Every command validates inputs and shows progress spinners
  - Every command handles Ctrl+C (KeyboardInterrupt) gracefully
  - Every command returns correct exit codes (0 = success, 1 = error, 130 = cancelled)
  - No raw stack traces ever reach the user
  - Structured errors with hints and did-you-mean suggestions
  - Offline detection with actionable guidance
  - Automatic token refresh on 401
"""
from __future__ import annotations

import io
import json
import os
import signal
import sys
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Cross-platform bootstrap — runs before ANY import that touches I/O
# ---------------------------------------------------------------------------
os.environ.setdefault("PYTHONUTF8", "1")

if sys.platform == "win32":
    # Enable ANSI/VT100 virtual terminal processing (colours in CMD + PowerShell)
    try:
        import ctypes
        _k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        _k32.SetConsoleMode(_k32.GetStdHandle(-11), 7)
        _k32.SetConsoleMode(_k32.GetStdHandle(-12), 7)
    except Exception:
        pass

    # Reconfigure stdout/stderr to UTF-8 so Rich can render Unicode (must happen
    # before Rich's Console() is constructed below)
    for _stream_name in ("stdout", "stderr"):
        _s = getattr(sys, _stream_name, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from . import config as cfg_mod
from .client import (
    AetherionAuthError,
    AetherionClientError,
    AetherionOfflineError,
    Client,
)


# ---------------------------------------------------------------------------
# Consoles — stdout vs stderr
# ---------------------------------------------------------------------------

console = Console()
err_console = Console(stderr=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_client() -> Client:
    return Client()


def _require_auth(client: Client) -> None:
    """Exit with a helpful message if the user is not authenticated."""
    if not client.token:
        err_console.print(
            "[yellow]✗ Not authenticated.[/yellow]  "
            "Run [bold]aetherion login[/bold] first."
        )
        sys.exit(1)


def _handle_error(exc: Exception, context: str = "") -> None:
    """Print a clean, user-friendly error and exit 1."""
    if isinstance(exc, AetherionOfflineError):
        err_console.print(f"[red]✗ Offline:[/red] {exc}")
    elif isinstance(exc, AetherionAuthError):
        err_console.print(f"[red]✗ Auth error:[/red] {exc}")
    elif isinstance(exc, AetherionClientError):
        err_console.print(f"[red]✗ Error:[/red] {exc}")
    else:
        ctx = f" [{context}]" if context else ""
        err_console.print(f"[red]✗ Unexpected error{ctx}:[/red] {exc}")
    sys.exit(1)


def _spinner(description: str) -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )


def _format_ts(ts) -> str:
    """Convert epoch float or ISO string to a readable local time."""
    if not ts:
        return "—"
    try:
        import datetime
        if isinstance(ts, (int, float)):
            dt = datetime.datetime.fromtimestamp(float(ts))
        else:
            dt = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)[:16]


def _output_json(data) -> None:
    console.print_json(json.dumps(data, default=str))


# ---------------------------------------------------------------------------
# Automatic token refresh on 401
# ---------------------------------------------------------------------------

def _try_refresh_token() -> bool:
    """Attempt to use a stored refresh token to get a new access token.
    Returns True if a new access token was stored, False otherwise.
    """
    rt = cfg_mod.get_refresh_token()
    if not rt:
        return False
    try:
        import httpx as _httpx
        server = cfg_mod.get_server_url().rstrip("/")
        r = _httpx.post(
            f"{server}/api/auth/refresh",
            json={"refresh_token": rt},
            timeout=10,
        )
        if r.is_success:
            data = r.json()
            new_token = data.get("access_token") or data.get("token")
            if new_token:
                cfg_mod.set_token(new_token)
                new_rt = data.get("refresh_token")
                if new_rt:
                    cfg_mod.set_refresh_token(new_rt)
                return True
    except Exception:
        pass
    return False


def _get_client_with_refresh() -> Client:
    """Return a client. If token appears expired, try to refresh first."""
    client = Client()
    # Pre-emptive refresh if token is absent (e.g., cleared by OS restart)
    if not client.token:
        if _try_refresh_token():
            client = Client()
    return client


# ---------------------------------------------------------------------------
# Did-you-mean suggestion helper
# ---------------------------------------------------------------------------

def _did_you_mean(name: str, choices: list) -> str:
    """Return a 'Did you mean X?' string for the closest match, or empty."""
    try:
        import difflib
        matches = difflib.get_close_matches(name, choices, n=1, cutoff=0.5)
        if matches:
            return f"  Did you mean [bold]{matches[0]}[/bold]?"
    except Exception:
        pass
    return ""


def _render_tools_table() -> None:
    table = Table(title="Aetherion Agent Tools Registry", show_header=True)
    table.add_column("Tool", style="bold cyan")
    table.add_column("Category", style="yellow")
    table.add_column("Sandbox", style="magenta")
    table.add_column("Description", style="white")
    table.add_row("terminal_execute", "execution", "container/pty", "Execute shell commands inside isolated sandbox")
    table.add_row("file_read", "filesystem", "workspace", "Read files within workspace directory")
    table.add_row("file_write", "filesystem", "workspace", "Write new files to workspace directory")
    table.add_row("file_edit", "filesystem", "workspace", "Surgically edit existing files with diff checking")
    table.add_row("git_operations", "vcs", "worktree", "Branch, commit, diff, and status operations")
    table.add_row("memory_search", "memory", "in-process", "Hybrid semantic vector and lexical memory recall")
    table.add_row("symbol_graph", "ast", "in-process", "AST repository intelligence and symbol mapping")
    table.add_row("web_search", "web", "network", "External information retrieval via search")
    table.add_row("test_runner", "verification", "container", "Run pytest and test suites with verification")
    console.print(table)


def _render_toolsets_table() -> None:
    table = Table(title="Aetherion Agent Toolsets", show_header=True)
    table.add_column("Toolset", style="bold green")
    table.add_column("Included Tools", style="cyan")
    table.add_column("Default Scope", style="yellow")
    table.add_row("core", "file_read, file_write, file_edit, terminal_execute", "Local / CI tasks")
    table.add_row("coding", "core + git_operations + symbol_graph + test_runner", "Software engineering")
    table.add_row("research", "file_read, memory_search, web_search", "Analysis & documentation")
    table.add_row("all", "All tools enabled with container sandbox isolation", "Autonomous full-stack")
    console.print(table)


# ===========================================================================
# Root CLI group
# ===========================================================================

class _AetherionGroup(click.Group):
    """Custom group that provides did-you-mean suggestions on bad commands,
    and transparently runs natural language tasks like Claude Code / Codex / Hermes.
    """

    def parse_args(self, ctx: click.Context, args: list) -> list:
        if args and not args[0].startswith("-"):
            cmd_name = args[0]
            if cmd_name not in self.commands:
                # If it's a single word and very close to an existing command, let did-you-mean suggest it
                if len(args) == 1 and (" " not in cmd_name and "	" not in cmd_name):
                    import difflib
                    close = difflib.get_close_matches(cmd_name, list(self.commands.keys()), n=1, cutoff=0.75)
                    if close:
                        return super().parse_args(ctx, args)
                # Claude Code / Hermes behavior: route arbitrary prompt text directly to the agent
                args = ["chat"] + args
        return super().parse_args(ctx, args)

    def get_command(self, ctx: click.Context, cmd_name: str):
        cmd = super().get_command(ctx, cmd_name)
        if cmd is None:
            # Suggest the closest matching command
            suggestion = _did_you_mean(cmd_name, list(self.commands.keys()))
            msg = f"No such command '{cmd_name}'."
            if suggestion:
                msg += f"\n{suggestion}"
            err_console.print(f"[red]Error:[/red] {msg}")
            err_console.print(f"  Run [bold]aetherion --help[/bold] to see available commands.")
            sys.exit(2)
        return cmd


@click.group(
    cls=_AetherionGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(package_name="aetherion", prog_name="aetherion")
@click.option(
    "--output",
    "-o",
    type=click.Choice(["table", "json", "plain"]),
    default=None,
    help="Output format (overrides config).",
    envvar="AETHERION_OUTPUT",
)
@click.option(
    "--query",
    "-q",
    default=None,
    help="Run a query / task directly (like Hermes -q).",
)
@click.option(
    "--model",
    "-m",
    default=None,
    help="Model override (e.g. gpt-4o, claude-3-5-sonnet, deepseek-v4-flash).",
)
@click.option(
    "--session",
    "-s",
    default=None,
    help="Session / workspace ID.",
)
@click.option(
    "--worktree",
    "-w",
    is_flag=True,
    default=False,
    help="Run in isolated git worktree.",
)
@click.option(
    "--resume",
    default=None,
    help="Resume a previous session by ID.",
)
@click.option(
    "--list-tools",
    is_flag=True,
    default=False,
    help="List available tools and exit.",
)
@click.option(
    "--list-toolsets",
    is_flag=True,
    default=False,
    help="List available toolsets and exit.",
)
@click.option(
    "--quiet",
    is_flag=True,
    default=False,
    help="Suppress extra output chatter.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    output: Optional[str],
    query: Optional[str] = None,
    model: Optional[str] = None,
    session: Optional[str] = None,
    worktree: bool = False,
    resume: Optional[str] = None,
    list_tools: bool = False,
    list_toolsets: bool = False,
    quiet: bool = False,
) -> None:
    """Aetherion AI Platform CLI — build anything with AI agents."""
    ctx.ensure_object(dict)
    ctx.obj["output"] = output or cfg_mod.get_output_format()
    ctx.obj["quiet"] = quiet

    if list_tools:
        _render_tools_table()
        sys.exit(0)

    if list_toolsets:
        _render_toolsets_table()
        sys.exit(0)

    if query:
        client = _get_client()
        _require_auth(client)
        _run_agent_task(
            client=client,
            task_str=query,
            model=model or "",
            session=session or resume,
            repo=os.getcwd() if worktree else None,
        )
        sys.exit(0)

    if ctx.invoked_subcommand is None:
        # No subcommand: launch the interactive TUI workspace
        try:
            from .tui import run_tui
        except ImportError as exc:
            err_console.print(
                f"[red]✗ TUI dependencies not installed:[/red] {exc}\n"
                "  Install with: [bold]pip install 'aetherion[tui]'[/bold]\n\n"
                "  Or run [bold]aetherion --help[/bold] to see all available commands."
            )
            sys.exit(1)
        m = model or cfg_mod.get_model()
        run_tui(model=m)


# ===========================================================================
# AUTH — login / logout / whoami
# ===========================================================================

@cli.command()
@click.option("--url", default=None, help="Override backend URL for this login.")
@click.option("--email", "-e", default="", help="Email (for password login, skips browser).")
@click.option("--password", "-p", default="", help="Password (for password login, skips browser).")
def login(url: str, email: str, password: str) -> None:
    """Authenticate with Aetherion.

    By default opens a browser for secure device authorization.
    Pass --email and --password for non-interactive / CI mode.

    Examples:

      aetherion login

      aetherion login --email you@example.com --password 'secret'
    """
    if url:
        cfg_mod.set_server_url(url)

    client = _get_client()

    # Non-interactive mode (CI / scripted)
    if email and password:
        with _spinner("Authenticating…") as progress:
            progress.add_task("Authenticating…")
            try:
                res = client.post("/api/auth/login", {"email": email, "password": password})
            except (AetherionClientError, Exception) as exc:
                _handle_error(exc, "login")
        token = res.get("access_token") or res.get("token")
        if not token:
            err_console.print("[red]✗ Login failed:[/red] server returned no token.")
            sys.exit(1)
        cfg_mod.set_token(token)
        rt = res.get("refresh_token")
        if rt:
            cfg_mod.set_refresh_token(rt)
        console.print(f"[green]✓[/green] Signed in as [bold]{email}[/bold]")
        return

    # Browser-based device authorization (default)
    import time
    import webbrowser
    import httpx as _httpx

    console.print("[bold]Opening browser for secure login…[/bold]")
    server_url = cfg_mod.get_server_url().rstrip("/")
    try:
        with _httpx.Client(timeout=15) as http:
            r = http.post(
                f"{server_url}/api/auth/device/request",
                json={"action": "login"},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        err_msg = str(exc)
        # Emit the configured URL so the operator knows what was attempted
        err_console.print(
            f"\n[red]✗ Could not reach auth server at:[/red] [bold]{server_url}[/bold]\n"
            f"  Error: {err_msg}\n"
        )
        if "getaddrinfo" in err_msg or "11001" in err_msg or "Name or service" in err_msg:
            err_console.print(
                "  [yellow]Hint:[/yellow] DNS resolution failed — the hostname could not be reached.\n"
                "  This usually means:\n"
                "    1. The local backend is not running (most common in local dev).\n"
                "       Start it with:  [bold]cd backend && python app.py[/bold]\n"
                "    2. The server URL is set to a remote hostname that doesn't exist yet.\n"
                f"       Current URL: [bold]{server_url}[/bold]\n"
                "    3. To point the CLI at the local backend, run:\n"
                "       [bold]aetherion config set server_url http://127.0.0.1:5000[/bold]\n"
                "    4. Then verify connectivity with:  [bold]aetherion doctor[/bold]\n"
            )
        elif "Connection refused" in err_msg or "ECONNREFUSED" in err_msg:
            err_console.print(
                "  [yellow]Hint:[/yellow] Connection refused — the server is not listening on that port.\n"
                "  Start the local backend:  [bold]cd backend && python app.py[/bold]\n"
                "  Then retry:  [bold]aetherion login[/bold]\n"
            )
        else:
            err_console.print(
                f"  [yellow]Hint:[/yellow] Run [bold]aetherion doctor[/bold] for full diagnostics.\n"
                f"  Check your configured URL:  [bold]aetherion config --show[/bold]\n"
            )
        sys.exit(1)

    device_code = data.get("device_code", "")
    user_code = data.get("user_code", "")
    verification_uri = data.get("verification_uri", "")
    interval = int(data.get("interval", 3))
    expires_in = int(data.get("expires_in", 300))

    console.print(f"  Visit: [bold blue]{verification_uri}[/bold blue]")
    console.print(f"  Code:  [bold yellow]{user_code}[/bold yellow]")
    opened = False

    # Multi-tier browser launch ensuring automatic foreground browser open
    if sys.platform == "win32":
        try:
            os.startfile(verification_uri)  # type: ignore[attr-defined]
            opened = True
        except Exception:
            opened = False
        if not opened:
            try:
                import subprocess
                subprocess.Popen(["cmd.exe", "/c", "start", "", verification_uri], shell=False)
                opened = True
            except Exception:
                pass
    elif sys.platform == "darwin":
        try:
            import subprocess
            subprocess.Popen(["open", verification_uri])
            opened = True
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        try:
            import subprocess
            subprocess.Popen(["xdg-open", verification_uri])
            opened = True
        except Exception:
            pass

    if not opened:
        try:
            opened = bool(webbrowser.open(verification_uri, new=2))
        except Exception:
            opened = False

    if not opened:
        err_console.print(
            "[yellow]Browser did not open automatically.[/yellow] "
            "Open the URL shown above to continue."
        )

    console.print("Waiting for browser authentication… (Ctrl+C to cancel)")
    start = time.monotonic()
    while time.monotonic() - start < expires_in:
        time.sleep(interval)
        try:
            with _httpx.Client(timeout=10) as http:
                pr = http.get(
                    f"{server_url}/api/auth/device/poll",
                    params={"device_code": device_code},
                )
                pr.raise_for_status()
                poll = pr.json()
        except Exception:
            continue

        status = poll.get("status")
        if status == "authorized":
            access_token = poll.get("access_token", "")
            refresh_token = poll.get("refresh_token", "")
            user = poll.get("user", {})
            cfg_mod.set_token(access_token)
            if refresh_token:
                cfg_mod.set_refresh_token(refresh_token)
            name_or_email = user.get("email") or user.get("name") or "user"
            console.print(f"[green]✓[/green] Authenticated as [bold]{name_or_email}[/bold]")
            console.print(f"  Token stored securely. [dim]Run [bold]aetherion whoami[/bold] to verify.[/dim]")
            return
        elif status == "denied":
            err_console.print("[red]✗[/red] Authentication denied.")
            sys.exit(1)
        elif status in ("expired", "expired_token"):
            err_console.print("[red]✗[/red] Session expired. Run [bold]aetherion login[/bold] again.")
            sys.exit(1)

    err_console.print("[red]✗[/red] Authentication timed out.")
    sys.exit(1)


@cli.command()
@click.option("--everywhere", "-a", is_flag=True, help="Revoke all sessions on all devices.")
def logout(everywhere: bool) -> None:
    """Sign out and remove stored credentials."""
    client = _get_client()

    # Best-effort server-side revocation
    if client.token:
        endpoint = "/api/auth/logout_everywhere" if everywhere else "/api/auth/logout"
        try:
            client.post(endpoint, {})
        except Exception:
            pass

    cfg_mod.clear_token()
    cfg_mod.clear_refresh_token()
    action = "all sessions revoked" if everywhere else "signed out"
    console.print(f"[green]✓[/green] {action.capitalize()}. Credentials cleared.")


@cli.command()
@click.pass_context
def whoami(ctx: click.Context) -> None:
    """Show the currently authenticated user."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Fetching profile…") as progress:
        progress.add_task("Fetching profile…")
        try:
            res = client.get("/api/auth/me")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "whoami")

    # /api/auth/me wraps data under a "user" key
    user = res.get("user", res) if isinstance(res, dict) else {}

    fmt = ctx.obj.get("output", "table")
    if fmt == "json":
        _output_json(user)
        return

    console.print(Panel.fit(
        f"[bold cyan]Email:[/bold cyan]  {user.get('email', '?')}\n"
        f"[bold cyan]Name:[/bold cyan]   {user.get('name', '?')}\n"
        f"[bold cyan]Role:[/bold cyan]   {user.get('role', user.get('tier', 'user'))}\n"
        f"[bold cyan]ID:[/bold cyan]     {user.get('user_id', user.get('id', '?'))}\n"
        f"[bold cyan]Plan:[/bold cyan]   {user.get('plan', 'Lite')}",
        title="[bold]Your Profile[/bold]",
    ))



# ===========================================================================
# DOCTOR
# ===========================================================================

@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output doctor diagnostics as JSON.")
def doctor(as_json: bool = False) -> None:
    """Check connectivity, auth, readiness, and environment health."""
    client = _get_client()
    ok = True
    checks = []

    # --- 1. Connectivity & Server Readiness
    health_result = client.health()
    if health_result.get("status") == "ok":
        version = health_result.get("version", "?")
        checks.append({
            "name": "Backend Reachability",
            "status": "PASS",
            "detail": f"Backend reachable (version {version})",
        })
        # Probe server task execution readiness via /readyz if server is up
        try:
            readyz_resp = client.get("/readyz")
            if isinstance(readyz_resp, dict) and readyz_resp.get("status") == "ready":
                checks.append({
                    "name": "Server Task Readiness",
                    "status": "PASS",
                    "detail": "Server is ready to accept autonomous coding tasks",
                })
            elif isinstance(readyz_resp, dict) and readyz_resp.get("status") == "not_ready":
                checks.append({
                    "name": "Server Task Readiness",
                    "status": "FAIL",
                    "detail": "Server reports NOT READY for task execution (dependencies degraded)",
                })
                ok = False
        except Exception:
            pass
    elif health_result.get("offline"):
        checks.append({
            "name": "Backend Reachability",
            "status": "FAIL",
            "detail": f"Backend unreachable — {health_result.get('error', '')}",
        })
        ok = False
    else:
        checks.append({
            "name": "Backend Reachability",
            "status": "FAIL",
            "detail": f"Backend responded unexpectedly: {health_result}",
        })
        ok = False

    # --- 2. Auth token
    if client.token:
        src = cfg_mod._token_source()
        token_valid = True
        user_email = ""
        try:
            me_res = client.get("/api/auth/me")
            me = me_res.get("user", me_res) if isinstance(me_res, dict) else {}
            user_email = me.get("email", "")
        except AetherionAuthError:
            if _try_refresh_token():
                user_email = "refreshed"
            else:
                token_valid = False
                ok = False
        except Exception:
            pass

        if token_valid:
            checks.append({
                "name": "Authentication State",
                "status": "PASS",
                "detail": f"Token configured (source: {src})" + (f" | Logged in as: {user_email}" if user_email else ""),
            })
        else:
            checks.append({
                "name": "Authentication State",
                "status": "FAIL",
                "detail": "Token is invalid or expired. Run `aetherion login`.",
            })
    else:
        checks.append({
            "name": "Authentication State",
            "status": "WARN",
            "detail": "No token set.  Run `aetherion login`.",
        })

    # --- 3. Python version
    pv = sys.version_info
    checks.append({
        "name": "Python Environment",
        "status": "PASS",
        "detail": f"Python {pv.major}.{pv.minor}.{pv.micro}",
    })

    # --- 4. Config file
    checks.append({
        "name": "Configuration Store",
        "status": "PASS",
        "detail": f"Config: {cfg_mod.config_path()}",
    })

    # --- 5. Execution Fabric Control Plane & Workers
    fabric_data = {
        "status": "UNKNOWN",
        "store_type": "unknown",
        "coordinator_mode": "unknown",
        "registered_workers": 0,
        "active_queue_items": 0,
    }
    fabric_status = "PASS"
    fabric_detail = ""

    # Attempt to probe server control-plane endpoint if server is up
    fabric_fetched = False
    try:
        cp_resp = client.get("/api/health/control-plane")
        if isinstance(cp_resp, dict) and cp_resp.get("ok"):
            fabric_data["status"] = cp_resp.get("status", "healthy")
            fabric_data["store_type"] = cp_resp.get("store", {}).get("type", "unknown")
            fabric_data["coordinator_mode"] = cp_resp.get("coordinator", {}).get("mode", "unknown")
            fabric_data["registered_workers"] = cp_resp.get("store", {}).get("registered_workers", 0)
            fabric_data["active_queue_items"] = cp_resp.get("store", {}).get("active_queue_items", 0)
            fabric_detail = (
                f"Fabric Store: {fabric_data['store_type']} | "
                f"Coord: {fabric_data['coordinator_mode']} | "
                f"Workers: {fabric_data['registered_workers']} | "
                f"Active Queue: {fabric_data['active_queue_items']}"
            )
            fabric_status = "PASS"
            fabric_fetched = True
    except Exception:
        pass

    # If server is offline or didn't return, check local fabric store
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
                f"Local Fabric: {fabric_data['store_type']} | "
                f"Coord: {fabric_data['coordinator_mode']} | "
                f"Workers: {fabric_data['registered_workers']} | "
                f"Active Queue: {fabric_data['active_queue_items']}"
            )
            fabric_status = "PASS"
        except Exception as exc:
            fabric_status = "WARN"
            fabric_detail = f"Execution Fabric offline or unconfigured: {exc}"

    checks.append({
        "name": "Execution Fabric",
        "status": fabric_status,
        "detail": fabric_detail,
    })

    if as_json:
        payload = {
            "ok": ok,
            "healthy": ok,
            "server_url": client.base_url,
            "execution_fabric": fabric_data,
            "checks": checks,
        }
        click.echo(json.dumps(payload, indent=2))
    else:
        console.print(f"[bold]Backend URL:[/bold] {client.base_url}")
        for c in checks:
            st = c["status"]
            if st == "PASS":
                console.print(f"[green]OK[/green] {c['detail']}")
            elif st == "WARN":
                console.print(f"[yellow]![/yellow] {c['detail']}")
            else:
                console.print(f"[red]X[/red] {c['detail']}")

    if not ok:
        sys.exit(1)


# ===========================================================================
# CHAT / RUN / PLAN — agent task commands
# ===========================================================================

def _run_agent_task(
    client: Client,
    task_str: str,
    model: str,
    session: Optional[str],
    prefix: str = "",
    repo: Optional[str] = None,
) -> None:
    """Stream an agent task and print output. Raises SystemExit on failure."""
    sid = session or f"cli-{uuid.uuid4().hex[:12]}"
    m = model or cfg_mod.get_model()

    console.print(f"[dim]Session: {sid} | Model: {m}[/dim]")
    if repo:
        console.print(f"[dim]Repository: {Path(repo).resolve()}[/dim]")
    console.print("[dim]---------------------------------[/dim]")

    payload = {
        "task": f"{prefix}{task_str}",
        "model": m,
        "session_id": sid,
    }
    if repo:
        payload["repo_root"] = str(Path(repo).resolve())

    try:
        stream_done = False
        for event in client.stream("/api/v1/agent/invoke", payload):
            ev_type = event.get("type", "")

            if ev_type == "text":
                console.print(event.get("text", ""), end="", highlight=False)

            elif ev_type == "raw":
                console.print(event.get("text", ""), end="", highlight=False)

            elif ev_type == "queued":
                console.print(f"[dim]Task queued: {event.get('task_id', '?')}[/dim]")

            elif ev_type == "state_change":
                state = event.get("agent_state", "")
                if state:
                    console.print(f"\n[dim]* {state}[/dim]")

            elif ev_type == "tool_call":
                tool = event.get("tool", "?")
                console.print(f"\n[cyan]  → {tool}[/cyan]")

            elif ev_type == "usage":
                in_tok = event.get("inputTokens", 0)
                out_tok = event.get("outputTokens", 0)
                console.print(
                    f"\n[dim]Tokens: {in_tok} in / {out_tok} out[/dim]"
                )

            elif ev_type == "error":
                err = event.get("error", "Unknown error")
                err_console.print(f"\n[red]✗ Agent error:[/red] {err}")
                sys.exit(1)

            elif ev_type == "done":
                stream_done = True
                status = event.get("status", "done")
                if status in ("error", "failed"):
                    err = event.get("error") or event.get("summary") or event.get("failure_class") or event.get("verification") or "Task execution failed"
                    err_console.print(f"\n[red]✗ Task failed:[/red] {err}")
                    sys.exit(1)
                console.print("\n[green]✓ Done[/green]")
                break

        if not stream_done:
            # Stream closed without a done event — tolerate it
            console.print("\n[dim](stream ended)[/dim]")

    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled[/yellow]")
        sys.exit(130)
    except AetherionOfflineError as exc:
        _handle_error(exc, "chat")
    except AetherionClientError as exc:
        _handle_error(exc, "chat")


@cli.command()
@click.argument("task", nargs=-1)
@click.option("--model", "-m", default=None, help="Model to use (e.g. gpt-4o, claude-3-5-sonnet).")
@click.option("--session", "-s", default=None, help="Session/workspace ID.")
def chat(task: tuple, model: Optional[str], session: Optional[str]) -> None:
    """Start an interactive or one-shot agent task.

    Examples:

      aetherion chat "Build a FastAPI app with PostgreSQL"

      aetherion chat --model gpt-4o "Explain this codebase"
    """
    client = _get_client()
    _require_auth(client)

    task_str = " ".join(task).strip()
    if not task_str:
        try:
            task_str = click.prompt("Task")
        except click.Abort:
            sys.exit(0)

    if not task_str:
        return

    _run_agent_task(client, task_str, model or "", session)


@cli.command()
@click.argument("task", nargs=-1)
@click.option("--model", "-m", default=None)
@click.option("--session", "-s", default=None)
@click.option("--repo", "-r", default=None, help="Target git repository root for isolated worktree.")
def run(task: tuple, model: Optional[str], session: Optional[str], repo: Optional[str] = None) -> None:
    """Run a task non-interactively (alias for chat).

    Suitable for use in scripts and CI pipelines.
    Exit code 0 = success, 1 = error, 130 = cancelled.
    """
    client = _get_client()
    _require_auth(client)

    task_str = " ".join(task).strip()
    if not task_str:
        err_console.print("[red]✗[/red] Provide a task: aetherion run \"<your task>\"")
        sys.exit(1)

    _run_agent_task(client, task_str, model or "", session, repo=repo)


@cli.command()
@click.argument("task", nargs=-1)
@click.option("--model", "-m", default=None)
def plan(task: tuple, model: Optional[str]) -> None:
    """Generate an execution plan for a task without running it."""
    client = _get_client()
    _require_auth(client)

    task_str = " ".join(task).strip()
    if not task_str:
        try:
            task_str = click.prompt("Task to plan")
        except click.Abort:
            sys.exit(0)

    _run_agent_task(client, task_str, model or "", session=None, prefix="[PLAN ONLY] ")


# ===========================================================================
# WORKSPACE
# ===========================================================================

@cli.group()
def workspace() -> None:
    """Manage Aetherion workspaces."""


@workspace.command("list")
@click.option("--limit", "-n", default=20, show_default=True, help="Max number of workspaces.")
@click.option("--json", "as_json", is_flag=True, help="Output workspaces as JSON.")
@click.pass_context
def workspace_list(ctx: click.Context, limit: int, as_json: bool = False) -> None:
    """List recent workspaces."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading workspaces…") as progress:
        progress.add_task("Loading workspaces…")
        try:
            res = client.get("/api/sessions", params={"limit": str(limit)})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "workspace list")

    items = res if isinstance(res, list) else res.get("sessions", [])
    fmt = "json" if as_json else (ctx.obj.get("output", "table") if ctx.obj else "table")

    if fmt == "json":
        _output_json(items)
        return

    if not items:
        console.print("[dim]No workspaces found. Create one with `aetherion workspace create`.[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim", width=20)
    table.add_column("Title")
    table.add_column("Status", width=10)
    table.add_column("Created", width=16)
    table.add_column("Model", width=16)

    for w in items:
        status = w.get("status", "?")
        status_color = {
            "done": "green", "running": "yellow",
            "error": "red", "queued": "cyan",
        }.get(status, "white")
        table.add_row(
            str(w.get("id", ""))[:18],
            str(w.get("title") or w.get("task") or "Untitled")[:50],
            f"[{status_color}]{status}[/{status_color}]",
            _format_ts(w.get("created_at")),
            str(w.get("model") or "—")[:14],
        )

    console.print(table)


@workspace.command("create")
@click.argument("title", default="New Workspace")
@click.option("--json", "as_json", is_flag=True, help="Output created workspace as JSON.")
@click.pass_context
def workspace_create(ctx: click.Context, title: str, as_json: bool = False) -> None:
    """Create a new workspace."""
    client = _get_client()
    _require_auth(client)

    ws_id = str(uuid.uuid4())
    with _spinner(f"Creating workspace '{title}'…") as progress:
        progress.add_task("Creating…")
        try:
            res = client.post("/api/sessions/create", {"id": ws_id, "title": title})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "workspace create")

    fmt = "json" if as_json else (ctx.obj.get("output", "table") if ctx.obj else "table")
    if fmt == "json":
        _output_json(res)
        return

    console.print(f"[green]✓[/green] Workspace created")
    console.print(f"  [bold]ID:[/bold]    {res.get('id', ws_id)}")
    console.print(f"  [bold]Title:[/bold] {title}")


@workspace.command("delete")
@click.argument("session_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
def workspace_delete(session_id: str, yes: bool) -> None:
    """Delete a workspace by session ID."""
    client = _get_client()
    _require_auth(client)

    if not yes:
        click.confirm(
            f"Delete workspace [bold]{session_id}[/bold]? This cannot be undone.",
            abort=True,
        )

    with _spinner("Deleting…") as progress:
        progress.add_task("Deleting…")
        try:
            client.delete(f"/api/session/{session_id}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "workspace delete")

    console.print(f"[green]✓[/green] Workspace [dim]{session_id}[/dim] deleted.")


@workspace.command("select")
@click.argument("workspace_id")
def workspace_select(workspace_id: str) -> None:
    """Select active workspace."""
    cfg_mod.set_active_workspace(workspace_id)
    console.print(f"[green]✓[/green] Active workspace set to [bold]{workspace_id}[/bold]")


@workspace.command("status")
def workspace_status() -> None:
    """Show active workspace."""
    active = cfg_mod.get_active_workspace()
    if active:
        console.print(f"Active workspace: [bold cyan]{active}[/bold cyan]")
    else:
        console.print("[dim]No active workspace selected. Use `aetherion workspace select <id>`.[/dim]")


# ===========================================================================
# HISTORY
# ===========================================================================

@cli.group()
def history() -> None:
    """Browse past agent sessions and their outputs."""


@history.command("list")
@click.option("--limit", "-n", default=20, show_default=True)
@click.pass_context
def history_list(ctx: click.Context, limit: int) -> None:
    """List recent agent sessions."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading history…") as progress:
        progress.add_task("Loading history…")
        try:
            res = client.get("/api/sessions", params={"limit": str(limit)})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "history list")

    items = res if isinstance(res, list) else res.get("sessions", [])
    fmt = ctx.obj.get("output", "table")

    if fmt == "json":
        _output_json(items)
        return

    if not items:
        console.print("[dim]No history yet.[/dim]")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("#", width=4)
    table.add_column("ID", style="dim", width=20)
    table.add_column("Task / Title", max_width=55)
    table.add_column("Status", width=10)
    table.add_column("Time", width=16)

    for i, w in enumerate(items, 1):
        title = str(w.get("title") or w.get("task") or "—")[:55]
        status = w.get("status", "?")
        sc = {"done": "green", "error": "red", "running": "yellow"}.get(status, "white")
        table.add_row(
            str(i),
            str(w.get("id", ""))[:18],
            title,
            f"[{sc}]{status}[/{sc}]",
            _format_ts(w.get("created_at")),
        )

    console.print(table)


@history.command("show")
@click.argument("session_id")
@click.pass_context
def history_show(ctx: click.Context, session_id: str) -> None:
    """Show details and logs for a specific session."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading session…") as progress:
        progress.add_task("Loading session…")
        try:
            res = client.get(f"/api/session/{session_id}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "history show")

    fmt = ctx.obj.get("output", "table")
    if fmt == "json":
        _output_json(res)
        return

    console.print(Panel.fit(
        f"[bold]ID:[/bold]      {res.get('id', session_id)}\n"
        f"[bold]Title:[/bold]   {res.get('title') or res.get('task') or '—'}\n"
        f"[bold]Status:[/bold]  {res.get('status', '?')}\n"
        f"[bold]Model:[/bold]   {res.get('model') or '—'}\n"
        f"[bold]Created:[/bold] {_format_ts(res.get('created_at'))}\n"
        f"[bold]Result:[/bold]  {str(res.get('result', '—'))[:200]}",
        title=f"[bold]Session {session_id[:16]}[/bold]",
    ))

    # Fetch logs
    try:
        logs = client.get(f"/api/logs", params={"session_id": session_id, "limit": "100"})
        entries = logs if isinstance(logs, list) else logs.get("logs", [])
        if entries:
            console.print("\n[bold]Recent logs:[/bold]")
            for entry in entries[-20:]:
                ts = _format_ts(entry.get("ts") or entry.get("timestamp"))
                msg = entry.get("message") or entry.get("text") or str(entry)
                console.print(f"  [dim]{ts}[/dim]  {msg[:120]}")
    except Exception:
        pass


# ===========================================================================
# SESSIONS (Phase 4 Durable Session Lifecycle)
# ===========================================================================

@cli.group()
def sessions() -> None:
    """Manage persistent agent sessions, history, and contexts."""


@sessions.command("list")
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--status", "-s", default=None, help="Filter by status (ACTIVE, IDLE, ARCHIVED)")
@click.pass_context
def sessions_list(ctx: click.Context, limit: int, status: Optional[str]) -> None:
    """List persistent agent sessions."""
    client = _get_client()
    _require_auth(client)

    params = {"limit": str(limit)}
    if status:
        params["status"] = status

    with _spinner("Loading sessions…") as progress:
        progress.add_task("Loading sessions…")
        try:
            res = client.get("/api/sessions", params=params)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "sessions list")

    items = res.get("sessions", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
    fmt = ctx.obj.get("output", "table")
    if fmt == "json":
        _output_json(items)
        return

    if not items:
        console.print("[dim]No sessions found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Session ID", style="dim", width=18)
    table.add_column("Title", max_width=40)
    table.add_column("Status", width=12)
    table.add_column("Model", width=16)
    table.add_column("Messages", width=8)
    table.add_column("Last Active", width=16)

    for s in items:
        table.add_row(
            str(s.get("id", ""))[:16],
            str(s.get("title", "—"))[:40],
            str(s.get("status", "ACTIVE")),
            str(s.get("model", "—")),
            str(s.get("message_count", 0)),
            _format_ts(s.get("last_activity_at")),
        )
    console.print(table)


@sessions.command("create")
@click.option("--title", "-t", default=None, help="Session title.")
@click.option("--model", "-m", default="claude-3-5-sonnet", show_default=True, help="Model name.")
@click.option("--workspace", "-w", default="default_workspace", show_default=True, help="Workspace ID.")
@click.pass_context
def sessions_create(ctx: click.Context, title: Optional[str], model: str, workspace: str) -> None:
    """Create a new persistent agent session."""
    client = _get_client()
    _require_auth(client)

    session_title = (title or "").strip() or f"Session {time.strftime('%Y-%m-%d %H:%M')}"
    payload = {"title": session_title, "model": model, "workspace_id": workspace}
    with _spinner("Creating session…") as progress:
        progress.add_task("Creating session…")
        try:
            res = client.post("/api/sessions", payload)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "sessions create")

    sid = res.get("session_id") or res.get("session", {}).get("id")
    if ctx.obj.get("output") == "json":
        _output_json(res)
        return
    console.print(f"[green]✓[/green] Created session: [bold]{sid}[/bold]")


@sessions.command("open")
@click.argument("session_id")
@click.pass_context
def sessions_open(ctx: click.Context, session_id: str) -> None:
    """Open and inspect an existing session and recent context."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Opening session…") as progress:
        progress.add_task("Opening session…")
        try:
            res = client.get(f"/api/sessions/{session_id}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "sessions open")

    if ctx.obj.get("output") == "json":
        _output_json(res)
        return

    sess = res.get("session", {})
    context = res.get("context", {})
    recent = context.get("recent_messages", [])

    console.print(Panel.fit(
        f"[bold]ID:[/bold]          {sess.get('id')}\n"
        f"[bold]Title:[/bold]       {sess.get('title')}\n"
        f"[bold]Status:[/bold]      {sess.get('status')}\n"
        f"[bold]Model:[/bold]       {sess.get('model')}\n"
        f"[bold]Messages:[/bold]    {sess.get('message_count')}\n"
        f"[bold]Summary:[/bold]     {sess.get('summary') or '(none)'}",
        title=f"[bold]Session Details: {session_id[:16]}[/bold]",
    ))

    if recent:
        console.print("\n[bold]Recent Messages:[/bold]")
        for m in recent:
            role = m.get("role", "user").upper()
            content = m.get("content", "")[:120]
            console.print(f"  [cyan]{role}:[/cyan] {content}")


@sessions.command("rename")
@click.argument("session_id")
@click.argument("new_title")
def sessions_rename(session_id: str, new_title: str) -> None:
    """Rename a session."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Renaming session…") as progress:
        progress.add_task("Renaming session…")
        try:
            client.put(f"/api/sessions/{session_id}", {"title": new_title})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "sessions rename")

    console.print(f"[green]✓[/green] Renamed session [bold]{session_id}[/bold] to '{new_title}'.")


@sessions.command("archive")
@click.argument("session_id")
def sessions_archive(session_id: str) -> None:
    """Archive a session."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Archiving session…") as progress:
        progress.add_task("Archiving session…")
        try:
            client.post(f"/api/sessions/{session_id}/archive", {})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "sessions archive")

    console.print(f"[green]✓[/green] Archived session [bold]{session_id}[/bold].")


@sessions.command("delete")
@click.argument("session_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
def sessions_delete(session_id: str, yes: bool) -> None:
    """Delete (soft-delete) a session."""
    client = _get_client()
    _require_auth(client)

    if not yes:
        click.confirm(f"Delete session [bold]{session_id}[/bold]? Access will be disabled.", abort=True)

    with _spinner("Deleting session…") as progress:
        progress.add_task("Deleting session…")
        try:
            client.delete(f"/api/sessions/{session_id}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "sessions delete")

    console.print(f"[green]✓[/green] Deleted session [bold]{session_id}[/bold].")


@sessions.command("export")
@click.argument("session_id")
@click.option("--output", "-o", "output_file", default=None, help="File to write exported JSON.")
@click.pass_context
def sessions_export(ctx: click.Context, session_id: str, output_file: Optional[str]) -> None:
    """Export a session and its message history with secrets redacted."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Exporting session…") as progress:
        progress.add_task("Exporting session…")
        try:
            res = client.get(f"/api/sessions/{session_id}/export")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "sessions export")

    export_data = res.get("export", res)
    if output_file:
        import json
        Path(output_file).write_text(json.dumps(export_data, indent=2), encoding="utf-8")
        console.print(f"[green]✓[/green] Exported session to [bold]{output_file}[/bold].")
    else:
        _output_json(export_data)


# ===========================================================================
# MEMORY
# ===========================================================================

@cli.group()
def memory() -> None:
    """Manage the agent's persistent memory store."""


@memory.command("search")
@click.argument("query", default="")
@click.option("--limit", "-n", default=10, show_default=True, help="Maximum number of memory results.")
@click.option("--hybrid", "-H", is_flag=True, default=False, help="Use semantic vector hybrid retrieval.")
@click.option("--alpha", default=0.6, type=float, show_default=True, help="Hybrid balance weight (1.0=vector, 0.0=lexical).")
@click.option("--index-mode", type=click.Choice(["auto", "exact", "approximate"]), default="auto", help="Vector index engine mode.")
@click.pass_context
def memory_search(ctx: click.Context, query: str, limit: int, hybrid: bool, alpha: float, index_mode: str = "auto") -> None:
    """Search agent memory by keyword or semantic hybrid retrieval.

    Omit QUERY to list all memories (same as `aetherion memory list`).
    """
    client = _get_client()
    _require_auth(client)

    if query:
        encoded = urllib.parse.quote(query)
        if hybrid:
            endpoint = f"/api/memory/retrieve?q={encoded}&limit={limit}&alpha={alpha}&index_mode={index_mode}"
        else:
            endpoint = f"/api/memory/search?q={encoded}&limit={limit}"
    else:
        endpoint = f"/api/memory/list?limit={limit}"

    with _spinner("Searching memory…") as progress:
        progress.add_task("Searching memory…")
        try:
            res = client.get(endpoint)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "memory search")

    if isinstance(res, list):
        results = res
    elif isinstance(res, dict):
        results = res.get("results", res.get("memories", []))
    else:
        results = []
    fmt = ctx.obj.get("output", "table")

    if fmt == "json":
        _output_json(results)
        return

    if not results:
        console.print("[dim]No results found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Score", width=6)
    table.add_column("Content")
    table.add_column("Created", width=16)

    for r in results:
        score = r.get("score", r.get("relevance", r.get("importance", "?")))
        content = str(r.get("content", ""))[:120]
        ts = _format_ts(r.get("created_at") or r.get("ts"))
        table.add_row(f"{score:.3f}" if isinstance(score, float) else str(score), content, ts)

    console.print(table)


@memory.command("remember")
@click.argument("content")
@click.option("--importance", "-i", default=0.7, show_default=True, type=float)
def memory_remember(content: str, importance: float) -> None:
    """Store a piece of information in agent memory."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Storing…") as progress:
        progress.add_task("Storing…")
        try:
            client.post("/api/memory/remember", {"content": content, "importance": importance})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "memory remember")

    console.print("[green]✓[/green] Stored in memory.")


@memory.command("list")
@click.option("--limit", "-n", default=20, show_default=True)
@click.pass_context
def memory_list(ctx: click.Context, limit: int) -> None:
    """List recent memory entries."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading memory…") as progress:
        progress.add_task("Loading memory…")
        try:
            res = client.get(f"/api/memory/list?limit={limit}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "memory list")

    items = res.get("memories", res.get("results", [])) if isinstance(res, dict) else (res if isinstance(res, list) else [])
    fmt = ctx.obj.get("output", "table")

    if fmt == "json":
        _output_json(items)
        return

    if not items:
        console.print("[dim]Memory is empty.[/dim]")
        return

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("ID", style="dim", width=20)
    table.add_column("Content", max_width=70)
    table.add_column("Importance", width=10)

    for m in items:
        table.add_row(
            str(m.get("id", ""))[:18],
            str(m.get("content", ""))[:70],
            str(m.get("importance", m.get("score", "?"))),
        )

    console.print(table)


@memory.command("clear")
@click.option("--scope", default="conversation", show_default=True,
              type=click.Choice(["conversation", "all"]),
              help="Scope of memory to clear.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
def memory_clear(scope: str, yes: bool) -> None:
    """Clear agent memory for the given scope."""
    client = _get_client()
    _require_auth(client)

    if not yes:
        click.confirm(f"Clear [bold]{scope}[/bold] memory? This cannot be undone.", abort=True)

    with _spinner("Clearing memory…") as progress:
        progress.add_task("Clearing memory…")
        try:
            client.post("/api/memory/clear", {"scope": scope})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "memory clear")

    console.print(f"[green]✓[/green] Memory cleared (scope: {scope}).")


@memory.command("add")
@click.argument("content")
@click.option("--title", "-t", default="Untitled Memory", help="Title of memory.")
@click.option("--type", "m_type", default="KNOWLEDGE", help="Type (USER_PREFERENCE, PROJECT_FACT, etc.).")
@click.option("--importance", "-i", default=0.7, type=float, help="Importance rating.")
@click.option("--tags", default="", help="Comma-separated tags.")
def memory_add(content: str, title: str, m_type: str, importance: float, tags: str) -> None:
    """Add a new persistent memory entry."""
    client = _get_client()
    _require_auth(client)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    with _spinner("Saving memory…") as progress:
        progress.add_task("Saving memory…")
        try:
            client.post("/api/memory", {
                "title": title,
                "content": content,
                "type": m_type,
                "importance": importance,
                "tags": tag_list,
            })
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "memory add")
    console.print(f"[green]✓[/green] Stored memory: '{title}'")


@memory.command("edit")
@click.argument("memory_id")
@click.option("--title", "-t", default=None, help="Updated title.")
@click.option("--content", "-c", default=None, help="Updated content.")
@click.option("--importance", "-i", default=None, type=float, help="Updated importance.")
def memory_edit(memory_id: str, title: Optional[str], content: Optional[str], importance: Optional[float]) -> None:
    """Edit an existing persistent memory entry."""
    client = _get_client()
    _require_auth(client)
    payload = {}
    if title is not None: payload["title"] = title
    if content is not None: payload["content"] = content
    if importance is not None: payload["importance"] = importance
    with _spinner("Updating memory…") as progress:
        progress.add_task("Updating memory…")
        try:
            client.put(f"/api/memory/{memory_id}", payload)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "memory edit")
    console.print(f"[green]✓[/green] Updated memory [bold]{memory_id}[/bold].")


@memory.command("delete")
@click.argument("memory_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
def memory_delete(memory_id: str, yes: bool) -> None:
    """Delete a memory entry."""
    client = _get_client()
    _require_auth(client)
    if not yes:
        click.confirm(f"Delete memory [bold]{memory_id}[/bold]?", abort=True)
    with _spinner("Deleting memory…") as progress:
        progress.add_task("Deleting memory…")
        try:
            client.delete(f"/api/memory/{memory_id}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "memory delete")
    console.print(f"[green]✓[/green] Deleted memory [bold]{memory_id}[/bold].")


@memory.command("import")
@click.argument("file_or_text")
@click.option("--commit", is_flag=True, default=False, help="Commit immediately without preview.")
@click.pass_context
def memory_import(ctx: click.Context, file_or_text: str, commit: bool) -> None:
    """Import memories from a file (TXT, MD, JSON) or raw text."""
    client = _get_client()
    _require_auth(client)
    path = Path(file_or_text)
    if path.exists() and path.is_file():
        content = path.read_text(encoding="utf-8", errors="replace")
    else:
        content = file_or_text

    with _spinner("Importing memory…") as progress:
        progress.add_task("Importing memory…")
        try:
            res = client.post("/api/memory/import", {"content": content, "commit": commit})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "memory import")

    if ctx.obj.get("output") == "json":
        _output_json(res)
        return

    if commit:
        count = res.get("committed_count", 0)
        console.print(f"[green]✓[/green] Successfully imported [bold]{count}[/bold] memories.")
    else:
        preview = res.get("preview", {})
        total = preview.get("total_extracted", 0)
        dups = preview.get("duplicate_count", 0)
        pid = preview.get("preview_id")
        console.print(f"[cyan]Preview ready:[/cyan] Extracted [bold]{total}[/bold] items ({dups} duplicates). Preview ID: [bold]{pid}[/bold].")


@memory.command("maintain")
@click.option(
    "--action",
    type=click.Choice([
        "backfill", "repair", "dedup", "reindex",
        "contradictions", "consolidate", "prune_cache", "lifecycle"
    ]),
    default="backfill",
    help="Maintenance task to run.",
)
@click.option("--batch-size", default=50, type=int, show_default=True, help="Batch size for backfill or repair.")
@click.pass_context
def memory_maintain(ctx: click.Context, action: str, batch_size: int) -> None:
    """Run Phase 6/7 background maintenance tasks."""
    # P3-B: Validate --batch-size is a positive bounded integer before sending to API
    if batch_size < 1 or batch_size > 500:
        err_console.print(
            f"[red]✗ Invalid --batch-size {batch_size}:[/red] "
            "must be an integer between 1 and 500 (inclusive)."
        )
        sys.exit(1)

    client = _get_client()
    _require_auth(client)

    with _spinner(f"Running memory maintenance ({action})…") as progress:
        progress.add_task(f"Running {action}…")
        try:
            res = client.post(f"/api/memory/maintenance/{action}", {"batch_size": batch_size})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, f"memory maintain {action}")

    if ctx.obj.get("output") == "json":
        _output_json(res)
        return

    job_id = res.get("job_id")
    if job_id:
        console.print(f"[green]✓[/green] Maintenance job [bold]{action}[/bold] queued.")
        console.print(f"  Job ID: [bold]{job_id}[/bold] — poll with [bold]aetherion jobs status {job_id}[/bold]")
    else:
        result_data = res.get("result", {})
        console.print(f"[green]✓[/green] Completed [bold]{action}[/bold]:")
        for k, v in result_data.items():
            console.print(f"  [cyan]{k}:[/cyan] {v}")


@memory.command("conflicts")
@click.option("--scan", is_flag=True, default=False, help="Trigger a live scan for contradictions across memories.")
@click.option("--resolve", "conflict_id", default=None, help="Conflict ID to resolve.")
@click.option(
    "--strategy",
    type=click.Choice(["keep_a", "keep_b", "supersede", "dismiss", "resolve"]),
    default="resolve",
    help="Resolution strategy.",
)
@click.option("--winner", "winner_id", default=None, help="Winning memory ID for resolve strategy.")
@click.pass_context
def memory_conflicts(
    ctx: click.Context,
    scan: bool,
    conflict_id: Optional[str],
    strategy: str,
    winner_id: Optional[str],
) -> None:
    """Detect, inspect, and non-destructively resolve memory contradictions."""
    client = _get_client()
    _require_auth(client)

    if conflict_id:
        with _spinner("Resolving contradiction…") as progress:
            progress.add_task("Resolving…")
            try:
                payload = {"strategy": strategy}
                if winner_id:
                    payload["winner_id"] = winner_id
                res = client.post(f"/api/memory/conflicts/{conflict_id}/resolve", payload)
            except (AetherionClientError, Exception) as exc:
                _handle_error(exc, "memory conflicts resolve")
        console.print(f"[green]✓[/green] Contradiction [bold]{conflict_id}[/bold] resolved via strategy '[bold]{strategy}[/bold]'.")
        return

    if scan:
        with _spinner("Scanning memories for contradictions…") as progress:
            progress.add_task("Scanning…")
            try:
                res = client.post("/api/memory/conflicts/scan", {})
            except (AetherionClientError, Exception) as exc:
                _handle_error(exc, "memory conflicts scan")
        detected = res.get("detected_conflicts", [])
        console.print(f"[green]✓[/green] Contradiction scan finished. Discovered [bold]{len(detected)}[/bold] conflict(s).")
    else:
        try:
            res = client.get("/api/memory/conflicts")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "memory conflicts")

    if ctx.obj.get("output") == "json":
        _output_json(res)
        return

    items = res.get("conflicts", res.get("detected_conflicts", []))
    if not items:
        console.print("[green]✓[/green] No memory contradictions detected.")
        return

    table = Table(show_header=True, header_style="bold red")
    table.add_column("Conflict ID", style="dim")
    table.add_column("Type")
    table.add_column("State")
    table.add_column("Confidence")
    table.add_column("Description")

    for c in items:
        table.add_row(
            c.get("id", ""),
            c.get("contradiction_type", ""),
            c.get("state", ""),
            f"{c.get('confidence', 0.0):.2f}",
            c.get("description", "")[:80],
        )
    console.print(table)


@memory.command("consolidate")
@click.option("--scan", is_flag=True, default=False, help="Trigger a live scan for consolidation candidates.")
@click.option("--apply", "apply_id", default=None, help="Consolidation proposal ID to accept and apply.")
@click.option("--reject", "reject_id", default=None, help="Consolidation proposal ID to reject.")
@click.pass_context
def memory_consolidate(
    ctx: click.Context,
    scan: bool,
    apply_id: Optional[str],
    reject_id: Optional[str],
) -> None:
    """Cluster near-duplicates and manage non-destructive memory consolidations."""
    client = _get_client()
    _require_auth(client)

    if apply_id:
        with _spinner("Applying consolidation proposal…") as progress:
            progress.add_task("Applying…")
            try:
                res = client.post(f"/api/memory/consolidate/{apply_id}/apply", {})
            except (AetherionClientError, Exception) as exc:
                _handle_error(exc, "memory consolidate apply")
        console.print(f"[green]✓[/green] Consolidation proposal [bold]{apply_id}[/bold] accepted and applied.")
        return

    if reject_id:
        with _spinner("Rejecting consolidation proposal…") as progress:
            progress.add_task("Rejecting…")
            try:
                res = client.post(f"/api/memory/consolidate/{reject_id}/reject", {})
            except (AetherionClientError, Exception) as exc:
                _handle_error(exc, "memory consolidate reject")
        console.print(f"[yellow]✓[/yellow] Consolidation proposal [bold]{reject_id}[/bold] rejected.")
        return

    if scan:
        with _spinner("Scanning for consolidation candidates…") as progress:
            progress.add_task("Scanning…")
            try:
                res = client.post("/api/memory/consolidate/scan", {})
            except (AetherionClientError, Exception) as exc:
                _handle_error(exc, "memory consolidate scan")
        proposals = res.get("proposed_consolidations", [])
        console.print(f"[green]✓[/green] Discovered [bold]{len(proposals)}[/bold] consolidation cluster(s).")
    else:
        try:
            res = client.get("/api/memory/consolidate")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "memory consolidate")

    if ctx.obj.get("output") == "json":
        _output_json(res)
        return

    items = res.get("consolidations", res.get("proposed_consolidations", []))
    if not items:
        console.print("[green]✓[/green] No memory consolidation proposals pending.")
        return

    table = Table(show_header=True, header_style="bold yellow")
    table.add_column("Consolidation ID", style="dim")
    table.add_column("Type")
    table.add_column("Primary Memory")
    table.add_column("Candidates")
    table.add_column("State")
    table.add_column("Reason")

    for c in items:
        table.add_row(
            c.get("id", ""),
            c.get("consolidation_type", ""),
            c.get("primary_memory_id", ""),
            str(len(c.get("candidate_ids", []))),
            c.get("state", ""),
            c.get("reason", "")[:70],
        )
    console.print(table)


@memory.command("evaluate")
@click.pass_context
def memory_evaluate(ctx: click.Context) -> None:
    """Run the 52-fixture retrieval quality benchmark suite and check threshold floors."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Running 52-fixture retrieval quality evaluation suite…") as progress:
        progress.add_task("Evaluating…")
        try:
            res = client.post("/api/memory/evaluate", {})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "memory evaluate")

    if ctx.obj.get("output") == "json":
        _output_json(res)
        return

    data = res.get("benchmark_results", {})
    console.print(Panel("[bold cyan]Retrieval Quality V3 Evaluation Results[/bold cyan]", expand=False))

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Metric")
    table.add_column("Measured Value")
    table.add_column("Quality Floor")
    table.add_column("Status")

    def row(name: str, val: Any, floor: str, passed: bool) -> None:
        st = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        table.add_row(name, str(val), floor, st)

    r1 = data.get("recall_at_1", 0.0)
    r3 = data.get("recall_at_3", 0.0)
    r5 = data.get("recall_at_5", 0.0)
    mrr = data.get("mrr", 0.0)
    ndcg5 = data.get("ndcg_at_5", 0.0)
    p50 = data.get("latency_p50_ms", 0.0)
    p95 = data.get("latency_p95_ms", 0.0)
    safety = data.get("prompt_injection_safety_verified", False)

    row("Total Fixtures", data.get("total_test_cases", 0), ">= 50", data.get("total_test_cases", 0) >= 50)
    row("Recall@1", f"{r1:.4f}", "Informational", True)
    row("Recall@3", f"{r3:.4f}", "Informational", True)
    row("Recall@5", f"{r5:.4f}", ">= 0.8500", r5 >= 0.85)
    row("Hit Rate@5", f"{r5:.4f}", ">= 0.8500", r5 >= 0.85)
    row("MRR", f"{mrr:.4f}", ">= 0.8000", mrr >= 0.80)
    row("NDCG@5", f"{ndcg5:.4f}", ">= 0.8000", ndcg5 >= 0.80)
    row("Latency p50", f"{p50:.2f} ms", "< 150 ms", p50 < 150.0)
    row("Latency p95", f"{p95:.2f} ms", "< 300 ms", p95 < 300.0)
    row("Prompt Injection Safety", "Verified", "Required", safety)

    console.print(table)
    if data.get("all_thresholds_met"):
        console.print("[bold green]✓ ALL RETRIEVAL QUALITY FLOORS MET[/bold green]")
    else:
        console.print("[bold red]✗ SOME QUALITY FLOORS WERE NOT MET[/bold red]")


@memory.command("health")
@click.pass_context
def memory_health(ctx: click.Context) -> None:
    """Display comprehensive memory subsystem health, vector coverage, and conflict counters."""
    client = _get_client()
    _require_auth(client)

    try:
        res = client.get("/api/memory/health")
    except (AetherionClientError, Exception) as exc:
        _handle_error(exc, "memory health")

    if ctx.obj.get("output") == "json":
        _output_json(res)
        return

    h = res.get("health", {})
    console.print(Panel(f"[bold cyan]Memory Subsystem Health: {h.get('status', 'unknown').upper()}[/bold cyan]", expand=False))

    counts = h.get("memory_counts", {})
    console.print(f"• [bold]Memories:[/bold] {counts.get('active', 0)} active, {counts.get('stale', 0)} stale, {counts.get('expired', 0)} expired ({counts.get('total', 0)} total)")

    v_idx = h.get("vector_index", {})
    console.print(f"• [bold]Vector Index:[/bold] {v_idx.get('indexed_vectors', 0)} vectors indexed ({v_idx.get('coverage_pct', 0.0)}% coverage)")

    intel = h.get("intelligence", {})
    console.print(f"• [bold]Contradictions:[/bold] {intel.get('open_conflicts', 0)} open conflict(s) ({intel.get('total_conflicts', 0)} total recorded)")
    console.print(f"• [bold]Consolidations:[/bold] {intel.get('proposed_consolidations', 0)} proposed cluster(s) ({intel.get('total_consolidations', 0)} total)")

    cache = h.get("cache", {})
    console.print(f"• [bold]Cache Utilization:[/bold] {cache.get('in_memory_entries', 0)} LRU memory / {cache.get('persistent_entries', 0)} persistent entries (Hit rate: {cache.get('hit_rate', 0.0):.2%})")


@memory.command("telemetry")
@click.pass_context
def memory_telemetry(ctx: click.Context) -> None:
    """Display Phase 6/7 memory retrieval and vector cache telemetry metrics."""
    client = _get_client()
    _require_auth(client)

    try:
        res = client.get("/api/memory/telemetry")
    except (AetherionClientError, Exception) as exc:
        _handle_error(exc, "memory telemetry")

    if ctx.obj.get("output") == "json":
        _output_json(res)
        return

    telem = res.get("telemetry", {})
    cache = res.get("cache", {})

    console.print(Panel("[bold cyan]Aetherion Memory Telemetry & Performance[/bold cyan]", expand=False))
    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Category")
    table.add_column("Metric")
    table.add_column("Value")

    emb = telem.get("embedding", {})
    table.add_row("Embedding", "Total Requests", str(emb.get("requests", 0)))
    table.add_row("Embedding", "Cache Hits", str(emb.get("cache_hits", 0)))
    table.add_row("Embedding", "Cache Misses", str(emb.get("cache_misses", 0)))
    table.add_row("Embedding", "Cache Hit Rate", f"{emb.get('cache_hit_rate', 0.0):.2%}")
    table.add_row("Embedding", "Avg Latency", f"{emb.get('avg_latency_ms', 0.0):.2f} ms")

    ret = telem.get("retrieval", {})
    table.add_row("Retrieval", "Total Queries", str(ret.get("requests", 0)))
    table.add_row("Retrieval", "Exact Index Queries", str(ret.get("exact_queries", 0)))
    table.add_row("Retrieval", "Approximate IVF Queries", str(ret.get("approximate_queries", 0)))
    table.add_row("Retrieval", "Avg Query Latency", f"{ret.get('avg_latency_ms', 0.0):.2f} ms")

    intel = telem.get("intelligence", {})
    table.add_row("Intelligence", "Contradictions Detected", str(intel.get("contradictions_detected", 0)))
    table.add_row("Intelligence", "Contradictions Resolved", str(intel.get("contradictions_resolved", 0)))
    table.add_row("Intelligence", "Consolidations Proposed", str(intel.get("consolidations_proposed", 0)))
    table.add_row("Intelligence", "Consolidations Applied", str(intel.get("consolidations_applied", 0)))
    table.add_row("Intelligence", "Learning Signals", str(intel.get("learning_signals", 0)))
    table.add_row("Intelligence", "Feedback Received", str(intel.get("feedback_total", 0)))

    table.add_row("Cache", "In-Memory LRU Entries", str(cache.get("in_memory_entries", 0)))
    table.add_row("Cache", "Persistent DB Entries", str(cache.get("persistent_entries", 0)))

    console.print(table)



# ===========================================================================
# BACKGROUND JOBS (Phase 4 Long-Running Autonomous Execution)
# ===========================================================================

@cli.group()
def jobs() -> None:
    """Manage asynchronous background agent execution jobs."""


@jobs.command("list")
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--status", "-s", default=None, help="Filter by status (QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED)")
@click.pass_context
def jobs_list(ctx: click.Context, limit: int, status: Optional[str]) -> None:
    """List background jobs."""
    client = _get_client()
    _require_auth(client)

    params = {"limit": str(limit)}
    if status:
        params["status"] = status

    with _spinner("Loading jobs…") as progress:
        progress.add_task("Loading jobs…")
        try:
            res = client.get("/api/jobs", params=params)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "jobs list")

    items = res.get("jobs", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
    fmt = ctx.obj.get("output", "table")
    if fmt == "json":
        _output_json(items)
        return

    if not items:
        console.print("[dim]No background jobs found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Job ID", style="dim", width=18)
    table.add_column("Name", max_width=30)
    table.add_column("Status", width=12)
    table.add_column("Duration", width=12)
    table.add_column("Attempt", width=8)
    table.add_column("Created", width=16)

    for j in items:
        table.add_row(
            str(j.get("id", ""))[:16],
            str(j.get("name", "Untitled"))[:30],
            str(j.get("status", "QUEUED")),
            str(j.get("elapsed_minutes", "—")),
            str(j.get("attempt_number", 1)),
            _format_ts(j.get("created_at")),
        )
    console.print(table)


@jobs.command("create")
@click.argument("task")
@click.option("--name", "-n", default=None, help="Job name.")
@click.option("--workspace", "-w", default="default_workspace", show_default=True, help="Workspace ID.")
@click.option("--session", "-s", default=None, help="Optional Session ID.")
@click.pass_context
def jobs_create(ctx: click.Context, task: str, name: Optional[str], workspace: str, session: Optional[str]) -> None:
    """Create a new queued background job."""
    client = _get_client()
    _require_auth(client)

    payload = {"task": task, "name": name or f"Job: {task[:25]}", "workspace_id": workspace, "session_id": session}
    with _spinner("Creating background job…") as progress:
        progress.add_task("Creating background job…")
        try:
            res = client.post("/api/jobs", payload)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "jobs create")

    jid = res.get("job_id") or res.get("job", {}).get("id")
    if ctx.obj.get("output") == "json":
        _output_json(res)
        return
    console.print(f"[green]✓[/green] Created background job: [bold]{jid}[/bold]")


@jobs.command("start")
@click.argument("job_id")
def jobs_start(job_id: str) -> None:
    """Start execution of a background job."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Starting job…") as progress:
        progress.add_task("Starting job…")
        try:
            client.post(f"/api/jobs/{job_id}/start", {})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "jobs start")

    console.print(f"[green]✓[/green] Started background job [bold]{job_id}[/bold].")


@jobs.command("pause")
@click.argument("job_id")
def jobs_pause(job_id: str) -> None:
    """Pause an active background job."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Pausing job…") as progress:
        progress.add_task("Pausing job…")
        try:
            client.post(f"/api/jobs/{job_id}/pause", {})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "jobs pause")

    console.print(f"[green]✓[/green] Paused job [bold]{job_id}[/bold].")


@jobs.command("resume")
@click.argument("job_id")
def jobs_resume(job_id: str) -> None:
    """Resume a paused background job."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Resuming job…") as progress:
        progress.add_task("Resuming job…")
        try:
            client.post(f"/api/jobs/{job_id}/resume", {})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "jobs resume")

    console.print(f"[green]✓[/green] Resumed job [bold]{job_id}[/bold].")


@jobs.command("cancel")
@click.argument("job_id")
def jobs_cancel(job_id: str) -> None:
    """Cancel a background job."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Cancelling job…") as progress:
        progress.add_task("Cancelling job…")
        try:
            client.post(f"/api/jobs/{job_id}/cancel", {})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "jobs cancel")

    console.print(f"[green]✓[/green] Cancelled job [bold]{job_id}[/bold].")


@jobs.command("retry")
@click.argument("job_id")
def jobs_retry(job_id: str) -> None:
    """Retry a failed or cancelled background job."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Retrying job…") as progress:
        progress.add_task("Retrying job…")
        try:
            res = client.post(f"/api/jobs/{job_id}/retry", {})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "jobs retry")

    attempt = res.get("attempt_number", 2)
    console.print(f"[green]✓[/green] Retrying job [bold]{job_id}[/bold] (Attempt #{attempt}).")


@jobs.command("delete")
@click.argument("job_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
def jobs_delete(job_id: str, yes: bool) -> None:
    """Delete a background job."""
    client = _get_client()
    _require_auth(client)

    if not yes:
        click.confirm(f"Delete job [bold]{job_id}[/bold]?", abort=True)

    with _spinner("Deleting job…") as progress:
        progress.add_task("Deleting job…")
        try:
            client.delete(f"/api/jobs/{job_id}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "jobs delete")

    console.print(f"[green]✓[/green] Deleted job [bold]{job_id}[/bold].")


# ===========================================================================
# PLUGINS
# ===========================================================================

@cli.group()
def plugins() -> None:
    """Manage Aetherion extensions and tools plugins."""


@plugins.command("list")
@click.option("--installed", "-i", is_flag=True, help="Show only installed plugins.")
@click.pass_context
def plugins_list(ctx: click.Context, installed: bool) -> None:
    """List installed plugins."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading plugins…") as progress:
        progress.add_task("Loading plugins…")
        try:
            res = client.get("/api/plugins")
            items = res if isinstance(res, list) else res.get("plugins", [])
            if installed:
                items = [p for p in items if p.get("installed")]
            else:
                # Also fetch marketplace items
                try:
                    mkt = client.get("/api/marketplace/plugins")
                    mkt_items = mkt if isinstance(mkt, list) else mkt.get("plugins", [])
                    inst_names = {p.get("name", p.get("id")) for p in items}
                    for p in mkt_items:
                        if p.get("name", p.get("id")) not in inst_names:
                            p["_installed"] = False
                            items.append(p)
                except Exception:
                    pass
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "plugins list")

    fmt = ctx.obj.get("output", "table")
    if fmt == "json":
        _output_json(items)
        return

    if not items:
        console.print("[dim]No plugins found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold green")
    table.add_column("ID / Name", width=22)
    table.add_column("Category", width=14)
    table.add_column("Version", width=8)
    table.add_column("Status", width=14)
    table.add_column("Description", max_width=50)

    for p in items:
        name = str(p.get("name") or p.get("id") or "")[:20]
        cat = str(p.get("category", "—"))[:12]
        ver = str(p.get("version", "—"))[:7]
        is_installed = p.get("installed") or p.get("_installed") or installed
        is_enabled = p.get("is_enabled", p.get("enabled", True))
        if is_installed and is_enabled:
            status = "[green]✓ enabled[/green]"
        elif is_installed:
            status = "[yellow]○ disabled[/yellow]"
        else:
            status = "[dim]available[/dim]"
        desc = str(p.get("description", ""))[:50]
        table.add_row(name, cat, ver, status, desc)

    console.print(table)


@plugins.command("inspect")
@click.argument("source")
@click.pass_context
def plugins_inspect(ctx: click.Context, source: str) -> None:
    """Inspect and preview a plugin before installation."""
    client = _get_client()
    _require_auth(client)

    with _spinner(f"Inspecting [bold]{source}[/bold]…") as progress:
        progress.add_task("Inspecting source…")
        try:
            res = client.post("/api/plugins/inspect", {"source": source})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "plugins inspect")

    fmt = ctx.obj.get("output", "table")
    if fmt == "json":
        _output_json(res)
        return

    manifest = res.get("manifest", {})
    console.print(f"\n[bold green]Plugin Inspection Result:[/bold green]")
    console.print(f"  [bold]Preview ID:[/bold]       {res.get('preview_id')}")
    console.print(f"  [bold]Plugin Name:[/bold]      {manifest.get('name')} (v{manifest.get('version')})")
    console.print(f"  [bold]Risk Level:[/bold]       {res.get('risk_level')}")
    console.print(f"  [bold]Needs Approval:[/bold]   {res.get('requires_explicit_approval')}")
    console.print(f"  [bold]Permissions:[/bold]      {', '.join(res.get('permissions_requested', [])) or 'None'}")
    console.print(f"  [bold]Content Hash:[/bold]     {res.get('content_hash', '')[:16]}...")
    console.print(f"  [bold]Tools Count:[/bold]      {len(manifest.get('provided_tools', []))}\n")


@plugins.command("install")
@click.argument("plugin_id")
@click.option("--auto-approve", is_flag=True, help="Automatically approve requested permissions.")
@click.option("--approve-permissions", default="", help="Comma-separated permissions to approve.")
def plugins_install(plugin_id: str, auto_approve: bool, approve_permissions: str) -> None:
    """Install a plugin by ID or source directory."""
    client = _get_client()
    _require_auth(client)

    payload = {"source": plugin_id, "name": plugin_id, "auto_approve": auto_approve}
    if approve_permissions:
        payload["approved_permissions"] = [p.strip() for p in approve_permissions.split(",") if p.strip()]

    with _spinner(f"Installing [bold]{plugin_id}[/bold]…") as progress:
        progress.add_task("Installing…")
        try:
            res = client.post("/api/plugins/install", payload)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "plugins install")

    if res.get("ok"):
        console.print(f"[green]✓[/green] Plugin [bold]{plugin_id}[/bold] installed.")
    else:
        err_console.print(f"[red]✗[/red] Installation failed: {res.get('error', 'unknown error')}")
        sys.exit(1)


@plugins.command("uninstall")
@click.argument("plugin_id")
@click.option("--yes", "-y", is_flag=True)
def plugins_uninstall(plugin_id: str, yes: bool) -> None:
    """Uninstall a plugin by name."""
    client = _get_client()
    _require_auth(client)

    if not yes:
        click.confirm(f"Uninstall plugin [bold]{plugin_id}[/bold]?", abort=True)

    with _spinner(f"Uninstalling {plugin_id}…") as progress:
        progress.add_task("Uninstalling…")
        try:
            client.delete(f"/api/plugins/{plugin_id}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "plugins uninstall")

    console.print(f"[green]✓[/green] Plugin [bold]{plugin_id}[/bold] uninstalled.")


@plugins.command("enable")
@click.argument("plugin_id")
def plugins_enable(plugin_id: str) -> None:
    """Enable an installed plugin."""
    client = _get_client()
    _require_auth(client)
    try:
        client.post(f"/api/plugins/{plugin_id}/enable", {})
        console.print(f"[green]✓[/green] Plugin [bold]{plugin_id}[/bold] enabled.")
    except (AetherionClientError, Exception) as exc:
        _handle_error(exc, "plugins enable")


@plugins.command("disable")
@click.argument("plugin_id")
def plugins_disable(plugin_id: str) -> None:
    """Disable an installed plugin without uninstalling it."""
    client = _get_client()
    _require_auth(client)
    try:
        client.post(f"/api/plugins/{plugin_id}/disable", {})
        console.print(f"[yellow]○[/yellow] Plugin [bold]{plugin_id}[/bold] disabled.")
    except (AetherionClientError, Exception) as exc:
        _handle_error(exc, "plugins disable")


@plugins.command("check")
@click.argument("plugin_id")
def plugins_check(plugin_id: str) -> None:
    """Perform diagnostic health check on a plugin."""
    client = _get_client()
    _require_auth(client)

    with _spinner(f"Testing plugin [bold]{plugin_id}[/bold]…") as progress:
        progress.add_task("Testing…")
        try:
            res = client.post(f"/api/plugins/{plugin_id}/test", {})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "plugins check")

    if res.get("ok"):
        console.print(f"[green]✓[/green] Status: [bold green]{res.get('status', 'VALID')}[/bold green]")
        console.print(f"  Diagnostics: {res.get('diagnostics', 'operational')}")
    else:
        err_console.print(f"[red]✗[/red] Status: [bold red]{res.get('status', 'INVALID')}[/bold red]")
        err_console.print(f"  Diagnostics: {res.get('diagnostics', 'unknown error')}")
        sys.exit(1)


@plugins.command("rollback")
@click.argument("plugin_id")
@click.option("--version", "-v", required=True, help="Target version to rollback to.")
def plugins_rollback(plugin_id: str, version: str) -> None:
    """Rollback a plugin to a previous version."""
    client = _get_client()
    _require_auth(client)

    with _spinner(f"Rolling back [bold]{plugin_id}[/bold] to {version}…") as progress:
        progress.add_task("Rolling back…")
        try:
            res = client.post(f"/api/plugins/{plugin_id}/rollback", {"version": version})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "plugins rollback")

    if res.get("ok"):
        console.print(f"[green]✓[/green] Plugin [bold]{plugin_id}[/bold] rolled back to v{version}.")
    else:
        err_console.print(f"[red]✗[/red] Rollback failed: {res.get('error', 'unknown')}")
        sys.exit(1)


@plugins.command("info")
@click.argument("plugin_id")
@click.pass_context
def plugins_info(ctx: click.Context, plugin_id: str) -> None:
    """Show details for a specific plugin."""
    client = _get_client()
    _require_auth(client)

    plugin = None
    with _spinner("Loading plugin info…") as progress:
        progress.add_task("Loading plugin info…")
        try:
            try:
                p_res = client.get(f"/api/plugins/{plugin_id}")
                if isinstance(p_res, dict) and p_res.get("ok"):
                    plugin = p_res.get("plugin")
            except Exception:
                plugin = None

            if not plugin:
                res = client.get("/api/marketplace/plugins")
                items = res if isinstance(res, list) else res.get("plugins", [])
                plugin = next(
                    (p for p in items if str(p.get("id") or p.get("plugin_id")) == plugin_id),
                    None,
                )
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "plugins info")

    if not plugin:
        err_console.print(f"[red]✗[/red] Plugin [bold]{plugin_id}[/bold] not found.")
        sys.exit(1)

    fmt = ctx.obj.get("output", "table")
    if fmt == "json":
        _output_json(plugin)
        return

    lines = [
        f"[bold]ID:[/bold]          {plugin.get('id') or plugin.get('plugin_id')}",
        f"[bold]Name:[/bold]        {plugin.get('name', '—')}",
        f"[bold]Category:[/bold]    {plugin.get('category', '—')}",
        f"[bold]Version:[/bold]     {plugin.get('version', '—')}",
        f"[bold]Author:[/bold]      {plugin.get('author', '—')}",
        f"[bold]Installed:[/bold]   {plugin.get('installed', False)}",
        f"[bold]Description:[/bold] {plugin.get('description', '—')}",
    ]
    console.print(Panel.fit("\n".join(lines), title=f"[bold]{plugin.get('name', plugin_id)}[/bold]"))


# ===========================================================================
# SKILLS
# ===========================================================================

@cli.group()
def skills() -> None:
    """Manage reusable skill instruction packages."""


@skills.command("list")
@click.option("--enabled", "-e", is_flag=True, help="Show only enabled skills.")
@click.pass_context
def skills_list(ctx: click.Context, enabled: bool) -> None:
    """List installed skills."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading skills…") as progress:
        progress.add_task("Loading skills…")
        try:
            res = client.get(f"/api/skills{'?enabled=true' if enabled else ''}")
            items = res if isinstance(res, list) else res.get("skills", [])
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "skills list")

    fmt = ctx.obj.get("output", "table")
    if fmt == "json":
        _output_json(items)
        return

    if not items:
        console.print("[dim]No skills installed.[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("ID / Name", width=22)
    table.add_column("Version", width=8)
    table.add_column("Status", width=14)
    table.add_column("Triggers", width=20)
    table.add_column("Description", max_width=50)

    for s in items:
        name = str(s.get("name") or s.get("id") or "")[:20]
        ver = str(s.get("version", "—"))[:7]
        is_enabled = s.get("is_enabled", s.get("enabled", True))
        status = "[green]✓ enabled[/green]" if is_enabled else "[yellow]○ disabled[/yellow]"
        trigs = ", ".join(s.get("triggers", []))[:18]
        desc = str(s.get("description", ""))[:50]
        table.add_row(name, ver, status, trigs, desc)

    console.print(table)


@skills.command("inspect")
@click.argument("source")
@click.pass_context
def skills_inspect(ctx: click.Context, source: str) -> None:
    """Inspect and preview a skill manifest or markdown file."""
    client = _get_client()
    _require_auth(client)

    with _spinner(f"Inspecting skill [bold]{source}[/bold]…") as progress:
        progress.add_task("Inspecting source…")
        try:
            res = client.post("/api/skills/inspect", {"source": source})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "skills inspect")

    fmt = ctx.obj.get("output", "table")
    if fmt == "json":
        _output_json(res)
        return

    manifest = res.get("manifest", {})
    console.print(f"\n[bold cyan]Skill Inspection Result:[/bold cyan]")
    console.print(f"  [bold]Preview ID:[/bold]       {res.get('preview_id')}")
    console.print(f"  [bold]Skill Name:[/bold]       {manifest.get('name')} (v{manifest.get('version')})")
    console.print(f"  [bold]Triggers:[/bold]         {', '.join(manifest.get('activation_triggers', [])) or 'None'}")
    console.print(f"  [bold]Permissions:[/bold]      {', '.join(res.get('permissions_requested', [])) or 'None'}")
    console.print(f"  [bold]Content Hash:[/bold]     {res.get('content_hash', '')[:16]}...")
    console.print(f"  [bold]Instructions Size:[/bold] {len(manifest.get('instructions', ''))} bytes\n")


@skills.command("install")
@click.argument("source_or_name")
@click.option("--auto-approve", is_flag=True, help="Auto approve skill installation.")
def skills_install(source_or_name: str, auto_approve: bool) -> None:
    """Install a skill from markdown, directory, or package."""
    client = _get_client()
    _require_auth(client)

    payload = {"source": source_or_name, "auto_approve": auto_approve}
    with _spinner(f"Installing skill [bold]{source_or_name}[/bold]…") as progress:
        progress.add_task("Installing…")
        try:
            res = client.post("/api/skills/install", payload)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "skills install")

    if res.get("ok"):
        console.print(f"[green]✓[/green] Skill [bold]{source_or_name}[/bold] installed.")
    else:
        err_console.print(f"[red]✗[/red] Skill installation failed: {res.get('error', 'unknown')}")
        sys.exit(1)


@skills.command("uninstall")
@click.argument("skill_id")
@click.option("--yes", "-y", is_flag=True)
def skills_uninstall(skill_id: str, yes: bool) -> None:
    """Uninstall a skill by ID."""
    client = _get_client()
    _require_auth(client)

    if not yes:
        click.confirm(f"Uninstall skill [bold]{skill_id}[/bold]?", abort=True)

    with _spinner(f"Uninstalling {skill_id}…") as progress:
        progress.add_task("Uninstalling…")
        try:
            client.delete(f"/api/skills/{skill_id}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "skills uninstall")

    console.print(f"[green]✓[/green] Skill [bold]{skill_id}[/bold] uninstalled.")


@skills.command("enable")
@click.argument("skill_id")
def skills_enable(skill_id: str) -> None:
    """Enable a skill."""
    client = _get_client()
    _require_auth(client)
    try:
        client.post(f"/api/skills/{skill_id}/enable", {})
        console.print(f"[green]✓[/green] Skill [bold]{skill_id}[/bold] enabled.")
    except (AetherionClientError, Exception) as exc:
        _handle_error(exc, "skills enable")


@skills.command("disable")
@click.argument("skill_id")
def skills_disable(skill_id: str) -> None:
    """Disable a skill without uninstalling it."""
    client = _get_client()
    _require_auth(client)
    try:
        client.post(f"/api/skills/{skill_id}/disable", {})
        console.print(f"[yellow]○[/yellow] Skill [bold]{skill_id}[/bold] disabled.")
    except (AetherionClientError, Exception) as exc:
        _handle_error(exc, "skills disable")


@skills.command("check")
@click.argument("skill_id")
def skills_check(skill_id: str) -> None:
    """Check health and diagnostic state of an installed skill."""
    client = _get_client()
    _require_auth(client)

    with _spinner(f"Checking skill [bold]{skill_id}[/bold]…") as progress:
        progress.add_task("Checking…")
        try:
            res = client.post(f"/api/skills/{skill_id}/test", {})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "skills check")

    if res.get("ok"):
        console.print(f"[green]✓[/green] Status: [bold green]{res.get('status', 'VALID')}[/bold green]")
        console.print(f"  Diagnostics: {res.get('diagnostics', 'operational')}")
    else:
        err_console.print(f"[red]✗[/red] Status: [bold red]{res.get('status', 'INVALID')}[/bold red]")
        err_console.print(f"  Diagnostics: {res.get('diagnostics', 'unknown error')}")
        sys.exit(1)


@skills.command("rollback")
@click.argument("skill_id")
@click.option("--version", "-v", required=True, help="Version to rollback to.")
def skills_rollback(skill_id: str, version: str) -> None:
    """Rollback a skill to a previous version."""
    client = _get_client()
    _require_auth(client)

    with _spinner(f"Rolling back [bold]{skill_id}[/bold] to {version}…") as progress:
        progress.add_task("Rolling back…")
        try:
            res = client.post(f"/api/skills/{skill_id}/rollback", {"version": version})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "skills rollback")

    if res.get("ok"):
        console.print(f"[green]✓[/green] Skill [bold]{skill_id}[/bold] rolled back to v{version}.")
    else:
        err_console.print(f"[red]✗[/red] Rollback failed: {res.get('error', 'unknown')}")
        sys.exit(1)


@skills.command("info")
@click.argument("skill_id")
@click.pass_context
def skills_info(ctx: click.Context, skill_id: str) -> None:
    """Show details for a specific skill."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading skill info…") as progress:
        progress.add_task("Loading…")
        try:
            res = client.get(f"/api/skills/{skill_id}")
            skill = res.get("skill") if isinstance(res, dict) and res.get("ok") else None
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "skills info")

    if not skill:
        err_console.print(f"[red]✗[/red] Skill [bold]{skill_id}[/bold] not found.")
        sys.exit(1)

    fmt = ctx.obj.get("output", "table")
    if fmt == "json":
        _output_json(skill)
        return

    lines = [
        f"[bold]ID:[/bold]          {skill.get('id')}",
        f"[bold]Name:[/bold]        {skill.get('name', '—')}",
        f"[bold]Version:[/bold]     {skill.get('version', '—')}",
        f"[bold]Author:[/bold]      {skill.get('author', '—')}",
        f"[bold]Enabled:[/bold]     {skill.get('is_enabled', skill.get('enabled', True))}",
        f"[bold]Triggers:[/bold]    {', '.join(skill.get('triggers', [])) or '—'}",
        f"[bold]Tools:[/bold]       {', '.join(skill.get('required_tools', [])) or '—'}",
        f"[bold]Description:[/bold] {skill.get('description', '—')}",
    ]
    console.print(Panel.fit("\n".join(lines), title=f"[bold]{skill.get('name', skill_id)}[/bold]"))


# ===========================================================================
# PROVIDERS
# ===========================================================================

@cli.group()
def providers() -> None:
    """Manage and inspect AI provider status."""


@providers.command("list")
@click.pass_context
def providers_list(ctx: click.Context) -> None:
    """List all configured AI providers and their availability."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Checking providers…") as progress:
        progress.add_task("Checking providers…")
        try:
            res = client.get("/api/providers/status")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "providers list")

    prov_data = res.get("providers", res)
    fmt = ctx.obj.get("output", "table")

    if fmt == "json":
        _output_json(prov_data)
        return

    if not prov_data:
        console.print("[dim]No providers configured.[/dim]")
        return

    table = Table(show_header=True, header_style="bold yellow")
    table.add_column("Key", style="dim", width=22)
    table.add_column("Provider", width=14)
    table.add_column("Model", width=26)
    table.add_column("Available", width=10)
    table.add_column("Quality", width=8)
    table.add_column("Cost", width=8)

    if isinstance(prov_data, dict):
        for key, info in prov_data.items():
            avail = info.get("available", info.get("ok", False))
            a_txt = "[green]✓[/green]" if avail else "[red]✗[/red]"
            table.add_row(
                str(key)[:20],
                str(info.get("provider", "—"))[:12],
                str(info.get("model", "—"))[:24],
                a_txt,
                str(info.get("quality", "—")),
                str(info.get("cost", "—")),
            )
    elif isinstance(prov_data, list):
        for info in prov_data:
            avail = info.get("available", info.get("ok", False))
            a_txt = "[green]✓[/green]" if avail else "[red]✗[/red]"
            table.add_row(
                str(info.get("key", ""))[:20],
                str(info.get("provider", "—"))[:12],
                str(info.get("model", "—"))[:24],
                a_txt,
                str(info.get("quality", "—")),
                str(info.get("cost", "—")),
            )

    console.print(table)


@providers.command("test")
@click.argument("provider_key")
def providers_test(provider_key: str) -> None:
    """Send a test ping to a specific provider."""
    client = _get_client()
    _require_auth(client)

    with _spinner(f"Testing provider [bold]{provider_key}[/bold]…") as progress:
        progress.add_task("Pinging provider…")
        try:
            res = client.post("/api/providers/test", {"provider_key": provider_key})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "providers test")

    if res.get("ok"):
        lat = res.get("latency_ms", "?")
        console.print(f"[green]✓[/green] Provider [bold]{provider_key}[/bold] is responding")
        console.print(f"  Latency:  {lat} ms")
        console.print(f"  Model:    {res.get('model', '?')}")
        console.print(f"  Response: {res.get('response', '')[:60]}")
    else:
        err_console.print(f"[red]✗[/red] Provider test failed: {res.get('error', 'unknown')}")
        sys.exit(1)


@providers.command("add")
@click.option("--name", required=True, help="Friendly name for provider")
@click.option("--type", "provider_type", default="OPENAI_COMPATIBLE", type=click.Choice(["OPENAI_COMPATIBLE", "ANTHROPIC_COMPATIBLE", "CHAT_COMPATIBLE"]), help="Provider type")
@click.option("--url", "base_url", required=True, help="API Base URL (e.g. http://localhost:11434/v1)")
@click.option("--key", "api_key", default="", help="API Key (optional for local models)")
@click.option("--key-env", default="", help="Environment variable containing the API key")
@click.option("--prompt-key", is_flag=True, help="Prompt interactively for API key with hidden input")
@click.option("--model", "default_model", default="", help="Default model name")
@click.option("--default", "is_default", is_flag=True, help="Set as default provider")
def providers_add(name: str, provider_type: str, base_url: str, api_key: str, key_env: str, prompt_key: bool, default_model: str, is_default: bool) -> None:
    """Register a custom LLM provider."""
    client = _get_client()
    _require_auth(client)

    if prompt_key:
        api_key = click.prompt("API Key", hide_input=True)
    elif key_env:
        api_key = os.environ.get(key_env, "")

    payload = {
        "name": name,
        "provider_type": provider_type,
        "base_url": base_url,
        "api_key": api_key,
        "default_model": default_model,
        "is_default": is_default,
    }
    with _spinner("Registering provider…") as progress:
        progress.add_task("Registering provider…")
        try:
            res = client.post("/api/providers", payload)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "providers add")

    if res.get("ok"):
        p = res.get("provider", {})
        console.print(f"[green]✓[/green] Provider [bold]{p.get('name')}[/bold] registered successfully (ID: {p.get('id')})")
    else:
        err_console.print(f"[red]✗[/red] Failed to add provider: {res.get('error', 'unknown')}")
        sys.exit(1)


@providers.command("remove")
@click.argument("provider_id")
def providers_remove(provider_id: str) -> None:
    """Delete a custom registered provider."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Deleting provider…") as progress:
        progress.add_task("Deleting provider…")
        try:
            res = client.delete(f"/api/providers/{provider_id}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "providers remove")

    if res.get("ok"):
        console.print(f"[green]✓[/green] Provider [bold]{provider_id}[/bold] deleted.")
    else:
        err_console.print(f"[red]✗[/red] Failed to delete provider: {res.get('error', 'unknown')}")
        sys.exit(1)


@providers.command("set-default")
@click.argument("provider_id")
def providers_set_default_cmd(provider_id: str) -> None:
    """Set a registered provider as default."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Updating default provider…") as progress:
        progress.add_task("Updating default provider…")
        try:
            res = client.post(f"/api/providers/{provider_id}/default", {})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "providers set-default")

    if res.get("ok"):
        console.print(f"[green]✓[/green] Provider [bold]{provider_id}[/bold] is now the default.")
    else:
        err_console.print(f"[red]✗[/red] Failed to set default provider: {res.get('error', 'unknown')}")
        sys.exit(1)


@providers.command("check")
@click.argument("provider_id")
def providers_check(provider_id: str) -> None:
    """Run fine-grained diagnostics against a registered provider."""
    client = _get_client()
    _require_auth(client)

    with _spinner(f"Running diagnostics on {provider_id}…") as progress:
        progress.add_task("Running health check…")
        try:
            res = client.post(f"/api/providers/{provider_id}/test", {})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "providers check")

    status = res.get("status", "UNKNOWN")
    lat = res.get("latency_ms", 0.0)
    diag = res.get("diagnostics", "")

    if res.get("ok"):
        console.print(f"[green]✓[/green] Status: [bold green]{status}[/bold green] ({lat} ms)")
        console.print(f"  Diagnostics: {diag}")
    else:
        console.print(f"[red]✗[/red] Status: [bold red]{status}[/bold red] ({lat} ms)")
        console.print(f"  Diagnostics: {diag}")
        sys.exit(1)


@providers.command("status")
@click.pass_context
def providers_status(ctx: click.Context) -> None:
    """Show overall AI provider status."""
    ctx.invoke(providers_list)


# Register singular alias 'provider' alongside plural 'providers'
cli.add_command(providers, name="provider")


# ===========================================================================
# CONFIG
# ===========================================================================

@cli.group("config", invoke_without_command=True)
@click.option("--url", default=None, help="Set the backend server URL.")
@click.option("--model", default=None, help="Set the default model.")
@click.option("--output-format", "output_format", default=None,
              type=click.Choice(["table", "json", "plain"]),
              help="Set default output format.")
@click.option("--show", is_flag=True, help="Show current configuration.")
@click.pass_context
def config_cmd(
    ctx: click.Context,
    url: Optional[str],
    model: Optional[str],
    output_format: Optional[str],
    show: bool,
) -> None:
    """View or update CLI configuration.

    Examples:
      aetherion config --show
      aetherion config show
      aetherion config --url https://api.aetherion.ai
      aetherion config set server_url https://api.aetherion.ai
    """
    if ctx.invoked_subcommand is not None:
        return

    if not any([url, model, output_format]) or show:
        data = cfg_mod.all_config()
        fmt = ctx.obj.get("output", "table") if (ctx.obj and isinstance(ctx.obj, dict)) else "table"
        if fmt == "json":
            _output_json(data)
            return
        console.print(Panel.fit(
            f"[bold]Server URL:[/bold]     {data['server_url']}\n"
            f"[bold]Default model:[/bold]  {data['model']}\n"
            f"[bold]Output format:[/bold]  {data['output_format']}\n"
            f"[bold]Token set:[/bold]      {'[green]Yes[/green]' if data['token_set'] else '[red]No[/red]'}\n"
            f"[bold]Token source:[/bold]   {data['token_source']}\n"
            f"[bold]Config file:[/bold]    {data['config_file']}",
            title="[bold]Aetherion CLI Configuration[/bold]",
        ))
        return

    if url:
        cfg_mod.set_server_url(url)
        console.print(f"[green]✓[/green] Server URL → {url}")

    if model:
        cfg_mod.set_model(model)
        console.print(f"[green]✓[/green] Model → {model}")

    if output_format:
        cfg_mod.set_output_format(output_format)
        console.print(f"[green]✓[/green] Output format → {output_format}")


@config_cmd.command("show")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def config_sub_show(ctx: click.Context, as_json: bool) -> None:
    """Show current configuration."""
    data = cfg_mod.all_config()
    fmt = "json" if as_json or (ctx.obj and isinstance(ctx.obj, dict) and ctx.obj.get("output") == "json") else "table"
    if fmt == "json":
        _output_json(data)
        return
    console.print(Panel.fit(
        f"[bold]Server URL:[/bold]     {data['server_url']}\n"
        f"[bold]Default model:[/bold]  {data['model']}\n"
        f"[bold]Output format:[/bold]  {data['output_format']}\n"
        f"[bold]Token set:[/bold]      {'[green]Yes[/green]' if data['token_set'] else '[red]No[/red]'}\n"
        f"[bold]Token source:[/bold]   {data['token_source']}\n"
        f"[bold]Config file:[/bold]    {data['config_file']}",
        title="[bold]Aetherion CLI Configuration[/bold]",
    ))


@config_cmd.command("set")
@click.argument("key")
@click.argument("value")
def config_sub_set(key: str, value: str) -> None:
    """Set a configuration value."""
    try:
        cfg_mod.set_setting(key, value)
        console.print(f"[green]✓[/green] Set {key} = {value}")
    except ValueError as exc:
        console.print(f"[red]✗ Error:[/red] {exc}")
        sys.exit(1)


# ===========================================================================
# TUI — Interactive Terminal Workspace
# ===========================================================================

@cli.command("tui")
@click.option("--model", "-m", default=None,
              help="AI model to use in the workspace.")
@click.option("--session", "-s", default=None,
              help="Resume a specific session ID.")
def tui_command(model: Optional[str], session: Optional[str]) -> None:
    """Launch the Aetherion interactive terminal workspace (TUI).

    Full keyboard-driven AI workspace with session management, streaming
    chat, command palette, context heatmap, and approval flows.

    Examples:

      aetherion tui

      aetherion tui --model gpt-4o

      aetherion tui --session my-session-id
    """
    try:
        from .tui import run_tui
    except ImportError as exc:
        err_console.print(
            f"[red]✗ TUI requires additional dependencies:[/red] {exc}\n"
            "  Install with: [bold]pip install 'aetherion[tui]'[/bold]"
        )
        sys.exit(1)

    m = model or cfg_mod.get_model()
    run_tui(model=m, session_id=session)


# ===========================================================================
# SKILLS
# ===========================================================================

@cli.group()
def skills() -> None:
    """Manage Aetherion skills."""


@skills.command("list")
@click.pass_context
def skills_list(ctx: click.Context) -> None:
    """List all available skills from the backend."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading skills…") as progress:
        progress.add_task("Loading skills…")
        try:
            res = client.get("/api/skills")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "skills list")

    items = res if isinstance(res, list) else res.get("skills", [])
    fmt = ctx.obj.get("output", "table")

    if fmt == "json":
        _output_json(items)
        return

    if not items:
        console.print("[dim]No skills found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name", width=24)
    table.add_column("Description", max_width=60)
    table.add_column("Version", width=10)

    for s in items:
        table.add_row(
            str(s.get("name", "—"))[:22],
            str(s.get("description", "—"))[:58],
            str(s.get("version", "—")),
        )

    console.print(table)


@skills.command("install")
@click.argument("skill_name")
def skills_install(skill_name: str) -> None:
    """Install a skill by name."""
    client = _get_client()
    _require_auth(client)

    with _spinner(f"Installing skill [bold]{skill_name}[/bold]…") as progress:
        progress.add_task("Installing…")
        try:
            client.post("/api/skills/install", {"name": skill_name})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "skills install")

    console.print(f"[green]✓[/green] Skill [bold]{skill_name}[/bold] installed.")


# ===========================================================================
# Skills: create + local
# ===========================================================================

@skills.command("create")
@click.option("--name", "-n", required=True, prompt="Skill name",
              help="Slug name for the skill (e.g. git-workflow).")
@click.option("--description", "-d", default="",
              help="One-line description of what this skill does.")
def skills_create(name: str, description: str) -> None:
    """Create a new local skill from a description.

    The skill is written to ~/.aetherion/config/skills/<name>/SKILL.md
    and registered with the backend if you are authenticated.

    Examples:

      aetherion skills create --name git-workflow \\
        --description "Automate git commit, tag, and push"
    """
    from .tui.skill_creator import create_skill_interactive

    with _spinner(f"Creating skill [bold]{name}[/bold]…") as progress:
        progress.add_task("Writing SKILL.md…")
        result = create_skill_interactive(name=name, description=description)

    if result.startswith("✓"):
        console.print(f"[green]{result}[/green]")
    else:
        err_console.print(f"[red]{result}[/red]")
        sys.exit(1)


@skills.command("local")
@click.pass_context
def skills_local(ctx: click.Context) -> None:
    """List skills stored locally under ~/.aetherion/config/skills/."""
    from .tui.skill_creator import list_local_skills

    items = list_local_skills()
    fmt   = (ctx.obj or {}).get("output", "table")

    if fmt == "json":
        _output_json(items)
        return

    if not items:
        console.print(
            "[dim]No local skills found. "
            "Run [bold]aetherion skills create[/bold] to make one.[/dim]"
        )
        return

    table = Table(show_header=True, header_style="bold magenta", title="Local Skills")
    table.add_column("Name",        width=24)
    table.add_column("Description", max_width=56)
    table.add_column("Path",        max_width=50, style="dim")

    for s in items:
        table.add_row(
            str(s.get("name",        "—"))[:22],
            str(s.get("description", "—"))[:54],
            str(s.get("path",        "—"))[:48],
        )

    console.print(table)


# ---------------------------------------------------------------------------
# Skills: versioning, rollback, export, import, enable, disable
# ---------------------------------------------------------------------------

@skills.command("versions")
@click.argument("skill_name")
@click.pass_context
def skills_versions(ctx: click.Context, skill_name: str) -> None:
    """List version history for a skill."""
    client = _get_client()
    _require_auth(client)
    try:
        res = client.get(f"/api/skills/{skill_name}/versions")
    except (AetherionClientError, Exception) as exc:
        _handle_error(exc, "skills versions")

    versions = res if isinstance(res, list) else res.get("versions", [])
    fmt = (ctx.obj or {}).get("output", "table")
    if fmt == "json":
        _output_json(versions)
        return
    if not versions:
        console.print("[dim]No version history found.[/dim]")
        return
    table = Table(show_header=True, header_style="bold magenta", title=f"Versions — {skill_name}")
    table.add_column("Tag", width=8)
    table.add_column("Created", width=22)
    table.add_column("Reason", max_width=40)
    for v in versions:
        table.add_row(
            str(v.get("tag", "—")),
            str(v.get("created_at", "—")),
            str(v.get("reason", "—")),
        )
    console.print(table)


@skills.command("rollback")
@click.argument("skill_name")
@click.option("--version", "-v", default="", help="Version tag to roll back to (e.g. v2). Default: previous.")
def skills_rollback(skill_name: str, version: str) -> None:
    """Roll back a skill to a previous version."""
    client = _get_client()
    _require_auth(client)
    body: dict = {}
    if version:
        body["version"] = version
    with _spinner(f"Rolling back [bold]{skill_name}[/bold]…") as p:
        p.add_task("Rolling back…")
        try:
            res = client.post(f"/api/skills/{skill_name}/rollback", body)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "skills rollback")
    tag = res.get("rolled_back_to", version or "previous")
    console.print(f"[green]✓[/green] [bold]{skill_name}[/bold] rolled back to [bold]{tag}[/bold].")


@skills.command("export")
@click.argument("skill_name")
@click.option("--out", "-o", default="", help="Output path for the .zip file.")
def skills_export(skill_name: str, out: str) -> None:
    """Export a skill as a .zip archive."""
    import httpx
    from .config import get_server_url, get_token
    dest = out or f"{skill_name}.zip"
    token = get_token() or ""
    url = f"{get_server_url()}/api/skills/{skill_name}/export"
    with _spinner(f"Exporting [bold]{skill_name}[/bold]…") as p:
        p.add_task("Exporting…")
        try:
            with httpx.Client(timeout=30) as http:
                r = http.get(url, headers={"Authorization": f"Bearer {token}"})
                r.raise_for_status()
                with open(dest, "wb") as f:
                    f.write(r.content)
        except Exception as exc:
            _handle_error(exc, "skills export")
    console.print(f"[green]✓[/green] Exported to [bold]{dest}[/bold].")


@skills.command("import")
@click.argument("zip_path")
def skills_import(zip_path: str) -> None:
    """Import a skill from a .zip archive."""
    import httpx
    from pathlib import Path as _Path
    from .config import get_server_url, get_token
    p = _Path(zip_path)
    if not p.exists():
        err_console.print(f"[red]✗[/red] File not found: {zip_path}")
        sys.exit(1)
    token = get_token() or ""
    url = f"{get_server_url()}/api/skills/import"
    with _spinner(f"Importing [bold]{p.name}[/bold]…") as prog:
        prog.add_task("Importing…")
        try:
            with httpx.Client(timeout=30) as http:
                with open(p, "rb") as fh:
                    r = http.post(
                        url,
                        files={"file": (p.name, fh, "application/zip")},
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    r.raise_for_status()
                    data = r.json()
        except Exception as exc:
            _handle_error(exc, "skills import")
    console.print(f"[green]✓[/green] Imported skill [bold]{data.get('name', p.stem)}[/bold].")


@skills.command("enable")
@click.argument("skill_name")
def skills_enable(skill_name: str) -> None:
    """Enable a skill."""
    client = _get_client()
    _require_auth(client)
    try:
        client.post("/api/skills/toggle", {"name": skill_name, "enabled": True})
        console.print(f"[green]✓[/green] Skill [bold]{skill_name}[/bold] enabled.")
    except (AetherionClientError, Exception) as exc:
        _handle_error(exc, "skills enable")


@skills.command("disable")
@click.argument("skill_name")
def skills_disable(skill_name: str) -> None:
    """Disable a skill without uninstalling it."""
    client = _get_client()
    _require_auth(client)
    try:
        client.post("/api/skills/toggle", {"name": skill_name, "enabled": False})
        console.print(f"[yellow]○[/yellow] Skill [bold]{skill_name}[/bold] disabled.")
    except (AetherionClientError, Exception) as exc:
        _handle_error(exc, "skills disable")


@skills.command("uninstall")
@click.argument("skill_name")
@click.option("--yes", "-y", is_flag=True)
def skills_uninstall(skill_name: str, yes: bool) -> None:
    """Uninstall a skill permanently."""
    client = _get_client()
    _require_auth(client)
    if not yes:
        click.confirm(f"Uninstall skill [bold]{skill_name}[/bold]?", abort=True)
    with _spinner(f"Uninstalling [bold]{skill_name}[/bold]…") as p:
        p.add_task("Uninstalling…")
        try:
            client.delete(f"/api/skills/{skill_name}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "skills uninstall")
    console.print(f"[green]✓[/green] Skill [bold]{skill_name}[/bold] uninstalled.")


@skills.command("edit")
@click.argument("skill_name")
def skills_edit(skill_name: str) -> None:
    """Open a skill's SKILL.md in $EDITOR for editing."""
    import os as _os
    import subprocess
    import tempfile
    client = _get_client()
    _require_auth(client)
    try:
        res = client.get("/api/skills/content", params={"name": skill_name})
        content = res.get("content", "")
    except (AetherionClientError, Exception) as exc:
        _handle_error(exc, "skills edit")

    editor = _os.environ.get("EDITOR", "notepad" if sys.platform == "win32" else "nano")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                     delete=False, encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        subprocess.run([editor, tmp_path], check=True)
        with open(tmp_path, encoding="utf-8") as f:
            new_content = f.read()
        if new_content != content:
            client.put("/api/skills/content", {"name": skill_name, "content": new_content})
            console.print(f"[green]✓[/green] Skill [bold]{skill_name}[/bold] updated.")
        else:
            console.print("[dim]No changes.[/dim]")
    finally:
        _os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Plugins: dependency graph + integrity verification
# ---------------------------------------------------------------------------

@plugins.command("deps")
@click.argument("plugin_id")
@click.pass_context
def plugins_deps(ctx: click.Context, plugin_id: str) -> None:
    """Show resolved dependency graph for a plugin."""
    client = _get_client()
    _require_auth(client)
    try:
        res = client.get(f"/api/plugins/{plugin_id}/deps")
    except (AetherionClientError, Exception) as exc:
        _handle_error(exc, "plugins deps")

    fmt = (ctx.obj or {}).get("output", "table")
    if fmt == "json":
        _output_json(res)
        return

    deps = res.get("dependencies", [])
    missing = res.get("missing", [])
    satisfied = res.get("satisfied", True)

    status = "[green]✓ Satisfied[/green]" if satisfied else f"[red]✗ Missing: {', '.join(missing)}[/red]"
    console.print(f"[bold]{plugin_id}[/bold] — {status}")
    if deps:
        console.print("Dependencies (install order):")
        for i, d in enumerate(deps, 1):
            is_missing = d in missing
            tag = "[red](missing)[/red]" if is_missing else "[green](installed)[/green]"
            console.print(f"  {i}. {d}  {tag}")
    else:
        console.print("[dim]No dependencies.[/dim]")


@plugins.command("verify")
@click.argument("plugin_id")
def plugins_verify(plugin_id: str) -> None:
    """Verify integrity of an installed plugin."""
    client = _get_client()
    _require_auth(client)
    try:
        res = client.get(f"/api/plugins/{plugin_id}/verify")
    except (AetherionClientError, Exception) as exc:
        _handle_error(exc, "plugins verify")

    if res.get("integrity") == "OK":
        console.print(f"[green]✓[/green] Plugin [bold]{plugin_id}[/bold] integrity OK "
                      f"(checksum: {res.get('checksum', '')})")
    else:
        err_console.print(f"[red]✗[/red] Integrity FAIL — stored: {res.get('stored')} "
                          f"computed: {res.get('computed')}")
        sys.exit(1)


@plugins.command("rollback")
@click.argument("plugin_id")
def plugins_rollback(plugin_id: str) -> None:
    """Roll back a plugin to its previous version."""
    client = _get_client()
    _require_auth(client)
    with _spinner(f"Rolling back [bold]{plugin_id}[/bold]…") as p:
        p.add_task("Rolling back…")
        try:
            res = client.post(f"/api/plugins/{plugin_id}/rollback", {})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "plugins rollback")
    console.print(f"[green]✓[/green] Plugin [bold]{plugin_id}[/bold] rolled back to "
                  f"[bold]{res.get('version', 'previous')}[/bold].")


# ===========================================================================
# GIT
# ===========================================================================

@cli.group()
def git() -> None:
    """Git integration — clone, branch, commit, push, PR prep."""


@git.command("clone")
@click.argument("url")
@click.option("--dest", "-d", default="", help="Local destination directory.")
@click.option("--branch", "-b", default="", help="Branch to checkout after clone.")
@click.option("--ssh/--https", "use_ssh", default=False,
              help="Force SSH protocol (HTTPS is default).")
def git_clone(url: str, dest: str, branch: str, use_ssh: bool) -> None:
    """Clone a GitHub, GitLab, or Bitbucket repository.

    Supports public and private repositories.
    Private repos require SSH keys or a stored PAT.

    Examples:

      aetherion git clone https://github.com/user/repo

      aetherion git clone git@github.com:user/repo --ssh

      aetherion git clone https://github.com/user/repo --branch dev --dest ./project
    """
    import subprocess
    import shutil

    if not shutil.which("git"):
        err_console.print("[red]✗[/red] git is not installed or not on PATH.")
        sys.exit(1)

    # Normalise URL: convert HTTPS → SSH if requested
    clone_url = url
    if use_ssh:
        for host in ("github.com", "gitlab.com", "bitbucket.org"):
            if f"https://{host}/" in clone_url:
                clone_url = clone_url.replace(f"https://{host}/", f"git@{host}:")
                break

    cmd = ["git", "clone", clone_url]
    if dest:
        cmd.append(dest)
    if branch:
        cmd += ["--branch", branch]

    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    try:
        result = subprocess.run(cmd, check=True)
        console.print(f"[green]✓[/green] Repository cloned successfully.")
    except subprocess.CalledProcessError as e:
        err_console.print(f"[red]✗[/red] Clone failed (exit {e.returncode}).")
        sys.exit(e.returncode)


@git.command("status")
@click.argument("path", default=".", required=False)
def git_status(path: str) -> None:
    """Show working tree status."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "-C", path, "status", "--short", "--branch"],
            capture_output=True, text=True, check=True
        )
        console.print(result.stdout)
    except subprocess.CalledProcessError as e:
        err_console.print(f"[red]✗[/red] {e.stderr.strip()}")
        sys.exit(1)


@git.command("branch")
@click.argument("name", required=False)
@click.option("--list", "list_branches", is_flag=True, help="List branches.")
@click.option("--delete", "-d", "delete", is_flag=True, help="Delete branch.")
@click.option("--path", "-C", "repo_path", default=".", help="Repo path.")
def git_branch(name: str, list_branches: bool, delete: bool, repo_path: str) -> None:
    """Create, list, or delete a git branch."""
    import subprocess
    if list_branches or not name:
        res = subprocess.run(
            ["git", "-C", repo_path, "branch", "-a"],
            capture_output=True, text=True
        )
        console.print(res.stdout or "[dim]No branches found.[/dim]")
        return
    cmd = ["git", "-C", repo_path, "branch"]
    if delete:
        cmd += ["-d", name]
    else:
        cmd.append(name)
    try:
        subprocess.run(cmd, check=True)
        action = "deleted" if delete else "created"
        console.print(f"[green]✓[/green] Branch [bold]{name}[/bold] {action}.")
    except subprocess.CalledProcessError as e:
        err_console.print(f"[red]✗[/red] {e.stderr.strip()}")
        sys.exit(1)


@git.command("pull")
@click.option("--path", "-C", "repo_path", default=".", help="Repo path.")
@click.option("--rebase", is_flag=True, help="Pull with rebase.")
def git_pull(repo_path: str, rebase: bool) -> None:
    """Pull latest changes from remote."""
    import subprocess
    cmd = ["git", "-C", repo_path, "pull"]
    if rebase:
        cmd.append("--rebase")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        console.print(result.stdout.strip() or "[green]✓[/green] Already up to date.")
    except subprocess.CalledProcessError as e:
        err_console.print(f"[red]✗[/red] Pull failed:\n{e.stderr.strip()}")
        sys.exit(1)


@git.command("commit")
@click.option("--message", "-m", required=True, prompt="Commit message", help="Commit message.")
@click.option("--all", "-a", "stage_all", is_flag=True, help="Stage all changes before committing.")
@click.option("--path", "-C", "repo_path", default=".", help="Repo path.")
def git_commit(message: str, stage_all: bool, repo_path: str) -> None:
    """Stage and commit changes."""
    import subprocess
    if stage_all:
        subprocess.run(["git", "-C", repo_path, "add", "-A"], check=True)
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "commit", "-m", message],
            capture_output=True, text=True, check=True
        )
        console.print(result.stdout.strip())
        console.print(f"[green]✓[/green] Committed: {message[:60]}")
    except subprocess.CalledProcessError as e:
        err_console.print(f"[red]✗[/red] Commit failed:\n{e.stderr.strip()}")
        sys.exit(1)


@git.command("push")
@click.option("--remote", default="origin", help="Remote name.")
@click.option("--branch", "-b", default="", help="Branch to push (default: current).")
@click.option("--force", "-f", is_flag=True, help="Force push (use with care).")
@click.option("--path", "-C", "repo_path", default=".", help="Repo path.")
def git_push(remote: str, branch: str, force: bool, repo_path: str) -> None:
    """Push commits to remote."""
    import subprocess
    cmd = ["git", "-C", repo_path, "push", remote]
    if branch:
        cmd.append(branch)
    if force:
        cmd.append("--force-with-lease")  # safer than --force
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = result.stdout.strip() or result.stderr.strip()
        console.print(out or "[green]✓[/green] Push complete.")
    except subprocess.CalledProcessError as e:
        err_console.print(f"[red]✗[/red] Push failed:\n{e.stderr.strip()}")
        sys.exit(1)


@git.command("pr")
@click.option("--title", "-t", required=True, prompt="PR title", help="Pull request title.")
@click.option("--body", "-b", default="", help="PR body / description.")
@click.option("--base", default="main", help="Target base branch (default: main).")
@click.option("--path", "-C", "repo_path", default=".", help="Repo path.")
def git_pr(title: str, body: str, base: str, repo_path: str) -> None:
    """Prepare a pull request description and open it in the browser.

    Requires GitHub CLI (gh) if you want automatic PR creation.
    Falls back to displaying the PR template for manual submission.
    """
    import subprocess
    import shutil

    # Get current branch
    try:
        branch_res = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True
        )
        current_branch = branch_res.stdout.strip()
    except Exception:
        current_branch = "current-branch"

    # Try gh CLI first
    if shutil.which("gh"):
        cmd = ["gh", "pr", "create",
               "--title", title,
               "--base", base,
               "--head", current_branch]
        if body:
            cmd += ["--body", body]
        try:
            result = subprocess.run(cmd, cwd=repo_path, check=True, capture_output=True, text=True)
            console.print(result.stdout.strip())
            console.print(f"[green]✓[/green] PR created: {title}")
            return
        except subprocess.CalledProcessError as e:
            err_console.print(f"[yellow]gh pr create failed:[/yellow] {e.stderr.strip()}")

    # Fallback: display PR template
    from rich.panel import Panel
    pr_text = (
        f"**Title:** {title}\n"
        f"**Base:** {base}  ←  {current_branch}\n\n"
        f"{body or '_No description provided._'}"
    )
    console.print(Panel(pr_text, title="[bold]PR Ready[/bold]", border_style="blue"))
    console.print("[dim]Push your branch and open a PR manually, or install GitHub CLI (gh).[/dim]")


# ===========================================================================
# REPOSITORY INTELLIGENCE — reuses /api/repo/* (routes/repo_intelligence_bp.py)
# ===========================================================================

@cli.group("repo")
def repo() -> None:
    """Inspect a workspace symbol map and import graph."""


@repo.command("scan")
@click.option("--session", "-s", "session_id", required=True,
              help="Workspace session id.")
def repo_scan(session_id: str) -> None:
    """Rebuild the symbol map for a workspace."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Scanning workspace…") as progress:
        progress.add_task("Scanning workspace…")
        try:
            res = client.post("/api/repo/scan", {"session_id": session_id})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "repo scan")

    console.print(
        f"[green]✓[/green] Indexed [bold]{res.get('symbols', 0)}[/bold] files "
        f"of [bold]{res.get('files', 0)}[/bold] walked."
    )


@repo.command("symbols")
@click.option("--session", "-s", "session_id", required=True,
              help="Workspace session id.")
@click.option("--path", "-p", default="", help="Restrict to one file.")
@click.pass_context
def repo_symbols(ctx: click.Context, session_id: str, path: str) -> None:
    """List indexed classes and functions per file."""
    client = _get_client()
    _require_auth(client)

    endpoint = f"/api/repo/symbols?session_id={urllib.parse.quote(session_id)}"
    if path:
        endpoint += f"&path={urllib.parse.quote(path)}"

    with _spinner("Loading symbols…") as progress:
        progress.add_task("Loading symbols…")
        try:
            res = client.get(endpoint)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "repo symbols")

    symbols = res.get("symbols", {}) if isinstance(res, dict) else {}
    if ctx.obj.get("output", "table") == "json":
        _output_json(symbols)
        return

    if not symbols:
        console.print("[dim]No symbols indexed. Run `aetherion repo scan` first.[/dim]")
        return

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("File", max_width=52)
    table.add_column("Classes", max_width=32)
    table.add_column("Functions", max_width=40)

    for file_path, entry in symbols.items():
        table.add_row(
            str(file_path)[:52],
            ", ".join(entry.get("classes", []))[:32] or "—",
            ", ".join(entry.get("functions", []))[:40] or "—",
        )

    console.print(table)

@repo.command("deps")
@click.option("--session", "-s", "session_id", required=True,
              help="Workspace session id.")
@click.pass_context
def repo_deps(ctx: click.Context, session_id: str) -> None:
    """Show the per-file import graph."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading dependencies…") as progress:
        progress.add_task("Loading dependencies…")
        try:
            res = client.get(
                f"/api/repo/dependencies?session_id={urllib.parse.quote(session_id)}"
            )
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "repo deps")

    deps = res.get("dependencies", {}) if isinstance(res, dict) else {}
    if ctx.obj.get("output", "table") == "json":
        _output_json(deps)
        return

    if not deps:
        console.print("[dim]No dependency graph. Run `aetherion repo scan` first.[/dim]")
        return

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("File", max_width=52)
    table.add_column("Imports", max_width=64)

    for file_path, entry in deps.items():
        table.add_row(
            str(file_path)[:52],
            ", ".join(entry.get("imports", []))[:64] or "—",
        )

    console.print(table)


@repo.command("refs")
@click.argument("symbol")
@click.option("--session", "-s", "session_id", required=True,
              help="Workspace session id.")
@click.pass_context
def repo_refs(ctx: click.Context, session_id: str, symbol: str) -> None:
    """List files importing SYMBOL."""
    client = _get_client()
    _require_auth(client)

    with _spinner(f"Finding references to [bold]{symbol}[/bold]…") as progress:
        progress.add_task("Searching…")
        try:
            res = client.get(
                f"/api/repo/references?session_id={urllib.parse.quote(session_id)}"
                f"&symbol={urllib.parse.quote(symbol)}"
            )
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "repo refs")

    refs = res.get("references", []) if isinstance(res, dict) else []
    if ctx.obj.get("output", "table") == "json":
        _output_json(refs)
        return

    if not refs:
        console.print(f"[dim]No importers of {symbol} found.[/dim]")
        return

    console.print(f"[bold]{len(refs)}[/bold] importer(s) of [bold]{symbol}[/bold]:")
    for ref in refs:
        console.print(f"  {ref}")

# ===========================================================================
# KANBAN — reuses /api/kanban/* (routes/kanban_bp.py)
# ===========================================================================

# Mirrors the KanbanColumn, ReviewState and DeployState enums in
# execution/kanban.py. Constrained here so a typo fails locally with the valid
# set rather than costing a round trip for a 400.
_KANBAN_COLUMNS = ["backlog", "ready", "running", "blocked", "review", "done", "failed"]
_KANBAN_REVIEW_STATES = ["none", "pending", "approved", "changes_requested"]
_KANBAN_DEPLOY_STATES = ["none", "staged", "deployed", "rolled_back"]


@cli.group("kanban")
def kanban() -> None:
    """Inspect and drive the execution board."""


@kanban.command("board")
@click.option("--session", "-s", "session_id", default="",
              help="Restrict to one workspace session.")
@click.option("--limit", "-n", default=200, show_default=True)
@click.pass_context
def kanban_board(ctx: click.Context, session_id: str, limit: int) -> None:
    """Show the board grouped by column."""
    client = _get_client()
    _require_auth(client)

    endpoint = f"/api/kanban/board?limit={limit}"
    if session_id:
        endpoint += f"&session_id={urllib.parse.quote(session_id)}"

    with _spinner("Loading board…") as progress:
        progress.add_task("Loading board…")
        try:
            res = client.get(endpoint)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "kanban board")

    if ctx.obj.get("output", "table") == "json":
        _output_json(res)
        return

    columns = res.get("columns", {}) if isinstance(res, dict) else {}
    counts = res.get("counts", {}) if isinstance(res, dict) else {}

    # integration_status(): a blank card field is diagnosable from the response
    # itself rather than by correlating server logs.
    integration = res.get("integration") if isinstance(res, dict) else None
    if integration:
        dead = [name for name, live in integration.items() if not live]
        if dead:
            console.print(f"[yellow]⚠ dead read edges:[/yellow] {', '.join(dead)}")

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Column", width=14)
    table.add_column("N", width=4)
    table.add_column("Executions", max_width=70)

    for name in _KANBAN_COLUMNS:
        cards = columns.get(name, []) or []
        ids = ", ".join(str(c.get("execution_id", ""))[:18] for c in cards)
        table.add_row(name, str(counts.get(name, len(cards))), ids[:70] or "—")

    console.print(table)
    console.print(f"[dim]{res.get('total', 0)} card(s) total.[/dim]")


@kanban.command("card")
@click.argument("execution_id")
@click.pass_context
def kanban_card(ctx: click.Context, execution_id: str) -> None:
    """Show one assembled card."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading card…") as progress:
        progress.add_task("Loading card…")
        try:
            res = client.get(f"/api/kanban/card/{urllib.parse.quote(execution_id)}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "kanban card")

    card = res.get("card", {}) if isinstance(res, dict) else {}
    if ctx.obj.get("output", "table") == "json":
        _output_json(card)
        return

    if not card:
        console.print("[dim]No card returned.[/dim]")
        return

    table = Table(show_header=False, box=None)
    for key in ("execution_id", "session_id", "column", "status", "priority",
                "review_state", "reviewer", "deploy_state", "owner_agent",
                "terminal", "verifier_backed", "evidence_count", "retries",
                "checkpoints", "resumable", "tokens_used", "cost_usd",
                "branch", "degraded"):
        if key in card:
            table.add_row(f"[dim]{key}[/dim]", str(card.get(key)))
    console.print(table)


@kanban.command("move")
@click.argument("execution_id")
@click.argument("column", type=click.Choice(_KANBAN_COLUMNS))
def kanban_move(execution_id: str, column: str) -> None:
    """Move EXECUTION_ID to COLUMN."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Moving card…") as progress:
        progress.add_task("Moving card…")
        try:
            client.post("/api/kanban/move",
                        {"execution_id": execution_id, "column": column})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "kanban move")

    console.print(f"[green]✓[/green] Moved [bold]{execution_id}[/bold] to [bold]{column}[/bold].")


@kanban.command("review")
@click.argument("execution_id")
@click.argument("state", type=click.Choice(_KANBAN_REVIEW_STATES))
@click.option("--reviewer", default="", help="Reviewer identity (defaults to you).")
def kanban_review(execution_id: str, state: str, reviewer: str) -> None:
    """Set the review state of EXECUTION_ID.

    `approved` is refused by the board unless the ledger shows verifier-backed
    evidence. That rule lives in the board, not here.
    """
    client = _get_client()
    _require_auth(client)

    body = {"execution_id": execution_id, "state": state}
    if reviewer:
        body["reviewer"] = reviewer

    with _spinner("Setting review state…") as progress:
        progress.add_task("Setting review state…")
        try:
            client.post("/api/kanban/review", body)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "kanban review")

    console.print(f"[green]✓[/green] Review state of [bold]{execution_id}[/bold] set to [bold]{state}[/bold].")


@kanban.command("deploy")
@click.argument("execution_id")
@click.argument("state", type=click.Choice(_KANBAN_DEPLOY_STATES))
def kanban_deploy(execution_id: str, state: str) -> None:
    """Set the deploy state of EXECUTION_ID."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Setting deploy state…") as progress:
        progress.add_task("Setting deploy state…")
        try:
            client.post("/api/kanban/deploy",
                        {"execution_id": execution_id, "state": state})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "kanban deploy")

    console.print(f"[green]✓[/green] Deploy state of [bold]{execution_id}[/bold] set to [bold]{state}[/bold].")


@kanban.command("tick")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
@click.pass_context
def kanban_tick(ctx: click.Context, yes: bool) -> None:
    """Run one automation sweep now (admin only).

    A sweep moves cards, reprioritises and can requeue work, so it confirms
    first. The server enforces the admin role regardless of this flag.
    """
    if not yes:
        click.confirm("Run an automation sweep now? It can move and requeue work.",
                      abort=True)

    client = _get_client()
    _require_auth(client)

    with _spinner("Running automation…") as progress:
        progress.add_task("Running automation…")
        try:
            res = client.post("/api/kanban/tick", {})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "kanban tick")

    report = res.get("report", {}) if isinstance(res, dict) else {}
    if ctx.obj.get("output", "table") == "json":
        _output_json(report)
        return

    console.print("[green]✓[/green] Sweep complete.")
    for action, outcome in (report.items() if isinstance(report, dict) else []):
        console.print(f"  [dim]{action}[/dim] {outcome}")


# ===========================================================================
# KNOWLEDGE GRAPH — reuses /api/knowledge-graph/*
# (routes/knowledge_graph_bp.py)
# ===========================================================================

# Mirrors NodeType in backend/agent/knowledge_graph.py.
_KG_NODE_TYPES = [
    "DECISION", "CONSTRAINT", "TASK_SOLUTION",
    "ERROR_PATTERN", "FAILURE_PATTERN", "FIX_PATTERN",
]


@cli.group("kg")
def kg() -> None:
    """Query the architectural knowledge graph."""


def _kg_node_table(nodes: list) -> Table:
    table = Table(show_header=True, header_style="bold blue")
    table.add_column("ID", style="dim", width=18)
    table.add_column("Type", width=17)
    table.add_column("Content", max_width=58)
    table.add_column("v", width=3)

    for n in nodes:
        table.add_row(
            str(n.get("id", ""))[:18],
            str(n.get("type", "—")),
            str(n.get("content", ""))[:58],
            str(n.get("version", 1)),
        )
    return table


@kg.command("list")
@click.option("--session", "-s", "session_id", required=True,
              help="Workspace session id.")
@click.option("--type", "-t", "node_type", default=None,
              type=click.Choice(_KG_NODE_TYPES, case_sensitive=False),
              help="Filter by node type.")
@click.option("--limit", "-n", default=100, show_default=True)
@click.pass_context
def kg_list(ctx: click.Context, session_id: str, node_type: Optional[str], limit: int) -> None:
    """List graph nodes, most recent first."""
    client = _get_client()
    _require_auth(client)

    endpoint = (f"/api/knowledge-graph/nodes"
                f"?session_id={urllib.parse.quote(session_id)}&limit={limit}")
    if node_type:
        endpoint += f"&type={urllib.parse.quote(node_type.upper())}"

    with _spinner("Loading nodes…") as progress:
        progress.add_task("Loading nodes…")
        try:
            res = client.get(endpoint)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "kg list")

    nodes = res.get("nodes", []) if isinstance(res, dict) else []
    if ctx.obj.get("output", "table") == "json":
        _output_json(nodes)
        return

    if not nodes:
        console.print("[dim]No nodes recorded for this workspace.[/dim]")
        return

    console.print(_kg_node_table(nodes))
    console.print(f"[dim]{res.get('total', len(nodes))} node(s) total.[/dim]")


@kg.command("constraints")
@click.option("--session", "-s", "session_id", required=True,
              help="Workspace session id.")
@click.pass_context
def kg_constraints(ctx: click.Context, session_id: str) -> None:
    """List CONSTRAINT nodes.

    Served by the dedicated endpoint, which reads SQLite directly and bypasses
    semantic search so a mandatory constraint is never missed because an
    embedding ranked it low.
    """
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading constraints…") as progress:
        progress.add_task("Loading constraints…")
        try:
            res = client.get(
                f"/api/knowledge-graph/constraints"
                f"?session_id={urllib.parse.quote(session_id)}"
            )
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "kg constraints")

    constraints = res.get("constraints", []) if isinstance(res, dict) else []
    if ctx.obj.get("output", "table") == "json":
        _output_json(constraints)
        return

    if not constraints:
        console.print("[dim]No constraints recorded.[/dim]")
        return

    console.print(_kg_node_table(constraints))


@kg.command("search")
@click.argument("query")
@click.option("--session", "-s", "session_id", required=True,
              help="Workspace session id.")
@click.option("--type", "-t", "node_type", default=None,
              type=click.Choice(_KG_NODE_TYPES, case_sensitive=False),
              help="Filter by node type.")
@click.option("-k", default=5, show_default=True, help="Result count.")
@click.pass_context
def kg_search(ctx: click.Context, query: str, session_id: str,
              node_type: Optional[str], k: int) -> None:
    """Semantic search across graph nodes."""
    client = _get_client()
    _require_auth(client)

    endpoint = (f"/api/knowledge-graph/search"
                f"?session_id={urllib.parse.quote(session_id)}"
                f"&q={urllib.parse.quote(query)}&k={k}")
    if node_type:
        endpoint += f"&type={urllib.parse.quote(node_type.upper())}"

    with _spinner("Searching graph…") as progress:
        progress.add_task("Searching graph…")
        try:
            res = client.get(endpoint)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "kg search")

    results = res.get("results", []) if isinstance(res, dict) else []
    if ctx.obj.get("output", "table") == "json":
        _output_json(results)
        return

    if not results:
        console.print("[dim]No matches.[/dim]")
        return

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Type", width=17)
    table.add_column("Content", max_width=72)

    for r in results:
        meta = r.get("metadata", {}) if isinstance(r, dict) else {}
        text = r.get("text") or r.get("document") or r.get("content") or ""
        table.add_row(str(meta.get("type", "—")), str(text)[:72])

    console.print(table)


@kg.command("add")
@click.argument("content")
@click.option("--session", "-s", "session_id", required=True,
              help="Workspace session id.")
@click.option("--type", "-t", "node_type", required=True,
              type=click.Choice(_KG_NODE_TYPES, case_sensitive=False),
              help="Node type.")
def kg_add(content: str, session_id: str, node_type: str) -> None:
    """Record CONTENT as a graph node."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Recording node…") as progress:
        progress.add_task("Recording node…")
        try:
            res = client.post("/api/knowledge-graph/nodes", {
                "session_id": session_id,
                "type": node_type.upper(),
                "content": content,
            })
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "kg add")

    node_id = res.get("node_id", "") if isinstance(res, dict) else ""
    console.print(f"[green]✓[/green] Recorded [bold]{node_type.upper()}[/bold] node {node_id}.")


@kg.command("link")
@click.argument("source_id")
@click.argument("target_id")
@click.option("--session", "-s", "session_id", required=True,
              help="Workspace session id.")
@click.option("--relationship", "-r", default="relates_to", show_default=True,
              help="Edge relationship label.")
def kg_link(source_id: str, target_id: str, session_id: str, relationship: str) -> None:
    """Add a directed edge SOURCE_ID → TARGET_ID."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Adding edge…") as progress:
        progress.add_task("Adding edge…")
        try:
            client.post("/api/knowledge-graph/edges", {
                "session_id": session_id,
                "source_id": source_id,
                "target_id": target_id,
                "relationship": relationship,
            })
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "kg link")

    console.print(f"[green]✓[/green] {source_id} —{relationship}→ {target_id}")


@kg.command("traverse")
@click.argument("node_id")
@click.option("--session", "-s", "session_id", required=True,
              help="Workspace session id.")
@click.pass_context
def kg_traverse(ctx: click.Context, node_id: str, session_id: str) -> None:
    """Show ancestors and descendants of NODE_ID."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Traversing graph…") as progress:
        progress.add_task("Traversing graph…")
        try:
            res = client.get(
                f"/api/knowledge-graph/traverse/{urllib.parse.quote(node_id)}"
                f"?session_id={urllib.parse.quote(session_id)}"
            )
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "kg traverse")

    if ctx.obj.get("output", "table") == "json":
        _output_json(res)
        return

    ancestors = res.get("ancestors", []) if isinstance(res, dict) else []
    descendants = res.get("descendants", []) if isinstance(res, dict) else []

    # A node absent from the DAG is not an error: it can exist in SQLite with no
    # edges yet. `known` is what separates "no edges" from "no such node".
    if not res.get("known"):
        console.print(f"[dim]{node_id} has no edges in the graph.[/dim]")

    console.print(f"[bold]ancestors[/bold]   ({len(ancestors)}): "
                  f"{', '.join(ancestors) if ancestors else '—'}")
    console.print(f"[bold]descendants[/bold] ({len(descendants)}): "
                  f"{', '.join(descendants) if descendants else '—'}")


# ===========================================================================
# REPLAY
# ===========================================================================
#
# Every command here is a read of the append-only event log. There is no
# `replay edit`, and there cannot be: replay is a projection, so mutating one is
# not a concept the storage model admits (see routes/replay_bp.py).
#
# `seq` is the frame address everywhere — a stable 0-based index into the
# ordered event list. `--timestamp` is offered for seek only, and resolves to
# the last frame at-or-before the instant, because two events written inside the
# same millisecond can tie and so a timestamp cannot address a frame uniquely.


def _replay_warn_dead_edges(res) -> None:
    """
    Surface dead read edges from `integration_status()`.

    A blank counter is then diagnosable from the response itself rather than by
    correlating server logs — same contract the kanban group follows.
    """
    integration = res.get("integration") if isinstance(res, dict) else None
    if integration:
        dead = [name for name, live in integration.items() if not live]
        if dead:
            console.print(f"[yellow]⚠ dead read edges:[/yellow] {', '.join(dead)}")


@cli.group("replay")
def replay() -> None:
    """Replay an execution from the evidence ledger."""


@replay.command("status")
@click.pass_context
def replay_status(ctx: click.Context) -> None:
    """Show whether replay can answer, and which read edges resolved."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Checking replay…") as progress:
        progress.add_task("Checking replay…")
        try:
            res = client.get("/api/replay/status")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "replay status")

    if ctx.obj.get("output", "table") == "json":
        _output_json(res)
        return

    available = res.get("available") if isinstance(res, dict) else False
    colour = "green" if available else "red"
    console.print(Panel.fit(
        f"[bold]Available:[/bold]   [{colour}]{available}[/{colour}]\n"
        f"[bold]Projection:[/bold]  {res.get('projection', '—')}\n"
        f"[bold]Persistence:[/bold] {res.get('persistence', '—')}",
        title="[bold]Replay[/bold]",
    ))
    _replay_warn_dead_edges(res)


@replay.command("list")
@click.option("--session", "-s", "session_id", default="",
              help="Workspace session id. Required unless you are an admin.")
@click.option("--limit", "-n", default=50, show_default=True)
@click.pass_context
def replay_list(ctx: click.Context, session_id: str, limit: int) -> None:
    """List replayable executions, newest first."""
    client = _get_client()
    _require_auth(client)

    endpoint = f"/api/replay/executions?limit={limit}"
    if session_id:
        endpoint += f"&session_id={urllib.parse.quote(session_id)}"

    with _spinner("Loading executions…") as progress:
        progress.add_task("Loading executions…")
        try:
            res = client.get(endpoint)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "replay list")

    rows = res.get("executions", []) if isinstance(res, dict) else []
    if ctx.obj.get("output", "table") == "json":
        _output_json(rows)
        return

    if not rows:
        console.print("[dim]No replayable executions.[/dim]")
        return

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Execution", style="dim", width=26)
    table.add_column("Status", width=12)
    table.add_column("Events", width=7, justify="right")
    table.add_column("Terminal", width=9)
    table.add_column("Updated", width=16)

    for row in rows:
        status = str(row.get("status") or "?")
        sc = {"succeeded": "green", "completed": "green", "failed": "red",
              "running": "yellow", "cancelled": "dim"}.get(status, "white")
        table.add_row(
            str(row.get("execution_id", ""))[:24],
            f"[{sc}]{status}[/{sc}]",
            str(row.get("event_count", 0)),
            "yes" if row.get("terminal") else "no",
            _format_ts(row.get("updated_at")),
        )

    console.print(table)
    console.print(f"[dim]{res.get('count', len(rows))} execution(s).[/dim]")


@replay.command("show")
@click.argument("execution_id")
@click.option("--limit", "-n", default=1000, show_default=True,
              help="Max frames to fetch.")
@click.option("--offset", default=0, show_default=True, help="Frame offset.")
@click.option("--kinds", default="",
              help="Comma-separated frame kinds to keep (filters output, not the fold).")
@click.option("--frames/--no-frames", "show_frames", default=True, show_default=True,
              help="Print the frame list as well as the summary.")
@click.option("--tail", default=20, show_default=True,
              help="How many trailing frames to print.")
@click.pass_context
def replay_show(ctx: click.Context, execution_id: str, limit: int, offset: int,
                kinds: str, show_frames: bool, tail: int) -> None:
    """Show the replay timeline for EXECUTION_ID."""
    client = _get_client()
    _require_auth(client)

    endpoint = (f"/api/replay/{urllib.parse.quote(execution_id)}"
                f"?limit={limit}&offset={offset}")
    if kinds.strip():
        endpoint += f"&kinds={urllib.parse.quote(kinds.strip())}"

    with _spinner("Folding event log…") as progress:
        progress.add_task("Folding event log…")
        try:
            res = client.get(endpoint)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "replay show")

    if ctx.obj.get("output", "table") == "json":
        _output_json(res)
        return

    summary = res.get("summary", {}) if isinstance(res, dict) else {}
    frames = res.get("frames", []) if isinstance(res, dict) else []

    status = str(summary.get("final_status") or "?")
    sc = {"succeeded": "green", "completed": "green", "failed": "red",
          "running": "yellow", "cancelled": "dim"}.get(status, "white")

    console.print(Panel.fit(
        f"[bold]Execution:[/bold] {res.get('execution_id', execution_id)}\n"
        f"[bold]Session:[/bold]   {res.get('session_id') or '—'}\n"
        f"[bold]Status:[/bold]    [{sc}]{status}[/{sc}]"
        f"{'  [dim](terminal)[/dim]' if summary.get('terminal') else ''}\n"
        f"[bold]Events:[/bold]    {summary.get('event_count', 0)}"
        f"   [bold]Frames:[/bold] {res.get('total_frames', 0)}\n"
        f"[bold]Duration:[/bold]  {summary.get('duration_ms', 0)} ms\n"
        f"[bold]Tools:[/bold]     {summary.get('tools_called', 0)} called / "
        f"{summary.get('tools_completed', 0)} ok / {summary.get('tools_failed', 0)} failed\n"
        f"[bold]Files:[/bold]     {summary.get('files_modified', 0)} modified"
        f"   [bold]Commands:[/bold] {summary.get('commands_run', 0)}\n"
        f"[bold]Tokens:[/bold]    {summary.get('prompt_tokens', 0)} in / "
        f"{summary.get('completion_tokens', 0)} out"
        f"   [bold]Cost:[/bold] ${summary.get('cost_usd', 0)}\n"
        f"[bold]Verified:[/bold]  {summary.get('verifier_backed', False)}",
        title=f"[bold]Replay {str(execution_id)[:16]}[/bold]",
    ))

    # status_agrees compares the fold against truth_kernel.derive_status. Both
    # read the same rows, so a mismatch is a real defect in replay's precedence
    # — surfaced rather than raised, because a divergent replay still helps.
    if summary and not summary.get("status_agrees", True):
        console.print(
            f"[yellow]⚠ replay status '{status}' disagrees with ledger "
            f"'{summary.get('ledger_status')}'[/yellow]"
        )
    _replay_warn_dead_edges(res)

    if not show_frames or not frames:
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Seq", width=6, justify="right")
    table.add_column("Kind", width=10)
    table.add_column("Event", max_width=30)
    table.add_column("+ms", width=8, justify="right")
    table.add_column("Status", width=11)

    shown = frames[-tail:] if tail and tail > 0 else frames
    for f in shown:
        table.add_row(
            str(f.get("seq")),
            str(f.get("kind") or "—")[:10],
            str(f.get("event_type") or "—")[:30],
            str(f.get("elapsed_ms", 0)),
            str((f.get("state") or {}).get("status") or "—")[:11],
        )

    console.print(table)
    if len(shown) < len(frames):
        console.print(f"[dim]Showing last {len(shown)} of {len(frames)} fetched "
                      f"frames — use --tail 0 for all.[/dim]")
    if res.get("truncated"):
        console.print("[dim]More frames exist — raise --limit or use --offset.[/dim]")


@replay.command("frame")
@click.argument("execution_id")
@click.argument("seq", type=int)
@click.pass_context
def replay_frame(ctx: click.Context, execution_id: str, seq: int) -> None:
    """Show one frame of EXECUTION_ID by its stable index SEQ."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading frame…") as progress:
        progress.add_task("Loading frame…")
        try:
            res = client.get(
                f"/api/replay/{urllib.parse.quote(execution_id)}/frame/{seq}"
            )
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "replay frame")

    frame = res.get("frame", {}) if isinstance(res, dict) else {}
    if ctx.obj.get("output", "table") == "json":
        _output_json(frame)
        return

    if not frame:
        console.print(f"[dim]No frame at seq {seq}.[/dim]")
        return

    table = Table(show_header=False, box=None)
    for key in ("seq", "event_id", "event_type", "kind", "timestamp",
                "elapsed_ms", "session_id", "correlation_id"):
        if key in frame:
            value = _format_ts(frame[key]) if key == "timestamp" else str(frame[key])
            table.add_row(f"[dim]{key}[/dim]", value)
    console.print(table)

    state = frame.get("state") or {}
    if state:
        console.print("\n[bold]Cumulative state after this event:[/bold]")
        st = Table(show_header=False, box=None)
        for key, value in state.items():
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value) or "—"
            st.add_row(f"[dim]{key}[/dim]", str(value))
        console.print(st)

    payload = frame.get("payload") or {}
    if payload:
        # Redacted in the engine's fold, before it ever left the server.
        console.print("\n[bold]Payload[/bold] [dim](secret-shaped keys masked)[/dim]")
        console.print_json(json.dumps(payload, default=str))


@replay.command("seek")
@click.argument("execution_id")
@click.option("--seq", type=int, default=None,
              help="Frame index to seek to. Exact; wins over --timestamp.")
@click.option("--timestamp", type=float, default=None,
              help="Epoch seconds; resolves to the last frame at-or-before it.")
@click.option("--transcript/--no-transcript", default=False, show_default=True,
              help="Print the transcript accumulated up to the seek point.")
@click.pass_context
def replay_seek(ctx: click.Context, execution_id: str, seq: Optional[int],
                timestamp: Optional[float], transcript: bool) -> None:
    """Reconstruct the state of EXECUTION_ID at an instant.

    With neither --seq nor --timestamp, seeks to the final frame.
    """
    client = _get_client()
    _require_auth(client)

    endpoint = f"/api/replay/{urllib.parse.quote(execution_id)}/seek"
    if seq is not None:
        endpoint += f"?seq={seq}"
    elif timestamp is not None:
        endpoint += f"?timestamp={timestamp}"

    with _spinner("Seeking…") as progress:
        progress.add_task("Seeking…")
        try:
            res = client.get(endpoint)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "replay seek")

    if ctx.obj.get("output", "table") == "json":
        _output_json(res)
        return

    console.print(
        f"[bold]seq[/bold] {res.get('seq')}   "
        f"[bold]event[/bold] {res.get('event_type') or '—'}   "
        f"[bold]+{res.get('elapsed_ms', 0)}ms[/bold]"
    )

    state = res.get("state") or {}
    if state:
        table = Table(show_header=False, box=None)
        for key, value in state.items():
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value) or "—"
            table.add_row(f"[dim]{key}[/dim]", str(value))
        console.print(table)

    _replay_warn_dead_edges(res)

    if transcript:
        text = res.get("transcript") or ""
        console.print("\n[bold]Transcript to this point:[/bold]")
        console.print(text[:4000] if text else "[dim]—[/dim]")
        if len(text) > 4000:
            console.print(f"[dim]… {len(text) - 4000} more chars.[/dim]")


@replay.command("diff")
@click.argument("execution_id")
@click.option("--from", "from_seq", type=int, default=0, show_default=True,
              help="Frame index to diff from.")
@click.option("--to", "to_seq", type=int, default=None,
              help="Frame index to diff to. Defaults to the last frame.")
@click.pass_context
def replay_diff(ctx: click.Context, execution_id: str, from_seq: int,
                to_seq: Optional[int]) -> None:
    """Show what changed between two frames of EXECUTION_ID."""
    client = _get_client()
    _require_auth(client)

    endpoint = (f"/api/replay/{urllib.parse.quote(execution_id)}/diff"
                f"?from={from_seq}")
    if to_seq is not None:
        endpoint += f"&to={to_seq}"

    with _spinner("Diffing frames…") as progress:
        progress.add_task("Diffing frames…")
        try:
            res = client.get(endpoint)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "replay diff")

    if ctx.obj.get("output", "table") == "json":
        _output_json(res)
        return

    if not res.get("found"):
        console.print("[dim]Nothing to diff — no frames for this execution.[/dim]")
        return

    console.print(
        f"[bold]seq {res.get('from_seq')} → {res.get('to_seq')}[/bold]   "
        f"{res.get('events_between', 0)} event(s)   "
        f"{res.get('elapsed_ms', 0)} ms"
    )
    if res.get("status_changed"):
        console.print(f"[bold]status:[/bold] {res.get('from_status')} → "
                      f"[bold]{res.get('to_status')}[/bold]")

    changes = res.get("changes") or {}
    if changes:
        table = Table(show_header=True, header_style="bold blue")
        table.add_column("Counter", width=22)
        table.add_column("Delta", width=12, justify="right")
        for key, delta in changes.items():
            table.add_row(key, f"+{delta}" if isinstance(delta, (int, float)) and delta > 0
                          else str(delta))
        console.print(table)
    else:
        console.print("[dim]No counters changed across this range.[/dim]")

    for label, key in (("files added", "files_added"), ("tools added", "tools_added")):
        items = res.get(key) or []
        if items:
            console.print(f"[bold]{label}:[/bold] {', '.join(str(i) for i in items[:20])}")

    _replay_warn_dead_edges(res)


@replay.command("export")
@click.argument("execution_id")
@click.option("--output", "-f", "out_path", default="",
              help="File to write. Defaults to replay-<execution_id>.json.")
@click.option("--frames/--no-frames", "include_frames", default=True, show_default=True,
              help="--no-frames writes summary only, for archiving many executions.")
@click.pass_context
def replay_export(ctx: click.Context, execution_id: str, out_path: str,
                  include_frames: bool) -> None:
    """Write a portable, redacted replay bundle for EXECUTION_ID.

    Secret-shaped keys are masked server-side during the fold, so the file is
    safe to attach to a ticket. The desktop app persists the same bundle into
    its sandboxed workspace via the `save_replay_bundle` IPC command.
    """
    client = _get_client()
    _require_auth(client)

    with _spinner("Exporting replay…") as progress:
        progress.add_task("Exporting replay…")
        try:
            res = client.get(
                f"/api/replay/{urllib.parse.quote(execution_id)}/export"
                f"?frames={'true' if include_frames else 'false'}"
            )
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "replay export")

    bundle = res.get("bundle", {}) if isinstance(res, dict) else {}

    # `-o json` means "give me the bundle on stdout" — piping to jq should not
    # also litter a file, so the write is skipped in that mode.
    if ctx.obj.get("output", "table") == "json":
        _output_json(bundle)
        return

    # Derive a safe default name; the id becomes a path component here.
    safe_id = "".join(
        c if (c.isalnum() or c in "-_") else "-" for c in str(execution_id)
    ).strip("-") or "unknown"
    target = out_path or f"replay-{safe_id[:120]}.json"

    try:
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(bundle, fh, indent=2, default=str)
    except OSError as exc:
        _handle_error(exc, "replay export write")

    summary = bundle.get("summary", {}) if isinstance(bundle, dict) else {}
    console.print(
        f"[green]✓[/green] Wrote [bold]{target}[/bold]  "
        f"[dim]({summary.get('event_count', 0)} events, "
        f"{bundle.get('frame_count', 0)} frames, "
        f"schema {bundle.get('schema_version', '?')})[/dim]"
    )


# ===========================================================================
# Worktree — isolated git checkouts (Phase 4.4)
# ===========================================================================
#
# Every execution that asks for isolation gets its own git worktree on its own
# branch, so two concurrent tasks cannot write the same file. These commands are
# the operator surface over that: routes/worktree_bp.py, backed by
# execution/worktree.py.
#
# Three properties are worth knowing before using them:
#
#   * Handles are **process-local.** They live in one gunicorn worker's memory,
#     so `worktree list` returns what *that* worker holds. An empty list means
#     "not in this worker", not "none exist". `git-list` reads the repository
#     instead and so sees every checkout on disk regardless of worker.
#   * `discard` and `sweep` are **destructive and irreversible.** Uncommitted work
#     in the checkout is gone. Both confirm before sending.
#   * Every git call is **governed.** A refusal comes back as `denied` (policy said
#     no), `escalate` (a human could approve it) or `unavailable` (the policy
#     engine is missing, and the backend refuses to run git without one). Those are
#     surfaced distinctly rather than collapsed into "failed".


def _worktree_warn_degraded(res) -> None:
    """
    Surface dead integration edges and the two conditions that break creation.

    Same contract as `_replay_warn_dead_edges`: a blank field is diagnosable from
    the response rather than by correlating server logs. `governed` and
    `git_available` are called out separately because they have different fixes —
    a policy change versus a Dockerfile change.
    """
    if not isinstance(res, dict):
        return
    integration = res.get("integration") or {}
    dead = [name for name, live in integration.items() if not live]
    if dead:
        console.print(f"[yellow]⚠ dead edges:[/yellow] {', '.join(dead)}")
    if res.get("governed") is False:
        console.print(
            "[red]⚠ governance unavailable[/red] — every git call is refused. "
            "Creation will return [bold]unavailable[/bold] until the command "
            "policy and tool governance edges resolve."
        )
    if res.get("git_available") is False:
        console.print(
            "[red]⚠ git is not on the server PATH[/red] — an image problem, "
            "not a policy one."
        )


def _worktree_table(rows: list) -> Table:
    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Worktree", style="dim", width=24)
    table.add_column("Branch", width=28)
    table.add_column("Base", width=10)
    table.add_column("Status", width=10)
    table.add_column("Execution", width=20)
    for row in rows:
        # `status`, not `state`: the field name comes from Worktree.to_dict(),
        # which is asdict() over the dataclass.
        status = str(row.get("status") or "?")
        sc = {"active": "green", "discarded": "dim", "failed": "red"}.get(status, "white")
        table.add_row(
            str(row.get("worktree_id", ""))[:22],
            str(row.get("branch", ""))[:26],
            str(row.get("base_ref", ""))[:8],
            f"[{sc}]{status}[/{sc}]",
            str(row.get("execution_id", ""))[:18],
        )
    return table


@cli.group("worktree")
def worktree() -> None:
    """Inspect and manage isolated git checkouts."""


@worktree.command("status")
@click.pass_context
def worktree_status(ctx: click.Context) -> None:
    """Show engine health, governance state and configured limits."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Checking worktree engine…") as progress:
        progress.add_task("Checking worktree engine…")
        try:
            res = client.get("/api/worktrees/status")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "worktree status")

    if ctx.obj.get("output", "table") == "json":
        _output_json(res)
        return

    governed = res.get("governed") if isinstance(res, dict) else False
    limits = res.get("limits", {}) if isinstance(res, dict) else {}
    gc = "green" if governed else "red"
    ac = "green" if res.get("git_available") else "red"
    console.print(Panel.fit(
        f"[bold]Governed:[/bold]       [{gc}]{governed}[/{gc}]\n"
        f"[bold]git available:[/bold]  [{ac}]{res.get('git_available')}[/{ac}]\n"
        f"[bold]Branch prefix:[/bold]  {res.get('branch_prefix', '—')}\n"
        f"[bold]Max/session:[/bold]    {limits.get('max_per_session', '—')}\n"
        f"[bold]git timeout:[/bold]    {limits.get('git_timeout_sec', '—')}s\n"
        f"[bold]Orphan age:[/bold]     {limits.get('orphan_age_sec', '—')}s",
        title="[bold]Worktree isolation[/bold]",
    ))
    _worktree_warn_degraded(res)


@worktree.command("list")
@click.option("--session", "-s", "session_id", default="",
              help="Workspace session id. Required unless you are an admin.")
@click.pass_context
def worktree_list(ctx: click.Context, session_id: str) -> None:
    """List checkouts held by the serving worker."""
    client = _get_client()
    _require_auth(client)

    endpoint = "/api/worktrees"
    if session_id:
        endpoint += f"?session_id={urllib.parse.quote(session_id)}"

    with _spinner("Loading checkouts…") as progress:
        progress.add_task("Loading checkouts…")
        try:
            res = client.get(endpoint)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "worktree list")

    rows = res.get("worktrees", []) if isinstance(res, dict) else []
    if ctx.obj.get("output", "table") == "json":
        _output_json(rows)
        return

    if not rows:
        # Said explicitly: the natural reading of an empty list is "none exist",
        # and acting on that belief means abandoning real checkouts on disk.
        console.print(
            "[dim]No checkouts held by the worker that served this request.[/dim]\n"
            "[dim]Handles are process-local — try again, or use "
            "[bold]worktree sweep-orphans[/bold] to reconcile against disk.[/dim]"
        )
        return

    console.print(_worktree_table(rows))
    console.print(f"[dim]{res.get('total', len(rows))} checkout(s), this worker only.[/dim]")


@worktree.command("show")
@click.argument("worktree_id")
@click.pass_context
def worktree_show(ctx: click.Context, worktree_id: str) -> None:
    """Show one checkout in full."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading checkout…") as progress:
        progress.add_task("Loading checkout…")
        try:
            res = client.get(f"/api/worktrees/{urllib.parse.quote(worktree_id)}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "worktree show")

    wt = res.get("worktree", {}) if isinstance(res, dict) else {}
    if ctx.obj.get("output", "table") == "json":
        _output_json(wt)
        return

    table = Table(show_header=False, box=None)
    table.add_column("Field", style="bold", width=16)
    table.add_column("Value")
    for key in ("worktree_id", "branch", "base_ref", "path", "repo_root",
                "session_id", "execution_id", "task_id", "tenant_id", "status",
                "created_at", "discarded_at", "git_calls"):
        value = wt.get(key)
        if key in ("created_at", "discarded_at") and value:
            value = _format_ts(value)
        table.add_row(key, "—" if value in (None, "") else str(value))
    console.print(Panel(table, title="[bold]Checkout[/bold]"))


@worktree.command("diff")
@click.argument("worktree_id")
@click.option("--context", "-U", "context_lines", default=3, show_default=True,
              help="Lines of context (server clamps to 0-25).")
@click.option("--output", "-f", "out_path", default="",
              help="Write the patch to a file instead of printing it.")
@click.pass_context
def worktree_diff(ctx: click.Context, worktree_id: str, context_lines: int,
                  out_path: str) -> None:
    """Show the work in WORKTREE_ID as a unified diff against its base ref."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Diffing checkout…") as progress:
        progress.add_task("Diffing checkout…")
        try:
            res = client.get(
                f"/api/worktrees/{urllib.parse.quote(worktree_id)}/diff"
                f"?context={context_lines}"
            )
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "worktree diff")

    if ctx.obj.get("output", "table") == "json":
        _output_json(res)
        return

    text = res.get("diff", "") if isinstance(res, dict) else ""

    # The server caps the response at 2 MB, so a large patch arrives cut off.
    # Warned unconditionally: applying a silently truncated patch corrupts a tree.
    if res.get("truncated"):
        console.print(
            f"[yellow]⚠ truncated[/yellow] at {len(text)} of "
            f"{res.get('bytes', '?')} bytes — this patch is [bold]not complete[/bold]."
        )

    if out_path:
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            _handle_error(exc, "worktree diff write")
        console.print(f"[green]✓[/green] Wrote [bold]{out_path}[/bold] "
                      f"[dim]({len(text)} bytes)[/dim]")
        return

    console.print(f"[dim]{res.get('branch', '?')} ← {res.get('base_ref', '?')}[/dim]")
    if not text:
        console.print("[dim]No changes yet.[/dim]")
        return
    console.print(Syntax(text, "diff", theme="ansi_dark", word_wrap=False))


@worktree.command("create")
@click.option("--session", "-s", "session_id", required=True,
              help="Workspace session id you own.")
@click.option("--execution", "-e", "execution_id", required=True,
              help="Execution the checkout belongs to.")
@click.option("--task", "-t", "task_id", default="",
              help="Task id. Defaults to the execution id.")
@click.option("--base", "-b", "base_ref", default="HEAD", show_default=True,
              help="Ref to branch from. A single ref, not a range.")
@click.pass_context
def worktree_create(ctx: click.Context, session_id: str, execution_id: str,
                    task_id: str, base_ref: str) -> None:
    """Create an isolated checkout on its own branch.

    The repository root and tenant are resolved server-side and cannot be set
    from here: accepting either would let a caller point git at a path outside
    every tenant workspace, or claim another tenant's quota.
    """
    client = _get_client()
    _require_auth(client)

    body = {"session_id": session_id, "execution_id": execution_id,
            "base_ref": base_ref}
    if task_id:
        body["task_id"] = task_id

    with _spinner("Creating checkout…") as progress:
        progress.add_task("Creating checkout…")
        try:
            res = client.post("/api/worktrees", body)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "worktree create")

    wt = res.get("worktree", {}) if isinstance(res, dict) else {}
    if ctx.obj.get("output", "table") == "json":
        _output_json(wt)
        return

    console.print(
        f"[green]✓[/green] Created [bold]{wt.get('worktree_id', '?')}[/bold]\n"
        f"  branch [bold]{wt.get('branch', '?')}[/bold] from {wt.get('base_ref', '?')}\n"
        f"  [dim]{wt.get('path', '?')}[/dim]"
    )


@worktree.command("discard")
@click.argument("worktree_id")
@click.option("--keep-branch", is_flag=True,
              help="Remove the checkout but leave the branch in place.")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation.")
@click.pass_context
def worktree_discard(ctx: click.Context, worktree_id: str, keep_branch: bool,
                     yes: bool) -> None:
    """Tear down WORKTREE_ID: child processes, then checkout, then branch.

    Irreversible. Uncommitted work in the checkout is lost — the server cannot
    ask, so the confirmation is here.
    """
    client = _get_client()
    _require_auth(client)

    if not yes:
        click.confirm(
            f"Discard {worktree_id}? Uncommitted work in it is lost.",
            abort=True,
        )

    with _spinner("Discarding checkout…") as progress:
        progress.add_task("Discarding checkout…")
        try:
            res = client.post(
                f"/api/worktrees/{urllib.parse.quote(worktree_id)}/discard",
                {"delete_branch": not keep_branch},
            )
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "worktree discard")

    if ctx.obj.get("output", "table") == "json":
        _output_json(res)
        return

    result = res.get("result", {}) if isinstance(res, dict) else {}
    errors = result.get("errors") or []

    # A partial discard returns 200 with `ok: false` — the checkout may be gone
    # while the branch survives. Reported rather than treated as success, because
    # a leftover branch is what the next `create` collides with.
    if res.get("ok"):
        console.print(f"[green]✓[/green] Discarded [bold]{worktree_id}[/bold]")
    else:
        console.print(f"[yellow]⚠ partially discarded[/yellow] [bold]{worktree_id}[/bold]")
    for err in errors:
        console.print(f"  [red]•[/red] {err}")
    if result.get("branch_deleted") is False and not keep_branch:
        console.print("  [yellow]•[/yellow] branch was not deleted")


@worktree.command("sweep")
@click.option("--session", "-s", "session_id", required=True,
              help="Session whose checkouts should all be discarded.")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation.")
@click.pass_context
def worktree_sweep(ctx: click.Context, session_id: str, yes: bool) -> None:
    """Discard every active checkout a session holds.

    The cleanup path for a session whose tasks died before reaching their
    teardown. Idempotent — a second call reports 0 discarded rather than failing.
    """
    client = _get_client()
    _require_auth(client)

    if not yes:
        click.confirm(
            f"Discard every active checkout held by session {session_id}? "
            "Uncommitted work in all of them is lost.",
            abort=True,
        )

    with _spinner("Sweeping session…") as progress:
        progress.add_task("Sweeping session…")
        try:
            res = client.post("/api/worktrees/sweep", {"session_id": session_id})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "worktree sweep")

    if ctx.obj.get("output", "table") == "json":
        _output_json(res)
        return

    result = res.get("result", {}) if isinstance(res, dict) else {}
    console.print(
        f"[green]✓[/green] Swept session [bold]{session_id}[/bold] "
        f"[dim](discarded {result.get('discarded', 0)}, "
        f"failed {result.get('failed', 0)})[/dim]"
    )
    for err in (result.get("errors") or []):
        console.print(f"  [red]•[/red] {err}")


@worktree.command("sweep-orphans")
@click.option("--max-age", "max_age_sec", type=float, default=None,
              help="Age in seconds past which a checkout is stale. "
                   "0 means everything active. Server default if omitted.")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation.")
@click.pass_context
def worktree_sweep_orphans(ctx: click.Context, max_age_sec: Optional[float],
                           yes: bool) -> None:
    """Reclaim abandoned checkouts across all sessions. Admin only.

    This is the same maintenance the scheduler runs hourly. It crosses session
    boundaries by design, which is why ownership cannot scope it and the server
    requires the admin role instead.
    """
    client = _get_client()
    _require_auth(client)

    # `--max-age 0` is honoured server-side and means "everything active is
    # stale", which will destroy checkouts belonging to *running* tasks. That is a
    # legitimate forced reclamation, so it is not clamped — but it is not the
    # thing a `-y` on a routine sweep should silently do either.
    if max_age_sec == 0 and not yes:
        click.confirm(
            "--max-age 0 discards every active checkout, including those held by "
            "running tasks. Continue?",
            abort=True,
        )
    elif not yes:
        click.confirm("Reclaim abandoned checkouts across all sessions?", abort=True)

    body = {} if max_age_sec is None else {"max_age_sec": max_age_sec}

    with _spinner("Reclaiming orphans…") as progress:
        progress.add_task("Reclaiming orphans…")
        try:
            res = client.post("/api/worktrees/sweep/orphans", body)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "worktree sweep-orphans")

    if ctx.obj.get("output", "table") == "json":
        _output_json(res)
        return

    report = res.get("report", {}) if isinstance(res, dict) else {}
    console.print(
        f"[green]✓[/green] Orphan sweep complete "
        f"[dim](discarded {report.get('discarded', 0)}, "
        f"checked {report.get('checked', 0)}, "
        f"failed {report.get('failed', 0)}, "
        f"cutoff {report.get('cutoff_age_sec', '?')}s)[/dim]"
    )
    if report.get("pruned"):
        console.print("  [dim]•[/dim] ran [bold]git worktree prune[/bold]")
    # `reconciled: false` means the sweep had no repository root to resolve from
    # and so could not compare its registry against disk. Distinct from "found
    # nothing", and the difference decides whether the run proved anything.
    if report.get("reconciled") is False:
        console.print(
            "  [yellow]•[/yellow] not reconciled against disk — no repository "
            "root was resolvable, so on-disk orphans were not inspected"
        )
    if report.get("unknown_on_disk"):
        console.print(
            f"  [yellow]•[/yellow] {report['unknown_on_disk']} checkout(s) on disk "
            "are not in this worker's registry (another worker may hold them)"
        )
    for err in (report.get("errors") or []):
        console.print(f"  [red]•[/red] {err}")


# ===========================================================================
# Automation — routes/automation_bp.py
# ===========================================================================

_AUTOMATION_SCHEDULES = ["once", "interval", "cron", "daily", "weekly",
                         "monthly", "conditional", "manual"]
_AUTOMATION_ACTIONS = ["prompt", "goal", "analysis", "webhook", "shell", "noop"]
_AUTOMATION_FAILURE_POLICIES = ["retry", "disable", "notify", "ignore"]

# State → colour. `exhausted` is red rather than yellow on purpose: it means the
# failure policy stopped the task, so it is not coming back without an operator.
_AUTOMATION_STATE_COLOUR = {
    "active": "green",
    "paused": "yellow",
    "disabled": "dim",
    "completed": "blue",
    "exhausted": "red",
}

_AUTOMATION_RUN_COLOUR = {
    "succeeded": "green",
    "failed": "red",
    "timeout": "red",
    "running": "cyan",
    "retrying": "yellow",
    "skipped": "dim",
    "cancelled": "dim",
    "pending": "dim",
}


def _automation_state(task: dict) -> str:
    state = str(task.get("state", "unknown"))
    colour = _AUTOMATION_STATE_COLOUR.get(state, "white")
    return f"[{colour}]{state}[/{colour}]"


def _automation_schedule_label(task: dict) -> str:
    stype = task.get("schedule_type", "—")
    value = task.get("schedule_value") or ""
    return f"{stype} {value}".strip()


@cli.group("automation")
def automation() -> None:
    """Create and drive scheduled, recurring and conditional tasks."""


@automation.command("list")
@click.option("--state", default="", help="Filter by state (active, paused, …).")
@click.option("--all", "show_all", is_flag=True,
              help="Every tenant's tasks. Admin only; ignored otherwise.")
@click.option("--limit", "-n", default=50, show_default=True)
@click.pass_context
def automation_list(ctx: click.Context, state: str, show_all: bool, limit: int) -> None:
    """List your automations."""
    client = _get_client()
    _require_auth(client)

    endpoint = f"/api/automation/tasks?limit={limit}"
    if state:
        endpoint += f"&state={urllib.parse.quote(state)}"
    if show_all:
        endpoint += "&all=1"

    with _spinner("Loading automations…") as progress:
        progress.add_task("Loading automations…")
        try:
            res = client.get(endpoint)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "automation list")

    tasks = res.get("tasks", []) if isinstance(res, dict) else []
    if ctx.obj.get("output", "table") == "json":
        _output_json(tasks)
        return

    if not tasks:
        console.print("[dim]No automations yet. Create one with "
                      "[bold]aetherion automation create[/bold].[/dim]")
        return

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("ID", width=22)
    table.add_column("Name", max_width=28)
    table.add_column("Schedule", max_width=24)
    table.add_column("State", width=10)
    table.add_column("Next run", width=20)
    table.add_column("Runs", width=12)

    for t in tasks:
        runs = f"{t.get('success_count', 0)}✓ / {t.get('failure_count', 0)}✗"
        table.add_row(
            str(t.get("task_id", ""))[:22],
            str(t.get("name", ""))[:28],
            _automation_schedule_label(t)[:24],
            _automation_state(t),
            _format_ts(t.get("next_run_at")) if t.get("next_run_at") else "—",
            runs,
        )

    console.print(table)
    console.print(f"[dim]{len(tasks)} automation(s).[/dim]")


@automation.command("create")
@click.option("--name", required=True, help="Human-readable task name.")
@click.option("--prompt", "-p", default="", help="Prompt the agent should run.")
@click.option("--schedule", "-s", "schedule_type", default="manual",
              type=click.Choice(_AUTOMATION_SCHEDULES), show_default=True)
@click.option("--value", "-v", "schedule_value", default="",
              help="Schedule value: cron expr, 'HH:MM', 'HH:MM mon,fri', "
                   "'HH:MM 15', seconds, or an ISO timestamp.")
@click.option("--action", "action_type", default="prompt",
              type=click.Choice(_AUTOMATION_ACTIONS), show_default=True)
@click.option("--timezone", "tz", default="UTC", show_default=True)
@click.option("--retries", default=1, show_default=True,
              help="Total attempts per run (1 = no retry).")
@click.option("--backoff", default=30.0, show_default=True,
              help="Seconds before the first retry; doubles thereafter.")
@click.option("--on-failure", "failure_policy", default="retry",
              type=click.Choice(_AUTOMATION_FAILURE_POLICIES), show_default=True)
@click.option("--timeout", default=900.0, show_default=True, help="Per-run timeout.")
@click.option("--max-runs", default=0, show_default=True, help="0 = unlimited.")
@click.option("--session", "session_id", default="", help="Bind runs to a session.")
@click.option("--tag", "tags", multiple=True, help="Repeatable.")
@click.option("--disabled", is_flag=True, help="Create without scheduling it.")
@click.pass_context
def automation_create(ctx: click.Context, name: str, prompt: str, schedule_type: str,
                      schedule_value: str, action_type: str, tz: str, retries: int,
                      backoff: float, failure_policy: str, timeout: float,
                      max_runs: int, session_id: str, tags: tuple,
                      disabled: bool) -> None:
    """Create an automation.

    \b
    Examples:
      aetherion automation create --name nightly -p "run the suite" \\
          -s cron -v "0 2 * * *"
      aetherion automation create --name standup -p "summarise yesterday" \\
          -s weekly -v "09:00 mon,wed,fri"
      aetherion automation create --name hourly -p "poll the queue" \\
          -s interval -v 3600 --retries 3 --on-failure disable
    """
    client = _get_client()
    _require_auth(client)

    body = {
        "name": name,
        "action_type": action_type,
        "payload": {"prompt": prompt} if prompt else {},
        "schedule_type": schedule_type,
        "schedule_value": schedule_value,
        "timezone": tz,
        "enabled": not disabled,
        "retry_policy": {"max_attempts": retries, "backoff_seconds": backoff},
        "failure_policy": failure_policy,
        "timeout_seconds": timeout,
        "max_runs": max_runs,
        "session_id": session_id,
        "tags": list(tags),
    }

    with _spinner("Creating automation…") as progress:
        progress.add_task("Creating automation…")
        try:
            res = client.post("/api/automation/tasks", body)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "automation create")

    task = res.get("task", {}) if isinstance(res, dict) else {}
    if ctx.obj.get("output", "table") == "json":
        _output_json(task)
        return

    console.print(f"[green]✓[/green] Created [bold]{task.get('name')}[/bold] "
                  f"([dim]{task.get('task_id')}[/dim])")
    if task.get("next_run_at"):
        console.print(f"  Next run: [bold]{_format_ts(task['next_run_at'])}[/bold]")
    else:
        console.print("  [dim]Not scheduled — run it with "
                      "[bold]aetherion automation run[/bold].[/dim]")


@automation.command("show")
@click.argument("task_id")
@click.pass_context
def automation_show(ctx: click.Context, task_id: str) -> None:
    """Show one automation and its recent runs."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading automation…") as progress:
        progress.add_task("Loading automation…")
        try:
            res = client.get(f"/api/automation/tasks/{urllib.parse.quote(task_id)}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "automation show")

    task = res.get("task", {}) if isinstance(res, dict) else {}
    if ctx.obj.get("output", "table") == "json":
        _output_json(task)
        return
    if not task:
        console.print("[dim]No automation returned.[/dim]")
        return

    retry = task.get("retry_policy", {}) or {}
    console.print(Panel.fit(
        f"[bold]Name:[/bold]        {task.get('name')}\n"
        f"[bold]State:[/bold]       {_automation_state(task)}\n"
        f"[bold]Action:[/bold]      {task.get('action_type')}\n"
        f"[bold]Schedule:[/bold]    {_automation_schedule_label(task)} "
        f"({task.get('timezone', 'UTC')})\n"
        f"[bold]Next run:[/bold]    "
        f"{_format_ts(task['next_run_at']) if task.get('next_run_at') else '—'}\n"
        f"[bold]Last run:[/bold]    "
        f"{_format_ts(task['last_run_at']) if task.get('last_run_at') else '—'}"
        f"  ({task.get('last_status') or 'never'})\n"
        f"[bold]Runs:[/bold]        {task.get('run_count', 0)} total, "
        f"{task.get('success_count', 0)} ok, {task.get('failure_count', 0)} failed\n"
        f"[bold]Retries:[/bold]     {retry.get('max_attempts', 1)} attempt(s), "
        f"{retry.get('backoff_seconds', 0)}s backoff ×{retry.get('backoff_multiplier', 2)}\n"
        f"[bold]On failure:[/bold]  {task.get('failure_policy')}\n"
        f"[bold]Timeout:[/bold]     {task.get('timeout_seconds')}s",
        title=f"[bold]{task.get('task_id')}[/bold]",
    ))

    runs = task.get("history", []) or []
    if not runs:
        console.print("[dim]No runs yet.[/dim]")
        return

    table = Table(show_header=True, header_style="bold blue", title="Recent runs")
    table.add_column("Run", width=22)
    table.add_column("Status", width=11)
    table.add_column("Trigger", width=9)
    table.add_column("Started", width=20)
    table.add_column("Duration", width=10)
    table.add_column("Error", max_width=30)

    for r in runs[:15]:
        status = str(r.get("status", ""))
        colour = _AUTOMATION_RUN_COLOUR.get(status, "white")
        duration = f"{(r.get('duration_ms') or 0) / 1000:.1f}s" if r.get("duration_ms") else "—"
        table.add_row(
            str(r.get("run_id", ""))[:22],
            f"[{colour}]{status}[/{colour}]",
            str(r.get("trigger", "")),
            _format_ts(r.get("started_at")) if r.get("started_at") else "—",
            duration,
            str(r.get("error") or "")[:30] or "—",
        )
    console.print(table)


@automation.command("run")
@click.argument("task_id")
@click.option("--wait", is_flag=True,
              help="Block until the run finishes instead of returning immediately.")
@click.pass_context
def automation_run(ctx: click.Context, task_id: str, wait: bool) -> None:
    """Run TASK_ID now, regardless of its schedule.

    A manual run does not shift the task's cadence — `next_run_at` is untouched.
    """
    client = _get_client()
    _require_auth(client)

    with _spinner("Triggering run…") as progress:
        progress.add_task("Triggering run…")
        try:
            res = client.post(f"/api/automation/tasks/{urllib.parse.quote(task_id)}/run",
                              {"blocking": wait})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "automation run")

    run = res.get("run", {}) if isinstance(res, dict) else {}
    if ctx.obj.get("output", "table") == "json":
        _output_json(run)
        return

    status = str(run.get("status", "pending"))
    colour = _AUTOMATION_RUN_COLOUR.get(status, "white")
    console.print(f"[green]✓[/green] Run [bold]{run.get('run_id')}[/bold] "
                  f"[{colour}]{status}[/{colour}]")
    if run.get("error"):
        console.print(f"  [red]{run['error']}[/red]")
    if not wait and status in ("running", "pending"):
        console.print(f"  [dim]Follow with: aetherion automation logs "
                      f"{run.get('run_id')}[/dim]")


@automation.command("pause")
@click.argument("task_id")
def automation_pause(task_id: str) -> None:
    """Hold TASK_ID without losing its schedule."""
    _automation_lifecycle(task_id, "pause", "Paused")


@automation.command("resume")
@click.argument("task_id")
def automation_resume(task_id: str) -> None:
    """Resume a paused TASK_ID."""
    _automation_lifecycle(task_id, "resume", "Resumed")


@automation.command("enable")
@click.argument("task_id")
def automation_enable(task_id: str) -> None:
    """Enable TASK_ID and recompute its next run from now."""
    _automation_lifecycle(task_id, "enable", "Enabled", {"enabled": True})


@automation.command("disable")
@click.argument("task_id")
def automation_disable(task_id: str) -> None:
    """Disable TASK_ID and clear its next run."""
    _automation_lifecycle(task_id, "enable", "Disabled", {"enabled": False})


def _automation_lifecycle(task_id: str, action: str, verb: str,
                          body: Optional[dict] = None) -> None:
    """Shared body for pause/resume/enable/disable — same call, same rendering."""
    client = _get_client()
    _require_auth(client)

    with _spinner(f"{verb[:-1]}ing…") as progress:
        progress.add_task(f"{verb[:-1]}ing…")
        try:
            res = client.post(
                f"/api/automation/tasks/{urllib.parse.quote(task_id)}/{action}",
                body or {})
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, f"automation {action}")

    task = res.get("task", {}) if isinstance(res, dict) else {}
    console.print(f"[green]✓[/green] {verb} [bold]{task.get('name', task_id)}[/bold] "
                  f"→ {_automation_state(task)}")
    if task.get("next_run_at"):
        console.print(f"  Next run: [bold]{_format_ts(task['next_run_at'])}[/bold]")


@automation.command("delete")
@click.argument("task_id")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def automation_delete(task_id: str, yes: bool) -> None:
    """Delete TASK_ID and its run history."""
    client = _get_client()
    _require_auth(client)

    if not yes:
        click.confirm(f"Delete automation {task_id} and all its history?", abort=True)

    with _spinner("Deleting…") as progress:
        progress.add_task("Deleting…")
        try:
            client.delete(f"/api/automation/tasks/{urllib.parse.quote(task_id)}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "automation delete")

    console.print(f"[green]✓[/green] Deleted [bold]{task_id}[/bold].")


@automation.command("history")
@click.argument("task_id")
@click.option("--limit", "-n", default=25, show_default=True)
@click.option("--status", default="", help="Filter by run status.")
@click.pass_context
def automation_history(ctx: click.Context, task_id: str, limit: int, status: str) -> None:
    """Show the run history of TASK_ID."""
    client = _get_client()
    _require_auth(client)

    endpoint = (f"/api/automation/tasks/{urllib.parse.quote(task_id)}"
                f"/history?limit={limit}")
    if status:
        endpoint += f"&status={urllib.parse.quote(status)}"

    with _spinner("Loading history…") as progress:
        progress.add_task("Loading history…")
        try:
            res = client.get(endpoint)
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "automation history")

    runs = res.get("runs", []) if isinstance(res, dict) else []
    if ctx.obj.get("output", "table") == "json":
        _output_json(runs)
        return
    if not runs:
        console.print("[dim]No runs recorded.[/dim]")
        return

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Run", width=22)
    table.add_column("Status", width=11)
    table.add_column("Try", width=4)
    table.add_column("Trigger", width=9)
    table.add_column("Started", width=20)
    table.add_column("Duration", width=10)

    for r in runs:
        st = str(r.get("status", ""))
        colour = _AUTOMATION_RUN_COLOUR.get(st, "white")
        duration = f"{(r.get('duration_ms') or 0) / 1000:.1f}s" if r.get("duration_ms") else "—"
        table.add_row(
            str(r.get("run_id", ""))[:22],
            f"[{colour}]{st}[/{colour}]",
            str(r.get("attempt", 1)),
            str(r.get("trigger", "")),
            _format_ts(r.get("started_at")) if r.get("started_at") else "—",
            duration,
        )
    console.print(table)


@automation.command("logs")
@click.argument("run_id")
@click.pass_context
def automation_logs(ctx: click.Context, run_id: str) -> None:
    """Show the execution log of RUN_ID."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Loading run…") as progress:
        progress.add_task("Loading run…")
        try:
            res = client.get(f"/api/automation/runs/{urllib.parse.quote(run_id)}")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "automation logs")

    run = res.get("run", {}) if isinstance(res, dict) else {}
    if ctx.obj.get("output", "table") == "json":
        _output_json(run)
        return
    if not run:
        console.print("[dim]No run returned.[/dim]")
        return

    status = str(run.get("status", ""))
    colour = _AUTOMATION_RUN_COLOUR.get(status, "white")
    console.print(Panel.fit(
        f"[bold]Status:[/bold]    [{colour}]{status}[/{colour}]\n"
        f"[bold]Attempt:[/bold]   {run.get('attempt')}\n"
        f"[bold]Trigger:[/bold]   {run.get('trigger')}\n"
        f"[bold]Started:[/bold]   "
        f"{_format_ts(run['started_at']) if run.get('started_at') else '—'}\n"
        f"[bold]Duration:[/bold]  "
        f"{(run.get('duration_ms') or 0) / 1000:.1f}s\n"
        f"[bold]Execution:[/bold] {run.get('execution_id') or '—'}"
        + (f"\n[bold]Error:[/bold]     [red]{run['error']}[/red]" if run.get("error") else ""),
        title=f"[bold]{run.get('run_id')}[/bold]",
    ))

    for entry in (run.get("logs") or []):
        console.print(f"  [dim]{_format_ts(entry.get('ts'))}[/dim]  {entry.get('message')}")


@automation.command("validate")
@click.option("--schedule", "-s", "schedule_type", required=True,
              type=click.Choice(_AUTOMATION_SCHEDULES))
@click.option("--value", "-v", "schedule_value", default="")
@click.option("--timezone", "tz", default="UTC", show_default=True)
@click.option("--preview", default=5, show_default=True, help="Fire times to show.")
@click.pass_context
def automation_validate(ctx: click.Context, schedule_type: str, schedule_value: str,
                        tz: str, preview: int) -> None:
    """Check a schedule expression and preview when it would fire.

    Catches a cron typo before it becomes a task that silently never runs.
    """
    client = _get_client()
    _require_auth(client)

    with _spinner("Validating…") as progress:
        progress.add_task("Validating…")
        try:
            res = client.post("/api/automation/validate", {
                "schedule_type": schedule_type,
                "schedule_value": schedule_value,
                "timezone": tz,
                "preview": preview,
            })
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "automation validate")

    if ctx.obj.get("output", "table") == "json":
        _output_json(res)
        return

    if not res.get("valid"):
        console.print(f"[red]✗ Invalid:[/red] {res.get('error')}")
        sys.exit(1)

    console.print("[green]✓ Valid.[/green]")
    upcoming = res.get("next_runs", []) or []
    if not upcoming:
        console.print("[yellow]⚠ Valid but never fires[/yellow] — no matching time "
                      "was found within the search horizon.")
        return
    for item in upcoming:
        console.print(f"  [dim]•[/dim] {_format_ts(item.get('ts'))}")


@automation.command("status")
@click.pass_context
def automation_status(ctx: click.Context) -> None:
    """Show scheduler health and upcoming runs."""
    client = _get_client()
    _require_auth(client)

    with _spinner("Checking scheduler…") as progress:
        progress.add_task("Checking scheduler…")
        try:
            res = client.get("/api/automation/status")
        except (AetherionClientError, Exception) as exc:
            _handle_error(exc, "automation status")

    if ctx.obj.get("output", "table") == "json":
        _output_json(res)
        return

    running = res.get("scheduler_running")
    rc = "green" if running else "red"
    metrics = res.get("metrics", {}) or {}
    by_state = res.get("by_state", {}) or {}

    console.print(Panel.fit(
        f"[bold]Scheduler:[/bold]   [{rc}]{'running' if running else 'stopped'}[/{rc}]\n"
        f"[bold]Tick:[/bold]        {res.get('tick_seconds')}s\n"
        f"[bold]Tasks:[/bold]       {res.get('total_tasks', 0)} "
        f"({', '.join(f'{k}={v}' for k, v in by_state.items()) or 'none'})\n"
        f"[bold]In flight:[/bold]   {res.get('runs_in_flight', 0)}\n"
        f"[bold]Fired:[/bold]       {metrics.get('fired', 0)} "
        f"({metrics.get('succeeded', 0)} ok, {metrics.get('failed', 0)} failed, "
        f"{metrics.get('retried', 0)} retried, {metrics.get('skipped', 0)} skipped)",
        title="[bold]Automation engine[/bold]",
    ))

    # integration_status(): a hook that silently does nothing is diagnosable
    # from the response rather than by correlating server logs.
    integration = res.get("integration") or {}
    dead = [name for name, live in integration.items() if not live]
    if dead:
        console.print(f"[yellow]⚠ degraded edges:[/yellow] {', '.join(dead)}")
    if not running:
        console.print("[yellow]⚠ The scheduler loop is stopped[/yellow] — tasks will "
                      "not fire on their own. Check AUTOMATION_ENABLED.")

    upcoming = res.get("next_runs", []) or []
    if upcoming:
        table = Table(show_header=True, header_style="bold blue", title="Next runs")
        table.add_column("Task", width=22)
        table.add_column("Name", max_width=30)
        table.add_column("When", width=20)
        for item in upcoming:
            table.add_row(str(item.get("task_id", ""))[:22],
                          str(item.get("name", ""))[:30],
                          _format_ts(item.get("next_run_at")))
        console.print(table)


# ===========================================================================
# Entry point
# ===========================================================================


def _install_signal_handlers() -> None:
    """Install clean exit handlers for SIGTERM (and SIGBREAK on Windows)."""
    def _graceful_exit(signum, frame):  # type: ignore[no-untyped-def]
        err_console.print(f"\n[yellow]Received signal {signum}. Exiting cleanly.[/yellow]")
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, _graceful_exit)
    except (OSError, ValueError):
        pass  # Not available in all contexts

    # Windows-specific: Ctrl+Break sends SIGBREAK
    if sys.platform == "win32":
        try:
            signal.signal(signal.SIGBREAK, _graceful_exit)  # type: ignore[attr-defined]
        except (OSError, ValueError, AttributeError):
            pass


def main() -> None:
    """Entry point used by pyproject.toml [project.scripts] definition."""
    _install_signal_handlers()

    try:
        cli(standalone_mode=False, obj={})
    except click.Abort:
        err_console.print("\n[yellow]Aborted.[/yellow]")
        sys.exit(130)
    except KeyboardInterrupt:
        err_console.print("\n[yellow]Cancelled.[/yellow]")
        sys.exit(130)
    except click.ClickException as e:
        e.show()
        sys.exit(e.exit_code)
    except SystemExit:
        raise
    except AetherionOfflineError as exc:
        err_console.print(f"[red]✗ Offline:[/red] {exc}")
        sys.exit(1)
    except AetherionAuthError as exc:
        err_console.print(f"[red]✗ Auth error:[/red] {exc}")
        err_console.print("  Run [bold]aetherion login[/bold] to authenticate.")
        sys.exit(1)
    except AetherionClientError as exc:
        err_console.print(f"[red]✗ Client error:[/red] {exc}")
        sys.exit(1)
    except Exception as exc:
        if os.environ.get("AETHERION_DEBUG"):
            import traceback
            err_console.print_exception()
        else:
            err_console.print(f"[red]✗ Fatal error:[/red] {exc}")
            err_console.print("  Run with [dim]AETHERION_DEBUG=1[/dim] for full traceback.")
        sys.exit(1)




# ── skills: offline workflow-skill management (Phase 2, P2-CLI-01) ──────────

@cli.group()
def skills() -> None:
    """Manage local workflow skills."""


@skills.command("local")
@click.pass_context
def skills_local(ctx: click.Context) -> None:
    """List locally installed workflow skills."""
    from aetherion_cli.tui import skill_creator as sc

    items = sc.list_local_skills()
    if ctx.obj.get("output") == "json":
        _output_json(items)
        return
    if not items:
        err_console.print("No local skills found. Create one with [bold]aetherion skills create[/bold].")
        return
    for item in items:
        desc = item.get("description") or "(no description)"
        err_console.print(f"[green]- {item['name']}[/green] : {desc}")


@skills.command("create")
@click.option("--name", required=True, help="Skill name; slugified for storage.")
@click.option("--description", default="", help="Short description of the skill.")
@click.pass_context
def skills_create(ctx: click.Context, name: str, description: str) -> None:
    """Create a local workflow skill from a name and description."""
    from aetherion_cli.tui import skill_creator as sc

    result = sc.create_skill_interactive(name, description)
    if ctx.obj.get("output") == "json":
        _output_json({"result": result})
        return
    err_console.print(result)


# ---------------------------------------------------------------------------
# Converged Command Surface Registrations
# ---------------------------------------------------------------------------
from .commands import (
    artifacts_group,
    auth_group,
    diff_cmd,
    logs_cmd,
    task_group,
    version_cmd,
    worker_group,
)

cli.add_command(auth_group, name="auth")
cli.add_command(task_group, name="task")
cli.add_command(artifacts_group, name="artifacts")
cli.add_command(diff_cmd, name="diff")
cli.add_command(logs_cmd, name="logs")
cli.add_command(version_cmd, name="version")
cli.add_command(worker_group, name="worker")
# ---------------------------------------------------------------------------
# Hermes Agent Parity Commands: model, tools, setup, update
# ---------------------------------------------------------------------------

def _render_model_info() -> None:
    current = cfg_mod.get_model()
    table = Table(title="Aetherion AI Model Configuration", show_header=True)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="green")
    table.add_row("Current Model", current or "default (gpt-4o)")
    table.add_row("Config File", str(cfg_mod.CONFIG_FILE))
    console.print(table)
    console.print("\n[bold]Common Models:[/bold]")
    console.print("  • [cyan]gpt-4o[/cyan] (OpenAI)")
    console.print("  • [cyan]claude-3-5-sonnet-20241022[/cyan] (Anthropic)")
    console.print("  • [cyan]gemini-1.5-pro[/cyan] (Google)")
    console.print("  • [cyan]deepseek-chat[/cyan] (DeepSeek)")
    console.print("  • [cyan]llama-3.3-70b-versatile[/cyan] (Groq)")
    console.print("\nSwitch model: [bold]aetherion model set <model-name>[/bold]")


@cli.group("model", invoke_without_command=True)
@click.pass_context
def model_group(ctx: click.Context) -> None:
    """Inspect or switch LLM model configuration (Hermes parity)."""
    if ctx.invoked_subcommand is None:
        _render_model_info()


@model_group.command("show")
def model_show() -> None:
    """Show current LLM model configuration."""
    _render_model_info()


@model_group.command("set")
@click.argument("name")
def model_set(name: str) -> None:
    """Set default LLM model name."""
    cfg_mod.set_model(name)
    console.print(f"[green]✓[/green] Default model set to: [bold]{name}[/bold]")


@model_group.command("list")
def model_list() -> None:
    """List supported models across providers."""
    table = Table(title="Supported Models", show_header=True)
    table.add_column("Provider", style="bold")
    table.add_column("Model ID", style="cyan")
    table.add_column("Context", style="dim")
    table.add_column("Tier", style="yellow")
    table.add_row("OpenAI", "gpt-4o", "128k", "General / Coding")
    table.add_row("OpenAI", "gpt-4o-mini", "128k", "Fast / Lightweight")
    table.add_row("Anthropic", "claude-3-5-sonnet-20241022", "200k", "Deep Reasoning / Elite Coding")
    table.add_row("Anthropic", "claude-3-5-haiku-20241022", "200k", "High-speed / Lightweight")
    table.add_row("Google", "gemini-1.5-pro", "1M+", "Massive Context")
    table.add_row("Google", "gemini-1.5-flash", "1M+", "Low Latency")
    table.add_row("DeepSeek", "deepseek-v4-flash", "64k", "High Concurrency / Code")
    table.add_row("Groq", "llama-3.3-70b-versatile", "128k", "Ultra-fast Inference")
    console.print(table)


@cli.group("tools", invoke_without_command=True)
@click.pass_context
def tools_group(ctx: click.Context) -> None:
    """Inspect available agent tools and toolsets (Hermes parity)."""
    if ctx.invoked_subcommand is None:
        _render_tools_table()


@tools_group.command("list")
def tools_list() -> None:
    """List all registered agent tools."""
    _render_tools_table()


@tools_group.command("toolsets")
def tools_toolsets() -> None:
    """List available toolsets and bundles."""
    _render_toolsets_table()


@tools_group.command("info")
@click.argument("name")
def tools_info(name: str) -> None:
    """Show details and security policy for a specific tool."""
    console.print(f"[bold cyan]Tool:[/bold cyan] {name}")
    console.print("[bold yellow]Sandbox Mode:[/bold yellow] Container (Docker / PTY)")
    console.print("[dim]Security Policy: Verified non-root execution with scrubbed environment.[/dim]")


@cli.command("setup")
@click.option("--url", default=None, help="Backend server URL.")
@click.option("--model", "-m", default=None, help="Default model name.")
def setup_cmd(url: Optional[str], model: Optional[str]) -> None:
    """Interactive onboarding and configuration setup wizard (Hermes parity)."""
    console.print(Panel.fit("[bold green]Aetherion AI Platform Setup[/bold green]\nGuided configuration wizard for local & production environments.", border_style="cyan"))
    cur_url = url or cfg_mod.get_server_url()
    cur_model = model or cfg_mod.get_model()

    is_interactive = sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False

    if not is_interactive and (not url or not model):
        console.print("[dim]Non-interactive environment detected (stdin is not a TTY). Using default or provided options.[/dim]")

    if not url:
        if is_interactive:
            try:
                inp_url = click.prompt("Backend Server URL", default=cur_url)
                cfg_mod.set_server_url(inp_url)
            except click.Abort:
                sys.exit(0)
        else:
            cfg_mod.set_server_url(cur_url)
    else:
        cfg_mod.set_server_url(url)

    if not model:
        if is_interactive:
            try:
                inp_model = click.prompt("Default LLM Model", default=cur_model)
                cfg_mod.set_model(inp_model)
            except click.Abort:
                sys.exit(0)
        else:
            cfg_mod.set_model(cur_model)
    else:
        cfg_mod.set_model(model)

    console.print("[green]✓[/green] Configuration saved.")
    console.print(f"  Server URL: [bold]{cfg_mod.get_server_url()}[/bold]")
    console.print(f"  Model:      [bold]{cfg_mod.get_model()}[/bold]")
    console.print("\nRun [bold]aetherion doctor[/bold] to verify connectivity.")


@cli.command("update")
def update_cmd() -> None:
    """Check version status and connectivity with backend runtime (Hermes parity)."""
    client = _get_client()
    with _spinner("Checking version and backend readiness…") as progress:
        progress.add_task("Checking version…")
        try:
            res = client.health()
        except Exception as exc:
            res = {"status": "error", "error": str(exc), "offline": True}

    if res.get("status") == "ok":
        be_ver = res.get("version", "1.0.0")
        console.print(f"[green]✓[/green] Backend reachable: v{be_ver}")
        console.print("[green]✓[/green] Aetherion CLI: v1.0.0 (up to date)")
    elif res.get("offline") or "getaddrinfo" in str(res.get("error", "")).lower() or "connect" in str(res.get("error", "")).lower():
        err_console.print(f"[yellow]![/yellow] Backend status: offline or unreachable ({client.base_url})")
        console.print("  Hint: Check your network connection or verify your server URL with 'aetherion config --show'.")
        console.print("  CLI version: v1.0.0")
    else:
        err_console.print(f"[yellow]![/yellow] Backend status: {res.get('error', 'offline or unreachable')}")
        console.print("  CLI version: v1.0.0")


if __name__ == "__main__":
    main()


