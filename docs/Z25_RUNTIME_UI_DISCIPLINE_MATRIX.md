# Z25 — Runtime UI Discipline Matrix

**Phase:** Z25C — Runtime UI Discipline Verification  
**Date:** 2026-05-16  
**Format:** Per-component discipline audit matrix  
**Status:** COMPLETE

---

## How to Read This Matrix

Each row is a UI component or system. Columns grade discipline across 7 dimensions:

| Symbol | Meaning |
|---|---|
| ✅ | Fully disciplined — meets standard |
| ⚠️ | Partially disciplined — known gap but functional |
| ❌ | Undisciplined — active defect |
| — | Not applicable |

**Dimensions:**
1. **Token** — Uses design tokens, not hardcoded values
2. **Type** — Typography from NDS scale
3. **A11y** — Accessible (ARIA, focus, contrast)
4. **State** — Handles all interaction states (idle/hover/active/disabled)
5. **Empty** — Has a defined empty/idle state
6. **Motion** — Animation is purposeful, reduced-motion safe
7. **Code** — No inline styles, clean HTML

---

## Component Matrix

### Shell Chrome

| Component | Token | Type | A11y | State | Empty | Motion | Code | Notes |
|---|---|---|---|---|---|---|---|---|
| Topbar (`nx-shell-topbar`) | ✅ | ✅ | ✅ | ✅ | — | ✅ | ⚠️ | Minor inline survivors |
| Nav rail | ✅ | — | ⚠️ | ✅ | — | ✅ | ✅ | No icon tooltips |
| Left panel | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ | Empty state CSS ready |
| Right panel (Inspector) | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ | No idle indicator |
| Split gutter | ✅ | — | — | ✅ | — | — | ✅ | Token-referenced after Z25B |
| Dock / footer | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | Fades during execution |

### Execution Controls

| Component | Token | Type | A11y | State | Empty | Motion | Code | Notes |
|---|---|---|---|---|---|---|---|---|
| Run button | ✅ | ✅ | ✅ | ✅ | — | — | ✅ | Accent-coloured, 600 weight |
| Stop button | ✅ | ✅ | ✅ | ✅ | — | — | ✅ | Red, correct |
| Run group container | ✅ | — | — | ✅ | — | — | ✅ | Exec-state border/glow |
| Run dot indicator | ✅ | — | ⚠️ | ✅ | — | ✅ | ✅ | No aria-label for state |
| Model selector | ✅ | ✅ | ✅ | ✅ | — | — | ✅ | — |
| Plan badge | ✅ | ✅ | ⚠️ | ✅ | — | — | ✅ | Dropdown ARIA partial |

### Workspace Content

| Component | Token | Type | A11y | State | Empty | Motion | Code | Notes |
|---|---|---|---|---|---|---|---|---|
| Workspace center (idle) | ✅ | ✅ | — | — | ⚠️ | — | — | CSS ready; HTML pending |
| Log pane | ✅ | ✅ | ✅ | ✅ | ⚠️ | — | ✅ | Empty CSS defined |
| Code editor (Monaco) | — | ✅ | ✅ | ✅ | ⚠️ | — | ✅ | Monaco handles its own |
| Terminal (xterm) | — | ✅ | ✅ | ✅ | ⚠️ | — | ✅ | xterm handles its own |
| Live tab | ✅ | ✅ | ⚠️ | ✅ | ⚠️ | ✅ | ✅ | Pipeline bar no ARIA |
| Session list | ✅ | ✅ | ✅ | ✅ | ⚠️ | — | ✅ | Empty CSS defined |
| Task chip bar | ✅ | ✅ | ⚠️ | ✅ | — | ✅ | ✅ | Chip ARIA partial |

### Auth Gate

| Component | Token | Type | A11y | State | Empty | Motion | Code | Notes |
|---|---|---|---|---|---|---|---|---|
| Auth card container | ✅ | ✅ | ✅ | — | — | — | ⚠️ | 12 inline style survivors |
| Login form | ✅ | ✅ | ✅ | ✅ | — | — | ⚠️ | Enter key handler inline |
| Signup form | ✅ | ✅ | ⚠️ | ✅ | — | — | ❌ | TOS label fully inline |
| Forgot password form | ✅ | ✅ | ✅ | ✅ | — | — | ⚠️ | `display:none` inline |
| Auth tabs | ✅ | ✅ | ❌ | ✅ | — | — | ✅ | No role="tab" / tablist |
| OAuth buttons | ✅ | ✅ | ✅ | ✅ | — | — | ✅ | — |
| Auth footer | ✅ | ✅ | ✅ | — | — | — | ✅ | — |

### Notifications & Banners

| Component | Token | Type | A11y | State | Empty | Motion | Code | Notes |
|---|---|---|---|---|---|---|---|---|
| Toast (success/error/info) | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | Token-based bg after Z25B |
| Cookie banner | ✅ | ✅ | ⚠️ | ✅ | — | — | ⚠️ | Dismiss btn no aria-label |
| Verify email banner | ✅ | ✅ | ✅ | ✅ | — | — | ⚠️ | Inline colour survivors |
| Error banner | ✅ | ✅ | ⚠️ | ✅ | — | — | ⚠️ | Close btn no aria-label |
| Notification bell panel | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | Empty state handled |

### Modals & Overlays

| Component | Token | Type | A11y | State | Empty | Motion | Code | Notes |
|---|---|---|---|---|---|---|---|---|
| Command palette | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ | Section emit pending |
| Modal system | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | Focus trap implemented |
| Settings modal | ✅ | ✅ | ✅ | ✅ | — | — | ✅ | — |
| Onboarding card | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | NDS pattern used |

### Animation System

| Component | Token | Type | A11y | State | Empty | Motion | Code | Notes |
|---|---|---|---|---|---|---|---|---|
| Global transitions | ✅ | — | ✅ | — | — | ✅ | — | GPU-only props |
| Run dot pulse | ✅ | — | ✅ | ✅ | — | ✅ | ✅ | Reduced-motion safe |
| Toast slide-in | ✅ | — | ✅ | — | — | ✅ | ✅ | translateX only |
| Loading bar | ✅ | — | ✅ | ✅ | — | ✅ | ✅ | Reduced-motion handled |
| Panel transitions | ✅ | — | ✅ | ✅ | — | ✅ | ✅ | Suppressed on reduce |
| Logo hover | ✅ | — | — | ✅ | — | ✅ | ✅ | Spring easing |

### CSS Architecture

| Concern | Status | Notes |
|---|---|---|
| Single token source | ✅ | `nds-tokens.css` is canonical; bridge in Z25B aligns legacy vars |
| Load order correctness | ⚠️ | `base.css` loads before motion.css and overrides NDS; bridge compensates |
| No token re-definition outside nds-tokens | ❌ | `motion.css` still redefines `--bg`, `--accent`, etc. (legacy) |
| No hardcoded colours in layout.css | ⚠️ | Split gutter still has `#21262d` in some rules |
| No inline styles in templates | ⚠️ | 12 survivors in auth gate; 3 in banners |
| Animation uses GPU compositing only | ✅ | All Z25B animations: transform + opacity |
| Reduced-motion coverage | ✅ | Both nx-a11y.css and Z25B suppress animations |
| Z-index uses tokens | ⚠️ | stability.css uses 999998/999999 — outside declared token range |

---

## Score Summary

| Category | Score | Max |
|---|---|---|
| Shell chrome | 6.2 | 7 |
| Execution controls | 6.3 | 7 |
| Workspace content | 5.1 | 7 |
| Auth gate | 5.0 | 7 |
| Notifications & banners | 5.6 | 7 |
| Modals & overlays | 6.6 | 7 |
| Animation system | 6.7 | 7 |
| CSS architecture | 5.4 | 8 |
| **TOTAL** | **47.0** | **57** | 
| **PERCENTAGE** | **82%** | | 

---

## Discipline Regression Risk Areas

The following are most likely to regress in future phases if not actively maintained:

1. **Token fragmentation** — `motion.css` still redefines legacy variables. Any new CSS that uses `--bg` without knowing the load order will get an unexpected value. **Mitigation:** Add a comment in `motion.css` noting the Z25B bridge supersedes its token definitions.

2. **Inline style creep** — Auth gate and banners still have inline survivors. New HTML edits may introduce more. **Mitigation:** Add a grep CI check for `style="` in templates.

3. **Z-index range overflow** — `stability.css` uses values > 10000 which are outside the declared z-index token range. **Mitigation:** Consolidate into `--nx-z-*` tokens.

4. **Empty state HTML debt** — CSS patterns are defined but HTML is not yet populated. Future feature additions may ship without empty states. **Mitigation:** Define empty state as a required element in the component checklist.

---

## Discipline Standards (Post-Z25)

For all future CSS / HTML contributions to Nexora:

| Standard | Rule |
|---|---|
| Colour values | MUST use `--nds-*` tokens or their `--legacy` bridge aliases |
| Typography | MUST use `--nds-type-*` tokens or equivalent px values from the scale |
| Spacing | MUST use `--nds-sp-*` tokens or 4px-grid multiples |
| Animations | MUST be GPU-composited (transform/opacity only); MUST include reduced-motion suppression |
| Inline styles | PROHIBITED in template files; use CSS classes |
| onclick style mutations | PROHIBITED; use `.nx-hidden` / `.nx-visible-*` class toggling |
| New components | MUST define empty/idle state |
| ARIA | All interactive components MUST have correct roles, labels, and state attributes |

*Z25C Runtime UI Discipline Matrix complete.*
