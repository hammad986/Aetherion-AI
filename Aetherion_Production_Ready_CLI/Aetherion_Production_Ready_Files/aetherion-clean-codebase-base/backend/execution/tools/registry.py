"""
backend/execution/tools/registry.py — Agent tool registry and tool execution dispatcher.
"""
from __future__ import annotations

import ast
import logging
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..sandbox.boundary import WorkspaceBoundary

from ..sandbox.boundary import SandboxBoundaryException
from .schemas import ToolRequest, ToolResult, get_anthropic_tools, get_openai_tools, TOOL_SCHEMAS

logger = logging.getLogger("aetherion.tools.registry")


class AgentToolRegistry:
    """Core development tool registry executed within the workspace sandbox."""

    def __init__(self, boundary: Any):
        self.boundary = boundary
        self.workspace_dir = Path(boundary.workspace_dir)
        self.custom_handlers: Dict[str, Any] = {}
        self.custom_schemas: List[Dict[str, Any]] = []

    BUILTIN_TOOL_NAMES = {
        "inspect_environment",
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "search_files",
        "delete_file",
        "run_command",
        "run_tests",
        "check_syntax",
        "git_status",
        "git_diff",
        "git_commit",
        "finish",
    }

    def register_custom_tool(self, name: str, schema: Dict[str, Any], handler: Any) -> None:
        """Register dynamic tool provided by an approved plugin."""
        if name in self.BUILTIN_TOOL_NAMES:
            raise ValueError(f"Cannot register custom tool '{name}': conflicts with built-in core tool.")
        self.custom_handlers[name] = handler
        self.custom_schemas = [s for s in self.custom_schemas if s.get("name") != name]
        self.custom_schemas.append(schema)

    def inspect_environment(self) -> ToolResult:
        """Inspect workspace structure, git state, and environment tooling."""
        t0 = time.time()
        files = []
        for root, dirs, filenames in os.walk(self.workspace_dir):
            r = Path(root)
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            for fn in filenames:
                if not fn.startswith(".") and not fn.endswith(".pyc"):
                    rel = (r / fn).relative_to(self.workspace_dir).as_posix()
                    files.append(rel)

        git_ok = (self.workspace_dir / ".git").exists()
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

        data = {
            "workspace_dir": str(self.workspace_dir),
            "files": sorted(files)[:100],
            "total_files": len(files),
            "git_initialized": git_ok,
            "python_version": py_ver,
            "platform": sys.platform,
            "sandbox_mode": self.boundary.sandbox_mode,
            "container_isolated": self.boundary.is_docker_available,
        }
        duration_ms = (time.time() - t0) * 1000.0
        return ToolResult(
            ok=True,
            stdout=f"Environment inspected: {len(files)} files in workspace. Python {py_ver}. Sandbox: {self.boundary.sandbox_mode}.",
            data=data,
            duration_ms=duration_ms,
        )

    def read_file(self, path: str) -> ToolResult:
        """Read content from a workspace file."""
        t0 = time.time()
        try:
            if not isinstance(path, str) or not path.strip():
                return ToolResult(ok=False, exit_code=1, error="File path cannot be empty.")
            safe_p = self.boundary.resolve_safe_path(path)
            if safe_p.is_dir() or safe_p == self.boundary.workspace_dir:
                return ToolResult(ok=False, exit_code=1, error=f"Cannot read directory as file: '{path}'", duration_ms=(time.time() - t0) * 1000.0)
            if not safe_p.exists():
                return ToolResult(
                    ok=False,
                    exit_code=1,
                    error=f"File not found: '{path}'",
                    duration_ms=(time.time() - t0) * 1000.0,
                )
            content = safe_p.read_text(encoding="utf-8", errors="replace")
            return ToolResult(
                ok=True,
                stdout=content,
                data={"path": str(path), "bytes": len(content.encode("utf-8"))},
                duration_ms=(time.time() - t0) * 1000.0,
            )
        except SandboxBoundaryException as sbe:
            return ToolResult(ok=False, exit_code=1, error=str(sbe), security_event=True)
        except Exception as exc:
            return ToolResult(ok=False, exit_code=1, error=str(exc))

    def write_file(self, path: str, content: str) -> ToolResult:
        """Create or overwrite a file in the workspace."""
        t0 = time.time()
        try:
            if not isinstance(path, str) or not path.strip():
                return ToolResult(ok=False, exit_code=1, error="File path cannot be empty.")
            if not isinstance(content, str):
                return ToolResult(ok=False, exit_code=1, error="File content must be a string.")
            safe_p = self.boundary.resolve_safe_path(path)
            if safe_p.is_dir() or safe_p == self.boundary.workspace_dir:
                return ToolResult(ok=False, exit_code=1, error=f"Cannot write file to directory path '{path}'")
            safe_p.parent.mkdir(parents=True, exist_ok=True)
            safe_p.write_text(content, encoding="utf-8")
            bytes_written = len(content.encode("utf-8"))
            return ToolResult(
                ok=True,
                stdout=f"Wrote {bytes_written} bytes to '{path}'",
                data={"path": str(path), "bytes": bytes_written},
                duration_ms=(time.time() - t0) * 1000.0,
            )
        except SandboxBoundaryException as sbe:
            return ToolResult(ok=False, exit_code=1, error=str(sbe), security_event=True)
        except Exception as exc:
            return ToolResult(ok=False, exit_code=1, error=str(exc))

    def edit_file(self, path: str, old_str: str, new_str: str) -> ToolResult:
        """Edit an existing file by replacing old_str with new_str."""
        t0 = time.time()
        try:
            if not isinstance(path, str) or not path.strip():
                return ToolResult(ok=False, exit_code=1, error="File path cannot be empty.")
            if not isinstance(old_str, str) or not isinstance(new_str, str):
                return ToolResult(ok=False, exit_code=1, error="Edit patterns must be strings.")
            safe_p = self.boundary.resolve_safe_path(path)
            if safe_p.is_dir() or safe_p == self.boundary.workspace_dir:
                return ToolResult(ok=False, exit_code=1, error=f"Cannot edit directory as file: '{path}'")
            if not safe_p.exists():
                return ToolResult(ok=False, exit_code=1, error=f"File not found: '{path}'")

            content = safe_p.read_text(encoding="utf-8", errors="replace")
            if old_str not in content:
                return ToolResult(
                    ok=False,
                    exit_code=1,
                    error=f"Target pattern to replace was not found in '{path}'.",
                    duration_ms=(time.time() - t0) * 1000.0,
                )

            new_content = content.replace(old_str, new_str, 1)
            safe_p.write_text(new_content, encoding="utf-8")
            return ToolResult(
                ok=True,
                stdout=f"Successfully edited '{path}'.",
                data={"path": str(path)},
                duration_ms=(time.time() - t0) * 1000.0,
            )
        except SandboxBoundaryException as sbe:
            return ToolResult(ok=False, exit_code=1, error=str(sbe), security_event=True)
        except Exception as exc:
            return ToolResult(ok=False, exit_code=1, error=str(exc))

    def list_dir(self, path: str = ".", recursive: bool = False) -> ToolResult:
        """List directory contents."""
        t0 = time.time()
        try:
            safe_p = self.boundary.resolve_safe_path(path)
            if not safe_p.is_dir():
                return ToolResult(ok=False, exit_code=1, error=f"Not a directory: '{path}'")

            items = []
            if recursive:
                for root, dirs, files in os.walk(safe_p):
                    for f in files:
                        items.append(str((Path(root) / f).relative_to(safe_p)))
            else:
                items = [p.name for p in safe_p.iterdir()]

            return ToolResult(
                ok=True,
                stdout="\n".join(items),
                data={"items": items},
                duration_ms=(time.time() - t0) * 1000.0,
            )
        except SandboxBoundaryException as sbe:
            return ToolResult(ok=False, exit_code=1, error=str(sbe), security_event=True)
        except Exception as exc:
            return ToolResult(ok=False, exit_code=1, error=str(exc))

    def search_files(self, pattern: str, path: str = ".") -> ToolResult:
        """Search for pattern across files in workspace."""
        t0 = time.time()
        try:
            safe_p = self.boundary.resolve_safe_path(path)
            matches = []
            regex = re.compile(pattern, re.IGNORECASE)

            for root, dirs, files in os.walk(safe_p):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
                for fn in files:
                    fp = Path(root) / fn
                    try:
                        text = fp.read_text(encoding="utf-8", errors="ignore")
                        for line_idx, line in enumerate(text.splitlines(), start=1):
                            if regex.search(line):
                                rel = fp.relative_to(self.workspace_dir).as_posix()
                                matches.append(f"{rel}:{line_idx}: {line.strip()}")
                                if len(matches) >= 50:
                                    break
                    except Exception:
                        continue
                if len(matches) >= 50:
                    break

            return ToolResult(
                ok=True,
                stdout="\n".join(matches),
                data={"matches": matches},
                duration_ms=(time.time() - t0) * 1000.0,
            )
        except SandboxBoundaryException as sbe:
            return ToolResult(ok=False, exit_code=1, error=str(sbe), security_event=True)
        except Exception as exc:
            return ToolResult(ok=False, exit_code=1, error=str(exc))

    def delete_file(self, path: str) -> ToolResult:
        """Delete a file in workspace."""
        t0 = time.time()
        try:
            if not isinstance(path, str) or not path.strip():
                return ToolResult(ok=False, exit_code=1, error="File path cannot be empty.")
            safe_p = self.boundary.resolve_safe_path(path)
            if safe_p == self.boundary.workspace_dir:
                return ToolResult(ok=False, exit_code=1, error="Deleting workspace root directory is prohibited", security_event=True)
            if safe_p.is_file():
                safe_p.unlink()
                return ToolResult(ok=True, stdout=f"Deleted '{path}'", duration_ms=(time.time() - t0) * 1000.0)
            elif safe_p.is_dir():
                shutil.rmtree(safe_p)
                return ToolResult(ok=True, stdout=f"Deleted directory '{path}'", duration_ms=(time.time() - t0) * 1000.0)
            return ToolResult(ok=False, exit_code=1, error=f"Path not found: '{path}'")
        except SandboxBoundaryException as sbe:
            return ToolResult(ok=False, exit_code=1, error=str(sbe), security_event=True)
        except Exception as exc:
            return ToolResult(ok=False, exit_code=1, error=str(exc))

    def run_command(
        self,
        command: str,
        timeout: float = 60.0,
        cancel_event: Optional[threading.Event] = None,
    ) -> ToolResult:
        """Run arbitrary governed shell command."""
        return self.boundary.run_command(command, timeout=timeout, cancel_event=cancel_event)

    def check_syntax(self, path: str) -> ToolResult:
        """Verify python syntax of a file without executing it."""
        t0 = time.time()
        try:
            safe_p = self.boundary.resolve_safe_path(path)
            if not safe_p.exists():
                return ToolResult(ok=False, exit_code=1, error=f"File not found: '{path}'")
            code = safe_p.read_text(encoding="utf-8", errors="replace")
            ast.parse(code, filename=str(safe_p))
            return ToolResult(
                ok=True,
                stdout=f"Syntax verified valid for '{path}'",
                duration_ms=(time.time() - t0) * 1000.0,
            )
        except SyntaxError as syn_err:
            return ToolResult(
                ok=False,
                exit_code=1,
                error=f"Syntax error in '{path}' line {syn_err.lineno}: {syn_err.msg}",
                stderr=f"SyntaxError: {syn_err.msg} at line {syn_err.lineno}",
                duration_ms=(time.time() - t0) * 1000.0,
            )
        except Exception as exc:
            return ToolResult(ok=False, exit_code=1, error=str(exc))

    def run_tests(
        self,
        cmd: Optional[str] = None,
        timeout: float = 60.0,
        cancel_event: Optional[threading.Event] = None,
    ) -> ToolResult:
        """Execute automated tests in workspace and capture results."""
        if not cmd:
            test_files = list(self.workspace_dir.glob("test_*.py")) + list(self.workspace_dir.glob("*_test.py"))
            if test_files:
                cmd = f"{sys.executable} -m pytest -q"
            else:
                cmd = f"{sys.executable} -m unittest discover -s . -p 'test_*.py'"

        res = self.boundary.run_command(cmd, timeout=timeout, cancel_event=cancel_event)
        res.data["command"] = cmd
        return res

    def git_status(self) -> ToolResult:
        """Inspect git repository status."""
        return self.boundary.run_command("git status --short")

    def git_diff(self) -> ToolResult:
        """Inspect uncommitted git diffs."""
        return self.boundary.run_command("git diff")

    def git_commit(self, message: str) -> ToolResult:
        """Stage all changes and create git commit using isolated agent identity."""
        self.boundary.run_command("git init")
        self.boundary.run_command("git add -A")
        msg_clean = message.replace('"', '\\"')
        git_env = {
            "GIT_AUTHOR_NAME": "Aetherion Agent",
            "GIT_AUTHOR_EMAIL": "agent@aetherion.ai",
            "GIT_COMMITTER_NAME": "Aetherion Agent",
            "GIT_COMMITTER_EMAIL": "agent@aetherion.ai",
        }
        cmd = f'git -c user.name="Aetherion Agent" -c user.email="agent@aetherion.ai" commit -m "{msg_clean}"'
        return self.boundary.run_command(cmd, env=git_env)

    def validate_request(self, req: ToolRequest) -> Optional[ToolResult]:
        """Validate ToolRequest structure, argument dictionary, and JSON schema constraints."""
        if not isinstance(req, ToolRequest):
            return ToolResult(ok=False, exit_code=1, error="Invalid tool request: expected ToolRequest instance")

        tool = req.tool
        if not isinstance(tool, str) or not tool.strip():
            return ToolResult(ok=False, exit_code=1, error="Tool name must be a non-empty string")

        if isinstance(req.arguments, str):
            try:
                import json
                req.arguments = json.loads(req.arguments)
            except Exception:
                pass

        if not isinstance(req.arguments, dict):
            return ToolResult(
                ok=False,
                exit_code=1,
                error=f"Invalid arguments for tool '{tool}': expected a JSON object (dict), got {type(req.arguments).__name__}",
            )

        args = req.arguments
        schema = TOOL_SCHEMAS.get(tool)
        if schema:
            properties = schema.get("properties", {})
            required = schema.get("required", [])

            # Normalize common LLM parameter aliases
            if isinstance(args, dict):
                if "path" not in args or args.get("path") is None:
                    for alias in ("file_path", "file_name", "filename", "filepath", "file", "target", "target_file", "dest", "destination", "rel_path", "relative_path", "name"):
                        if alias in args and args[alias] is not None:
                            args["path"] = args[alias]
                            break
                if "content" not in args or args.get("content") is None:
                    for alias in ("code", "text", "body", "data", "contents"):
                        if alias in args and args[alias] is not None:
                            args["content"] = args[alias]
                            break
                if "command" not in args or args.get("command") is None:
                    for alias in ("cmd", "script", "bash", "shell"):
                        if alias in args and args[alias] is not None:
                            args["command"] = args[alias]
                            break

            # 1. Enforce required properties
            for field in required:
                if field not in args or args[field] is None:
                    return ToolResult(ok=False, exit_code=1, error=f"Missing required parameter '{field}' for tool '{tool}'. Received keys: {list(args.keys()) if isinstance(args, dict) else type(args)}")

            # 2. Enforce field types and constraints
            for k, v in args.items():
                if k in properties:
                    prop_def = properties[k]
                    expected_type = prop_def.get("type")
                    if expected_type == "string" and not isinstance(v, str):
                        return ToolResult(
                            ok=False,
                            exit_code=1,
                            error=f"Invalid type for parameter '{k}' in tool '{tool}': expected string, got {type(v).__name__}",
                        )
                    elif expected_type == "boolean" and not isinstance(v, bool):
                        return ToolResult(
                            ok=False,
                            exit_code=1,
                            error=f"Invalid type for parameter '{k}' in tool '{tool}': expected boolean, got {type(v).__name__}",
                        )
                    elif expected_type == "integer" and (not isinstance(v, int) or isinstance(v, bool)):
                        return ToolResult(
                            ok=False,
                            exit_code=1,
                            error=f"Invalid type for parameter '{k}' in tool '{tool}': expected integer, got {type(v).__name__}",
                        )
                    elif expected_type == "number" and (not isinstance(v, (int, float)) or isinstance(v, bool)):
                        return ToolResult(
                            ok=False,
                            exit_code=1,
                            error=f"Invalid type for parameter '{k}' in tool '{tool}': expected number, got {type(v).__name__}",
                        )
                    elif expected_type == "object" and not isinstance(v, dict):
                        return ToolResult(
                            ok=False,
                            exit_code=1,
                            error=f"Invalid type for parameter '{k}' in tool '{tool}': expected object, got {type(v).__name__}",
                        )
                    elif expected_type == "array" and not isinstance(v, list):
                        return ToolResult(
                            ok=False,
                            exit_code=1,
                            error=f"Invalid type for parameter '{k}' in tool '{tool}': expected array, got {type(v).__name__}",
                        )

                    if "enum" in prop_def and v not in prop_def["enum"]:
                        return ToolResult(
                            ok=False,
                            exit_code=1,
                            error=f"Invalid value for parameter '{k}' in tool '{tool}': expected one of {prop_def['enum']}, got '{v}'",
                        )

        # 3. Enforce tool-specific semantic constraints
        if tool in ("read_file", "write_file", "edit_file", "delete_file", "check_syntax"):
            p = args.get("path")
            if p is not None and (not isinstance(p, str) or not p.strip()):
                return ToolResult(ok=False, exit_code=1, error=f"Parameter 'path' in tool '{tool}' cannot be empty")

        if tool == "run_command":
            cmd = args.get("command")
            if cmd is not None and (not isinstance(cmd, str) or not cmd.strip()):
                return ToolResult(ok=False, exit_code=1, error="Parameter 'command' in tool 'run_command' cannot be empty")

        if tool == "finish":
            summary = args.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                return ToolResult(ok=False, exit_code=1, error="Parameter 'summary' in tool 'finish' cannot be empty")

        return None

    def execute_tool(self, req: ToolRequest, cancel_event: Optional[threading.Event] = None) -> ToolResult:
        """Dispatch structured tool call with strict validation and policy enforcement."""
        val_err = self.validate_request(req)
        if val_err is not None:
            return val_err

        tool = req.tool
        args = req.arguments

        try:
            if tool == "inspect_environment":
                return self.inspect_environment()
            elif tool == "read_file":
                return self.read_file(args.get("path", ""))
            elif tool == "write_file":
                return self.write_file(args.get("path", ""), args.get("content", ""))
            elif tool == "edit_file":
                return self.edit_file(args.get("path", ""), args.get("old_str", ""), args.get("new_str", ""))
            elif tool == "list_dir":
                return self.list_dir(args.get("path", "."), recursive=args.get("recursive", False))
            elif tool == "search_files":
                return self.search_files(args.get("pattern", ""), args.get("path", "."))
            elif tool == "delete_file":
                return self.delete_file(args.get("path", ""))
            elif tool == "run_command":
                timeout_val = args.get("timeout", 60.0)
                try:
                    timeout_float = float(timeout_val)
                except (TypeError, ValueError):
                    timeout_float = 60.0
                return self.run_command(args.get("command", ""), timeout=timeout_float, cancel_event=cancel_event)
            elif tool == "run_tests":
                timeout_val = args.get("timeout", 60.0)
                try:
                    timeout_float = float(timeout_val)
                except (TypeError, ValueError):
                    timeout_float = 60.0
                return self.run_tests(cmd=args.get("cmd"), timeout=timeout_float, cancel_event=cancel_event)
            elif tool == "check_syntax":
                return self.check_syntax(args.get("path", ""))
            elif tool == "git_status":
                return self.git_status()
            elif tool == "git_diff":
                return self.git_diff()
            elif tool == "git_commit":
                return self.git_commit(args.get("message", "Autonomous commit by Aetherion"))
            elif tool == "finish":
                return ToolResult(
                    ok=True,
                    stdout=args.get("summary", "Task completed."),
                    data={"summary": args.get("summary"), "verification": args.get("verification")},
                )
            elif tool in self.custom_handlers:
                return self.custom_handlers[tool](req)
            else:
                return ToolResult(ok=False, exit_code=1, error=f"Unknown tool: '{tool}'")
        except SandboxBoundaryException as sbe:
            return ToolResult(ok=False, exit_code=1, error=str(sbe), security_event=True)
        except Exception as exc:
            logger.error("Unexpected error in tool execution for %s: %s", tool, exc, exc_info=True)
            return ToolResult(ok=False, exit_code=1, error=str(exc))

    def get_anthropic_tools(self=None) -> List[Dict[str, Any]]:
        """Return Anthropic-compatible tool definitions for LLM calling including plugins."""
        base = get_anthropic_tools()
        schemas = getattr(self, "custom_schemas", []) if self is not None else []
        return base + list(schemas)

    def get_openai_tools(self=None) -> List[Dict[str, Any]]:
        """Return OpenAI-compatible tool definitions for LLM calling including plugins."""
        base = get_openai_tools()
        schemas = getattr(self, "custom_schemas", []) if self is not None else []
        # Convert any custom Anthropic schemas to OpenAI format if needed
        converted = []
        for s in schemas:
            if s.get("type") == "function" and "function" in s:
                converted.append(s)
            else:
                converted.append({
                    "type": "function",
                    "function": {
                        "name": s.get("name", ""),
                        "description": s.get("description", ""),
                        "parameters": s.get("input_schema", {}),
                    },
                })
        return base + converted
