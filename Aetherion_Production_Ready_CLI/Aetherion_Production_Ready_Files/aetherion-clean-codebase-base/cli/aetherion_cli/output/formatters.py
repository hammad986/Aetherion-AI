"""
aetherion_cli/output/formatters.py
Standardized human-readable and machine-readable output formatters for Aetherion CLI.
"""
from __future__ import annotations

import datetime
import json
import sys
import uuid
from typing import Any, Dict, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..security.redaction import redact_secrets

stdout_console = Console()
stderr_console = Console(stderr=True)


def format_json(
    data: Any = None,
    *,
    ok: bool = True,
    error: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> str:
    """Build a standard redacted JSON envelope."""
    payload: Dict[str, Any] = {
        "ok": ok,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "correlation_id": correlation_id or str(uuid.uuid4()),
    }
    if ok:
        payload["data"] = redact_secrets(data)
    else:
        payload["error"] = redact_secrets(error or {"message": "Unknown error"})
    return json.dumps(payload, indent=2, default=str)


def print_output(
    data: Any,
    *,
    as_json: bool = False,
    human_renderer: Optional[callable] = None,
    correlation_id: Optional[str] = None,
    quiet: bool = False,
) -> None:
    """Print output in either JSON or human-readable format."""
    if as_json:
        stdout_console.print_json(format_json(data, ok=True, correlation_id=correlation_id))
    elif not quiet:
        if human_renderer:
            human_renderer(data)
        elif isinstance(data, (dict, list)):
            stdout_console.print_json(json.dumps(redact_secrets(data), indent=2, default=str))
        else:
            stdout_console.print(str(data))


def print_error_and_exit(
    message: str,
    *,
    status_code: int = 1,
    hint: str = "",
    as_json: bool = False,
    correlation_id: Optional[str] = None,
) -> None:
    """Print error to stderr or JSON and exit with status_code."""
    if as_json:
        err_obj = {"message": message, "code": status_code}
        if hint:
            err_obj["hint"] = hint
        stderr_console.print_json(format_json(ok=False, error=err_obj, correlation_id=correlation_id))
    else:
        stderr_console.print(f"[bold red]✗ Error ({status_code}):[/bold red] {message}")
        if hint:
            stderr_console.print(f"  [yellow]Hint:[/yellow] {hint}")
    sys.exit(status_code)
