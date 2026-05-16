# Z45_CAUSAL_SYNCHRONIZATION_REPORT.md
## Phase Z45A — Causal Execution Synchronization Report

---

### Pre-Z45 Synchronization State

Contrary to the Z44 deferral notes, Z36 already implemented full DAG ↔ Timeline hover sync:

```javascript
// Z36 already implemented (nx-z36-cohesion.js lines 239-302):
dagSurface.addEventListener('mouseover', _onDagHover)     // DAG hover → NxBus
tlDock.addEventListener('mouseover', _onTimelineHover)    // Timeline hover → NxBus
NxBus.emit('z36.node.focus', { id: nodeId, source })      // Cross-surface event
// CSS: .z36-focus-ring on DAG nodes
// CSS: .z36-timeline-focus on timeline events
```

**Z45A therefore focused on:**
1. Visual polish of the existing `.z36-focus-ring` and `.z36-timeline-focus` CSS classes
2. Adding a label-match fallback for timeline events without `data-z36-node-id` attributes
3. Wiring state-derived colors into the focus ring (error state → red ring, done state → green ring)

---

### Synchronization Architecture (Final State)

```
DAG surface hover (#z30DagSurface)
  ↓ [mouseover → _onDagHover → finds data-node-id]
  ↓ [_propagateFocus → NxBus.emit('z36.node.focus', {id, source:'dag'})]
  ↓
NxBus 'z36.node.focus' subscribers:
  ├── Z36 → highlights timeline [data-z36-node-id=id] with .z36-timeline-focus
  ├── Z36 → soft-opens inspector for this node
  ├── Z36 → emits execution pulse on node
  └── Z45 → adds .z45-soft-focus on label-match events (no data-z36-node-id)

Timeline hover (#z33TimelineDock)
  ↓ [mouseover → _onTimelineHover → finds data-z36-node-id]
  ↓ [_propagateFocus → NxBus.emit('z36.node.focus', {id, source:'timeline'})]
  ↓
NxBus 'z36.node.focus' subscribers:
  ├── Z36 → highlights DAG node [data-node-id=id] with .z36-focus-ring
  ├── Z36 → soft-opens inspector  
  └── Z45 → skips (source='timeline', no feedback loop)

Z45 label fallback (for timeline events lacking data-z36-node-id):
  ↓ [global mouseover → .z33-tl-event:not([data-z36-node-id])]
  ↓ [NxBus.emit('z36.node.focus', {label: textContent, source:'z45-timeline-label'})]
  ↓ Z45 listener adds .z45-soft-focus to DAG nodes whose id contains label
```

---

### Causal Flow Visualization

Timeline events now carry visual type differentiation via `data-event-type`:
- `retry` → amber left border
- `recovery` → green left border
- `replan` → cyan left border
- `node-done` → green (dim) left border
- `general` → transparent

DAG nodes with pressure tiers show right-edge stripes:
- `critical` → red stripe
- `high` → amber stripe
- `medium` → yellow stripe

Decision chain arrows in the Z36 forensic section now use `→` with state-colored from/to labels, making the causal path readable at a glance.

---

### Remaining Synchronization Gaps

1. **`data-z36-node-id` coverage**: Timeline events only carry this attribute when Z36's `_wireTimelineReplayReconstruction` processes them — which requires `z36.node.focus` to have fired first. On first page load, timeline events may lack IDs until the user interacts with the DAG. The Z45 label-match fallback covers this case.

2. **Replay cursor synchronization**: Z33's timeline replay cursor position is internal to Z33's JS state. Z45 can set CSS classes (`.z45-replay-current`, `.z45-replay-past`) but the JS to determine WHICH event is "current" during replay requires knowing Z33's internal cursor index — not exposed. Visual replay CSS is ready; cursor-binding is deferred.

3. **Inspector ↔ surface sync**: When the inspector shows a node's details (via Z36's `_softOpenInspector`), clicking a different area doesn't clear the inspector. This is Z36's intended behavior (persistent inspection) — not a Z45 concern.

4. **Retry lineage visualization**: The chain of retry nodes (node A retry → node A' → node A'') is visualized in Z36's decision chain but not connected visually in the DAG (no visual arrows between retry siblings). This requires DAG rendering changes — not CSS-achievable.

### Production Readiness Verdict

> **PASS** — Cross-surface synchronization was already implemented in Z36. Z45 polished the visual output, added label-match fallback for ID-less timeline events, and unified focus ring colors with the Z44 state system. All sync remains event-driven via NxBus with zero polling cost.
