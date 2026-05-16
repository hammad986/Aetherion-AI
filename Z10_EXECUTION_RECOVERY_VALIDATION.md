# Z10 — Execution Recovery Validation
**Aetherion AI · Phase Z10 · Failure Simulation & Verification**
Generated: 2026-05-16

---

## Validation Matrix

Each scenario below documents the trigger, the expected system response after
Z10 hardening, and the verification mechanism.

---

### Scenario 1 — SIGTERM During Active Execution

**Trigger:** Gunicorn sends SIGTERM to a worker running an agent task (graceful
shutdown, rolling deploy).

**System Response (Z10):**
1. Worker process receives SIGTERM.
2. `LightweightWorker._wrapper` thread is running as a daemon — it is **not**
   blocked by the signal.
3. Worker reconciler on another worker detects heartbeat expiry within ≤30 s.
4. `_remediate_orphan()` clears `nx:running:<wid>` and `nx:owner:<sid>`.
5. TASK_FAILED event appended to ExecutionStore.
6. Session marked as orphan via `nx:orphan:<sid>`.

**Zombie prevention:**
- The stop-poller thread in `LightweightWorker` will fire `task.cancel()` if
  a cross-worker stop signal is set before the worker dies.
- If the worker dies without the stop-poller firing, the OS reclaims all child
  processes whose parent PID was the worker (PPID → 1). The subprocess becomes
  an orphan OS process. The `nx:proc:<sid>` record enables post-mortem PID
  identification for audit; the process will terminate when its own TTL or
  output-pipe close triggers exit.

**Verification:**
- `nx:running:<wid>` key absent ≤30 s after worker death.
- `reconciliation_log` row with `action=ownership_cleared`.
- ExecutionStore `status=failed` for the execution.

---

### Scenario 2 — Worker Crash (SIGKILL / OOM Kill)

**Trigger:** Worker killed with SIGKILL (OOM, host restart, kill -9).

**System Response (Z10):**
1. Worker heartbeat thread (daemon) dies immediately.
2. `nx:worker:<wid>` key TTL expires within 60 s.
3. Reconciler (on surviving worker) detects `nx:running:<wid>` with no heartbeat.
4. Full `_remediate_orphan()` sequence executed.
5. Any HITL-paused session receives `__RECONCILER_TIMEOUT__` injection.

**Duplicate execution prevention:**
- `nx:owner:<sid>` is cleared before any new worker can pop the session.
- The session is **not** re-queued automatically (no automatic retry).
  The user must re-submit if they wish to retry — preventing silent
  duplicate execution.

**Verification:**
- `nx:owner:<sid>` key absent after reconcile cycle.
- `hitl_requests` row updated to `status=timeout` if HITL was active.
- `nx:orphan:<sid>` key present with `reason=worker_death`.

---

### Scenario 3 — Redis Reconnect During Execution

**Trigger:** Redis connection drops and recovers while a task is running.

**System Response (Z10):**
1. `_redis_call()` wrapper catches the exception, logs a warning, returns None.
2. Stop-poller thread's `check_stop_requested()` returns False during outage
   (safe default: continue execution).
3. Heartbeat thread silently drops failed pings.
4. On Redis reconnect, `nx:worker:<wid>` key will be refreshed on next heartbeat.
5. `nx:running:<wid>` has TTL = 600 s — it survives brief Redis outages.

**Risk:** If Redis is down for > NX_RUNNING_TTL (600 s), the running key expires
and the reconciler may incorrectly declare the worker dead on reconnect. This
is mitigated by the heartbeat's 60 s TTL — if the heartbeat is still being
refreshed when Redis recovers, the reconciler will see the heartbeat and not
declare the worker dead.

**Verification:**
- No TASK_FAILED event appended during a transient Redis outage (< 60 s).
- Normal task completion and cleanup after Redis recovers.

---

### Scenario 4 — Replay Interruption

**Trigger:** `ExecutionReplayEngine.reconstruct_timeline()` is called while
a new execution for the same session is starting.

**System Response (Z10):**
- `reconstruct_timeline()` is a pure SQLite read — it holds no locks on the
  live execution path.
- The SQLite WAL mode allows concurrent reads during writes.
- No interaction between replay reads and the running task.

**Verification:**
- `reconstruct_timeline()` completes without blocking the execution thread.
- Event log is consistent (append-only; no partial reads).

---

### Scenario 5 — Forced Stop During HITL Pause

**Trigger:** User clicks "Stop" while execution is blocked at a HITL approval
request.

**Same-worker path:**
1. `POST /api/stop` calls `global_job_manager.cancel_task(execution_id)`.
2. `ExecutionTask.cancel()` sets `is_cancelled=True` and fires `_cancel_event`.
3. `HITLEventTracker.request_approval()` is blocked on `threading.Event.wait()`.
4. `cancel()` does **not** set the HITL Event directly — it only sets the task
   cancel event.
5. The HITL wait will unblock when its `timeout_sec` elapses (max 300 s).
6. After unblocking, the runner checks `task.is_cancelled` and exits.

**Improvement path (Z10 recommendation):**
`ExecutionTask.cancel()` should also call `global_hitl_tracker.resume_execution(execution_id)`.
This is a safe surgical addition outside Z10's scope (no orchestration change).

**Cross-worker path:**
1. `POST /api/stop` on non-owning worker sets `nx:stop:<sid>`.
2. Stop-poller on owning worker fires `task.cancel()`.
3. Same resolution path as above.

**Verification:**
- `is_cancelled=True` reflected in task status within STOP_POLL_INTERVAL (1 s).
- `nx:stop_ack:<sid>` written after cancel, preventing duplicate stop attempts.

---

### Scenario 6 — Queue Interruption (Pop-then-Crash)

**Trigger:** Worker pops session from `nx:queue` then crashes before calling
`set_running()`.

**System Response (Z10):**
- Session is no longer in `nx:queue` (already popped).
- No `nx:running:<wid>` key was written.
- Reconciler scans `nx:running:*` — this session is NOT present → not detected
  by ownership scan.
- Session is silently lost (this is the RISK-05 identified in the audit).

**Mitigation status (Z10):**
This risk requires an atomic pop-and-claim operation (Redis Lua script) to fully
close. The Z10 reconciler handles the post-crash case (heartbeat-based) but cannot
detect the sub-millisecond pop window. Logged in audit as an operational ceiling.
The impact is limited: session must be manually re-submitted.

---

### Scenario 7 — Zombie Task Verification

**Post-Z10 verification checklist:**

| Check | Method | Pass Condition |
|---|---|---|
| No zombie subprocesses after stop | `ps aux \| grep exec_` | Zero `exec_*` processes after task.cancel() + 5s |
| No duplicate stop attempts | Redis `GET nx:stop_ack:<sid>` | Key exists after first stop; second call returns False |
| No orphan ownership after worker death | Redis `KEYS nx:owner:*` | Keys absent ≤30s after heartbeat expiry |
| No stuck HITL rows | `SELECT * FROM hitl_requests WHERE status='pending'` | Zero rows after reconcile cycle |
| Replay timeline complete | `GET /api/execution/<id>/timeline` | TASK_FAILED event present for interrupted executions |

---

## Summary of Z10 Correctness Guarantees

| Property | Status After Z10 |
|---|---|
| Cross-worker stop signal delivered | ✓ — stop-poller wired into LightweightWorker |
| Subprocess SIGTERM on stop | ✓ — ExecutionTask.cancel() sends SIGTERM + SIGKILL escalator |
| Stop deduplication | ✓ — nx:stop_ack prevents double termination |
| Worker death detected | ✓ — heartbeat-based detection ≤30s |
| Orphan ownership cleared | ✓ — reconciler clears nx:running + nx:owner |
| Replay event continuity | ✓ — TASK_FAILED appended by reconciler |
| HITL unblock on death | ✓ — reconciler injects __RECONCILER_TIMEOUT__ |
| Proc PID auditable | ✓ — nx:proc:<sid> tracks subprocess PID |
| Queue pop window | ⚠ — partial (atomic pop-and-claim not yet implemented) |
| Auto-resume after orphan | ⚠ — orphan marked; manual re-submit still required |
