# Z34 Replay Fusion Audit

**Phase:** Z34A — DAG ↔ Timeline Forensic Fusion  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL

---

## What Was Built

A shared `ReplayCursor` singleton coordinates replay position across the DAG engine, the timeline dock, the forensic inspector, and the replay viewer. When the user clicks a timeline event, the DAG reconstructs node states at that exact moment and scrolls the inspector to match. When the user selects a DAG node, the timeline dock seeks to the first event for that node and highlights it. All four surfaces share a single position counter and timestamp.

---

## Remaining Replay Weaknesses

| Item | Status |
|------|--------|
| Cursor relies on in-memory timeline events — not persisted across page refresh | Known gap |
| Replay reconstruction uses forward-scan O(n) over events; acceptable for ≤500 events | Acceptable |
| `_reconstructNodeStatesAt` rebuilds from scratch on every seek (no snapshot cache) | Optimize if replay > 200 events |
| No scrubber drag UI for the shared cursor — only click-to-seek via timeline rows | Future: Z34 Phase 2 |

---

## Remaining Timeline Inconsistencies

- Timeline rows must be rendered before `_wireTimelineRows()` can attach click handlers. A 300ms debounce is used; rapid session restarts may miss the first batch of events.
- Timeline events with no `nodeId` (e.g., session-start, compression) do not participate in DAG seek — by design.
- Grouped timeline rows (≥3 same type) count as a single click target, seeking to the group's first event index.

---

## Remaining Operator Confusion Risks

- The inspector panel slides in from the right and overlaps the DAG surface on narrow viewports. A min-width guard is advisable.
- The `REPLAY` vs `LIVE` depth indicator appears near the replay mode label; two similar indicators may confuse operators briefly — consolidation planned.

---

## Remaining Visual Clutter Zones

- The continuity panel appended to Z31 adds a third scrollable section inside an already compact panel. Recommend collapsible toggle in next iteration.
- Confidence drift strip requires ≥2 data points to render; appears blank on single-execution nodes.

---

## Remaining Forensic Blind Spots

- Recovery narrative is reconstructed from node flags set during live execution. If a session is loaded from a historical snapshot (Z31), recovery flags are not available.
- Dependency lineage uses timeline event order as a proxy for actual graph edges — accurate for linear pipelines, approximate for concurrent branches.

---

## Honest Operational Verdict

The replay fusion layer works. Timeline clicks seek the DAG correctly. DAG node selection seeks the timeline correctly. The shared cursor is clean and extensible. The inspector shows reasoning context that was previously scattered across three separate panels. No regressions to existing Z30–Z33 systems were introduced.
