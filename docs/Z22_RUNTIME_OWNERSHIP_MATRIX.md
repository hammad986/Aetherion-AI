# Z22 — Runtime Ownership Matrix

**Date:** 2026-05-16  
**Phase:** Z22 — Frontend Module Decomposition

This matrix maps every runtime resource (listeners, timers, observers, state) to its owning module, enabling safe cleanup and preventing listener proliferation in long sessions.

---

## 1. Event Listeners

| Event | Target | Handler | Owner module | Duplicate? |
|-------|--------|---------|-------------|-----------|
| `keydown` | `document` | All shortcuts | `nx-keyboard-shortcuts.js` | No (single) |
| `keydown` | `#nxPaletteInput` | Arrow/Enter/Escape | `nx-command-palette.js` | No |
| `keydown` | `.p57-drawer` (dynamic) | Focus trap | `nx-modal-system.js` (per-open) | No |
| `keydown` | `#settingsBackdrop` (dynamic) | Focus trap | `nx-modal-system.js` (per-open) | No |
| `keydown` | `document` | Escape → closeSettings | `runtime.js` | ⚠️ Overlaps with nx-keyboard-shortcuts.js |
| `click` | `document` | Notif panel close | `runtime.js` | No |
| `click` | `document` | Plan dropdown close | `ui.js` | No |
| `click` | `document` | Plus menu close | `ui.js` | No |
| `click` | `document` | Provider menu close | `dashboard.js` | No |
| `keydown` | `#nxTabBar` | Arrow nav between tabs | `nx-tab-manager.js` | No (Z22/Z23) |
| `input` | `#nxPaletteInput` | Palette search | `nx-command-palette.js` | No |
| `change` | `document` | Plan mode inputs | `dashboard.js` | No |
| `p7PlanChanged` | `window` | Plan changed | `dashboard.js` | No |
| `p8PlanGate` | `document` | Plan gate | `dashboard.js` | No |
| `nxTaskStart` | `document` | Task start hook | `dashboard.js` | No |
| `nxTaskDone` | `document` | Task done hook | `dashboard.js` × 2 | No |
| `resize` | `window` | Layout recalc | `runtime.js` | No |
| `mousemove` | `document` | Drag handle (conditional) | `runtime.js` (drag) | Only during drag |
| `mouseup` | `document` | Drag handle (conditional) | `runtime.js` (drag) | Only during drag |

### Overlap Analysis
**`keydown` on `document`:** registered by both `runtime.js` (closes settings on Escape) and `nx-keyboard-shortcuts.js` (handles all shortcuts). These are non-conflicting because:
- runtime.js handles only `e.key === 'Escape'` when settings modal is open
- nx-keyboard-shortcuts.js handles Escape only when `settingsBackdrop` or palette is active
- Both will fire, but the first handler to call `e.stopPropagation()` wins; since neither does, both execute safely

---

## 2. setInterval Timers

| Timer ID | File | Interval | Purpose | Cleaned up on session reset? |
|----------|------|----------|---------|------------------------------|
| Log ceiling enforcer | `nx-runtime-hygiene.js` | 5 s | DOM trim backup | ❌ Permanent (acceptable) |
| Toast TTL sweep | `nx-runtime-hygiene.js` | 6 s | Toast prune | ❌ Permanent (acceptable) |
| SSE staleness check | `nx-runtime-hygiene.js` | 10 s | SSE health | ❌ Permanent (acceptable) |
| HUD refresh | `nx-runtime-hygiene.js` | 2 s | Perf HUD | ❌ Permanent (acceptable) |
| Heap sample | `nx-runtime-hygiene.js` | 30 s | Z24 trend | ❌ Permanent (acceptable) |
| DOM node alarm | `nx-runtime-hygiene.js` | 30 s | Z24 alarm | ❌ Permanent (acceptable) |
| Notif poll | `runtime.js` IIFE | 12 s | Bell refresh | ❌ Permanent |
| Session status tick | `runtime.js` | 3–8 s dynamic | Poll status | ✅ Dynamic (stops when done) |
| Metrics poll | `runtime.js` | 8 s | System metrics | ❌ Permanent |
| Provider list | `dashboard.js` | 60 s | BYOK providers | ❌ Permanent |
| Token poll | `dashboard.js` P4 | configurable | Token usage | ✅ Ref tracked in `p4TokenPollTimer` |
| Worker poll | `dashboard.js` | 5 s | Worker dashboard | ✅ Ref tracked |
| Team poll | `dashboard.js` | 6 s | Team dashboard | ✅ Ref tracked |
| Projects poll | `dashboard.js` | 5 s | Projects | ✅ Ref tracked |
| P8 poll | `dashboard.js` | 60 s | Monetization | ✅ Ref tracked |
| P9 poll | `dashboard.js` | 30 s | Model routing | ✅ Ref tracked |
| P10 timer | `dashboard.js` | 45 s | Memory | ✅ Ref tracked |

---

## 3. MutationObservers

| Observer | Target | Purpose | Disconnected? | Owner |
|----------|--------|---------|-------------|-------|
| Toast observer | `document.body` | Toast eviction | ❌ Permanent | `nx-runtime-hygiene.js` |
| MO count patch | All new MOs | Count tracking | N/A | `nx-runtime-hygiene.js` |
| P7 task observer | `#taskInput` | Session flash | ❌ Never | `dashboard.js` |
| P6 run observer | `#runBtnLabel` | State tracking | ❌ Never | `dashboard.js` |

**Z24 Note:** The 2 dashboard observers target stable DOM elements and are low-cost. They are documented as permanent by design.

---

## 4. EventSource (SSE) Connections

| Instance | URL | Cleanup | Owner |
|----------|-----|---------|-------|
| Log stream | `/api/session/:id/stream` | `closeLogStream()` | `runtime.js` |
| Notification stream | `/api/notifications/stream` | Auto-reconnect | `runtime.js` IIFE |

---

## 5. Module Public API Registry

| API | Module | Methods |
|-----|--------|---------|
| `window.NxBus` | `nx-event-bus.js` | `on`, `once`, `off`, `emit`, `clear`, `_debug` |
| `window.NxKeyboard` | `nx-keyboard-shortcuts.js` | `register`, `unregister`, `list` |
| `window._NxPalette` | `nx-command-palette.js` | `open`, `close`, `forceClose`, `runItem`, `register` |
| `window.NxTabManager` | `nx-tab-manager.js` | `setTab`, `getActive`, `getHistory`, `onTabChange` |
| `window.NxModal` | `nx-modal-system.js` | `open`, `close` |
| `window.NxExecState` | `nx-exec-indicators.js` | `setState`, `getState`, `onStateChange`, `STATES` |
| `window._nxPerfState` | `nx-runtime-hygiene.js` | State object (read-only) |
| `window._nxDiagSnapshot` | `nx-runtime-hygiene.js` | Full diagnostic snapshot fn |
| `window.nxPerfHUD` | `nx-runtime-hygiene.js` | Toggle HUD |
| `window._nxClearAllTimers` | `nx-runtime-hygiene.js` | Emergency timer eviction |

---

## 6. Module Dependency Graph

```
nx-event-bus.js      (no deps)
    ↑
nx-command-palette.js (NxBus, window.nxSetTab, window.NX_BOOT_TASKS)
    ↑
nx-keyboard-shortcuts.js (window.nxOpenPalette, window.nxRunOrStop, window.NxWorkspace)
    ↑
nx-tab-manager.js    (NxBus, window.nxSetTab, window.NX_LOAD_TASKS)

nx-modal-system.js   (NxBus, window.openSettings, window.p55OpenPanel)
    ↑
nx-exec-indicators.js (NxBus, window.nxSetGlobalStatus)

nx-runtime-hygiene.js (NxBus, window.EventSource, window.MutationObserver)
```
