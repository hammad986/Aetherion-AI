# Z36 Execution Immersion Report

**Phase:** Z36D + Z36E — Execution Immersion + Spatial Density + Continuity Presence  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — spatial depth, density governance, focus steering, drift awareness live

---

## Spatial Depth Layers

Five depth states are assigned to DAG, timeline, inspector, and memory surfaces:

| State | CSS | Visual Effect |
|-------|-----|--------------|
| `active` | `opacity: 1` | Full visibility |
| `dormant` | `opacity: 0.55` | Clearly de-emphasized |
| `background` | `opacity: 0.75` | Visible but secondary |
| `unstable` | `opacity: 0.9 + amber border` | Subtly flagged |
| `replayed` | `opacity: 0.85 + warm border` | Distinguishable from live |

Assignment rules per phase:

| Phase | DAG | Timeline | Inspector | Memory |
|-------|-----|----------|-----------|--------|
| executing | active | background | background | dormant |
| planning | active | background | background | dormant |
| recovering | unstable | background | active | unstable |
| replay | replayed | active | background | background |
| idle | dormant | dormant | background | dormant |
| escalating | active | background | active | dormant |

All transitions use 200–300ms `ease` on `opacity` and `border-color` only — GPU composited, zero layout impact.

---

## Density Governance

Three density levels are applied via `data-z36-density` on `<html>`:

| Level | Trigger | Effect |
|-------|---------|--------|
| `compact` | nodeCount ≥ 12 OR drift = rising | Timeline rows: 2px vertical padding |
| `normal` | nodeCount 6–11 | Default timeline row padding |
| `spacious` | nodeCount < 6 | Timeline rows: 5px vertical padding |

This keeps the timeline readable at scale without requiring operator action. On complex 12-node DAGs the timeline condenses automatically; on simple 3-node tasks it breathes.

---

## Focus Steering

The top 2 hotspot nodes from `PressureMemory` (by peak pressure score) receive a `z36-steered` CSS class if their `peakPressure >= 0.5`. This applies a 3s pulsing amber outline to the DAG node element — slow enough to be directional guidance rather than an alarm.

Steering is recalculated on every `_steerAttention()` call (RAF-batched). Old steered classes are cleared before new ones are applied, preventing stale highlights.

The operator does not need to know the steering is happening — they simply notice the most unstable node is subtly asking for attention.

---

## Continuity Thread Bar

The `#z36ThreadBar` injected into `.z30-dag-panel-hdr` shows four execution state counters:
- Running (blue) · Error (red) · Done (green) · Pending (grey)

Each is a circular badge with a count. On hover, the title attribute shows the full count and state label. This gives the operator a persistent summary of DAG execution topology without requiring them to scan all nodes.

---

## Drift Awareness

`PressureMemory.recordDrift(pressure, phase)` is called every 8 seconds with the current Z35 pressure score. The drift log maintains the last 60 readings (8 minutes of data). `getDriftTrend()` compares the most recent reading to the rolling average of the prior 5:

- Rising: last > avg5 + 0.15
- Falling: last < avg5 - 0.15
- Stable: within ±0.15

The drift trend is displayed in the thread bar as `DRIFT: stable | rising | falling` with color coding. An operator watching the thread bar over time can sense "mission fatigue" — a session that has been under sustained pressure shows `rising` drift for minutes, which is a signal to consider compressing context or replanning.

---

## Remaining Continuity Limitations

1. **Pressure drift log is 60-entry cap** — at 8s intervals this covers 8 minutes. Very long sessions (>8min of sustained pressure) will lose early drift data.
2. **Thread bar badge counts** are absolute node counts — they don't distinguish between a 2-node DAG with 1 error (50% failure rate) and a 20-node DAG with 1 error (5% failure rate). Relative failure rate would be more informative.
3. **Session boundary** — thread bar clears on session start (`NodeRegistry.clear()`). Cross-session continuity threads from Z34D are not reflected in this counter.
4. **`setInterval` for drift polling** at 8s is not cleared on page unload. Should register a `beforeunload` cleanup.

---

## FPS / Memory Validation

- Spatial depth changes: CSS `opacity` + `border-color` transitions on `<div>` — GPU composited, no layout.
- Density governance: one `setAttribute` on `<html>` per RAF cycle — negligible.
- Focus steering: one `querySelectorAll` for `[data-node-id]` per update — O(n) on DAG node count, typically 5–15 elements.
- Thread bar: small HTML string re-render per update cycle — no animation.
- Drift polling: one function call every 8s — negligible CPU.
- NodeRegistry: one plain object per node. At 50 nodes × ~40 fields = ~2KB peak memory. Negligible.
- PressureMemory: 60 drift entries × 2 fields = ~120 entries. Negligible.
