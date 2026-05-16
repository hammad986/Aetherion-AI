# Z38 Persistent Runtime Memory

**Phase:** Z38A — Persistent Execution Memory  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — SQLite-backed node persistence with WAL, bounded retention, schema versioning

---

## What Was Built

A `z38_cognition.db` SQLite database with WAL journal mode stores complete node execution history across page reloads, browser restarts, and process restarts. All Z36 NodeRegistry records, Z37 CausalGraph lineage, pressure traces, decision chains, failure reasons, and recovery outcomes are persisted to disk on key execution events.

---

## Schema

### `z38_node_memory`
One row per execution event per node. Multiple rows per node accumulate over time (bounded to 50 per node).

| Column | Type | Content |
|--------|------|---------|
| node_id | TEXT | Stable node identifier |
| session_id | TEXT | Session this event occurred in |
| state | TEXT | Node state at write time |
| heat | REAL | Pressure score (0–1) |
| retries | INTEGER | Cumulative retry count |
| errors | INTEGER | Cumulative error count |
| dur_ms | REAL | Execution duration |
| parent_id | TEXT | Causal graph parent (from Z37) |
| branch_type | TEXT | main / retry / recovery / escalation |
| confidence | REAL | Semantic confidence (from Z32) |
| provider | TEXT | LLM provider used |
| decision_chain | TEXT | JSON array of state transitions |
| failure_reasons | TEXT | JSON array of failure log strings |
| pressure_trace | TEXT | JSON array of pressure samples |

### `z38_recovery_events`
One row per recovery action.

### `z38_evolution`
Session-level aggregate snapshots for health trend.

### `z38_schema_version`
Single-row schema version table for future migrations.

---

## WAL + Integrity

All connections use `PRAGMA journal_mode=WAL` and `synchronous=NORMAL`. This provides:
- **Concurrent readers** during writes (no lock contention with session queries)
- **Crash safety** — partial writes are never committed
- **PASSIVE checkpoint** on GC runs to fold WAL back into main database

---

## Bounded Retention

| Limit | Value |
|-------|-------|
| Max rows per node | 50 |
| Max recovery events per node | 30 |
| Max pressure trace points | 60 |
| Max total node records | 2,000 |
| Max evolution rows | 500 |

GC fires on every `POST /api/z38/gc` call (triggered from frontend every 10 minutes) and on every individual node write (per-node bounds). Global bounds are checked on GC.

---

## Persistence Write Strategy

All writes from the frontend are **fire-and-forget** `fetch()` calls. They are:
- **Non-blocking** — never hold up execution flow
- **Debounced** — writes from the same node within 2 seconds are batched
- **Tolerant of failures** — a dropped write doesn't corrupt session state

On session end, the frontend immediately flushes all queued writes and posts a final evolution snapshot.

---

## Remaining Persistence Weaknesses

1. **No write confirmation UI** — if the backend is unavailable, writes are silently dropped. The frontend has no retry mechanism for failed persists.

2. **Debounce window (2s) may miss rapid state changes** — a node that transitions from `running → error → recovered` within 2 seconds may only flush the final state, losing the intermediate failure record.

3. **Node ID stability** — log-derived phase nodes (`plan`, `code`, `debug`) use generic phase names as IDs. Multiple sessions' "plan" node records accumulate in the same node_id bucket, conflating different execution contexts.

4. **No migration system** — `z38_schema_version` is written once at bootstrap. Future schema changes require a manual migration step; there is no automated migration runner.

5. **WAL file size** — on write-heavy sessions (many small persists), the WAL file can grow before the PASSIVE checkpoint runs. Under sustained load this is bounded by SQLite's automatic checkpointing at 1000 frames, but explicit forced checkpoints on session end would reduce WAL growth.
