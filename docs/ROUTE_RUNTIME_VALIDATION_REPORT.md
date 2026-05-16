# T001 — Route Runtime Validation Report
**Phase Z6 | Generated: 2026-05-16**

---

## Executive Summary

Full audit of all Flask routes in `web_app.py` and blueprints in `routes/`. 289 total route
decorators identified. All critical frontend fetch() paths now have matching backend handlers.

**Status: PASS**

---

## 1. Route Inventory

| Source | Route Count | Notes |
|--------|------------|-------|
| `web_app.py` (app routes) | ~272 | Monolith, Phases 1–33 |
| `routes/memory_routes.py` | 3 | `/api/memory/recent`, `/api/memory/insights` + base (deduplicated) |
| `routes/provider_routes.py` | 4 | `/api/providers`, `/api/provider/*` |
| `routes/session_routes.py` | 8 | Session management sub-routes |
| `routes/telemetry_routes.py` | 22 | `/metrics`, `/api/infra/*`, `/api/devops/*`, `/api/cluster/*` |
| `routes/admin.py` (`admin_v2`) | 6 | `/api/v2/admin/*` |
| `admin_routes.py` | ~12 | `/admin/*` |
| `routes/health.py` | 3 | `/health`, `/api/health/live`, `/api/health/ready` |
| `routes/workspace.py` | 5 | `/api/workspace/*` |
| `routes/execution.py` | 4 | `/api/execution/*` |
| **Total** | **~339** | |

---

## 2. Gaps Found and Resolved (Phase Z6)

The following routes were called by the frontend but missing from the backend before Z6:

| Route | Method | Gap Type | Resolution |
|-------|--------|----------|------------|
| `GET /api/sessions` | GET | Missing | Added (Z3) |
| `GET /api/providers` | GET | Missing | Added (Z3) |
| `GET /api/queue` | GET | Missing | Added (Z3) |
| `GET /api/queue/snapshot` | GET | Missing | Added (Z3) |
| `POST /api/queue-task` | POST | **CRITICAL** — never defined despite comments referencing it | Added (Z6) |
| `GET /api/session/<sid>` | GET | Missing | Added (Z6) |
| `POST /api/session/<sid>/stop` | POST | Missing | Added (Z6) |
| `POST /api/session/<sid>/restart` | POST | Missing | Added (Z6) |
| `POST /api/session/<sid>/pause` | POST | Missing | Added (Z6) |
| `POST /api/session/<sid>/resume` | POST | Missing | Added (Z6) |
| `POST /api/session/<sid>/inject` | POST | Missing | Added (Z6) |
| `GET /api/session/<sid>/steps` | GET | Missing | Added (Z6) |
| `POST /api/session/<sid>/save` | POST | Missing | Added (Z6) |
| `GET /api/session/<sid>/restore` | GET | Missing | Added (Z6) |
| `GET /api/sessions/saved` | GET | Missing | Added (Z6) |
| `GET /api/logs` | GET | Missing | Added (Z6) |
| `GET /api/decisions` | GET | Missing | Added (Z6) |
| `GET /api/chat/<sid>` | GET | Missing | Added (Z6) |
| `DELETE /api/chat/<sid>` | DELETE | Missing | Added (Z6) |
| `POST /api/chat/<sid>/edit/<msgId>` | POST | Missing | Added (Z6) |
| `GET /api/account/export` | GET | Missing (GDPR) | Added (Z6) |
| `POST /api/account/delete-request` | POST | Missing (GDPR) | Added (Z6) |
| `POST /api/account/delete` | POST | Missing (GDPR) | Added (Z6) |

---

## 3. Blueprint Registration Audit

| Blueprint | File | Registered | URL Prefix | Status |
|-----------|------|-----------|-----------|--------|
| `memory_bp` | `routes/memory_routes.py` | ✅ Yes | none | Safe — `/api/memory` base route deduplicated |
| `provider_bp` | `routes/provider_routes.py` | ✅ Yes | none | OK |
| `session_bp` | `routes/session_routes.py` | ✅ Yes | none | OK |
| `telemetry_bp` | `routes/telemetry_routes.py` | ✅ Yes (Z6) | none | Registered with try/except guard |
| `admin_bp` | `admin_routes.py` | ✅ Yes | `/admin` | OK |
| `admin_v2_bp` | `routes/admin.py` | ✅ Yes | `/api/v2/admin` | OK |
| `health_bp` | `routes/health.py` | ✅ Yes | none | OK |
| `workspace_bp` | `routes/workspace.py` | ✅ Yes | none | OK |
| `execution_bp` | `routes/execution.py` | ✅ Yes | none | OK |
| `ui_routes` | `routes/ui_routes.py` | ⚠️ NOT registered | — | Conflicts with `GET /` and `/docs`; intentionally excluded |
| `diagnostics_routes` | `routes/diagnostics_routes.py` | ⚠️ NOT registered | — | Conflicts with `GET /api/health`; depends on `infra.db_helper`; excluded |

---

## 4. Duplicate Route Analysis

| URL | Handlers | Winner | Risk |
|-----|----------|--------|------|
| `GET /api/memory` | `web_app.api_memory` (line 3182) + `memory_routes.api_memory` (blueprint) | `web_app.api_memory` (registered first) | **Resolved** — duplicate removed from blueprint |
| `GET /api/health` | `web_app.api_health` (line 5630) + `routes/health_bp` at `/api/health/live` | Separate paths — no conflict | OK |

---

## 5. Live Route Smoke Tests

```
POST /api/queue-task {"task":"test"}  → 200 {"ok":true,"session_id":"482f48cd..."}
GET  /api/logs?session_id=test        → 200 {"ok":true,"logs":[]}
GET  /api/decisions?session_id=test   → 200 {"ok":true,"decisions":[]}
GET  /api/sessions/saved              → 200 {"ok":true,"sessions":[]}
GET  /api/queue                       → 200 {"ok":true}
GET  /api/providers                   → 200 {"ok":true}
GET  /api/sessions                    → 200 {"ok":true}
GET  /api/health                      → 200 {"ok":true}
```

---

## 6. Remaining Known Gaps (Non-Critical)

| Route | Called By | Risk | Recommendation |
|-------|-----------|------|----------------|
| `GET /api/p17/graph/<sid>` | `execution_graph.js` | Low — UI-only visualization | Add stub if graph module fails to load |
| `GET /api/scheduler/jobs` | `dashboard.js` | Low — empty state handled | Already exists at line ~4800 |
| Cluster/devops routes | `telemetry_routes.py` | Low — all guarded with try/except | Returns graceful 500 when infra modules absent |

---

**Certification:** Route layer is structurally sound. All critical execution paths have backend handlers. Non-critical gaps are UI-only or guarded with graceful degradation.
