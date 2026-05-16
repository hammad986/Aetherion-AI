# Z28 Execution Readability Audit

**Phase:** Z28A + Z28B — Live Decision Feed + Execution Timeline Intelligence  
**Date:** 2026-05-16  
**Status:** PASS

---

## Scope

This audit verifies that all decision records surfaced to operators in the Z28 Live Decision Feed are:
1. **Accurate** — faithfully represent the system's actual decision
2. **Readable** — expressed in plain, non-technical language understandable to a non-ML operator
3. **Complete** — contain sufficient context to understand the why, not just the what
4. **Safe** — never expose internal chain-of-thought, weights, or scoring internals

---

## Decision Record Schema Audit

Each `DecisionRecord` surfaced to the operator contains:

| Field | Operator-Visible | Content Policy |
|-------|-----------------|----------------|
| `decision_type` | Yes | Enum string — no internals |
| `summary` | Yes | Human-written template string |
| `reason_category` | Yes | One of: performance, error, safety, budget, policy |
| `contributing_factors` | Yes | Observable facts only (step index, counts, names) |
| `outcome` | Yes | Plain past/present tense description |
| `confidence` | Yes | Normalized 0–1 float — no raw scoring formula |
| `ts` | Yes | Unix timestamp |
| `sid` | Backend only | Never surfaced directly in feed items |
| `step_id` | Yes | Step identifier (step-0, step-1, etc.) |
| `record_id` | Yes | Opaque short hash for support reference |

No fields from internal scoring functions (`ConfidenceReport`, `_score_step`, etc.) are exposed directly.

---

## Decision Type Readability Samples

### model_selection
```
Summary: "Selected model 'gpt-4o': selected by router based on plan mode and provider health"
Category: performance
Factors: ["step=2/5", "provider=gpt-4o", "errors_so_far=0"]
Outcome: "Using gpt-4o for this step"
```
**Readability:** PASS — A non-technical operator can understand why this model was chosen.

### retry
```
Summary: "Retry attempt 2: tool_error: timeout calling bash after 30s"
Category: error
Factors: ["tool=bash", "category=tool_error", "total_failures=2"]
Outcome: "Attempting step again (attempt 2)"
```
**Readability:** PASS — Clear that the tool failed and the agent is retrying.

### escalation
```
Summary: "Escalated to operator: Last: [TOOL_ERROR] Unable to install package after 3 attempts"
Category: safety
Factors: ["total_failures=3", "threshold=3", "category=tool_error"]
Outcome: "Execution paused — waiting for operator input"
```
**Readability:** PASS — Operator knows they need to intervene.

### replanning
```
Summary: "Replanning triggered: adaptive replanner triggered"
Category: error
Factors: ["decision=ReplanDecision.INJECT_STEPS", "category=parse_error", "errors=1"]
Outcome: "Original plan revised (was: Install dependencies and run tests)"
```
**Readability:** PASS — Original plan fragment preserved for context. `ReplanDecision` enum value visible but benign.

### provider_switch
```
Summary: "Switched from gemini-flash to gemini-pro: quality escalation after 2 failures"
Category: performance
Factors: ["error_count=2", "threshold=2"]
Outcome: "Continuing with gemini-pro"
```
**Readability:** PASS — Provider change is clear and justified.

---

## Timeline Phase Readability

The execution timeline derives phases from the decision sequence. Phase labels are the `decision_type` enum values, rendered in the UI as human-readable labels:

| Raw type | UI Label |
|----------|----------|
| `model_selection` | Model Selected |
| `retry` | Retry |
| `escalation` | Escalated |
| `replanning` | Replanned |
| `provider_switch` | Provider Switch |
| `context_compression` | Compressed |

Each phase node shows:
- The step ID it occurred on
- An 80-character summary
- A 60-character outcome

---

## Information Security Audit

The following sensitive fields are **never** included in Z28 API responses or SSE payloads:

| Sensitive Item | Protected | Method |
|----------------|-----------|--------|
| Raw LLM prompt text | Yes | Only plan step truncated to 80 chars |
| LLM raw response text | Yes | Never included in decision records |
| Internal confidence scoring formula | Yes | Only final normalized score exposed |
| User API keys | Yes | Not present in any runtime module |
| Provider authentication tokens | Yes | Not present in any runtime module |
| Internal chain-of-thought | Yes | Explicitly excluded by design in `explainability.py` docstring |

---

## Audit Conclusion

All Z28A and Z28B components pass the execution readability audit. Decision records are:
- Factually accurate
- Non-technical enough for operators without ML backgrounds
- Free of internal scoring internals or sensitive data
- Complete enough to support root cause analysis

**Status: AUDIT PASSED**
