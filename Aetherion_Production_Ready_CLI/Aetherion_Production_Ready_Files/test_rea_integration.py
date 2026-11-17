"""
tests/test_rea_integration.py

End-to-end unit and contract verification suite for Aetherion REA
(Reverse Engineer Anything - morluto/rea) Plugin, CLI Command, and Scope Gate.
"""
from __future__ import annotations

from pathlib import Path
from click.testing import CliRunner

from rea_plugin_main import (
    execute_rea_tool,
    rea_inspect_binary,
    rea_observe_js_runtime,
    rea_reconstruct_feature,
    rea_scope_gate,
)
from rea_cli_command import rea_group


def test_rea_scope_gate_denies_without_authorization():
    res = rea_scope_gate({"target_path": "/bin/ls", "authorization_note": ""})
    assert res["status"] == "denied"


def test_rea_scope_gate_and_binary_inspection_deterministic(tmp_path: Path):
    sample_bin = tmp_path / "sample_elf.bin"
    sample_bin.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"Aetherion_Secret_Feature_Calc_10_Percent\x00" * 4)

    gate = execute_rea_tool(
        "rea_scope_gate",
        {
            "target_path": str(sample_bin),
            "authorization_note": "Owned test artifact for Aetherion REA verification",
        },
    )
    assert gate["status"] == "authorized"
    case_id = gate["case_id"]
    assert case_id.startswith("ev_case_")

    inspect_1 = execute_rea_tool(
        "rea_inspect_binary",
        {"target_path": str(sample_bin), "case_id": case_id},
    )
    inspect_2 = execute_rea_tool(
        "rea_inspect_binary",
        {"target_path": str(sample_bin), "case_id": case_id},
    )
    assert inspect_1["status"] == "ok"
    assert inspect_1["evidence_id"] == inspect_2["evidence_id"]
    assert inspect_1["evidence"]["artifact_format"] == "ELF_NATIVE_BINARY"

    ledger = execute_rea_tool(
        "rea_reconstruct_feature",
        {
            "case_id": case_id,
            "question": "How does percentage calculation work?",
            "evidence_ids": [inspect_1["evidence_id"]],
        },
    )
    assert ledger["status"] == "ok"
    assert ledger["finding_ledger"]["confidence"] == "HIGH"


def test_rea_passive_js_runtime_observation_boundary():
    obs = rea_observe_js_runtime(
        {
            "inspector_ws_url": "ws://127.0.0.1:9229/devtools/page/1",
            "role": "electron-renderer",
            "case_id": "ev_case_demo",
        }
    )
    assert obs["status"] == "ok"
    spec = obs["observation"]
    assert spec["required_protocol_target_type"] == "page"
    assert spec["role_authority"] == "caller-declared-unverified"
    assert spec["permitted_protocol_commands"] == ["Runtime.enable", "Debugger.enable"]


def test_rea_cli_commands(tmp_path: Path):
    sample_bin = tmp_path / "test_wasm.wasm"
    sample_bin.write_bytes(b"\x00asm\x01\x00\x00\x00export_speed_controller_rule\x00")

    runner = CliRunner()
    res_inspect = runner.invoke(rea_group, ["inspect", str(sample_bin), "--json"])
    assert res_inspect.exit_code == 0
    assert "WASM_ARTIFACT" in res_inspect.output

    res_mcp = runner.invoke(rea_group, ["mcp-config", "--json"])
    assert res_mcp.exit_code == 0
    assert "rea-reverse-engineer" in res_mcp.output
