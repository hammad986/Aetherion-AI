# RUNTIME ROUTE DEPENDENCY MAP
# Phase Z3 Modularization
# Generated: 2026-05-15

## 1. Domain Classification & Extraction Risk

### A. Provider Routes (`/api/providers`)
**Risk Level:** SAFE
**Routes:**
- `GET /api/providers`
- `GET /api/providers/status`
**Dependencies:**
- `PROVIDERS` dict (from `web_app.py` or configuration)
- Requires minimal state.
**Conclusion:** Perfect candidate for safe extraction to `routes/provider_routes.py`.

### B. Memory Routes (`/api/memory`)
**Risk Level:** SAFE
**Routes:**
- `GET /api/memory`
- `GET /api/memory/recent`
- `GET /api/memory/insights`
**Dependencies:**
- Direct SQLite queries to `memory.db` and JSON file reads.
- Does not mutate `runtime.state`.
**Conclusion:** Safe for extraction to `routes/memory_routes.py`.

### C. Session & Queue Routes (`/api/session`, `/api/queue`, `/api/chat`, `/api/logs`)
**Risk Level:** MEDIUM (State-Coupled)
**Routes:**
- `GET /api/sessions`, `GET /api/session/<sid>`
- `DELETE /api/session/<sid>`
- `POST /api/session/<sid>/stop`, `POST /api/session/<sid>/restart`
- `GET /api/session/<sid>/stream` (SSE)
- HITL Routes: `pause`, `resume`, `inject`, `hitl-state`
- `GET /api/logs`, `GET /api/decisions`, `/api/chat/*`
**Dependencies:**
- **State Module:** Heavily depends on `runtime.state` (`running`, `pending_queue`, `queue_lock`, `_hitl_lock`, etc.)
- **SSE:** `/stream` manages the EventSource lifecycle.
- **Database:** Relies on `_conn()` or `db_helper.get_connection()`.
**Conclusion:** Now that Phase Z2 decoupled the state into `runtime/state.py`, these routes can be safely extracted to `routes/session_routes.py`, provided we import the state correctly.

## 2. Mandatory Safeguards for Extraction

1. **State Independence:** The extracted blueprints MUST import their required locks and queues directly from `runtime.state`.
2. **SSE Behavior:** The `/stream` route logic must remain identical. We will NOT move the `RedisSSEBridge` initialization or `sse_redis.py`, only the endpoint that yields the stream.
3. **Helper Encapsulation:** Any database helpers (e.g., `db_session`, `db_logs`, `db_chat_get`) used exclusively by these routes should ideally move with them or be imported from a shared `infra` module.