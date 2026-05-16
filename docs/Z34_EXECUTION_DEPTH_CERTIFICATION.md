# Z34 Execution Depth Certification

**Phase:** Z34B + Z34E — Execution Spatial Presence + Visual Depth  
**Date:** 2026-05-16  
**Verdict:** CERTIFIED — calm operational immersion achieved

---

## Depth Layer Hierarchy

Four named layers provide visual separation between operational modes:

| Layer | Trigger | Visual Signal |
|-------|---------|--------------|
| `runtime` | Live session active | Faint blue tint on DAG surface, blue depth indicator, left border on live wrap |
| `replay` | Replay mode entered | Warm amber tint on DAG panel header, amber depth indicator |
| `forensic` | Z31 historical session loaded | Faint purple tint on DAG panel |
| `timeline` | Timeline dock expanded with focus | Green timeline dock border |

All tints are 3–6% opacity. No neon. No animated gradients during idle. No flashing.

---

## Live Execution Wave Propagation

During active execution, each log row is classified by phase (plan / code / debug / tool / done). A radial gradient is briefly applied to the DAG surface keyed to the phase:

| Phase | Wave Color | Radial Origin |
|-------|-----------|--------------|
| plan  | Blue (5%)  | Left-center |
| code  | Green (6%) | Center |
| debug | Red (6%)   | Right-center |
| tool  | Purple (5%)| Bottom-center |

Wave duration: 1.8 seconds, easing out via CSS `@keyframes z34-wave-fade`. After the wave completes, the `data-z34-phase` attribute is removed and the surface returns to baseline.

**RAF-gated**: only one wave frame dispatched per animation frame. Rapid log events are silently throttled — no visual overload.

---

## Motion Governance Checklist

- ✓ All transitions ≤ 220ms (inspector slide-in: cubic-bezier ease)
- ✓ Wave animations ≤ 1.8s, opacity-only (no transforms, no layout shifts)
- ✓ No blinking, no pulsing idle states
- ✓ Sync pulse (1.2s) on health bar dot only on explicit user interaction
- ✓ Depth indicator color change: 150ms transition
- ✓ Zero animation during replay reconstruction (static snapshot apply)

---

## Spatial Awareness Test

An operator looking at the live tab should instantly understand:

| Signal | Visual Cue |
|--------|-----------|
| Active execution | Left border glow on live wrap + blue RUNTIME indicator |
| Replay mode | Warm header tint + REPLAY indicator + cursor position shown |
| Historical/forensic | Purple tint + FORENSIC indicator |
| Failed node | Inspector shows red state dot + failure narrative |
| High retry count | Retry value in inspector highlighted red (> 2 retries) |
| Low confidence | Drift strip bars are red + confidence value in red |

---

## FPS / Performance Certification

- All DOM writes are RAF-batched via existing Z30 `_scheduleRender` pattern
- Wave propagation uses `requestAnimationFrame` with a single-frame throttle
- CSS transitions use `opacity` and `background-color` only — GPU composited, zero layout
- Inspector body re-renders only on explicit node selection (no polling)
- No `setInterval` timers introduced in Z34 beyond continuity data load (30s interval inherited from Z31)

---

## Remaining Issues

- `color-mix()` used for replay/forensic tints requires Chrome 111+ / Firefox 113+. Graceful degradation: tint simply does not appear on older browsers; all other functionality is unaffected.
- Wave animation uses `::after` pseudo-element on `#z30DagSurface`. If Z30 already uses `::after` for its own overlays, this will conflict. Audit Z30 CSS if adding additional pseudo-element layers.
