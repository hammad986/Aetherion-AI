# Z26 — HITL Escalation Matrix

## Overview

Human-in-the-loop (HITL) escalation is triggered when runtime confidence falls below the critical threshold or when specific policy conditions are met.

## Escalation Thresholds

| Condition                          | Threshold          | Response                       |
|------------------------------------|--------------------|---------------------------------|
| Step confidence score              | < 0.35             | Pause step, alert operator      |
| Sustained session avg (5-window)   | < 0.35             | Flag session for review         |
| Sustained low avg (5-window)       | < 0.50             | Operator warning notification   |
| Retry count (single step)          | ≥ 4                | Escalate regardless of score    |
| Tool failure rate                  | ≥ 4 failures/step  | Immediate escalation            |
| Hallucination markers detected     | ≥ 1 marker         | Score penalty + log             |

## Escalation Actions by Level

### CRITICAL (score < 0.35)
- `requires_hitl = True` on `ConfidenceReport`
- Operator alert message generated
- Session tracker records escalation
- Caller MUST pause execution before proceeding
- Log entry written at WARNING level

### LOW (0.35–0.49)
- `requires_hitl = False`
- Operator alert string populated
- Log entry written at INFO level
- Execution continues but operator is notified

### MODERATE / HIGH
- No escalation
- Score recorded in telemetry
- No operator notification

## Escalation Sources

| Source                     | Decision Type      | See Also                        |
|----------------------------|--------------------|----------------------------------|
| Confidence engine          | Automatic scoring  | `runtime/confidence_engine.py`   |
| Explainability engine      | Manual record      | `runtime/explainability.py`      |
| Execution planner          | Policy check       | `execution_planner.py`           |
| Governance layer           | Safety gate        | `governance_layer.py`            |

## Operator Response Protocol

When a HITL escalation is presented to the operator:

1. **Review** the confidence report and contributing signals
2. **Inspect** the step's decision explanation (`explain_escalation()`)
3. **Choose**: Continue / Abort / Override with modified instructions
4. **Log** the operator decision for audit trail

## Deferral Policy

An escalation may be deferred (not acted upon immediately) only if:
- The operator is unavailable AND
- The step is non-destructive (read-only tool calls) AND
- The confidence score is ≥ 0.20

Destructive operations (file writes, shell commands, network calls) MUST NOT proceed without operator acknowledgment when `requires_hitl = True`.

## FUTURE_RUNTIME markers

- `FUTURE_RUNTIME_ASYNC_HITL`: async human review queue with SLA tracking — deferred to v2
- `FUTURE_RUNTIME_APPROVAL_WORKFLOWS`: multi-approver escalation chains — deferred to v2
