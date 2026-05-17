# Z51 Performance Stabilization Report

## Phase Z51E — Runtime Pressure Reduction

**Date:** 2026-05-17  
**Status:** COMPLETE (partial)  

---

## Issues Found

### 1. MutationObserver Over-Budget: 17–19 observers (budget 8)

The NDS performance monitor reports 17–19 active MutationObservers. The budget is 8.

**Root causes identified:**

| Observer | File | Watches | Type |
|---|---|---|---|
| Inspector slot watchers (×2) | `ui.js` | `#nxDecisionSlot2`, `#nxOutputSlot` | subtree + characterData |
| Log area narrative watcher | `nx-z44-runtime.js` | `#logArea` | subtree + childList |
| Log area collapse watcher | `ux_trust.js` | `#logArea` | subtree + childList |
| Run button class watcher | `nx-z44-runtime.js` | `#runBtn` | attributes |
| Activity bar watcher | Immersive system | `#nxActivityBar` | childList + subtree |
| Z50 queue count badge | `nx-z50.js` (Z50) | `#nxQueueCount` | text |
| Z50 SSE label watcher | `nx-z50.js` (Z50) | `#nx-obs-conn-label` | text |
| Z51 approvals row | `nx-z51.js` (Z51) | `#z33ApprovalsRow` | childList + subtree |
| Z51 billing content | `nx-z51.js` (Z51) | `#p36InspBillingContent` | childList (conditional) |
| Various sub-observers | multiple `nx-z3x` files | Misc DOM nodes | childList |

**Z50 + Z51 added:** 3 observers (queue badge, SSE label conditional, approvals row)

**Action taken in Z51:**
- `#logArea` wrapped in CSS `contain: strict` to limit reflow cost of child mutations
- `#nxActivityBar` wrapped in CSS `contain: layout style`
- `body.nx-resizing *` suppresses all transitions to avoid layout-triggered observer callbacks
- Registered `window.waitForNxBus()` helper to eliminate 100ms polling loops in z45/z33/z48

### 2. Duplicate 100ms NxBus Ready Loops

`nx-z45-sync.js`, `nx-z33-palette.js`, `nx-z48.js` all run independent `setInterval(..., 100)` loops waiting for `window.NxBus`:

```javascript
// Before Z51 — in 3 separate files:
const t = setInterval(() => { if (window.NxBus) { clearInterval(t); cb(); } }, 100);
```

**Fix:** `z51PerformanceStabilize()` registers `window.waitForNxBus(cb)` which:
1. Calls `cb()` immediately if `NxBus` is already available
2. Uses a single shared observer interval with proper cleanup
3. Dispatches `nx:bus:ready` custom event when NxBus becomes available — future modules can use `document.addEventListener('nx:bus:ready', ...)` instead of polling

The 3 existing polling loops in z45/z33/z48 cannot be patched without modifying those files. They are already self-clearing once `NxBus` loads (typically < 200ms). Risk: if NxBus takes > 10s, those loops leak. Unlikely in practice.

### 3. Metric Polling Overlap

Two 8-second metric intervals run in parallel:
- `ui.js`: `nxRefreshMetrics` every 8s
- `nx-z47.js`: `_updateDockMeta` every 8s

**Fix:** `nxStartMetrics()` is wrapped to return early if `NX.metricTimer` already exists. Prevents double-scheduling from module re-runs.

### 4. Placeholder Rotation Interval

`nxStartPlaceholderRotation()` in `ui.js` runs a 3500ms interval. It continues even when the composer input is not in the DOM / not visible.

**Fix:** Z51 registers an `IntersectionObserver` on the composer input that clears `NX._placeholderTimer` when the input is scrolled out of view.

### 5. Layout Thrash

`nxApplyLayout()` in `ui.js` reads/writes CSS variables via `style.setProperty`. Z50 already uses `requestAnimationFrame` to batch these. Z51 adds `body.nx-resizing * { transition: none !important; }` to prevent transition recalculations during drag.

---

## Observer Count After Z51

Estimated observer count: 17–19 (pre-Z51) → 18–20 (post-Z51 adds 3, no net reduction of existing)

**Important note:** Z51 cannot reduce the pre-existing observer count without modifying the files that created them (z44, z45, z47, ux_trust, etc.). The CSS `contain` properties applied to `#logArea` and `#nxActivityBar` significantly reduce the *cost* of each mutation callback by limiting reflow scope, even if the count remains above budget.

**True fix path for v1.0:** Consolidate `#logArea` observers into a single `NxBus` event pipe. Replace all subtree MutationObservers with targeted childList-only observers.

---

## Remaining Stability Risks

1. 17+ MutationObservers remain. The NDS budget warning will persist.
2. `nx-z47.js` `_dockTimer` is never cleared — minor leak if dock is hidden early.
3. `nx-z44-runtime.js` sets multiple `classifyState` intervals across story feed initialization.

---

## Beta Readiness Score: 6.5/10

Performance is meaningfully improved via CSS containment and metric polling deduplication. The observer count remains above budget due to pre-existing observers in earlier phases. Full resolution requires refactoring those earlier modules — a v1.0 task.
