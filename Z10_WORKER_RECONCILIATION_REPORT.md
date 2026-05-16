# Z10 — Worker Reconciliation Report
**Aetherion AI · Phase Z10 · Worker Death Reconciliation**
Generated: 2026-05-16

---

## Implementation Overview

The `WorkerReconciler` (`execution/worker_reconciler.py`) provides continuous,
distributed detection and remediation of dead-worker orphan sessions.

---

## Reconciliation Architecture

```
Every RECONCILE_INTERVAL seconds (default: 30s)
    └─► One elected reconciler (via Redis SET NX lock)
            └─► Scan nx:running:* keys
            └─► For each: check if nx:worker:<wid> heartbeat exists
            └─► If heartbeat absent → worker presumed dead
                    └─► Clear nx:running:<wid>       [ownership cleared]
                    └─► Clear nx:owner:<sid>          [ownership cleared]
                    └─► Clear nx:proc:<sid>           [PID record cleared]
                    └─► SET nx:orphan:<sid>           [session marked recoverable]
                    └─► Append TASK_FAILED event     [replay durability]
                    └─► If HITL paused → inject timeout [unblocks stuck thread]
                    └─► Clear nx:stop:<sid>           [stale stop signal removed]
                    └─► Write reconciliation_log row  [audit trail]
```

---

## Distributed Lock Contract

| Property | Value |
|---|---|
| Lock key | `nx:reconcile:lock` |
| Lock TTL | 60 s (= 2 × RECONCILE_INTERVAL) |
| Acquisition | Redis SET NX (atomic) |
| Holder value | `worker_id` of elected reconciler |
| Crash safety | TTL auto-expires lock if reconciler dies mid-cycle |
| Fallback (no Redis) | Each worker reconciles independently (single-worker mode) |

---

## Worker Liveness Contract

| Signal | Meaning |
|---|---|
| `nx:worker:<wid>` key EXISTS | Worker alive (heartbeat within TTL=60s) |
| `nx:worker:<wid>` key ABSENT | Worker dead or unreachable |
| `nx:running:<wid>` key EXISTS | Worker claims to own a session |
| Both exist | Normal: worker running session |
| Running exists, worker absent | **Orphan condition** → reconcile |
| Worker exists, no running | Worker idle (normal between tasks) |

---

## Remediation Steps Per Orphan

### Step 1: Ownership Cleared
- Deletes `nx:running:<wid>` and `nx:owner:<sid>` atomically (Redis pipeline).
- Deletes `nx:proc:<sid>` (subprocess PID record).
- Effect: session is available for a new execution; no worker will believe it is still running.

### Step 2: Orphan Marked
- Writes `nx:orphan:<sid>` with `{reason, ts, worker}` payload.
- TTL = `NX_ORPHAN_TTL` (default 1 hour).
- Effect: recovery system can query `list_orphans()` and attempt resume.

### Step 3: ExecutionStore Event
- Appends a `TASK_FAILED` event with `correlation_id=z10_reconciler`.
- Upserts execution snapshot to `status=failed`.
- Effect: replay timeline is complete; no gap where the execution simply "vanishes".

### Step 4: HITL Timeout Injection
- If `nx:hitl:<sid>.paused == "1"`: injects `__RECONCILER_TIMEOUT__` into the inject queue.
- Updates `hitl_requests` SQLite row to `status=timeout`.
- Effect: execution thread unblocks from `HITLEventTracker.request_approval()` wait; HITL
  state machine advances to timeout branch.

### Step 5: Stop Signal Cleanup
- Calls `clear_stop_signal(sid)` (deletes both `nx:stop:<sid>` and `nx:stop_ack:<sid>`).
- Effect: stale stop signals from the dead worker cannot interfere with a future
  execution of the same session.

---

## Replay Continuity Preservation

| Scenario | Before Z10 | After Z10 |
|---|---|---|
| Worker crashes during tool call | Execution silently absent from store | TASK_FAILED appended with `z10_reconciler` correlation |
| Worker crashes during HITL pause | `hitl_requests` row stuck as `pending` | Row updated to `timeout`; inject_queue unblocks thread |
| Session stuck in "running" after crash | State remains `running` until TTL (10 min) | Cleared within one reconcile cycle (≤30s) |
| Next execution attempt for same session | Rejected (session still "running") | Accepted (ownership cleared by reconciler) |

---

## Operational Ceilings

| Metric | Value | Notes |
|---|---|---|
| Detection latency (worker death → orphan cleared) | ≤ 30 s | One full reconcile cycle |
| Detection latency if reconciler also crashes | ≤ 60 s | Next worker acquires lock after lock TTL |
| Max orphans processed per cycle | Unbounded | All dead workers scanned in one sweep |
| SQLite audit retention | Unlimited (manual purge needed) | `reconciliation_log` table in `sessions.db` |

---

## Database Schema Addition

```sql
CREATE TABLE IF NOT EXISTS reconciliation_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at      REAL    NOT NULL,
    dead_worker TEXT    NOT NULL,
    session_id  TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    detail      TEXT
);
```

Actions recorded: `ownership_cleared`, `orphan_marked`, `store_event_appended`,
`hitl_timeout_injected`.

---

## Startup Integration

`start_worker_reconciler()` is called from `web_app.py` after Flask app
initialisation. The call is idempotent (guarded by a module-level lock), so
it is safe in multi-threaded pre-fork Gunicorn environments.

The reconciler thread is a **daemon thread** — it will not prevent process
shutdown, and it does not hold any application state that needs flushing.
