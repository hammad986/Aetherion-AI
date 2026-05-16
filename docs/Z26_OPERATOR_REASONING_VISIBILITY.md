# Z26 — Operator Reasoning Visibility

## Principle

Operators should be able to answer these questions at any point during execution:

1. What is the agent currently doing, and why?
2. What decision was just made, and what triggered it?
3. How confident is the system about its current trajectory?
4. What would cause the system to escalate to a human?

This document describes what is exposed, what is deliberately hidden, and where to find each piece of information.

## Visibility Matrix

| Question                           | Source                          | Exposed |
|------------------------------------|---------------------------------|---------|
| Current task objective             | `CriticalNote(type="goal")`     | ✅ Yes  |
| Why this model was selected        | `DecisionRecord(model_selection)` | ✅ Yes |
| Why a retry happened               | `DecisionRecord(retry)`         | ✅ Yes  |
| Why execution was paused           | `DecisionRecord(execution_pause)` | ✅ Yes |
| Why a tool was rejected            | `DecisionRecord(tool_rejection)` | ✅ Yes |
| Current confidence score           | `ConfidenceReport.final_score`  | ✅ Yes  |
| What signals affected confidence   | `ConfidenceReport.signals`      | ✅ Yes (type + detail only) |
| Internal LLM chain-of-thought      | (not captured)                  | ❌ No   |
| Raw probability distributions      | (not captured)                  | ❌ No   |
| Model temperature / sampling params | (not exposed)                  | ❌ No   |
| Prior episode verbatim content     | (compressed and discarded)      | ❌ No   |

## Operator API Surface

### Get all decisions for a session
```python
from runtime.explainability import get_session_explanation_summary
summary = get_session_explanation_summary(sid)
```

### Get current confidence state
```python
from runtime.confidence_engine import get_tracker
tracker = get_tracker(sid)
state = tracker.summary()
```

### Get current context token usage
```python
from runtime.context_compression import get_session_context
ctx = get_session_context(sid)
usage = ctx.token_usage()
```

### Get scheduled missions
```python
from runtime.scheduler import list_missions
missions = list_missions(sid=sid)
```

## What Operators Cannot Access

By design:

- **Chain-of-thought traces** — these are never stored or exposed. The system only surfaces observable outcomes.
- **Individual confidence signal penalties** — operators see signal types and descriptions, not the numeric penalty applied.
- **Compressed episode verbatim content** — once compressed, only the summary is available.

## Alert Surface

The system proactively surfaces information to operators via:

1. **HITL escalation** (confidence < 0.35) — execution pauses
2. **Low confidence alert** (0.35–0.49) — warning logged to operator UI
3. **Deadline approaching** (< 20% of deadline remaining) — scheduler alert
4. **Execution timeout** — scheduler marks mission timed out

## FUTURE_RUNTIME markers

- `FUTURE_RUNTIME_LIVE_DASHBOARD`: real-time operator decision feed with audit trail — deferred to v2
- `FUTURE_RUNTIME_EXPLANATION_API`: REST endpoint for `/api/session/{sid}/explain` — planned for v2
