"""
aetherion_cli/api/client.py
Typed API client wrapping core HTTP transport with endpoint-specific methods.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..client import Client as BaseClient
from .contracts import (
    ArtifactInfo,
    ExecutionState,
    TaskCreateRequest,
    TaskStatusResponse,
    WorkspaceInfo,
)


class ApiClient(BaseClient):
    """High-level client providing typed methods for CLI and TUI."""

    def get_health(self) -> Dict[str, Any]:
        """Fetch system health and capabilities."""
        return self.get("/api/system/health")

    def create_task(self, req: TaskCreateRequest) -> TaskStatusResponse:
        """Submit a new task for execution."""
        payload = req.to_dict()
        data = self.post("/api/tasks", json_body=payload)
        return TaskStatusResponse.from_dict(data)

    def get_task_status(self, task_id: str) -> TaskStatusResponse:
        """Query execution status of a task."""
        data = self.get(f"/api/tasks/{task_id}")
        return TaskStatusResponse.from_dict(data)

    def stop_task(self, task_id: str) -> Dict[str, Any]:
        """Request cancellation of an active task."""
        return self.post(f"/api/tasks/{task_id}/stop")

    def retry_task(self, task_id: str) -> Dict[str, Any]:
        """Retry a failed or stopped task."""
        return self.post(f"/api/tasks/{task_id}/retry")

    def approve_task(self, gate_id: str, decision: str = "approve", comment: str = "") -> Dict[str, Any]:
        """Submit an approval or rejection for a HITL gate."""
        endpoint = f"/api/z29/governance/{decision}/{gate_id}"
        return self.post(endpoint, json_body={"comment": comment, "decision": decision})

    def list_workspaces(self) -> List[WorkspaceInfo]:
        """List active user workspaces."""
        res = self.get("/api/workspaces")
        items = res if isinstance(res, list) else res.get("workspaces", [])
        return [WorkspaceInfo.from_dict(item) for item in items]

    def list_artifacts(self, session_id: Optional[str] = None) -> List[ArtifactInfo]:
        """List artifacts, optionally filtered by session."""
        params = {"session_id": session_id} if session_id else None
        res = self.get("/api/artifacts", params=params)
        items = res if isinstance(res, list) else res.get("artifacts", [])
        return [ArtifactInfo.from_dict(item) for item in items]

    def get_diff(self, task_id: str) -> str:
        """Get git/worktree diff for a completed or in-flight task."""
        res = self.get(f"/api/worktrees/{task_id}/diff")
        if isinstance(res, dict):
            return res.get("diff") or res.get("patch") or ""
        return str(res)
