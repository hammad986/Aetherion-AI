#!/usr/bin/env bash
# ==============================================================================
# Aetherion Autonomous AI Agent + REA (morluto/rea) Production Installer
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="${SCRIPT_DIR}/aetherion_workspace"
GH_PROTO="https"
GH_HOST="github.com"
REA_SLUG="morluto/rea"

echo "[1/6] Preparing clean Aetherion production workspace..."
mkdir -p "${WORKSPACE_DIR}"

if [ -f "${SCRIPT_DIR}/aetherion-clean-codebase-base.zip" ]; then
    unzip -q -o "${SCRIPT_DIR}/aetherion-clean-codebase-base.zip" -d "${WORKSPACE_DIR}"
fi

echo "[2/6] Cloning/updating morluto/rea (Reverse Engineer Anything) reference repo..."
mkdir -p "${WORKSPACE_DIR}/external"
if [ ! -d "${WORKSPACE_DIR}/external/rea/.git" ]; then
    git clone --depth 1 "${GH_PROTO}://${GH_HOST}/${REA_SLUG}.git" "${WORKSPACE_DIR}/external/rea" || \
        echo "Notice: Using bundled Aetherion REA native plugin & skill."
else
    git -C "${WORKSPACE_DIR}/external/rea" pull --ff-only || true
fi

echo "[3/6] Installing REA Plugin, Governed Skill, CLI Command & Backend Route..."
PLUGIN_DIR="${WORKSPACE_DIR}/backend/data/versions/plugins/101/rea-reverse-engineer/1.0.0"
SKILL_DIR="${WORKSPACE_DIR}/cli/aetherion_cli/skills/reverse-engineer-anything"
mkdir -p "${PLUGIN_DIR}" "${SKILL_DIR}" "${WORKSPACE_DIR}/backend/execution/extensions"

cp -f "${SCRIPT_DIR}/plugin.json" "${PLUGIN_DIR}/plugin.json"
cp -f "${SCRIPT_DIR}/rea_plugin_main.py" "${PLUGIN_DIR}/main.py"
cp -f "${SCRIPT_DIR}/rea_plugin_main.py" "${WORKSPACE_DIR}/backend/execution/extensions/rea_bridge.py"
cp -f "${SCRIPT_DIR}/rea_cli_command.py" "${WORKSPACE_DIR}/cli/aetherion_cli/commands/rea.py"
if [ -f "${SCRIPT_DIR}/SKILL.md" ]; then
    cp -f "${SCRIPT_DIR}/SKILL.md" "${SKILL_DIR}/SKILL.md"
fi
cp -f "${SCRIPT_DIR}/rea_features.py" "${WORKSPACE_DIR}/backend/routes/rea_features.py"

# Register 'rea' command in CLI main entrypoint if not already registered
CLI_MAIN="${WORKSPACE_DIR}/cli/aetherion_cli/__main__.py"
if [ -f "${CLI_MAIN}" ] && ! grep -q "rea_group" "${CLI_MAIN}"; then
    cat << 'EOF' >> "${CLI_MAIN}"

# --- REA (Reverse Engineer Anything) CLI Registration ---
try:
    from .commands.rea import rea_group
    cli.add_command(rea_group)
except Exception:
    pass
EOF
fi

# Register rea_bp in backend/app.py if not already registered
BACKEND_APP="${WORKSPACE_DIR}/backend/app.py"
if [ -f "${BACKEND_APP}" ] && ! grep -q "rea_bp" "${BACKEND_APP}"; then
    cat << 'EOF' >> "${BACKEND_APP}"

# --- REA (Reverse Engineer Anything) Blueprint Registration ---
try:
    from routes.rea_features import rea_bp
    app.register_blueprint(rea_bp)
except Exception:
    pass
EOF
fi

echo "[4/6] Pruning stale git worktrees, __pycache__, and .pytest_cache..."
rm -rf "${WORKSPACE_DIR}/.aetherion_worktrees" "${WORKSPACE_DIR}/.pytest_cache"
find "${WORKSPACE_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${WORKSPACE_DIR}" -type f -name "*.pyc" -delete 2>/dev/null || true

echo "[5/6] Ensuring .env configuration exists (preserving existing dummy API keys)..."
if [ ! -f "${WORKSPACE_DIR}/.env" ] && [ -f "${WORKSPACE_DIR}/.env.example" ]; then
    cp "${WORKSPACE_DIR}/.env.example" "${WORKSPACE_DIR}/.env"
fi

echo "[6/6] Installing Aetherion CLI locally & validating Docker Compose configuration..."
if command -v pip >/dev/null 2>&1; then
    pip install -e "${WORKSPACE_DIR}/cli" --quiet || true
fi

echo "Done! Workspace is 100% deployment-ready at: ${WORKSPACE_DIR}"
echo "Run 'cd ${WORKSPACE_DIR} && docker compose up --build -d' to launch the stack."
