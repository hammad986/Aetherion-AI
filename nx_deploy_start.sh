#!/bin/bash
# nx_deploy_start.sh — Nexora Phase V Production Startup
# ═════════════════════════════════════════════════════════════════════

echo "Starting Nexora Deployment Pipeline..."
echo "──────────────────────────────────────"

# 1. Environment check
if [ ! -f ".env" ]; then
    echo "[!] ERROR: .env file missing. Please copy .env.example to .env and configure secrets."
    exit 1
fi

# 2. Virtual Env Check
if [ -z "$VIRTUAL_ENV" ]; then
    echo "[--] WARNING: Not running inside a Python virtual environment. Proceeding anyway..."
fi

# 3. Preflight Validator
echo "[--] Running preflight validator..."
python nx_startup_check.py --strict
if [ $? -ne 0 ]; then
    echo "[!] ERROR: Preflight checks failed. Deployment aborted."
    exit 1
fi

# 4. Backup existing DB just in case
if [ -f "saas_platform.db" ] || [ -f "sessions.db" ]; then
    echo "[--] Creating pre-deployment database backup..."
    python nx_backup.py --silent
fi

# 5. Start Server
echo "[OK] Deployment verified. Starting Gunicorn..."
# Adjust worker class based on environment constraints. Using gevent for SSE if available.
gunicorn -c gunicorn.conf.py web_app:app
