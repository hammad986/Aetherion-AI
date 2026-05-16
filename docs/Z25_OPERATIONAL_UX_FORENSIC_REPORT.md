# Z25 — Operational UX Forensic Report

**Phase:** Z25A — Forensic Operational UX Audit  
**Date:** 2026-05-16  
**Auditor:** Nexora Z25 Stabilization Pass  
**Status:** FORENSIC COMPLETE — SURGICAL FIXES AUTHORIZED

---

## Executive Summary

After Z22/Z23/Z24, the platform is architecturally cleaner but operationally fragile. Three competing CSS token systems produce surface-level colour inconsistency visible at a pixel level. Typography is fragmented across two font declarations that never resolve to the same face at runtime. Inline style attributes persist in the auth gate and verification banner. Empty workspace states communicate nothing to the operator. The topbar run/stop controls are undersized relative to their operational importance. These are not cosmetic defects — they represent execution-gravity failures and operator-cognition friction.

---

## 1. Visual Instability Findings

### 1.1 Three-System Token Fragmentation (CRITICAL)

The stylesheet load order in `templates/index.html` creates cascading override chaos:

| Load Position | File | `--bg` value | `--panel` value |
|---|---|---|---|
| 1 | `nds-tokens.css` | `#0F1017` (NDS canonical) | — (not defined) |
| 2 | `base.css` | **OVERRIDES** → `#0d1117` | `#161b22` |
| 9 | `motion.css` | **OVERRIDES** → `#0a0a12` | `#12121c` |

At runtime: components using `--bg` render `#0a0a12`, components using `--nds-bg` render `#0F1017` — a **30-unit luminance split** across the same page. This creates perceivable surface stratification that has no design intent behind it.

**Affected surfaces:** topbar, auth gate background, panel backgrounds, sidebar, cookie banner.

### 1.2 Dual Colour Alias Systems

`base.css` defines the legacy palette: `--bg`, `--panel`, `--border`, `--muted`, `--accent`, `--purple` (GitHub-inspired dark, `#0d1117` family).

`nds-tokens.css` defines the NDS palette: `--nds-bg`, `--nds-surface-*`, `--nds-accent`, `--nds-purple` (Replit Premium Dark, `#0F1017` family).

`motion.css` defines a **third** intermediate palette: redefines `--bg`, `--surface`, `--panel`, `--accent`, `--purple` to a cooler warmer-charcoal family (`#0a0a12`, `#6366f1` accent vs `#0079F2` NDS accent).

Result: `--accent` at runtime = `#6366f1` (motion.css indigo) for legacy components, `#0079F2` (NDS blue) for NDS components. **Two different brand accents coexist on the same screen.**

---

## 2. Layout Hierarchy Findings

### 2.1 Shell Grid vs Legacy Flex Coexistence

`nx-shell-root` uses a named CSS grid (`topbar`, `navrail`, `main`, `dock`). The outer panel split system in `layout.css` (line 919) uses a 5-column grid: `var(--leftW) 4px 1fr 4px var(--rightW)`. These two grid systems are nested but use different measurement strategies — one is named-area based, the other is column-count based — causing reflow ambiguity on panel collapse/expand.

### 2.2 Panel Divider Inconsistency

Dividers use three different border references:
- `nx-z19z20z21.css`: `var(--panel-border, #30363d)` (legacy base.css value)
- `motion.css:238`: `.nx-divider` uses `--nds-surface-4` (`#3A3D48`) with blue glow on hover
- `layout.css`: raw `#30363d` hardcoded in several split-gutter rules

This produces visible border-colour inconsistency between the topbar, panel edges, and split gutters.

### 2.3 Z-Index Stratification (Documented but Unverified)

Declared hierarchy: Surface(10) → Dock(20) → Inspector(30) → Navrail(40) → Topbar(50) → Overlay(100) → Toasts(10000). The cookie banner at `nx-z-overlay` (99999) and toasts at 999998 (stability.css) exceed the declared token range — token and implementation are out of sync.

---

## 3. Interaction Friction Findings

### 3.1 Inline Style Attributes in Production HTML

The following inline `style=` attributes survive in `templates/index.html` despite Z19/Z20 having removed them from shell components:

| Line | Element | Inline Style | Issue |
|---|---|---|---|
| 73 | SR status div | `style="display:none"` | Should be class-driven |
| 112 | Forgot-pw form | `style="display:none"` | Should be class-driven |
| 124 | Signup form | `style="display:none"` | Should be class-driven |
| 134 | Password label span | `style="color:var(--muted);font-weight:400;"` | Hardcoded inline |
| 139–140 | TOS agree label | `style="display:flex;…"` | Full layout inline |
| 140 | Checkbox input | `style="margin-top:2px;accent-color:…"` | Inline override |
| 141–143 | TOS links | `style="color:var(--accent,#58a6ff)"` | Inline colour |
| 145 | Signup button | `style="margin-top:10px"` | Inline spacing |
| 191 | Cookie dismiss btn | `onclick="…style.display='none'"` | JS inline mutation |
| 211 | Verify banner | `style="color:#d29922"` | Hardcoded hex |
| 212 | Verify message span | `style="color:#8b949e;font-size:0.78rem"` | Dual inline |
| 225 | Error banner close | `onclick="…style.display='none'"` | JS inline mutation |

Total: **12 surviving inline style violations** in the auth/banner region.

### 3.2 Onclick Handlers Using `style.display`

Three `onclick` handlers directly mutate `element.style.display`. These bypass the class-driven visibility model established in Z20, making the state untrackable by CSS selectors and accessibility trees.

---

## 4. Typography Inconsistencies

### 4.1 Font Resolution Mismatch

`nds-tokens.css` declares `--nds-font: 'IBM Plex Sans', system-ui, sans-serif`.  
`motion.css` declares `--font: 'Inter', 'Segoe UI', system-ui, sans-serif`.  
`templates/index.html` loads **only Inter** from Google Fonts (line 52) — IBM Plex Sans is never fetched.

At runtime: `--nds-font` resolves to `system-ui` (fallback). Components using `--font` render Inter. Components using `--nds-font` render system-ui (OS default). **Two different physical fonts render on screen.**

### 4.2 Type Scale Fragmentation

| Token Source | Scale Used | Values |
|---|---|---|
| `nds-tokens.css` | `--nds-type-*` | 10/11/12/13/15/18px |
| `motion.css` | raw px / `0.79rem` | Mixed em/px throughout |
| `base.css` | `0.78rem`, `0.8rem`, `0.82rem` | No token reference |
| `stability.css` | `0.79rem` toast | No token reference |

Result: toast text (0.79rem ≈ 12.6px), cookie text (0.82rem ≈ 13.1px), verify text (0.78rem ≈ 12.5px) — all similar sizes expressed in three different units referencing three different baselines. No operator-visible defect, but a maintenance fragility.

### 4.3 Weight Hierarchy Inconsistency

- Auth card heading: no explicit weight token — inherits body weight (400)
- Section labels in settings: `font-weight: 500` (hardcoded)
- Toast icon: `font-weight: 700` (hardcoded in stability.css)
- Panel section headers: mix of 500 and 600 weights with no governing token

---

## 5. Dead-Space Analysis

### 5.1 Workspace Empty State: Non-Operational

When no session is active and the center workspace panel is empty, the UI presents:
- A raw `<div>` container with no content
- No status communication (active model, system health, readiness)
- No keyboard hint surface
- No recent session context
- No operational guidance

The `.nds-empty` pattern (icon → title → subtext → actions) exists in `nds.css` but is not applied to the primary workspace empty state. The workspace reads as an unfinished HTML shell.

### 5.2 Log/Code/Terminal Tabs: Silent on Empty

All three main content tabs (Logs, Code, Terminal) display empty containers when no execution has occurred. No "waiting for execution" state, no model status, no keyboard shortcuts are communicated to the operator.

### 5.3 Left Rail Session List: Empty Silence

The session list area shows nothing until sessions exist. No "Start your first session" prompt, no keyboard shortcut hint (`Ctrl+Enter` to run).

---

## 6. Compositional Imbalance

### 6.1 Topbar Balance

The topbar is structured as left / center / right clusters (all `flex: 1`). Current visual weight:
- **Left:** Nav toggle button (16×16 icon) + breadcrumb text — very lightweight
- **Center:** Run/Stop group (26px height, 12px font) + Model selector — functionally critical but visually undersized
- **Right:** Three icon buttons (Command, Inspector, Settings) — equal weight to left

The execution controls — the most operationally important topbar element — are the same visual weight as the nav toggle. This inverts operator attention priority.

### 6.2 Run/Stop Control Sizing

Run button: 26px height, 12px font, transparent background. This is identical in visual presence to a breadcrumb label. An active execution state has no stronger visual presence than an idle one at the topbar level.

### 6.3 Navrail Icon Spacing

Icons in the left navrail use fixed pixel padding without a spacing-token reference. Active-state indicator (left border accent) renders correctly but the inactive states have insufficient contrast separation from the rail background — particularly at high ambient light or on calibrated monitors.

---

## 7. Execution Gravity Weaknesses

- No persistent "system ready" indicator communicating that the AI backend is connected and responsive
- No execution-state persistence across tab switches (operator loses running context when switching to Code tab)
- The `data-exec-state` attribute on the run button (running/streaming/failed) drives topbar state but does not propagate to the workspace panel — the panel does not visually change during execution
- AI editing banner appears during execution but does not anchor operator attention to the correct panel

---

## 8. Runtime Distraction Sources

- **Cookie banner emoji:** `🍪` in the cookie notice text — not consistent with the platform's operational/professional tone
- **Verify banner `⚠`:** Raw Unicode character, not icon-system consistent
- **Error banner `⚠`:** Same — raw Unicode
- **Footer:** Visible at all times as a persistent element, adds vertical noise when the workspace is in execution mode
- **`nx-legacy-header-controls`:** A hidden `<div>` with 8 nested sub-elements preserved purely for JS reference — increases DOM weight with zero visual output

---

## 9. Accessibility Regressions

### 9.1 SR Status Region Uses Inline `display:none`

`#nxSbStatusSr` is declared with `style="display:none"` in HTML and un-hidden by CSS rule `body.nx-sse-reconnecting #nxSbStatusSr { display: block !important; }`. The JS `!important` override requires the inline style to be overridden — this is fragile and could fail if specificity changes.

### 9.2 Auth Tabs Missing `role="tablist"`

The auth gate tabs (`#nx-tab-login`, `#nx-tab-signup`) use `class="nx-auth-tab"` but lack `role="tab"`, `role="tablist"` on the container, and `aria-selected` attributes. Screen readers cannot determine the active tab.

### 9.3 Cookie Dismiss Handler Not Keyboard-Accessible

The cookie dismiss button's `onclick` directly sets `style.display='none'`. This pattern works on click but the button itself has no `aria-label` describing its action.

### 9.4 Error Banner Close Button: No Label

`<button onclick="…" class="nx-error-banner-close">✕</button>` — the `✕` character is the only label. Needs `aria-label="Dismiss error"`.

---

## 10. Operator Cognition Analysis

### Hierarchy of Operator Attention (Observed vs Required)

| Priority | Required | Actual |
|---|---|---|
| 1 | Execution state (running/idle/failed) | Topbar dot (6px, secondary colour) |
| 2 | Active model + plan | Small text in center cluster |
| 3 | Output / result | Logs panel (requires tab selection) |
| 4 | Next action | No affordance |

The current layout does not visually establish what the operator should focus on. All panels have equal visual weight. Execution state is communicated by a 6px dot — sub-perceptual in peripheral vision.

---

## 11. Command Palette UX Weaknesses

- Items in the palette list lack section grouping (commands, sessions, tabs appear in a flat undifferentiated list)
- Keyboard selection uses arrow keys but selection highlight uses `outline` rather than `background` change — lower contrast on dark surfaces
- No "recent commands" section surfaces prior operator actions
- Palette trigger keyboard shortcut (`Ctrl+K`) is not displayed anywhere in the idle UI — operator must discover it

---

## 12. Empty-State Operational Failures

| Location | Current State | Required State |
|---|---|---|
| Workspace center (no session) | Empty div | Runtime status + keyboard hints |
| Logs tab (no output) | Empty | "Waiting for execution…" |
| Code tab (no file) | Empty | File tree with create-file affordance |
| Terminal tab (uninitialized) | Empty | "Terminal connects on first run" hint |
| Session list (no sessions) | Empty | "No sessions yet. Press Ctrl+Enter to start." |

---

## 13. Panel Cohesion Analysis

- Left panel and right panel use different border strategies: left uses `border-right`, right uses `border-left`, both reference `--panel-border` which resolves to `#30363d` (base.css) — correct but inconsistent with `--nds-surface-4` (#3A3D48) used in motion.css components
- The split gutter (4px wide, `background: #21262d` hardcoded in layout.css) does not match any token value — it is between `--nds-surface-0` and `--nds-surface-1` visually but is not token-referenced
- Topbar `border-bottom` uses `--panel-border` while navrail uses `--nds-surface-4` — visually the topbar bottom and navrail edge produce different border weights

---

## 14. Visual Density Map

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TOPBAR [LOW DENSITY — overcautious whitespace, execution controls undersized]│
├──────┬──────────────────────────────────────────────────────────┬───────────┤
│ NAV  │ CENTER WORKSPACE [DEAD — no content in idle state]       │ INSPECTOR │
│ RAIL │                                                          │ [MODERATE]│
│[MED] │ [Actual density: 0/10 when idle, 8/10 during execution]  │           │
│      │                                                          │           │
├──────┴──────────────────────────────────────────────────────────┴───────────┤
│ DOCK/FOOTER [LOW — persistent footer adds nav noise in execution mode]       │
└─────────────────────────────────────────────────────────────────────────────┘
```

The visual density is **binary**: near-zero at idle, overloaded during active execution. A production-grade execution environment maintains moderate operational density at all states.

---

## 15. Topbar Balance Analysis

```
LEFT [flex:1]              CENTER [flex:1]            RIGHT [flex:1]
─────────────────          ─────────────────          ─────────────────
≡ Nexora / session         ▶ Run | ■ Stop             ⌘  ⊞  ⚙
[Nav toggle + breadcrumb]  [26px exec controls]        [3 icon buttons]
Visual weight: ~2/10       Visual weight: ~3/10        Visual weight: ~3/10
```

**Finding:** The center execution controls are not visually dominant. A non-expert operator cannot identify "where to run" by eye flow alone.

---

## 16. Nav Rail Usability Analysis

- 5 icons visible, no labels visible by default (icon-only)
- Active state: left border accent (3px purple/blue line) — correctly implemented
- Inactive state: icon at ~40% opacity, no hover affordance text
- No tooltip on icon hover (keyboard users cannot discover icon labels)
- Bottom of navrail has no "account" or "settings" anchor — these are top-only
- Rail collapse state not persisted across page refresh

---

## Summary: Severity Classification

| Finding | Severity | Category |
|---|---|---|
| Triple token system (3 bg values) | HIGH | Visual Instability |
| Dual accent colour (indigo vs blue) | HIGH | Brand Inconsistency |
| Font never loaded (IBM Plex Sans) | MEDIUM | Typography |
| 12 inline style attributes | MEDIUM | Code Discipline |
| Workspace empty state non-operational | HIGH | Execution Gravity |
| Auth tabs no ARIA roles | MEDIUM | Accessibility |
| Error/dismiss buttons no aria-label | LOW | Accessibility |
| Cookie banner emoji | LOW | Tone |
| Topbar execution controls undersized | MEDIUM | Execution Gravity |
| SR region inline display style | MEDIUM | Accessibility |
| Command palette: no section grouping | LOW | Ergonomics |
| Split gutter hardcoded colour | LOW | Token Hygiene |
| Nav rail: no hover tooltips | LOW | Usability |

---

*Z25A Forensic Audit complete. Surgical fixes authorized under Z25B constraints.*
