# Z36 Runtime Cohesion Audit

**Phase:** Z36 Complete  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — unified execution surface established

---

## What Changed

Before Z36, the workspace operated as five coordinated-but-separate systems: DAG (Z30), forensics (Z31), semantics (Z32), UX timeline (Z33), and forensic inspector (Z34). Each had its own state, its own node representation, and no shared identity layer.

After Z36, a single `NodeRegistry` owns all node identity, lineage, generation, state transitions, decision chain, failure reasons, recovery history, and pressure trace. Every surface references this registry. Node focus, hover, pulse, and state propagate through a single NxBus emission.

---

## Remaining Execution Coherence Gaps

1. **NodeRegistry is in-memory only.** A page refresh clears all node state. Z31 forensic snapshots are not re-hydrated into the NodeRegistry on load — this would require a Z31→Z36 rehydration step on session restore.

2. **data-node-id patching relies on MutationObserver.** If Z30 renders nodes using canvas or SVG without HTML elements carrying class names `.dag-node` / `.nx-dag-node` / `[data-id]`, the observer will find no targets. The CSS heat/pulse/focus effects require DOM elements with `data-node-id` — if Z30 uses canvas rendering, these visual effects have no target.

3. **Node identity for log-derived phase nodes** (`plan`, `code`, `debug`, `tool`, `done`) uses the phase name as the nodeId. This creates artificial nodes that may not correspond to actual DAG topology nodes. True node identity requires the backend to emit structured node IDs in log rows.

4. **Lineage tracking** is set only when `setParent(child, parent)` is called explicitly. Currently no system calls `setParent` — lineage chains remain flat. Full lineage requires Z30 to emit parent relationships in `dag.node.*` events.

---

## Remaining Replay Weaknesses

- PressureMemory is session-scoped. Replay of a historical session does not restore its pressure hotspots.
- The NodeRegistry `decisionChain` is built from live state transitions during the current session only. Historical replay from Z31 cannot reconstruct it.
- Execution pulse (`z36-pulse-active`) is a 1.4s animation triggered by live events. During replay it fires on each reconstructed node selection but may fire too rapidly if reconstruction is fast.

---

## Remaining Operator Overload Risks

- The continuity thread bar added to `.z30-dag-panel-hdr` adds four badge elements (running/error/done/pending counts). On very narrow DAG panel headers this may overflow and wrap.
- Focus steering (`z36-steered` class) applies a 3s pulsing outline to the top 2 hotspot nodes. If both top nodes are adjacent in the DAG, the two pulsing outlines may create visual noise.
- The `setInterval` drift polling runs every 8 seconds — permanent interval. It is light (one Z35 state read + two arithmetic ops) but should be cleared on session end. Currently not cleared.

---

## Honest Production Readiness Verdict

The NodeRegistry and PressureMemory modules are clean, well-bounded, and correctly scoped. Cross-surface hover sync and execution pulse work within the DOM constraints described above. The forensic section appended to Z34's inspector correctly shows decision chains, failure pressure analysis, and recovery intelligence from live data. The MutationObserver-based `data-node-id` patching is the highest-risk element — its effectiveness depends entirely on Z30's DOM rendering strategy.
