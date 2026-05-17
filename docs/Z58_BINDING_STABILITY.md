# Z58_BINDING_STABILITY.md
Phase Z58F — Hydration + Binding Stability Report
Date: 2026-05-17

## Objective
Identify and resolve all interaction binding races, duplicate listener issues,
stale initializations, and hydration timing problems.

---

## Binding Architecture Overview

The Aetherion JS stack loads in phases via `defer` script tags in index.html.
The load order is approximately:

```
boot.js (defer)
  → nx-bus.js
  → nx-shim.js
  → nx-z45.js ... nx-z50.js ... nx-z51.js ... nx-z52.js
  → nx-z54.js → nx-z55.js → nx-z57.js → nx-z58.js
```

All phases use `(function(){'use strict';...})()` IIFE wrappers. Each registers
on `DOMContentLoaded` or uses `setTimeout(fn, N)` deferral.

---

## Binding Issues Found

### 1. Cookie Dismiss Double-Handler Race
**Problem:** Two handlers competed for the same dismiss button:
- `nx-z50.js` used `dismissBtn.onclick = ...` (overrides HTML onclick)
- `templates/index.html` had `onclick="document.getElementById('nx-cookie-banner').style.display='none'"`
- `nx-z51.js` added a third `addEventListener('click', ...)` listener

The inline HTML onclick fires first (it is the default handler before z50 loads).
If z50 loads after the button is rendered but before the user clicks, `dismissBtn.onclick`
is overwritten by z50 — but z50 set it to `z50DismissCookieBanner(false)` (no persistence).
If the user clicks before z50 loads, the inline handler fires — no persistence.

**Fix:** HTML now has `onclick="nxAcceptCookies()"` for both buttons. `z50` sets
dismiss to `z50DismissCookieBanner(true)`. `z51` covers all buttons. `z58` adds
capture-phase backup. All four paths now converge to the same outcome.

### 2. `nxTogglePanel` Double-Fires on Fast Click
**Problem:** Rapid clicks on navrail icons could fire `nxTogglePanel` twice — once
from the navrail onclick, once from a delegated listener in z50. This caused a
panel to open and immediately close.
**Fix:** Z58 wraps `nxTogglePanel` with a 120ms debounce guard.

### 3. Run Button — Double Submit During Execution
**Problem:** While `nx-running` state was active, rapid clicks on the run button
could queue a second task or trigger a `stopSession` immediately followed by a
new run, causing an API race.
**Fix:** Z58 adds a capture-phase listener that blocks run button clicks within
800ms if `body.classList.contains('nx-running')`.

### 4. Panel Header `z57UpgradePanelHeaders` Timing
**Problem:** Panels are rendered lazily (content injected on first open via z50
`contentEl.dataset.z50loaded` guard). If z57 runs before the panel is opened,
there is no header element to upgrade.
**Fix (Z57):** `z57UpgradePanelHeaders()` is re-run on each `nxTogglePanel` call
via `requestAnimationFrame`. Already resolved in Z57; confirmed still working in Z58.

### 5. Panel Close Buttons — Missing Fallback
**Problem:** All panel close buttons call `window.nxClosePanels?.()`. If `nxClosePanels`
is not defined (e.g., z50 failed to load), close buttons silently do nothing.
**Fix:** Z58 adds a delegated `click` listener for `.nx-close-btn` that falls back
to hiding the closest `.nx-panel` or `[id^="nxPanel-"]` ancestor directly.

### 6. Late Binding of `nxSetTask` / `nxNewSession`
**Problem:** The idle hero chips call `nxSetTask(...)` which may not be defined if
z54 or the composer module hasn't loaded yet. The chips render in the initial HTML
before any JS runs.
**Fix (Z57):** z57.js defines polyfills for `nxSetTask` and `nxNewSession` early in
its boot sequence. Z58 re-registers these as a second guarantee.

---

## Event Listener Deduplication

The z50 module runs `_cleanDuplicateListeners()` on boot (lines 617+). This catches
any event-delegated listeners that were added twice. Z58 does not add another
deduplication scan — the z50 scan is sufficient for structural listeners.

Z58's own listeners use `{ once: true }` where appropriate (cookie buttons) and
guard flags (`dataset.z58guard`, `dataset.z58`) to prevent re-registration.

---

## Binding Stability Score

| Issue | Severity | Status |
|---|---|---|
| Cookie dismiss race | HIGH | **FIXED** |
| `nxTogglePanel` double-fire | MEDIUM | **FIXED** (120ms debounce) |
| Run button double-submit | MEDIUM | **FIXED** (800ms guard) |
| Panel header late-binding | MEDIUM | Fixed in Z57, confirmed Z58 |
| Panel close fallback | LOW | **FIXED** |
| `nxSetTask` late-binding | LOW | Fixed in Z57, confirmed Z58 |
| `nxClosePanels` undefined | LOW | **FIXED** (fallback handler) |

### Remaining Risks

1. **SSE re-subscription** — When the SSE connection drops and reconnects, some
   event listeners in Z31–Z38 may re-register. The z52 reconnect storm guard
   catches extreme cases (5 reconnects in 10s) but individual duplicate SSE events
   are not deduplicated at the binding level.

2. **Monaco editor tab switch** — Switching away from the Code tab while Monaco is
   loading can leave a half-initialized editor. Switching back does not always re-trigger
   the load. No fix applied in Z58 — deferred.

3. **xterm.js focus after panel open** — Opening the Files or History panel while
   the Terminal tab is active can steal focus from the xterm instance. Terminal typing
   stops working until the user clicks back. Deferred to Z59.

---

## Reliability Score — Binding Stability

| Dimension | Score (1–10) |
|---|---|
| Cookie handler convergence | 10/10 |
| Panel toggle debounce | 9/10 |
| Run button guard | 9/10 |
| Close button fallback | 9/10 |
| Late-binding polyfills | 8/10 |
| SSE re-subscription | 6/10 (known risk, no fix) |
| Monaco tab stability | 6/10 (deferred) |
| **Overall** | **8.1/10** |
