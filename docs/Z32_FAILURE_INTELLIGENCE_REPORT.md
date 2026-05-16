# Z32 Failure Intelligence Report

**Phase:** Z32E — Semantic Failure Intelligence  
**Status:** CERTIFIED  
**Date:** 2026-05-16

---

## Failure Cluster Taxonomy

| Cluster | Trigger Condition | Severity | Description |
|---------|------------------|----------|-------------|
| `retry_storm` | retry_count ≥ 5 | WARNING | Repeated retry attempts — execution stalling |
| `hallucinated_success` | error_after_done > 0 | CRITICAL | Node reached "done" then errored — false completion |
| `tool_instability` | tool_error_rate > 35% | WARNING | Tool calls failing at high rate |
| `context_pressure` | token_count > 6000 OR context_pressure > 0.7 | WARNING | Context approaching compression threshold |
| `dependency_deadlock` | blocked_count > 1 | CRITICAL | Two or more nodes mutually blocking |
| `provider_degradation` | provider_failures > 2 | CRITICAL | Provider repeatedly failing |

---

## Predictive Warning System

| Rule | Trigger | Severity | Operator Message |
|------|---------|----------|-----------------|
| `retry_escalation` | 2 ≤ retries < 5 | WARNING | Retry count trending toward storm |
| `context_approaching` | 4000 < tokens ≤ 6000 | WARNING | Context approaching compression threshold |
| `confidence_decay` | 0.35 ≤ confidence < 0.55 | DEGRADED | Confidence degrading — recommend validation checkpoint |
| `tool_degrading` | 0.2 < tool_error_rate ≤ 0.35 | WARNING | Tool error rate rising |
| `recovery_saturation` | replan_count ≥ 2 | DEGRADED | Multiple replanning events — recommend human escalation |

---

## Runtime Pressure Metrics

| Metric | Formula | Range |
|--------|---------|-------|
| `context_pressure` | token_count / 8000 | 0–1 |
| `reasoning_degradation` | 1 - confidence_score | 0–1 |
| `recovery_saturation` | replan_count / 4 | 0–1 |
| `semantic_instability` | (retries × 0.1 + errors × 0.15) capped at 1.0 | 0–1 |
| `overall_pressure` | max(above × 0.8–1.0) | 0–1 |

`pressure_level`: NOMINAL (<0.35) / ELEVATED (0.35–0.59) / HIGH (0.60–0.79) / CRITICAL (≥0.80)

---

## Operator Signal Design

The `top_signal` field in `/api/z32/intelligence/<sid>` returns a single high-signal explanation:
- If `pressure_level` is CRITICAL or HIGH: pressure level + cluster type
- Else: first predictive warning message

**This is NOT raw telemetry.** The system filters the most actionable signal to surface to the operator — reducing alert fatigue.

---

## Remaining Instability Ceilings

1. **All cluster detection is threshold-based**: No statistical learning. A gradually degrading session that never crosses a threshold will not trigger any cluster. Mitigation: sliding window anomaly detection.
2. **`hallucinated_success` detection is indirect**: We detect errors that follow a "done" state, but the "done" state itself may be incorrectly set by the node synthesis. Mitigation: require a structured "success validation" event, not just absence of errors.
3. **Predictive rules are static**: Thresholds are hardcoded. Different workloads have different baselines. A session with 10,000 tokens is normal for a large codebase task, but the static 6,000 token threshold would trigger a false warning. Mitigation: adaptive thresholds based on session type.
4. **No cross-session pattern learning**: Failure clusters are computed per-session. Patterns that only manifest across many sessions (e.g., a specific provider failing every Tuesday) are not detected. Mitigation: aggregate failure cluster history across sessions.
5. **No false positive rate measurement**: Predictive warnings are not validated against actual outcomes. We don't know what % of `retry_escalation` warnings actually lead to retry storms. Mitigation: add outcome tracking to `failure_clusters`.

---

## Production-Readiness Verdict

**PRODUCTION-READY as an operational early-warning system.** Suitable for surfacing actionable operator signals. Not suitable as an automated safety gate without outcome validation and adaptive threshold calibration.
