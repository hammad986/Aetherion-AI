"""
aetherion_cli/execution/lifecycle.py
Task lifecycle coordination using durable backend states and cancel-wins semantics.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from ..api.client import ApiClient
from ..api.contracts import ExecutionState, TaskCreateRequest, TaskStatusResponse


class ExecutionLifecycle:
    """Coordinates task execution, polling, event watching, and cancellation."""

    def __init__(self, client: Optional[ApiClient] = None):
        self.client = client or ApiClient()

    def create_task(
        self,
        task: str,
        workspace_id: Optional[str] = None,
        model: str = "auto",
        mode: str = "autonomous",
        budget_cap: Optional[float] = None,
        use_worktree: bool = True,
    ) -> TaskStatusResponse:
        """Submit a new task to the durable execution backend."""
        req = TaskCreateRequest(
            task=task,
            workspace_id=workspace_id,
            model=model,
            mode=mode,
            budget_cap=budget_cap,
            use_worktree=use_worktree,
        )
        return self.client.create_task(req)

    def get_status(self, task_id: str) -> TaskStatusResponse:
        """Fetch current state of the task."""
        return self.client.get_task_status(task_id)

    def cancel(self, task_id: str) -> Dict[str, Any]:
        """Cancel a running or queued task with cancel-wins semantics."""
        return self.client.stop_task(task_id)

    def retry(self, task_id: str) -> Dict[str, Any]:
        """Retry a stopped or failed task."""
        return self.client.retry_task(task_id)

    def approve(self, gate_id: str, decision: str = "approve", comment: str = "") -> Dict[str, Any]:
        """Approve or reject an awaiting HITL gate."""
        return self.client.approve_task(gate_id, decision, comment)

    def watch(
        self,
        task_id: str,
        on_update: Optional[Callable[[TaskStatusResponse], None]] = None,
        poll_interval: float = 1.0,
        timeout: float = 3600.0,
    ) -> TaskStatusResponse:
        """Poll task state until a terminal status is reached."""
        deadline = time.time() + timeout
        last_status = None

        while time.time() < deadline:
            resp = self.get_status(task_id)
            if resp.status != last_status:
                last_status = resp.status
                if on_update:
                    on_update(resp)

            if resp.terminal:
                return resp

            time.sleep(poll_interval)

        raise TimeoutError(f"Task {task_id} did not complete within {timeout} seconds.")
