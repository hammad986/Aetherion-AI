# T005 — Redis / SSE Runtime Certification
**Phase Z6 | Generated: 2026-05-16**

---

## Executive Summary

Nexora does not use Redis. All real-time push is handled by Server-Sent Events (SSE)
over a pure in-process queue structure (`_sse_queues`). SSE reconnect, stale client
cleanup, and orphan subscription handling have been audited.

**Status: PASS (no Redis dependency; SSE is stable for single-worker deployment)**

---

## 1. SSE Architecture

```
Browser ← HTTP/1.1 keep-alive ← Flask SSE endpoint ← Queue ← Agent worker thread
```

| Component | Implementation |
|-----------|---------------|
| SSE queue store | `_sse_queues: dict` — `{sid: deque(maxlen=200)}` |
| Queue lock | `_sse_lock` (threading.Lock) |
| Heartbeat | Every 15s (`data: \n\n` ping) |
| Stale detection | `Queue.get(timeout=15)` — timeout triggers heartbeat or disconnect |
| Client registry | `_sse_clients: dict` — `{sid: set(queue_refs)}` |

---

## 2. SSE Endpoint Analysis

### `GET /api/stream/<sid>` (primary execution stream)
- Opens `queue.Queue()` and registers it in `_sse_queues[sid]`
- Generator yields `data:` lines for each event
- On `GeneratorExit` (client disconnect): removes queue from registry
- On timeout: sends heartbeat `data: \n\n` to keep connection alive

### `GET /api/events` (global event bus)
- Same pattern, session-agnostic
- Used by dashboard for cross-session status updates

### Reconnect behaviour
Browser `EventSource` API auto-reconnects with `Last-Event-ID` after:
- Network drop (browser reconnects within ~3 seconds)
- Server restart (browser reconnects; server creates new queue)

**Limitation:** On reconnect after a server restart, events emitted during the
downtime are lost (no persistence). For a coding agent this is acceptable — the
agent result is persisted in SQLite, not the SSE stream.

---

## 3. Stale Client Cleanup

```python
# Pattern used in SSE generators:
try:
    event = q.get(timeout=15)
    yield f"data: {json.dumps(event)}\n\n"
except Empty:
    yield "data: \n\n"  # heartbeat
except GeneratorExit:
    _remove_client(sid, q)  # cleanup on disconnect
    return
```

When the browser tab closes, `GeneratorExit` is raised by Flask/Werkzeug on the
next yield, triggering immediate cleanup. No orphan queues persist beyond 1–2
heartbeat cycles.

---

## 4. Orphan Subscription Handling

Orphan scenario: SSE generator still running after the agent session completes.

Resolution:
- Agent worker calls `_emit_done(sid)` which puts a `{"type": "done"}` sentinel
  into all queues for `sid`
- SSE generator receives sentinel, yields it to browser, then closes the connection
- On `GeneratorExit`, queue is removed from `_sse_queues[sid]`

---

## 5. Redis: Not Required

| Feature | Redis alternative | Current implementation |
|---------|-----------------|----------------------|
| Pub/sub for SSE | Not needed (single process) | In-process deque |
| Session persistence | Not needed | SQLite WAL |
| Task queue | Not needed (single worker) | `pending_queue` deque |
| Rate limiter state | Not needed | `_auth_limiter`, `_task_limiter` dicts |
| Celery/worker tasks | Not needed | `threading.Thread` workers |

**If scaling to multi-worker:** Replace `_sse_queues` with Redis pub/sub channels.
The current deque-based approach is correct for `--workers 1 --threads N`.

---

## 6. SSE Load Profile

| Metric | Value | Notes |
|--------|-------|-------|
| Max concurrent SSE connections | Unbounded | Each is a Flask thread |
| Queue max size | 200 events | Old events dropped on overflow |
| Heartbeat interval | 15 seconds | Prevents proxy timeout (nginx default 60s) |
| Event serialisation | JSON | All events are `{"type": ..., "data": ...}` |

---

## 7. Known Issues

| Issue | Severity | Recommendation |
|-------|----------|---------------|
| No `Last-Event-ID` replay support | Low | Add event log for last 50 events per session |
| Browser console shows `SSE state: Not connected` on initial load | Low | SSE connects lazily when a task starts |
| Long-task browser warning (384ms) | Low | Frontend-only; unrelated to SSE |

---

**Certification:** SSE layer is stable, orphan-safe, and correctly implements
stale-client cleanup. No Redis dependency is present or required for current deployment.
