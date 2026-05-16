# Z29 Override Safety Report

**Phase:** Z29C — Operator Override Engine  
**Date:** 2026-05-16  
**Status:** OPERATIONAL

---

## Overview

The Z29C Override Engine (`runtime/override_engine.py`) provides live runtime override controls that allow operators to tune session execution parameters without restarting or corrupting state. All overrides are validated, audited, and applied cooperatively between agent execution steps.

---

## Override Validation Matrix

| Key | Type | Min | Max | Validation |
|-----|------|-----|-----|-----------|
| `provider` | str | — | — | Non-empty, max 64 chars, stripped |
| `model` | str | — | — | Non-empty, max 64 chars, stripped |
| `retry_budget` | int | 1 | 20 | Coerced to int, range check |
| `confidence_threshold` | float | 0.0 | 1.0 | Coerced to float, range check |
| `execution_timeout` | float | 5.0 | 600.0 | Coerced to float, range check |
| `compression_aggressiveness` | float | 0.0 | 1.0 | Coerced to float, range check |

Invalid overrides are rejected with a descriptive error message — they never enter the active override dict.

---

## Replay Integrity Guarantee

Override values are **read cooperatively** by agent.py between steps via `get_override(sid, key, default)`. They are never applied mid-step. This ensures:

1. Each step executes with a consistent set of parameters
2. The step outcome can be replayed given the override state at step start
3. Overrides do not mutate DAG node state or step outputs
4. Clearing an override restores the system to its default behavior for the next step

Override history is recorded in `SessionOverrides.history` — a chronological list of `{key, prev, new, note, ts}` entries — providing a complete audit trail for replay analysis.

---

## Override Application Points in agent.py

| Override Key | Read Location | Effect |
|-------------|--------------|--------|
| `retry_budget` | Error retry loop | Replaces `config.MAX_RETRIES` for this session |
| `confidence_threshold` | Confidence engine call | Replaces `HITL_ESCALATION_THRESHOLD` |
| `execution_timeout` | Tool execution timeout | Replaces per-tool timeout |
| `provider` | Router call | Forces router to use specific provider |
| `model` | Router call | Forces router to use specific model |
| `compression_aggressiveness` | Context compression call | Adjusts when compression triggers |

---

## Override Explainability

Every successful override application generates a `DecisionRecord` in `runtime/explainability.py`:

```
decision_type: "provider_switch"    (for provider/model overrides)
               "execution_pause"    (for numerical overrides)
summary:       "Override applied: confidence_threshold = 0.25"
reason_category: "policy"
contributing_factors: ["key=confidence_threshold", "value=0.25", "note=recovery action"]
outcome:       "Runtime override active for confidence_threshold"
```

These records appear in the Z28 Live Decision Feed under the Z28A panel.

---

## Override Security Considerations

1. **All overrides require an active session:** `apply_override(sid, ...)` creates an empty `SessionOverrides` if the session doesn't exist yet, but the agent only reads overrides for its own session ID. No cross-session override pollution is possible.

2. **Override endpoint is authenticated:** The `POST /api/z29/overrides/{sid}` route is behind the existing session auth middleware.

3. **Dangerous override combinations:** Setting both `confidence_threshold = 0.0` and `retry_budget = 20` simultaneously disables HITL escalation and maximizes retries — this combination should be flagged at the governance layer. The current implementation does not automatically submit a governance request for this specific combination; the `override_confidence` op type with HIGH_RISK severity would need to be explicitly called by the route handler.

4. **Override persistence:** Overrides are in-memory only. A server restart clears all overrides. If persistence is needed, operators must reapply overrides after restart.

---

## Remaining Override Risks

1. **No combination validation:** Individual overrides are validated in isolation. Dangerous combinations (e.g., zero confidence threshold + max retries) are not flagged.

2. **No expiry mechanism:** Overrides remain active indefinitely until cleared. Long-running sessions could retain stale overrides from a previous operator intervention.

3. **Governance gate bypass for WARNING-level overrides:** `provider` and `model` overrides are classified as WARNING severity, which auto-approves by default. A hostile or mistaken override of provider to a non-existent endpoint would not require approval.
