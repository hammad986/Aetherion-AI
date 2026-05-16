# Z11 — Future Runtime Compatibility Notes
**Aetherion AI · Phase Z11 · Environment Bootstrap Strategy**
Status: DESIGN ONLY — No implementation. Future V2/V3 target.

---

## Overview

This document establishes the runtime compatibility strategy for Aetherion AI
across heterogeneous deployment environments: Replit, VPS, Docker, cloud
platforms (Railway, Render, Fly.io), and future container-native deployments.

---

## 1. Replit-Style Autonomous Adaptation

### Detection
```python
IS_REPLIT = bool(os.getenv("REPLIT_DEV_DOMAIN"))
```

### Behavioral Overrides
| Setting | Replit Value | Default Value |
|---|---|---|
| Bind host | `0.0.0.0` | `127.0.0.1` |
| Port | `int(os.getenv("PORT", 5000))` | `5000` |
| Browser auto-open | `False` (NO_BROWSER=1) | `True` |
| Debug mode | `False` | `True` |
| Worker count | `4` (Gunicorn) | `1` |
| DB path prefix | `/home/runner/workspace/` | `./` |
| Log format | JSON structured | Human readable |

### Replit-Specific Signals
- `REPLIT_DEV_DOMAIN` — the public proxy URL. Always use this for SSE callbacks.
- `REPL_ID` — unique repl identifier, use as tenant namespace.
- `DATABASE_URL` — PostgreSQL when Replit DB is provisioned.
- `REPLIT_DOMAINS` — allowed CORS origins.

### Replit SSE Compatibility
Replit proxies all HTTP through a CDN that may buffer SSE. Mitigations:
- Set `X-Accel-Buffering: no` response header on all SSE endpoints.
- Use `text/event-stream` content type (correct).
- Flush after each event with `\n\n` terminator (correct).
- Consider Redis pub/sub bridge for multi-worker SSE fan-out.

---

## 2. Environment Bootstrap Strategy

### Bootstrap Priority Chain
```
1. Explicit environment variables (highest priority)
2. .env file (python-dotenv)
3. Platform-injected secrets (Replit Secrets, Railway Vars)
4. Dockerfile ENV directives
5. Inferred defaults (lowest priority)
```

### Bootstrap Validation (on startup)
- Verify `SECRET_KEY` is set and ≥ 32 chars.
- Verify `JWT_SECRET` is set and ≥ 32 chars.
- Verify database is reachable (SQLite path writable; PostgreSQL ping).
- Verify Redis is reachable if `REDIS_URL` is set.
- Log warnings (not errors) for missing optional API keys.
- Abort startup only if required secrets (`SECRET_KEY`, `JWT_SECRET`) are absent.

---

## 3. Dependency Inference

### Runtime Package Audit (future `bootstrap/dependency_auditor.py`)
On startup (or on demand via `/api/bootstrap/audit`):
1. Read `requirements.txt` to get declared dependencies.
2. Compare against `pip list --format=json` (installed packages).
3. Compute: `missing`, `outdated`, `extra` packages.
4. Expose result via `/api/health` extended response.
5. On missing critical package: log warning + emit `bootstrap.dependency_missing` SSE event.

### Version Compatibility Matrix (future `bootstrap/compatibility.json`)
```json
{
  "python": {"min": "3.10", "tested": ["3.10", "3.11", "3.12"]},
  "flask": {"min": "3.0.0", "max": "3.x"},
  "tiktoken": {"min": "0.7.0"},
  "redis": {"min": "5.0.0", "optional": true}
}
```

---

## 4. Deployment Portability

### Portable Configuration Checklist
- [ ] No hardcoded ports (always `os.getenv("PORT", 5000)`).
- [ ] No hardcoded hostnames (always relative URLs in frontend).
- [ ] Database paths relative to workspace root (not hardcoded `/home/runner/...`).
- [ ] All secrets via environment variables (no `.env` committed to git).
- [ ] Health check endpoint at `/api/health` returning `{"status":"ok"}`.
- [ ] Graceful shutdown on SIGTERM (30 s drain window).
- [ ] Structured JSON logging when `LOG_FORMAT=json`.

### Portable Startup Wrapper (future `bootstrap/start.py`)
```python
# Auto-selects: gunicorn (prod) vs flask dev server (dev)
if os.getenv("NODE_ENV") == "production" or os.getenv("GUNICORN"):
    exec_gunicorn()
else:
    exec_flask_dev()
```

---

## 5. Runtime Migration Logic

### V1 → V2 Migration Contract
When upgrading Aetherion AI to V2, the following must be preserved:
- `sessions.db` schema (use `ALTER TABLE` migrations, never DROP).
- `saas_platform.db` schema (billing data is permanent).
- Session workspace directories (`workspace/` per session).
- User credentials in `users` table.

### Migration Steps (future `bootstrap/migrate.py`)
1. Detect current schema version from `schema_version` table.
2. Apply pending migration scripts from `bootstrap/migrations/` (ordered by version).
3. Verify post-migration row counts match pre-migration counts.
4. Write new schema version to `schema_version` table.
5. Emit `bootstrap.migration_complete` event.

### Rollback Strategy
- Every migration script has a corresponding `rollback_VXXX.sql`.
- On migration failure: automatic rollback, then HITL escalation.
- Migration state persisted to `bootstrap_migrations` table (not just a file).

---

## 6. Container Awareness

### Docker Detection
```python
IS_DOCKER = os.path.exists("/.dockerenv")
```

### Container-Specific Overrides
| Setting | Container Value |
|---|---|
| Logging | Stdout only (no log files) |
| PID file | Not written |
| Gunicorn workers | `2 * CPU_COUNT + 1` |
| Health check | `/api/health` on internal port |
| Signal handling | SIGTERM → 30 s graceful shutdown |

### Container Resource Limits (future `bootstrap/resource_limits.py`)
- Read cgroup memory limit from `/sys/fs/cgroup/memory/memory.limit_in_bytes`.
- Set `max_memory_mb` = 80% of cgroup limit.
- Read CPU quota from `/sys/fs/cgroup/cpu/cpu.cfs_quota_us`.
- Dynamically set Gunicorn `--workers` based on available CPU.

### Docker Compose Integration (future `docker-compose.prod.yml`)
```yaml
services:
  aetherion:
    image: aetherion:latest
    environment:
      - PORT=5000
      - REDIS_URL=redis://redis:6379
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/api/health"]
      interval: 30s
      retries: 3
  redis:
    image: redis:7-alpine
```

---

## 7. Future Cloud Execution Adaptation

### Target Platforms

| Platform | Adaptation | Status |
|---|---|---|
| Replit Autoscale | Multi-worker Gunicorn + Redis SSE | V1 Ready |
| Railway | `PORT` env, `Procfile` | V1 Ready |
| Render | `PORT` env, health check route | V1 Ready |
| Fly.io | `fly.toml` config, multi-region | V2 Target |
| AWS Lambda | WSGI adapter (`mangum`) | V3 Target |
| Google Cloud Run | Containerized, stateless | V2 Target |
| Kubernetes | Helm chart, PVC for workspace | V3 Target |

### Stateless Adaptation (V3 Requirement)
Current V1 stores session state in local SQLite. V3 cloud-native requires:
- All session state → PostgreSQL (already supported via `DATABASE_URL`).
- All workspace files → Object storage (S3/GCS/R2).
- All SSE fan-out → Redis pub/sub (already implemented in `redis_layer.py`).
- Zero local disk assumptions.

### Multi-Region Considerations (V3)
- Session affinity: route all requests for a `session_id` to same region via sticky routing.
- Cross-region replication: PostgreSQL logical replication for read replicas.
- Redis: single primary with read replicas; cross-region latency acceptable for coordination.

---

## Implementation Readiness Summary

| Capability | V1 Status | V2 Target | V3 Target |
|---|---|---|---|
| Replit adaptation | ✓ Complete | — | — |
| Docker deployment | ✓ Complete (Dockerfile present) | — | — |
| Multi-worker Redis | ✓ Complete (redis_layer.py) | — | — |
| Dependency audit | Partial (startup log only) | Full auditor | — |
| Container resource limits | ✗ Not implemented | ✓ Target | — |
| Schema migrations | ✗ Not implemented | ✓ Target | — |
| Object storage | ✗ Not implemented | — | ✓ Target |
| Multi-region | ✗ Not implemented | — | ✓ Target |
| Visual canvas | ✗ Not implemented | — | ✓ Target |
| Drag execution planning | ✗ Not implemented | — | ✓ Target |
