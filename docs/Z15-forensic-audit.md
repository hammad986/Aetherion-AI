# Phase Z15 — Forensic CSS Audit
**Status: AUDIT COMPLETE — No implementation changes made**  
**Date: 2026-05-16**  
**Files audited:** 12 CSS files (17,397 total lines), templates/index.html (3,654 lines)

---

## FINDING REGISTRY

### F-01 · THREE COMPETING TOKEN SYSTEMS (Critical)
**Files:** `nds-tokens.css`, `nx-shell.css` (line 7), `base.css` (line 1)

Three independent `:root` token namespaces coexist with no bridging discipline:

| System | Prefix | Declared in | Usage |
|---|---|---|---|
| Canonical NDS | `--nds-*` | `nds-tokens.css` | nds.css components only |
| Shell tokens | `--nx-space-*`, `--nx-z-*`, `--nx-shell-*` | `nx-shell.css` line 7–33 | nx-shell.css only |
| Legacy raw | `--bg`, `--panel`, `--text`, `--accent`, etc. | `base.css` line 1–23 | layout.css, base.css, forms.css |

`nds-tokens.css` defines legacy aliases (`--bg`, `--panel`, etc.) to bridge the third system, **but `base.css` re-declares those same names first** in its own `:root`, so the canonical values are unreachable via specificity where base.css loads later.

**Consequence:** `--bg` resolves to `#0d1117` (base.css) or `#0F1017` (nds-tokens.css) depending on load order — a 6-value hex difference that creates a visible seam between elements.

---

### F-02 · DUPLICATE `:root` INSIDE nx-shell.css (Critical)
**File:** `nx-shell.css` lines 7–33 (Phase A) **and** lines 1259–1264 (Phase N)

Phase N re-opens `:root` inside the same file to declare motion tokens:
```css
/* line 1259 */
:root {
  --nx-t-fast:   120ms ease;
  --nx-t-normal: 200ms ease;
  --nx-t-slow:   300ms ease;
  --nx-ease-out: cubic-bezier(0.16, 1, 0.3, 1);
}
```
These motion tokens **duplicate** the motion tokens already present in `nds-tokens.css` (`--nds-motion-fast`, `--nds-motion-slow`). Neither is used consistently — Phase N uses `var(--nx-t-fast)` while nds.css uses `var(--nds-motion-fast)`.

---

### F-03 · THREE CONFLICTING `html, body` BACKGROUND COLORS (Critical)
**Files:** `nx-shell.css` line 38, `layout.css` line 4, `nds-tokens.css` line 45

| File | Declaration | Resolved color |
|---|---|---|
| `nx-shell.css` line 38 | `background-color: #121212` | #121212 |
| `layout.css` line 4 | `background: var(--bg)` | #0d1117 |
| `nds-tokens.css` | `--nds-bg: #0F1017` | #0F1017 |

Three different backgrounds. Load order determines which wins. The dark seam between panes that appears on resize is caused by the last-in-wins value overriding the painted surface color.

---

### F-04 · DUAL BODY FONT STACKS (High)
**Files:** `nx-shell.css` line 42, `layout.css` line ~18

| File | Font family |
|---|---|
| `nx-shell.css` | `'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif` |
| `layout.css` | `var(--nds-font, 'IBM Plex Sans', system-ui, sans-serif)` |

The winner is load-order dependent. Throughout `layout.css`, `'Inter'` is hardcoded 10+ times as an inline `font-family` value on individual components, bypassing the token entirely.

---

### F-05 · DUPLICATE `@keyframes nx-pulse` (High)
**Files:** `layout.css` line 187, `nx-shell.css` line 352

Two completely different animations share the same name:
- `layout.css`: modulates `box-shadow` (the live-dot glow ring)
- `nx-shell.css`: modulates `opacity + transform: scale()` (the icon pulse)

CSS last-in-wins: whichever file loads second silently overwrites the other. Elements animated by the first definition receive the wrong motion.

---

### F-06 · DUPLICATE `@keyframes nxFadeIn` WITHIN layout.css (High)
**File:** `layout.css` lines 895 **and** 1372

Same keyframe name declared twice inside the same file. The second definition (line 1372) always wins. The first copy is dead CSS.

---

### F-07 · FOUR CONFLICTING `:focus-visible` RINGS (High)
**Files:** `nds.css`, `layout.css`, `nx-shell.css` Phase G, `nx-shell.css` Phase O

| Source | Rule |
|---|---|
| `nds.css` | `outline: none; box-shadow: var(--nds-focus-ring)` |
| `layout.css` | `button:focus-visible { outline: none; box-shadow: 0 0 0 3px rgba(99,102,241,0.22) }` |
| `nx-shell.css` Phase G | `button:focus-visible { outline: 1px solid var(--nx-color-accent, #bc8cff) }` |
| `nx-shell.css` Phase O | `:focus-visible { outline: 2px solid #bc8cff; outline-offset: 2px }` |

The Phase O rule cascades last and wins globally, but it uses a hardcoded hex `#bc8cff` rather than a token. nds.css's `outline: none` on the global selector is overridden by Phase O's `2px solid` — accessibility regression on elements that relied on nds's box-shadow ring.

---

### F-08 · FOUR COMPETING SCROLLBAR DEFINITIONS (High)
**Files:** `layout.css` line 27, `nx-shell.css` Phase G line 297, `nx-shell.css` Phase N line 1355, `nx-shell.css` Phase O line 1512

| Source | Width | Target |
|---|---|---|
| `layout.css` line 27 | 6px | `::-webkit-scrollbar` (global) |
| `nx-shell.css` Phase G line 297 | 6px | `::-webkit-scrollbar` (global again) |
| `nx-shell.css` Phase N line 1355–1367 | 4px | `.nx-tab-content`, `.nx-insp-chain`, `.nx-chunk-body`, `#nxInspectorContent` |
| `nx-shell.css` Phase O line 1512–1515 | `thin` (Firefox) | `*` (all elements) |

Panels end up with 4px or 6px scrollbars depending on selector specificity. The two global `::-webkit-scrollbar` blocks are fully redundant.

---

### F-09 · DUPLICATE `box-sizing: border-box` (Medium)
**Files:** `nds.css` line 8, `layout.css` line 4

Both declare `*, *::before, *::after { box-sizing: border-box; }`. Harmless in effect but signals token discipline failure — one canonical reset file should own this.

---

### F-10 · `transition` APPLIED TO ALL ELEMENTS via `*` (Medium)
**File:** `base.css` line ~35

```css
* {
  transition: var(--theme-transition);
}
```
`--theme-transition` is `background-color 0.25s ease, color 0.25s ease, border-color 0.25s ease`. This fires on **every element** on every state change, including scroll events, dynamic class toggles, and focus changes. Causes performance overhead on low-end devices and visual noise on elements that should snap instantly (e.g., live terminal output lines).

---

### F-11 · DUPLICATE `.nx-live-dot` (Medium)
**Files:** `layout.css` line 199, `nx-shell.css` line 344

Both define `.nx-live-dot`. Different animations: `layout.css` uses `nx-blink`, `nx-shell.css` uses `nx-pulse`. The shell version uses `box-shadow` pulse; the layout version uses `currentColor`. Load-order determines which renders.

---

### F-12 · DUPLICATE `.nx-icon-btn:hover` (Medium)
**Files:** `layout.css` line 270, `nx-shell.css` Phase G line 317

Both define hover state for `.nx-icon-btn`. Phase G uses `!important` overrides:
```css
/* nx-shell.css Phase G */
.nx-icon-btn:hover, .nx-nav-icon:hover {
  background: rgba(255,255,255,0.08) !important;
  color: #E0E0E0 !important;
}
```
The `!important` prevents any future specific override from working cleanly.

---

### F-13 · Z-INDEX ANARCHY — 10+ RAW VALUES (Medium)
**Files:** `layout.css`, `nx-shell.css`, `nds.css`, `base.css`

Observed raw z-index values (no token usage): 1, 2, 5, 10, 20, 45, 50, 100, 200, 400, 500, 1000, 5000, 9000, 9999, 99990, 99999, 10000, 10000.

`nx-shell.css` defines a z-index token system (`--nx-z-base`, `--nx-z-overlay`, `--nx-z-topbar`, `--nx-z-navrail`, `--nx-z-modal`) **that is never used by layout.css or base.css**. The highest value (`z-index: 99999`) is on a loading overlay in `layout.css`; the token system caps at `--nx-z-modal: 400`.

---

### F-14 · THREE SPACING NAMING CONVENTIONS (Medium)
**Files:** `nds-tokens.css`, `nx-shell.css`, `layout.css`/`base.css`

| Convention | Example | Where |
|---|---|---|
| `--nds-sp-*` | `--nds-sp-2: 8px` | nds-tokens.css |
| `--nx-space-*` | `--nx-space-2: 8px` | nx-shell.css |
| `--sp-*` (alias) | `--sp-2: var(--nds-sp-2)` | nds-tokens.css (bridge) |

`layout.css` and `base.css` use neither — they use raw pixel values (`8px`, `12px`, `16px`) throughout.

---

### F-15 · DUPLICATE `@media (prefers-reduced-motion)` (Low)
**Files:** `nds-tokens.css` line 15–25, `nx-shell.css` Phase O line 1488–1495

Both declare reduced-motion overrides. The nds-tokens.css version is comprehensive; the Phase O version partially overlaps it. Harmless in effect but adds file weight and contradicts the single-source principle.

---

### F-16 · HARDCODED HEX THROUGHOUT base.css (Low — Scope: Large)
`base.css` (3,680 lines) contains hundreds of hardcoded hex values (`#161b22`, `#0d1117`, `#30363d`, `#58a6ff`, `#3fb950`, `#f85149`, etc.) instead of token references. This is the largest source of theme-drift and the root cause why dark/light theme switching is incomplete. Not in scope for Z15 surgical fix — noted as a separate backlog item.

---

## SUMMARY TABLE

| ID | Severity | Description | Files Affected |
|---|---|---|---|
| F-01 | **Critical** | 3 competing token systems | nds-tokens.css, nx-shell.css, base.css |
| F-02 | **Critical** | Duplicate `:root` in nx-shell.css | nx-shell.css |
| F-03 | **Critical** | 3 conflicting body background colors | nx-shell.css, layout.css, nds-tokens.css |
| F-04 | **High** | Dual body font stacks | nx-shell.css, layout.css |
| F-05 | **High** | Duplicate `@keyframes nx-pulse` | layout.css, nx-shell.css |
| F-06 | **High** | Duplicate `@keyframes nxFadeIn` | layout.css (internal) |
| F-07 | **High** | 4 conflicting `:focus-visible` rings | nds.css, layout.css, nx-shell.css ×2 |
| F-08 | **High** | 4 competing scrollbar definitions | layout.css, nx-shell.css ×3 |
| F-09 | **Medium** | Duplicate `box-sizing: border-box` | nds.css, layout.css |
| F-10 | **Medium** | `transition` on `*` selector | base.css |
| F-11 | **Medium** | Duplicate `.nx-live-dot` | layout.css, nx-shell.css |
| F-12 | **Medium** | Duplicate `.nx-icon-btn:hover` + `!important` | layout.css, nx-shell.css |
| F-13 | **Medium** | Z-index anarchy (raw values, no tokens) | layout.css, nx-shell.css, nds.css |
| F-14 | **Medium** | 3 spacing naming conventions | nds-tokens.css, nx-shell.css, layout.css |
| F-15 | **Low** | Duplicate reduced-motion media query | nds-tokens.css, nx-shell.css |
| F-16 | **Low** | Hardcoded hex values in base.css | base.css (large scope, deferred) |

---

## PROPOSED Z15 IMPLEMENTATION PLAN

### Phase 2 — Token Unification
- Add a `/* Z15 BRIDGE */` `:root` block at the top of `nds-tokens.css` that maps all `--nx-space-*` → `--nds-sp-*` and all `--nx-t-*` → `--nds-motion-*`
- Merge the Phase N `:root` block in nx-shell.css back into the Phase A `:root` block (eliminate duplicate `:root`)
- Ensure `--bg` and `--panel` in `base.css` defer to `nds-tokens.css` values

### Phase 3 — Nav Rail
- Audit `.nx-navrail`, `.nx-nav-icon`, `.nx-nav-btn` across layout.css and nx-shell.css
- Consolidate into a single location (nx-shell.css, which owns the shell chrome)
- Remove `!important` from Phase G hover rules

### Phase 4 — Topbar
- Consolidate `html, body` declarations into one block in a single file (layout.css)
- Remove the competing `background-color: #121212` from nx-shell.css
- Unify font-family to one token reference

### Phase 5 — Execution Surface
- Remove duplicate `@keyframes nx-pulse` from either layout.css or nx-shell.css (keep one, rename the other)
- Remove duplicate `@keyframes nxFadeIn` from layout.css (delete the second copy at line 1372)
- Consolidate `.nx-live-dot` into one location

### Phase 6 — Split.js Borders
- Audit Split.js gutter styles, confirm `.gutter-horizontal` and `.gutter-vertical` have correct cursor and border tokens

### Phase 7 — Validation Certificate
- After each phase: verify no broken selectors, no visual regression on app screenshot

### Phase 8 — Comment Discipline
- Each surviving rule gets a single-line source comment (`/* Z15: consolidated from nx-shell.css Phase G */`)
- Remove phase comments that no longer correspond to living code

---

## SURGICAL CONSTRAINTS (per mission spec)
- NO framework migration, NO React/Tailwind rewrite
- NO feature invention
- NO runtime redesign
- Edit only existing CSS/HTML — no new files except the final validation cert
- All changes are additive-removal only (delete duplicates, point tokens to canonical source)
