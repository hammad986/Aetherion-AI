# Z30 Replay Graph Forensics Report

**Phase:** Z30C — Replay + Forensic Navigation  
**Status:** CERTIFIED  
**Date:** 2026-05-16

---

## Replay System Architecture

### Frontend Replay (NxDagEngine)

- **Snapshot buffer:** In-memory circular buffer, max 200 snapshots.
- **Persistence:** `localStorage` keyed by `nx_dag_replay:<sid>`, version-stamped.
- **Step controls:** `replayStep(±delta)` — integer step through snapshot array.
- **Scrubber:** HTML range input (`z30ReplayScrubber`) maps directly to snapshot index.
- **Export/Import:** JSON format with version validation. Downloadable as `.json`.

### Backend Replay (ExecutionReplayEngine)

- **Storage:** SQLite `event_log` table in `ExecutionStore` (append-only).
- **Reconstruction:** `ExecutionReplayEngine.reconstruct_timeline(execution_id)` folds events into a semantic timeline.
- **Time-seek:** `seek_state(execution_id, target_timestamp)` reconstructs state at any point in time.
- **API endpoint:** `GET /api/z30/dag/<sid>/timeline` returns chronological events for forensic inspection.

---

## Forensic Navigation Features

| Feature | Implemented | Notes |
|---------|-------------|-------|
| Node replay (step back/forward) | ✅ | Frontend: NxDagEngine snapshots |
| Replay scrubber | ✅ | `z30ReplayScrubber` range input |
| Retry lineage | ✅ | Retry count badges + dashed edges |
| Replan lineage | ⚠ | Not yet structured — heuristic only |
| Recovery lineage | ⚠ | Detected from `mission_recovery.py` log patterns |
| Timeline ↔ DAG sync | ✅ | `data-timeline-phase` click → `_showIntelPanel()` |
| DAG node → timeline jump | ✅ | `dag.node.selected` NxBus event → intel panel |
| Override history | ⚠ | `override_engine.py` logs captured but not DAG-linked |
| Export replay JSON | ✅ | `_z30.replayExport()` downloads session graph |
| Import replay JSON | ✅ | `NxDagEngine.replayImport()` with schema validation |

---

## Replay Weaknesses

1. **Session boundary**: Replay snapshots are per-session, per-browser tab. A page refresh loses in-memory snapshots (only `localStorage` version survives).
2. **Snapshot granularity**: Snapshots are taken on each `applySnapshot()` call. High-frequency sessions (>10 events/sec) may drop intermediate states.
3. **Corrupt data handling**: `_validateReplayPayload()` checks version + array types but does not validate individual node schemas. Malformed node data silently passes.
4. **Multi-device**: `localStorage` replay is not shared across devices or tabs. Server-side replay store required for multi-device forensics.

---

## Replay Integrity Validation

- [x] Replay rebuilds from snapshot buffer correctly
- [x] Step prev/next maintains index bounds (clamped to 0..total-1)
- [x] Live mode resumes correctly after `replayStop()`
- [x] Export JSON passes `_validateReplayPayload()` on re-import
- [x] Corrupt localStorage data triggers warning + removal
- [x] Session change clears snapshot buffer

---

## Production-Readiness Verdict

**PRODUCTION-READY for single-session forensics.** Multi-device and long-session (>8h) replay requires server-side snapshot store.
