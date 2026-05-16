# Z27 — Runtime Integration Report

## Summary

Phase Z27A wires the four Z26 cognitive runtime modules into the live agent execution loop (`agent.py`).

---

## Integration Points

### 1. Initialization (agent.py: run() start)

At the beginning of each `run()` invocation, the Z26 modules are loaded:

```python
from runtime.context_compression import get_session_context
from runtime.confidence_engine   import score_step, get_tracker
from runtime.explainability      import explain_model_selection, explain_retry, ...
_z26_ctx          = get_session_context(session_id)
_z26_conf_tracker = get_tracker(session_id)
```

Failure is non-fatal — the agent continues without Z26 if import fails.

### 2. Context Compression (after each LLM call)

After every `router.chat()` call:
```python
_z26_ctx.add_message("user",      active_step[:600])
_z26_ctx.add_message("assistant", raw[:600])
```

Token pressure is emitted as an SSE event `agent.context_state` for UI display.

### 3. Model Selection Explanation (after each LLM call)

```python
explain_model_selection(sid, step_id, model=used_model, reason=..., factors=[...])
```

Logged to the explainability registry for operator query via `/api/runtime/decisions`.

### 4. Confidence Scoring + Retry Explanation (on error)

On each tool failure:
```python
_z26_report = score_step(sid, step_id, output_text=err, retry_count=error_count, ...)
_z26_conf_tracker.record(_z26_report)
explain_retry(sid, step_id, attempt=error_count, failure_reason=..., factors=[...])
```

If `_z26_report.requires_hitl`, emits `agent.confidence_warning` SSE event.

### 5. Provider Switch Explanation (on Gemini pro escalation)

```python
explain_provider_switch(sid, step_id, from_provider="gemini-flash", to_provider="gemini-pro", ...)
```

### 6. Replanning Explanation (on adaptive replan)

```python
explain_replanning(sid, step_id, original_plan_summary=..., reason=_rp.reason, ...)
```

### 7. HITL Escalation Explanation (on HITL trigger)

```python
explain_escalation(sid, step_id, trigger=_hitl_reason, confidence=rolling_avg, ...)
```

### 8. Task Completion Cleanup

At task end:
```python
agent.runtime_telemetry  # emitted via SSE
drop_session_context(sid)
drop_tracker(sid)
```

---

## Scheduler Integration (web_app.py)

`runtime.scheduler.start_background_checker(interval_secs=10)` is called at app startup, enabling deadline/timeout enforcement and alert callbacks.

---

## New API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/runtime/telemetry` | GET | Compression + confidence + scheduler snapshot |
| `/api/runtime/decisions` | GET | Explainability decision log (operator-safe) |
| `/api/runtime/context/{sid}` | GET | Context token usage for a session |

---

## SSE Events Added

| Event | Payload | Consumer |
|-------|---------|----------|
| `agent.context_state` | token_pct, total_tokens, episodes | UI context indicator |
| `agent.confidence_warning` | score, level, alert | UI confidence warning |
| `agent.runtime_telemetry` | context_stats, confidence_summary | UI telemetry panel |

---

## Non-Breaking Guarantee

All Z26 integration is wrapped in `try/except`. If any runtime module fails to import or throws, the agent continues executing as before. Zero regressions.
