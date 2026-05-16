# Z35 Runtime Immersion Report

**Phase:** Z35E — Execution Immersion Refinement  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — state-driven ambient presence, zero decorative motion

---

## Ambient Runtime Layer

An absolutely-positioned `#z35AmbientLayer` overlay sits inside `#z30DagSurface`. It is `pointer-events: none` and sits at `z-index: 0` — below all DAG nodes and edges, never interfering with interactions.

The overlay renders a phase-specific radial gradient from a directional origin point:

| Phase | Gradient Origin | Color | Opacity |
|-------|----------------|-------|---------|
| planning   | Left-center (15%, 40%) | Blue  | 4% |
| executing  | Center (50%, 45%)      | Green | 4% |
| validating | Right-center (80%, 40%)| Light green | 4% |
| recovering | Lower-center (50%, 60%)| Amber | 4% |
| escalating | Upper-center (50%, 30%)| Red   | 4% |
| replay     | Left-center (30%, 50%) | Warm amber | 4% |
| idle       | — (no gradient) | — | 0% |

All gradients are a single `radial-gradient` covering ~65% of the DAG surface area. No animation, no movement, no transitions between gradient positions — the gradient is static once the phase is set. It fades in via `opacity: 0 → 1` with a `600ms ease` transition.

---

## Pressure-Driven Spatial Signal

The `#z30DagSurface` element carries two data attributes that drive ambient border signals:

- `data-z35-pressure="normal"` — no border
- `data-z35-pressure="elevated"` — 1px amber inset border at 8% opacity
- `data-z35-pressure="critical"` — 1px red inset border at 12% opacity

These borders apply via `::after` pseudo-element. They are structural hints that something in the execution environment is stressed — not decorative.

---

## Operational Motion Language Compliance

| Principle | Implementation |
|-----------|---------------|
| Motion represents state | ✓ — ambient gradient only changes on phase transition |
| Motion represents pressure | ✓ — pressure fill bar animates on pressure change |
| Motion remains minimal | ✓ — longest animation is 600ms ambient fade-in |
| No idle motion | ✓ — only the escalation dot pulses (2s, represents active alert) |
| No transform animations | ✓ — all transitions are opacity/color/width only |

---

## Spatial Runtime Awareness Test

An operator looking at the live tab should instantly identify:

| State | Visual Signal |
|-------|--------------|
| Active execution | Green ambient in DAG center + green EXECUTING phase label |
| Planning | Blue ambient left + blue PLANNING label |
| Recovering | Amber ambient lower + amber RECOVERING label + amber pressure fill |
| Critical pressure | Red inset border on DAG surface + red PRESSURE fill |
| Escalated | Pulsing red dot + ESCALATED label in mission bar |
| Unstable node | Red inset ring on that DAG node (heat-critical class) |
| Successful recovery | Node heat cools; ambient returns to green |

---

## FPS / Memory Validation

- Ambient layer is a single `div` with a CSS `background` property. No canvas, no SVG, no animation loop.
- Phase transitions replace the CSS class on `#z35AmbientLayer` — the browser recalculates the gradient once per transition. Gradient computation is GPU-composited.
- Pressure fill bar: a single `width` style update per pressure recalculation (triggered by log events at most 10–20/s during peak execution). Transition smooths the visual update over 400ms.
- Heat map application: `document.querySelector('[data-node-id="..."]')` per dirty node per RAF cycle. Typical DAG has 5–15 nodes; O(n) DOM queries per update cycle is acceptable.
- Zero new `setInterval` timers in Z35. All state changes are event-driven.
- Memory: all state in `S` object (one object per session). `heatMap` stores one float per node — negligible memory footprint.

---

## Remaining Immersion Weaknesses

1. Ambient gradient origin is fixed per phase. On a DAG where the active node happens to be at the opposite end from the gradient origin, the spatial connection between gradient and activity is loose. A true spatially-aware ambient would position the gradient origin at the active DAG node's SVG coordinates — deferred to Z36+.

2. The ambient gradient disappears instantly when the phase changes to `idle`. A 600ms fade-out to match the fade-in would be more polished.

3. `#z30DagSurface::after` is shared with Z34's wave animation pseudo-element. If both Z34 and Z35 apply `::after` simultaneously, only one will render (last in cascade wins). A future refactor should consolidate pseudo-element usage into a single overlay div.
