"""
cli/aetherion_cli/commands/worker.py — Phase 22 Execution Fabric Worker Management Commands.

Operator CLI commands to start, inspect, and drain multi-process execution fabric workers.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Optional

import click
from rich.table import Table

from ..output.formatters import print_error_and_exit, print_output, stdout_console

logger = logging.getLogger("aetherion.cli.worker")


@click.group("worker")
def worker_group() -> None:
    """Manage Execution Fabric workers, multi-process runtimes, and draining."""


@worker_group.command("start")
@click.option("--worker-id", default="", help="Unique identifier for this worker instance.")
@click.option("--max-concurrency", "-c", default=4, type=int, help="Maximum concurrent task step executions.")
@click.option("--capabilities", default="DEFAULT", help="Comma-separated capability tags (e.g. DEFAULT,CPU,DOCKER).")
@click.option("--timeout", default=None, type=float, help="Duration in seconds to run worker before exiting.")
@click.option("--json", "as_json", is_flag=True, help="Output startup info as JSON.")
def worker_start(
    worker_id: str,
    max_concurrency: int,
    capabilities: str,
    timeout: Optional[float],
    as_json: bool,
) -> None:
    """Start an Execution Fabric worker runtime."""
    try:
        from backend.execution.fabric.worker_runtime import WorkerRuntime

        caps = [c.strip() for c in capabilities.split(",") if c.strip()]
        runtime = WorkerRuntime(
            worker_id=worker_id or None,
            max_concurrency=max_concurrency,
            supported_capabilities=set(caps) if caps else {"DEFAULT"},
        )

        if as_json:
            print_output({
                "status": "STARTING",
                "worker_id": runtime.worker_id,
                "process_id": runtime.process_id,
                "max_concurrency": runtime.max_concurrency,
                "capabilities": list(runtime.supported_capabilities),
            }, as_json=True)
        else:
            stdout_console.print(
                f"[bold green]Starting Execution Fabric Worker[/bold green] "
                f"[cyan]{runtime.worker_id}[/cyan] (PID: {runtime.process_id}, "
                f"Concurrency: {runtime.max_concurrency}, Caps: {list(runtime.supported_capabilities)})"
            )

        runtime.setup_signal_handlers()
        runtime.run(timeout=timeout)

        if as_json:
            print_output({
                "status": runtime.status.value,
                "worker_id": runtime.worker_id,
                "items_processed": runtime.items_processed,
                "items_succeeded": runtime.items_succeeded,
                "items_failed": runtime.items_failed,
            }, as_json=True)
        else:
            stdout_console.print(
                f"[bold yellow]Worker {runtime.worker_id} finished.[/bold yellow] "
                f"Status: {runtime.status.value}, Processed: {runtime.items_processed} items."
            )
    except Exception as exc:
        print_error_and_exit(f"Failed to run worker: {exc}", as_json=as_json)


@worker_group.command("status")
@click.option("--worker-id", default="", help="Query specific worker ID.")
@click.option("--json", "as_json", is_flag=True, help="Output status as JSON.")
def worker_status(worker_id: str, as_json: bool) -> None:
    """Inspect Execution Fabric registered workers and live status."""
    try:
        from backend.execution.fabric.store import get_fabric_store
        store = get_fabric_store()

        if worker_id:
            worker = store.get_worker(worker_id)
            if not worker:
                print_error_and_exit(f"Worker '{worker_id}' not found.", as_json=as_json)
                return
            workers = [worker]
        else:
            workers = store.list_workers()

        if as_json:
            data = [
                {
                    "worker_id": w.worker_id,
                    "process_id": w.process_id,
                    "status": w.status.value,
                    "concurrency": f"{w.active_concurrency}/{w.max_concurrency}",
                    "drain_state": w.drain_state,
                    "capabilities": list(w.supported_capabilities),
                    "last_heartbeat": w.last_heartbeat,
                    "active_leases": w.active_leases,
                }
                for w in workers
            ]
            print_output(data, as_json=True)
        else:
            table = Table(title="Execution Fabric Workers", show_header=True, header_style="bold cyan")
            table.add_column("Worker ID", style="cyan")
            table.add_column("PID", justify="right")
            table.add_column("Status", style="bold")
            table.add_column("Capacity", justify="center")
            table.add_column("Drain", justify="center")
            table.add_column("Capabilities", style="dim")
            table.add_column("Heartbeat", justify="right")

            now = time.time()
            for w in workers:
                status_color = "green" if w.status.value == "READY" else ("yellow" if w.status.value == "BUSY" else "red")
                hb_ago = f"{int(now - w.last_heartbeat)}s ago" if w.last_heartbeat > 0 else "—"
                table.add_row(
                    w.worker_id,
                    str(w.process_id),
                    f"[{status_color}]{w.status.value}[/{status_color}]",
                    f"{w.active_concurrency}/{w.max_concurrency}",
                    "YES" if w.drain_state else "NO",
                    ",".join(sorted(w.supported_capabilities)),
                    hb_ago,
                )
            stdout_console.print(table)
    except Exception as exc:
        print_error_and_exit(f"Failed to query worker status: {exc}", as_json=as_json)


@worker_group.command("drain")
@click.argument("worker_id")
@click.option("--timeout", default=30.0, type=float, help="Drain timeout in seconds.")
@click.option("--json", "as_json", is_flag=True, help="Output drain result as JSON.")
def worker_drain(worker_id: str, timeout: float, as_json: bool) -> None:
    """Initiate graceful drain for an Execution Fabric worker."""
    try:
        from backend.execution.fabric.coordination import FabricCoordinator
        from backend.execution.fabric.service import ExecutionFabricService
        from backend.execution.fabric.store import get_fabric_store

        store = get_fabric_store()
        service = ExecutionFabricService(store=store)
        coordinator = FabricCoordinator()

        worker = store.get_worker(worker_id)
        if not worker:
            print_error_and_exit(f"Worker '{worker_id}' not found.", as_json=as_json)
            return

        # Mark in store
        updated_worker = service.drain_worker(worker_id)
        # Notify via coordinator
        coordinator.request_stop(worker_id, ttl_seconds=int(timeout))

        if as_json:
            print_output({
                "status": "DRAINING",
                "worker_id": worker_id,
                "active_concurrency": updated_worker.active_concurrency,
                "drain_state": True,
            }, as_json=True)
        else:
            stdout_console.print(
                f"[bold yellow]Graceful drain requested for worker[/bold yellow] "
                f"[cyan]{worker_id}[/cyan]. Active concurrency: {updated_worker.active_concurrency}."
            )
    except Exception as exc:
        print_error_and_exit(f"Failed to drain worker: {exc}", as_json=as_json)
