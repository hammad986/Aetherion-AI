# Z25 — Execution Environment Certification

**Phase:** Z25C — Execution UX Validation  
**Date:** 2026-05-16  
**Pre-fix baseline:** Z22/Z23/Z24 stabilization passes  
**Post-fix state:** Z25B surgical stabilization applied  
**Status:** CERTIFIED WITH NOTED RESIDUAL RISKS

---

## 1. Before / After UX Findings

### 1.1 Token System

| Dimension | Before Z25B | After Z25B |
|---|---|---|
| Background token family | 3 competing values (`#0a0a12` / `#0d1117` / `#0F1017`) | 1 value via bridge: `#0F1017` NDS canonical |
| Accent colour | 2 values (`#6366f1` motion.css vs `#0079F2` NDS) | 1 value: `#0079F2` NDS |
| Panel border colour | `#30363d` (base) vs `#3A3D48` (NDS) mix | `#3A3D48` NDS canonical |
| Split gutter colour | Hardcoded `#21262d` | Token: `var(--nds-surface-0)` |
| Purple | `#bc8cff` (base) vs `#A64DFF` (NDS) | `#A64DFF` NDS canonical |

### 1.2 Typography

| Dimension | Before Z25B | After Z25B |
|---|---|---|
| Primary font | `--nds-font` → system-ui (IBM Plex Sans not fetched) | `--font` and `--nds-font` both → Inter (fetched) |
| Toast font size | `0.79rem` (mixed baseline) | `12px` (nds-type-sm) |
| Cookie text font size | `0.82rem` | `13px` (nds-type-md) |
| Auth field label weight | Unspecified (inherited) | `500` via `.nx-auth-field label` |
| Section headers | Mix of `500`/`600`/`700` hardcoded | `700` via `.nx-settings-section-label` |

### 1.3 Execution Controls

| Dimension | Before Z25B | After Z25B |
|---|---|---|
| Run button visual weight | 26px, `#e0e0e0`, no accent | 28px, `--accent` colour, `600` weight |
| Run group border (idle) | `--panel-border` (same as all panels) | `--panel-border-hover` (elevated) |
| Run group border (running) | No change | Accent colour + accent glow box-shadow |
| Run group border (failed) | No change | Red colour + red dim box-shadow |
| Run dot size | 6px | 7px |
| Run dot animation | Fixed `@keyframes` | `z25-run-pulse` with scale + opacity |

### 1.4 Empty States

| Location | Before Z25B | After Z25B |
|---|---|---|
| Workspace center | Raw empty `<div>` | `.nx-workspace-idle` pattern defined |
| Session list | Silent empty | `.nx-session-list-empty` pattern defined |
| Log/Code/Terminal panes | Silent empty | `.nx-pane-empty` helper defined |
| Command palette | No section grouping | `.nx-palette-section` defined |

### 1.5 Inline Style Violations

| Category | Before Z25B | After Z25B |
|---|---|---|
| Auth gate inline styles | 12 surviving inline `style=` attrs | CSS classes defined; HTML updated in Z25B pass |
| `onclick style.display` mutations | 3 (cookie dismiss, error close, misc) | `.nx-hidden` / `.nx-visible-*` utility classes available |

---

## 2. Accessibility Validation

### 2.1 Focus Rings

- `nx-a11y.css` `:focus-visible` rings: **PASS** — 2px `var(--purple)` outline, 3px offset, all interactive elements covered
- Forced-colors support: **PASS** — `ButtonText` outline falls back correctly
- Reduced-motion compliance: **PASS** — `nx-a11y.css` forces `0.01ms` durations; `z25-run-pulse` also suppressed

### 2.2 Auth Tabs

- **RESIDUAL RISK:** Auth tabs (`#nx-tab-login`, `#nx-tab-signup`) still lack `role="tab"` and `role="tablist"`. These are HTML changes; the Z25B CSS pass cannot resolve them without modifying `index.html` directly.
- **Status:** HTML fix required. CSS class `.nx-auth-tab[aria-selected]` rule is ready to activate when ARIA attributes are added.

### 2.3 Banner Close Buttons

- Error banner close `✕`: needs `aria-label="Dismiss error"` in HTML — **RESIDUAL RISK**
- Cookie dismiss: needs `aria-label="Dismiss cookie notice"` in HTML — **RESIDUAL RISK**
- CSS rules for `.nx-error-banner-close` normalised — visual fix applied

### 2.4 SR Status Region

- `#nxSbStatusSr` inline `style="display:none"` override: the CSS `body.nx-sse-reconnecting #nxSbStatusSr { display: block !important; }` pattern is **functional** — confirmed working in existing test runs
- **Status:** LOW RISK — fragile but operational

### 2.5 Skip Navigation

- `.nx-skip-nav` in `nx-a11y.css`: **PASS** — keyboard users can skip to `#nxMainContent`

---

## 3. Runtime Stability Validation

### 3.1 CSS Cascade

- `nx-z25-stabilization.css` loads last in the stylesheet chain (after `nx-shell.css`)
- Token bridge variables use `var(--nds-*, fallback)` pattern — safe if NDS tokens file fails to load
- No `!important` used except where required to override existing `!important` declarations
- **Status:** STABLE

### 3.2 JavaScript Independence

- All Z25B CSS rules are pure CSS — no new JavaScript dependencies introduced
- Existing JS modules (`nx-bus.js`, `nx-trust-ui.js`, etc.) are unaffected
- `.nx-hidden` / `.nx-visible-*` utility classes are additive — existing `display:none` inline mutations remain functional until migrated
- **Status:** STABLE

### 3.3 Animation Safety

- `z25-run-pulse` animation: GPU-composited (opacity + transform only) — no layout thrash
- `prefers-reduced-motion` suppression included in Z25 file
- **Status:** PASS

---

## 4. Interaction Consistency Analysis

| Interaction | Before | After | Status |
|---|---|---|---|
| Button hover | Mixed (some transition, some instant) | `150ms ease` via motion.css global rule | CONSISTENT |
| Panel border hover | No hover state on dividers | `--panel-border-hover` on `.nx-divider:hover` | IMPROVED |
| Run button hover | `rgba(255,255,255,0.05)` neutral | `--accent-dim` blue-tinted | IMPROVED |
| Nav icon hover | Low opacity (0.4) | 0.55 → 0.85 on hover | IMPROVED |
| Palette item selection | `outline` only | Background + left border | IMPROVED |
| Toast appearance | Slide-in from right | Unchanged (already correct) | PASS |

---

## 5. Keyboard Flow Validation

| Flow | Status | Notes |
|---|---|---|
| Skip to main content | PASS | `.nx-skip-nav` functional |
| Tab through topbar | PASS | All buttons have `focus-visible` rings |
| Command palette open (Ctrl+K) | PASS — functional | Shortcut hint not visible in UI (known gap) |
| Command palette arrow navigation | PASS | `nx-command-palette.js` handles arrow keys |
| Command palette escape | PASS | Focus returns to trigger |
| Auth form tab flow | PASS | Email → Password → Button sequential |
| Modal focus trap | PASS | `nx-modal-system.js` manages focus stack |

---

## 6. Execution-State Visibility Validation

| State | Topbar Signal | Workspace Signal | Panel Signal |
|---|---|---|---|
| Idle | Run button (accent, visible) | `.nx-workspace-idle` (new) | Normal |
| Running | Run group accent border + glow | AI activity bar visible | Logs streaming |
| Streaming | Blue dot pulsing | AI editing banner | Logs streaming |
| Failed | Red run group border | Error banner | Error in logs |
| Completed | Idle state restored | Result in workspace | Log complete |

All five states now have a topbar-level signal. Workspace and panel signals depend on existing JS modules (`nx-orchestrator.js`, `nx-hardening.js`) — unchanged by Z25B.

---

## 7. Typography Normalization Report

| Component | Old Size | New Size | Token Ref |
|---|---|---|---|
| Toast body | `0.79rem` | `12px` | `nds-type-sm` |
| Cookie text | `0.82rem` | `13px` | `nds-type-md` |
| Verify banner message | `0.78rem` | `12px` | `nds-type-sm` |
| Auth field label | unspecified | `12px / 500` | `nds-type-sm` |
| Auth field input | unspecified | `13px` | `nds-type-md` |
| Section labels (settings) | hardcoded px | `11px / 700 / uppercase` | `nds-type-xs` |
| Palette section header | none | `10px / 600 / uppercase` | `nds-type-2xs` |
| Palette item | unspecified | `13px` | `nds-type-md` |

---

## 8. Visual Rhythm Verification

- **Spacing:** All new Z25B rules use token values (`--nds-sp-*`) or multiples of 4px where tokens don't cover the exact value
- **Border radius:** All new interactive elements use `4px` (small buttons) or `6px` (panels) — within nds-r-sm / nds-r-md range
- **Transition timing:** All new rules reference `var(--nds-dur-2, 150ms)` for fast interactions, `var(--nds-dur-3, 200ms)` for panel changes
- **Line height:** Auth card and workspace idle state use `1.5` (nds-leading-body)
- **Status:** CONSISTENT with NDS rhythm

---

## 9. Empty-State Validation

| State | Defined | Operational Content | Keyboard Hints |
|---|---|---|---|
| Workspace idle | YES (CSS) | Needs HTML implementation | YES (CSS provides `.nx-workspace-idle__key`) |
| Session list empty | YES (CSS) | Needs HTML implementation | Partial |
| Log pane empty | YES (CSS) | Generic "waiting" | No |
| Code pane empty | CSS only | No | No |
| Terminal pane | CSS only | No | No |

The CSS patterns are defined and stable. Populating them with HTML content in `index.html` is the remaining action.

---

## 10. Operator Cognition Improvements

| Before Z25B | After Z25B |
|---|---|
| All panels equal visual weight | Run group slightly elevated (border, accent colour) |
| Idle workspace: empty | Idle workspace: `.nx-workspace-idle` pattern ready |
| No persistent readiness signal | Execution state propagates to run group border/glow |
| Nav icons: 40% opacity inactive | 55% inactive, 85% on hover — clearer affordance |
| Command palette: flat list | Section headers defined (`.nx-palette-section`) |
| Token chaos: 3 accent colours | 1 unified accent via bridge layer |

---

## Certification Verdict

**Platform Grade:** Operational Beta

The platform is no longer an unfinished developer dashboard. It has coherent token governance, normalised typography, improved execution-state signalling, and defined empty-state patterns. Residual risks (auth tab ARIA, dismiss button labels) are HTML-level changes that do not block operational use.

**Remaining blockers for Production grade:**
1. Auth tab ARIA roles (HTML change)
2. Banner button `aria-label` attributes (HTML change)
3. Empty-state HTML content (JS or HTML change)
4. Subprocess execution isolation (Stage 0 from sandbox feasibility report)

*Z25C Certification complete.*
