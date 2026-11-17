"""
aetherion_cli/commands/logs.py
Log streaming and historical log retrieval commands.
"""
from __future__ import annotations

import sys
import click

from ..api.client import ApiClient
from ..output.formatters import print_error_and_exit, stdout_console


@click.command("logs")
@click.argument("task_id")
@click.option("--follow", "-f", is_flag=True, help="Follow streaming logs in real time.")
@click.option("--json", "as_json", is_flag=True, help="Output logs as raw JSON stream.")
def logs_cmd(task_id: str, follow: bool, as_json: bool) -> None:
    """Stream or inspect execution logs for a task."""
    client = ApiClient()
    endpoint = f"/api/logs/{task_id}"

    if follow:
        try:
            stream_url = f"/api/notifications/stream"
            stdout_console.print(f"[dim]Streaming live logs for task {task_id}... (Ctrl+C to stop)[/dim]")
            for event in client.stream(stream_url, params={"session_id": task_id}):
                if as_json:
                    stdout_console.print(str(event.data))
                else:
                    stdout_console.print(f"[cyan]{event.event or 'log'}[/cyan] {event.data}")
        except KeyboardInterrupt:
            sys.exit(130)
        except Exception as e:
            print_error_and_exit(f"Log stream failed: {e}", as_json=as_json)
    else:
        try:
            logs = client.get(endpoint)
            if as_json:
                import json
                stdout_console.print(json.dumps(logs, indent=2, default=str))
            else:
                text = logs.get("logs") if isinstance(logs, dict) else str(logs)
                stdout_console.print(text or "[dim]No logs recorded for this task.[/dim]")
        except Exception as e:
            print_error_and_exit(f"Failed to fetch logs: {e}", as_json=as_json)
