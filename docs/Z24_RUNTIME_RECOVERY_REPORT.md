# Z24 — Runtime Recovery Report

**Date:** 2026-05-16  
**Phase:** Z24 — Real-World Session Stress Validation

This report documents all failure modes, their detection mechanisms, and recovery paths.

---

## 1. SSE Failure Recovery

### 1.1 Normal Disconnect (network blip)
```
SSE error event fires
    ↓
body.nx-sse-reconnecting set (CSS indicator + screen reader)
    ↓
runtime.js reconnect logic: setTimeout(_connectSSE, 15000) [notif stream]
runtime.js openLogStream: re-opens EventSource after reconnect
    ↓
SSE open fires → body.nx-sse-reconnecting removed
    ↓
Log stream resumes from lastLogSeq (no missed lines)
```
**Recovery time:** 15–30 seconds for notification stream; immediate for log stream on new session load

### 1.2 SSE Reconnect Storm
```
>5 reconnects in 60 s detected by hygiene module
    ↓
body[data-sse-storm="true"] set
NxBus.emit('sseReconnectStorm')
console.warn logged
HUD shows "STORM (N reconnects)"
    ↓
Application continues (not crashed; degraded mode)
    ↓
On first successful open: storm cleared
```
**Operator action:** Check server health, network, proxy timeouts

### 1.3 SSE Stale (silent stream)
```
No message event for >45 s
    ↓
body.nx-sse-reconnecting set (staleness alarm)
    ↓
On next message: class removed, _state.sseLastMessage updated
```

---

## 2. Page Refresh Recovery

All frontend state is either:
- **Persisted** (localStorage / cookies) — session ID, active session, plan mode, theme
- **Re-fetched** (API) — log history since lastLogSeq, session status, config
- **Reinitialised** (module code) — all IIFEs re-run cleanly

### Reinitialisation order on refresh
```
1. boot.js (sync) — sets up NX_BOOT_TASKS, NX_LOAD_TASKS
2. ui.js (defer) — registers nxSetTab, NX state
3. nx-event-bus.js — NxBus ready
4. nx-command-palette.js — palette ready, NX_BOOT_TASKS.push(_init)
5. nx-keyboard-shortcuts.js — keydown listener attached
6. nx-tab-manager.js — tab manager wraps nxSetTab, NX_LOAD_TASKS.push(_init)
7. runtime.js — sets up SSE, openSettings, notif bell
8. nx-runtime-hygiene.js — hygiene module starts all monitors
9. nx-modal-system.js — patches openSettings, p55OpenPanel
10. nx-exec-indicators.js — patches nxSetGlobalStatus
11. dashboard.js — P4–P36 phases initialise
12. DOMContentLoaded → nxBoot() processes NX_BOOT_TASKS
13. Post-DOMContentLoaded → NX_LOAD_TASKS processed
14. Session restored from localStorage
15. SSE reconnects
```
**No stale listener accumulation:** every page refresh is a clean slate.

---

## 3. Task / Execution Recovery

### 3.1 Task runs during page refresh
```
User refreshes mid-execution
    ↓
Session still running server-side
    ↓
selectSession() called on session restore
openLogStream() reconnects SSE from lastLogSeq=0
    ↓
Log history loaded via /api/session/:id/logs?since=0
Task status polled via pollSessionStatus
    ↓
UI reflects actual execution state (running/done)
```

### 3.2 Orphaned exec state
```
Page refreshed after task completes but before UI updated
    ↓
pollSessionStatus returns status='done'
nxSetGlobalStatus('idle') called
NxExecState.setState('idle') propagates
    ↓
Run dot hidden, run button label restored, aria-pressed=false
```

---

## 4. Modal / Focus Recovery

### 4.1 Modal left open during refresh
No recovery needed — page refresh reinitialises DOM. All modals start hidden.

### 4.2 Modal close fails (JS error)
```
If NxModal.close() throws:
    → trapListener not removed from element
    → but element becomes hidden via closeSettings() DOM mutation
    → focus drift possible
```
**Mitigation:** All critical modal close paths (`closeSettings`, Escape handler) call the original runtime.js functions first; the NxModal patch is additive.

---

## 5. Memory / Heap Recovery

### 5.1 Heap pressure
```
Heap > 85% of limit detected by HUD
    ↓
Red indicator shown
Operator action: hard refresh (cleanest recovery)
    ↓
Fresh page load: all JS memory released
```

### 5.2 Heap growth trend
```
5 consecutive rising heap samples
    ↓
console.warn + NxBus.emit('heapGrowthTrend')
    ↓
Operator investigates: check logTrimCount, observerCount, domNodes
    ↓
If log ceiling: verify enforceLogCeiling running (should auto-fix)
If observer leak: use DevTools memory profiler
```

---

## 6. Recovery Tooling Reference

| Tool | Usage | Recovery |
|------|-------|---------|
| `nxPerfHUD()` | Toggle performance HUD | Visual check |
| `_nxDiagSnapshot()` | Full system state JSON | Diagnose issues |
| `_nxPerfState` | Live state object | Real-time monitoring |
| `_nxClearAllTimers()` | Emergency timer eviction | Stop runaway timers |
| `NxBus._debug()` | List all registered event handlers | Debug event leaks |
| `NxTabManager.getHistory()` | Tab switch history | Debug navigation |
| Hard refresh | Full memory release | Ultimate recovery |
| Session switch | Reset log stream, log buffer | Per-session recovery |

---

## 7. Certification Statement

> The Nexora AI runtime satisfies the Z24 Runtime Recovery Standard as of 2026-05-16.  
> All identified failure modes have defined detection mechanisms and recovery paths.  
> SSE disconnect, storm, and stale scenarios are detected, surfaced to the operator, and resolved automatically where possible.  
> Page refresh produces a clean, deterministic reinitialisation of all modules.  
> Task execution state is recovered from server-side status on session restore.  
> All diagnostic tools are available in the browser console without DevTools extensions.

**Module:** `static/js/nx-runtime-hygiene.js`, all Z22 modules
