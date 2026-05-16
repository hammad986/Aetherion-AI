# Z37 Dependency Pressure Propagation Report

**Phase:** Z37B — Dependency Pressure Propagation  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — propagation, cascade detection, bottleneck ranking, cooling live

---

## Pressure Inheritance Model

When a node's heat score rises (error, retry, confidence drop), `PressurePropagation.propagate(nodeId, sourcePressure)` distributes pressure to all direct children:

```
child.heat += sourcePressure × INHERIT_FACTOR (0.45)
```

If the inherited pressure exceeds 0.10, propagation continues recursively to grandchildren with further decay:
```
grandchild.heat += sourcePressure × 0.45 × 0.45 = sourcePressure × 0.20
```

The recursive call stops when inherited pressure falls below 0.10 (typically after 2-3 levels). This prevents pressure from propagating indefinitely through deep graphs.

### Example: 4-node chain, root error at 0.8

| Node | Inherited Pressure |
|------|-------------------|
| plan (root) | 0.80 (source) |
| code (child) | 0.80 × 0.45 = 0.36 |
| debug (grandchild) | 0.36 × 0.45 = 0.16 |
| done (great-grandchild) | 0.16 × 0.45 = 0.07 (below threshold → stops) |

---

## Cascade Detection

`detectCascades()` scans all nodes with `heat >= 0.6` (the `CASCADE_THRESHOLD`). For each, it resolves the subtree via `CausalGraph.getSubtree()` and counts nodes with `heat >= 0.3`. If ≥ 2 subtree nodes are unstable, a cascade is reported as `{root, affected[], maxPressure}`.

Cascades are displayed in the forecast bar as red `cascade` badges. Hovering shows the affected node list in the title attribute.

---

## Bottleneck Intelligence

`detectBottlenecks()` ranks nodes by `retries + errors` descending. The top 3 are returned as bottleneck objects. These are factored into the system risk forecast and displayed in the Z37C `Predictive Failure` surface.

---

## Pressure Cooling

`startCooling(nodeId)` fires on `dag.node.done` and `z32.replan.applied`. It runs 6 cooling cycles at 2.5s intervals:

```
node.heat = max(0, node.heat − COOLING_RATE)   where COOLING_RATE = 0.08
child.heat = max(0, child.heat − 0.04)          // half-rate for children
```

A fully hot node (heat = 1.0) cools to 0.52 after 6 cycles (~15 seconds). This gives operators visible confirmation that a recovered node is stabilizing without an abrupt heat reset.

---

## Remaining Cascade Detection Weaknesses

1. **`getSubtree()` uses CausalGraph BFS** — requires edges to be populated. On sessions where lineage inference hasn't run yet (early session), subtrees may be empty, suppressing cascade detection.

2. **`CASCADE_THRESHOLD = 0.6` is a fixed constant.** On long sessions where overall pressure is elevated, many nodes may exceed this threshold simultaneously, producing many cascade reports. Adaptive threshold (mean + 1.5σ) would be more discriminating.

3. **Cooling intervals are not cleared on session end** — `setInterval` inside `startCooling` continues for 15 seconds after it fires. If a new session starts within that window, cooling events from the previous session will apply to the new NodeRegistry. Mitigation: `clear()` wipes all node records, so the cooling `get()` call will return null and silently no-op.

4. **Pressure inheritance assumes all children run after the parent.** In recovery-replan scenarios where a new child replaces a failed one, the new child may be registered before its parent is marked `done`. This can propagate pressure forward into the replacement node incorrectly.
