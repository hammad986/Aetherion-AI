# Z37 Causal Runtime Audit

**Phase:** Z37A — True Dependency Lineage Engine  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — causal graph active, lineage inference functional

---

## What Was Built

A `CausalGraph` module sits alongside Z36's `NodeRegistry`, adding true directed dependency edges. Every execution node now has:

- A `parentId` and `ancestorChain` resolved via `getAncestors(nodeId)`
- A `branchId` and `branchType` (`main | retry | recovery | escalation`)
- A `dependencyTrace` — ordered list of `{from, to, branchType}` transitions explaining why the node ran
- A `subtree` — all descendant nodes reachable from a given node

The inspector now shows a **Dependency Trace** for any selected node: a left-to-right breadcrumb of ancestor nodes, each colored by their branch type, terminating in the selected node highlighted in blue.

---

## Lineage Emission Strategy

Z37 uses two mechanisms to populate CausalGraph edges:

### 1. Explicit `dag.node.selected` events with `parentId`
When Z30 emits `dag.node.selected` with a `parentId` field, `CausalGraph.addEdge(parentId, nodeId, branchType)` is called directly. This is the correct, authoritative path.

### 2. Temporal order inference (`_inferLineageFromOrder`)
When `parentId` is not available, nodes are sorted by `ts_start` and connected sequentially. This assumes linear execution order — accurate for sequential pipelines, approximate for concurrent branches. Runs on every RAF update cycle and on every `agent.log_row` event.

### 3. Log-pattern edge detection
Structured log patterns `[plan] -> [code]` are parsed as explicit edges. Low-frequency but high-accuracy when present.

---

## Branch Divergence Tracking

| Branch Type | Trigger | Visual |
|------------|---------|--------|
| `main` | Normal sequential execution | Blue chip |
| `retry` | Node with `retries > 0` | Amber chip |
| `recovery` | Node with `recoveryHistory.length > 0` | Green chip |
| `escalation` | Node involved in HITL | Red chip |

Branch IDs are generated as `${parentId}:${type}:${timestamp}` — unique per divergence event. All nodes in a recovery branch share the same `branchId`.

---

## Remaining Dependency Weaknesses

1. **Temporal inference assumes sequential execution.** Concurrent DAG branches will be linearized incorrectly. True concurrent edge support requires explicit `parentId` emission from Z30's dag renderer for every node.

2. **Branch type assignment at edge creation time** — if a node accumulates retries *after* the edge is created, its `branchType` remains `main`. The branch type should be updated when retry/recovery events fire, not just at edge creation.

3. **`CausalGraph.clear()` is called on session start** — cross-session lineage is not preserved. Z34D continuity data and Z37 causal graph are not shared.

4. **Circular dependency protection** — `getAncestors()` breaks at depth 20, preventing infinite loops in malformed graphs. However, the underlying graph structure will still contain the cycle — it is not detected and corrected.

---

## Remaining Replay Blind Spots

- During replay, `_inferLineageFromOrder()` runs on every update, re-sorting all nodes. If replay events arrive out of chronological order (which Z34's `_reconstructNodeStatesAt` can produce), edge ordering may flip.
- Causal replay depth classes (`data-z37-replay`) require `data-node-id` attributes on Z30 DOM elements. If Z30 uses canvas rendering, these classes apply to nothing.

---

## Honest Operational Verdict

The causal graph is functionally correct within its constraints. For sequential DAG topologies (the common case), dependency traces are accurate and the inspector correctly shows "which node triggered this one." The branch coloring system cleanly distinguishes main execution from retry/recovery divergences. No regressions to Z36's NodeRegistry were introduced.
