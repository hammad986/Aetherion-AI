# Z29 Governance Engine Report

**Phase:** Z29B — Governance + Approval Engine  
**Date:** 2026-05-16  
**Status:** OPERATIONAL

---

## Overview

The Z29 Governance Engine (`runtime/governance_engine.py`) provides a structured, operator-facing approval workflow for runtime operations. It is entirely separate from `governance_layer.py` (which governs code patch validation) — this module governs live execution operations.

---

## Protected Operations Registry

| Operation | Severity | Requires Approval |
|-----------|----------|-------------------|
| `file_delete` | GOVERNANCE_REQUIRED | Yes |
| `file_delete_bulk` | CRITICAL | Yes |
| `deploy_production` | CRITICAL | Yes |
| `credential_modify` | CRITICAL | Yes |
| `credential_delete` | CRITICAL | Yes |
| `mass_file_write` | HIGH_RISK | Yes |
| `external_execution` | HIGH_RISK | Yes |
| `escalation_external` | GOVERNANCE_REQUIRED | Yes |
| `db_drop` | CRITICAL | Yes |
| `db_truncate` | GOVERNANCE_REQUIRED | Yes |
| `env_var_modify` | GOVERNANCE_REQUIRED | Yes |
| `package_install` | WARNING | No (auto-approved by default) |
| `network_outbound` | WARNING | No |
| `shell_command` | WARNING | No |
| `hitl_force_approve` | HIGH_RISK | Yes |
| `mission_cancel` | HIGH_RISK | Yes |
| `override_provider` | WARNING | No |
| `override_model` | WARNING | No |
| `override_confidence` | HIGH_RISK | Yes |
| `mission_recovery` | HIGH_RISK | Yes |

---

## Severity Level System

```
INFO (0)
  │  Auto-approved, logged only
WARNING (1)
  │  Auto-approved by default, visible in history
HIGH_RISK (2)
  │  Queued for operator approval
GOVERNANCE_REQUIRED (3)
  │  Requires explicit operator sign-off
CRITICAL (4)
  └─ Blocks execution until approved or rejected
```

The `auto_approve_below` parameter to `submit_approval_request()` defaults to `WARNING` — meaning anything WARNING or below is auto-approved. Callers can set this to `INFO` to require human review for all operations, or `CRITICAL` to auto-approve everything except critical ops.

---

## Approval Request Lifecycle

```
submit_approval_request(sid, op_type, summary, context)
  │
  ├─ Severity < threshold → status = AUTO, persist, return immediately
  │
  └─ Severity ≥ threshold → status = PENDING
       │
       ├─ Added to _pending dict (in-memory)
       ├─ threading.Event created in _waiters dict
       ├─ Persisted to governance_engine.db
       ├─ SSE: agent.governance_request emitted
       │
       ├─ [Operator approves] → resolve_request(id, "approve")
       │     └─ status = APPROVED, Event.set(), persist
       │
       ├─ [Operator rejects] → resolve_request(id, "reject")
       │     └─ status = REJECTED, Event.set(), persist
       │
       └─ [Timeout 5 min] → expire_old_requests()
             └─ status = EXPIRED, Event.set(), persist
```

---

## Thread Safety

- All in-memory state (`_pending`, `_waiters`) is protected by `_queue_lock` (threading.Lock)
- SQLite access protected by `_db_lock` (threading.Lock) with WAL journal mode
- `wait_for_approval()` blocks on `threading.Event.wait()` — safe for worker threads (does NOT block gunicorn request threads — only agent daemon threads)

---

## Persistence Schema

```sql
CREATE TABLE approval_requests (
    request_id   TEXT PRIMARY KEY,
    sid          TEXT,
    op_type      TEXT,
    severity     TEXT,
    summary      TEXT,
    context_json TEXT,  -- JSON blob of additional context
    status       TEXT,  -- pending/approved/rejected/expired/auto
    created_at   REAL,
    resolved_at  REAL,
    resolved_by  TEXT,
    resolution   TEXT
)
```

Database: `data/governance_engine.db` (SQLite with WAL mode)

---

## API Contract

```
GET  /api/z29/governance/queue
  → { pending_count, critical_count, pending_items, ts }

GET  /api/z29/governance/history?sid=&limit=
  → { history: [ApprovalRequest, ...] }

POST /api/z29/governance/approve/{request_id}
  body: { resolved_by, resolution_note }
  → { ok, request_id, status }

POST /api/z29/governance/reject/{request_id}
  body: { resolved_by, resolution_note }
  → { ok, request_id, status }
```

---

## Remaining Governance Risks

1. **Long-running approval waits:** If an agent step waits for approval and the operator is away, the `wait_for_approval()` call times out after 120 seconds — the operation is then treated as rejected. This is conservative-safe but may cause unnecessary task failures during extended operator absence.

2. **Cross-worker approval:** In multi-worker Gunicorn deployments without Redis, the in-memory `_pending` dict is per-process. An approval submitted to Worker A may not be visible from Worker B. Resolution: the SQLite DB is shared across workers; polling the DB directly handles this case.

3. **Governance bypass:** Operations that don't call `submit_approval_request()` bypass the governance gate. The registry covers known tool patterns; new tools added without wiring will not be governed automatically. Recommendation: add governance checks to `tools.py` for destructive operations.
