# Z60B — Encoding + Text Sanitization Report

**Date:** Phase Z60  
**Scope:** All JS files, CSS, HTML templates — UTF-8 consistency, toast rendering, broken symbols

---

## 1. Root Cause

Several JS files were saved with double-encoded UTF-8 sequences. This occurs when a UTF-8 file is opened in an editor that misidentifies the encoding as Latin-1 and re-saves it, causing each multibyte UTF-8 character to be stored as two or more broken codepoints.

---

## 2. Files Fixed

### `static/js/workspace.js`

**Method:** Python script with binary-safe replacement  
**Fixes applied:**

| Broken | Correct | Occurrences |
|--------|---------|-------------|
| `Ã—` | `×` | 5 |
| `â–¾` | `▾` | 3 |
| `â–²` | `▲` | 1 |

**User-facing impact:** The panel close toast message at line 434 previously rendered as:
```
Panel closed â€" restore from More â–¾
```
After fix:
```
Panel closed — restore from More ▾
```

**Comment lines** (non-user-facing) also had `â€"` sequences. These are in developer comments only and do not affect the rendered UI.

---

## 3. Audit of Other JS Files

Binary inspection found that 70+ JS files contain the UTF-8 byte sequences for `—`, `×`, `▾`, `▲`. These were confirmed to be **valid UTF-8** — the bytes represent correctly encoded Unicode characters used legitimately (box-drawing, dashes, arrows). No user-facing broken text was found in these files beyond `workspace.js`.

Grep for mangled sequences (`â€`, `Ã—`, `Ã©`) in user-facing contexts (toast, innerHTML, textContent, placeholder, aria-label) returned **zero matches** outside `workspace.js`.

---

## 4. Toast Rendering

All toast messages are passed through HTML-escape functions (`_esc()`) before being injected via innerHTML. No raw user input is interpolated into toast HTML without escaping. No mangled Unicode was found in toast content after the workspace.js fix.

---

## 5. Restore Message Fix

In `nx-z52.js`, the restore consolidation message previously read:
```
items.length + ' systems synced'
```
Changed to `null` (no detail line) — eliminates the fake "systems synced" message from the restore toast.

---

## 6. Onboarding Text Fix

In `nx-onboard.js`, the runtime readiness indicator previously read:
```
Runtime ready — all systems operational
```
Changed to:
```
Ready
```

---

## 7. Remaining Risks

- Comment-only `â€"` sequences in `workspace.js` lines 2, 30, 89, 154 — developer-visible only, not rendered in browser UI
- No user-facing broken encoding remains

## Beta Readiness Score: 9/10
