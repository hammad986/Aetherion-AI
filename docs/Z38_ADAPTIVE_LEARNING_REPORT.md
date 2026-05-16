# Z38 Adaptive Learning Report

**Phase:** Z38B — Adaptive Runtime Learning  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — recovery learning, pattern detection, historical calibration live

---

## Recovery Learning

When `POST /api/z38/recovery` is called (fired on `z32.replan.applied` and `z29.hitl.resolved` events), the recovery outcome is persisted with:
- `recovery_type` — the strategy used (replan, compress_context, hitl, etc.)
- `success` — boolean
- `confidence_before` / `confidence_after` — confidence delta from Z32

On hydration (`GET /api/z38/memory/<node_id>`), the response includes `recovery_by_type` — a dict of strategy → `{count, successes}` — and `best_recovery` — the strategy with the highest success rate.

This is injected into Z37's `ExecutionMemory` via `_applyHydration()`, so the inspector's **Runtime Memory** insight immediately benefits from historical recovery data without waiting for the current session to accumulate its own.

---

## Operational Pattern Detection

`GET /api/z38/patterns` returns four query results:

| Pattern | Query | Signal |
|---------|-------|--------|
| `unstable_nodes` | Nodes with errors or retries, sorted by `errors+retries DESC` | Chronically unstable workflows |
| `bottlenecks` | Nodes sorted by `avg(dur_ms) DESC` | Historically expensive paths |
| `recovery_stats` | Recovery types sorted by `success_rate DESC` | Best recovery strategies globally |
| `retry_by_branch` | Branch types sorted by `sum(retries) DESC` | Retry-heavy execution branches |

These patterns are rendered in the **Evolution Panel** inside the forensic inspector as two sections: **CHRONIC INSTABILITY** (top 3 unstable nodes) and **RECOVERY STRATEGIES** (top 3 by success rate, color-coded green/amber/red).

---

## Adaptive Risk Calibration

Z38 does not override Z37's risk formula with learned coefficients (that would require a training loop). Instead, it calibrates through hydration:

When a node is hydrated from persistence, its historical `total_retries` and `total_errors` are merged into the Z36 NodeRegistry. Z37's `Predictor.getEscalationProbability(nodeId)` now uses the inflated retry/error counts — so a node that has historically been unstable will show elevated risk immediately at session start, before it fails again in the current session.

This is a simple but effective form of historical calibration: **past failure record biases current risk estimate upward**.

---

## Remaining Adaptive Learning Gaps

1. **Recovery type labels are free-form strings** — `z32.replan.applied` emits the action description as a string (e.g., "Compress context and retry"). If the label changes slightly between sessions, two records of the same recovery strategy may accumulate as different types in `recovery_by_type`.

2. **Confidence delta tracking** — `confidence_before` is read from the NodeRegistry at the time of `z32.replan.applied`. For the first recovery of a session, this will be the hydrated historical confidence (partial) rather than the exact pre-recovery confidence.

3. **Pattern detection requires accumulated data** — on a fresh database with no history, `GET /api/z38/patterns` returns empty results. The adaptive system has no bootstrapped priors.

4. **No negative reinforcement for stable nodes** — the pattern query only surfaces unstable nodes. A node that was previously unstable but has stabilized (zero errors/retries in the last 3 sessions) will continue appearing in `unstable_nodes` until manually pruned.
