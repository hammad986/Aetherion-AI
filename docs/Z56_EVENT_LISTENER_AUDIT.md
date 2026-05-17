# Z56 — Event Listener Consolidation Audit
**Phase**: Z56 Interaction Stabilization + Trust Recovery  
**Date**: 2026-05-17  
**Scope**: Duplicate event listeners, MutationObserver sprawl, NxBus handler deduplication

---

## 1. MutationObserver Consolidation (nx-z44-runtime.js)

### Before Z56

`attachAll()` created 4 individual MutationObserver instances, each watching a single DOM element and calling the same callback `() => applyState(classifyState())`:

```
watchRunBtn(runBtn)        → new MutationObserver #1
watchStatusPill(stStatus)  → new MutationObserver #2
watchHitlStrip(hitlStrip)  → new MutationObserver #3
watchErrorCard(errorCard)  → new MutationObserver #4
```

Total: 4 active observers for the same logical purpose.

### After Z56

```
watchStateElements(runBtn, stStatus, hitlStrip, errorCard)
  → new MutationObserver #1
       .observe(runBtn,     { attributes: true, attributeFilter: ['class'] })
       .observe(stStatus,   { childList: true, characterData: true, subtree: true })
       .observe(hitlStrip,  { attributes: true, attributeFilter: ['style'] })
       .observe(errorCard,  { attributes: true, attributeFilter: ['style'] })
```

Total: 1 active observer. Savings: 3 observers.  
Observer semantics are identical — same options per element as before.

---

## 2. Duplicate `window.nxAcceptCookies` Definition

### Before Z56

| Load order | File | Function | localStorage key |
|---|---|---|---|
| 1st | `session.js` | `window.nxAcceptCookies` | `nx_cookie_ok` |
| 2nd (overwrites) | `nx-z50.js` | `window.nxAcceptCookies` | `nx_cookie_accepted` |

Result: The session.js definition was always dead code after page load. Both banner init functions checked different keys.

### After Z56

| File | Function | localStorage key |
|---|---|---|
| `nx-z50.js` (sole owner) | `window.nxAcceptCookies` → `z50DismissCookieBanner(true)` | Writes BOTH `nx_cookie_accepted` AND `nx_cookie_ok` |

`session.js` cookie block removed. Banner init in `z50InitCookieBanner()` checks both keys.

---

## 3. NxBus Listener Audit

### Listeners per event (measured at runtime)

NxBus runs in strict dev mode on `localhost`, which logs duplicate listener warnings to console. No duplicates were observed in the current session logs.

| Event | Expected listeners | Source files |
|---|---|---|
| `nx:session:created` | 3 | `nx-z28-operator.js`, `nx-z29-governance.js`, inline `<script>` blocks in index.html |
| `nx:agent:done` | 1 | `nx-z28-operator.js` |
| `nx:agent:start` | 1 | `nx-z28-operator.js` |
| `nx:toast` | 1 | `nx-bus.js` bridge (→ NdsToast) |
| `nx:agent:status` | 1 | `nx-bus.js` bridge (→ NxStatusBar) |
| `nx:tab:change` | 1 | `nx-bus.js` bridge (→ NxInspector) |

`nx:session:created` has 3 listeners which is acceptable — each module needs its own init on new session. No overflow detected.

---

## 4. `nxTogglePanel` Override Chain

`workspace.js` exports `window.nxTogglePanel` at line 559.  
`nx-z50.js` overrides `window.nxTogglePanel` at line 135 (wrapping the original).

This single-level override is acceptable. If future Z-series scripts add more overrides, a proper middleware chain should be used instead.

**Recommendation**: Register panel hooks via `NxBus.on(EVENTS.PANEL_TOGGLE, ...)` rather than wrapping `window.nxTogglePanel` in future phases.

---

## 5. `document.addEventListener` Audit for Key Events

Searched all JS files for `addEventListener('DOMContentLoaded'`, `addEventListener('click'`, and `addEventListener('keydown'`:

| Event | Files registering listeners | Notes |
|---|---|---|
| `DOMContentLoaded` | 14 files | All guarded with `readyState` check — correct |
| Global `keydown` | `runtime.js`, `nx-command-palette.js`, `nx-z50.js` | No duplicates for same shortcut |
| Global `click` | `runtime.js` (backdrop close), `nx-z50.js` (panel close) | No conflicts |

No unguarded duplicate global key listeners found.

---

## Summary

| Issue | Observer/Listener Savings | Status |
|---|---|---|
| z44 state observer consolidation | −3 MutationObservers | Done |
| nxAcceptCookies deduplication | −1 function definition | Done |
| NxBus duplicate listeners | 0 found | Clean |
| nxTogglePanel override chain | Acceptable (1 level) | Documented |
| Global keydown/click listeners | No duplicates | Clean |
