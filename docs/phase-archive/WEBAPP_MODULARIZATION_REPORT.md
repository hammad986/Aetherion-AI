# WEBAPP_MODULARIZATION_REPORT.md
# Phase Y — Part 4b: web_app.py Modularization Report
# Generated: 2026-05-15

---

## SUMMARY

web_app.py remains at ~504KB / 11,783 lines. This is a known maintainability
risk. However, **the V2 extraction is already partially underway** (routes/,
middleware/, execution/ packages exist and are active).

This report documents what was done, what was found, and what the safe path forward is.

---

## WHAT WAS DONE IN PHASE Y

### Fix 1: Blueprint Collision Resolved

**Bug:** Both `routes/admin.py` and `admin_routes.py` exported blueprints
named `'admin'`. Flask would silently use the first registered and warn about
the second, potentially causing endpoint lookup failures.

**Fix:** `routes/admin.py` blueprint renamed from `'admin'` to `'admin_v2'`.
The `url_prefix='/api/v2/admin'` was already different — only the Flask
internal name collided.

**Impact:** Zero routing behavior change. Both blueprints are now distinct.

### Fix 2: WAL Bypass in Auth Routes

**Bug:** `api_auth_change_password()` and `api_auth_delete_account()` both did
`import sqlite3 as _sqlite3` locally then used `_sqlite3.connect()`. This
re-imported the STDLIB `sqlite3` module attribute directly, bypassing the
`infra.db_helper.patch_sqlite_globally()` monkey-patch.

**Fix:** Removed the `import sqlite3 as _sqlite3` local aliases. All three
connection points now use the module-level `sqlite3.connect` which IS patched.

**Impact:** `saas_platform.db` and `sessions.db` connections in auth routes now
correctly use WAL mode + 5s busy_timeout. Eliminates potential auth-path
`database is locked` errors under concurrent login/logout.

---

## EXTRACTION STATUS

| Module | Status | Routes | Notes |
|--------|--------|--------|-------|
| `routes/health.py` | ✅ ACTIVE | 1 | Health check |
| `routes/workspace.py` | ✅ ACTIVE | ~8 | V2 workspace |
| `routes/execution.py` | ✅ ACTIVE | ~6 | V2 execution |
| `routes/admin.py` | ✅ ACTIVE (fixed) | 8 | V2 admin ops |
| `admin_routes.py` | ✅ ACTIVE | ~30 | Full admin panel |
| `middleware/observability.py` | ✅ ACTIVE | — | Request hooks |
| `middleware/guards.py` | ✅ ACTIVE | — | Auth decorators |
| `middleware/api_response.py` | ✅ ACTIVE | — | Response format |
| `execution/` (package) | ✅ ACTIVE | — | All execution subsystems |

**Main web_app.py remaining:** ~280 routes covering auth, sessions, SSE streaming,
files, HITL, billing, telemetry, support, and platform routes.

---

## SAFE NEXT STEPS (Phase Z)

Priority order for safe extraction:

1. **`routes/ui_routes.py`** — Static page routes (`/`, `/privacy-policy`, etc.)
   Risk: LOW. Pure render_template, no shared state.

2. **`routes/file_routes.py`** — File browser/upload/download
   Risk: LOW. Self-contained helpers (`_walk_session_files`, etc.)

3. **`routes/billing_routes.py`** — Billing/payment routes
   Risk: LOW. Delegates entirely to `payments.py`.

4. **`routes/support_routes.py`** — Support ticket routes
   Risk: LOW. Delegates to `support.py`.

5. **`routes/auth_routes.py`** — Auth routes (35 routes)
   Risk: MEDIUM. Requires careful import of shared auth decorators.

6. **`routes/session_routes.py`** — Session/task management
   Risk: HIGH. Deep shared state dependencies.

7. **`routes/stream_routes.py`** — SSE streaming endpoint
   Risk: VERY HIGH. Leave this last.

---

## BLOCKER: Shared State

The primary blocker for safe extraction is shared module-level state:

```python
# Currently in web_app.py at module level
_sessions = {}          # Active session cache
_active_threads = {}    # Running agent threads
_pending_tasks = {}     # Queued tasks
_db_lock = threading.Lock()
_broadcast_to_session = ...
```

These CANNOT be split without either:
a) Creating a shared state module (risk: new abstraction layer)
b) Passing them as parameters to blueprint factory functions

Both approaches are valid but require careful planning in Phase Z.

---

## RISK REGISTER

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|-----------|
| Circular import during extraction | HIGH | HIGH | Use factory functions, deferred imports |
| Route order change breaks auth | MEDIUM | CRITICAL | Register blueprints AFTER security setup |
| Shared state fragmentation | HIGH | HIGH | Extract state to dedicated module first |
| SSE stream losing context | LOW | CRITICAL | Keep SSE route in web_app.py until last |
| Double route registration | LOW | HIGH | Fixed (admin_v2 rename) |

---

## CONCLUSION

Phase Y has achieved:
- ✅ Two critical bugs fixed in web_app.py
- ✅ Blueprint collision eliminated
- ✅ WAL hardening fully applied to all auth DB paths
- ✅ Full route inventory and categorization complete
- ✅ Safe extraction path documented

The monolith risk is **reduced** (V2 extraction active, bugs fixed) but not
eliminated. Full modularization is a Phase Z project with no shortcuts.
