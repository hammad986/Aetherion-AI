"""
aetherion_cli/commands/auth.py
Authentication command suite: login, device flow, status, logout, sessions, revoke.
"""
from __future__ import annotations

import sys
import webbrowser
import click
import httpx
from rich.table import Table

from ..auth.device_flow import DeviceAuthFlow
from ..auth.token_store import TokenStore
from ..config.settings import get_server_url, set_server_url
from ..output.formatters import print_error_and_exit, print_output, stderr_console, stdout_console


@click.group("auth")
def auth_group() -> None:
    """Manage authentication, credentials, device authorization, and sessions."""


@auth_group.command("login")
@click.option("--email", "-e", default="", help="Account email (non-interactive).")
@click.option("--password", "-p", default="", help="Account password (non-interactive).")
@click.option("--device", is_flag=True, help="Use device-code flow explicitly.")
@click.option("--url", default="", help="Override server URL.")
@click.option("--json", "as_json", is_flag=True, help="Output login response as JSON.")
def auth_login(email: str, password: str, device: bool, url: str, as_json: bool) -> None:
    """Log in to Aetherion using password credentials or device authorization flow."""
    if url:
        set_server_url(url)
    server_url = get_server_url()

    # If email and password provided: direct password grant
    if email and password:
        login_url = f"{server_url}/api/auth/login"
        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(login_url, json={"email": email, "password": password})
                if not res.is_success:
                    hint = ""
                    if res.status_code == 401:
                        hint = "\n  Hint: Default developer credentials: admin@aetherion.ai / AdminPassword123!\n  Or register a new account: aetherion auth register --email <email> --password <pwd>"
                    print_error_and_exit(f"Login failed ({res.status_code}): {res.text}{hint}", as_json=as_json)
                data = res.json()
                token = data.get("access_token") or data.get("token")
                if not token:
                    print_error_and_exit("Server did not return an access token.", as_json=as_json)
                storage = TokenStore.set_access_token(token)
                if data.get("refresh_token"):
                    TokenStore.set_refresh_token(data["refresh_token"])

                result = {
                    "success": True,
                    "storage": storage,
                    "user": data.get("user") or {"email": email},
                }
                if as_json:
                    print_output(result, as_json=True)
                else:
                    stdout_console.print(f"[bold green]✓ Successfully logged in as {email}[/bold green]")
                    stdout_console.print(f"  [dim]Token stored in {storage}[/dim]")
                return
        except Exception as e:
            hint = f"\n  Target Server: {server_url}\n  Hint: Check if the backend is running (`python app.py`). If running on port 5000, run:\n    aetherion config set server_url http://127.0.0.1:5000\n  Or pass: --url http://127.0.0.1:5000"
            print_error_and_exit(f"Connection error: {e}{hint}", as_json=as_json)

    # Otherwise: execute device code flow
    flow = DeviceAuthFlow(server_url)
    try:
        req = flow.initiate()
    except Exception as e:
        print_error_and_exit(f"Failed to initiate device flow: {e}", as_json=as_json)

    device_code = req.get("device_code")
    user_code = req.get("user_code")
    verification_uri = req.get("verification_uri") or f"{server_url}/device"
    complete_uri = req.get("verification_uri_complete") or f"{verification_uri}?user_code={user_code}"
    interval = req.get("interval", 5)
    expires_in = req.get("expires_in", 300)

    if as_json:
        print_output({
            "stage": "pending_user_action",
            "user_code": user_code,
            "verification_uri": verification_uri,
            "verification_uri_complete": complete_uri,
            "expires_in": expires_in,
        }, as_json=True)
    else:
        stdout_console.print("\n[bold cyan]Device Authorization Required[/bold cyan]")
        stdout_console.print(f"  1. Open: [link={complete_uri}]{complete_uri}[/link]")
        stdout_console.print(f"  2. Confirm code: [bold yellow]{user_code}[/bold yellow]\n")
        stdout_console.print("[dim]Waiting for authorization in browser... (Press Ctrl+C to cancel)[/dim]")

        # Attempt to open browser automatically
        try:
            webbrowser.open(complete_uri)
        except Exception:
            pass

    poll_result = flow.poll(device_code, interval=interval, expires_in=expires_in, quiet=as_json)
    if poll_result.get("success"):
        if as_json:
            print_output(poll_result, as_json=True)
        else:
            stdout_console.print("[bold green]✓ Device authorized successfully! Logged in.[/bold green]")
    else:
        print_error_and_exit(poll_result.get("error", "Device authorization failed."), as_json=as_json)


@auth_group.command("device")
@click.option("--url", default="", help="Override server URL.")
@click.option("--json", "as_json", is_flag=True, help="Output device auth details as JSON.")
def auth_device(url: str, as_json: bool) -> None:
    """Initiate terminal device authorization flow explicitly."""
    auth_login.callback(email="", password="", device=True, url=url, as_json=as_json)


@auth_group.command("status")
@click.option("--json", "as_json", is_flag=True, help="Output auth status as JSON.")
def auth_status(as_json: bool) -> None:
    """Display current authentication identity without printing secret tokens."""
    status = TokenStore.get_status()
    if as_json:
        print_output(status, as_json=True)
    else:
        if status["authenticated"]:
            claims = status.get("claims") or {}
            user = claims.get("username") or claims.get("sub") or "User"
            role = claims.get("role") or "standard"
            stdout_console.print(f"[bold green]✓ Authenticated as {user}[/bold green] (Role: {role})")
            stdout_console.print(f"  [dim]Storage:[/dim] {status['storage_kind'].upper()}")
            stdout_console.print(f"  [dim]Masked Token:[/dim] {status['masked_token']}")
        else:
            stdout_console.print("[yellow]✗ Not authenticated.[/yellow] Run `aetherion auth login`.")


@auth_group.command("logout")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def auth_logout(as_json: bool) -> None:
    """Clear locally stored credentials."""
    TokenStore.clear_tokens()
    if as_json:
        print_output({"success": True, "message": "Logged out and local credentials purged."}, as_json=True)
    else:
        stdout_console.print("[bold green]✓ Logged out successfully. Local credentials purged.[/bold green]")


@auth_group.command("sessions")
@click.option("--json", "as_json", is_flag=True, help="Output sessions as JSON.")
def auth_sessions(as_json: bool) -> None:
    """List active user device sessions."""
    server_url = get_server_url()
    tok = TokenStore.get_access_token()
    if not tok:
        print_error_and_exit("Not authenticated.", as_json=as_json)

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{server_url}/api/auth/sessions",
                headers={"Authorization": f"Bearer {tok}"},
            )
            if not resp.is_success:
                resp = client.get(
                    f"{server_url}/api/auth/device/sessions",
                    headers={"Authorization": f"Bearer {tok}"},
                )
            if resp.is_success:
                sessions = resp.json()
                items = sessions if isinstance(sessions, list) else sessions.get("sessions", [])
                if as_json:
                    print_output(items, as_json=True)
                else:
                    table = Table(title="Active Device Sessions", show_header=True, header_style="bold cyan")
                    table.add_column("Session ID", style="cyan")
                    table.add_column("Device / IP", min_width=20)
                    table.add_column("Created", min_width=16)
                    for s in items:
                        table.add_row(
                            str(s.get("id", ""))[:12],
                            f"{s.get('device_info', 'Unknown')} ({s.get('ip_address', '127.0.0.1')})",
                            str(s.get("created_at", ""))[:19],
                        )
                    stdout_console.print(table)
            else:
                print_error_and_exit(f"Failed to query sessions ({resp.status_code}): {resp.text}", as_json=as_json)
    except Exception as e:
        print_error_and_exit(f"Error: {e}", as_json=as_json)


@auth_group.command("revoke")
@click.argument("session_id", default="")
@click.option("--all", "revoke_all", is_flag=True, help="Revoke all remote sessions.")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def auth_revoke(session_id: str, revoke_all: bool, as_json: bool) -> None:
    """Revoke a specific session or all active sessions."""
    server_url = get_server_url()
    tok = TokenStore.get_access_token()
    if not tok:
        print_error_and_exit("Not authenticated.", as_json=as_json)

    endpoint = f"{server_url}/api/auth/logout_everywhere" if revoke_all else f"{server_url}/api/auth/device/revoke"
    payload = {} if revoke_all else {"session_id": session_id}

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {tok}"},
            )
            if resp.is_success:
                if revoke_all:
                    TokenStore.clear_tokens()
                if as_json:
                    print_output({"success": True, "message": "Session(s) revoked."}, as_json=True)
                else:
                    stdout_console.print("[bold green]✓ Session(s) revoked successfully.[/bold green]")
            else:
                print_error_and_exit(f"Revocation failed ({resp.status_code}): {resp.text}", as_json=as_json)
    except Exception as e:
        print_error_and_exit(f"Error: {e}", as_json=as_json)


@auth_group.command("register")
@click.option("--email", "-e", default="", help="Account email.")
@click.option("--password", "-p", default="", help="Account password.")
@click.option("--name", "-n", default="", help="Display name.")
@click.option("--url", default="", help="Override server URL.")
@click.option("--json", "as_json", is_flag=True, help="Output registration response as JSON.")
def auth_register(email: str, password: str, name: str, url: str, as_json: bool) -> None:
    """Register a new user account and authenticate."""
    if url:
        set_server_url(url)
    server_url = get_server_url()

    if not email:
        email = click.prompt("Email")
    if not password:
        password = click.prompt("Password", hide_input=True)

    reg_url = f"{server_url}/api/auth/register"
    try:
        with httpx.Client(timeout=15.0) as client:
            res = client.post(
                reg_url,
                json={"email": email, "password": password, "name": name or email.split("@")[0]}
            )
            if not res.is_success:
                print_error_and_exit(f"Registration failed ({res.status_code}): {res.text}", as_json=as_json)
            data = res.json()
            token = data.get("access_token")
            storage = "none"
            if token:
                storage = TokenStore.set_access_token(token)
                if data.get("refresh_token"):
                    TokenStore.set_refresh_token(data["refresh_token"])
            if as_json:
                print_output(data, as_json=True)
            else:
                stdout_console.print(f"[bold green]✓ Account registered and logged in as {email}[/bold green]")
                stdout_console.print(f"  [dim]Token stored in {storage}[/dim]")
    except Exception as e:
        hint = f"\n  Target Server: {server_url}\n  Hint: Check if the backend is running (`python app.py`). If running on port 5000, run:\n    aetherion config set server_url http://127.0.0.1:5000\n  Or pass: --url http://127.0.0.1:5000"
        print_error_and_exit(f"Registration error: {e}{hint}", as_json=as_json)
