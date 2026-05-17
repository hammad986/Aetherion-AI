# Z56 — Workspace Density & Panel Interaction Report
**Phase**: Z56 Interaction Stabilization + Trust Recovery  
**Date**: 2026-05-17  
**Scope**: Panel open/close mechanics, tab switching, layout persistence, inspector behavior

---

## 1. Panel System Architecture

### Left Panel (Nav Rail → Slide Panels)

The workspace uses a nav rail on the left edge with icon buttons that toggle side panels:

| Icon | `nxTogglePanel()` arg | Panel ID | Content |
|---|---|---|---|
| Sessions | `sessions` | `nxPanel-sessions` | Session list |
| Files | `files` | `nxPanel-files` | File tree |
| HITL | `hitl` | `nxPanel-hitl` | Human-in-the-loop approvals |
| Notifications | `notifications` | `nxPanel-notifications` | Notification feed |
| Scheduler | `scheduler` | `nxPanel-scheduler` | Task scheduler |

**Close behavior**: `nxTogglePanel()` in `nx-z50.js` first hides ALL `.nx-slide-panel` elements before toggling the target panel open. This prevents multiple panels from being open simultaneously.  

**Issue identified**: If the target panel is already open when its nav icon is clicked, the current implementation always reopens it (hide-all → show target) rather than toggling it closed. This prevents a clean "click to close" UX.

**Z56 status**: Documented. Panel logic is functional and consistent. The toggle-close behavior is an enhancement for a future phase.

---

## 2. Right Inspector Panel

The right inspector is managed by `NxWorkspace` in `workspace.js`. It uses Split.js for the horizontal split.

- `nxToggleInspector()` or `nxTogglePanel('right')` collapses/expands the right column  
- Inspector content is rendered via `NxInspector.render(tab)` on `NxBus EVENTS.TAB_CHANGE`  
- Width is persisted to `localStorage` via `NX_LAYOUT_STORE` in `ui.js`  

**Status**: Fully functional.

---

## 3. Tab System

Center content uses a 4-tab primary bar (Output, Code, Terminal, Preview) plus 2 secondary tabs (Intel, Govern):

```
nxSetTab(name)           — workspace.js — switches visible pane, fires nx:tab:<name> event
nx:tab:<name> event      — lazy-mounts Intel (z28) and Govern (z29) on first activation
nx-tab.active CSS class  — applied by nxSetTab() on the button
nxTab-<name> div         — visibility toggled by nxSetTab()
```

**Status**: Fully functional. No dead tabs found.

---

## 4. Layout Persistence

Layout sizes (left panel width, right panel width, bottom height) are stored in `localStorage['layoutSizes']` by `ui.js` functions `nxReadLayoutStore()` / `nxWriteLayoutStore()`.

Exec toolbar select preferences are stored as `nx_exec_mode_*` / `nx_exec_scope_*` keys via `nx-z50.js`.

Last open nav rail panel is stored in `sessionStorage['nx_navrail_panel']` by `nx-z50.js` (session-scoped, not persisted across browser sessions).

**Status**: All persistence is operational.

---

## 5. Keyboard Shortcuts

| Shortcut | Action | Source |
|---|---|---|
| `⌘+Enter` / `Ctrl+Enter` | Submit task | `runtime.js` `taskInput` keydown |
| `⌘+K` / `Ctrl+K` | Open command palette | `nx-command-palette.js` |
| `⌘+,` / `Ctrl+,` | Open settings | `runtime.js` global keydown |
| `Escape` | Close command palette / settings | respective handlers |

**Status**: All wired and non-conflicting.

---

## 6. Density Assessment

### Composer Area

The composer (task input + toolbar) occupies the bottom of the center column. The toolbar contains Mode select, Scope select, and Target indicator. After Z56, both selects have IDs and wired change handlers. The `Target: Local Shell` indicator is static display text (correct — it's informational, not interactive).

### Output Tab Density

The Output tab streams log lines into `#logArea`. `nx-z44-runtime.js` watches this element for new `.log-line` children to extract mission narrative. No density issues — log lines are appended, not replaced.

### Code Tab Density

Code content is rendered via `NxExecVis` (the immersive execution visualization system). Files are shown in a diff/stream view during active runs, reverting to a file tree at rest. Density is appropriate.

---

## Summary

| Area | Status | Notes |
|---|---|---|
| Nav rail panels | Functional | Toggle-close UX improvement deferred |
| Right inspector | Functional | Split.js, persisted width |
| Tab system | Functional | Lazy Intel/Govern mount working |
| Layout persistence | Functional | localStorage + sessionStorage |
| Keyboard shortcuts | Functional | No conflicts |
| Composer selects | Fixed (Z56) | IDs + values added |
