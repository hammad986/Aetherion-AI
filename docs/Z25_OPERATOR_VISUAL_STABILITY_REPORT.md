# Z25 — Operator Visual Stability Report

**Phase:** Z25C — Operator Cognition & Visual Stability Assessment  
**Date:** 2026-05-16  
**Basis:** Post-Z25B stabilization pass  
**Status:** STABLE — RESIDUAL RISKS NOTED

---

## Purpose

This report evaluates the platform from the operator's perspective: what they see, what they understand, and where the UI fails to communicate operational context clearly.

---

## 1. Operator Eye-Flow Analysis

### Primary Gaze Path (Idle State)

```
1. Topbar: Logo / breadcrumb (top-left anchor)
2. Center: Run button (now accent-coloured — improved)
3. Left: Nav rail icons
4. Center: Empty workspace (no content — gap)
5. Right: Inspector panel
```

**Finding:** After Z25B, step 2 is improved — the run button is now accent-blue with 600 weight, making it identifiable as the primary action. Steps 4 remains a dead zone in idle state. The CSS patterns for `.nx-workspace-idle` are defined but not yet injected by JS.

### Primary Gaze Path (Active Execution)

```
1. Topbar: Run group (accent border glowing) — clear "something is happening"
2. Center: Workspace — AI activity bar, streaming log, AI editing banner
3. Run dot: Pulsing (7px, more visible after Z25B)
4. Right: Inspector panel (agent reasoning)
```

**Finding:** Active execution state is well-communicated after Z25B. The multi-signal approach (topbar border glow + run dot + AI activity bar + logs) creates redundant confirmation of activity — appropriate for an execution environment.

---

## 2. Visual Stability Score Card

| Area | Pre-Z25B Score | Post-Z25B Score | Max | Notes |
|---|---|---|---|---|
| Token coherence | 3 | 9 | 10 | Bridge layer applied |
| Execution gravity | 4 | 7 | 10 | Run button elevated; workspace idle still empty |
| Typography harmony | 4 | 7 | 10 | Sizes normalised; font family unified |
| Panel cohesion | 5 | 8 | 10 | Borders unified to NDS token |
| Empty state intelligence | 1 | 4 | 10 | CSS defined, HTML not yet populated |
| Command palette ergonomics | 5 | 7 | 10 | Section headers + selection improved |
| Accessibility compliance | 6 | 7 | 10 | ARIA gaps remain in auth + banners |
| Interaction consistency | 5 | 8 | 10 | Hover states unified |
| Execution-state visibility | 6 | 8 | 10 | Topbar + run dot improved |
| Runtime clutter | 5 | 7 | 10 | Footer fades during execution; legacy div hidden |
| **OVERALL** | **4.4** | **7.2** | **10** | |

---

## 3. Operator Confusion Points: Resolved vs Residual

### Resolved by Z25B

| Confusion Point | Resolution |
|---|---|
| "Is the Run button for running AI or something else?" | Accent colour + 600 weight makes it the primary CTA |
| "Why do some panels look slightly different shades?" | Token bridge aligns all `--bg`/`--panel` to one palette |
| "The accent colour changes between panels" | Unified to `#0079F2` via bridge override |
| "Panel borders look different between topbar and sidebar" | All border references now resolve to `--nds-surface-4` |
| "Toasts look disconnected from the rest of the UI" | Toast background now uses `--surface` token |

### Residual Confusion Points

| Confusion Point | Status | Required Fix |
|---|---|---|
| "Nothing is happening when I load the app" | OPEN | Workspace idle state HTML needed |
| "I don't know what keyboard shortcuts exist" | OPEN | Keyboard hint surface needed (idle state) |
| "The auth form feels inconsistent with the main app" | PARTIAL | Font unified; ARIA roles still missing |
| "The palette just shows a flat list — I can't tell what category things are in" | PARTIAL | CSS section headers defined; JS needs to emit `.nx-palette-section` elements |
| "The session list is empty and I don't know how to start" | OPEN | Session list empty state HTML needed |

---

## 4. Platform Tone Assessment

### Before Z25B
The platform oscillated between "GitHub-dark consumer tool" (base.css) and "Replit-premium developer tool" (nds-tokens.css) depending on which component was rendered. This created an incoherent brand signal.

### After Z25B
The unified token bridge establishes a single surface palette. The accent colour resolves to one value. The font resolves to one face (Inter). The tone is now consistently: **professional dark-mode developer tool with operational character**.

Remaining tone issues:
- Cookie banner `🍪` emoji — minor but tonally inconsistent with the platform's operational gravity
- `⚠` raw Unicode in verify/error banners — should use the icon system

---

## 5. Execution Confidence Signal Analysis

An "execution confidence signal" is a visual element that tells the operator the system is ready, working, or done — without requiring them to read text.

| Signal | Element | Quality |
|---|---|---|
| System ready | None defined | GAP |
| Execution started | Run dot (pulsing) + topbar glow | GOOD (after Z25B) |
| AI thinking | AI activity bar + streaming log | GOOD |
| Execution complete | Dot disappears, log ends | ADEQUATE |
| Error state | Red topbar border + error banner | GOOD (after Z25B) |
| Disconnected SSE | SR region `(reconnecting…)` text | WEAK (text only) |

**Critical gap:** There is no visual signal that says "the AI backend is online and ready to receive a task" before the operator runs for the first time. The workspace appears identical whether the backend is healthy or down.

---

## 6. Visual Density Under Different States

### State: Idle (No Session)

```
Topbar:    ████░░░░░░  (40% full — nav + run + 3 icons)
Left rail: ██████░░░░  (60% — icons only, no labels)
Workspace: ░░░░░░░░░░  (0% — empty after Z25B CSS defined, HTML pending)
Inspector: ████░░░░░░  (40% — session info, no current session)
```

**Assessment:** Too sparse at idle. A production execution environment should communicate readiness even when idle. The workspace idle CSS is ready — HTML population is the remaining step.

### State: Active Execution

```
Topbar:    ████████░░  (80% — run dot, streaming indicators)
Left rail: ████░░░░░░  (40% — icons, active session highlighted)
Workspace: ██████████  (100% — AI activity bar + log stream + code view)
Inspector: ████████░░  (80% — agent reasoning, live metrics)
```

**Assessment:** Appropriately dense during execution. No unnecessary UI elements competing for attention.

### State: Post-Execution Review

```
Topbar:    ████░░░░░░  (40% — idle state restored)
Workspace: ██████░░░░  (60% — completed output visible)
Inspector: ██████░░░░  (60% — session summary, metrics)
```

**Assessment:** Adequate. Post-execution could surface a "Session complete" summary chip more prominently, but this is outside Z25B scope.

---

## 7. Visual Regression Checks

The following visual regressions were checked for after applying `nx-z25-stabilization.css`:

| Check | Result |
|---|---|
| Auth gate background (should be dark panel) | PASS — `--panel` now = `#23252F` |
| Auth gate accent links (should be blue) | PASS — `--accent` = `#0079F2` |
| Run button colour (should be accent on idle) | PASS — `.nx-topbar-run-btn { color: var(--accent) }` |
| Stop button colour (should be red) | PASS — unchanged `var(--red)` |
| Toast success colour (should be green-tinted) | PASS — border uses green dim |
| Toast error colour (should be red-tinted) | PASS — border uses red dim |
| Nav active icon (should be accent-bordered) | PASS — `border-left: 3px solid var(--accent)` |
| Panel borders (should be consistent) | PASS — all resolve to `#3A3D48` |
| Focus rings (should be purple) | PASS — nx-a11y.css unchanged |
| Light theme (should still work) | PASS — Z25B variables are dark-only defaults, light-theme overrides in base.css still apply |

---

## 8. Outstanding Visual Work (Post-Z25)

These items are out of scope for Z25B surgical constraints but are documented for future phases:

| Item | Priority | Effort |
|---|---|---|
| Workspace idle state HTML content | HIGH | Low (1 day) |
| Session list empty state HTML | HIGH | Low (0.5 day) |
| Auth tab ARIA roles | MEDIUM | Low (1 hour) |
| Cookie banner tone (remove emoji) | LOW | Trivial |
| Banner button `aria-label` attributes | MEDIUM | Trivial |
| Command palette section emit in JS | MEDIUM | Low (2 hours) |
| "System ready" idle indicator | MEDIUM | Medium (2 days) |
| SSE reconnecting visual indicator | LOW | Low (1 day) |
| Nav rail icon tooltips | LOW | Low (1 day) |

---

## Stability Verdict

**Pre-Z25B:** Visual instability — three competing token systems, two accent colours, font never resolved.  
**Post-Z25B:** Visually stable — one token system, one accent, one font face. Empty states defined in CSS but not yet populated.

**Operator experience grade:** `Operational Beta` — a developer familiar with the platform can use it confidently. A new operator loading the app for the first time encounters a confusingly sparse idle state.

*Z25C Operator Visual Stability Report complete.*
