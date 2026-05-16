# DELETE ACCOUNT IMPLEMENTATION READINESS
# Phase Z5 — Phase 7 | Generated: 2026-05-15
# Status: AUDIT ONLY — NO IMPLEMENTATION

## EXISTING GOVERNANCE DOCUMENT
Reference: `ACCOUNT_DELETION_GOVERNANCE_PLAN.md` (Phase Z2)

---

## VALIDATION ASSESSMENT

The existing governance plan (Phase Z2) is architecturally sound. This document
validates its completeness and identifies gaps for implementation readiness.

---

## SECTION 1 — SESSION DELETION

### Existing Plan Coverage: ✅ ADEQUATE
The Phase Z2 plan specifies:
- JWT revocation via `_oauth_states` mapping purge
- Session row deletion from `auth_sessions` and `sessions` tables
- SSE stream termination via `ACCOUNT_DELETED` cluster event

### Gap Analysis
| Gap | Severity | Notes |
|-----|---------|-------|
| `_hitl_state` cleanup | HIGH | `runtime/state.py` L27: `_hitl_state: dict[str, dict]`. Must purge any `{sid: ...}` entries for the deleted user's sessions |
| `_STEP_STORE` cleanup | MEDIUM | `runtime/state.py` L29: step store may hold session-linked data |
| `workflow_queues` cleanup | MEDIUM | `runtime/state.py` L31: user-linked workflow queues must be cleared |
| `_P7_PIPELINES` cleanup | LOW | `runtime/state.py` L34: pipeline state; session-scoped |
| `ext_counts` cleanup | LOW | Session-scoped counters |

**VERDICT**: The Z2 plan covers database-layer cleanup well but **does not explicitly address**
the in-memory runtime state objects in `runtime/state.py`. A real implementation MUST include
a `runtime_state_purge(user_id)` function.

---

## SECTION 2 — MEMORY CLEANUP

### Existing Plan Coverage: ✅ ADEQUATE (database layer)
The Z2 plan covers:
- SQLite batch DELETE with LIMIT 1000 anti-bloat strategy
- Workspace directory `shutil.rmtree` via background worker

### Gap Analysis
| Gap | Severity | Notes |
|-----|---------|-------|
| `long_term_memory.py` vector entries | HIGH | If vector_store.py is enabled (chromadb), user embeddings must be purged |
| `memory.json` snapshot | MEDIUM | Flat-file memory snapshot may persist after DB deletion |
| `checkpoint.json` | LOW | Agent checkpoint state; may reference deleted user's session |

---

## SECTION 3 — WAL IMPLICATIONS

### Risk: SQLite WAL State During Deletion
**Scenario**: If a DELETE batch runs while the WAL file has uncommitted writes from
active sessions, the WAL checkpoint may be delayed.

**Current Mitigation** (Z2 plan): Batch `DELETE ... LIMIT 1000` strategy prevents
write starvation. The `_db_lock` in `runtime/state.py` serializes writes.

**Additional Requirement for Implementation**:
```python
# After all batch deletes complete, force WAL checkpoint:
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
```
This ensures the WAL file does not grow unboundedly post-deletion.

**VERDICT**: Z2 plan is WAL-aware but does not include the explicit `PRAGMA wal_checkpoint`
call. Must be added in implementation.

---

## SECTION 4 — REPLAY CLEANUP

### Risk: SSE Replay Buffer
`execution/replay.py` maintains a replay buffer of recent SSE events.
If a user's session events are in the replay buffer at deletion time:

**Required Action**:
```python
# Purge replay buffer for all session IDs belonging to user
replay.purge_sessions(user_session_ids)
```

### Risk: Redis SSE Pub/Sub Channels
`streaming/sse_redis.py` uses per-session Redis channels.
If user's sessions have active Redis channels at deletion:

**Required Action**:
```python
# Unsubscribe and purge all session channels
RedisSSEBridge.purge_user_channels(user_session_ids)
```

**VERDICT**: Z2 plan does not explicitly address replay buffer or Redis channel purge.
These are **critical gaps** for a complete implementation.

---

## SECTION 5 — AUDIT TRAIL HANDLING

### Existing Plan Coverage: ✅ ADEQUATE
The Z2 plan correctly specifies:
- Financial/usage telemetry is NOT deleted (legal/financial retention)
- PII strings in telemetry are scrubbed (anonymization approach)

### Gap Analysis
| Gap | Severity | Notes |
|-----|---------|-------|
| Audit log anonymization scope | MEDIUM | Which specific columns to scrub must be enumerated |
| Retention period | LOW | GDPR-style plan needs explicit retention duration (e.g., 7 years for financial) |
| Admin audit trail | LOW | Admin action logs referencing deleted user should be preserved but anonymized |

---

## SECTION 6 — GDPR-STYLE LIFECYCLE IMPLICATIONS

### Right to Erasure Compliance Checklist

| Requirement | Z2 Plan Status | Gap |
|------------|---------------|-----|
| Hard delete of PII from session tables | ✅ Covered | None |
| Hard delete of user profile | ✅ Covered | None |
| Anonymization of telemetry | ✅ Covered | Specific columns not enumerated |
| Anonymization of billing records | ⚠️ Partial | Financial amounts retained; user name/email must be scrubbed |
| Workspace file deletion | ✅ Covered | Via `shutil.rmtree` |
| Backup/snapshot handling | ❌ MISSING | Snapshots in `/snapshots/` dir not addressed |
| Email suppression | ❌ MISSING | Deleted user's email must be added to suppression list |
| Data export before deletion | ❌ MISSING | GDPR requires offering data portability before erasure |

---

## SECTION 7 — ASYNC CLEANUP ORCHESTRATION

### Existing Plan Coverage: ✅ ADEQUATE (conceptually)
The Z2 plan proposes a 4-stage soft-delete → background cleanup flow.

### Required Implementation Pattern

```python
# Pseudocode — NOT implemented yet
def delete_account_async(user_id: str):
    # Stage 1: Synchronous pre-flight (blocking, <100ms)
    revoke_all_tokens(user_id)
    broadcast_account_deleted_event(user_id)
    flag_account_deleting(user_id)

    # Stage 2: Background task (non-blocking)
    def _background_cleanup():
        terminate_running_tasks(user_id)      # SIGTERM + queue purge
        purge_runtime_state(user_id)          # runtime/state.py in-memory cleanup
        purge_replay_buffer(user_session_ids) # execution/replay.py
        purge_redis_channels(user_session_ids)# streaming/sse_redis.py
        batch_delete_db_rows(user_id)         # SQLite WAL-safe batch
        anonymize_telemetry(user_id)          # infra/telemetry.py
        delete_workspace_files(user_id)       # shutil.rmtree
        checkpoint_wal()                      # PRAGMA wal_checkpoint(TRUNCATE)
        mark_account_deleted(user_id)         # Final status flag

    threading.Thread(target=_background_cleanup, daemon=False).start()
```

### Idempotency Requirement
Background cleanup must use `idempotency.py` to ensure crash-safe restartability.
If the worker crashes mid-cleanup, the next startup must resume from the last
completed stage. This requires a `deletion_stage` column in the user table.

---

## READINESS VERDICT

| Aspect | Status | Notes |
|--------|--------|-------|
| Overall plan quality | ✅ SOUND | Good architectural foundation |
| Runtime state cleanup | ❌ GAP | `_hitl_state`, `_STEP_STORE`, `workflow_queues` not covered |
| WAL checkpoint | ❌ GAP | Missing explicit `PRAGMA wal_checkpoint(TRUNCATE)` |
| Replay buffer purge | ❌ GAP | Not addressed in Z2 plan |
| Redis channel purge | ❌ GAP | Not addressed in Z2 plan |
| Snapshot handling | ❌ GAP | `/snapshots/` directory not addressed |
| Data export (GDPR) | ❌ GAP | Not addressed |
| Email suppression | ❌ GAP | Not addressed |
| Idempotent cleanup | ⚠️ PARTIAL | Mentioned but not designed in detail |
| Billing data retention | ✅ ADEQUATE | Correct approach: anonymize not delete |

**IMPLEMENTATION PREREQUISITE**: Before implementation, the gaps above must be
addressed in an updated governance document. Implementation is NOT approved for Phase Z5.

**Estimated implementation complexity**: Medium-High (5–8 days of careful engineering)
**Recommended phase**: Z6 or standalone governance sprint
