# Z62D — Toast System Audit Report

## System Architecture (Before Z62)

### Three Parallel Toast Systems (Pre-existing Technical Debt)
1. **`nxToast(type, message, title)`** in `stability.js` — primary, uses `#nx-toasts` container
2. **`toast(message)`** in `runtime.js` — legacy, uses `#toast` single element
3. **`ToastGov`** in `nx-z52.js` — governance layer that intercepts both

### Governance Already In Place
- `nx-runtime-hygiene.js` caps simultaneous toasts at 5 (`TOAST_MAX_ALIVE`)
- `nx-z52.js` ToastGov routes both `toast()` and `nxToast()` through a single UI
- SSE reconnect storm detection (>5 reconnects/60s = silent mode)

## Fixes Applied in Z62

### 1. Message-Level Toast Deduplication — NEW
**File:** `static/js/stability.js`
**Fix:** Added `_nxToastSeen` Map at the IIFE level. Before creating any toast, `nxToast()` checks if the same `type|message` combination was shown in the last 6 seconds. If yes, silently drops it.
- Prevents stacked "Rate Limited" toasts from the fetch wrapper
- Prevents duplicate "Connection Error" toasts during brief network blips
- Prevents repeated "Session restored" or "Snapshot saved" messages within rapid windows
- Map pruned to prevent memory growth (capped at 40 entries with expiry cleanup)

### 2. 429 Background Polling Suppression — from Z61B
**File:** `static/js/stability.js`
**Fix (carried forward):** Background polling paths are excluded from the 429 toast. `_nx429Debounce` prevents stacking.

## Remaining Noise Sources (Post-Fix)

### `activity.js` Snapshot Toasts
`activity.js` calls `toast()` on every snapshot save/restore. These are very frequent during active task execution. The 6-second deduplication window in `nxToast` will suppress duplicates, but `activity.js` uses the legacy `toast()` function which routes through `ToastGov` — so the deduplication applies.

### `runtime.js` Legacy `toast()` Usage
~58 call sites for `toast()`. These go through `ToastGov` which maps to `nxToast`. Deduplication will suppress repeated messages from rapid state changes.

## Toast Inventory by Type

| Source | Frequency | Type | After Z62 |
|---|---|---|---|
| auth failure | On demand | error | ✅ Appropriate |
| 429 rate limit (user-triggered) | On demand | warning | ✅ Debounced |
| 429 rate limit (background poll) | Suppressed | — | ✅ Silent |
| 5xx server error | On demand | error | ✅ Appropriate |
| session restored | On restore | success | ✅ Deduplicated |
| snapshot saved | Frequent | info | ✅ Deduplicated (6s window) |
| SSE reconnect storm | Storm only | warning | ✅ Storm-gated |
| provider not configured | 45s delay | warning | ✅ One-shot |
| network error | On demand | error | ✅ Deduplicated |

## Remaining Gaps
- Legacy `toast()` in `runtime.js` is still separate from `nxToast` at the definition level — governance bridges them but this is technical debt
- `activity.js` could be silenced further by making snapshot toasts `console.debug` only — left for future

## Beta Toast Score: 8.5/10
Deduplication eliminates stacking. Background polling is silent. Storm detection prevents SSE noise. The system is now calm during idle operation.
