# gunicorn.conf.py — Production WSGI Server Configuration
# ═══════════════════════════════════════════════════════════════════
# Usage: gunicorn -c gunicorn.conf.py web_app:app
#
# Multi-worker strategy:
#   - With REDIS_URL set: workers=4 (Redis routes SSE cross-worker)
#   - Without REDIS_URL:  workers=1 (in-process SSE, session state)
# ═══════════════════════════════════════════════════════════════════

import os

# ── Binding ──────────────────────────────────────────────────────────────────
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"

# ── Worker config ─────────────────────────────────────────────────────────────
_redis_url = os.getenv("REDIS_URL", "").strip()

if _redis_url:
    # Redis available: allow multi-worker (SSE routed via pub/sub)
    workers      = int(os.getenv("GUNICORN_WORKERS", "4"))
    print(f"[Gunicorn] Redis detected — starting {workers} workers (multi-worker mode).")
else:
    # No Redis: single worker required for shared in-memory SSE state
    workers      = 1
    print("[Gunicorn] No REDIS_URL — single-worker mode (set REDIS_URL for scaling).")

threads      = int(os.getenv("GUNICORN_THREADS", "32"))
worker_class = "gthread"
timeout      = int(os.getenv("GUNICORN_TIMEOUT", "120"))
keepalive    = 5

# ── Request limits ────────────────────────────────────────────────────────────
# Prevent runaway requests from exhausting workers
max_requests           = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter    = int(os.getenv("GUNICORN_MAX_JITTER", "100"))
graceful_timeout       = 30

# ── Logging ──────────────────────────────────────────────────────────────────
loglevel          = os.getenv("GUNICORN_LOG_LEVEL", "info")
accesslog         = "-"   # stdout
errorlog          = "-"   # stderr
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s %(D)sµs'

# ── Security ─────────────────────────────────────────────────────────────────
server_name  = "aetherion"
forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1")

# ── Process title ────────────────────────────────────────────────────────────
proc_name = "aetherion-ai"

# ── Lifecycle hooks ───────────────────────────────────────────────────────────

def on_starting(server):
    server.log.info("Aetherion AI starting (workers=%d, threads=%d).", workers, threads)

def post_fork(server, worker):
    """Called in each worker after fork. Re-seed RNG to prevent key collision."""
    import os as _os
    import random
    random.seed()
    _os.environ.setdefault("WORKER_PID", str(worker.pid))
    server.log.info("[Gunicorn] Worker %d forked.", worker.pid)

def worker_exit(server, worker):
    """
    Called when a worker exits (crash or graceful shutdown).
    Triggers crash recovery to mark orphaned sessions.
    """
    server.log.warning("[Gunicorn] Worker %d exiting.", worker.pid)
    try:
        from nx_crash_recovery import on_worker_crash
        on_worker_crash(worker.pid)
    except Exception as e:
        server.log.error("[Gunicorn] Crash recovery hook failed: %s", e)
    # Stop Redis SSE subscriber for this worker
    try:
        from streaming.sse_redis import RedisSSEBridge
        RedisSSEBridge.stop()
    except Exception:
        pass

def on_exit(server):
    server.log.info("Aetherion AI shutting down.")
