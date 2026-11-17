"""
aetherion_cli/commands/artifacts.py
Artifact inspection, listing, and download commands.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import click
from rich.table import Table

from ..artifacts.manager import ArtifactManager
from ..output.formatters import print_error_and_exit, print_output, stdout_console


@click.group("artifacts")
def artifacts_group() -> None:
    """Manage generated task artifacts, snapshots, and exports."""


@artifacts_group.command("list")
@click.option("--task", "-t", default="", help="Filter by task ID.")
@click.option("--json", "as_json", is_flag=True, help="Output artifacts as JSON.")
def artifacts_list(task: str, as_json: bool) -> None:
    """List generated artifacts."""
    mgr = ArtifactManager()
    try:
        items = mgr.list_artifacts(session_id=task or None)
        if as_json:
            print_output([i.__dict__ for i in items], as_json=True)
        else:
            table = Table(title="Generated Artifacts", show_header=True, header_style="bold cyan")
            table.add_column("Artifact ID", style="cyan")
            table.add_column("Name", min_width=24)
            table.add_column("Task / Session", style="dim")
            table.add_column("Size", justify="right")

            for art in items:
                table.add_row(
                    art.artifact_id,
                    art.name,
                    art.session_id or "—",
                    f"{art.file_size} B" if art.file_size else "—",
                )
            stdout_console.print(table)
    except Exception as e:
        print_error_and_exit(f"Failed to list artifacts: {e}", as_json=as_json)


@artifacts_group.command("get")
@click.argument("artifact_id")
@click.option("--output", "-o", default="", help="Destination directory or filename.")
@click.option("--checksum", default="", help="Expected SHA-256 checksum for verification.")
@click.option("--json", "as_json", is_flag=True, help="Output download summary as JSON.")
def artifacts_get(artifact_id: str, output: str, checksum: str, as_json: bool) -> None:
    """Download an artifact securely to disk."""
    mgr = ArtifactManager()
    target_dir = Path(output).resolve() if output else Path.cwd()
    try:
        saved_path = mgr.download_artifact(
            artifact_id=artifact_id,
            destination_dir=target_dir,
            expected_checksum=checksum or None,
        )
        if as_json:
            print_output({
                "success": True,
                "artifact_id": artifact_id,
                "saved_to": str(saved_path),
            }, as_json=True)
        else:
            stdout_console.print(f"[bold green]✓ Artifact downloaded successfully:[/bold green] {saved_path}")
    except Exception as e:
        print_error_and_exit(f"Download failed: {e}", as_json=as_json)
