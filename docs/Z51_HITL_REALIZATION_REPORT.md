# Z51 HITL Realization Report

## Phase Z51B — Operational Human-in-the-Loop

**Date:** 2026-05-17  
**Status:** COMPLETE  

---

## Problem Statement

Before Z51, the HITL system had:
- A working backend (`execution/hitl.py`, `nx_hitl_response.py`, `web_app.py`)
- A working frontend panel (`nx-hitl-panel.js`, `nx-hitl-bridge.js`)
- BUT: the main polling endpoint `GET /api/hitl/pending` returned **404** (unimplemented)
- The approval queue panel in the Live tab (`#z33ApprovalsRow`) was always empty
- The HITL strip in the composer showed but had no visual connection to real queue state

---

## Changes Made

### Backend: New `/api/hitl/pending` Endpoint

Added `GET /api/hitl/pending` to `web_app.py`:
- Reads pending events from `global_hitl_tracker._pending` dict
- Also checks session-scoped pause state via `_hitl_get(sid)` for Redis-backed cross-worker pauses
- Accepts optional `?sid=` query param to filter by session
- Returns `{ ok, pending: [...], count }` — same shape expected by the frontend

### Frontend: Approval Queue Panel (`#z51HitlQueuePanel`)

Injected into `#z33ApprovalsRow` in the Live tab:
- Polls `GET /api/session/<sid>/hitl/pending` (session-scoped, `nx_hitl_response.py`)
- Falls back to `GET /api/hitl/pending?sid=<sid>` (global, new endpoint)
- Renders each pending item with: reason text, event ID, timeout countdown, note input, Approve + Reject buttons
- `z51HitlDecide(eventId, decision, noteIdx)` sends to session-scoped respond endpoint first, global approve endpoint as fallback
- Emits `nx:hitl:resolved` on `NxBus` so existing `nx-hitl-bridge.js` listeners can react

### Audit Trail

Each decision is stored in `_z51HitlAudit` (module-private array) and rendered in a scrollable audit section below the queue:
- Status badge (approved/rejected/timeout), truncated reason, timestamp
- Persists for the lifetime of the page session

### Auto-Refresh Triggers

HITL queue is refreshed when:
1. `nxSetGlobalStatus('running')` fires (3s delay for agent to emit HITL event)
2. `nxSetGlobalStatus('idle')` fires (catch any remaining items)
3. Every 8s via `_z51HitlPollTimer` while `NX.lastStatus === 'running'`

### HITL Strip Wiring

The Pause/Resume buttons in `#nxHitlStrip` (composer area) are now wired to real endpoints:
- Pause → `POST /api/session/<sid>/pause`
- Resume → `POST /api/session/<sid>/resume`
- Both update the `#hitlStatusText` span and toggle button visibility

---

## HITL Flow

```
Agent encounters decision point
  → calls global_hitl_tracker.request_approval()
  → agent thread blocks (waits up to timeout_sec)
  
Frontend polls /api/session/<sid>/hitl/pending
  → z51HitlQueuePanel renders item
  → Operator enters optional note, clicks Approve/Reject

z51HitlDecide() fires
  → POST /api/session/<sid>/hitl/respond
  → global_hitl_tracker.provide_approval() unblocks thread
  → Agent continues with feedback injected into memory
  → Audit trail entry added
  → NxBus emits nx:hitl:resolved
```

---

## Remaining Weaknesses

1. `_z51HitlAudit` is page-session memory only — not persisted to `hitl_audit` table. The backend `nx_hitl_response.py` has the audit table; Z51 should read from `GET /api/session/<sid>/hitl/audit` on load. Implemented as a best-effort load on each refresh.
2. Timeout countdown uses client-side `Date.now()` subtraction — can drift if clock is skewed. Adequate for beta.
3. The HITL panel shows only when `NX.lastStatus === 'running'`. If the agent pauses immediately before the frontend detects running state, the item may briefly not show. Addressed by the 3s delayed first poll.

---

## Beta Readiness Score: 7.5/10

HITL is operational for the common case (approval/rejection of running agent). Timeout escalation and cross-session audit persistence are beta-grade (page-level) rather than production-grade (DB-level). Sufficient for beta validation.
