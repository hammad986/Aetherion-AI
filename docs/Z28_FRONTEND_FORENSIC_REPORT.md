# Z28 Frontend Forensic Report

**Phase:** Z28E — UI Stability + Forensic Review  
**Date:** 2026-05-16  
**Status:** STABLE

---

## Scope

Full forensic review of the Z28 frontend implementation: module architecture, CSS namespacing, event bus wiring, DOM lifecycle, tab integration, and regression safety.

---

## File Inventory

| File | Role | Size |
|------|------|------|
| `static/js/nx-z28-operator.js` | Z28 UI module — all four panels | ~620 lines |
| `static/css/nx-z28-operator.css` | Scoped styles for Z28 components | ~350 lines |
| `templates/index.html` (additions) | Intel tab button, content div, init script | +42 lines |
| `static/js/nx-sse-runtime.js` (additions) | Four new SSE case handlers | +16 lines |

---

## Module Architecture

`nx-z28-operator.js` exports a single `window._z28` namespace object:

```javascript
window._z28 = {
  mount(container, sid)  // Initialize and mount all panels into container
  setSid(sid)            // Update active session, trigger refresh
  refresh()              // Force-poll all four API endpoints
  destroy()              // Detach all listeners and clear timers
}
```

All internal state is closure-scoped. No global variable pollution beyond the `_z28` namespace.

---

## Panel Architecture

The module renders four panels inside `#nxTab-intel`:

| Panel | Class | Data Source |
|-------|-------|-------------|
| Decision Feed | `.z28-feed` | `GET /api/z28/decisions` + `nx:z28:decision` SSE |
| Execution Timeline | `.z28-timeline` | `GET /api/z28/timeline` |
| Context Pressure | `.z28-context` | `GET /api/z28/context-pressure` + `nx:z28:context` SSE |
| Health Bar | `.z28-health` | `GET /api/z28/health` + `nx:z28:health` SSE |

---

## CSS Namespacing

All Z28 CSS rules are scoped under the `.z28-*` prefix. No existing CSS class names are overridden or conflicted.

```css
/* Example — all rules follow this pattern */
.z28-root { ... }
.z28-feed { ... }
.z28-feed-item { ... }
.z28-feed-item--model_selection { ... }
.z28-health-bar { ... }
.z28-context-bar { ... }
.z28-timeline { ... }
.z28-phase { ... }
```

CSS custom properties used (`--nx-bg-2`, `--nx-text-1`, `--nx-accent`) are existing Nexora design tokens, ensuring automatic dark/light theme compatibility.

---

## DOM Lifecycle

### Mount Sequence

1. `nxSetTab('intel')` called — dispatches `nx:tab:intel` DOM event
2. Inline init script catches event → calls `window._z28.mount(container, sid)`
3. `mount()` checks for `container.querySelector('.z28-root')` — prevents double-mount
4. Renders HTML skeleton into container
5. Attaches NxBus listeners for `nx:z28:decision`, `nx:z28:context`, `nx:z28:health`
6. Starts polling timers (8-second intervals)
7. Performs immediate first-poll for all four endpoints

### Session Change Sequence

1. `NxBus.on(SESSION_CREATED)` fires
2. Inline script calls `window._z28.setSid(session_id)`
3. `setSid()` updates `_currentSid`, clears existing feed items, re-polls all endpoints

### Destroy Sequence (tab deactivation or page unload)

1. `window._z28.destroy()` clears all `setInterval` timers
2. NxBus listeners are detached

---

## Event Bus Audit

All Z28 events flow through the existing `NxBus` system. No new global event emitters are introduced.

| SSE Event | NxBus Channel | Handler Location |
|-----------|---------------|-----------------|
| `agent.explain` | `nx:z28:decision` | `nx-z28-operator.js` |
| `agent.context_state` | `nx:z28:context` | `nx-z28-operator.js` |
| `agent.confidence_warning` | `nx:z28:health` | `nx-z28-operator.js` |
| `agent.scheduler_state` | `nx:z28:scheduler` | Reserved for future use |

---

## Regression Safety

### Existing Tabs

The Intel tab is inserted before the `#nx-legacy-tabs` div in the tab bar. The tab button follows the existing pattern:

```html
<button class="nx-tab" role="tab" aria-selected="false"
        data-nxtab="intel" onclick="nxSetTab('intel'); ...">
```

The `nxSetTab()` function handles all tab switching; adding a new tab button does not affect existing tab behavior.

### SSE Runtime

The four new `case` branches in `nx-sse-runtime.js` are added before the `default:` fall-through. No existing case branches are modified. The `heartbeat` case, `agent.done`, `agent.error`, and all other existing events are unaffected.

### Script Load Order

`nx-z28-operator.js` is loaded with `defer` after `nx-sse-runtime.js` and `nx-intelligence.js`. The init script uses `setTimeout` fallback to wait for NxBus availability, so load order issues cannot cause initialization failures.

---

## Performance Profile

| Operation | Frequency | Cost |
|-----------|-----------|------|
| Decision feed poll | Every 8s (tab active) | ~1KB JSON, O(n) DOM update |
| Health poll | Every 8s (tab active) | ~0.5KB JSON, single element update |
| Context pressure poll | Every 8s (tab active) | ~0.5KB JSON, single bar update |
| Timeline poll | Every 15s (tab active) | ~0.5KB JSON, phase list update |
| SSE event processing | On event (any tab) | O(1) NxBus emit |

Polling is suspended when the Intel tab is not active. Timer cleanup in `destroy()` prevents memory leaks.

---

## Accessibility

| Feature | Status |
|---------|--------|
| Tab button has `role="tab"` and `aria-selected` | PASS |
| Tab button has descriptive `title` attribute | PASS |
| Intel tab dot indicator uses CSS only (no ARIA impact) | PASS |
| Color is not the sole differentiator (labels accompany all color bands) | PASS |

---

## Forensic Conclusion

The Z28 frontend implementation is architecturally sound, namespace-clean, and introduces no regressions to existing UI modules. All lifecycle paths (mount, session change, destroy) are implemented. Event bus wiring is complete and consistent with existing Nexora conventions.

**Status: FORENSIC REVIEW PASSED**
