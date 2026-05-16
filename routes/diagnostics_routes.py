import os
import json
import urllib.request
import urllib.error
from flask import Blueprint, jsonify

from infra.db_helper import get_connection

diagnostics_bp = Blueprint('diagnostics_routes', __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSIONS_DB = os.path.join(BASE_DIR, "sessions.db")

def _http_get(url, timeout=5):
    req = urllib.request.Request(url)
    return urllib.request.urlopen(req, timeout=timeout)

@diagnostics_bp.route("/api/health", methods=["GET"])
def api_health():
    """Return a real-time system health snapshot."""
    import platform

    try:
        import psutil
        cpu_pct  = psutil.cpu_percent(interval=None)
        mem      = psutil.virtual_memory()
        mem_used = mem.percent
        mem_gb   = round(mem.used / 1024**3, 2)
    except ImportError:
        cpu_pct = mem_used = mem_gb = None

    metrics_path = os.path.join(BASE_DIR, "data", "metrics.json")
    metrics = {}
    try:
        with open(metrics_path, "r", encoding="utf-8") as fh:
            metrics = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    with get_connection(SESSIONS_DB) as c:
        total_sessions = c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        running = c.execute(
            "SELECT COUNT(*) FROM sessions WHERE status='running'"
        ).fetchone()[0]
        success = c.execute(
            "SELECT COUNT(*) FROM sessions WHERE success=1"
        ).fetchone()[0]

    success_rate = round((success / total_sessions * 100), 1) if total_sessions else 0

    return jsonify({
        "ok": True,
        "system": {
            "platform": platform.system(),
            "python":   platform.python_version(),
            "cpu_pct":  cpu_pct,
            "mem_used_pct": mem_used,
            "mem_used_gb":  mem_gb,
        },
        "sessions": {
            "total":   total_sessions,
            "running": running,
            "success_rate_pct": success_rate,
        },
        "observability": metrics,
        "safety": {
            "max_iterations": int(os.environ.get("MAX_ITER", 3)),
            "max_commands":   int(os.environ.get("MAX_CMD", 10)),
            "max_runtime_s":  int(os.environ.get("MAX_RUNTIME", 60)),
        },
    })

@diagnostics_bp.route("/api/check-ollama")
def api_check_ollama():
    """Ping the local Ollama daemon. Optionally returns the running model list."""
    from model_router import get_ollama_base_url
    base = get_ollama_base_url()
    try:
        with _http_get(base + "/api/tags", timeout=3) as r:
            body = r.read().decode("utf-8", "replace")[:4000]
            try:
                data = json.loads(body)
                models = [m.get("name") for m in (data.get("models") or [])]
            except Exception:
                models = []
            return jsonify({"ok": True, "host": base, "models": models})
    except urllib.error.URLError as e:
        return jsonify({"ok": False, "host": base, "error": f"Not reachable: {e.reason}"}), 200
    except Exception as e:
        return jsonify({"ok": False, "host": base, "error": str(e)}), 200

@diagnostics_bp.route("/api/hardware/status")
def api_hardware_status():
    """Returns Phase 36 hardware monitoring data."""
    try:
        from hardware_monitor import get_hardware_monitor
        return jsonify({"ok": True, "data": get_hardware_monitor().status()})
    except ImportError:
        return jsonify({"ok": False, "error": "Hardware monitor not available"}), 503
