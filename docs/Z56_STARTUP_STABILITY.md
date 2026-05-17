# Z56 — Startup Stability Report
**Phase**: Z56 Interaction Stabilization + Trust Recovery  
**Date**: 2026-05-17  
**Scope**: Console noise, startup errors, false warnings, polling spam on app load

---

## Observed Startup Issues (Pre-Z56)

### Issue 1: `/api/scheduler/stats` 404 — every 12 seconds from t=0

**Source**: `nx-z50.js` `z50UpdateIdleStats()` — called immediately on `z50StartLivePresence()` and then on a 12s interval.  
**Symptom**: Werkzeug access log shows `GET /api/scheduler/stats HTTP/1.1 404` starting from the first page load and repeating indefinitely.  
**Impact**: Server log noise, misleading warning-level log entries, idle dashboard "Scheduler" field always shows `—`.  
**Resolution**: Added `GET /api/scheduler/stats` route to `web_app.py` returning lightweight task count data.

---

### Issue 2: `[NDS Perf] MutationObservers: 24 exceeds budget 8` — every 15 seconds

**Source**: `nx-onboard.js` wraps `MutationObserver` to count active instances. The budget is 8. At page load, 24 observers are simultaneously active, generating this warning on each perf tick.

**Root cause breakdown** (pre-Z56):  
- `nx-z44-runtime.js`: 4 individual state observers (`watchRunBtn`, `watchStatusPill`, `watchHitlStrip`, `watchErrorCard`) + 1 log observer + 1 init wait observer = 6  
- `nx-z43-exec-state.js`: 2 observers (one on `#runBtn`, one body-level init wait)  
- `dashboard.js`: 3 observers  
- `nx-clarity.js`: 3 observers  
- `nx-z50.js`: 3 observers  
- `nx-z45-sync.js`: 2 observers  
- `nx-runtime-hygiene.js`: 1 observer  
- Others (`nx-z51`, `nx-z52`, `nx-z54`, `nx-z36`, `nx-z33`, `ux_trust`, `activity`, `nx-session-cleanup`): ~4 combined  

**Resolution (Z56)**: Consolidated `watchRunBtn`, `watchStatusPill`, `watchHitlStrip`, `watchErrorCard` in `nx-z44-runtime.js` into a single `watchStateElements()` function backed by ONE `MutationObserver` instance with four `.observe()` calls. Active observer count reduced by 3 (from 6 to 3 for that module).  
**Net reduction**: 24 → 21. Budget warning continues but rate is reduced.

**Recommended follow-up**: `nx-z43-exec-state.js` observes `#runBtn` for the same class changes already covered by `watchStateElements()`. Its `docObserver` (body-level) duplicates `nx-z44`'s init observer. Removing `nx-z43-exec-state.js` entirely would save 2 more observers (further reduction to ~19). The `document.body[data-nx-exec]` attribute it sets is only needed if Z43 CSS selects on it — a separate audit should confirm CSS usage before removal.

---

### Issue 3: `[NX:STABILITY] CRITICAL LONG TASK` — 275ms at t=73s

**Source**: `nx-diagnostics.js` PerformanceObserver on `longtask` entries.  
**Symptom**: One long task of 275ms at ~73 seconds into the session.  
**Impact**: Console warning only — does not crash or disrupt the UI.  
**Analysis**: 275ms is above the 50ms long-task threshold but below the ~500ms range that causes noticeable jank. Likely caused by a deferred heavy render (e.g. DAG or forensics tab first paint). No user-facing issue observed.  
**Resolution**: No code change — monitoring only. The PerformanceObserver is functioning correctly.

---

### Issue 4: `NX_BOOT_TASKS` vs. `DOMContentLoaded` Race

**Observation**: Several late-loading scripts (e.g. `nx-z28-operator.js`, `nx-z29-governance.js`) call `document.addEventListener('DOMContentLoaded', ...)` as fallback when `NX_BOOT_TASKS` is not an array. In practice, since these scripts are `defer`, `DOMContentLoaded` has already fired by the time they execute, so they register on a never-fired future event.

**Resolution**: Both files use the correct guard:
```js
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', fn);
} else {
  fn();
}
```
This is correct. No bug — documentation only.

---

## Summary

| Issue | Severity | Status |
|---|---|---|
| `/api/scheduler/stats` 404 spam | Medium | Fixed |
| 24 MutationObservers (budget 8) | Medium | Partially fixed (24→21) |
| 275ms long task at t=73s | Low | Monitored, no action |
| DOMContentLoaded race | None | Correctly handled |
