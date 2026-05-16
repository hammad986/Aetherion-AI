# ACCOUNT DELETION GOVERNANCE PLAN
# Phase Z2 Design Document

## 1. Objective
Design a safe, multi-stage account deletion architecture for Nexora that guarantees complete forensic sanitization without breaking concurrent cluster jobs or introducing orphan references in the SSE streams.

## 2. Forensic Sanitization Stages

### Stage 1: Pre-Flight & Suspension (Soft Delete)
- **Token Revocation:** All active JWTs and `_oauth_states` mapping to the `user_id` are immediately revoked.
- **SSE Stream Termination:** A specialized cluster event (`ACCOUNT_DELETED`) is broadcast. The `RedisSSEBridge` intercepts this and forcibly closes all EventSource streams for that tenant.
- **Status Flagging:** The user's account row is flagged as `DELETING`. No new sessions, tasks, or API requests are permitted.

### Stage 2: Background Task Termination
- **Task Orchestrator Intercept:** The `task_orchestrator` scans `pending_queue` and `running` structures in `runtime/state.py` across all workers.
- **Graceful Kill:** Any `running` subprocess belonging to the tenant is sent a `SIGTERM`.
- **Queue Purge:** Jobs in the `pending_queue` are discarded safely using `queue_lock`.

### Stage 3: Artifact & Persistence Purge (Delayed Purge)
- **Database Sanitization (SQLite WAL):** A background batch job iteratively deletes rows from `auth_sessions`, `sessions`, `logs`, and `decisions` using `DELETE FROM ... WHERE user_id = ? LIMIT 1000` to prevent WAL bloat and write starvation.
- **Telemetry Anonymization:** Historic usage telemetry is not deleted (to preserve financial metrics) but all strings linking to `user_id` are scrubbed.
- **Artifact Deletion:** The user's workspace directory (`WORKSPACE_DIR/tenant_id`) is moved to an `archive_purge/` folder. A dedicated worker thread executes `shutil.rmtree` at a low IO priority.

### Stage 4: Rollback & Safety (Orphan Prevention)
- **Rollback Safety:** Between Stage 1 and Stage 3, the account remains recoverable by an Admin.
- **Orphan Prevention:** The deletion job maintains idempotency constraints. If a worker crashes during Stage 3, the job restarts exactly where it left off on the next deployment cycle.

*Note: No code implementation performed during Phase Z2. This is the finalized architectural design.*
