# Z21 — Memory Stability Certification

**Date:** 2026-05-16  
**Phase:** Z21 — Runtime Performance & Memory Discipline  
**Result:** ✅ CERTIFIED

---

## 1. Module Summary

`static/js/nx-runtime-hygiene.js` is a self-contained IIFE that activates after `runtime.js` and enforces all Z21 memory discipline guarantees. It has zero external dependencies and adds no startup latency (loaded as `defer`).

---

## 2. Log DOM Bounded Growth — CERTIFIED

### Guarantee
The log area (`#logArea`) will never exceed **1,500 child nodes** as observed in any 5-second window.

### Mechanism
```javascript
const LOG_DOM_CEILING = 1500;

function enforceLogCeiling() {
  const area = document.getElementById('logArea');
  if (!area || area.children.length <= LOG_DOM_CEILING) return;
  const toRemove = area.children.length - LOG_DOM_CEILING;
  for (let i = 0; i < toRemove; i++) {
    if (area.firstChild) area.removeChild(area.firstChild);
  }
  _state.logTrimCount += toRemove;
  // Visual notice updated
}
setInterval(enforceLogCeiling, 5000);
```

**Defence layers:**
1. **Primary:** `runtime.js` RAF-batched trim at `MAX_LOG_LINES = 1500`
2. **Secondary (Z21):** Polling enforcer every 5 s — catches backgrounded-tab drift
3. **Visual:** Trim notice shown at top of log area when nodes are evicted

---

## 3. Toast Node Lifecycle — CERTIFIED

### Guarantee
At most **5 toast nodes** will exist simultaneously in `document.body`. Excess nodes are evicted with a 200 ms fade transition. All toasts are eligible for sweep after **6 seconds**.

### Mechanism
```javascript
const TOAST_MAX_ALIVE = 5;
const TOAST_TTL_MS    = 6000;

// MutationObserver: fires on new body children, prunes if over limit
// setInterval(TOAST_TTL_MS): periodic sweep regardless of observer
```

**Targets:** `.nx-toast`, `.toast`, `[data-toast]`, `.nxToast`

---

## 4. SSE Health Tracking — CERTIFIED

### Guarantee
If the SSE stream is silent for more than **45 seconds**, `body.nx-sse-reconnecting` is set, triggering the CSS status indicator. When a new message or `open` event arrives, the class is removed.

### Mechanism
```javascript
const SSE_STALE_MS = 45000;

// EventSource is patched at construction time to track sseLastMessage
// checkSSEStaleness() polls every 10 s
// body.nx-sse-reconnecting set/cleared by patch + staleness check
```

**CSS effect:**
```css
.nx-sse-reconnecting #nxSbStatus::after {
  content: ' (reconnecting…)';
  color: var(--yellow, #d29922);
}
```

---

## 5. FPS Monitoring — CERTIFIED

### Guarantee
Frame rate is sampled every second via `requestAnimationFrame`. Values are available in `window._nxPerfState.fps` and in the perf HUD when enabled.

```javascript
const _state = { fps: 0, fpsFrames: 0, fpsLast: performance.now() };

function sampleFPS(ts) {
  _state.fpsFrames++;
  if (ts - _state.fpsLast >= 1000) {
    _state.fps = Math.round((_state.fpsFrames * 1000) / (ts - _state.fpsLast));
    // reset counters
  }
  requestAnimationFrame(sampleFPS);
}
```

**Thresholds:** green ≥40 fps, yellow 20–39, red <20

---

## 6. Perf HUD — CERTIFIED

### Usage
```javascript
// In browser console:
nxPerfHUD()         // toggle on/off
nxPerfHUD(true)     // force on
nxPerfHUD(false)    // force off
```

### Metrics displayed
| Metric | Source | Warn | Critical |
|--------|--------|------|---------|
| FPS | rAF sampler | <40 | <20 |
| DOM nodes | `querySelectorAll('*').length` | >8,000 | >14,000 |
| Log rows | `#logArea.children.length` | — | >1,500 |
| Trimmed | `_state.logTrimCount` | — | — |
| SSE age | `Date.now() - sseLastMessage` | >30s | disconnected |
| JS Heap | `performance.memory` | >60% | >85% |

---

## 7. Emergency Tools

```javascript
// Inspect current state
window._nxPerfState

// Clear all tracked timers (debug only)
window._nxClearAllTimers()

// Toggle perf HUD
window.nxPerfHUD()
```

---

## 8. Performance Budget — Z21 Compliance

| Category | Budget | Mechanism | Status |
|----------|--------|-----------|--------|
| Log DOM rows | ≤ 1,500 | Dual-layer ceiling | ✅ |
| Toast nodes | ≤ 5 | MutationObserver + sweep | ✅ |
| SSE stale threshold | 45 s | EventSource patch | ✅ |
| DOM node warn | 8,000 | HUD indicator | ✅ |
| JS Heap warn | 60% | HUD indicator | ✅ |
| FPS floor warn | 40 fps | rAF sampler | ✅ |

---

## 9. Certification Statement

> The Nexora AI runtime satisfies the Z21 Memory Stability Standard as of 2026-05-16.  
> Log DOM growth is bounded by a dual-layer ceiling (runtime.js primary + hygiene module secondary).  
> Toast nodes are evicted by a MutationObserver + TTL sweep, capping live nodes at 5.  
> SSE staleness is detected within 45 seconds and surfaced to the user via CSS status indicator.  
> A debug perf HUD is available via `nxPerfHUD()` for operator diagnostics without DevTools.  
> All hygiene is self-contained in `nx-runtime-hygiene.js` with zero external dependencies.

**Certified by:** Z21 automated audit pass  
**Module:** `static/js/nx-runtime-hygiene.js` (deferred, ~8 KB)
