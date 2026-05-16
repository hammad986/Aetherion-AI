# Z10 — Execution Ownership Audit
**Aetherion AI · Phase Z10 · Cross-Worker Execution Control**
Generated: 2026-05-16

---

## 1. Subprocess Ownership Flows

### 1.1 Primary Execution Path

```
HTTP Request → web_app.py (/api/run or equivalent)
    └─► global_job_manager.submit_task(session_id, payload)
            └─► ExecutionTask created (execution_id, session_id)
            └─► LightweightWorker.execute_async(task, runner_fn)
                    └─► daemon Thread spawned (worker-{execution_id})
                            └─► runner_fn(task)  ←── agent.run() wired here
                                    └─► Agent.run() → tools.py → subprocess
```

**Owner of record:** The OS thread that called `execute_async()`. This is always within the same Gunicorn worker process. The `execution_id` is the canonical ownership token.

**Redis ownership record:**
- `nx:running:<worker_id>` → session_id  (STRING, TTL = NX_RUNNING_TTL)
- `nx:owner:<session_id>` → worker_id   (STRING, TTL = NX_RUNNING_TTL)

**Local-only state (not replicated):**
- `LightweightWorker._active_tasks[execution_id]` → `ExecutionTask` object
- `NexoraRedisLayer._local_running["proc"]` → subprocess handle
- `HITLEventTracker._pauses[execution_id]` → `threading.Event`

---

### 1.2 Queue Ownership Path

```
web_app.py → NexoraRedisLayer.push(session_id)   [nx:queue LIST]
    └─► Any Gunicorn worker calls pop_blocking()
            └─► That worker becomes owner
            └─► set_running(sid, seq) writes ownership to Redis
```

**Gap identified:** Between `pop_blocking()` and `set_running()` there is a window (~microseconds in Python) where no ownership record exists in Redis. If a worker crashes in this window, the session is silently lost with no orphan marker.

---

### 1.3 Subprocess Lifecycle Path

```
agent.run() → tools.py → code_runner.py / terminal_backend.py
    └─► subprocess.Popen(cmd, ...)          ← PID born here
            └─► stored ONLY in local memory  ← NOT in Redis
            └─► proc returned to caller
NexoraRedisLayer.set_proc(proc)             ← local _local_running["proc"]
```

**Critical gap:** The subprocess PID is stored only in the owning worker's memory. No other worker can discover, signal, or verify it. The Redis `nx:stop:<sid>` flag is the only cross-worker signal path.

---

## 2. Worker-Local Process References

| Reference | Location | Redis-replicated? | Notes |
|---|---|---|---|
| `ExecutionTask._cancel_event` | `execution/worker.py:30` | No | threading.Event, in-process only |
| `ExecutionTask.is_cancelled` | `execution/worker.py:29` | No | boolean flag |
| `_local_running["proc"]` | `redis_layer.py:81` | No | subprocess.Popen object |
| `_local_running["sid"]` | `redis_layer.py:81` | Partially | Also in `nx:running:<wid>` |
| `HITLEventTracker._pauses` | `execution/hitl.py:17` | No | Dict[str, threading.Event] |
| `LightweightWorker._active_tasks` | `execution/worker.py:49` | No | Dict[execution_id → ExecutionTask] |

**Verdict:** All process-level state is worker-local. Redis contains only session-level routing metadata. This is architecturally sound for a single-subprocess-per-session model but creates reconciliation blind spots on worker death.

---

## 3. Stop / Kill Lifecycle Paths

### 3.1 Same-Worker Stop (Local)

```
POST /api/stop → web_app handler
    → global_job_manager.cancel_task(execution_id)
        → LightweightWorker.get_task(execution_id)
        → ExecutionTask.cancel()         ← sets is_cancelled=True, cancel_event.set()
    → NexoraRedisLayer.request_stop(sid)
        → _local_running["sid"] == sid   ← same worker? return True
        → caller handles SIGTERM locally  ← BUT there is no actual SIGTERM here
```

**Gap:** `cancel_task()` sets the cooperative cancellation flag but does **not** call `proc.terminate()` or `proc.kill()`. Long-running subprocess tools (shell commands, file writes) will continue until they naturally yield to the cancellation check. The subprocess itself is not signalled.

### 3.2 Cross-Worker Stop (Distributed)

```
POST /api/stop on Worker A (not the owner)
    → NexoraRedisLayer.request_stop(sid)
        → _local_running["sid"] != sid   ← different worker
        → redis.set(nx:stop:<sid>, "1", ex=300)  ← flag written
    [Worker B — owning worker — polls check_stop_requested(sid)]
        → BUT: polling only happens if called from within the execution loop
        → NO automatic polling integration in LightweightWorker or runner_fn
```

**Critical gap:** `check_stop_requested()` is defined but **never called** inside `LightweightWorker._wrapper()` or the default `_real_runner` in `job_manager.py`. The cross-worker stop flag is therefore **inert** — it expires after 5 minutes without ever being acted upon.

### 3.3 HITL Stop During Pause

```
Execution blocked on HITLEventTracker.request_approval()
    → threading.Event.wait(timeout=300)
    → If worker dies: Event is garbage collected
    → HITL state in SQLite retains "pending" status forever
```

**Gap:** No mechanism exists to detect that the HITL wait was interrupted by a worker crash. The `hitl_requests` row remains `pending` indefinitely. The Redis `nx:hitl:<sid>` state is cleared by `hitl_clear()` but this is only called on normal completion or timeout.

---

## 4. Interruption Boundaries

| Boundary | Handled? | Risk Level |
|---|---|---|
| Task cancelled via API (same worker) | Partial — flag set but no proc SIGTERM | Medium |
| Task cancelled via API (cross-worker) | No — Redis flag unpolled | High |
| Worker SIGTERM (graceful shutdown) | No — active tasks not checkpointed | High |
| Worker SIGKILL (crash) | No — no heartbeat-based reconciliation | Critical |
| HITL pause on dead worker | No — threading.Event lost | High |
| Replay interrupted by cancellation | No — replay has no cancellation check | Medium |
| Queue pop → crash window | No — session silently lost | High |

---

## 5. Replay Interruption Flows

### Current State

```
ExecutionReplayEngine.reconstruct_timeline(execution_id)
    → ExecutionStore.get_events(execution_id)   ← SQLite read, always safe
    → No live subprocess involvement             ← replay is read-only
    → No cancellation check during replay
```

**Observation:** Replay itself is safe — it's a pure read from SQLite. The risk is in `RuntimeRecovery.attempt_resume()`, which is an interface stub only (`return True` without actual re-execution). No resume path currently exists.

---

## 6. Identified Risks

### RISK-01: Zombie Execution (SEVERITY: High)
**Trigger:** Worker receives SIGTERM; OS kills it; `_local_running["proc"]` subprocess was still running.
**Effect:** Subprocess continues as orphan (parent PID = 1 on Linux). No stop signal ever sent.
**Root cause:** No SIGTERM handler registered in Gunicorn workers to clean up child processes.

### RISK-02: Stale Running State (SEVERITY: High)
**Trigger:** Worker crashes; `nx:running:<wid>` and `nx:owner:<sid>` remain in Redis until TTL (10 min).
**Effect:** Other workers believe session is still running. No new execution accepted for that session.
**Root cause:** No heartbeat-triggered ownership invalidation.

### RISK-03: Cross-Worker Stop Flag Ignored (SEVERITY: Critical)
**Trigger:** Stop requested on Worker A for session owned by Worker B.
**Effect:** Redis `nx:stop:<sid>` set, but never polled by Worker B. Execution continues until completion.
**Root cause:** `check_stop_requested()` not wired into execution loop.

### RISK-04: Orphan HITL Pause (SEVERITY: High)
**Trigger:** Worker hosting a HITL-paused execution crashes.
**Effect:** `hitl_requests` row stuck as "pending". Session unrecoverable without manual DB intervention.
**Root cause:** `threading.Event` is not Redis-backed; HITL pause state not reconciled on worker death.

### RISK-05: Ownership Pop Window (SEVERITY: Medium)
**Trigger:** Worker pops session from `nx:queue` then crashes before `set_running()`.
**Effect:** Session silently dropped — not in queue, not marked running, no ownership record.
**Root cause:** No atomic `pop-and-claim` operation; two separate operations with crash window.

### RISK-06: No Worker Death Detection (SEVERITY: Critical)
**Trigger:** Worker crash with heartbeat TTL=60s.
**Effect:** Stale ownership persists for up to 60 seconds. No automatic orphan marking or recovery initiation.
**Root cause:** Heartbeat thread exists but no consumer watches for heartbeat expiry.

### RISK-07: Duplicate Stop Attempt (SEVERITY: Low)
**Trigger:** Rapid multiple stop requests for same session.
**Effect:** Multiple Redis writes to `nx:stop:<sid>` (idempotent, but generates noise).
**Root cause:** No stop-in-progress guard or acknowledgement tracking.

---

## 7. Race Conditions

| Scenario | Race Type | Impact |
|---|---|---|
| Two workers pop same session from queue | TOCTOU | Duplicate execution |
| Stop requested while task completing | Flag-after-complete | Stale stop flag pollutes next run |
| Worker re-registers same session on restart | Ownership collision | Old state clobbers new |
| HITL inject while worker is dying | Lost message | Injection silently dropped |

---

## 8. Remediation Map (Phase Z10)

| Risk | Remediation | Phase |
|---|---|---|
| RISK-03 Cross-worker stop ignored | Wire `check_stop_requested()` into `LightweightWorker._wrapper()` | Phase 2 |
| RISK-01 Zombie subprocess | Send `proc.terminate()` in `ExecutionTask.cancel()` and on stop poll | Phase 2 |
| RISK-07 Duplicate stop | Add `nx:stop_ack:<sid>` acknowledgement key | Phase 2 |
| RISK-06 No worker death detection | `WorkerReconciler` background thread | Phase 3 |
| RISK-02 Stale running state | Reconciler clears expired `nx:running:*` keys | Phase 3 |
| RISK-04 Orphan HITL | Reconciler marks HITL sessions recoverable; inject timeout path | Phase 3 |
| RISK-05 Ownership pop window | Add `nx:orphan:<sid>` marker written atomically with pop | Phase 3 |
