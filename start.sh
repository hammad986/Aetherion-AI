#!/bin/sh
# start.sh — Nexora production startup script
set -e
echo "[Nexora] Starting production server..."
if [ -z "$JWT_SECRET" ]; then
  echo "[WARN] JWT_SECRET not set — using default. Set it before exposing publicly."
fi
exec gunicorn -c gunicorn.conf.py web_app:app
