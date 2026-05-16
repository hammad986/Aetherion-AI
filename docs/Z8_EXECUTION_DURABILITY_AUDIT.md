# Z8 Execution Durability Audit
**Phase Z8 — Phase 1: Distributed Execution Hardening**
**Date:** 2026-05-16 | **Status:** COMPLETE

---

## Executive Summary

All execution lifecycle transitions have been mapped.  Durability depends on
SQLite WAL mode for persistence and Redis (when available) for cross-worker
coordination.  Every critical state transition is written to SQLite before
any in-memory update, making the system recoverable from a clean restart.

---

## 1 — Execution Lifecycle Transitions

```
[queued] ─► [running] ─► [completed]
                │
                ├─► [failed]
                │
                └─► [stopped]
```

| Transition | SQLite Write | Redis Write | Durability |
|---|---|---|---|
| `enqueue_task()` → queued | `db_insert_session()` before queue push | `nx:queue` LPUSH | DURABLE — DB first |
| `queue_worker()` → running | `db_update_session(status='running')` inside `run_session` | `nx:running:<wid>` | DURABLE |
| Agent loop → log entry | `db_insert_log(sid, seq, ts, ...)` every emit | `nx:running:<wid>` seq update | DURABLE |
| `run_session` → completed | `db_update_session(status='completed')` | `release_running()` | DURABLE |
| `run_session` → failed | `db_update_session(status='failed', exit_code=-1)` | `release_running()` | DURABLE |
| `stop_running_session()` | `db_update_session(status='stopped')` | Redis stop signal | DURABLE |

---

## 2 — Replay Persistence

| Replay Type | Storage | Persistence | Recovery Path |
|---|---|---|---|
| SSE event replay | In-process `SSEManager._replay_buffers` | Process lifetime | Redis replay or SQLite |
| Redis SSE replay | `nx:replay:<sid>` LIST | 1-hour TTL | Available to reconnecting clients |
| Execution log replay | SQLite `logs` table, all entries | Permanent | `/api/logs/<sid>` endpoint |
| Session state replay | SQLite `sessions` table | Permanent | `/api/session/<sid>` endpoint |

---

## 3 — WAL Checkpoint Timing

SQLite WAL mode is enabled on all three databases (`sessions.db`, `billing.db`,
`saas_platform.db`) via `PRAGMA journal_mode=WAL` executed at connection open
time.

| Database | WAL Mode | Checkpoint Strategy | Notes |
|---|---|---|---|
| `sessions.db` | YES | Automatic (default 1000 pages) | Main execution DB |
| `billing.db` | YES | Automatic | Payment/subscription data |
| `saas_platform.db` | YES | Automatic | Auth and user data |

**WAL checkpoint behaviour:**
- Automatic checkpoint fires when WAL reaches 1000 pages (~4MB)
- `PRAGMA wal_autocheckpoint=1000` (SQLite default)
- No explicit `PRAGMA wal_checkpoint(TRUNCATE)` calls — relies on auto-checkpoint
- Readers never block writers in WAL mode
- Writers serialised per-database via `_db_lock` (threading.Lock)

**Concurrent write safety:**
```
Thread A: _db_lock.acquire() → execute INSERT → _db_lock.release()
Thread B: blocks on _db_lock.acquire() → executes after A
```

All database helpers (`db_insert_session`, `db_update_session`, `db_insert_log`,
etc.) acquire `_db_lock` before the connection context, ensuring serialised
writes from multiple Flask worker threads.

---

## 4 — Recovery Dependencies

| Recovery Scenario | Dependency | Available Without Redis? |
|---|---|---|
| Session list recovery | SQLite `sessions` table | YES |
| Log replay | SQLite `logs` table | YES |
| Queue state recovery | Redis `nx:queue` LIST | NO — in-process queue lost on crash |
| HITL state recovery | Redis `nx:hitl:<sid>` HASH | NO — in-process state lost on crash |
| SSE event replay (cross-worker) | Redis `nx:replay:<sid>` LIST | NO — must use SQLite logs fallback |
| Running session detection | `status='running'` in SQLite OR Redis | YES (SQLite sufficient) |

---

## 5 — Session Resurrection Paths

### Path A: Normal restart (same sessions.db)
1. Worker starts → `queue_worker()` launches
2. Stale `running` sessions detected by `reset_stuck_running()` in agent memory
3. Sessions with `status='queued'` are NOT automatically re-queued (manual action required)
4. Completed/failed sessions available immediately via `/api/sessions`

### Path B: Redis-assisted restart
1. Worker starts → `_nx_redis = get_nx_redis()` → connects to Redis
2. `nx:queue` LIST may contain session IDs from pre-crash queue
3. `queue_worker()` → `BRPOP nx:queue` → picks up surviving tasks
4. `nx:running:<wid>` keys from dead workers expire automatically (600s TTL)

### Path C: Full cold start (new DB)
1. No sessions.db → fresh start → no recovery needed

---

## 6 — Execution Durability Score

| Category | Score | Notes |
|---|---|---|
| SQLite write durability | 9/10 | WAL mode; all transitions DB-first |
| Queue durability (in-process) | 4/10 | Volatile; lost on crash |
| Queue durability (Redis) | 8/10 | Durable; in-flight task still lost |
| HITL durability (in-process) | 3/10 | Volatile; lost on crash |
| HITL durability (Redis) | 7/10 | Durable; recovered on reconnect |
| Log durability | 10/10 | SQLite WAL; permanent |
| SSE replay durability | 6/10 | Redis 1h TTL; SQLite infinite |
| **Overall** | **7/10** | Acceptable for beta; Redis improves to 8/10 |

---

## 7 — Recommendations

| Priority | Action |
|---|---|
| HIGH | Set `REDIS_URL` in production to elevate queue durability from 4/10 to 8/10 |
| HIGH | Add `reset_stuck_running()` call on startup to clean stale `status='running'` sessions |
| MEDIUM | Add `status='queued'` auto-requeue on startup (read SQLite, push to Redis queue) |
| MEDIUM | Add `PRAGMA wal_checkpoint(TRUNCATE)` periodic call to prevent unbounded WAL growth |
| LOW | Consider PostgreSQL migration for multi-host deployments (SQLite is per-VPS) |
