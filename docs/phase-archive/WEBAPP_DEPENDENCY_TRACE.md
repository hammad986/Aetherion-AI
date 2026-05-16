# WEBAPP_DEPENDENCY_TRACE.md
# Phase Z1 — Pre-Extraction Forensics
# Generated: 2026-05-15

---

## 1. IMPORTS & MIDDLEWARE DEPENDENCIES

**Core Middleware & Decorators (Used widely across routes):**
- `@token_required`: Depends on `g`, `request`, `auth_system`. Sets `g.user_id`, `g.plan`.
- `@check_rate_limit(type)`: Depends on `security.py`.
- `@idempotent()`: Depends on `idempotency.py`.

**Shared State Dependencies (The true blockers for full extraction):**
- `_sessions` (dict): Cache of active session objects.
- `_active_threads` (dict): Map of session_id to running threading.Thread.
- `_pending_tasks` (dict): Queue representation.
- `_db_lock` (Lock): Legacy global lock for some DB operations.
- `_broadcast_to_session` (function): The dynamic SSE bridge function.
- `_scheduler` (TaskScheduler): Background task executor.
- `PROVIDERS`, `P5_ROUTE_RULES`, `MANAGED_LIMITS` (dicts): Runtime configs.

---

## 2. STARTUP & INITIALIZATION ORDER

1. **DB Monkey-Patch:** `infra.db_helper.patch_sqlite_globally()` runs immediately.
2. **Flask App Creation:** `app = Flask(__name__)`
3. **Phase 36 Extraction Loading:** `health_bp`, `workspace_bp`, `execution_bp`, `admin_v2` registered.
4. **Security Loading:** `security.py` limiters and keys loaded. `app.secret_key` set.
5. **Phase 17 Admin Loading:** `admin_bp` (from `admin_routes.py`) registered.
6. **SSE Redis Bridge Init:** `RedisSSEBridge.init()` runs. Overwrites `_broadcast_to_session`.
7. **Crash Recovery Daemon:** `nx_crash_recovery.py` imported (starts background daemon thread).
8. **Logging Setup:** Standard `app.log` configuration.
9. **Provider Registry:** Defines `PROVIDERS` dict.
10. **Global Services Check:** `_SUPPORT_AVAILABLE`, `_IDEMPOTENCY_AVAILABLE`, etc. checked via try/except imports.
11. **Main Route Definitions:** 300+ `@app.route` decorators executed.
12. **HITL Route Injection:** `nx_hitl_response.register_hitl_routes(app, ...)` called at the end.
13. **Background Services Start:** `_scheduler.start()`, `start_hardware_monitor()`.

---

## 3. SAFE EXTRACTION ZONES

To safely extract routes into blueprints, the routes must:
1. Not mutate the shared state dictionaries (`_sessions`, `_active_threads`).
2. Not rely on variables that are defined after the app creation block (unless imported properly).
3. Be purely structural (returning static data, querying independent DBs, or calling pure service functions).

### LOW RISK (Safe to Extract Now)
* **Health / Diagnostics:** `/api/health`, `/metrics`, `/api/check-ollama`, `/api/hardware`
* **Static UI Pages:** `/`, `/privacy-policy`, `/terms-of-service`, `/refund-policy`
* **Support:** `/api/support/*` (pure delegation to `support.py`)
* **Billing:** `/api/billing/*`, `/api/payments/*`, `/api/invoice/*` (pure delegation to `payments.py`)

### MEDIUM RISK (Requires careful decorator importing)
* **Auth:** `/api/auth/*` (requires moving the `token_required` decorator or importing it correctly).
* **Telemetry / DevOps / Infra:** `/api/cluster`, `/api/devops`, `/api/infra`, `/api/system` (often read-only, but sometimes access internal structures).
* **Goals / Chains:** `/api/goals`, `/api/chains` (complex logic, but mostly self-contained or DB-backed).

### HIGH RISK (Do NOT Extract in Phase Z1)
* **Core Session Management:** `/api/session`, `/api/sessions`, `/api/queue` (heavy mutation of `_sessions`, `_pending_tasks`).
* **Streaming Endpoint:** `/api/stream/<sid>` (Extremely sensitive Flask generator context).
* **Terminal / PTY:** `/api/terminal`, `/api/pty` (Requires complex WebSocket/SSE emulation).

---

## 4. EXTRACTION STRATEGY

1. Create target blueprints in `routes/`.
2. Ensure they import necessary utilities (`success_response`, `error_response`, `require_auth` from `middleware.guards` instead of local `token_required` if possible).
3. Move the low-risk routes.
4. Register the new blueprints in the "Phase 36: Observability, Middlewares, and Blueprints" block in `web_app.py` (lines 489-504).
