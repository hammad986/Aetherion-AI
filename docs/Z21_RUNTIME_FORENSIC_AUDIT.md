# Z21 — Runtime Performance & Memory Discipline Forensic Audit

**Date:** 2026-05-16  
**Scope:** `static/js/runtime.js`, `static/js/boot.js`, `static/js/ui.js`, `static/js/nx-runtime-hygiene.js` (new)  
**Classification:** Pre/post-remediation baseline

---

## 1. Executive Summary

The Z21 audit examined four risk areas in the browser runtime: DOM unbounded growth, detached DOM node accumulation from toasts, SSE connection hygiene, and timer/interval proliferation across long sessions. All critical findings have been addressed by the new `nx-runtime-hygiene.js` module.

---

## 2. Log DOM Growth Analysis

### Finding Z21-001 — Log area DOM ceiling exists but trim path has a gap

**File:** `static/js/runtime.js` lines 1317–1346  
**Risk:** `MAX_LOG_LINES = 1500` is defined and the DOM trim runs inside `requestAnimationFrame`. However, if the RAF callback is delayed (e.g. tab backgrounded), `pendingLogRows` could flush unboundedly before trimming.

**Mitigation applied:**  
`nx-runtime-hygiene.js` adds a **secondary 5-second polling ceiling** enforcer (`enforceLogCeiling`) that is independent of the RAF flush path. This provides defence-in-depth: the primary ceiling in runtime.js remains the authoritative path; the hygiene module provides a backup enforcer with a visual notice showing how many lines were trimmed.

**Constants:**
```
LOG_DOM_CEILING = 1500   (matches runtime.js MAX_LOG_LINES)
```

---

## 3. Toast / Notification Node Lifecycle

### Finding Z21-002 — `_toast()` creates body-appended nodes with no eviction guarantee

**File:** `static/js/runtime.js` (internal `_toast` helper)  
**Risk:** Each toast call appends a `<div>` to `document.body`. In long sessions with frequent errors, these nodes accumulate without any maximum-alive enforcement.

**Mitigation applied:**  
`patchToastSystem()` in `nx-runtime-hygiene.js` installs a `MutationObserver` on `document.body` that prunes excess toast nodes (> `TOAST_MAX_ALIVE = 5`) with a 200 ms fade-out transition. Additionally, a `setInterval(TOAST_TTL_MS = 6000)` sweep runs as a second eviction pass.

---

## 4. SSE Connection Tracking

### Finding Z21-003 — No stale SSE detection in the application layer

**File:** `static/js/runtime.js` (SSE connection setup)  
**Risk:** If the SSE stream silently stops delivering messages (e.g. proxy timeout, server-side keepalive gap), the UI shows no indication and the user cannot tell whether the agent is running or frozen.

**Mitigation applied:**  
`installSSEHealthPatch()` monkey-patches `window.EventSource` to:
- Track `_state.sseLastMessage` timestamp on every `message` event
- Set `body.nx-sse-reconnecting` class when `error` fires
- Clear it on `open`
- A 10-second interval checks staleness: if `Date.now() - sseLastMessage > SSE_STALE_MS (45s)`, the reconnecting class is applied, triggering the CSS status indicator

---

## 5. FPS & Heap Monitoring

### Finding Z21-004 — No runtime performance visibility in production

**Risk:** There is no mechanism to detect rendering jank, heap pressure, or DOM node explosion in a running session without DevTools.

**Mitigation applied:**  
A lightweight `requestAnimationFrame` FPS counter samples frame rate per second. A debug HUD (`#nxPerfHud`) is created in the DOM but hidden behind `body.nx-debug-perf`. Operators can enable it by calling `nxPerfHUD()` in the console.

**HUD metrics:**
- FPS (colour-coded: green ≥40, yellow 20–39, red <20)
- Total DOM node count (warn >8,000, critical >14,000)
- Log row count
- Lines trimmed by hygiene module
- SSE staleness in seconds
- JS Heap used / total (Chrome only, via `performance.memory`)

---

## 6. Timer Hygiene

### Finding Z21-005 — `setInterval` proliferation across long sessions

**Risk:** Multiple `setInterval` calls are registered in `runtime.js`, `ui.js`, and subordinate modules. In long sessions, abandoned timers (e.g. from unmounted components or session resets) continue executing against stale DOM references.

**Mitigation applied:**  
`nx-runtime-hygiene.js` exposes `window._nxClearAllTimers()` for emergency timer eviction during debugging sessions. A `_timerIds` `Set` is maintained for tracking. The `_nxPerfState` object is exposed on `window` for external inspection.

---

## 7. Memory Thresholds

| Metric | Warn threshold | Critical threshold | Action |
|--------|---------------|-------------------|--------|
| DOM nodes | 8,000 | 14,000 | HUD warning colour |
| Log area rows | 1,500 | — | Auto-trim with notice |
| Toast nodes alive | 5 | — | Auto-prune with fade |
| SSE staleness | 30 s | 45 s | `body.nx-sse-reconnecting` |
| JS Heap usage | 60% of limit | 85% of limit | HUD warning colour |
| FPS | < 40 | < 20 | HUD warning colour |

---

## 8. Files Created / Modified

| File | Change |
|------|--------|
| `static/js/nx-runtime-hygiene.js` | New — Z21 discipline module |
| `static/css/nx-z19z20z21.css` | `.nx-perf-hud`, `.nx-log-trimmed-notice`, `.nx-sse-reconnecting` |
| `templates/index.html` | Script include added at bottom of body |

---

## 9. Long-Session Validation Protocol

To validate Z21 compliance in a long session:

1. Open the app and run `nxPerfHUD()` in browser console
2. Start a task that generates heavy log output
3. Observe that log rows stay at or below 1,500 and the "trimmed" counter increments
4. Simulate SSE disconnect by blocking the `/stream` endpoint
5. Verify `body.nx-sse-reconnecting` class appears and the statusbar shows the reconnecting indicator
6. Check DOM nodes HUD value stays below 8,000 during normal operation
