# SAFE_EXTRACTION_MAP.md
# Phase Z1 — Controlled Modular Extraction Roadmap
# Generated: 2026-05-15

---

## 1. LOW RISK EXTRACTION TARGETS (Phase Z1 Focus)

These routes involve minimal shared state, rely primarily on independent databases, and act as pure APIs.

### Target 1: `routes/health_routes.py`
- `/api/health` (Might be already in `routes/health.py`, need to merge or verify)
- `/api/check-ollama`
- `/api/hardware`

### Target 2: `routes/telemetry_routes.py`
- `/metrics`
- `/api/logs`
- `/api/trace`
- `/api/analytics` (if any exist)

### Target 3: `routes/diagnostics_routes.py`
- `/api/system` (4 routes)
- `/api/cluster` (7 routes)
- `/api/devops` (13 routes)
- `/api/infra` (4 routes)

### Target 4: `routes/ui_routes.py`
- `/`
- `/privacy-policy`
- `/terms-of-service`
- `/refund-policy`
- `/reset-password` (Static render)

---

## 2. RUNTIME BOOTSTRAP CONSOLIDATION

**Target:** `runtime/runtime_bootstrap.py`

Will contain dictionaries and initialization constants currently cluttering the top of `web_app.py`:
- `PROVIDERS` dict (~60 lines)
- `P5_ROUTE_RULES` dict (~20 lines)
- `MANAGED_LIMITS` dict (~15 lines)
- `PLAN_CAPABILITIES` dict (~30 lines)
- `_TEXT_EXTS` set
- Helper functions like `format_uptime()`, `format_bytes()` if not needed by active routes in `web_app.py`

**Constraint:** The actual `app = Flask(__name__)` and DB monkey-patching MUST stay in `web_app.py`.

---

## 3. EXTRACTION PROCEDURE

For each target:
1. View the exact lines in `web_app.py` for the target routes.
2. Ensure they do not reference `_sessions`, `_pending_tasks`, or complex `web_app.py` internal functions.
3. If they require authentication, ensure `middleware.guards.require_auth` can be used instead of `token_required` (or extract `token_required` if safe).
4. Create the new blueprint in `routes/`.
5. Register the blueprint in `web_app.py`.
6. Remove the old routes from `web_app.py`.
7. Run the full validation suite (`python -m py_compile web_app.py`, route uniqueness checks, etc.).
