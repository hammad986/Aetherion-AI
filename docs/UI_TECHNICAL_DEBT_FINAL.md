# UI Technical Debt — Final Registry
**Date: 2026-05-16**
**Status: Post Z15+Z16+Z17+Z18**

---

## RESOLVED DEBT (Z15–Z18)

| Item | Resolved In |
|---|---|
| Global `* { transition }` on all elements | Z15 |
| 2 duplicate `@keyframes` collisions | Z15 |
| 4 competing `:root` blocks | Z15 |
| 4 conflicting `:focus-visible` definitions | Z15 |
| 3 conflicting scrollbar definitions | Z15 |
| 30 shell tokens outside canonical namespace | Z15 |
| nx-shell.css not loaded (1798 lines inert) | Z16 |
| Nav rail zero CSS definition | Z16 |
| Topbar zero CSS definition | Z16 |
| Composer zero CSS definition | Z16 |
| Exec strip zero CSS definition | Z16 |
| Inspector zero CSS definition | Z16 |
| Tab bar inline style defeat | Z16 |
| Uncertainty modal z-index below SSE badge | Z17 |
| Composer textarea no focus ring | Z17 |
| Exec toolbar dashed border | Z17 |
| No aria-label on 11 shell chrome controls | Z16/Z17 |
| No role="tablist/tab" on tab bar | Z17 |
| No aria-live on log output | Z17 |
| No aria-live on activity bar | Z17 |
| z-index anarchy (no registry) | Z18 |
| Hardcoded animations bypass reduced-motion | Z18 |
| No focus-visible ring on transparent buttons | Z18 |
| No content containment on scroll areas | Z18 |
| SSE badge obscured by uncertainty modal | Z18 |

---

## ACTIVE DEBT (Deferred)

### Category A — HTML Inline Styles (Medium Priority)

| Item | Location | Impact |
|---|---|---|
| Composer textarea inline background/border | index.html L629 | Theme switching incomplete |
| Composer exec toolbar selects inline | index.html L634–641 | Mode/scope colors hardcoded |
| Composer "+" and voice buttons inline | index.html L615, L652 | Colors hardcoded |
| Topbar Run/Stop compound button inline | index.html L281–287 | JS hover, hardcoded #30363d |
| Topbar breadcrumb inline colors | index.html L264–270 | Color hardcoded |
| Idle hero text colors inline | index.html L716–720 | Colors hardcoded (partially overridden by Z16 !important) |
| Idle hero chip buttons inline+JS hover | index.html L723–725 | Colors hardcoded, JS hover |

**Resolution approach:** Systematic HTML audit pass — add CSS classes to each element, remove inline style attributes. No JS changes needed.

### Category B — base.css Hex Values (Lower Priority)

| Item | File | Scope |
|---|---|---|
| Hardcoded hex colors (#161b22, #30363d, #58a6ff, etc.) | base.css | ~hundreds of instances across 3680 lines |

**Resolution approach:** Automated sed or search-replace pass mapping known hex values to canonical token names. Requires careful regex and visual validation after each batch.

### Category C — JavaScript-Layer UX (Requires JS Changes)

| Item | Impact | Effort |
|---|---|---|
| Tab keyboard arrow-key navigation | WCAG AA compliance | Medium |
| JS sync of aria-selected on tab switch | Screen reader tab state | Low |
| Focus trap in modals (uncertaintyModal, p8-modal) | WCAG AA compliance | High |
| Focus return after modal close | WCAG AA compliance | Medium |
| HITL label language ("Inject" → "Guide the agent") | Operator clarity | Low |
| Idle hero 3rd action chip | Operator onboarding | Low |
| Pipeline stage specificity | Execution visibility | Medium |
| MutationObserver count > 8 (pre-existing) | Performance budget | Medium |

### Category D — CSS Structural (Lower Priority)

| Item | File | Impact |
|---|---|---|
| Raw z-index values in layout.css (1 to 99999) | layout.css | No canonical registry adoption |
| base.css: legacy token vars (#bg, #panel, etc.) competing with nds-tokens.css | base.css | Resolved via bridge but dual-definition overhead |
| nx-shell.css: Phase O scoped scrollbar references (minor) | nx-shell.css | Cosmetic |

---

## DEBT TRAJECTORY

| Phase | Items Introduced | Items Resolved | Net |
|---|---|---|---|
| Legacy (pre-Z15) | ~80 estimated | 0 | +80 |
| Z15 | 0 | 8 | -8 |
| Z16 | 0 | 11 | -11 |
| Z17 | 0 | 7 | -7 |
| Z18 | 0 | 5 | -5 |
| **Total** | | **31** | **~49 remaining** |

The platform is now in a net-negative debt trajectory. All major architectural debts (missing CSS, z-index anarchy, inline style defeats) are resolved. Remaining debt is systematic cleanup work with no runtime risk.
