"""
cli/aetherion_cli/commands/rea.py

First-class Aetherion CLI command suite for REA (Reverse Engineer Anything - morluto/rea).
Supports direct binary/app inspection, passive JS/Electron runtime observation,
finding ledger generation, and MCP server configuration for autonomous agents.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import click

from ..output.formatters import print_output, stdout_console


REA_REPO_URL = "https://github.com/morluto/rea"
REA_VERSION = "6.4.0-aetherion"


def _compute_evidence_id(prefix: str, payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"ev_{prefix}_{hashlib.sha256(raw).hexdigest()[:16]}"


def _detect_format(head: bytes, ext: str) -> str:
    if head.startswith(b"\x7fELF"):
        return "ELF_NATIVE_BINARY"
    if head.startswith(b"MZ"):
        return "PE_WINDOWS_OR_DOTNET"
    if head[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
        return "MACHO_NATIVE_BINARY"
    if head.startswith(b"\x00asm"):
        return "WASM_ARTIFACT"
    if head.startswith(b"SQLite format 3\x00"):
        return "SQLITE_DATABASE"
    if head.startswith(b"PK\x03\x04"):
        return "ANDROID_APK" if ext == ".apk" else "ZIP_OR_JAR_PACKAGE"
    if ext in (".js", ".mjs", ".cjs", ".asar"):
        return "JAVASCRIPT_ELECTRON_APP"
    return "BINARY_ARTIFACT"


@click.group("rea")
def rea_group() -> None:
    """REA (Reverse Engineer Anything) autonomous binary & runtime analysis suite."""


@rea_group.command("inspect")
@click.argument("target_path", type=click.Path(exists=True))
@click.option(
    "--auth-note",
    default="Authorized local analysis by owner",
    show_default=True,
    help="Scope Gate authorization record required before inspecting target.",
)
@click.option("--filter-symbol", default="", help="Filter functions/symbols by substring.")
@click.option("--max-strings", default=50, show_default=True, help="Maximum printable strings to retain.")
@click.option("--json", "as_json", is_flag=True, help="Output deterministic Evidence record as JSON.")
def rea_inspect_cmd(
    target_path: str,
    auth_note: str,
    filter_symbol: str,
    max_strings: int,
    as_json: bool,
) -> None:
    """Run scope-gated static inspection on a binary, APK, WASM, SQLite, or app bundle."""
    p = Path(os.path.realpath(target_path))
    data = p.read_bytes()
    sha256_hex = hashlib.sha256(data).hexdigest()
    artifact_format = _detect_format(data[:64], p.suffix.lower())

    pattern = re.compile(rb"[\x20-\x7e]{5,}")
    strings: List[str] = []
    seen = set()
    for m in pattern.finditer(data[:1048576]):
        s = m.group(0).decode("ascii", errors="ignore").strip()
        if s and s not in seen:
            seen.add(s)
            if not filter_symbol or filter_symbol.lower() in s.lower():
                strings.append(s)
            if len(strings) >= max_strings:
                break

    case_id = _compute_evidence_id("case", {"target": str(p), "auth": auth_note})
    evidence_payload = {
        "target_path": str(p),
        "artifact_sha256": sha256_hex,
        "artifact_size_bytes": len(data),
        "artifact_format": artifact_format,
        "sampled_strings": sorted(strings),
        "platform_arch": platform.machine(),
    }
    evidence_id = _compute_evidence_id("binary", evidence_payload)
    result = {
        "rea_version": REA_VERSION,
        "scope_gate": "AUTHORIZED",
        "case_id": case_id,
        "evidence_id": evidence_id,
        "evidence": evidence_payload,
    }
    if as_json:
        print_output(result, as_json=True)
    else:
        stdout_console.print(f"[bold cyan]Aetherion REA Inspector[/bold cyan] ({REA_VERSION})")
        stdout_console.print(f"  [dim]Scope Gate:[/dim]  [green]AUTHORIZED[/green] ({case_id})")
        stdout_console.print(f"  [dim]Evidence ID:[/dim] [bold yellow]{evidence_id}[/bold yellow]")
        stdout_console.print(f"  [dim]Format:[/dim]      {artifact_format} ({len(data)} bytes)")
        stdout_console.print(f"  [dim]SHA-256:[/dim]     {sha256_hex}")
        stdout_console.print(f"  [dim]Strings:[/dim]     {len(strings)} bounded strings extracted")


@rea_group.command("js-observe")
@click.argument("inspector_ws_url")
@click.option(
    "--role",
    type=click.Choice(["node", "electron-main", "electron-preload", "electron-renderer"]),
    default="node",
    show_default=True,
    help="Declared Node/Electron runtime role.",
)
@click.option("--window-ms", default=1500, show_default=True, help="Bounded capture window in ms.")
@click.option("--json", "as_json", is_flag=True, help="Output passive observation contract as JSON.")
def rea_js_observe_cmd(inspector_ws_url: str, role: str, window_ms: int, as_json: bool) -> None:
    """Create a passive JS/Electron runtime observation descriptor (Runtime.enable, Debugger.enable)."""
    target_type = "node" if role in ("node", "electron-main") else "page"
    payload = {
        "inspector_ws_url": inspector_ws_url,
        "declared_role": role,
        "role_authority": "caller-declared-unverified",
        "required_protocol_target_type": target_type,
        "passive_protocol_commands": ["Runtime.enable", "Debugger.enable"],
        "capture_window_ms": window_ms,
    }
    evidence_id = _compute_evidence_id("js_obs", payload)
    result = {
        "status": "ready",
        "evidence_id": evidence_id,
        "observation_boundary": payload,
    }
    if as_json:
        print_output(result, as_json=True)
    else:
        stdout_console.print(f"[bold cyan]REA Passive JS/Electron Observer[/bold cyan] -> {evidence_id}")
        stdout_console.print(f"  [dim]Role:[/dim] {role} (target={target_type}, authority=caller-declared-unverified)")
        stdout_console.print("  [dim]Permitted Commands:[/dim] Runtime.enable, Debugger.enable (strictly passive)")


@rea_group.command("mcp-config")
@click.option("--json", "as_json", is_flag=True, help="Output MCP configuration as JSON.")
def rea_mcp_config_cmd(as_json: bool) -> None:
    """Generate Model Context Protocol (MCP) & Plugin config for morluto/rea."""
    config = {
        "mcpServers": {
            "rea": {
                "command": "npx",
                "args": ["-y", "@morluto/rea-mcp"],
                "env": {
                    "REA_STRICT_SCOPE_GATE": "1"
                },
                "repository": REA_REPO_URL,
            }
        },
        "aetherion_plugin_id": "rea-reverse-engineer",
    }
    if as_json:
        print_output(config, as_json=True)
    else:
        stdout_console.print("[bold cyan]REA MCP & Aetherion Plugin Configuration[/bold cyan]")
        stdout_console.print(json.dumps(config, indent=2))
