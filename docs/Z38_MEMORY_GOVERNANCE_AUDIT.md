# Z38 Memory Governance Audit

**Phase:** Z38E — Operational Memory Governance  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — bounded retention, WAL integrity, amplification guard, GC scheduler live

---

## Memory Bounds

| Resource | Limit | Enforcement |
|----------|-------|-------------|
| Node records per node_id | 50 | `_gc_node()` on every write |
| Recovery events per node_id | 30 | `_gc_node()` on every write |
| Pressure trace points per node | 60 | Sliced in `POST /api/z38/memory` |
| Decision chain entries | 20 | Sliced in `POST /api/z38/memory` |
| Failure reason entries | 8 | Sliced in `POST /api/z38/memory` |
| Total distinct node_ids | 2,000 | `_gc_global()` on GC call |
| Evolution rows | 500 | `_gc_global()` on GC call |

All bounds are enforced server-side — the frontend cannot exceed them regardless of what data it sends.

---

## WAL Integrity

- `PRAGMA journal_mode=WAL` — all connections
- `PRAGMA synchronous=NORMAL` — durability without fsync on every write
- `PRAGMA foreign_keys=ON` — referential integrity (no orphan recovery records)
- `PRAGMA wal_checkpoint(PASSIVE)` — called on every `POST /api/z38/gc`

SQLite's WAL mode means:
- Writers never block readers
- Readers never block writers  
- The WAL file accumulates until checkpointed
- Crash recovery automatically applies the WAL on next open

---

## GC Scheduler

The frontend calls `POST /api/z38/gc` every 10 minutes via `setInterval`. The backend runs:
1. `_gc_global()` — prunes to 2,000 nodes + 500 evolution rows
2. `PRAGMA wal_checkpoint(PASSIVE)` — folds WAL into main DB file

The GC interval is started once in `_scheduleGC()` and is not cleared on session end. This is intentional — GC should run periodically regardless of whether a session is active.

---

## Pressure Amplification Guard

`_guardPressureAmplification()` is called on every `agent.log_row` event. It scans all nodes in the Z36 NodeRegistry and caps `heat` at 1.0. This prevents the Z37 pressure propagation cascade from exceeding the 0–1 range through repeated inheritance calls.

The guard is a hard ceiling — not a soft correction. It is appropriate because:
1. The heat scale (0–1) maps to visual and risk signals; exceeding 1.0 produces no additional information
2. Z37's `INHERIT_FACTOR=0.45` means propagation naturally decays, but edge cases (rapid retry storms with multiple children) could briefly push heat above 1.0 before cooling catches up

---

## Recursion + Loop Protection

CausalGraph's `getAncestors()` breaks at depth 20 (loop guard). PressurePropagation's recursive `propagate()` stops when inherited pressure < 0.10 (natural decay stop). Both prevent infinite loops in malformed dependency graphs.

---

## Remaining Memory Scaling Risks

1. **`setInterval` in `_scheduleGC()` is never cleared** — if the page runs for very long periods, multiple GC calls will fire in the background. This is safe (GC is idempotent) but adds unnecessary network calls on idle pages.

2. **`z38_cognition.db` has no maximum file size** — SQLite itself doesn't enforce a file size limit. On systems with very active sessions over many hours, even with GC, the DB can grow if GC intervals are missed (e.g., browser tab in background prevents `setInterval`).

3. **`_gc_global()` uses `max(updated_at)` to determine which nodes to prune** — this correctly preserves recently active nodes. However, a node that was seen once long ago and never updated will be pruned even if it's historically important (high instability count). A combined score (recency × instability) would be a better pruning key.

4. **No size monitoring** — the frontend never checks `z38_cognition.db` file size or row counts. An admin endpoint showing DB health metrics would allow operators to monitor growth.

5. **Frontend `S.flushQueue` is unbounded in memory** — if many nodes are queued before the 2-second debounce fires, the queue can grow large. In practice this is bounded by the number of DAG nodes (typically < 20), but a max-queue-length guard of 100 would be defensive.
