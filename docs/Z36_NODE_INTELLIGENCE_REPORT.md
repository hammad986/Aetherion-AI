# Z36 Node Intelligence Report

**Phase:** Z36A — DAG Node Identity + Execution Cohesion  
**Date:** 2026-05-16  
**Verdict:** FOUNDATIONAL — stable identity layer, generation tracking, cross-surface sync

---

## NodeRegistry Data Model

Every node known to the runtime is stored as a `NodeRecord`:

```javascript
{
  id:              string,        // stable node identifier
  lineageId:       string,        // same as id; stable across retries
  replayId:        string,        // "${id}:${generation}" — changes with each retry
  parentId:        string|null,   // parent node in DAG topology
  generation:      number,        // retry/replan generation count
  state:           string,        // pending | running | done | error
  heat:            number,        // 0-1 instability score
  confidence:      number|null,   // 0-1 semantic confidence
  provider:        string|null,   // last LLM provider used
  tokens:          number,        // cumulative token count
  retries:         number,
  errors:          number,
  ts_start:        number|null,
  ts_end:          number|null,
  dur_ms:          number|null,
  lastLog:         string|null,
  decisionChain:   [{from, to, ts}],     // state transition history (max 20)
  failureReasons:  [{reason, ts, gen}],  // failure log entries (max 8)
  recoveryHistory: [{action, success, ts}], // recovery outcomes (max 8)
  pressureTrace:   [{p, ts}],            // pressure history (max 30)
}
```

`replayId` changes on each retry generation, allowing replay systems to distinguish "node X on first attempt" from "node X on second attempt" even within the same session.

---

## Execution Generation Tracking

When a retry is detected on a node (via `agent.log_row` text or `dag.node.error` + subsequent `dag.node.selected`), `NodeRegistry.upsert(id, { _retry: true })` increments `generation` and updates `replayId`. This provides a monotonically increasing generation counter per node — equivalent to a "version" of the node's execution attempt.

---

## Cross-Surface Synchronization

Hover events on the DAG surface and timeline dock propagate through `_propagateFocus(nodeId, source)`:

1. Source fires `NxBus.emit('z36.node.focus', { id, source })`
2. All subscribers receive the event and can highlight their corresponding element
3. DAG focus → CSS class `z36-focus-ring` (1px blue outline, 120ms transition)
4. Timeline focus → CSS class `z36-timeline-focus` (left border + background tint)
5. Inspector soft-opens via Z34 for the hovered node

On `mouseleave` from either surface, focus is cleared across all surfaces simultaneously.

---

## data-node-id Patching (MutationObserver)

The `_startNodeObserver()` function attaches a `MutationObserver` to `#z30DagSurface` watching for `childList` changes (including subtree). When Z30 renders new node elements, the observer fires `_auditNodeIds()` which:

1. Queries for `.dag-node, .nx-dag-node, [data-id], [data-nodeid], .z30-node, g[id]`
2. For each element lacking `data-node-id`, reads `data-id` / `data-nodeid` / `id` attribute as the node id
3. Sets `data-node-id` on the element
4. Registers the node in `NodeRegistry` if not yet known

This runs once on init (for existing nodes) and on each DOM mutation (for newly added nodes). It is passive — it never modifies Z30's rendering logic.

---

## Execution Pulse Routing

`_emitExecutionPulse(nodeId)` applies CSS class `z36-pulse-active` to:
- The DAG element: `[data-node-id="${nodeId}"]`
- All timeline rows: `[data-z36-node-id="${nodeId}"]`

The pulse is a 1.4s `@keyframes` animation that expands a blue outline ring and fades to transparent. It self-clears via `setTimeout(1400)`. Rapid pulses are not queued — a new pulse immediately replaces the previous one.

---

## Remaining Gaps

- `setParent()` is never called currently — lineage chains are flat. Z30 must emit `{ parentId }` in `dag.node.selected` for true lineage.
- `data-id` / `data-nodeid` attribute presence depends on Z30's DAG renderer. If Z30 uses SVG `<g>` elements with `id` only (no `data-id`), patching will use the `id` — which may be an auto-generated DOM id rather than a semantic node id.
- The NodeRegistry `clear()` wipes all nodes on session start. Background/queued DAG renders from the previous session may re-add stale nodes if Z30 doesn't clear its canvas on session start.
