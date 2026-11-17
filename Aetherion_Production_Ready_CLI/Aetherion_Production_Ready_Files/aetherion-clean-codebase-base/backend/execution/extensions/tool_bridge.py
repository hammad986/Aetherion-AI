"""
backend/execution/extensions/tool_bridge.py — Bridge for executing plugin tools via AgentToolRegistry and ExtensionRunner.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..sandbox.boundary import SandboxBoundaryException, WorkspaceBoundary
from ..tools.schemas import ToolRequest, ToolResult
from .manager import ExtensionManager
from .models import PermissionEnum
from .runner import ExtensionRunner

logger = logging.getLogger("aetherion.extensions.tool_bridge")


class PluginToolBridge:
    """Dispatches plugin tools through governance, permission checks, and isolated sandboxed execution."""

    def __init__(
        self,
        manager: ExtensionManager,
        boundary: WorkspaceBoundary,
        runner: Optional[ExtensionRunner] = None,
    ):
        self.manager = manager
        self.boundary = boundary
        self.runner = runner or ExtensionRunner()

    def get_plugin_tools_schemas(self, owner_id: str) -> List[Dict[str, Any]]:
        """Collect all tools provided by enabled plugins owned by this tenant."""
        plugins = self.manager.list_plugins(owner_id, enabled_only=True)
        schemas = []
        for p in plugins:
            tools = p.get("provided_tools", [])
            for t in tools:
                # Format Anthropic/OpenAI compatible schema
                schemas.append({
                    "name": t.get("name"),
                    "description": f"[Plugin: {p.get('name')}] {t.get('description', '')}",
                    "input_schema": t.get("parameters") or t.get("input_schema") or {
                        "type": "object",
                        "properties": {},
                    },
                    "_plugin_id": p.get("id"),
                    "_required_permissions": t.get("required_permissions", []),
                })
        return schemas

    def execute_plugin_tool(self, req: ToolRequest, owner_id: str) -> ToolResult:
        """Execute a tool provided by an installed and enabled plugin via isolated ExtensionRunner."""
        tool_name = getattr(req, "tool", None) or getattr(req, "name", None) or (req.get("tool") if isinstance(req, dict) else "") or ""
        t0 = time.time()

        # Find matching enabled plugin
        enabled_plugins = self.manager.list_plugins(owner_id, enabled_only=True)
        matched_plugin = None
        matched_tool_def = None

        for p in enabled_plugins:
            for t in p.get("provided_tools", []):
                if t.get("name") == tool_name:
                    matched_plugin = p
                    matched_tool_def = t
                    break
            if matched_plugin:
                break

        if not matched_plugin:
            # Check if it exists but is disabled
            all_plugins = self.manager.list_plugins(owner_id, enabled_only=False)
            for p in all_plugins:
                for t in p.get("provided_tools", []):
                    if t.get("name") == tool_name:
                        return ToolResult(
                            ok=False,
                            exit_code=1,
                            error=f"Tool '{tool_name}' belongs to disabled plugin '{p['id']}' and cannot be executed.",
                        )
            return ToolResult(ok=False, exit_code=1, error=f"Plugin tool '{tool_name}' not found.")

        # Permission verification
        plugin_perms = set(matched_plugin.get("permissions", []))
        tool_req_perms = set(matched_tool_def.get("required_permissions", []))
        missing_perms = tool_req_perms - plugin_perms
        if missing_perms:
            return ToolResult(
                ok=False,
                exit_code=1,
                error=f"Permission denied: Plugin '{matched_plugin['id']}' lacks required permissions: {sorted(missing_perms)}",
                security_event=True,
            )

        # Check plugin install directory
        install_dir = Path(matched_plugin.get("install_path", ""))
        entrypoint_str = matched_plugin.get("entrypoint", "")
        file_part, _, func_part = entrypoint_str.partition(":")
        handler_file = install_dir / file_part

        if not handler_file.exists():
            return ToolResult(
                ok=False,
                exit_code=1,
                error=f"Plugin entrypoint file '{handler_file.name}' not found at {install_dir}.",
            )

        args = getattr(req, "arguments", {}) or (req.get("arguments") if isinstance(req, dict) else {}) or {}

        try:
            # Sandbox Boundary Enforcement on path-like arguments
            for k, v in args.items():
                if isinstance(v, str) and ("/" in v or "\\" in v or k in ("path", "file_path", "filename", "target")):
                    self.boundary.resolve_safe_path(v)

            # Isolated execution dispatch
            return self.runner.execute_tool(req, matched_plugin, self.boundary.workspace_dir)

        except SandboxBoundaryException as sbe:
            return ToolResult(ok=False, exit_code=1, error=str(sbe), security_event=True)
        except Exception as exc:
            logger.error("Plugin tool execution failed (%s): %s", tool_name, exc)
            return ToolResult(ok=False, exit_code=1, error=f"Plugin execution error: {exc}")
