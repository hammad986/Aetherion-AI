# Z34 DAG ↔ Timeline Synchronization Report

**Phase:** Z34A — Bidirectional DAG/Timeline Sync  
**Date:** 2026-05-16  
**Verdict:** SYNCHRONIZED

---

## Synchronization Architecture

```
NxBus event
     │
     ├─► nx-z34-fusion.js (_ingestRow / dag.node.selected)
     │        │
     │        ├─► S.timelineEvents[]   (canonical event store)
     │        ├─► S.nodeIndex{}        (per-node execution data)
     │        └─► ReplayCursor         (shared position)
     │
     ├─► Timeline click → _onTimelineEventClick(idx)
     │        ├─► ReplayCursor.seek(idx, ts)
     │        ├─► NxDagEngine.applySnapshot(reconstructedNodes)
     │        └─► _openForensicInspector(nodeId, idx)
     │
     └─► DAG node click → dag.node.selected → _onDagNodeSelected(nodeId)
              ├─► Find earliest timeline idx for nodeId
              ├─► ReplayCursor.seek(idx, ts)
              ├─► _scrollTimelineToIndex(idx)
              └─► _openForensicInspector(nodeId, idx)
```

---

## Timeline → DAG

When a user clicks a timeline event row:

1. `_onTimelineEventClick(idx)` fires.
2. `ReplayCursor.seek(idx, ev.ts)` updates the shared cursor.
3. `_reconstructNodeStatesAt(idx)` forward-scans `S.timelineEvents[0..idx]` to rebuild the DAG node state at that moment (states: pending/running/done/error, retry counts, confidence).
4. `NxDagEngine.applySnapshot()` applies the reconstructed snapshot — restoring node states, confidence overlays, and retry counts.
5. The related node's inspector opens with timeline position context.
6. A sync pulse animates on the health bar's sync dot.

---

## DAG → Timeline

When a user clicks a DAG node:

1. `dag.node.selected` fires on NxBus (from `nx-dag.js`).
2. Z34 finds the first `S.timelineEvents` entry with matching `nodeId`.
3. `ReplayCursor.seek()` updates cursor position.
4. `_scrollTimelineToIndex()` scrolls the timeline dock to the matching row and applies a 2s highlight class.
5. Inspector opens with full node detail including retries, confidence drift, and recovery narrative.

---

## Timestamp Fidelity

All events in `S.timelineEvents` carry a `Date.now()` timestamp at ingestion. The `ReplayCursor` stores the timestamp at the current position. The forensic inspector displays the timeline position as `N / Total` rather than raw timestamps to avoid confusion between wall-clock time and execution time.

---

## Remaining Timeline Inconsistencies

- Events ingested from `agent.log_row` carry `Date.now()` at ingestion time, not the server log timestamp. This introduces a minor lag (< 100ms typical) between server event time and cursor timestamp.
- The `_reconstructNodeStatesAt` function does not replay confidence overlays from Z32 semantic data — it only replays confidence values captured in timeline events with type `conf-drop`.
- Replay of branching (non-linear) DAG topologies is approximate; the reconstruction assumes a linear node order.
