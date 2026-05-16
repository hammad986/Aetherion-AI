# Z26 — Confidence Engine

## Purpose

Lightweight runtime uncertainty estimation. Detects low-confidence states, contradictory outputs, retry instability, and hallucination suspicion markers.

**Exposes ONLY decision confidence summaries to operators — never chain-of-thought or model internals.**

## Scoring Model

Each execution step receives a `ConfidenceReport` derived from observable runtime signals only.

### Score Range

| Range     | Level      | Action                    |
|-----------|------------|---------------------------|
| 0.75–1.00 | HIGH       | Continue normally         |
| 0.50–0.74 | MODERATE   | Log, no intervention      |
| 0.35–0.49 | LOW        | Operator alert issued     |
| 0.00–0.34 | CRITICAL   | HITL escalation triggered |

### Signal Penalties

| Signal Type          | Penalty             | Cap   |
|----------------------|---------------------|-------|
| Retry (per retry)    | −0.10               | −0.40 |
| Tool failure (each)  | −0.08               | −0.30 |
| Low evidence         | −0.20               | —     |
| Hallucination marker | −0.15               | —     |
| Output contradiction | −0.12               | —     |

Penalties are additive. Final score is clamped to [0.0, 1.0].

## Hallucination Suspicion Markers

The engine scans output text for phrases that suggest the model is uncertain about its own outputs:

```
"as an ai", "i cannot actually", "i may be wrong",
"i'm not sure", "i cannot confirm", "this is speculative",
"i cannot guarantee", "i might be hallucinating", ...
```

This is **not** semantic analysis. It is simple substring matching on observable model output — no hidden reasoning is accessed.

## Contradiction Detection

The engine compares the current output against the last 5 outputs in the session, checking for opposing word pairs:

```
(success ↔ failed), (completed ↔ error), (created ↔ deleted), ...
```

## HITL Escalation

When `final_score < 0.35`, `requires_hitl = True` is set on the report. It is the caller's responsibility to pause execution and surface the escalation to the operator. The confidence engine does not directly pause execution — it only scores and flags.

## Session-Level Tracking

`SessionConfidenceTracker` maintains a rolling window of recent scores. If the rolling average falls below thresholds, `is_sustained_low()` and `is_sustained_critical()` return `True`.

## Telemetry

`confidence_telemetry_snapshot()` returns:
- Total steps scored
- HITL escalation count
- % low-confidence steps
- Session average score

## FUTURE_RUNTIME markers

- `FUTURE_RUNTIME_CALIBRATION`: a learned calibration layer may be injected as a scoring callable — never couple to model weights directly
- `FUTURE_RUNTIME_EVIDENCE_GRAPH`: rich evidence tracking with source attribution deferred to v2
