#!/usr/bin/env bash
# ==============================================================================
# Aetherion CLI — Part 3 Elite CLI/TUI Engine & Backend Control Plane Installer
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="${SCRIPT_DIR}/aetherion_workspace"

echo "[1/5] Ensuring Part 1 + Part 2 base workspace is unpacked and initialized..."
if [ ! -d "${WORKSPACE_DIR}/backend" ]; then
    bash "${SCRIPT_DIR}/setup_part2_and_rea_marketplace.sh"
fi

echo "[2/5] Installing Part 3 CLI/TUI Slash-Command Harness & Doctor Command..."
CLI_PKG_DIR="${WORKSPACE_DIR}/cli/aetherion_cli"
mkdir -p "${CLI_PKG_DIR}/commands"
cp -f "${SCRIPT_DIR}/part3_cli_tui_and_harness.py" "${CLI_PKG_DIR}/part3_cli_tui_and_harness.py"

CLI_MAIN="${CLI_PKG_DIR}/__main__.py"
if [ -f "${CLI_MAIN}" ] && ! grep -q "doctor_cmd" "${CLI_MAIN}"; then
    cat << 'EOF' >> "${CLI_MAIN}"

# --- Part 3 Doctor Diagnostics & TUI Slash Harness Registration ---
try:
    from .part3_cli_tui_and_harness import doctor_cmd
    cli.add_command(doctor_cmd)
except Exception:
    pass
EOF
fi

echo "[3/5] Installing Part 3 Backend Control Plane & Reconciled D3 Routes..."
ROUTES_DIR="${WORKSPACE_DIR}/backend/routes"
mkdir -p "${ROUTES_DIR}"
cp -f "${SCRIPT_DIR}/part3_backend_control_plane_routes.py" "${ROUTES_DIR}/part3_backend_control_plane_routes.py"

BACKEND_APP="${WORKSPACE_DIR}/backend/app.py"
if [ -f "${BACKEND_APP}" ] && ! grep -q "control_plane_bp" "${BACKEND_APP}"; then
    cat << 'EOF' >> "${BACKEND_APP}"

# --- Part 3 Unified Control Plane, Plugin Marketplace & D3 Blueprint ---
try:
    from routes.part3_backend_control_plane_routes import control_plane_bp
    app.register_blueprint(control_plane_bp)
except Exception:
    pass
EOF
fi

echo "[4/5] Running Option-B Legacy Worktree Hygiene Guard & Bytecode Cleanup..."
rm -rf "${WORKSPACE_DIR}/.aetherion_worktrees" "${WORKSPACE_DIR}/.pytest_cache"
find "${WORKSPACE_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${WORKSPACE_DIR}" -type f -name "*.pyc" -delete 2>/dev/null || true

echo "[5/5] Refreshing editable Aetherion CLI installation..."
if command -v pip >/dev/null 2>&1; then
    pip install -e "${WORKSPACE_DIR}/cli" --quiet || true
fi

echo "=============================================================================="
echo "Part 3 (Aetherion CLI/TUI Harness + Backend Control Plane Routes) Ready!"
echo "New CLI & TUI Capabilities:"
echo "  aetherion doctor --prune-worktrees"
echo "  aetherion plugins list / aetherion plugins install rea"
echo "  TUI Slash Commands: /plan, /plugins, /rea, /worktree, /checkpoint, /doctor"
echo "=============================================================================="
