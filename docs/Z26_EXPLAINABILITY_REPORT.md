# Z26 — Explainability System Report

## Purpose

Operators must be able to understand WHY the system made major execution decisions without needing to inspect raw LLM outputs or internal scoring data.

The explainability system records high-level, human-readable decision records for every significant runtime event.

## What is Explained

| Decision Type      | Example                                                  |
|--------------------|----------------------------------------------------------|
| `model_selection`  | "Selected gpt-4o: lowest latency for this plan mode"     |
| `retry`            | "Retry attempt 2: tool returned malformed JSON"          |
| `escalation`       | "Escalated to operator: confidence below threshold"      |
| `replanning`       | "Replanning triggered: target file not found"            |
| `execution_pause`  | "Execution paused: destructive command needs approval"   |
| `tool_rejection`   | "Tool 'shell_exec' rejected: command matched deny-list"  |
| `provider_switch`  | "Switched openai→anthropic: rate limit exceeded"         |
| `context_compression` | "Compressed 20 messages into episode 3"             |

## What is NOT Exposed

- Raw model chain-of-thought
- Internal probability distributions
- Intermediate scoring details
- Confidence penalty breakdowns (those are in `ConfidenceReport`, not here)
- Model weights or hyperparameters

## Record Structure

Each `DecisionRecord` contains:
```
record_id           — unique identifier
decision_type       — one of the types above
summary             — one-sentence human-readable explanation
reason_category     — "performance" | "safety" | "budget" | "error" | "policy"
contributing_factors — list of 1–5 observable facts
outcome             — what actually happened as a result
confidence          — score at time of decision (0–1)
ts                  — unix timestamp
```

## Query API

```python
get_decisions(sid=None, decision_type=None, limit=50)
get_session_explanation_summary(sid)
explainability_telemetry()
```

## Integration Points

Callers should record decisions at these integration points:

- **Router** → `explain_model_selection()` on every LLM dispatch
- **Agent** → `explain_retry()` on each retry cycle
- **Governance layer** → `explain_tool_rejection()` on deny-list hit
- **Confidence engine** → `explain_escalation()` on HITL trigger
- **Execution planner** → `explain_replanning()` on plan revision
- **Context compression** → `explain_context_compression()` on episode creation

## Retention

The in-memory registry holds up to 2,000 decision records (FIFO eviction). For persistent audit trails, records should be flushed to SQLite periodically.

## FUTURE_RUNTIME markers

- `FUTURE_RUNTIME_CAUSAL_GRAPH`: structured causal graph for multi-step decision chains — deferred to v2
- `FUTURE_RUNTIME_OPERATOR_DASHBOARD`: dedicated UI panel for decision timeline visualization — deferred to v2
