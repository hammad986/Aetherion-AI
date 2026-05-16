# Z37 Runtime Forecasting Report

**Phase:** Z37C — Predictive Failure + Recovery Modeling  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — four-level risk classification, node-level forecast, system-wide prediction

---

## Risk Level Classification

The `Predictor.getRiskLevel()` function computes a normalized risk score from four inputs:

```
score = (pressure × 3.0)
      + (min(1, errors/3)  × 2.0)
      + (min(1, retries/6) × 1.5)
      + (max(0, 1-confidence) × 1.5)   [if confidence known]

normalized = min(1, score / 8)
```

| Risk Level | Normalized Score | Visual |
|-----------|-----------------|--------|
| LOW       | 0.00 – 0.24     | Grey text, no border |
| ELEVATED  | 0.25 – 0.49     | Amber text, amber border |
| HIGH      | 0.50 – 0.74     | Orange text, orange border |
| CRITICAL  | 0.75 – 1.00     | Red text, pulsing animation |

Risk is displayed in the mission bar (risk indicator badge) and in the forecast bar strip.

---

## Node-Level Forecasts (Inspector)

For each inspected node the causal section shows:

| Signal | Computation |
|--------|------------|
| **Branch** | `CausalGraph.getBranchType(nodeId)` — main / retry / recovery / escalation |
| **Escalation Probability** | `(retries/5)×0.4 + (errors/3)×0.4 + heat×0.2` |
| **Recovery Confidence** | `successes / total` from `recoveryHistory`, null if no history |
| **Retry Amplification Risk** | retries≥5→severe, ≥3→elevated, ≥1→low, 0→none |

---

## System-Wide Forecast

`getSystemForecast()` aggregates:
- Mean heat across all known nodes
- Total errors and retries
- Z35 confidence and pressure signals (if available)
- Cascade list from Z37B
- Bottleneck list from Z37B
- Next likely unstable node (highest-heat running/pending node)

The forecast runs every 12 seconds via `setInterval` and on every NxBus event that mutates node state. The forecast bar only becomes visible when risk ≥ ELEVATED or at least one cascade is active — it does not appear during calm LOW-risk execution.

---

## Next Unstable Node Prediction

`predictNextUnstableNode()` returns the running or pending node with the highest current heat score. This is a simple rank, not a probabilistic model. It surfaces the most likely candidate for the operator to watch, without claiming certainty.

The prediction is shown in the forecast bar as: `watch: <nodeId>` — only when risk is ELEVATED or above.

---

## Remaining Predictive Limitations

1. **Static risk formula, no learning.** The weighting coefficients (3.0, 2.0, 1.5, 1.5) are fixed constants calibrated conservatively. They are not updated based on session outcomes. A session with consistently low-confidence executions that always succeed would still show ELEVATED risk.

2. **Escalation probability is a linear combination, not a calibrated probability.** The `%` value displayed may systematically over- or under-estimate true escalation likelihood. It is best read as a relative signal ("node A is 3× more likely to escalate than node B") rather than an absolute probability.

3. **Recovery confidence requires at least one recovery event.** For nodes that have never been recovered, the signal shows `—`. This is honest but leaves a gap in newly-started sessions.

4. **Next unstable node prediction degrades on flat heat maps.** If all nodes have similar heat scores (e.g., all at 0.2), the prediction will name one node arbitrarily. No minimum threshold is applied before showing the `watch:` label.

5. **System forecast does not incorporate Z31 historical session data.** A node that fails consistently across sessions would not show elevated initial risk unless it has already failed in the current session.
