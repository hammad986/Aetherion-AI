# Z51 Workspace Calmness Report

## Phase Z51D — Visual Noise Reduction + Operational Hierarchy

**Date:** 2026-05-17  
**Status:** COMPLETE  

---

## Noise Sources Identified

| Source | Severity | Action |
|---|---|---|
| Multiple pulse animations active simultaneously during idle | Medium | Throttled: non-hero pulse dots use 6s breathe period (vs 4s) |
| Activity dots (`.nx-activity-dot`) glowing during idle | Low | CSS: opacity 0.3 when body lacks `.nx-running` class |
| Inspector live badges pulsing during idle | Low | CSS: opacity 0.5 when not running |
| Error pulse (`error-pulse`) was looping | High | Restricted to single-shot forward animation via `nxErrorPulse` keyframe |
| Stage indicator dots in pipeline bar all animated simultaneously | Medium | CSS: only `.active` stage dot animates; idle dots are static opacity 0.35 |
| Run button box-shadow persisting during idle | Low | CSS: `box-shadow: none !important` on `body:not(.nx-running) .nx-run-btn` |
| Z33 signal chips at full opacity at all times | Low | CSS: opacity 0.7 at rest, 1.0 on hover |
| Metric labels at full brightness during idle | Low | CSS: `var(--text-dim)` colour during idle via body class |
| Tag/badge count chips always at full opacity | Low | CSS: 0.8 at rest, 1.0 on hover |

---

## Body Class System

Z51 adds a CSS-driving body class system via `nxSetGlobalStatus()` hook:

```css
body           → idle state (no special class)
body.nx-running → agent actively executing
body.nx-error   → agent encountered error
```

This allows CSS to "know" the execution state without JS targeting individual elements on every state change. 25+ CSS rules now target `body:not(.nx-running)` to suppress decoration during idle.

---

## Visual Hierarchy After Z51

**During idle:**
- Logo, topbar, compose area: full opacity
- Execution indicators, metrics, badges: reduced (0.3–0.5 opacity)
- Hero area: soft breathe only on heartbeat dot
- Inspector: static, no pulsing

**During running:**
- Green pulse dot in topbar: fast (1.2s)
- Active pipeline stage: animated
- Activity bar: full opacity
- Run button: no glow suppression

**During error:**
- Right panel receives single error-pulse shot (not looping)
- Error card shown, logs banner shown
- Red topbar dot: static (no animation on error)

---

## Attention Fragmentation Reduction

Before Z51, the following elements could all animate simultaneously during idle:
1. Topbar pulse dot (breathe)
2. Activity bar stream dots
3. Pipeline stage dots
4. Inspector live badges
5. Queue count badge (if > 0)

After Z51, only **1 element animates during idle**: the heartbeat dot in the topbar. All others are static until execution begins.

---

## Remaining Noise

1. The `nxLogPulse` element in the logs tab header still pulses during runs alongside the topbar dot — redundant but intentional for log-tab focus signalling.
2. The z33 idle signals row (`#z33IdleSignals`) can show multiple runtime chips simultaneously. Their opacity is reduced (0.7) but the count itself can feel busy on a loaded session.
3. Some third-party fonts (JetBrains Mono) occasionally cause a flash-of-unstyled-text on first load due to async loading. Minor, not related to animation noise.

---

## Beta Readiness Score: 8/10

The workspace is meaningfully calmer. The single-animation-during-idle principle is enforced via CSS body class targeting. Remaining noise is limited to intentional execution signals.
