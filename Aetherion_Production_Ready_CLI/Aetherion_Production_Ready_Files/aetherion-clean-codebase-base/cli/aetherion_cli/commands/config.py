"""
aetherion_cli/commands/config.py
Configuration display and mutation commands.
"""
from __future__ import annotations

import click
from rich.table import Table

from ..config.settings import get_all_settings, set_setting
from ..output.formatters import print_output, stdout_console


@click.group("config")
def config_group() -> None:
    """Manage local CLI configuration, endpoints, and defaults."""


@config_group.command("show")
@click.option("--json", "as_json", is_flag=True, help="Output settings as JSON.")
def config_show(as_json: bool) -> None:
    """Display current CLI settings with sensitive values redacted."""
    cfg = get_all_settings(redacted=True)
    if as_json:
        print_output(cfg, as_json=True)
    else:
        table = Table(title="Aetherion CLI Configuration", show_header=True, header_style="bold cyan")
        table.add_column("Setting", width=20, style="cyan")
        table.add_column("Value", min_width=35)
        for k, v in sorted(cfg.items()):
            table.add_row(k, str(v))
        stdout_console.print(table)


@config_group.command("set")
@click.argument("key")
@click.argument("value")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def config_set(key: str, value: str, as_json: bool) -> None:
    """Set a configuration value (e.g. server_url, model, output)."""
    set_setting(key, value)
    msg = f"Updated '{key}' to '{value}'."
    if as_json:
        print_output({"success": True, "key": key, "value": value}, as_json=True)
    else:
        stdout_console.print(f"[bold green]✓[/bold green] {msg}")
