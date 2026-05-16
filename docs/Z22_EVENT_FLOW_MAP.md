# Z22 — Event Flow Map

**Date:** 2026-05-16  
**Phase:** Z22 — Frontend Module Decomposition

---

## 1. NxBus Event Flows

```
┌─────────────────────────────────────────────────────────────────────┐
│                         NxBus Event Bus                             │
│                     (nx-event-bus.js)                               │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────────┐
        │                  │                       │
   tabChange          paletteOpen/Close      modalOpen/Close
        │                  │                       │
        ↓                  ↓                       ↓
 NxTabManager        NxCommandPalette         NxModalSystem
(subscribers can    (emits on open/close)   (emits on open/close;
  react to tab                               tracks focus stack)
   switches)
        
   execStateChange    sseReconnectStorm    heapGrowthTrend   domNodeCritical
        │                  │                    │                  │
        ↓                  ↓                    ↓                  ↓
  NxExecIndicators  NxRuntimeHygiene      NxRuntimeHygiene   NxRuntimeHygiene
  (updates UI,      (detects > 5          (heap samples       (DOM node
   aria-live)       reconnects/60s)       growing)            alarm)
```

---

## 2. DOM Event Flows (Pre-existing)

### Keyboard
```
document.keydown
    ├─→ nx-keyboard-shortcuts.js (ALL shortcuts — primary handler)
    │       ├─ Ctrl+Enter → nxRunOrStop()
    │       ├─ Ctrl+K → nxOpenPalette()
    │       ├─ Escape → nxForcePaletteClose() / closeSettings()
    │       └─ Ctrl+Shift+E/I → nxToggleLeft/Right()
    │
    └─→ runtime.js (secondary — Escape → closeSettings guard)
        [non-conflicting: does not preventDefault on duplicate cases]
```

### Click
```
document.click
    ├─→ ui.js → nxClosePlanDropdown(), nxClosePlusMenu()
    ├─→ runtime.js → close notif panel (_closePanel)
    └─→ dashboard.js → close provider menu
```

### Custom Events (DOM)
```
document/window
    ├─ dispatchEvent(p7PlanChanged) ← dashboard.js P7
    │       └─→ dashboard.js P7 listener
    ├─ dispatchEvent(p8PlanGate) ← dashboard.js P8
    │       └─→ dashboard.js P8 listener
    ├─ dispatchEvent(nxTaskStart) ← dashboard.js
    │       └─→ dashboard.js P9, P10 listeners
    └─ dispatchEvent(nxTaskDone) ← dashboard.js
            └─→ dashboard.js P9, P10 listeners
```

---

## 3. Tab-Switch Flow (Z22 enhanced)

```
User clicks .nx-tab or calls nxSetTab('logs')
    │
    ↓
window.nxSetTab(id)  ←── patched by NxTabManager
    │
    ├─→ [original ui.js nxSetTab logic]
    │       ├─ Remove .active from all tabs
    │       ├─ Set aria-selected="false" on all tabs (Z19)
    │       ├─ Add .active to target tab
    │       ├─ Set aria-selected="true" on target (Z19)
    │       ├─ Show correct tab panel
    │       └─ Call nxUpdateTabActions(id)
    │
    └─→ NxBus.emit('tabChange', { tab: id, prev })
            │
            └─→ Any subscriber (future dashboards, analytics, etc.)
```

---

## 4. Command Palette Flow (Z22 module, Z23 ARIA)

```
User presses Ctrl+K
    │
    ↓
nx-keyboard-shortcuts.js captures Ctrl+K
    │
    ↓
window.nxOpenPalette()  [owned by nx-command-palette.js]
    ├─ _lastFocus = document.activeElement  (Z19 focus capture)
    ├─ palette.classList.add('open')
    ├─ palette.setAttribute('aria-hidden', 'false')
    ├─ input.focus()
    ├─ NxBus.emit('paletteOpen')
    └─ _announce('Command palette open. N commands available.')
          └─→ #nxPaletteAnnounce [aria-live=assertive]

User types → input event → _render(query) → filtered list re-rendered
User presses ArrowDown/Up → _selected changes → re-render
User presses Enter → nxRunPaletteItem(i)
    ├─ _closeClean()
    │       ├─ palette.classList.remove('open')
    │       ├─ palette.setAttribute('aria-hidden', 'true')
    │       ├─ _restoreFocus()  → _lastFocus.focus()  (Z19)
    │       └─ NxBus.emit('paletteClose')
    └─ _ITEMS[i].action()
```

---

## 5. Modal Open/Close Flow (Z22 module, Z23 focus trap)

```
openSettings(tab) called
    │
    ↓
runtime.js original openSettings  (shows modal DOM)
    │
    ↓ [monkey-patched by nx-modal-system.js]
NxModal.open(settingsBackdrop)
    ├─ prevFocus = document.activeElement
    ├─ el.setAttribute('aria-hidden', 'false')
    ├─ el.setAttribute('role', 'dialog')
    ├─ el.setAttribute('aria-modal', 'true')
    ├─ trapListener = _trapFocus(el)    ← Z23 Tab key trap
    ├─ el.addEventListener('keydown', trapListener)
    ├─ _stack.push({ el, prevFocus, trapListener })
    ├─ first focusable element.focus()
    ├─ _announce('Settings dialog opened')
    └─ NxBus.emit('modalOpen', { id: 'settingsBackdrop' })

closeSettings() called
    │
    ↓
runtime.js original closeSettings  (hides modal DOM)
    │
    ↓ [monkey-patched by nx-modal-system.js]
NxModal.close(settingsBackdrop)
    ├─ Remove trapListener from el
    ├─ el.setAttribute('aria-hidden', 'true')
    ├─ prevFocus.focus()    ← Z19/Z23 focus restoration
    ├─ _announce('Dialog closed')
    └─ NxBus.emit('modalClose')
```

---

## 6. Execution State Flow (Z22 module, Z23 aria-live)

```
nxSetGlobalStatus(state) called  [patched by nx-exec-indicators.js]
    │
    ├─→ [original ui.js nxSetGlobalStatus logic]
    │
    └─→ NxExecState.setState(state)
            ├─ root.setAttribute('data-exec-state', state)
            ├─ run dot: classList.toggle('visible', running)
            ├─ run btn: aria-label + aria-pressed updated
            ├─ status bar text updated
            ├─ _announce('Task started' / 'Task completed' / ...)
            │       └─→ #nxExecAnnounce [aria-live=polite]
            ├─ handlers array notified
            └─ NxBus.emit('execStateChange', { state, prev })
```

---

## 7. SSE Lifecycle Flow (Z21/Z24 patched)

```
new EventSource(url)  [patched by nx-runtime-hygiene.js]
    ├─ _state.sseConnected = true
    ├─ Wrap message events → update _state.sseLastMessage
    ├─ error → body.classList.add('nx-sse-reconnecting')
    │         → Z24 reconnect storm counter
    └─ open  → body.classList.remove('nx-sse-reconnecting')
              → Z24 storm resolution

setInterval(checkSSEStaleness, 10s):
    └─ if Date.now() - sseLastMessage > 45s → body.nx-sse-reconnecting
```
