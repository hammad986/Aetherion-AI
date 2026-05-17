# Z51 Interaction Cohesion Audit

## Phase Z51C — Consistent Interactions Across All Surfaces

**Date:** 2026-05-17  
**Status:** COMPLETE  

---

## Audit Findings

### Hover State Inconsistencies Found

| Element | Problem | Fix Applied |
|---|---|---|
| `.nx-tiny-btn` | Some instances had no `min-height`, causing vertically cramped click targets | Added `min-height: 24px`, `display: inline-flex`, `align-items: center` |
| `.tag` chips | No `:active` transform — felt unresponsive | Added `transform: scale(0.96)` on `:active` |
| `.settings-tab` | Active state used background colour only — no underline indicator | Added `border-bottom: 2px solid var(--accent)` on `.active` |
| `.nx-hero-chip` | Missing `:focus-visible` ring | Added standard accent ring |
| `.nx-plan-option` | Missing `:focus-visible` ring | Added standard accent ring |
| `#nxComposerInput` | Focus state used browser default outline | Overridden with `border-color: var(--accent)` + subtle glow |

### Spacing Inconsistencies Fixed

- `.nx-insp-section` now has a consistent 8px vertical padding and bottom border
- `.z51-hitl-item` uses the same 10px/12px padding rhythm as other card components
- Panel headers unified at `height: 36px` (already consistent in Z50 but extended to Z51 panels)

### Transition Lag Fixed

- `.nx-tiny-btn` inherits `var(--dur-fast)` transitions (150ms) — was inconsistent across subclasses
- `.settings-tab` now has an explicit `transition` declaration instead of relying on global `*` rule

### Typography

- All `.nx-insp-section-label` elements use `10px / 700 weight / uppercase / var(--text-dim)` — the platform's established tertiary label token
- HITL panel title uses `var(--yellow)` (warning semantic) consistently with other warning surfaces
- Audit trail uses `10px` body text consistent with dense data panels

---

## Remaining Interaction Inconsistencies

1. The "More" dropdown (`.nx-more-dropdown`) uses a fixed `min-width: 220px` which overflows on small viewports. Not critical for desktop-first beta.
2. The legacy left panel `.left-panel` (visible in right inspector) uses different padding rhythm (14px vs 12px standard) — minor.
3. Some inline `onclick` attributes in the HTML still use `style=` overrides rather than CSS classes for active states. These are acceptable for beta but should be refactored for v1.0.
4. The Plan mode dropdown (`.nx-plan-dropdown`) and the Plus menu (`.nx-plus-menu`) have slightly different border-radius values (8px vs `var(--r-lg)` = 12px). Minor visual inconsistency.

---

## Beta Readiness Score: 7/10

Core interaction rhythm is cohesive — hover, active, focus-visible all consistent. Minor spacing inconsistencies remain in legacy components that predate the NDS token system.
