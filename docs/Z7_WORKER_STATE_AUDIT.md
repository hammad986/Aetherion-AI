# Z7 Worker State Audit
**Phase Z7 — Phase 1: Multi-Worker Runtime Stabilization**
**Date:** 2026-05-16 | **Status:** COMPLETE

---

## Executive Summary

All in-process runtime state has been audited, categorised, and mapped to its
Redis-backed replacement.  Every structure that must survive a worker death or
be visible across workers has been wrapped by `NexoraRedisLayer`
(`redis_layer.py`) with a transparent in-process fallback when `REDIS_URL` is
unset.

---

## 1 — All In-Memory Runtime State

### 1.1 `runtime/state.py`

| Variable | Type | Purpose | Worker-local? |
|---|---|---|---|
| `queue_lock` | `threading.Lock` | Serialises local queue ops | YES |
| `pending_queue` | `deque` | Session IDs waiting to run | YES ← **risk** |
| `running` | `dict {sid,proc,seq}` | Current running session | YES ← **risk** |
| `managed_runs` | `deque` | Rate-limit timestamps | YES |
| `_hitl_state` | `dict` | Pause/inject state per session | YES ← **risk** |
| `_hitl_lock` | `threading.Lock` | Serialises HITL dict | YES |
| `_db_lock` | `threading.Lock` | SQLite write serialiser | YES |
| `_BROWSER_LOCK` | `threading.Lock` | Browser session lock | YES |
| `_STEP_STORE` | `dict` | Execution step cache | YES |
| `_P7_PIPELINES` | `dict` | Agent pipeline registry | YES |
| `_oauth_states` | `dict` | OAuth CSRF state tokens | YES |
| `workflow_queues` | `dict` | Long-running workflow queues | YES |
| `ext_counts` | `dict` | Extension usage counters | YES |

### 1.2 `web_app.py` singleton structures

| Variable / pattern | Type | Purpose | Worker-local? |
|---|---|---|---|
| `_get_chain_runner()` | function | Single ChainRunner instance | YES |
| `_scheduler` | `TaskScheduler` | Background cron scheduler | YES |
| `_deletion_requests` | `dict` | Account-delete tokens | YES |
| `_memory` | `AgentMemory` | Short-term memory | YES |
| Rate-limiter counters | `dict` | Per-IP call counts | YES |

---

## 2 — Singleton Assumptions

| Assumption | Location | Risk | Mitigation |
|---|---|---|---|
| One task runs at a time | `running["sid"]` | **HIGH** — two workers could both pick queue items | Redis BRPOP atomicity; only one worker wins |
| Queue is local deque | `pending_queue` | **HIGH** — task invisible to other workers | Redis LIST with LPUSH/BRPOP |
| HITL state is in-process | `_hitl_state` | **HIGH** — pause on worker A not visible to worker B | Redis HASH per session |
| SSE clients local | `SSEManager._clients` | **HIGH** — agent on worker B can't reach client on A | RedisSSEBridge pub/sub |
| OAuth states local | `_oauth_states` | MEDIUM — OAuth flow broken if redirected to different worker | Sticky session or Redis (future) |
| Rate limiter local | counters dict | MEDIUM — per-IP limits enforced per-worker | Acceptable for current load |
| Scheduler runs once | `TaskScheduler` | LOW — each worker schedules independently | Cron deduplication via DB check |
| ChainRunner local | `_chain_runner` | LOW — chains isolated per submission | Acceptable: chains started on submitting worker |

---

## 3 — Thread-Local Assumptions

| Location | Pattern | Risk |
|---|---|---|
| `run_session` `emit` closure | `nonlocal seq` counter | Worker-local only — correct |
| `_auto_run_chain` thread | `threading.Thread` | Worker-local — correct |
| `queue_worker` thread | `daemon=True` | One per worker — correct with Redis pop |
| `SSEManager._start_reaper_once()` | `_reaper_started` flag | Worker-local — correct, each worker needs own reaper |

No thread-local storage (`threading.local`) is used anywhere in the codebase.
All thread-level state is via closures or shared dicts protected by locks.

---

## 4 — Non-Shared Queues (Before Z7)

| Queue | Location | Shared? |
|---|---|---|
| `pending_queue` (deque) | `runtime/state.py` | **NO** — worker-local |
| SSE per-client queue | `SSEClient.queue` | **NO** — in-process Queue objects |
| `workflow_queues` | `runtime/state.py` | **NO** — worker-local |

---

## 5 — Non-Shared Replay Buffers (Before Z7)

| Buffer | Location | Shared? |
|---|---|---|
| `SSEManager._replay_buffers` | `streaming/sse_manager.py` | **NO** — worker-local deque |
| `SSEManager._seq_counters` | `streaming/sse_manager.py` | **NO** — worker-local |

**Mitigation:** `RedisSSEBridge` (`streaming/sse_redis.py`) maintains a Redis
LIST replay buffer (`nx:replay:<sid>`) keyed by session, accessible by all
workers.  In-process replay still functions for the worker that holds the
clients.

---

## 6 — Non-Shared Execution Maps (Before Z7)

| Structure | Location | Shared? |
|---|---|---|
| `running` dict | `runtime/state.py` | **NO** — worker-local |
| `_hitl_state` dict | `runtime/state.py` | **NO** — worker-local |
| `_STEP_STORE` dict | `runtime/state.py` | **NO** — worker-local |

---

## 7 — Hidden Worker-Local Caches

| Cache | Location | Note |
|---|---|---|
| LRU cache on `get_setting()` | `web_app.py` | `@lru_cache` — each worker warms independently |
| `_check_feature_cache` | `web_app.py` | Per-worker feature flag cache (TTL-based) |
| SQLite `cached_property` | `memory.py` | Per-process, invalidated on next write |

---

## 8 — Z7 Resolution Summary

| Risk Item | Z7 Resolution | File |
|---|---|---|
| `pending_queue` | Redis LIST via `_nx_redis.push()` / `pop_blocking()` | `redis_layer.py`, `web_app.py` |
| `running` (sid/seq) | Redis STRING `nx:running:<wid>` + owner map | `redis_layer.py` |
| `running["proc"]` | Local only (subprocess not serialisable) | `web_app.py` — `_nx_redis.set_proc()` |
| `_hitl_state` | Redis HASH `nx:hitl:<sid>` | `redis_layer.py`, `web_app.py` |
| SSE cross-worker | Redis pub/sub via `RedisSSEBridge` | `streaming/sse_redis.py` |
| Stop signals | Redis STRING `nx:stop:<sid>` | `redis_layer.py` |
| Worker visibility | Redis HASH `nx:worker:<wid>` + `/api/workers` | `redis_layer.py`, `web_app.py` |

---

## 9 — Structures Intentionally Left Worker-Local

| Structure | Justification |
|---|---|
| `_oauth_states` | OAuth flows complete on same worker via sticky cookie |
| Rate-limiter counters | Per-worker limits acceptable at current Replit concurrency |
| `_STEP_STORE` | Short-lived step cache; steps written to SQLite immediately |
| `_deletion_requests` | 24h token stored in SQLite; in-memory cache is a perf opt only |
| `TaskScheduler` | Job deduplication relies on DB `status='queued'` check |

---

## Audit Verdict: PASS
All critical shared-state paths have been identified and wrapped by
`NexoraRedisLayer` with graceful fallback.  The system is safe to run with
`--workers N` when `REDIS_URL` is set, and continues to operate identically
with `--workers 1` when `REDIS_URL` is unset.
