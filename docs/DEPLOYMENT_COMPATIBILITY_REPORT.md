# T008 — Deployment Compatibility Report
**Phase Z6 | Generated: 2026-05-16**

---

## Executive Summary

The application is validated for Replit deployment (single-process Flask/gunicorn). VPS
deployment is supported with documented configuration. Multi-worker gunicorn requires
one configuration change (see section 4).

**Status: PASS for Replit | CONDITIONAL for multi-worker VPS**

---

## 1. Replit Deployment Configuration

### Workflow
```
Name: Start application
Command: python web_app.py
Port: 5000 (bound to 0.0.0.0)
```

### Environment Variables Required
| Variable | Purpose | Set |
|----------|---------|-----|
| `SECRET_KEY` | Flask session + JWT signing | ✅ Replit shared secret |
| `JWT_SECRET` | JWT signing (same as SECRET_KEY) | ✅ Replit shared secret |
| `PORT` | Bind port (default 5000) | Optional |
| `FLASK_DEBUG` | Debug mode (must be "0" in production) | ✅ Default "0" |
| AI provider keys | BYOK model — user provides via UI | Optional |

### Startup Sequence
1. `web_app.py` imports all modules (all optional deps guarded)
2. WAL patch applied to `sqlite3.connect`
3. All blueprints registered
4. Queue worker thread started (daemon)
5. Scheduler background thread started (daemon)
6. Flask dev server starts on `0.0.0.0:5000`

All startup steps confirmed clean in logs (2026-05-16 08:22:00).

---

## 2. Gunicorn Configuration (Replit Production Publish)

For `replit deploy`, gunicorn is used. Recommended config:

```
gunicorn web_app:app \
  --workers 1 \
  --threads 4 \
  --bind 0.0.0.0:5000 \
  --timeout 120 \
  --keep-alive 5 \
  --access-logfile - \
  --error-logfile -
```

**Why `--workers 1`:** The queue, SSE, and HITL state are in-process. Multiple workers
would each have separate queues and SSE would not reach clients connected to different
workers. Single-worker + threads is the correct model.

**Why `--timeout 120`:** Long agent runs (up to `MAX_RUNTIME` seconds) must not be
killed by gunicorn's default 30-second timeout.

---

## 3. VPS / Docker Deployment

For a self-hosted VPS:

```nginx
# nginx reverse proxy
server {
    listen 443 ssl;
    server_name nexora.example.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 120s;
        proxy_buffering off;  # Required for SSE
    }
}
```

**Critical:** `proxy_buffering off` is required for SSE (`/api/stream/<sid>` and
`/api/events`) to deliver events in real-time.

---

## 4. Multi-Worker Gunicorn (Advanced)

If scaling to `--workers N` (N > 1), the following components must be externalised:

| Component | Current | Multi-worker fix |
|-----------|---------|-----------------|
| Task queue | `pending_queue` deque | Redis list + worker |
| SSE subscriptions | `_sse_queues` dict | Redis pub/sub |
| HITL state | `_hitl_state` dict | Redis hash |
| Rate limiter counters | In-process dict | Redis + redis-py |
| Deletion tokens | `_deletion_requests` dict | SQLite table |

Until these are externalised, `--workers 1` is mandatory.

---

## 5. Static Files

All static assets (`/static/`) are served by Flask directly. For production:

```python
# Add to nginx config for better performance:
location /static/ {
    alias /path/to/nexora/static/;
    expires 7d;
    add_header Cache-Control "public, immutable";
}
```

---

## 6. Health Check Endpoints

| Endpoint | Use |
|----------|-----|
| `GET /api/health` | Full health snapshot (CPU, memory, session counts) |
| `GET /api/infra/health` | Infra layer health (DB, event bus, degraded mode) |

Both return `{"ok": true}` when healthy. Use for load balancer or uptime monitor probes.

---

## 7. Replit-Specific Notes

| Item | Status |
|------|--------|
| mTLS proxy (Replit preview) | ✅ App binds `0.0.0.0` not `127.0.0.1` |
| `allowedHosts` | N/A — Flask doesn't restrict hosts by default |
| WebSocket support | N/A — SSE over HTTP/1.1 (no WS needed) |
| Persistent storage | ✅ SQLite files in project root survive restarts |
| Secret injection | ✅ Replit env vars injected at runtime |
| Cold start time | ~2.5 seconds (observed) | Acceptable |

---

**Certification:** Application is deployment-ready for Replit single-process model.
Multi-worker deployment requires Redis externalisation of queues and state, which is
documented but not required for current scale.
