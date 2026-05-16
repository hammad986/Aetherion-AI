# Z31 Runtime Memory Analysis

**Phase:** Z31E — Runtime Memory + Scaling Discipline  
**Status:** AUDITED  
**Date:** 2026-05-16

---

## Memory Layers

### 1. Frontend DAG Surface (`NxDagEngine`)

| Structure | Estimated Size | Notes |
|-----------|---------------|-------|
| In-memory snapshot buffer | ~200 snapshots × 20 nodes × 400 bytes = ~1.6 MB max | Capped in `_guardSnapshotSize()` at 1 MB → trim to 50 |
| SVG DOM (30 nodes) | ~240 DOM nodes × 200 bytes avg = ~48 KB | Rebuilt on each render |
| `localStorage` replay | Same ~1.6 MB, persisted | Guarded by size check |

### 2. Z31 Frontend Controller (`nx-z31-forensics.js`)

| Structure | Estimated Size | Notes |
|-----------|---------------|-------|
| `_cache.snapshots` | Copy of last 200 server snapshots (metadata only, no full node JSON) | ~200 × 300 bytes = ~60 KB |
| `_cache.sessions` | Last 50 session summaries × 200 bytes = ~10 KB | Refreshed every 30s |
| `_cache.integrity` | Per-session integrity report | ~500 bytes per session |

### 3. Backend `forensics.db`

| Table | Row Size | Max Rows | Max Table Size |
|-------|---------|---------|--------------|
| `dag_snapshots` | ~2–10 KB (node JSON varies) | 500/session × N sessions | ~100 MB for 1000 sessions |
| `replay_events` | ~200 bytes | ~500/session | ~5 MB for 1000 sessions |
| `forensic_exports` | ~50 bytes (summary only) | Unbounded | ~5 MB for 10K exports |

**DB growth rate at steady state:** ~500 KB/session. 100 active sessions/day → ~50 MB/day → cleanup required after ~1–2 weeks without pruning.

---

## Memory Leak Vectors

### Frontend

1. **SVG node accumulation:** `_render()` calls `while (_svg.firstChild) _svg.removeChild(...)`. Full clear before each rebuild — no orphan risk. ✅
2. **Snapshot buffer growth:** `_guardSnapshotSize()` trims to 50 when >1 MB. ✅
3. **NxBus listeners:** All listeners registered with `{ owner: 'z31' }`. Not yet cleared on unmount — minor leak if Live tab is destroyed. Mitigation: call `NxBus.offOwner('z31')` on tab destroy.
4. **Interval timers:** `_pollTimer` and `_snapshotTimer` stored in module state, cleared on session end. If session crashes without calling `_onSessionEnd()`, timers leak. Mitigation: `visibilitychange` or `beforeunload` listener to force cleanup.

### Backend

5. **`forensic_exports` table:** No cleanup policy implemented. Grows unbounded. Mitigation: TTL-based janitor.
6. **WAL file growth:** Under high write load, WAL file grows until checkpoint. Auto-checkpoint at 1000 pages (~4 MB). Acceptable for normal load.
7. **`replay_events` table:** No per-session cap. A pathological session with millions of log rows could produce millions of replay events. Mitigation: add `MAX_EVENTS_PER_SESSION = 2000` trim.

---

## Replay Hydration Latency

| Scenario | Estimated Latency | Notes |
|----------|------------------|-------|
| 20 snapshots, 20 nodes each | <50ms | Single SQLite read |
| 200 snapshots, 50 nodes each | <200ms | Index scan over snapshot range |
| 500 snapshots, 100 nodes each | 300–600ms | Full table scan (no index on node count) |
| Cross-device (cold load) | +RTT for `GET /api/z31/snapshot/<sid>/latest` | ~50–200ms depending on network |

**Recommended optimization:** Index `(session_id, snapshot_index DESC)` for latest-snapshot queries — already implemented.

---

## Graph Serialization Cost

| Node Count | `json.dumps()` Time | `gzip.compress()` Time |
|-----------|-------------------|----------------------|
| 20 nodes  | <1ms              | <5ms                 |
| 100 nodes | ~2ms              | ~15ms                |
| 500 nodes | ~10ms             | ~60ms                |
| 1000 nodes | ~20ms            | ~100ms               |

**Ceiling:** Serialization is not on the hot render path. Export bundles are generated on-demand. Acceptable.

---

## Verdict

**STABLE for sessions up to 200 nodes / 500 snapshots.** Memory leak vectors #4 (interval cleanup) and #7 (replay_events cap) should be addressed before deploying to sessions expected to run for >4 hours.
