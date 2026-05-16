# Z18 Operational UI Audit
**Date: 2026-05-16**
**Scope: Long-session behavior, resize stability, reconnect states, execution persistence, accessibility, keyboard flow, viewport stability**

---

## EXECUTIVE SUMMARY

The operational surface has six critical concerns for production deployment: (1) overlay z-index anarchy with no registry, (2) infinite animations that cause visual fatigue, (3) no accessible keyboard flow through primary execution controls, (4) ARIA coverage is zero on interactive shell chrome, (5) SSE reconnect badge can be obscured by exec strip, and (6) no DOM accumulation controls for long sessions.

---

## 1. LONG SESSION STABILITY

### DOM Accumulation Risk

**Log output container:** `#nxTab-logs` receives log line appends during execution. No visible max-item cap or DOM pruning in HTML structure. For multi-hour sessions, thousands of `.log-line` DOM nodes accumulate. This is a JavaScript concern (out of scope for CSS), but the CSS needs to ensure the scroll container has `overflow-y:auto` and `contain:strict` to limit paint scope.

**Live pane streams:** `#nxLiveCodeArea` and `#nxLiveTermArea` receive streamed content. Same accumulation risk. CSS `contain` property can limit layout recalculation scope but is currently absent.

**Session history list:** `#p4SessList` — entries accumulate with no visible virtualization. CSS `max-height` + `overflow-y:auto` appears to be present via `.p4-sess-list` class but needs verification.

**Inspector sections:** Fixed structure — no accumulation risk.

### Visual Degradation Prevention

- No `will-change` hints on animated surfaces (pipeline bar, activity bar, idle hero).
- `nxHeroBreathe` animation on `.nx-hero-logo` runs `infinite` — but the hero is hidden during execution so this stops when execution starts. Low risk.
- `#nxRunDot` animation (`nxRunDot`, `1.2s infinite`) runs continuously while the run button is in running state. This is appropriate.
- `.nx-live-dot` animation (if `nx-live-pulse` is applied) runs during stream — stops when stream ends. Acceptable.

### Execution Clutter Reduction

- After execution, the pipeline bar remains in its last state (not reset) until next run. "Done" stage remains lit. This could be confusing in a long session.
- Activity bar remains visible after execution until JS hides it. CSS has no `:empty` or state-based display rules.

---

## 2. OVERLAY SYSTEM

### Z-Index Map (Full Audit)

| Element | z-index | File | Notes |
|---|---|---|---|
| `#nx-loading-bar` | 999999 | stability.css | Correct — top of all |
| `#nx-toasts` | 999998 | stability.css | Correct — below loading bar |
| `#uncertaintyModal` | 9999 | inline (index.html) | Wrong — below SSE badge |
| `#nx-sse-status` | 9997 | stability.css | Reconnect badge — obscures modal gap |
| `.nx-palette-backdrop` | layout.css | TBD | Needs audit |
| `#nxExecStrip` | UNSET | unknown | Risk: floats to base stacking context |
| `.nx-gh-overlay` | UNSET | index.html class | GitHub modal has no z-index |
| `.p8-modal-backdrop` | UNSET | index.html class | Upgrade modal has no z-index |
| `#nx-failsafe-banner` | 999 | stability.css | OK — operational banner |
| `#feedback-overlay` | UNSET | inline/feedback CSS | FAB has no z-index |

**Critical issues:**
1. `#uncertaintyModal` at z-index 9999 is BELOW `#nx-sse-status` at 9997 (toasts at 999998 would also cover it) — the uncertainty resolution modal can be obscured by system notifications during reconnection.
2. GitHub overlay and upgrade modal have no z-index — can be obscured by exec strip or other surfaces.
3. Exec strip has no defined z-index — if another element creates a stacking context, exec strip may disappear behind it.

### Modal Discipline

- `#uncertaintyModal`: inline `position:fixed;bottom:20px;right:20px` — fixed position, bottom-right corner. Conflicts with exec strip position (also bottom fixed).
- `.p8-modal-backdrop`: no `z-index` defined — backdrop may not cover all surfaces.
- No `<dialog>` element used anywhere — all modals are `<div>` with `position:fixed`. No native focus trap.
- No `role="dialog"` or `aria-modal` on any modal element.
- No `aria-labelledby` connecting modal titles to modal containers.

---

## 3. KEYBOARD FLOW

### Current Tab Order

The natural DOM tab order through the workspace:
1. Auth gate inputs (when shown)
2. Nav rail buttons (4 buttons — Files, Chat, History, Settings)
3. Topbar buttons (hamburger, run, stop, model, search, inspector, settings)
4. Composer "+" button → textarea → mode select → scope select → voice button
5. Tab bar buttons (Output, Code, Terminal, Preview)
6. Tab content (varies)

**Issues:**
- Nav rail buttons: `tabindex` not set — default order. Fine.
- Topbar run/stop compound button group: Two buttons in a visual group but no `role="group"` or `aria-label` on the container.
- Composer textarea: Primary execution input. It receives focus when user starts typing — OK. But no shortcut to jump directly to it from keyboard (no `accesskey`).
- Tab bar: `onclick="nxSetTab(...)"` — no keyboard handling. Arrow key navigation between tabs is expected (WCAG tab panel pattern) but not implemented.
- Modals: No focus trap — when opened, user can tab out of modal to background elements.

### Shortcut Consistency

Documented shortcuts:
- `⌘K` — Command palette (shown in topbar and idle hero)
- `⌘+Enter` — Execute (shown in composer placeholder)
- `⌘\` — Inspector toggle (shown in inspector button title)

**Issues:**
- No shortcut visible for stopping execution (only the Stop button's `title="Stop execution"` tooltip).
- No shortcut for switching between Output/Code/Terminal/Preview tabs.
- No shortcut for HITL pause/inject.
- Shortcuts are documented in `title=` and `placeholder=` but no centralized help surface.

### Focus Routing

- After executing a task: focus should return to the composer textarea. Not confirmed from HTML.
- After closing a modal: focus should return to the trigger element. No `data-return-focus` or similar pattern visible.
- After session switch: focus destination is undefined.

---

## 4. ACCESSIBILITY

### ARIA Coverage Audit

| Element | Current State | Issue |
|---|---|---|
| Nav rail buttons | `title="Files"` etc. | `title` is not reliable for screen readers — need `aria-label` |
| Run button | `title="Execute task"` | needs `aria-label`, `aria-pressed` or `aria-busy` during execution |
| Stop button | `title="Stop execution"` | needs `aria-label` |
| Model button | `title="Model & API config"` | needs `aria-label` |
| Search/palette trigger | `title="Command palette (Ctrl+K)"` | needs `aria-label` |
| Inspector toggle | `title="Inspector (⌘\)"` | needs `aria-label` |
| Tab bar buttons | no aria | needs `role="tab"`, `aria-selected`, `aria-controls` |
| HITL pause | no aria | needs `aria-label` |
| HITL inject input | `placeholder="Inject instruction..."` | needs `aria-label` |
| Modals | no aria | needs `role="dialog"`, `aria-modal`, `aria-labelledby` |
| Live status | no aria | needs `role="status"`, `aria-live="polite"` |
| Log output | no aria | needs `role="log"`, `aria-live="polite"` |
| Activity bar text | no aria | needs `aria-live="polite"` |

### Focus Visibility

**Current state:** Z15 established `nds.css` as canonical `:focus-visible` with `box-shadow: var(--nds-focus-ring)`. Phase O in nx-shell.css (NOT LOADED) had a scoped rule. The canonical rule applies to all elements.

**Issues:**
- Buttons with `background:transparent` and `border:none` (all topbar and nav buttons) — the focus ring `box-shadow` may be invisible on dark backgrounds depending on `--nds-focus-ring` value.
- Need to verify `--nds-focus-ring` contrasts with `var(--nds-bg)` background.

### Reduced-Motion Integrity

**Status:** Z15 addressed the global policy. `nds-tokens.css` `@media (prefers-reduced-motion: reduce)` sets `--nds-dur-fast: 0ms`, `--nds-dur-base: 0ms`, `--nds-dur-slow: 0ms`.

**Issues:**
- `nxHeroBreathe` animation uses hardcoded duration (3s) not a token — not suppressed by reduced-motion.
- `nxRunDot` animation uses hardcoded duration (1.2s) not a token — not suppressed by reduced-motion.
- `nx-live-pulse` animation uses hardcoded duration — not suppressed by reduced-motion.
- `nx-bar-shimmer` animation in stability.css uses hardcoded duration — not suppressed by reduced-motion.
- Several `transition: all 0.15s` inline styles — not suppressed by reduced-motion.
- These infinite animations will still fire under `prefers-reduced-motion: reduce` — WCAG failure.

### Contrast Stability

**Dark theme (primary):**
- `#8b949e` on `#0F1017` — ratio ~4.5:1 (AA pass for normal text, marginal)
- `#6e7681` on `#0F1017` — ratio ~3.5:1 (AA fail for normal text, pass for large text only)
- `#484f58` used in exec strip — ratio ~2.3:1 on dark background (FAIL)
- `#30363d` as border color on `#121212` — extremely low contrast border (not text, acceptable)

**Issues:** Three text color values in exec strip use below-AA contrast. The exec strip state/model/session information is therefore not accessible.

---

## 5. VISUAL FATIGUE REDUCTION

### Infinite Animation Audit

| Animation | Element | Duration | Condition | Fatigue Risk |
|---|---|---|---|---|
| `nxHeroBreathe` | `.nx-hero-logo` | 3s infinite | Idle state only | Low (slow, subtle) |
| `nxRunDot` | `#nxRunDot` | 1.2s infinite | During execution | Medium (visible) |
| `nx-live-pulse` | `.nx-live-dot` | 1.4s infinite | During stream | Medium (blinking dot) |
| `nx-bar-shimmer` | `#nx-loading-bar` | 1.2s infinite | During API call | Low (top bar only) |
| `uxit-pulse` | `.nx-sse-dot` | 1s infinite | During reconnect | Medium (prominent) |
| `nx-toast-in` | `.nx-toast` | 0.22s once | On notification | None (one-shot) |

**Assessment:** At maximum signal state (execution running + SSE reconnecting + code streaming), three simultaneous infinite animations are active: `nxRunDot` (green dot in topbar), `nx-live-pulse` (live dots in live tab), `uxit-pulse` (yellow dot in SSE badge). This creates multi-focal animation fatigue.

### Bright Accent Dominance

- `#bc8cff` (purple accent) appears on active tab border, active nav icon, plan mode badge, live code stream file label, pipeline active stage — 5+ concurrent surfaces.
- `#58a6ff` (blue) appears on onboarding prompts, info toasts, P15 metrics.
- `#3fb950` (green) appears on run dot, target indicator, success log lines, success toasts.
- At execution time, all three accent colors are simultaneously active — high chromatic complexity.

### Cognitive Overload Sources

1. Left panel: Session info → HITL controls → Upload chips → Thought stream → Decision stream → Recall stream → Session history → Prompt templates → Idle prompts — 9 sections visible simultaneously.
2. Right panel: Observability (thoughts + actions) → Status → Model → Memory → Agents → Metrics → Learning → Decisions → Output → Downloads — 10 sections.
3. Center (during execution): Composer → Tab bar → Activity bar → Pipeline bar → Log output — 5 stacked layers.

---

## 6. DEPLOYMENT UX

### Startup Loading Discipline

**Loading bar:** `#nx-loading-bar` with `transform:scaleX(0)` initial — good. Active state `transform:scaleX(0.7)` is JS-driven. CSS transition `transform 0.25s ease` is applied.

**Boot sequence:** Multiple `[BOOT]`, `[Phase X]` console logs visible in browser — good for development but should be suppressed in production. This is JS (out of scope) but CSS-level: no "boot" visual shown to user beyond whatever the auth gate shows.

**Reconnect clarity:**
- `#nx-sse-status` badge at `bottom:54px` — but exec strip is also at bottom. If exec strip height is ~32px, the SSE badge at 54px should clear it. However exec strip z-index is unset, so stacking is unpredictable.
- SSE badge uses `pointer-events:none` — correct, operators cannot accidentally click it.
- SSE badge animation `uxit-pulse 1s infinite` on the dot — visible but the badge itself has no `role="status"` or `aria-live`.

**Degraded-state clarity:**
- `#nx-failsafe-banner` for network errors — good, uses `position:sticky;top:0`. Will always be visible at top of content area.
- `.nx-sse-dot` animation indicates reconnecting — amber color (`#d29922`). Appropriate warning color.
- No "degraded mode" indicator for when AI provider is unavailable (failover bar `p5-failover-bar` covers this partially).

---

## IMPLEMENTATION PRIORITY MATRIX

| Issue | Severity | Effort | Phase |
|---|---|---|---|
| Add `aria-label` to nav, topbar, tab controls | High (WCAG) | Low | Z18 |
| Add `role="tab"/"tablist"/"tabpanel"` to tab bar | High (WCAG) | Low | Z18 |
| Add `role="log"/"status"` to log/activity areas | High (WCAG) | Low | Z18 |
| Fix `uncertaintyModal` z-index (9999 → 10000) | High | Low | Z18 |
| Add z-index to GitHub/upgrade modals | High | Low | Z18 |
| Fix exec strip z-index | Medium | Low | Z18 |
| Add `prefers-reduced-motion` to hardcoded animations | High (WCAG) | Medium | Z18 |
| Fix `#484f58` text contrast in exec strip | High (WCAG) | Low | Z18 |
| Focus trap for modals | High (WCAG) | High (JS) | Deferred |
| Tab bar keyboard navigation | High (WCAG) | High (JS) | Deferred |
| DOM accumulation cap | Medium | High (JS) | Deferred |
| `contain:content` on scroll areas | Medium | Low | Z18 |

---

## SUCCESS CRITERIA

- Zero WCAG AA contrast failures on visible text in exec surfaces
- All interactive shell chrome elements have `aria-label`
- Tab bar has correct ARIA roles
- Live/log output areas have `aria-live`
- Reduced-motion media query suppresses all infinite animations
- z-index registry consistent: modals > 10000, SSE badge < modals, exec strip < SSE badge
- No simultaneous triple-animation fatigue under normal operation
