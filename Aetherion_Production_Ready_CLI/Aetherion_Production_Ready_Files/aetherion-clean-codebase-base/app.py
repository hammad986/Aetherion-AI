"""
Aetherion Backend Application Entry Point.
==========================================
Authoritative root-level WSGI entry point and application re-exporter.

Exports the Flask application instance (`app`) and application factory (`create_app`)
from `backend.app`. This ensures that:
1. `from app import app` resolves to the identical production Flask instance whether
   imported from `backend/` or from the repository root (e.g. under `python -m pytest`).
2. WSGI runners (Gunicorn, uWSGI) running `app:app` from root start the production app.
3. Direct execution (`python app.py`) starts the backend server locally.
"""
import sys
from pathlib import Path

# Ensure root and backend directories are in sys.path
_ROOT = Path(__file__).resolve().parent
_BACKEND_DIR = _ROOT / "backend"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Import canonical backend.app module
import backend.app as _backend_app

# Canonicalize sys.modules so both 'app' and 'backend.app' point to the exact same module
sys.modules["app"] = _backend_app
try:
    import backend
    backend.app = _backend_app
except Exception:
    pass

# Populate root module namespace so any caller holding an existing reference gets full fidelity
for _k in dir(_backend_app):
    if not _k.startswith("__"):
        globals()[_k] = getattr(_backend_app, _k)

app = _backend_app.app
create_app = _backend_app.create_app

__all__ = ["app", "create_app"]

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", "5000"))
    explicit_host = os.environ.get("HOST", "").strip()
    if explicit_host:
        host = explicit_host
    else:
        is_container = os.environ.get("RUNNER_CONTAINER") == "1"
        raw_env = os.environ.get("ENVIRONMENT", "").strip().lower()
        host = "0.0.0.0" if (is_container or raw_env in ("production", "prod")) else "127.0.0.1"
    app.run(host=host, port=port)
