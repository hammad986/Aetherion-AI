"""
cli/aetherion_cli/commands/plugins.py

Aetherion CLI Plugin Marketplace & Management Section.
Lists built-in catalog plugins (including 'rea' - Reverse Engineer Anything),
and downloads/installs 'rea' directly from its GitHub repository (morluto/rea)
when the user runs `aetherion plugins install rea`.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict

import click

from ..output.formatters import print_output, stdout_console
from .rea_marketplace_catalog_and_fetcher import (
    BUILTIN_PLUGIN_CATALOG,
    download_and_install_github_plugin,
    get_plugins_base_dir,
    list_catalog_plugins,
    uninstall_plugin,
)


@click.group("plugins")
def plugins_group() -> None:
    """Browse, install, and manage Aetherion Agent plugins (including REA from GitHub)."""


@plugins_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output plugin catalog & status as JSON.")
def plugins_list_cmd(as_json: bool) -> None:
    """List all available built-in & marketplace plugins and their installation status."""
    catalog = list_catalog_plugins()
    if as_json:
        print_output({"plugins": catalog}, as_json=True)
        return

    stdout_console.print("[bold cyan]Aetherion CLI — Plugin Marketplace & Installed Extensions[/bold cyan]\n")
    for p in catalog:
        status_badge = "[bold green]INSTALLED[/bold green]" if p["installed"] else "[yellow]AVAILABLE (Not Installed)[/yellow]"
        stdout_console.print(f"• [bold white]{p['id']}[/bold white] ({p['name']}) — v{p['version']} [{status_badge}]")
        stdout_console.print(f"  [dim]Category:[/dim] {p['category']} | [dim]Trust:[/dim] {p['trust_level']} | [dim]Source:[/dim] {p['source_type']}")
        if p.get("repository"):
            stdout_console.print(f"  [dim]GitHub Repo:[/dim] {p['repository']}")
        stdout_console.print(f"  [dim]Description:[/dim] {p['description']}")
        stdout_console.print(f"  [dim]Tools:[/dim] {', '.join(p['provided_tools'])}")
        if not p["installed"]:
            stdout_console.print(f"  [dim]Install Command:[/dim] [bold green]aetherion plugins install {p['id']}[/bold green]\n")
        else:
            stdout_console.print(f"  [dim]Path:[/dim] {p['install_path']}\n")


@plugins_group.command("install")
@click.argument("plugin_id")
@click.option("--force", is_flag=True, help="Force re-download from GitHub even if already installed.")
@click.option("--json", "as_json", is_flag=True, help="Output installation result as JSON.")
def plugins_install_cmd(plugin_id: str, force: bool, as_json: bool) -> None:
    """Download and install a plugin from its official GitHub repository (e.g. 'rea')."""
    if not as_json:
        stdout_console.print(f"[cyan]Fetching plugin '{plugin_id}' from official repository...[/cyan]")

    result = download_and_install_github_plugin(plugin_id=plugin_id, force=force)
    if result.get("status") in ("installed", "already_installed"):
        # Copy the bundled REA runtime bridge into the installed plugin folder so it is immediately executable
        install_dir = Path(result["install_dir"])
        local_bridge = Path(__file__).resolve().parent / "rea_plugin_main.py"
        if local_bridge.exists():
            shutil.copyfile(local_bridge, install_dir / "main.py")

    if as_json:
        print_output(result, as_json=True)
        return

    if result.get("status") == "error":
        stdout_console.print(f"[bold red]Error:[/bold red] {result.get('error')}")
        raise SystemExit(1)

    stdout_console.print(
        f"[bold green]Success![/bold green] Plugin [bold]{result['plugin_id']}[/bold] "
        f"(v{result['version']}) is now {result['status'].upper()}."
    )
    stdout_console.print(f"  [dim]Repository:[/dim]   {result.get('repository')}")
    stdout_console.print(f"  [dim]Install Path:[/dim] {result.get('install_dir')}")


@plugins_group.command("uninstall")
@click.argument("plugin_id")
@click.option("--json", "as_json", is_flag=True, help="Output uninstall result as JSON.")
def plugins_uninstall_cmd(plugin_id: str, as_json: bool) -> None:
    """Uninstall an Aetherion plugin from the local environment."""
    result = uninstall_plugin(plugin_id=plugin_id)
    if as_json:
        print_output(result, as_json=True)
    else:
        stdout_console.print(f"Plugin '{plugin_id}': [yellow]{result['status']}[/yellow]")


@plugins_group.command("info")
@click.argument("plugin_id")
@click.option("--json", "as_json", is_flag=True, help="Output detailed plugin metadata as JSON.")
def plugins_info_cmd(plugin_id: str, as_json: bool) -> None:
    """Display detailed manifest and GitHub source info for a catalog plugin."""
    catalog = list_catalog_plugins()
    entry = next((p for p in catalog if p["id"] == plugin_id or p.get("alias") == plugin_id), None)
    if not entry:
        if as_json:
            print_output({"status": "error", "error": f"Unknown plugin: {plugin_id}"}, as_json=True)
        else:
            stdout_console.print(f"[bold red]Unknown plugin:[/bold red] {plugin_id}")
        raise SystemExit(1)

    if as_json:
        print_output(entry, as_json=True)
    else:
        stdout_console.print(json.dumps(entry, indent=2))
