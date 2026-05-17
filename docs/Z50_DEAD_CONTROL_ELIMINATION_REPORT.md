# Z50 Dead Control Elimination Report

## Phase Z50A/B — Operational Interaction Realism

**Date:** 2026-05-17  
**Status:** COMPLETE  

---

## Audit Summary

A full pass was performed on every interactive element in `templates/index.html` and `static/js/ui.js` to identify controls that were rendered but not wired to any functional behaviour.

---

## Dead Controls Found & Fixed

| Control | Location | Problem | Fix Applied |
|---|---|---|---|
| Mode select (`Autonomous / Architect / Debug`) | Composer toolbar | Rendered but not bound to any state | Wired via `z50WireExecSelects()` → sets `NX.execMode`, persists to `localStorage`, shows change highlight |
| Scope select (`Workspace / Active File`) | Composer toolbar | Rendered but not bound | Same as above → sets `NX.execScope`, persists |
| Cookie banner Accept button | `#nx-cookie-banner` | `nxAcceptCookies()` was callable but state was reset on every page load | Overrode `nxAcceptCookies()` to write `nx_cookie_accepted` to `localStorage`; banner reads it on boot and stays hidden |
| Cookie banner Dismiss button | `#nx-cookie-banner` | `.nx-cookie-dismiss` click had inline style with no persistence | Wired `.nx-cookie-dismiss` click → `z50DismissCookieBanner(false)`, adds `z50-hiding` CSS class, removes after 220ms |
| NavRail "Files" panel | `#nxPanel-files` | `#nxPanelContent-files` was empty | Built file tree with search, populates from `/api/files?sid=…` |
| NavRail "History" panel | `#nxPanel-history` | `#nxPanelContent-history` was empty | Populates from `/api/sessions?limit=30`, clickable to restore session |
| NavRail "Settings" panel | `#nxPanel-settings` | `#nxPanelContent-settings` was empty | Renders quick-action buttons + live system health data |
| NavRail "Chat" panel | `#nxPanel-chat` | `#nxPanelContent-chat` was empty | Shows guide with direct link to Chat tab |
| `nxTogglePanel()` | `static/js/ui.js` | No mutual-exclusion, no active-nav tracking | Overridden in Z50 to ensure only one panel open, deactivates all icons, tracks `_z50ActivePanel` |
| `nxClosePanels()` | `static/js/ui.js` | Existed but did not clear nav icon `.active` state | Re-implemented to clear all `z50-active` classes |

---

## Persistence Strategy

All user preferences are written to `localStorage` under namespaced keys:

- `nx_cookie_accepted` — set when cookie banner is dismissed
- `nx_exec_mode:…` — last selected mode per toolbar
- `nx_exec_scope:…` — last selected scope
- `nx_navrail_panel` → `sessionStorage` — which panel was open last

Controls that previously silently dropped state now show a visual diff (`.z50-changed` border-color: `var(--accent)`) whenever the value differs from default.

---

## Regression Notes

- `nxSetTab()` and all existing tab wiring are unchanged.
- The Mode/Scope selects emit a `toast()` on change only when switching Mode (not Scope) to avoid noise.
- NavRail panel content is only populated once per session (guarded by `data-z50loaded`), then kept live with targeted refresh calls on execution state changes.
