# Z28 Context + Memory Pressure Analysis

**Phase:** Z28C — Context + Memory Pressure Visibility  
**Date:** 2026-05-16  
**Status:** OPERATIONAL

---

## Overview

The Z28C context pressure module provides operators real-time visibility into how the agent is managing its active context window. This is critical for long-running sessions where token budget exhaustion triggers automatic compression, potentially altering the agent's available working memory.

---

## Architecture: Context Lifecycle

```
User message / Agent step
       │
       ▼
SessionContext.add_message()
  ├─ Appends to _active[] with token count
  └─ Calls _maybe_compress() if budget_pct > threshold
         │
         ▼
    SessionContext.compress()
      ├─ Rolls oldest N messages into EpisodeSummary
      ├─ Stores episode in _episodes[]
      ├─ Prunes _active[]
      └─ Emits audit event: "episode_compressed"

SessionContext.build_prompt_context()
  └─ Returns: critical_notes + episodes + active_window
```

---

## Token Budget

Default token budget is configured in `runtime/context_compression.py`:

| Parameter | Default | Notes |
|-----------|---------|-------|
| `token_budget` | 32,000 | Configurable per-session via `get_session_context(sid, token_budget=N)` |
| Compression threshold | budget_pct > 100% | Auto-compress when total exceeds budget |
| Episode rollup size | Oldest messages until 25% freed | Heuristic per-compress call |

---

## Token Budget Percentage Calculation

```python
budget_pct = (active_window + episode_summaries + critical_notes) / token_budget * 100
```

This is emitted via SSE as part of the `agent.context_state` event and polled via `GET /api/z28/context-pressure`.

---

## Pressure Zones

| Budget % | Zone | Color | Operator Action |
|----------|------|-------|----------------|
| 0–60% | Normal | Blue | None required |
| 61–80% | Elevated | Amber | Monitor — compression likely soon |
| 81–95% | High | Orange | Compression triggered or imminent |
| 96–100%+ | Critical | Red | Active compression cycle |

---

## SSE Event: `agent.context_state`

Emitted from `agent.py` after each step's explain call:

```json
{
  "token_pct": 67.3,
  "total_tokens": 21536,
  "episodes": 2,
  "session_id": "abc123"
}
```

Routed by `nx-sse-runtime.js` to `NxBus.emit('nx:z28:context', payload)`.

---

## API Contract

```
GET /api/z28/context-pressure?sid={session_id}

Response:
{
  "ok": true,
  "sid": "abc123",
  "budget_pct": 67.3,
  "total_tokens": 21536,
  "episodes": 2,
  "compression_count": 1,
  "critical_notes": 3,
  "audit_tail": [
    {
      "event": "episode_compressed",
      "sid": "abc123",
      "episode_index": 0,
      "messages_compressed": 12,
      "summary_tokens": 340
    },
    ...
  ]
}
```

---

## Audit Log

The compression audit log (`_audit_log` in `runtime/context_compression.py`) records every significant context event:

| Event Type | Trigger |
|------------|---------|
| `episode_compressed` | Auto-compress cycle completed |
| `critical_note_added` | Operator/agent added a retained note |
| `auto_compress_triggered` | Token budget exceeded threshold |
| `session_context_dropped` | Session context freed from memory |

The API returns the last 10 audit entries for the session.

---

## UI Components

- **Budget Bar** — horizontal fill bar 0–100%, color-zoned per pressure level
- **Token Count** — total tokens currently in context
- **Episode Count** — number of compressed episode summaries retained
- **Critical Notes** — count of forced-retained messages
- **Compression Events** — total auto-compress cycles this session
- **Audit Tail** — collapsible last-10 audit events table

---

## Operator Guidance

- **Episodes = 0:** Session is young; all context is in the active window
- **Episodes ≥ 3:** Significant history has been compressed; agent is working from summaries — subtle context drift is possible
- **Budget % > 90%:** Another compression cycle is imminent — check if task is well-bounded
- **Critical Notes > 5:** Many forced-retained messages; consider starting a new session for a new task
