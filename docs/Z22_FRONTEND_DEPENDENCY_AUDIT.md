# Z22 — Frontend Dependency & Lifecycle Audit

**Date:** 2026-05-16  
**Scope:** `runtime.js` (4,269 lines), `dashboard.js` (3,144 lines), `ui.js` (1,210 lines), `activity.js` (1,365 lines), `boot.js` (516 lines), `feedback.js` (81 lines)  
**Classification:** Pre-decomposition forensic baseline

---

## 1. File Inventory & Responsibility Map

| File | Lines | Responsibility | Modular? |
|------|-------|---------------|---------|
| `boot.js` | 516 | Bootstrap tasks, NX_BOOT_TASKS/NX_LOAD_TASKS registry | Partially |
| `ui.js` | 1,210 | Tab management, panel layout, palette, keyboard, exec status | Monolithic |
| `runtime.js` | 4,269 | SSE log stream, settings modal, Monaco editor, file tree, exec | Monolithic |
| `dashboard.js` | 3,144 | Phases P4–P36: token tracking, providers, agents, routing | Monolithic |
| `activity.js` | 1,365 | P33 activity bar, execution pipeline visualization | Semi-modular |
| `feedback.js` | 81 | Feedback modal | Modular |
| `nx-runtime-hygiene.js` | 275 | Z21/Z24 DOM/memory discipline | Modular (IIFE) |

---

## 2. Global State Inventory

### `runtime.js` top-level globals (all script-scope mutable)
| Variable | Type | Purpose | Risk |
|----------|------|---------|------|
| `currentSession` | string | Active session ID | Shared by all phases |
| `lastLogSeq` | number | SSE log sequence counter | SSE coupling |
| `logStream` | EventSource | Main SSE connection | Singleton; hard to clean up |
| `logBuffer` | Array | Full log history | Unbounded before Z21 fix |
| `pendingLogRows` | Array | RAF-buffered DOM rows | RAF flush gap |
| `settingsModalOpen` | boolean | Modal state guard | Shared across phases |
| `runtimeInitialized` | boolean | One-time guard | Race condition risk |
| `workingConfig` | object | Mutable BYOK config | Mutated by multiple phases |

### `dashboard.js` top-level globals
| Variable | Type | Leaks |
|----------|------|-------|
| `p4TokenPollTimer` | setInterval | Accumulates if session resets |
| `_workerPollTimer` | setInterval | Accumulates |
| `_teamPollTimer` | setInterval | Accumulates |
| `_projectsPollTimer` | setInterval | Accumulates |
| `p6RecTimer` | setTimeout | Potentially abandoned |
| `p7PollTimer` | setInterval | Accumulates across phase resets |
| `p8PollTimer` | setInterval | 60 s polling |
| `p9PollTimer` | setInterval | 30 s polling |
| `_p10Timer` | setInterval | 45 s polling |
| `_p11Polling` | setInterval | 2 s polling during agent run |
| 2× `MutationObserver` | object | Never disconnected |

### `ui.js` top-level globals
| Variable | Purpose |
|----------|---------|
| `NX` | Global UI state: `activeTab`, `leftW`, `rightW` etc. |
| All `nx*` functions | All exposed via `window.*` at module end |

---

## 3. `window.*` Global Registration Audit

### Registered by `runtime.js`
`window.NX_LOAD_TASKS`, `window.nxBellToggle`, `window.nxNotifMarkAllRead`, `window.openSettings`, `window.closeSettings`, `window.CodeEditor`, `window.clearEditorOutput`, `window.hideEditorOutput`, `window.saveCurrentFile`, `window.runCurrentFile`, `window.testCurrentFile`

### Registered by `dashboard.js` (on `window.*`)
`window.applySystemPatch`, `window.p4ToggleTheme`, `window.p4RefreshSessionHistory`, `window.p4RestoreSession`, `window.p4SaveCustomTemplate`, `window.p4DeleteSavedTemplate`, `window.p4SetTplCat`, `window.p4OnTaskInput`, `window.p4CloseSuggest`, `window.p5ToggleProvMenu`, `window.p5SelectProvider`, `window.p5RenderByokPanel`, `window.p5ClearKey`, `window.p5TestAllKeys`, `window.p5ShowFailover`, `window.p6SetPriority`, `window.p6LockProvider`, `window.p6LoadIntelPanel`, `window.p6LoadPerfBadges`, `window.p6OnTaskType`, `window.p6ApplyRecommendation`, `window.p7ToggleBody`, `window.nxRunOrStop` (monkey-patched), `window.openSettings` (monkey-patched ×2), `window.switchSettingsTab` (monkey-patched), `window.renderByokProviders` (monkey-patched), `window.getByokApiKeys` (monkey-patched), `window.ingestLogRow` (monkey-patched ×2), `window.pollSessionStatus` (monkey-patched), `window.selectSession` (monkey-patched)

**Monkey-patch chains:** `window.openSettings` is monkey-patched by at least 3 sources (runtime.js, dashboard.js P5, dashboard.js P6). Each wraps the previous. Chain depth: 3.

### Registered by `ui.js`
`window.nxSetTab`, `window.nxSwitchTab`, `window.nxHideHero`, `window.nxShowHero`, `window.nxToggleMore`, `window.nxCloseMore`, `window.nxToggleLeft`, `window.nxToggleRight`, `window.nxToggleBottom`, `window.nxApplyLayout`, `window.nxRunOrStop`, `window.nxSetPlan`, `window.nxTogglePlanDropdown`, `window.nxClosePlanDropdown`, `window.nxTogglePlusMenu`, `window.nxClosePlusMenu`, `window.nxPlusMenu_file`, `window.nxPlusMenu_image`, `window.nxPlusMenu_folder`, `window.nxOpenGithubModal`, `window.nxCloseGithubModal`, `window.nxQueueTask`, `window.nxRunTask`, `window.nxEnsureTerminal`, `window.nxOpenPanel`, `window.nxToast`, `window.nxSetTask`, `window.p57SetView`, `window.p57UpdateLayout`, `window.p55OpenPanel`, `window.p55ClosePanel`, `window.p57OpenDetail`, `window.p57CloseDetail`, `window.p57FixError`

---

## 4. Event System Audit

### Custom events dispatched (`CustomEvent` / `dispatchEvent`)
| Event name | Dispatcher | Listeners |
|-----------|-----------|---------|
| `p7PlanChanged` | dashboard.js | dashboard.js (P7) |
| `p8PlanGate` | dashboard.js | dashboard.js (P8) |
| `nxTaskStart` | dashboard.js | dashboard.js (P9, P10) |
| `nxTaskDone` | dashboard.js | dashboard.js (P9, P10) |

### NxBus events (Z22 new — via `nx-event-bus.js`)
| Event name | Emitter | Listeners |
|-----------|---------|----------|
| `tabChange` | `nx-tab-manager.js` | External subscribers |
| `paletteOpen` | `nx-command-palette.js` | External subscribers |
| `paletteClose` | `nx-command-palette.js` | External subscribers |
| `modalOpen` | `nx-modal-system.js` | External subscribers |
| `modalClose` | `nx-modal-system.js` | External subscribers |
| `execStateChange` | `nx-exec-indicators.js` | External subscribers |
| `sseReconnectStorm` | `nx-runtime-hygiene.js` | External subscribers |
| `heapGrowthTrend` | `nx-runtime-hygiene.js` | External subscribers |
| `domNodeCritical` | `nx-runtime-hygiene.js` | External subscribers |

### `document.addEventListener` registrations
| File | Event | Handler | Duplicate risk |
|------|-------|---------|---------------|
| `ui.js` | `click` | Plan dropdown close | ✅ Single |
| `ui.js` | `click` | Plus menu close | ✅ Single |
| `runtime.js` | `click` | Notif bell panel close | ✅ Single |
| `runtime.js` | `keydown` | `closeSettings` on Escape | ⚠️ Duplicated by nx-keyboard-shortcuts.js |
| `dashboard.js` | `change` | Plan mode change | ✅ Single |
| `dashboard.js` | `click` | Provider menu close | ✅ Single |
| `dashboard.js` | `p8PlanGate` | Plan gate handler | ✅ Single |
| `dashboard.js` | `nxTaskStart/Done` | Phase 9/10 handlers | ✅ Single |
| `nx-keyboard-shortcuts.js` | `keydown` | All global keyboard shortcuts | ✅ Single (Z22) |

**Duplicate listener risk:** `runtime.js` registers its own `keydown` for Escape/closeSettings AND `nx-keyboard-shortcuts.js` handles Escape globally. This is safe but the runtime.js handler should be audited to check it doesn't double-close.

---

## 5. Timer Inventory (setInterval / setTimeout)

### `runtime.js`
| Timer | Interval | Purpose |
|-------|----------|---------|
| Poll tick | ~3–8 s dynamic | Session status polling |
| Metrics poll | ~8 s | System metrics |
| Notif bell | 12 s | Notification polling |

### `dashboard.js`
| Timer | Interval | Phase |
|-------|----------|-------|
| `_workerPollTimer` | 5 s | Worker dashboard |
| `_teamPollTimer` | 6 s | Team dashboard |
| `_projectsPollTimer` | 5 s | Projects dashboard |
| `p4TokenPollTimer` | configurable | Token usage |
| `p5LoadProviders` | 60 s | Provider list refresh |
| `p6RecTimer` | 600 ms debounce | AI recommendation |
| `p7PollTimer` | 800 ms | Agent status |
| `p8PollTimer` | 60 s | Monetization |
| `p9PollTimer` | 30 s | Model routing |
| `_p10Timer` | 45 s | Memory refresh |
| `_p11Polling` | 2 s | Multi-agent status |
| `waitForProviders` | internal | Provider wait loop |

**Total timers:** ~14 concurrent in a running session

### `ui.js` / modules
| Timer | Owner |
|-------|-------|
| Queue poll | `ui.js` nxPollQueue (boot task) |
| Suggest refresh | `ui.js` dashboard interval |

---

## 6. SSE Connections

| Connection | File | URL | Cleanup |
|-----------|------|-----|---------|
| Log stream | `runtime.js` `openLogStream` | `/api/session/:id/stream` | `closeLogStream()` — explicitly called |
| Notification stream | `runtime.js` IIFE | `/api/notifications/stream` | Reconnects on error, no explicit close |

---

## 7. MutationObserver Inventory

| File | Target | Purpose | Disconnected? |
|------|--------|---------|-------------|
| `dashboard.js` P7 | `#taskInput` | Flash new session highlight | ❌ Never |
| `dashboard.js` P6 | `#runBtnLabel` | Track run state for recommendation | ❌ Never |

**Z24 note:** These 2 observers are permanent but low-cost. Their never-disconnected status is acceptable for long sessions as they target stable DOM elements. Added to Z24 stress matrix.

---

## 8. Modules Extracted (Z22 Implementation)

| Module | Extracted from | Lines | Owns |
|--------|---------------|-------|------|
| `nx-event-bus.js` | New | ~60 | Typed event bus |
| `nx-command-palette.js` | ui.js (lines 998–1086) | ~160 | Palette state, focus trap, ARIA, input wiring |
| `nx-keyboard-shortcuts.js` | ui.js (lines 1088–1110, line 146) | ~120 | Global keydown listener, shortcut registry |
| `nx-tab-manager.js` | Wraps ui.js nxSetTab | ~80 | Tab change events, arrow navigation (Z23), ARIA |
| `nx-modal-system.js` | New | ~150 | Focus trap, aria-hidden, modal stack, Z23 semantics |
| `nx-exec-indicators.js` | New | ~100 | Exec state machine, run dot, status bar, aria-live |

---

## 9. Global State Reduction

| Item | Before Z22 | After Z22 | Reduction |
|------|-----------|-----------|----------|
| `window.nxOpenPalette` | Defined in ui.js | Owned by nx-command-palette.js | Module-owned |
| `window.nxClosePalette` | Defined in ui.js | Owned by nx-command-palette.js | Module-owned |
| `window.nxForcePaletteClose` | Defined in ui.js | Owned by nx-command-palette.js | Module-owned |
| `document.keydown` listener | Registered in ui.js nxInit | Owned by nx-keyboard-shortcuts.js | Module-owned |
| `window.NxBus` | Not present | Registered by nx-event-bus.js | Normalized bus |
| `window.NxTabManager` | Not present | Registered by nx-tab-manager.js | Module API |
| `window.NxModal` | Not present | Registered by nx-modal-system.js | Module API |
| `window.NxExecState` | Not present | Registered by nx-exec-indicators.js | Module API |
| `window.NxKeyboard` | Not present | Registered by nx-keyboard-shortcuts.js | Module API |
