# Aetherion Platform — Production Readiness & Release Contract

**Document Version:** 1.0.0 (Phase 12 Trust Kernel)  
**Classification:** Authoritative Systems Contract  
**Target Runtime:** Verified Coding Agent Kernel  

---

## 1. Supported Deployment Modes

Aetherion operates under four mutually exclusive, explicitly identified environment modes defined in `core/environment.py`:

| Mode Identifier | Target Environment | Security & Dependency Posture |
|---|---|---|
| `production` | Production clusters, public-facing API gateways | Strict fail-closed boundary: managed PostgreSQL mandatory, Redis cluster mandatory, high-entropy independent secrets mandatory, container/microVM sandbox mandatory, dev-auth bypass strictly prohibited. |
| `staging` | Pre-production validation and staging clusters | Strict fail-closed boundary: mirrors production infrastructure requirements; fails fast on absent or degraded dependencies. |
| `CI-test` | Automated test runners and CI pipelines | Fast in-memory mock adapters; test databases and ephemeral caches allowed; external LLM calls mocked. |
| `local-development` | Developer workstations | Permissive fallbacks enabled: local SQLite (`saas_platform.db`), in-process deque for SSE coordination, host subprocess runner with security warnings. |

> [!IMPORTANT]
> **Explicit Environment Identity Invariant**: Unrecognized or misspelled environment values (e.g. `AETHERION_ENV=prduction`) are classified as `UNKNOWN` and **fail closed** immediately during boot, preventing accidental fallback to insecure local-development defaults.

---

## 2. Environment Variables & Secret Configuration

The authoritative configuration is read from process environment variables (or `.env` via `python-dotenv`).

### Mandatory Production Environment Variables

| Variable | Description | Security Constraints |
|---|---|---|
| `AETHERION_ENV` | Explicit environment identity | Must be `production` or `staging`. |
| `JWT_SECRET` | Cryptographic secret for signing JWT access and refresh tokens | Must be at least 32 characters of high-entropy random data (e.g. `openssl rand -hex 32`). Must not match known placeholders (`secret`, `changeme`, `testing_secret_key`). |
| `SECRET_KEY` | Flask session cookie and CSRF signing secret | Must be at least 32 characters, cryptographically independent from `JWT_SECRET` (`JWT_SECRET != SECRET_KEY`). |
| `DATABASE_URL` | PostgreSQL connection string | Must use `postgresql://` or `postgres://` scheme. SQLite connection strings are rejected in production. |
| `REDIS_URL` | Redis instance connection string | Required for multi-worker process synchronization, cross-process task queueing, and Server-Sent Event (SSE) broadcasting. |
| `WORKSPACE_DIR` | Absolute path to workspace root | Must be a dedicated, writable directory owned by the non-root process user. |
| `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` | LLM provider credentials | At least one valid AI provider key must be configured for autonomous coding execution. |

### Optional & Override Variables

| Variable | Default (Prod) | Default (Dev) | Description |
|---|---|---|---|
| `REQUIRE_SANDBOX` | `true` | `false` | When `true`, Docker daemon reachability is mandatory. When set to `false`, runs in host-degraded execution mode. |
| `ALLOW_DEV_AUTH` | `0` (enforced) | `0` | Development auth bypass (`?dev=1`). **Setting `ALLOW_DEV_AUTH=1` in `production` or `staging` causes fatal startup abort.** |
| `PORT` | `5000` | `5000` | HTTP listen port. |
| `HOST` | `0.0.0.0` | `0.0.0.0` | HTTP bind address. |
| `WORKER_PID` | (unset) | (unset) | Set by Gunicorn in `post_fork()` to signal worker processes to skip redundant live database connection storms. |

---

## 3. Preflight & Startup Safety Gate

Startup validation is enforced at container/application entry by `core/startup_validator.py` and `core/preflight.py`:

```
                       Process Launch
                             ↓
              core/environment.get_environment_mode()
                             ↓
                 Explicit Mode Recognized?
               ├── NO  → Fail Closed (sys.exit(1))
               └── YES ↓
                 core/preflight.run_preflight()
                             ↓
           Evaluate 9 Canonical Preflight Checks:
           1. config.environment (Identity)
           2. security.jwt_secret (Entropy >= 32 chars)
           3. security.secret_key (Entropy >= 32 chars)
           4. security.secrets_independence (JWT != SECRET)
           5. security.dev_auth_guard (ALLOW_DEV_AUTH == 0)
           6. database.readiness (Postgres reachability & schema)
           7. redis.coordination (Redis ping response)
           8. ai.providers (Active provider key present)
           9. filesystem.workspace (Read-after-write test)
          10. sandbox.docker (Docker daemon reachable)
                             ↓
            Any Critical/Required Check Failed?
               ├── YES (in prod/staging) → Print BLOCK items → sys.exit(1)
               └── NO  (or local-dev)    → Emit Warnings    → Continue Boot
```

### Zero-Secret-Leakage Protection

All preflight checks, logs, diagnostics, and error responses pass through `core.preflight.audit_no_secret_leakage()` and `core.preflight.sanitize_dsn()`:
- Database and Redis passwords in URIs are masked (`postgresql://user:***@host/db`).
- Raw token keys and secrets are never reflected in check messages or JSON payloads.
- Secret lengths and entropy are reported without disclosing key bytes.

---

## 4. Kubernetes Probe & Readiness Semantics

Aetherion strictly separates process liveness, application health, and task execution readiness across dedicated HTTP endpoints:

### `/livez` — Process Liveness Probe
- **Target:** Kubernetes `livenessProbe`.
- **Behavior:** Purely in-process probe returning process ID (`pid`) and uptime (`uptime_seconds`).
- **Dependency Isolation:** Completely independent of external dependencies. A temporary PostgreSQL outage or Redis blip **will never** cause Kubernetes to kill and restart the container fleet.
- **Response:** HTTP 200 `{"status": "ok", "pid": 1234, "uptime_seconds": 45.2}`.

### `/readyz` — Task Execution Readiness Probe
- **Target:** Kubernetes `readinessProbe` and load balancer traffic routing.
- **Behavior:** Executes `core.preflight.run_preflight(probe_live=True)` to assess whether the node has the dependencies required to safely execute autonomous tasks.
- **Fail-Closed Contract:**
  - **Ready (HTTP 200):** All required dependencies are online and verified. The node accepts tasks.
  - **Not Ready (HTTP 503):** One or more required dependencies (database, Redis, secrets, workspace) are degraded. Traffic is stopped immediately to prevent partial task execution.
- **Schema:**
  ```json
  {
    "status": "ready",
    "ready": true,
    "environment": "production",
    "checks": {
      "database": {"ok": true, "required": true, "detail": "..."},
      "environment": {"ok": true, "required": true, "detail": "..."},
      "security.jwt_secret": {"ok": true, "required": true, "detail": "..."},
      "redis.coordination": {"ok": true, "required": true, "detail": "..."}
    },
    "timestamp": "2026-09-24T20:30:00Z",
    "summary": {"pass": 9, "warn": 0, "fail": 0, "critical_fails": 0}
  }
  ```

### `/api/health` — Application Telemetry & Health Probe
- **Target:** CLI status checks, dashboards, and system observability.
- **Behavior:** Exposes runner isolation class (`container` vs `process`), system CPU/memory metrics, and service status.

---

## 5. Container Sandbox & Isolation Contract

Autonomous coding agents execute arbitrary shell commands, code compilation, and tool dispatches. Aetherion isolates execution using a defense-in-depth model:

1. **Production Sandbox:** Requires a running Docker daemon. Untrusted code, plugins, and command executions run in ephemeral, unprivileged Linux containers (`user != root`, dropped capabilities, no access to `/var/run/docker.sock`, and network isolation).
2. **Host-Degraded Mode (`REQUIRE_SANDBOX=false`):** When operating in local developer environments or environments where Docker-in-Docker is unavailable, setting `REQUIRE_SANDBOX=false` permits degraded host-process execution guarded by strict filesystem boundary confinement (`WorkspaceBoundary`).

---

## 6. Release Verification & Operational Procedures

### CLI Doctor (`aetherion doctor`)
Operates locally and in CI/CD pipelines to verify end-to-end operational health:
- **Interactive Terminal:** `aetherion doctor` renders a live table of CLI storage, tokens, server connectivity, and task readiness.
- **Automation / CI Mode:** `aetherion doctor --json` produces structured machine-readable diagnostics.
- **Exit Code Contract:**
  - `0`: All critical preflight checks passed. Safe to promote or route traffic.
  - `1`: One or more critical diagnostics failed. Build/release pipeline must abort.

### Standalone Preflight Hook (`scripts/production_preflight.py`)
Used by deployment containers and rollout smoke tests:
- **Config Only:** `python scripts/production_preflight.py --config` (validates secrets, environment, and file permissions without running server).
- **Runtime Probes:** `python scripts/production_preflight.py --runtime --base-url http://localhost:5000` (probes `/livez`, `/readyz`, `/startupz`, `/metrics`).
- **Complete Gate:** `python scripts/production_preflight.py --all --json` (combines config and runtime gates with machine-readable JSON).
