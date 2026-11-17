#!/usr/bin/env bash
# ==============================================================================
# Aetherion CLI — Part 1 + Part 2 + Built-in REA Plugin Marketplace Installer
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="${SCRIPT_DIR}/aetherion_workspace"

echo "[1/5] Extracting clean Aetherion codebase..."
mkdir -p "${WORKSPACE_DIR}"
if [ -f "${SCRIPT_DIR}/aetherion-clean-codebase-base.zip" ]; then
    unzip -q -o "${SCRIPT_DIR}/aetherion-clean-codebase-base.zip" -d "${WORKSPACE_DIR}"
fi

echo "[2/5] Wiring Built-in Plugin Marketplace Catalog ('rea' listed in CLI plugins section)..."
EXT_DIR="${WORKSPACE_DIR}/backend/execution/extensions"
CLI_CMD_DIR="${WORKSPACE_DIR}/cli/aetherion_cli/commands"
mkdir -p "${EXT_DIR}" "${CLI_CMD_DIR}"

cp -f "${SCRIPT_DIR}/rea_marketplace_catalog_and_fetcher.py" "${EXT_DIR}/rea_marketplace.py"
cp -f "${SCRIPT_DIR}/rea_marketplace_catalog_and_fetcher.py" "${CLI_CMD_DIR}/rea_marketplace_catalog_and_fetcher.py"
cp -f "${SCRIPT_DIR}/rea_plugin_main.py" "${CLI_CMD_DIR}/rea_plugin_main.py"
cp -f "${SCRIPT_DIR}/rea_plugin_main.py" "${EXT_DIR}/rea_bridge.py"
cp -f "${SCRIPT_DIR}/cli_plugins_section.py" "${CLI_CMD_DIR}/plugins.py"
cp -f "${SCRIPT_DIR}/rea_cli_command.py" "${CLI_CMD_DIR}/rea.py"
cp -f "${SCRIPT_DIR}/rea_features.py" "${WORKSPACE_DIR}/backend/routes/rea_features.py"

echo "[3/5] Installing Part 2 Autonomous Execution Engine Upgrades..."
cp -f "${SCRIPT_DIR}/part2_execution_engine_upgrades.py" "${WORKSPACE_DIR}/backend/execution/part2_execution_engine_upgrades.py"

# Register 'plugins' and 'rea' command groups in CLI __main__.py
CLI_MAIN="${WORKSPACE_DIR}/cli/aetherion_cli/__main__.py"
if [ -f "${CLI_MAIN}" ] && ! grep -q "plugins_group" "${CLI_MAIN}"; then
    cat << 'EOF' >> "${CLI_MAIN}"

# --- Aetherion Plugin Marketplace & REA CLI Registration ---
try:
    from .commands.plugins import plugins_group
    from .commands.rea import rea_group
    cli.add_command(plugins_group)
    cli.add_command(rea_group)
except Exception:
    pass
EOF
fi

echo "[4/5] Cleaning stale git worktrees, __pycache__, and duplicate SQLite files..."
rm -rf "${WORKSPACE_DIR}/.aetherion_worktrees" "${WORKSPACE_DIR}/.pytest_cache"
rm -f "${WORKSPACE_DIR}/backend/durable_state.db"
find "${WORKSPACE_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${WORKSPACE_DIR}" -type f -name "*.pyc" -delete 2>/dev/null || true

if [ ! -f "${WORKSPACE_DIR}/.env" ] && [ -f "${WORKSPACE_DIR}/.env.example" ]; then
    cp "${WORKSPACE_DIR}/.env.example" "${WORKSPACE_DIR}/.env"
fi

echo "[5/5] Installing Aetherion CLI..."
if command -v pip >/dev/null 2>&1; then
    pip install -e "${WORKSPACE_DIR}/cli" --quiet || true
fi

echo "=============================================================================="
echo "Aetherion CLI (Part 1 + Part 2 + REA Plugin Marketplace) is Ready!"
echo "Try:"
echo "  aetherion plugins list          # Shows 'rea' in built-in Plugin Marketplace"
echo "  aetherion plugins install rea   # Downloads & installs morluto/rea from GitHub"
echo "=============================================================================="
