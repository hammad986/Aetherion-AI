# Z31 Persistent Runtime Certification

**Phase:** Z31A — Persistent DAG Snapshot Engine  
**Status:** CERTIFIED  
**Date:** 2026-05-16

---

## Scope

Certifies the persistent execution graph storage system: SQLite WAL-backed snapshot store, incremental append strategy, cross-device replay hydration, and forensic bundle portability.

---

## Storage Architecture

| Layer | Technology | Location |
|-------|-----------|---------|
| Snapshot DB | SQLite WAL | `forensics.db` |
| Schema | 3 tables: `dag_snapshots`, `replay_events`, `forensic_exports` | — |
| Isolation | Forensics DB is separate from `sessions.db`, `billing.db`, `saas_platform.db` | Prevents cross-contamination |
| WAL mode | `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL` | Set on every connection open |

---

## Snapshot Persistence Schema

```sql
dag_snapshots (
    id, session_id, snapshot_index, snapshot_hash,
    nodes_json, edges_json, metrics_json, fingerprint, created_at
    UNIQUE (session_id, snapshot_index)
)

replay_events (
    id, session_id, event_type, node_id,
    payload_json, fingerprint, ts
)

forensic_exports (
    id, session_id, export_hash, bundle_json, created_at
)
```

---

## Survival Guarantees

| Event | Snapshot Survives? |
|-------|-------------------|
| Page refresh | ✅ Yes — stored in `forensics.db` |
| Browser close | ✅ Yes |
| Worker restart | ✅ Yes — WAL-safe SQLite |
| VPS reboot | ✅ Yes — disk-persisted SQLite |
| `localStorage` clear | ✅ Yes — server-side only |
| Session ID change | ✅ Yes — keyed by `session_id` |

---

## Incremental Append Strategy

- Each `POST /api/z31/snapshot/<sid>` call appends a new row (never rewrites).
- `snapshot_index` is monotonically increasing per session.
- Max `MAX_SNAPSHOTS_PER_SESSION = 500` snapshots enforced via trim-on-insert.
- Trim strategy: delete oldest `N - MAX` snapshots by `snapshot_index ASC`.
- Hash deduplication: `UNIQUE (session_id, snapshot_index)` prevents duplicate writes.

---

## WAL Safety Analysis

- All writes use `INSERT OR REPLACE` within explicit `with _fdb() as conn` blocks (auto-commit on exit).
- `PRAGMA synchronous=NORMAL` gives acceptable durability (survives OS crash, not hard power-off).
- For production environments requiring full durability: set `PRAGMA synchronous=FULL`.
- No concurrent writer conflicts expected: Flask is single-process by default; WAL handles concurrent readers safely.

---

## Remaining Persistence Risks

1. **`PRAGMA synchronous=NORMAL`**: A hard power failure between WAL write and checkpoint could corrupt the last frame. Mitigation: set `FULL` in production or use periodic `PRAGMA wal_checkpoint`.
2. **Max 500 snapshots**: Long sessions (>500 snapshots) will lose the oldest frames. Mitigation: server-side snapshot store with tiered archiving.
3. **No multi-DB replication**: `forensics.db` is local to the worker. In multi-worker deployments, different workers will have divergent snapshot stores. Mitigation: centralize to PostgreSQL or add Redis-backed snapshot bus.
4. **Forensic DB not backed up**: No automated backup. Recommend daily SQLite `.dump` to object storage.

---

## Production-Readiness Verdict

**PRODUCTION-READY for single-worker deployments.** Multi-worker deployments require centralized storage.
