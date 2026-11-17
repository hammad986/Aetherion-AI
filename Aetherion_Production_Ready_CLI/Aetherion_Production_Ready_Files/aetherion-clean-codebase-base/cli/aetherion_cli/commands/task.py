"""
aetherion_cli/commands/task.py
Task creation, durable execution, monitoring, cancellation, retry, and HITL gate approval.
"""
from __future__ import annotations

import sys
import click
from rich.table import Table

from ..config.settings import get_active_workspace
from ..execution.lifecycle import ExecutionLifecycle
from ..output.formatters import print_error_and_exit, print_output, stdout_console


@click.group("task")
def task_group() -> None:
    """Manage asynchronous autonomous tasks and execution lifecycles."""


@task_group.command("create")
@click.argument("prompt")
@click.option("--workspace", "-w", default="", help="Target workspace ID.")
@click.option("--model", "-m", default="auto", help="Model to route to.")
@click.option("--mode", default="autonomous", help="Execution mode.")
@click.option("--budget", "-b", type=float, default=None, help="Spend cap in USD.")
@click.option("--json", "as_json", is_flag=True, help="Output created task as JSON.")
def task_create(prompt: str, workspace: str, model: str, mode: str, budget: float, as_json: bool) -> None:
    """Create a task without waiting for completion."""
    ws = workspace or get_active_workspace()
    lifecycle = ExecutionLifecycle()
    try:
        resp = lifecycle.create_task(
            task=prompt,
            workspace_id=ws,
            model=model,
            mode=mode,
            budget_cap=budget,
        )
        if as_json:
            print_output(resp.__dict__, as_json=True)
        else:
            stdout_console.print(f"[bold green]✓ Task created:[/bold green] {resp.task_id}")
            stdout_console.print(f"  [dim]Status:[/dim] {resp.status}")
            stdout_console.print(f"  [dim]Run `aetherion task status {resp.task_id}` to inspect.[/dim]")
    except Exception as e:
        print_error_and_exit(f"Failed to create task: {e}", as_json=as_json)


@task_group.command("run")
@click.argument("prompt")
@click.option("--workspace", "-w", default="", help="Target workspace ID.")
@click.option("--model", "-m", default="auto", help="Model to route to.")
@click.option("--mode", default="autonomous", help="Execution mode.")
@click.option("--budget", "-b", type=float, default=None, help="Spend cap in USD.")
@click.option("--timeout", type=float, default=3600.0, help="Max wait seconds.")
@click.option("--json", "as_json", is_flag=True, help="Output final execution state as JSON.")
def task_run(prompt: str, workspace: str, model: str, mode: str, budget: float, timeout: float, as_json: bool) -> None:
    """Create a task and watch execution until terminal state."""
    ws = workspace or get_active_workspace()
    lifecycle = ExecutionLifecycle()
    try:
        resp = lifecycle.create_task(
            task=prompt,
            workspace_id=ws,
            model=model,
            mode=mode,
            budget_cap=budget,
        )
        if not as_json:
            stdout_console.print(f"[bold cyan]Ignited Task {resp.task_id}[/bold cyan] ({model} in {mode} mode)")

        def _on_update(upd):
            if not as_json:
                stdout_console.print(f"  [dim]State update:[/dim] [bold]{upd.status.upper()}[/bold]")

        final_resp = lifecycle.watch(resp.task_id, on_update=_on_update, timeout=timeout)
        if as_json:
            print_output(final_resp.__dict__, as_json=True)
        else:
            color = "green" if final_resp.status in ("completed", "succeeded") else "red"
            stdout_console.print(f"\n[{color}]✓ Task finalized with status: {final_resp.status.upper()}[/{color}]")
            if final_resp.result:
                stdout_console.print(final_resp.result)
    except KeyboardInterrupt:
        stdout_console.print("\n[yellow]Interrupted by operator. Requesting cancellation...[/yellow]")
        try:
            lifecycle.cancel(resp.task_id)
            stdout_console.print("[dim]Task cancellation requested.[/dim]")
        except Exception:
            pass
        sys.exit(130)
    except Exception as e:
        print_error_and_exit(f"Task run failed: {e}", as_json=as_json)


@task_group.command("status")
@click.argument("task_id")
@click.option("--json", "as_json", is_flag=True, help="Output status as JSON.")
def task_status(task_id: str, as_json: bool) -> None:
    """Inspect durable execution status of a task."""
    lifecycle = ExecutionLifecycle()
    try:
        st = lifecycle.get_status(task_id)
        if as_json:
            print_output(st.__dict__, as_json=True)
        else:
            table = Table(title=f"Task {task_id}", show_header=True, header_style="bold cyan")
            table.add_column("Property", width=16, style="cyan")
            table.add_column("Value", min_width=30)
            table.add_row("Status", st.status.upper())
            table.add_row("Workspace", str(st.workspace_id or "—"))
            table.add_row("Terminal", str(st.terminal))
            if st.error:
                table.add_row("Error", f"[red]{st.error}[/red]")
            stdout_console.print(table)
    except Exception as e:
        print_error_and_exit(f"Failed to fetch task status: {e}", as_json=as_json)


@task_group.command("stop")
@click.argument("task_id")
@click.option("--json", "as_json", is_flag=True, help="Output cancellation response as JSON.")
def task_stop(task_id: str, as_json: bool) -> None:
    """Cancel an active task with cancel-wins semantics."""
    lifecycle = ExecutionLifecycle()
    try:
        res = lifecycle.cancel(task_id)
        if as_json:
            print_output(res, as_json=True)
        else:
            stdout_console.print(f"[bold green]✓ Cancellation signal dispatched for task:[/bold green] {task_id}")
    except Exception as e:
        print_error_and_exit(f"Failed to cancel task: {e}", as_json=as_json)


@task_group.command("retry")
@click.argument("task_id")
@click.option("--json", "as_json", is_flag=True, help="Output retry response as JSON.")
def task_retry(task_id: str, as_json: bool) -> None:
    """Retry a stopped or failed task."""
    lifecycle = ExecutionLifecycle()
    try:
        res = lifecycle.retry(task_id)
        if as_json:
            print_output(res, as_json=True)
        else:
            stdout_console.print(f"[bold green]✓ Task restarted successfully:[/bold green] {task_id}")
    except Exception as e:
        print_error_and_exit(f"Failed to retry task: {e}", as_json=as_json)


@task_group.command("approve")
@click.argument("gate_id")
@click.option("--reject", "is_reject", is_flag=True, help="Reject the approval gate instead of approving.")
@click.option("--comment", "-c", default="", help="Optional approval / rejection reason.")
@click.option("--json", "as_json", is_flag=True, help="Output decision response as JSON.")
def task_approve(gate_id: str, is_reject: bool, comment: str, as_json: bool) -> None:
    """Approve or reject a Human-in-the-Loop (HITL) gate."""
    lifecycle = ExecutionLifecycle()
    decision = "reject" if is_reject else "approve"
    try:
        res = lifecycle.approve(gate_id, decision=decision, comment=comment)
        if as_json:
            print_output(res, as_json=True)
        else:
            stdout_console.print(f"[bold green]✓ Gate {gate_id} decision submitted:[/bold green] {decision.upper()}")
    except Exception as e:
        print_error_and_exit(f"Failed to submit decision: {e}", as_json=as_json)
