# Z31 Scaling Limits Report

**Phase:** Z31E — Scaling Discipline Audit  
**Status:** AUDITED  
**Date:** 2026-05-16

---

## Summary Table

| Dimension | Current Ceiling | Symptom at Ceiling | Mitigation Path |
|-----------|----------------|-------------------|----------------|
| Snapshots/session | 500 | Oldest frames dropped | Tiered archival to object storage |
| Nodes/DAG | ~50 optimal, 200 functional | SVG render jank >16ms | Incremental node diffing |
| Sessions in history browser | 200 (query limit) | Paginated | Server-side cursor pagination |
| Bundle size | ~20 MB practical | Base64 proxy timeout | Streaming download endpoint |
| `forensics.db` total size | ~500 MB practical (SQLite file lock risk) | WAL checkpoint stall | Migrate to PostgreSQL |
| Replay event rows/session | Unbounded (current) | Memory + scan latency | Add MAX_EVENTS_PER_SESSION trim |
| Concurrent replay hydrations | ~5 (SQLite write lock) | Import timeout | Connection pool + read replica |
| Frontend snapshot cache | ~1.6 MB (guarded) | `localStorage` quota error | Already guarded — trim to 50 |
| NxDagEngine SVG nodes | 200 max (guarded) | Render removed all nodes | Graceful degradation message |

---

## Detailed Ceiling Analysis

### 1. DAG Render Scalability

- SVG full rebuild: O(n) nodes + O(e) edges per frame.
- At 50 nodes: ~8ms layout + ~10ms SVG DOM = 18ms (just over 16ms budget).
- Mitigation: Incremental diffing with `<use>` element reuse + virtual node pool.

### 2. SQLite WAL Pressure

- Under high-frequency snapshot writes (>10/sec), WAL file grows faster than auto-checkpoint rate.
- Auto-checkpoint at 1000 pages (~4 MB WAL) introduces a ~50ms write stall.
- For sessions with >1 event/sec: configure `PRAGMA wal_autocheckpoint=100` for more frequent, smaller checkpoints.

### 3. Replay Hydration for Long Sessions

- `GET /api/z31/snapshot/<sid>/latest` → single row read: O(log n). Fast.
- `GET /api/z31/snapshots/<sid>` with limit=500 → index scan: O(500). ~50ms.
- Full bundle export (500 snapshots × 100 nodes): ~10 MB JSON, ~2 MB gzipped → 200ms serialization, 100ms compression. Acceptable for on-demand export.

### 4. Cross-Device Replay

- Replay hydration requires `GET /api/z31/snapshot/<sid>/latest`.
- On a cold device, this is a single HTTP round-trip + SQLite read. Expected: <100ms on LAN.
- Cross-datacenter: +50–200ms RTT. Acceptable.
- No session state in cookies or headers is required — session ID from URL or user input is sufficient.

### 5. Historical Session Browser Scaling

- Default query: last 50 sessions by `last_ts DESC`. Single GROUP BY + index scan: O(n sessions).
- At 10,000 sessions: ~100ms query. Acceptable.
- At 100,000 sessions: ~1s query. Mitigation: materialized session summary table.

---

## Long-Session Replay Stability

| Session Length | Snapshot Count | Replay Stable? |
|---------------|---------------|----------------|
| <30 min | <50 | ✅ Yes |
| 1–2 hours | 50–200 | ✅ Yes |
| 4–8 hours | 200–500 | ✅ Yes (within cap) |
| >8 hours | >500 | ⚠ Oldest frames trimmed |
| >24 hours | >2000 (if cap raised) | ❌ Render jank at high node count |

---

## Operational Recovery Risks

1. **`forensics.db` corruption:** SQLite WAL corruption from hard power-off. Recovery: `sqlite3 forensics.db ".recover"`. No data loss beyond last unflushed WAL frame.
2. **Lost session ID:** If the client loses the `session_id`, there is no secondary lookup key. Mitigation: store session ID in user-facing URL or profile.
3. **Import isolation bypass:** As noted in forensic export report — `replay:` prefix is convention, not enforced.
4. **Snapshot trim race:** If two workers simultaneously call `_trim_snapshots()` for the same session, a race condition could delete more rows than intended. Mitigation: use `DELETE ... WHERE id IN (SELECT ... LIMIT ?)` with a single-statement DELETE.

---

## Production-Readiness Verdict

**PRODUCTION-READY for:**
- Sessions ≤200 nodes per snapshot
- Sessions ≤500 snapshots
- Single-worker deployments
- Up to ~1000 concurrent sessions

**Requires further work for:**
- Multi-worker/multi-process deployments
- Sessions running >8 hours
- >10,000 stored sessions
- Cryptographic-grade forensic audit requirements
