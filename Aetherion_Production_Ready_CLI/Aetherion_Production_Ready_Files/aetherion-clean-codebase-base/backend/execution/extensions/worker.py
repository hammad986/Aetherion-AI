"""
backend/execution/extensions/worker.py — Standalone worker executing extension code inside isolated environments.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict


def execute_request(req_data: Dict[str, Any], plugin_root: Path, workspace_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Execute single tool invocation within this isolated worker process."""
    req_id = req_data.get("request_id", "")
    entrypoint = req_data.get("entrypoint", "main.py:run")
    tool_name = req_data.get("tool_name", "")
    arguments = req_data.get("arguments") or {}
    context = req_data.get("context") or {}

    t0 = time.time()
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    old_stdout = sys.stdout
    old_stderr = sys.stderr

    # Ensure plugin directory is on sys.path
    plugin_str = str(plugin_root.resolve())
    if plugin_str not in sys.path:
        sys.path.insert(0, plugin_str)

    # Change directory to workspace if provided
    if workspace_dir and workspace_dir.exists():
        try:
            os.chdir(str(workspace_dir))
        except Exception:
            pass

    try:
        sys.stdout = captured_stdout
        sys.stderr = captured_stderr

        # Resolve entrypoint file and function
        if ":" in entrypoint:
            file_part, _, func_name = entrypoint.partition(":")
        else:
            file_part, func_name = entrypoint, "main"

        func_name = func_name or "main"
        target_file = plugin_root / file_part
        if not target_file.exists():
            # Try searching directly
            possible = list(plugin_root.glob(file_part))
            if possible:
                target_file = possible[0]
            else:
                return {
                    "request_id": req_id,
                    "ok": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": f"Entrypoint file '{file_part}' not found in {plugin_root}",
                    "error": f"Entrypoint file '{file_part}' not found in plugin root.",
                    "duration_ms": (time.time() - t0) * 1000.0,
                    "security_event": False,
                }

        module_name = f"plugin_mod_{target_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, str(target_file))
        if not spec or not spec.loader:
            return {
                "request_id": req_id,
                "ok": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Could not create module spec for {target_file}",
                "error": f"Could not load module {target_file}",
                "duration_ms": (time.time() - t0) * 1000.0,
                "security_event": False,
            }

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)

        if not hasattr(mod, func_name):
            return {
                "request_id": req_id,
                "ok": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Function '{func_name}' not found in {target_file.name}",
                "error": f"Function '{func_name}' not found in plugin module.",
                "duration_ms": (time.time() - t0) * 1000.0,
                "security_event": False,
            }

        fn = getattr(mod, func_name)

        # Invoke handler with flexible argument unpacking
        try:
            raw_out = fn(**arguments, context=context)
        except TypeError:
            try:
                raw_out = fn(arguments, context=context)
            except TypeError:
                try:
                    raw_out = fn(**arguments)
                except TypeError:
                    try:
                        raw_out = fn(arguments)
                    except TypeError:
                        raw_out = fn()

        sys.stdout = old_stdout
        sys.stderr = old_stderr
        duration_ms = (time.time() - t0) * 1000.0

        out_str = captured_stdout.getvalue()
        err_str = captured_stderr.getvalue()

        if isinstance(raw_out, dict):
            return {
                "request_id": req_id,
                "ok": bool(raw_out.get("ok", True)),
                "exit_code": int(raw_out.get("exit_code", 0)),
                "stdout": str(raw_out.get("stdout") or raw_out.get("output") or out_str),
                "stderr": str(raw_out.get("stderr") or err_str),
                "error": raw_out.get("error"),
                "data": raw_out.get("data", raw_out),
                "duration_ms": duration_ms,
                "security_event": bool(raw_out.get("security_event", False)),
            }
        else:
            final_stdout = out_str if out_str else (str(raw_out) if raw_out is not None else "")
            return {
                "request_id": req_id,
                "ok": True,
                "exit_code": 0,
                "stdout": final_stdout,
                "stderr": err_str,
                "error": None,
                "data": raw_out,
                "duration_ms": duration_ms,
                "security_event": False,
            }

    except Exception as exc:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        duration_ms = (time.time() - t0) * 1000.0
        err_str = captured_stderr.getvalue() + "\n" + traceback.format_exc()
        return {
            "request_id": req_id,
            "ok": False,
            "exit_code": 1,
            "stdout": captured_stdout.getvalue(),
            "stderr": err_str,
            "error": str(exc),
            "data": None,
            "duration_ms": duration_ms,
            "security_event": False,
        }


def main():
    parser = argparse.ArgumentParser(description="Aetherion Isolated Extension Worker")
    parser.add_argument("--input", "-i", type=str, help="Path to request JSON file (default: stdin)")
    parser.add_argument("--output", "-o", type=str, help="Path to response JSON file (default: stdout)")
    parser.add_argument("--plugin-dir", "-p", type=str, default="/plugin", help="Path to plugin directory")
    parser.add_argument("--workspace-dir", "-w", type=str, default="/workspace", help="Path to workspace directory")
    args = parser.parse_args()

    # Read Request
    if args.input and os.path.exists(args.input):
        with open(args.input, "r", encoding="utf-8") as f:
            req_data = json.load(f)
    else:
        req_data = json.load(sys.stdin)

    plugin_dir = Path(args.plugin_dir if os.path.exists(args.plugin_dir) else req_data.get("context", {}).get("plugin_dir", "/plugin"))
    workspace_dir = Path(args.workspace_dir if os.path.exists(args.workspace_dir) else req_data.get("context", {}).get("workspace_dir", "/workspace"))

    resp_data = execute_request(req_data, plugin_dir, workspace_dir)

    # Write Response
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(resp_data, f, indent=2)
    else:
        json.dump(resp_data, sys.stdout)
        sys.stdout.flush()


if __name__ == "__main__":
    main()
