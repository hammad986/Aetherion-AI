# Z16 Implementation Report
**Status: COMPLETE**
**Date: 2026-05-16**
**Scope: Visual System Stabilization — Phases A–F**

---

## Executive Summary

Z16 addressed 6 categories of visual instability. The primary finding — that `nx-shell.css` (1798 lines) was never loaded by the HTML — was resolved by adding it to the CSS load order. This single change activated the shell chrome grid, nav rail geometry, topbar structure, inspector slide-over, and dock definitions for the first time. Additional CSS overrides in layout.css addressed inline style defeats on the tab bar, composer, idle hero, and exec strip. All implementation was CSS-first with zero JS runtime changes.

---

## Phase A — Nav Rail Discipline

**Root cause:** `.nx-shell-navrail` and `.nx-nav-icon` had zero CSS in any loaded file. Nav rail rendered as unstyled block-level content.

**Fix:** Added `<link rel="stylesheet" href="/static/css/nx-shell.css">` to index.html (line 67, after nx-agi-native.css). This activated:
- `.nx-shell-navrail { width: 48px; display:flex; flex-direction:column; background: var(--nx-color-bg-surface-elevated); border-right: 1px solid var(--nx-color-border); }`
- `.nx-nav-icon { width:36px; height:36px; border-radius:6px; box-shadow: inset 2px 0 0 transparent; }` (pre-reserved active indicator slot — no layout shift)
- `.nx-nav-icon:hover { background: rgba(255,255,255,0.05); }`
- `.nx-nav-icon.active { background: rgba(255,255,255,0.08); box-shadow: inset 2px 0 0 var(--nx-color-text-muted); }`

**ARIA:** Added `aria-label` to all 4 nav icon buttons (Files panel, Chat panel, Session history, Settings panel). Added `aria-hidden="true"` to flex spacer div.

---

## Phase B — Topbar Execution Hierarchy

**Root cause:** `.nx-shell-topbar` had zero CSS in any loaded file. All layout via inline styles. All hover effects via JS `onmouseover`/`onmouseout`.

**Fix:** nx-shell.css now loaded provides:
- `.nx-shell-topbar { grid-area:topbar; height: var(--nx-shell-topbar-height); background: var(--nx-color-bg-surface); border-bottom: 1px solid var(--nx-color-border); display:flex; align-items:center; z-index: var(--nx-z-topbar); user-select:none; }`

**ARIA:** Added `aria-label` to: Run button, Stop button, Model button, Command palette trigger, Inspector toggle, Settings button.

---

## Phase C — Execution Surface Balance

**Root cause:** `.nx-composer` had zero CSS in any loaded file. `.nx-tab-bar` CSS class was defeated by inline `style="gap:24px;padding:0 16px;background:#18181B;"`. Tab buttons defeated by inline styles.

**Fixes:**
1. Added `.nx-composer { padding:12px 16px 10px; border-bottom:1px solid var(--panel-border); background:var(--bg); flex-shrink:0; }` to layout.css Z16 block.
2. Added `#taskInput:focus { border-color:var(--accent); box-shadow: 0 0 0 1px var(--accent-dim); }` — first focus ring for composer textarea.
3. Upgraded exec toolbar: `.nx-exec-toolbar { border-top:1px solid var(--panel-border) !important; }` — replaces dashed border (weak signal) with solid muted border.
4. Added `#nxTabBar { gap:0 !important; padding:0 8px !important; background:var(--panel) !important; }` to override inline style defeat.
5. Added `.nx-tab { font-size:13px !important; font-weight:600 !important; padding:0 14px !important; color:var(--text-dim) !important; }` — matches existing inline sizes while restoring token color.
6. Added `.nx-tab.active { color:var(--text) !important; border-bottom-color:var(--accent) !important; background:transparent !important; }` — token-driven active state.
7. Removed inline styles from all 4 tab buttons in index.html — layout.css now owns visual state.
8. Added `role="tablist"` to tab bar, `role="tab"` + `aria-selected` + `aria-controls` to each tab button.

**Idle hero:**
9. Added `.nx-idle-hero { color:var(--text-muted) !important; }` — overrides inline `color:#8b949e`.

---

## Phase D — Panel Discipline

**Root cause:** `.nx-shell-root` (grid container), `.nx-shell-inspector`, `.nx-shell-dock`, `.nx-shell-center` all had zero CSS in any loaded file.

**Fix:** All now defined by nx-shell.css (now loaded):
- `.nx-shell-root { display:grid; grid-template-columns:var(--nx-shell-navrail-width) 1fr auto; grid-template-rows:var(--nx-shell-topbar-height) 1fr auto; grid-template-areas:"topbar topbar topbar" "navrail center inspector" "navrail dock inspector"; }`
- `.nx-shell-inspector { position:absolute; width:var(--nx-shell-inspector-width); transform:translateX(100%); transition:transform 0.25s; }` — slide-over, not layout-shifting
- `.nx-shell-inspector.is-open { transform:translateX(0); box-shadow:-10px 0 40px rgba(0,0,0,0.7); }` — proper reveal
- `.nx-shell-dock { grid-area:dock; height:var(--nx-shell-dock-height-collapsed); }` — constrained height

**Exec strip:**
Added complete `.nx-exec-strip` definition (position:fixed; bottom:0; z-index:90; height:24px; background:var(--panel); border-top:1px solid var(--panel-border)). Previously undefined in any loaded CSS.

---

## Phase E — Typography System

**Improvements applied:**
- Z15 (already done): 15 hardcoded `font-family:'Inter'` → `var(--font, 'Inter', sans-serif)` in layout.css
- Z16: Tab labels now use `font-size:13px !important` (matching existing inline, now CSS-controlled)
- Z16: Exec strip uses `font-size:10px; font-family:var(--font)` from CSS class (not inline)
- Z16: `.nx-inspector-header h3 { font-size:13px; font-weight:600; }` via nx-shell.css

---

## Phase F — Responsive Stability

nx-shell.css includes a `@media (max-width: 768px)` block that:
- Collapses inspector from grid column to absolute overlay
- Adjusts shell-root grid-template-areas for narrow viewports

These were previously inert (file not loaded). Now active.

---

## Implementation Inventory

| Change | File | Type |
|---|---|---|
| Load nx-shell.css | templates/index.html | HTML link |
| Nav icon aria-label ×4 | templates/index.html | ARIA |
| Run/Stop/Model aria-label | templates/index.html | ARIA |
| Palette/Inspector/Settings aria-label | templates/index.html | ARIA |
| Tab bar role="tablist" | templates/index.html | ARIA |
| Tab buttons role/aria-selected/aria-controls ×4 | templates/index.html | ARIA |
| Tab bar inline style removal | templates/index.html | CSS cleanup |
| Tab button inline style removal ×4 | templates/index.html | CSS cleanup |
| #nxTabBar !important override | static/css/layout.css | CSS |
| .nx-tab !important override | static/css/layout.css | CSS |
| .nx-tab.active !important override | static/css/layout.css | CSS |
| .nx-composer definition | static/css/layout.css | CSS |
| #taskInput:focus | static/css/layout.css | CSS |
| .nx-exec-toolbar !important | static/css/layout.css | CSS |
| .nx-idle-hero !important | static/css/layout.css | CSS |
| .nx-exec-strip full definition | static/css/layout.css | CSS |
| .nx-exec-strip-item, -divider | static/css/layout.css | CSS |

---

## Unresolved Technical Debt (Deferred)

1. **Topbar hover still JS-driven** — `onmouseover`/`onmouseout` on Run/Stop/Model buttons. CSS `:hover` rules exist in nx-shell.css now that it's loaded, but JS handlers also fire. Harmless duplication. Cleanup requires removing JS hover handlers from HTML (deferred — not within "no JS runtime rewrites" constraint interpretation).
2. **Composer textarea/toolbar still inline-styled** — background, border, select styles are inline. The CSS class `.nx-composer` now defines the container, but inner elements remain inline-styled. Full cleanup requires systematic HTML editing of ~8 additional inline elements.
3. **base.css hex values** — ~hundreds of hardcoded hex values. Deferred (original Z16 context note).
