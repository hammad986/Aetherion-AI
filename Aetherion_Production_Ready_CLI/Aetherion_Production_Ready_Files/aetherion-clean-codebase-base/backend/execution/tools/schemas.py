"""
backend/execution/tools/schemas.py — Agent tool definitions and structured contracts.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional


@dataclasses.dataclass
class ToolRequest:
    tool: str
    arguments: Dict[str, Any]
    task_id: str
    session_id: str


@dataclasses.dataclass
class ToolResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: float = 0.0
    data: Dict[str, Any] = dataclasses.field(default_factory=dict)
    error: Optional[str] = None
    security_event: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "data": self.data,
            "error": self.error,
            "security_event": self.security_event,
        }


ANTHROPIC_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "inspect_environment",
        "description": "Inspect workspace directory structure, files, and environment tools.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file",
        "description": "Read content from a file within the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to file within workspace"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file within the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to file"},
                "content": {"type": "string", "description": "Full UTF-8 content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Edit an existing file by replacing an exact substring (old_str) with new_str.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to file"},
                "old_str": {"type": "string", "description": "Exact text to find and replace"},
                "new_str": {"type": "string", "description": "New text to substitute"},
            },
            "required": ["path", "old_str", "new_str"],
        },
    },
    {
        "name": "list_dir",
        "description": "List contents of a directory in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path (defaults to '.')"},
                "recursive": {"type": "boolean", "description": "Whether to list recursively"},
            },
        },
    },
    {
        "name": "search_files",
        "description": "Search for a regex pattern in workspace files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex search pattern"},
                "path": {"type": "string", "description": "Directory to search from"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "delete_file",
        "description": "Delete a file or directory inside the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to file or directory"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "run_command",
        "description": "Execute a shell command inside the workspace sandbox.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"}
            },
            "required": ["command"],
        },
    },
    {
        "name": "run_tests",
        "description": "Run automated test suite (pytest or unittest) in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Optional custom test command"}
            },
        },
    },
    {
        "name": "check_syntax",
        "description": "Validate Python syntax using AST parser without executing the file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to Python file"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "git_status",
        "description": "Inspect git status of the workspace.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "git_diff",
        "description": "Inspect uncommitted git diffs.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "finish",
        "description": "Complete the task when all work and verifications are done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Executive summary of changes made"},
                "verification": {
                    "type": "string",
                    "enum": ["VERIFIED", "PARTIALLY VERIFIED", "FAILED", "NOT VERIFIED"],
                    "description": "Verification outcome claim",
                },
            },
            "required": ["summary"],
        },
    },
]


TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {t["name"]: t.get("input_schema", {}) for t in ANTHROPIC_TOOLS}


def get_anthropic_tools() -> List[Dict[str, Any]]:
    """Return tool definitions in Anthropic schema format."""
    return [dict(t) for t in ANTHROPIC_TOOLS]


def get_openai_tools() -> List[Dict[str, Any]]:
    """Return tool definitions converted to OpenAI function format."""
    oai_tools = []
    for t in ANTHROPIC_TOOLS:
        oai_tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        })
    return oai_tools
