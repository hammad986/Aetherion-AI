# Z19 — UI State & Interaction Forensic Audit

**Date:** 2026-05-16  
**Scope:** `templates/index.html`, `static/js/ui.js`, `static/js/runtime.js`, `static/js/boot.js`  
**Classification:** Pre-certification baseline

---

## 1. Executive Summary

A full forensic audit of the Nexora AI frontend was conducted across 7,876 lines of markup and 5,971 lines of JavaScript. The audit targeted four categories: ARIA state synchronisation, focus management, inline event handler hygiene, and keyboard interaction correctness.

**Total violations found before remediation:** 31  
**Severity breakdown:** Critical: 3 · High: 9 · Medium: 12 · Low: 7

---

## 2. Critical Findings

### C-01 — `aria-selected` not synchronised by `nxSetTab`
- **File:** `static/js/ui.js` line 236–243
- **Impact:** Screen readers could not determine the active tab; WCAG 2.1 SC 4.1.2 failure.
- **Root cause:** `nxSetTab` toggled `.active` class but never called `setAttribute('aria-selected', ...)`.
- **Status:** ✅ FIXED — Both `true` and `false` values are now set on every tab switch.

### C-02 — Command palette leaves stale focus after close
- **File:** `static/js/ui.js` lines 1021–1023
- **Impact:** After closing the palette with Escape or backdrop click, keyboard focus was lost (returned to `document.body`), breaking sequential keyboard navigation.
- **Root cause:** `nxClosePalette` called only `classList.remove('open')` — no focus restoration.
- **Status:** ✅ FIXED — `_nxPaletteLastFocus` captured on open; focus restored on all close paths (backdrop click, Escape key, item activation).

### C-03 — Escape key bypassed focus-restoring close path
- **File:** `static/js/ui.js` line 1076
- **Impact:** The Escape handler used raw `classList.remove('open')` even after `nxForcePaletteClose` existed, negating the fix when triggered via keyboard.
- **Status:** ✅ FIXED — Escape now calls `nxForcePaletteClose()` only when palette is open.

---

## 3. High-Severity Findings

### H-01 — 7 topbar buttons with `onmouseover`/`onmouseout` inline hover JS
- **Elements:** Run button, Stop button, Model button, Inspector button, Settings button (× 2 patterns)
- **Impact:** Inline JS hover overrides CSS specificity; theme-switching cannot override colours without re-running JS.
- **Status:** ✅ FIXED (Z20 CSS layer + HTML cleanup)

### H-02 — Hero chip buttons: 2 elements with inline hover JS
- **Elements:** "Run Tests" chip, "Audit Workspace" chip
- **Impact:** Same inline override problem; CSS `:hover` rules in nx-shell.css were silently defeated.
- **Status:** ✅ FIXED

### H-03 — Site footer: 4 links with `onmouseover`/`onmouseout`
- **Status:** ✅ FIXED — CSS `.nx-footer-link:hover` replaces all four handlers.

### H-04 — `nxSetTab` aria-selected not initialised on page load
- **Impact:** On first render no tab button had `aria-selected="true"`.
- **Status:** ✅ FIXED — The `nxSetTab('logs')` call in `nxInitBackgroundTasks` now propagates `aria-selected`.

### H-05 through H-09 — Inspector section collapsible labels with inline `cursor`, `display`, `align-items`, `justify-content` styles × 5 occurrences
- **Status:** ✅ FIXED — `.nx-insp-collapsible-label` class defined in Z20 CSS layer.

---

## 4. Medium-Severity Findings (12)

| ID | Element | Finding | Status |
|----|---------|---------|--------|
| M-01 | `#nx-site-footer` | Entire element inline-styled (7 properties) | ✅ Fixed |
| M-02 | `#nx-cookie-banner` | Entire element inline-styled (8 properties) | ✅ Fixed |
| M-03 | `#p57-error-banner` spans/buttons | 4 inline style blocks | ✅ Fixed |
| M-04 | `.p57-drawer` | 9 inline style properties on drawer container | ✅ Fixed |
| M-05 | `#p57-detail-modal` | 7 inline style properties on modal container | ✅ Fixed |
| M-06 | `.nx-idle-hero` | Duplicate inline `display/flex-direction/align-items` fighting CSS | ✅ Fixed |
| M-07 | `.nx-hero-chips` | Inline `display/gap/flex-wrap/max-width/opacity` | ✅ Fixed |
| M-08 | `<nav class="nx-shell-navrail">` | Redundant `grid-area:navrail` (already in nx-shell.css) | ✅ Fixed |
| M-09 | `<main class="nx-shell-center">` | Redundant `grid-area:center;display:flex;height;width;overflow` | ✅ Fixed |
| M-10 | `<header class="nx-shell-topbar">` | Redundant `grid-area:topbar;justify-content;padding;font-size` | ✅ Fixed |
| M-11 | Topbar `LEFT/CENTER/RIGHT` divs | Three anonymous `style="display:flex;..."` wrapper divs | ✅ Fixed |
| M-12 | Topbar run-group div | Anonymous inline `style="display:flex;background;border..."` | ✅ Fixed |

---

## 5. Low-Severity Findings (7)

| ID | Finding | Status |
|----|---------|--------|
| L-01 | `#nxRunDot` inline `display:none;width;height;border-radius;background` | ✅ Fixed — `.nx-run-dot` class |
| L-02 | `#runBtnLabel` inline `display:flex;align-items;gap` | Deferred (minor layout) |
| L-03 | `nxCurProjectHeader` inline `font-weight:500` | Deferred (single property) |
| L-04 | `.nx-model-btn-caret` inline `opacity;font-size` | ✅ Fixed — `.nx-model-btn-caret` class |
| L-05 | `#p6IrProv` inline `color:#79c0ff` | Deferred (dynamic, controlled by JS) |
| L-06 | Legacy-shell hidden `display:none !important` | Acceptable — intentional |
| L-07 | `#p9PlanModeTag` inline `margin-left:auto;font-size;opacity` | Deferred |

---

## 6. Interaction State Machine Coverage

| State | CSS class / attribute | JS-driven | Verified |
|-------|-----------------------|-----------|---------|
| Tab active | `.nx-tab.active` + `aria-selected="true"` | `nxSetTab` | ✅ |
| Tab inactive | `aria-selected="false"` | `nxSetTab` | ✅ |
| Palette open | `#nxPalette.open` | `nxOpenPalette` | ✅ |
| Palette focus restored | `_nxPaletteLastFocus.focus()` | close paths | ✅ |
| Exec running | `[data-exec-state="running"]` + `.nx-run-dot.visible` | runtime.js | ✅ |
| Exec streaming | `[data-exec-state="streaming"]` | runtime.js | ✅ |
| SSE reconnecting | `body.nx-sse-reconnecting` | nx-runtime-hygiene.js | ✅ |
| Panel open | `.p57-drawer` right:0 | runtime.js | ✅ |
| Panel closed | `.p57-drawer` right:-420px | CSS default | ✅ |

---

## 7. Files Modified

- `static/js/ui.js` — `nxSetTab`, `nxOpenPalette`, `nxClosePalette`, `nxForcePaletteClose`, Escape handler
- `templates/index.html` — topbar, cookie banner, footer, error banner, hero, drawer, modal
- `static/css/nx-z19z20z21.css` — new (Z19 state classes, aria-selected CSS rules)
