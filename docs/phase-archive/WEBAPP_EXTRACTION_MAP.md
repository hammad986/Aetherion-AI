# WEBAPP_EXTRACTION_MAP.md
# Phase Y — Part 4: web_app.py Modular Extraction Map
# Generated: 2026-05-15

---

## CURRENT STATE ASSESSMENT

| Metric | Value |
|--------|-------|
| File size | 504,780 bytes (≈ 493KB) |
| Total lines | 11,783 |
| Total `@app.route()` definitions | 328 |
| Registered blueprints | health_bp, workspace_bp, execution_bp, admin_v2 (routes/), admin_bp (admin_routes.py) |
| Already-extracted modules | 9 (routes/, middleware/, execution/) |

---

## EXTRACTION ALREADY COMPLETE (V2 Extraction Phase 36)

These have already been cleanly extracted:

| Module | Blueprint | Routes |
|--------|-----------|--------|
| `routes/health.py` | `health_bp` | `/api/health` |
| `routes/workspace.py` | `workspace_bp` | `/api/v2/workspace/*` |
| `routes/execution.py` | `execution_bp` | `/api/v2/execution/*` |
| `routes/admin.py` | `admin_v2` | `/api/v2/admin/*` |
| `admin_routes.py` | `admin_bp` | `/admin/*` |
| `middleware/observability.py` | — | Request observability |
| `middleware/guards.py` | — | Auth guards |
| `middleware/api_response.py` | — | API response format |

---

## ROUTE CATEGORIZATION (Remaining in web_app.py)

### Category 1 — Auth Routes (~35 routes)
Prefix: `/api/auth/`

| Route Pattern | Handler |
|--------------|---------|
| `/api/auth/register` | POST — user registration |
| `/api/auth/login` | POST — JWT login |
| `/api/auth/logout` | POST — JWT logout |
| `/api/auth/refresh` | POST — token refresh |
| `/api/auth/me` | GET — current user |
| `/api/auth/google` | GET — OAuth initiate |
| `/api/auth/google/callback` | GET — OAuth callback |
| `/api/auth/change-password` | POST — password change |
| `/api/auth/delete-account` | POST — account delete |
| `/api/auth/sessions` | GET — list sessions |
| `/api/auth/logout-all` | POST — revoke all |
| `/api/auth/verify-email` | GET — email verify |
| `/api/auth/resend-verify` | POST |
| `/api/auth/forgot-password` | POST |
| `/api/auth/reset-password` | POST |
| `/api/auth/account` | GET — account info |
| `/api/auth/oauth-providers` | GET |
| `/api/auth/provider/remove` | POST |
| ... (BYOK key management) | ~8 routes |

### Category 2 — Session/Task Routes (~80 routes)
Prefix: `/api/session/`, `/api/task/`, `/api/`

| Route Pattern | Notes |
|--------------|-------|
| `/api/session` | POST — create session |
| `/api/session/<sid>` | GET — session state |
| `/api/session/<sid>/cancel` | POST |
| `/api/session/<sid>/retry` | POST |
| `/api/stream/<sid>` | GET — SSE stream endpoint |
| `/api/task/queue` | POST — queue task |
| `/api/task/status/<tid>` | GET |
| `/api/task/cancel/<tid>` | POST |
| `/api/sessions` | GET — list sessions |
| `/api/files/<sid>` | GET — file tree |
| `/api/file/<sid>` | GET — file content |
| `/api/upload/<sid>` | POST |
| `/api/download/<sid>` | GET — zip download |
| `/api/preview/<sid>/<path>` | GET — file preview |
| `/api/create-folder/<sid>` | POST |
| `/api/delete/<sid>/<path>` | DELETE |
| `/api/move/<sid>` | POST |

### Category 3 — HITL Routes (~10 routes)
Registered via `nx_hitl_response.register_hitl_routes()`

| Route Pattern | Notes |
|--------------|-------|
| `/api/hitl/approve` | POST — approve action |
| `/api/hitl/reject` | POST — reject action |
| `/api/hitl/status/<event_id>` | GET |
| `/api/hitl/pending` | GET |

### Category 4 — Telemetry/Analytics (~20 routes)
Prefix: `/api/analytics/`, `/api/telemetry/`

### Category 5 — Payment/Billing (~15 routes)
Prefix: `/api/billing/`, `/api/payment/`

### Category 6 — Admin/Platform (~30 routes)
Prefix: `/api/admin/`, `/api/platform/`
(In addition to `admin_routes.py`)

### Category 7 — Support (~10 routes)
Prefix: `/api/support/`

### Category 8 — Misc/UI (~30 routes)
`/`, `/privacy-policy`, `/terms-of-service`, `/api/settings/*`, etc.

---

## RECOMMENDED TARGET STRUCTURE

```
routes/
    auth_routes.py          # ~35 auth routes (READY TO EXTRACT)
    session_routes.py       # ~40 session/task routes
    stream_routes.py        # SSE stream endpoint (SENSITIVE)
    file_routes.py          # File browser/upload/download
    hitl_routes.py          # HITL (partially in nx_hitl_response.py)
    billing_routes.py       # Payments/billing
    telemetry_routes.py     # Analytics/telemetry
    support_routes.py       # Support
    ui_routes.py            # Static page routes

services/
    startup_services.py     # _init_db(), _register_hitl_routes_safe()
    deployment_services.py  # deploy-related helpers

runtime/
    runtime_bootstrap.py    # PROVIDERS, P5_ROUTE_RULES, MANAGED_LIMITS, PLAN_CAPABILITIES
```

---

## EXTRACTION COMPLEXITY ASSESSMENT

> [!WARNING]
> web_app.py is deeply interconnected. Safe extraction requires INCREMENTAL steps
> with syntax validation and route audit after EACH extraction.

| Category | Complexity | Risk | Blocker |
|----------|-----------|------|---------|
| `auth_routes.py` | MEDIUM | Medium | Needs `token_required`, `g`, `auth_system` imports |
| `stream_routes.py` | HIGH | High | SSE generator — complex context and lifecycle |
| `session_routes.py` | HIGH | High | Many shared state variables (`_sessions`, etc.) |
| `file_routes.py` | LOW | Low | Mostly self-contained helpers |
| `billing_routes.py` | LOW | Low | Delegates to `payments.py` |
| `telemetry_routes.py` | LOW | Low | Delegates to infra modules |
| `support_routes.py` | LOW | Low | Delegates to `support.py` |
| `ui_routes.py` | LOW | Low | Simple render_template calls |

---

## CRITICAL CONSTRAINTS FOR EXTRACTION

1. **Shared state variables** — many routes reference module-level dicts like `_sessions`,
   `_active_threads`, `_pending_tasks`. These MUST remain in one module or be refactored
   into a proper store (not within this phase's scope).

2. **Import order** — `token_required` decorator, `_conn()`, `_broadcast_to_session` are
   defined mid-file and referenced throughout. Any extracted blueprint must import these
   from a shared location.

3. **Blueprint name uniqueness** — the `admin_bp` collision has been fixed (routes/admin.py
   now exports `admin_v2`). Do not reintroduce name collisions.

4. **SSE endpoint** — `/api/stream/<sid>` MUST NOT be extracted into a blueprint unless
   the generator function and all its dependencies are fully co-located. SSE streams
   with Flask require careful context handling.

5. **Circular import risk** — extracting routes into blueprints that import from `web_app.py`
   (which imports the blueprint) creates circular imports. Use deferred imports or
   pass dependencies as arguments.

---

## CURRENT PHASE STATUS

This phase has:
- ✅ Audited all 328 routes
- ✅ Categorized routes into logical groups
- ✅ Identified extraction complexity
- ✅ Fixed duplicate blueprint collision (immediate bug)
- ✅ Documented full extraction map

**Actual route extraction is recommended as a SEPARATE phase** (Phase Z)
to avoid destabilizing the current working runtime during Phase Y consolidation.

The architectural risk of extracting 11,783 lines of tangled route handlers
into blueprints without comprehensive regression tests outweighs the
maintainability benefit at this stage.

**Recommendation: Phase Y COMPLETE for web_app.py. Phase Z = incremental extraction.**
