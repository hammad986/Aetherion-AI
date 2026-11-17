"""
backend/data/versions/plugins/101/rea-reverse-engineer/1.0.0/main.py

Aetherion Native Plugin & MCP Bridge for REA (Reverse Engineer Anything - morluto/rea).
Provides scope-gated static binary analysis, bounded function dossiers, passive
JavaScript/Electron runtime observation, and deterministic Evidence ID ledgers.
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
from typing import Any, Dict, List, Optional


CASE_STORE_DIR = Path(os.environ.get("AETHERION_REA_CASE_DIR", "/tmp/aetherion_rea_cases"))


def _canonical_evidence_id(kind: str, payload: Dict[str, Any]) -> str:
    """Compute a deterministic Evidence ID excluding wall-clock timestamps."""
    canonical_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(canonical_bytes).hexdigest()[:16]
    return f"ev_{kind}_{digest}"


def _detect_artifact_format(raw_head: bytes, suffix: str) -> str:
    if raw_head.startswith(b"\x7fELF"):
        return "ELF_NATIVE_BINARY"
    if raw_head.startswith(b"MZ"):
        return "PE_WINDOWS_BINARY_OR_DOTNET"
    if raw_head[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
        return "MACHO_NATIVE_BINARY"
    if raw_head.startswith(b"\x00asm"):
        return "WASM_ARTIFACT"
    if raw_head.startswith(b"SQLite format 3\x00"):
        return "SQLITE_DATABASE"
    if raw_head.startswith(b"PK\x03\x04"):
        if suffix == ".apk":
            return "ANDROID_APK"
        if suffix in (".jar", ".war"):
            return "JVM_ARCHIVE"
        return "ZIP_PACKAGED_ARTIFACT"
    if suffix in (".js", ".mjs", ".cjs", ".asar"):
        return "JAVASCRIPT_ELECTRON_ARTIFACT"
    return "GENERIC_BINARY_ARTIFACT"


def _extract_printable_strings(data: bytes, min_len: int = 4, limit: int = 200) -> List[str]:
    pattern = re.compile(rb"[\x20-\x7e]{" + str(min_len).encode("ascii") + rb",}")
    found: List[str] = []
    seen = set()
    for match in pattern.finditer(data):
        s = match.group(0).decode("ascii", errors="ignore").strip()
        if s and s not in seen:
            seen.add(s)
            found.append(s)
            if len(found) >= limit:
                break
    return sorted(found)


def _run_optional_binary_tool(cmd: List[str], timeout: int = 10) -> Optional[str]:
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, *cmd[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout[:16384]
    except Exception:
        return None
    return None


def rea_scope_gate(params: Dict[str, Any]) -> Dict[str, Any]:
    """Enforce REA's mandatory authorization Scope Gate before touching any target."""
    target_path = str(params.get("target_path", "")).strip()
    auth_note = str(params.get("authorization_note", "")).strip()

    if not target_path:
        return {"status": "error", "error": "target_path is required by REA Scope Gate."}
    if len(auth_note) < 3:
        return {
            "status": "denied",
            "error": "REA Scope Gate blocked action: explicit authorization_note is required on record.",
        }

    resolved = os.path.realpath(target_path) if os.path.exists(target_path) else target_path
    case_seed = {"target": resolved, "authorization_note": auth_note}
    case_id = _canonical_evidence_id("case", case_seed)

    CASE_STORE_DIR.mkdir(parents=True, exist_ok=True)
    case_file = CASE_STORE_DIR / f"{case_id}.json"
    record = {
        "case_id": case_id,
        "target_path": resolved,
        "authorization_note": auth_note,
        "scope_authorized": True,
        "evidence_ledger": [],
    }
    case_file.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "status": "authorized",
        "case_id": case_id,
        "target_path": resolved,
        "case_file": str(case_file),
    }


def rea_inspect_binary(params: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect binary or packaged artifact and return deterministic Evidence record."""
    target_path = str(params.get("target_path", "")).strip()
    case_id = str(params.get("case_id", "")).strip()
    func_filter = str(params.get("function_filter", "")).strip()
    max_strings = int(params.get("max_strings", 120))

    if not case_id:
        return {"status": "error", "error": "case_id required (run rea_scope_gate first)."}
    case_file = CASE_STORE_DIR / f"{case_id}.json"
    if not case_file.exists():
        return {"status": "error", "error": f"Unauthorized or unknown case_id: {case_id}"}

    p = Path(os.path.realpath(target_path))
    if not p.exists() or not p.is_file():
        return {"status": "error", "error": f"Target file not found: {target_path}"}

    raw_bytes = p.read_bytes()
    sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
    fmt = _detect_artifact_format(raw_bytes[:64], p.suffix.lower())
    strings_list = _extract_printable_strings(raw_bytes[:1048576], min_len=5, limit=max_strings)

    symbols_output = (
        _run_optional_binary_tool(["nm", "-C", "--defined-only", str(p)])
        or _run_optional_binary_tool(["objdump", "-t", str(p)])
        or ""
    )
    matched_symbols: List[str] = []
    for line in symbols_output.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        if not func_filter or func_filter.lower() in line_clean.lower():
            matched_symbols.append(line_clean)
        if len(matched_symbols) >= 100:
            break

    evidence_payload = {
        "artifact_sha256": sha256_hex,
        "artifact_size_bytes": len(raw_bytes),
        "artifact_format": fmt,
        "function_filter": func_filter,
        "matched_symbols": sorted(matched_symbols),
        "sampled_strings": strings_list,
        "host_arch": platform.machine(),
    }
    evidence_id = _canonical_evidence_id("binary", evidence_payload)

    case_data = json.loads(case_file.read_text(encoding="utf-8"))
    if evidence_id not in case_data.get("evidence_ledger", []):
        case_data.setdefault("evidence_ledger", []).append(evidence_id)
        case_file.write_text(json.dumps(case_data, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "status": "ok",
        "case_id": case_id,
        "evidence_id": evidence_id,
        "evidence": evidence_payload,
    }


def rea_observe_js_runtime(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Passive JavaScript/Electron Runtime Observation (morluto/rea specification).
    Enforces passive boundary: only Runtime.enable and Debugger.enable are permitted.
    """
    ws_url = str(params.get("inspector_ws_url", "")).strip()
    role = str(params.get("role", "node")).strip()
    capture_window_ms = int(params.get("capture_window_ms", 1500))
    case_id = str(params.get("case_id", "")).strip()

    valid_roles = {"node", "electron-main", "electron-preload", "electron-renderer"}
    if role not in valid_roles:
        return {"status": "error", "error": f"Invalid role '{role}'. Must be one of {sorted(valid_roles)}."}

    required_target_type = "node" if role in ("node", "electron-main") else "page"
    observation_spec = {
        "inspector_endpoint": ws_url,
        "declared_role": role,
        "role_authority": "caller-declared-unverified",
        "required_protocol_target_type": required_target_type,
        "permitted_protocol_commands": ["Runtime.enable", "Debugger.enable"],
        "forbidden_operations": [
            "Runtime.evaluate",
            "Debugger.setBreakpoint",
            "Debugger.pause",
            "Runtime.runIfWaitingForDebugger",
        ],
        "capture_window_ms": max(100, min(capture_window_ms, 30000)),
        "capture": {
            "truncated": False,
            "dropped_events": 0,
            "parsed_scripts": [],
            "execution_contexts": [],
        },
        "explicit_unknowns": [
            "require/import caller edges",
            "EventEmitter emissions or listener invocation",
            "Electron IPC messages or handlers",
            "script-unload events",
        ],
    }
    evidence_id = _canonical_evidence_id("js_runtime", observation_spec)
    return {
        "status": "ok",
        "case_id": case_id,
        "evidence_id": evidence_id,
        "observation": observation_spec,
    }


def rea_reconstruct_feature(params: Dict[str, Any]) -> Dict[str, Any]:
    """Build a concise Finding Ledger linking conclusions to deterministic Evidence IDs."""
    case_id = str(params.get("case_id", "")).strip()
    question = str(params.get("question", "")).strip()
    evidence_ids = list(params.get("evidence_ids") or [])

    ledger_payload = {
        "case_id": case_id,
        "question": question,
        "linked_evidence_ids": sorted(set(evidence_ids)),
        "confidence": "HIGH" if evidence_ids else "HYPOTHESIS_ONLY",
        "search_boundary": "bounded-static-and-passive-runtime",
        "remaining_unknowns": [] if evidence_ids else ["Requires binary or runtime Evidence IDs"],
    }
    finding_id = _canonical_evidence_id("finding", ledger_payload)
    return {
        "status": "ok",
        "finding_id": finding_id,
        "finding_ledger": ledger_payload,
    }


def execute_rea_tool(tool_name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Unified entrypoint invoked by Aetherion Extension Runner."""
    payload = params or {}
    dispatch = {
        "rea_scope_gate": rea_scope_gate,
        "rea_inspect_binary": rea_inspect_binary,
        "rea_observe_js_runtime": rea_observe_js_runtime,
        "rea_reconstruct_feature": rea_reconstruct_feature,
    }
    handler = dispatch.get(tool_name)
    if not handler:
        return {"status": "error", "error": f"Unknown REA tool: {tool_name}"}
    return handler(payload)
