# MODULARIZATION_PROGRESS_REPORT.md
# Phase Z1
# Generated: 2026-05-15

## 1. Current Progress
- **Initial Lines (`web_app.py`):** 11,783
- **Current Lines (`web_app.py`):** 11,366
- **Total Lines Extracted:** ~417 lines
- **Total Routes Extracted:** 38 routes

## 2. Extraction Blockers (Honest Assessment)
The target for Z1 was a 20-35% size reduction of `web_app.py`. We achieved a ~3.5% reduction. **Why did we stop here?**

The mandate strictly enforced: **"NO runtime logic rewrites. NO route behavior changes. NO endpoint redesign. STOP extraction immediately if... endpoint regressions appear."**

Upon performing the `WEBAPP_DEPENDENCY_TRACE.md`, we discovered that over 80% of `web_app.py`'s bulk is concentrated in `api/session`, `api/memory`, and `api/providers`. These endpoints deeply mutate and query the global shared state variables (`_sessions`, `_active_threads`, `_pending_tasks`, `PROVIDERS`).

To extract them to separate blueprints safely, one of two things must happen:
1. **Option A:** They import `_sessions` from `web_app.py` (Creates a circular import because `web_app.py` must import the blueprint).
2. **Option B:** `_sessions` is extracted into a `runtime/state.py` file, and both `web_app.py` and the blueprints import it. (This changes startup ordering and risks desynchronizing the `RedisSSEBridge` injection and `nx_crash_recovery.py` which monkey-patches into the state).

Because Option B risks destabilizing the SSE and crash recovery lifecycles (which violate the Z1 hard limits), we halted extraction after capturing all truly decoupled, stateless domains (UI, Metrics, Telemetry, DevOps, Infra, Cluster).

## 3. Honest Remaining Risks
- `web_app.py` remains a monolith for core AI session operations.
- Debugging session creation logic still requires scrolling through an 11k line file.

## 4. Recommended Future Extraction Order (Phase Z2)
To reach the 35% extraction goal safely, a dedicated "State Extraction" phase is required:
1. **Extract `_sessions` and `_active_threads`** into a pure, logic-less `core/shared_state.py`.
2. **Move `_conn()` and `_init_db()`** into `infra/db_helper.py` fully.
3. Update `nx_crash_recovery.py` to target `core.shared_state`.
4. *Only then*, extract `routes/session_routes.py` and `routes/memory_routes.py`.
