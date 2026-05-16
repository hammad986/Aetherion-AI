# Z16 Visual Forensic Report
**Date: 2026-05-16**
**Scope: Complete visual audit of Nexora AI workspace shell**

---

## EXECUTIVE SUMMARY

The workspace has a severe inline-style crisis. The shell chrome (topbar, composer, tab bar, idle hero, exec strip) is almost entirely driven by hardcoded `style=""` attributes rather than CSS classes. The CSS token system from Z15 cannot reach these surfaces. The nav rail and inspector have no CSS definitions in any loaded file. nx-shell.css (1798 lines) is not loaded by the HTML — it is dead weight.

---

## CRITICAL FINDING: nx-shell.css NOT LOADED

**Severity: CRITICAL**

`static/css/nx-shell.css` (1798 lines) is referenced in zero `<link>` tags in `templates/index.html`. The HTML loads:
```
nds-tokens.css → base.css → components.css → layout.css → forms.css →
graphs.css → support.css → stability.css → motion.css → nds.css →
nx-observability.css → nx-agi-native.css
```
nx-shell.css is absent. Z15 edits to that file are therefore inert for browser rendering. All `.nx-shell-root`, `.nx-shell-topbar`, `.nx-shell-navrail`, `.nx-nav-icon` class definitions it contains are not applied.

---

## PHASE A — NAV RAIL DISCIPLINE

### Audit Findings

**File:** `templates/index.html` lines 455–469

```html
<nav class="nx-shell-navrail" style="grid-area:navrail;">
  <button class="nx-nav-icon" onclick="nxTogglePanel('files')" title="Files">
    <!-- SVG icon 20×20 -->
  </button>
  ...
  <div style="flex:1"></div>  ← unnamed spacer, no CSS class
  <button class="nx-nav-icon" onclick="nxTogglePanel('settings')" title="Settings">
```

**Issues found:**
1. `.nx-shell-navrail` — **ZERO CSS definition in any loaded file**. It renders as block-level with no width constraint, no background, no padding.
2. `.nx-nav-icon` — **ZERO CSS definition in any loaded file**. Raw unstyled buttons.
3. `style="grid-area:navrail;"` inline — grid-area declaration with no parent `display:grid` or `grid-template-areas` in any loaded file. This declaration is meaningless.
4. Spacer `<div style="flex:1">` — unnamed inline-only spacer, no CSS class.
5. No `.active` state class set in HTML — active state presumably managed by JS only.
6. No `aria-label` on any nav icon button — only `title=` attribute which is not screen-reader reliable.
7. 4 icons: Files, Chat, History, Settings — History and Settings separated by the flex:1 spacer.

**Root cause:** The nav rail styles live in nx-shell.css which is not loaded.

---

## PHASE B — TOPBAR EXECUTION HIERARCHY

### Audit Findings

**File:** `templates/index.html` lines 257–307

```html
<header class="nx-shell-topbar" style="grid-area:topbar; justify-content:space-between; 
  padding:0 12px; font-size:12px;">
```

**Issues found:**
1. `.nx-shell-topbar` — **ZERO CSS definition in any loaded file**. Height, background, border, z-index all absent.
2. **All layout is inline:** Three flex groups (left, center, right) all use `style="display:flex;..."` inline.
3. **All hover effects use JS:** `onmouseover="this.style.background='rgba(255,255,255,0.05)'"` — JS-driven hover on every button. No CSS `:hover` rules.
4. **Run/Stop compound button:** Uses `background:rgba(255,255,255,0.03);border:1px solid #30363d;border-radius:4px;overflow:hidden;height:26px;` — entirely inline, no CSS class.
5. **Model selector:** `.nx-model-btn` has CSS in layout.css BUT is overridden by inline `style="background:transparent;border:1px solid transparent;..."` making the CSS rule partially irrelevant.
6. **Breadcrumb:** `style="font-weight:500;color:#c9d1d9;"` inline — ignores token system.
7. **palette-trigger, inspector-btn, settings-btn:** All use inline styles for color, padding, border-radius.
8. **Failover bar:** `.p5-failover-bar` has CSS in layout.css — uses class correctly.
9. **Recommendation bar:** `.p6-inline-rec` has CSS in layout.css — uses class correctly.

**Visual hierarchy weakness:** Center cluster (Run/Stop/Model) is visually equal weight to utility bar (right). No execution-first emphasis. The Run button has no visual precedence over Search/Inspector/Settings.

---

## PHASE C — EXECUTION SURFACE BALANCE

### Audit Findings

**Composer** (`templates/index.html` line 612):
```html
<div class="nx-composer" style="padding:16px;border-bottom:1px solid #27272A;
  background:#121212;flex-shrink:0;">
```
**Issues:**
- `.nx-composer` has **NO CSS definition in any loaded file**.
- Inline `background:#121212` — hardcoded, ignores token system. Token value for this surface should be `var(--nds-bg)` = `#0F1017`.
- Inline `border-color:#27272A` — hardcoded border.
- Textarea: `background:#202024;border:1px solid #27272A;color:#E0E0E0;font-family:inherit;font-size:14px` all inline.
- Exec toolbar: `border-top:1px dashed #27272A` — dashed border is visually weak, hardcoded.
- Mode/Scope selects: `background:#202024;border:1px solid #27272A;color:#8b949e` all inline.
- "+" button and Voice button: entirely inline-styled.

**Tab bar** (`templates/index.html` line 654):
```html
<div class="nx-tab-bar" id="nxTabBar" style="display:flex; gap:24px; border-bottom:1px solid #27272A; 
  padding:0 16px; background:#18181B;">
```
**Issues:**
- `.nx-tab-bar` IS defined in layout.css (line 1191) with `height:38px;background:var(--panel);border-bottom:1px solid var(--panel-border);padding:0 4px;gap:1px;`. 
- But the inline `style="gap:24px;padding:0 16px;background:#18181B"` **overrides** the CSS class definition — CSS class is defeated.
- Active tab uses inline `border-bottom:2px solid #bc8cff;color:#E0E0E0` — not using CSS class `.nx-tab.active`.
- Inactive tabs use inline `border-bottom:2px solid transparent;color:#8b949e` — overriding the CSS class's `color:var(--text-dim)`.

**Idle hero** (`templates/index.html` line 714):
```html
<div class="nx-idle-hero" id="nxIdleHero" style="display:flex;flex-direction:column;
  align-items:center;justify-content:center;height:100%;color:#8b949e;user-select:none;">
```
**Issues:**
- `.nx-idle-hero` IS defined in layout.css (line 1355) with correct flex layout.
- Inline `style="..."` partially overrides: `height:100%` conflicts with CSS `flex:1` (both work but are redundant), `color:#8b949e` overrides any token-based color.
- All chip buttons use inline styles + JS hover handlers.
- Hero text uses inline `color:#c9d1d9` and `color:#6e7681` — hardcoded.

---

## PHASE D — PANEL DISCIPLINE

### Audit Findings

**Shell root structure** (`templates/index.html` lines 256–3515):
- `.nx-shell-root` wraps: topbar, navrail, shell-center, shell-inspector, shell-dock
- **NO CSS grid template** in any loaded file for `.nx-shell-root` — `grid-area:` inline declarations on children are inert.
- The actual workspace grid is inside `.nx-body → .nx-main` (layout.css line 908–921): `grid-template-columns: var(--leftW) 4px 1fr 4px var(--rightW)`
- `.nx-shell-inspector` (aside, line 3502): **ZERO CSS definition in any loaded file** — renders as inline with no panel geometry.
- `.nx-shell-dock` (footer, line 3512): **ZERO CSS definition in any loaded file** — has inline background/border fallback but no height, no z-index.
- **Exec strip** (line 3622): `.nx-exec-strip` — check if defined in loaded CSS (needs verification in nx-agi-native.css).

**Inspector panel** (right panel, inside `.nx-right`):
- `.nx-inspector-section` IS defined in layout.css (line 1638).
- `.nx-insp-label` IS defined in layout.css (line 1895).
- `.nx-stat-grid`, `.nx-stat`, `.nx-stat-label`, `.nx-stat-val` all defined in layout.css.
- Inspector itself is well-structured with proper CSS classes.

**Border collisions:**
- Topbar bottom border: not defined (CSS class absent).
- Composer border: `border-bottom:1px solid #27272A` inline.
- Tab bar border: `border-bottom:1px solid #27272A` inline (overrides CSS class).
- Result: `#27272A` border color used throughout but not via token — if theme changes, borders stay dark.

---

## PHASE E — TYPOGRAPHY SYSTEM

### Audit Findings

**Z15 fix applied:** 15 hardcoded `font-family:'Inter'` replaced with `var(--font, 'Inter', sans-serif)` in layout.css.

**Remaining issues:**
- Topbar font-size: inline `font-size:12px` on `<header>` — sets a root font size for all topbar text.
- Breadcrumb: `font-weight:500;color:#c9d1d9` inline.
- Run/Stop labels: `font-size:12px;font-weight:500` inline.
- Composer textarea: `font-size:14px` inline — does not use `var(--nds-text-sm)` token.
- Tab labels: `font-size:13px;font-weight:600` inline.
- Mode/scope selects: `font-size:11px` inline.
- Inspector `.nx-insp-label`: correctly uses CSS class with `font-size:10px;font-weight:700;letter-spacing:0.07em`.
- Idle hero: `font-size:13px;font-weight:500` inline for "Ready for execution" text.
- Exec strip: no font-size set → inherits from parent → unstable.

**Typography rhythm:** 10px (inspector labels) → 11px (mode selects, exec strip) → 12px (topbar, HITL) → 13px (tabs, idle hero) → 14px (composer textarea). This is a 5-level scale but none of it uses named type tokens.

---

## PHASE F — RESPONSIVE STABILITY

### Audit Findings

**Loaded CSS responsive rules:**
- layout.css has `grid-template-columns: var(--leftW) 4px 1fr 4px var(--rightW)` — responsive via JS-controlled CSS vars.
- No explicit media queries for panel collapse visible in the inline CSS of shell chrome.
- The shell chrome (topbar, navrail) has no responsive rules because it has no CSS definitions.

**Viewport issues:**
- At <800px: topbar three-column flex layout with `flex:1` on left and right groups will collapse strangely.
- At <800px: "CENTER" Run/Stop/Model group with `justify-content:center;flex:1` may get squeezed.
- The command palette (`.nx-palette`) IS defined in layout.css with proper max-width constraints.

---

## INLINE STYLE INVENTORY

**Total inline `style=` occurrences:** 380+ in templates/index.html (entire file)

**Critical surfaces (shell chrome):**
| Surface | CSS Class Exists? | Inline Style Count | Hardcoded Colors |
|---|---|---|---|
| `.nx-shell-topbar` | NO (not in loaded files) | 1 on header + ~12 on children | 4+ (#30363d, #E0E0E0, #8b949e, #c9d1d9) |
| `.nx-shell-navrail` | NO (not in loaded files) | 1 on nav | 0 |
| `.nx-composer` | NO (not in loaded files) | 1 on div + ~8 on children | 6+ (#121212, #27272A, #202024, #E0E0E0, #8b949e) |
| `.nx-tab-bar` | YES (layout.css) but overridden | 1 override on div + 4 on tab buttons | 3 (#18181B, #27272A, #bc8cff) |
| `.nx-idle-hero` | YES (layout.css) but overridden | 1 override on div + ~6 on children | 4+ (#8b949e, #c9d1d9, #6e7681, #30363d) |
| `.nx-exec-strip` | UNKNOWN (likely nx-agi-native.css) | 2 on children | 2 (#484f58, #6e7681) |
| `.nx-shell-inspector` | NO (not in loaded files) | 1 on close button | 0 |
| `.nx-shell-dock` | NO (not in loaded files) | 1 on footer | 2 (#27272A + var mix) |

---

## Z-INDEX AUDIT

| Layer | z-index | Surface |
|---|---|---|
| Loading bar | 999999 | `#nx-loading-bar` (stability.css) |
| Toast container | 999998 | `#nx-toasts` (stability.css) |
| SSE status | 9997 | `#nx-sse-status` (stability.css) |
| Uncertainty modal | 9999 | inline (index.html) |
| Failsafe banner | 999 | inline (index.html) |
| Command palette backdrop | layout.css | not yet audited |
| Exec strip | UNSET | `.nx-exec-strip` (z-index unknown) |

**Issues:** z-index 9999 (uncertainty modal) is below 9997 (SSE badge) — modal can be obscured by reconnect badge. No canonical z-index registry used across loaded CSS.

---

## SUMMARY: TOP 10 VISUAL DEBT ITEMS

1. **nx-shell.css not loaded** — 1798 lines of dead CSS
2. **Shell chrome has no CSS** — topbar, navrail, inspector, dock undefined
3. **Composer fully inline** — background, border, textarea, toolbar all hardcoded
4. **Tab bar CSS class overridden by inline** — gap, padding, background, active state all inline
5. **Idle hero inline colors** — 4+ hardcoded hex values overriding token system
6. **Topbar hover effects via JS** — no CSS :hover, all onmouseover/onmouseout JS
7. **No active state CSS for nav rail** — active icon tracking is JS-only
8. **No aria-label on nav icons** — only `title=` attribute
9. **z-index chaos** — 5 different stacking levels, no registry
10. **Typography not tokenized** — 5 font-size levels, none use named tokens
