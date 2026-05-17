# Z56 — Interaction Audit Report
**Phase**: Z56 Interaction Stabilization + Trust Recovery  
**Date**: 2026-05-17  
**Scope**: All interactive UI surfaces — buttons, selects, panels, tabs, forms, modals

---

## Audit Methodology

Each interactive element was traced from HTML source through to its JavaScript handler and (where applicable) its backend API endpoint. Elements without a complete handler chain were classified as dead, broken, or deferred.

---

## 1. Composer Toolbar — Mode / Scope Selects

| Element | ID Before Z56 | ID After Z56 | Status |
|---|---|---|---|
| Mode select | *(none)* | `nxExecModeSelect` | Fixed |
| Scope select | *(none)* | `nxExecScopeSelect` | Fixed |

**Finding**: Both `<select>` elements in `.nx-exec-toolbar` had no `id`, no `value` attributes on options, and no `title`. The `z50WireExecSelects()` function in `nx-z50.js` already targeted them via `qsa('select', toolbar)` and wired `change` handlers that persist to `localStorage` and reflect into `window.NX.execMode` / `window.NX.execScope`.

**Fix applied**: Added stable IDs (`nxExecModeSelect`, `nxExecScopeSelect`), `value` attributes on all options, and `title` tooltips. The z50 wiring already covers the interaction logic.

---

## 2. Run / Stop Button (`#runBtn`)

**Status**: Fully wired.  
- `onclick` → `sendTask()` in `runtime.js`  
- State class `is-running` watched by `nx-z43-exec-state.js` (sets `document.body[data-nx-exec]`) and now also by the consolidated observer in `nx-z44-runtime.js`  
- Visual feedback: button text changes, `nx-z43` / `nx-z44` CSS applies workspace-level styling

---

## 3. Nav Rail Panel Buttons

**Status**: Wired via `nxTogglePanel(panelId)` in `nx-z50.js` (line 135).  
- All 5 nav icons (`sessions`, `files`, `hitl`, `notifications`, `scheduler`) call `nxTogglePanel()`  
- `nx-z50.js` overrides `window.nxTogglePanel` to include panel content population  
- `workspace.js` provides the underlying `NxWorkspace.toggleLeft()`  

**Known limitation**: The override chain is fragile — if a Z-series file re-overrides `nxTogglePanel` after z50, the population step is skipped. No such override was found in the current codebase.

---

## 4. Settings Modal (gear icon → `openSettings()`)

**Status**: Fully wired.  
- `openSettings(tab)` and `closeSettings()` defined in `runtime.js` (lines 318, 331)  
- Backdrop click closes: `if (e.target.id === 'settingsBackdrop') closeSettings()`  
- Exposed as `window.openSettings` / `window.closeSettings`  
- All settings tabs (API, Models, Memory, Billing, Legal, Account) have content rendered  

---

## 5. Send / Voice Input

**Status**: Wired.  
- `#nxVoiceBtn` → `toggleVoice()` in `runtime.js`  
- `#taskInput` `oninput` → `p4OnTaskInput()` (AI suggestions) and `p6OnTaskType()` (plan type)  
- File attachment inputs (`#nxFileInput`, `#nxImageInput`, `#nxFolderInput`) → `handleFileUpload()` / `nxHandleFolderUpload()`  

---

## 6. Tab Bar (`nxSetTab`)

**Status**: Fully wired.  
- All 4+2 tabs call `nxSetTab(name)` defined in `workspace.js`  
- Tab activation dispatches `nx:tab:<name>` custom event for lazy-mount modules (Intel, Govern tabs)  

---

## 7. HITL Approve/Reject Buttons

**Status**: Wired via `nx-z28-operator.js` `handleHitlRequired()` / `handleDecisionEvent()`.  
- Decision sent via `POST /api/hitl/decision`  
- Toast feedback on success/failure  

---

## Summary

| Category | Count | Status |
|---|---|---|
| Fully wired | 6 | ✅ |
| Fixed in Z56 | 1 | ✅ |
| Dead / deferred | 0 | — |

All interactive surfaces are now traceable to real handlers. No dead buttons or disconnected controls remain in the main workspace UI.
