# Z61B — Rate Limit + Polling Stabilization Report

## Real Problems Found & Fixed

### FIXED — 429 Toast Fires on Background Polling
**File:** `static/js/stability.js`
**Issue:** The global fetch wrapper triggered a "Rate Limited" toast on any 429 response, including background polling endpoints like `/api/queue`, `/api/health`, `/api/metrics`, `/api/costs/totals`. These poll every 3–8 seconds and any transient 429 from the server would spam the UI with warning toasts, creating a noisy and untrustworthy experience.
**Fix:** Added `isBgPoll` regex check that suppresses 429 toasts for known background polling paths. User-triggered calls (task submission, auth, file ops) still show the toast. Added 10-second debounce so even user-triggered 429s don't stack.

### FIXED — Debounce on 429 Toast
**File:** `static/js/stability.js`  
**Issue:** Multiple rapid 429 responses could stack multiple toasts simultaneously.
**Fix:** Added `_nx429Debounce` flag — once a 429 toast is shown, no new ones appear for 10 seconds.

## Polling Inventory (Audited)

| Endpoint | Interval | Trigger | Background? |
|---|---|---|---|
| `/api/queue` | 3s | `setInterval` in `ui.js` | Yes |
| `/api/system/metrics` | 8s | `NX.metricTimer` | Yes |
| `/api/health` | ~2s | SSE keepalive | Yes |
| `/api/costs/totals` | on-demand + interval | metrics tab | Yes |
| `/api/sessions` | on-demand | session list | Yes |
| `/api/z38/evolution`, `/api/z38/patterns` | ~2s | cognition module | Yes |
| `/api/p5/routing`, `/api/p9/routing` | on-demand | plan selector | Yes |
| `/api/support/tickets` | on-demand | support tab | Yes |

All background polls confirmed non-overlapping in timing. No duplicate loops found for the same endpoint.

## Session Restore (Audited)
- `_restoreLastSession()` in `stability.js` has an in-flight guard (`_restoreInFlight`) — prevents duplicate restore calls
- Restore waits 1200ms after load to let session list populate — appropriate delay
- `NxBus` events `SESSION_CREATED` and `SESSION_RESTORED` cancel any pending restore — correct

## SSE Reconnect (Audited)
- SSE reconnect handled exclusively by `NxSSERuntime` in `nx-sse-runtime.js`
- Exponential backoff is implemented — no polling spam on disconnect
- `stability.js` explicitly defers to `NxSSERuntime` — no duplicate reconnect logic

## Startup Request Analysis
Startup fires these in the first 2 seconds:
- Health check, queue poll, sessions load, metrics load, provider load, routing load
- These are all independent and non-blocking — no rate limit risk at startup

## Remaining Gaps
- The `/api/costs/totals` endpoint logged 4346ms response time — this is slow and could cascade delays. Recommend async computation or caching.
- Polling intervals are not centrally configurable at runtime — acceptable for beta.

## Beta Polling Stability Score: 8/10
No duplicate loops. Background 429s no longer spam the UI. Startup is clean. The slow costs endpoint is the main remaining concern.
