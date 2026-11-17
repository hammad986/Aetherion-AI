"""
aetherion_cli/commands/version.py
Version reporting command with JSON and machine-readable metadata.
"""
from __future__ import annotations

import platform
import sys
import click

from ..output.formatters import print_output, stdout_console

CLI_VERSION = "1.0.0"
API_COMPAT_VERSION = "v1"


@click.command("version")
@click.option("--json", "as_json", is_flag=True, help="Output version info as JSON.")
def version_cmd(as_json: bool) -> None:
    """Show the Aetherion CLI version and runtime details."""
    info = {
        "version": CLI_VERSION,
        "api_compatibility": API_COMPAT_VERSION,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "arch": platform.machine(),
    }
    if as_json:
        print_output(info, as_json=True)
    else:
        stdout_console.print(f"[bold cyan]Aetherion CLI[/bold cyan] v{CLI_VERSION}")
        stdout_console.print(f"  [dim]API Compatibility:[/dim] {API_COMPAT_VERSION}")
        stdout_console.print(f"  [dim]Python:[/dim] {sys.version.split()[0]} ({platform.platform()})")
