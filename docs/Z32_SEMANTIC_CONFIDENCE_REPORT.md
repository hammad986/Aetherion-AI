# Z32 Semantic Confidence Report

**Phase:** Z32B — Semantic Confidence Engine  
**Status:** CERTIFIED  
**Date:** 2026-05-16

---

## Confidence Formula

```
confidence_score =
    0.35 × validation_rate           (fraction of nodes that completed without error)
  + 0.20 × (1 - retry_penalty)       (inverse of retry frequency, capped at 1.0)
  + 0.20 × tool_stability            (fraction of tool calls that succeeded)
  + 0.15 × dep_health                (fraction of nodes not blocked)
  + 0.10 × hist_baseline             (average confidence from last 10 sessions)
  − contradiction_penalty            (errors found in "done" state nodes)
```

Bounded to [0.0, 1.0].

---

## Confidence Sources

| Source | Weight | Notes |
|--------|--------|-------|
| `validation_rate` | 35% | Primary signal — actual node completion rate |
| `(1 - retry_penalty)` | 20% | High retries → low confidence |
| `tool_stability` | 20% | Tool reliability directly affects execution trust |
| `dep_health` | 15% | Blocked dependencies indicate structural risk |
| `hist_baseline` | 10% | Historical mean confidence as a prior |
| `contradiction_penalty` | -0–30% | Hallucinated success detection |

---

## Confidence Levels

| Score | Level | Action |
|-------|-------|--------|
| ≥ 0.75 | HIGH | Normal execution |
| 0.45–0.74 | MEDIUM | Monitor closely, log to pressure bar |
| < 0.45 | LOW | HITL escalation required, DAG replan evaluation triggered |

---

## Drift Tracking

- Every confidence computation is stored in `confidence_snapshots` with `drift = current - previous`.
- Positive drift → confidence improving.
- Negative drift → confidence decaying.
- Drift is visualized as ↑/↓ arrows in the Z32 confidence overlay on the DAG surface.

---

## Adaptive HITL Escalation

- `score < 0.45` → `escalation_required: true` in API response.
- A `low_confidence` failure cluster is written to `failure_clusters` with `severity=CRITICAL`.
- Frontend fires the Z32 prediction banner with escalation message.
- DAG surface flashes red border (CSS escalation animation).

---

## Semantic Hallucination Vectors

1. **`contradiction_penalty` underestimation**: The penalty is capped at 0.30. If many nodes reach "done" then immediately error, the penalty may not fully reflect the severity.
2. **`hist_baseline` cold start**: First session has no history. Default `hist_baseline = 0.75` may overestimate confidence on a brand-new deployment.
3. **Tool stability metric**: Currently taken from `metrics["tool_ok"] / metrics["tool_calls"]`. If a tool call succeeds superficially (returns 200 but wrong output), it is counted as stable. Mitigation: add semantic validation of tool output.
4. **Confidence inertia**: `hist_baseline` is a trailing average. After a string of bad sessions, a good session will be penalized by the bad historical mean.

---

## Production-Readiness Verdict

**PRODUCTION-READY for operational confidence monitoring.** Not suitable as a standalone safety gate — confidence should be one of several signals, not the sole escalation criterion.
