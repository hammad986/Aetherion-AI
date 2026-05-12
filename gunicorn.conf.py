# gunicorn.conf.py — Production WSGI Server Configuration
# ═══════════════════════════════════════════════════════════════════
# Usage: gunicorn -c gunicorn.conf.py web_app:app
# ═══════════════════════════════════════════════════════════════════

import os

# ── Binding ──────────────────────────────────────────────────────────────────
bind    = f"0.0.0.0:{os.getenv('PORT', '5000')}"

# ── Worker config ─────────────────────────────────────────────────────────────
# IMPORTANT: This app uses shared in-memory state (session queue, scheduler,
# live-log SSE, background workers). Multiple *processes* would each get their
# own copy of that state, causing sessions queued in process A to be invisible
# in process B. Keep workers=1 and scale via threads instead.
#
# worker_class="gthread": stdlib-only threaded worker; WSGI-compatible; safe
# with Flask, SSE, threading.Event, queue.Queue, and sqlite3.  No new deps.
# Raises SSE concurrency ceiling from 8 to 32 sessions.
# Rollback: set worker_class="sync" and GUNICORN_THREADS=8.
workers      = 1
threads      = int(os.getenv("GUNICORN_THREADS", "32"))
worker_class = "gthread"
timeout      = int(os.getenv("GUNICORN_TIMEOUT", "120"))
keepalive    = 5

# ── Logging ──────────────────────────────────────────────────────────────────
loglevel          = os.getenv("GUNICORN_LOG_LEVEL", "info")
accesslog         = "-"   # stdout
errorlog          = "-"   # stderr
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sµs'

# ── Security ─────────────────────────────────────────────────────────────────
# Do not expose the server name in response headers
server_name = "nexora"

# ── Process title ────────────────────────────────────────────────────────────
proc_name = "nexora-ai"

# ── Lifecycle hooks ───────────────────────────────────────────────────────────
def on_starting(server):
    server.log.info("Nexora AI Platform starting up (production mode).")

def on_exit(server):
    server.log.info("Nexora AI Platform shutting down.")
