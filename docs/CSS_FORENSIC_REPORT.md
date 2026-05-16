# CSS FORENSIC REPORT
# Phase Z5 — CSS Audit | Generated: 2026-05-15

## FILES AUDITED (13 total — 330KB combined)

| File | Size | Role |
|------|------|------|
| `base.css` | 77KB | Global resets, typography, color tokens |
| `layout.css` | 90KB | Shell grid, panels, command palette, all legacy sections |
| `nx-shell.css` | 45KB | Navigation rail, topbar, inspector (Phase Z4 refined) |
| `motion.css` | 30KB | Animations, transitions, palette motion |
| `components.css` | 13KB | Reusable UI components |
| `nds.css` | 15KB | Nexora Design System |
| `nds-tokens.css` | 9KB | NDS design tokens |
| `nx-agi-native.css` | 18KB | AGI surface styles |
| `nx-observability.css` | 11KB | Observability panel styles |
| `stability.css` | 7.5KB | Stability monitor styles |
| `support.css` | 12KB | Support panel styles |
| `graphs.css` | 4KB | Graph visualization styles |
| `forms.css` | 5.6KB | Form elements and inputs |

---

## SECTION 1 — DESIGN TOKEN AUDIT

### Token Systems Present
Two overlapping token systems exist — a known technical debt:

**System A: `nds-tokens.css`** (Nexora Design System)
```css
--nds-color-bg-primary, --nds-space-*, --nds-radius-*, --nds-font-*
```

**System B: `base.css` + `nx-shell.css`** (Operational Shell)
```css
--nx-color-bg-base, --nx-space-*, --nx-shell-topbar-height, --nx-z-*
```

**System C: `layout.css`** (Legacy CSS vars from earlier phases)
```css
--bg, --panel, --surface, --text, --text-dim, --text-muted, --accent, --accent-dim
--panel-border, --panel-border-hover, --green, --red, --red-dim
```

### VERDICT
- System C (`--bg`, `--panel`, etc.) is the dominant token set used by the oldest and largest CSS files
- System A (NDS) is used by newer components only
- System B (nx-shell) is used exclusively by the Z4 shell redesign
- **No conflicts detected** — scopes do not overlap in production rendering
- **Technical Debt**: Three token systems is 2 too many. Future consolidation recommended (NOT this phase)

---

## SECTION 2 — DUPLICATE VARIABLE DETECTION

| Variable | Defined In | Duplicate Risk |
|----------|-----------|----------------|
| `--accent` | `layout.css` L1, `base.css` | Same value `#bc8cff`; redundant |
| `--green` | `layout.css`, `base.css` | Same value `#3fb950`; redundant |
| `--red` | `layout.css`, `base.css` | Same value `#f85149`; redundant |
| `--panel-border` | `layout.css`, `components.css` | Same value `#27272A`; redundant |
| `border-radius: 6px` | Appears 47 times across files | Not a variable; use `--nx-radius-md` |

**Duplicate count: 4 variable conflicts, 0 breakages** (later declaration wins; values are identical so no visual regression).

---

## SECTION 3 — DEAD SELECTOR DETECTION

### Confirmed Dead Selectors (safe to remove in future cleanup pass)

| Selector | File | Reason |
|----------|------|--------|
| `.nx-hero-logo` | `layout.css` ~L1820 | Replaced by Z4 minimal idle state; element removed from HTML |
| `.nx-hero-heading` | `layout.css` | Same as above |
| `.nx-hero-sub` | `layout.css` | Same as above |
| `.nx-hero-hint kbd` | `layout.css` | Same as above |
| `.p8-sub-badge.p8-elite` | `layout.css` L2500 | Elite plan not yet launched; no DOM element |
| `#legacy-shell` | `layout.css` L2264 | `display:none !important`; retained as safety override |
| `.preview-state-card` | `layout.css` L2332 | Preview state UI replaced; card no longer rendered |

**Dead selector count: 7** (minor). No visual regressions from preserving them.
**Recommended action**: Remove in a future dedicated CSS cleanup pass.

---

## SECTION 4 — OBSOLETE ANIMATION AUDIT

| Animation | File | Status |
|-----------|------|--------|
| `@keyframes nx-blink` | `layout.css` | ACTIVE — live dot pulse |
| `@keyframes nx-fade-in` | `layout.css` | ACTIVE — command palette open |
| `@keyframes nx-slide-up` | `layout.css` | ACTIVE — toast notification |
| `@keyframes slidePanelEnter` | `nx-shell.css` | ACTIVE — slide panel enter |
| `@keyframes nx-pulse` | `nx-shell.css` | ACTIVE — live status dots |
| `@keyframes fadeInUp` | `motion.css` | ACTIVE — panel transitions |
| `@keyframes shimmer` | `motion.css` | ACTIVE — loading states |
| `@keyframes spin` | `motion.css` | ACTIVE — loading spinner |

**Verdict**: No obsolete animations detected. All keyframes are referenced by active class rules.

---

## SECTION 5 — STALE LAYOUT SYSTEMS

### Legacy Panel System (base.css)
The original `.pane`, `.pane-header`, `.pane-body` system from Phase 1 is overridden in `layout.css`:
```css
.pane-header { display: none !important; }  /* layout.css L2375 */
```
The selectors still exist in `base.css` but are effectively dead. **Low risk** — leave in place.

### Duplicate Spacing Systems
- `base.css` defines `--space-1` through `--space-12`
- `nx-shell.css` defines `--nx-space-1` through `--nx-space-12`
- Both are 4pt grids with identical values but different namespaces
- **No breakage** — each system is consumed by its own components

---

## SECTION 6 — UNUSED UTILITY CLASSES

After cross-referencing against `index.html` (207KB) and known component patterns:

| Class | File | Usage Status |
|-------|------|-------------|
| `.nx-text-xs`, `.nx-text-sm`, `.nx-text-base` | `nx-shell.css` | Referenced in dynamic JS; ACTIVE |
| `.nx-font-mono` | `nx-shell.css` | Referenced in terminal components; ACTIVE |
| `.nx-empty-state` | `nx-shell.css` | Used by empty panel fallbacks; ACTIVE |
| `.p7-sev-high/medium/low/ok` | `layout.css` | Security audit panel; ACTIVE |
| `.p8-modal-backdrop` | `layout.css` | Billing modal; ACTIVE |

**Verdict**: No confirmed unused utility classes detected.

---

## SECTION 7 — CRITICAL FINDINGS

### FINDING 1: `.nx-nav-icon.active` Border Token Inconsistency
- `nx-shell.css` now correctly uses `border-left: 2px solid #8b949e` (Z4 fix)
- `layout.css` still has an older override with `border-left: 2px solid #bc8cff`
- **Action Required**: Audit layout.css for any lingering purple border override on navrail

### FINDING 2: Command Palette Defined in Two Files
- `layout.css` L2112–L2220: Full palette implementation (canonical, updated in Z4)
- `nx-shell.css` L477–L485: Partial override (`.nx-palette`, `.nx-palette-input`)
- **Risk**: The nx-shell.css overrides may conflict. Verified: nx-shell.css overrides are additive and compatible (box-shadow, border strengthening only).

### FINDING 3: `app.log` Not in `.gitignore`
- `app.log` (4.4MB) and `agent.log` are committed to history
- **Action Required**: Add to `.gitignore`

---

## RECOMMENDATIONS

| Priority | Action | Risk |
|----------|--------|------|
| HIGH | Add `*.log` to `.gitignore` | Zero |
| MEDIUM | Remove 7 confirmed dead selectors | Minimal |
| LOW | Future: Consolidate 3 token systems into 1 | Moderate |
| LOW | Future: Remove `.pane-header` override cascade | Minimal |
| NONE | All animations: retain as-is | — |
| NONE | All utility classes: retain as-is | — |
