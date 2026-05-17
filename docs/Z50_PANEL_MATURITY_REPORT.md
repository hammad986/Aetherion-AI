# Z50 Panel Maturity Report

## Phase Z50E — NavRail & Slide Panel Operational Completeness

**Date:** 2026-05-17  
**Status:** COMPLETE  

---

## Overview

Prior to Z50, the four NavRail slide panels (Files, History, Settings, Chat) rendered as empty grey boxes. The toggle logic worked but there was no active-state tracking, no mutual-exclusion enforcement, and no keyboard-accessible dismiss.

---

## NavRail Active State Tracking

The class `z50-active` is now applied to the `.nx-nav-icon` button whose panel is currently open:

```css
.nx-nav-icon.z50-active {
  background: var(--accent-dim);
  color: var(--accent);
  box-shadow: inset 2px 0 0 var(--accent);  /* left accent stripe */
}
```

`_z50ActivePanel` is a module-private variable that tracks the currently open panel ID. Clicking the same icon a second time closes the panel (toggle behaviour). Clicking a different icon closes the current panel and opens the new one.

State survives tab switches but does not persist across full page reload (by design — `sessionStorage` only stores the last open panel name, not a full reopen).

---

## Panel Content

### Files Panel
- Search field (client-side filter over rendered items)
- File tree populated from `GET /api/files?sid=<active>` (up to 120 files)
- Clicking a file: marks it `.z50-active`, closes the panel, switches to Code tab, calls `openFileInEditor(name)` or `nxOpenFile(name)` if available
- Empty state shown when no session is active

### History Panel  
- Populated from `GET /api/sessions?limit=30` in reverse-chronological order
- Each row shows: session name, status badge (colour-coded), timestamp
- Clicking a row: closes panel, calls `loadSession(sid)` or `p4LoadSession(sid)` or sets `NX.activeSid` as fallback
- `window.z50RefreshHistory()` exposed for external use
- Error state with retry button if fetch fails

### Settings Panel
- Four quick-action buttons: Model & API Settings, Plans & Billing, Toggle Theme, Account & Security
- Live system status card (CPU %, Memory %, Session count, Online status) from `GET /api/health`

### Chat Panel
- Informational placeholder with direct "Open Chat →" button
- Closes panel and switches to Chat tab on click

---

## Keyboard Accessibility

- `Escape` key closes the active panel (registered on `document`)
- All panel close buttons (`.nx-close-btn`) are keyboard-focusable
- NavRail icon buttons receive `:focus-visible` ring via existing CSS token system
- Panel content areas have `animation: z50-slide-in-up` on entry for visual confirmation of opening

---

## Mutual Exclusion

`nxTogglePanel(panelId)` is fully overridden by Z50:

1. Close all `.nx-slide-panel` elements unconditionally
2. If `panelId === _z50ActivePanel` → close only (toggle off)
3. Otherwise → open the requested panel, update `_z50ActivePanel`, apply `z50-active` to the correct icon

This prevents the previous state where two panels could be "open" in DOM terms but only one was visible due to overlapping CSS.

---

## Panel Empty States

All panels have a consistent empty state component (`.z50-panel-empty`) shown when data is unavailable:

```
Icon (24px, 40% opacity)
Label (11px, var(--text-muted), font-weight 500)
Hint  (10px, var(--text-dim), max-width 160px, line-height 1.5)
```
