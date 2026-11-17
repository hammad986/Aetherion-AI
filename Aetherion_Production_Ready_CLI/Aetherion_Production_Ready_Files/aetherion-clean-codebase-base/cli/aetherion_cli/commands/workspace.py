"""
aetherion_cli/commands/workspace.py
Workspace discovery, selection, and creation commands.
"""
from __future__ import annotations

import click
from rich.table import Table

from ..output.formatters import print_error_and_exit, print_output, stdout_console
from ..workspace.manager import WorkspaceManager


@click.group("workspace")
def workspace_group() -> None:
    """Manage multi-tenant workspaces and context boundaries."""


@workspace_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output workspaces as JSON.")
def workspace_list(as_json: bool) -> None:
    """List available workspaces."""
    mgr = WorkspaceManager()
    try:
        workspaces = mgr.list_workspaces()
        current = mgr.get_current()
        if as_json:
            print_output([
                {
                    "workspace_id": w.workspace_id,
                    "name": w.name,
                    "status": w.status,
                    "is_active": w.workspace_id == current,
                }
                for w in workspaces
            ], as_json=True)
        else:
            table = Table(title="Workspaces", show_header=True, header_style="bold cyan")
            table.add_column("Active", width=8, justify="center")
            table.add_column("Workspace ID", style="cyan")
            table.add_column("Name", min_width=24)
            table.add_column("Status", width=12)

            for w in workspaces:
                is_cur = "*" if w.workspace_id == current else ""
                table.add_row(is_cur, w.workspace_id, w.name, w.status)
            stdout_console.print(table)
    except Exception as e:
        print_error_and_exit(f"Failed to list workspaces: {e}", as_json=as_json)


@workspace_group.command("select")
@click.argument("workspace_id")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def workspace_select(workspace_id: str, as_json: bool) -> None:
    """Select active workspace by ID."""
    mgr = WorkspaceManager()
    mgr.select(workspace_id)
    if as_json:
        print_output({"success": True, "selected_workspace": workspace_id}, as_json=True)
    else:
        stdout_console.print(f"[bold green]✓ Selected workspace:[/bold green] {workspace_id}")


@workspace_group.command("create")
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Output created workspace as JSON.")
def workspace_create(name: str, as_json: bool) -> None:
    """Create and automatically select a new workspace."""
    mgr = WorkspaceManager()
    try:
        ws = mgr.create(name)
        if as_json:
            print_output({
                "success": True,
                "workspace_id": ws.workspace_id,
                "name": ws.name,
            }, as_json=True)
        else:
            stdout_console.print(f"[bold green]✓ Created and selected workspace:[/bold green] {ws.name} ({ws.workspace_id})")
    except Exception as e:
        print_error_and_exit(f"Failed to create workspace: {e}", as_json=as_json)
