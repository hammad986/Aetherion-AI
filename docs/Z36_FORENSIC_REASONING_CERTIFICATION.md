# Z36 Forensic Reasoning Certification

**Phase:** Z36C — Forensic Execution Intelligence  
**Date:** 2026-05-16  
**Verdict:** CERTIFIED — decision chain, failure pressure, recovery intelligence operational

---

## What Was Built

A `#z36ForensicSection` div is appended to the Z34 inspector body (`#z34InspectorBody`). It renders three reasoning blocks for the currently selected node, updated on every node focus change.

---

## Decision Chain

The `NodeRecord.decisionChain` array records every state transition for the node:

```
[{ from: "pending", to: "running", ts: ... },
 { from: "running", to: "error",   ts: ... },
 { from: "error",   to: "running", ts: ... },  ← retry
 { from: "running", to: "done",    ts: ... }]
```

Rendered as a sequence of color-coded state chips with arrows. State colors:
- `running` → blue
- `done`    → green
- `error`   → red
- `pending` → grey

Maximum 6 most recent transitions shown. This answers "why did this node execute" by showing its complete state path.

---

## Failure Pressure Analysis

When `failureReasons.length > 0`, the inspector shows:

| Signal | Derivation | Values |
|--------|-----------|--------|
| Cascade Risk | `errors >= 3` → high, `errors >= 1` → medium | high / medium / none |
| Retry Amplification | `retries >= 4` → amplified, `retries >= 1` → active | amplified / active / none |
| Hotspot | Whether node is in top 3 PressureMemory hotspots | yes / no |

Plus the last 3 failure reason strings (first 80 chars each) with timestamps. This answers "why did the retry occur" and "is this node a systemic bottleneck."

---

## Recovery Intelligence

When `recoveryHistory.length > 0`, the inspector shows:

| Signal | Derivation |
|--------|-----------|
| Recovery Rate | `successes / total × 100` |
| Stabilization Confidence | ≥75% → high, ≥40% → moderate, <40% → low |

Plus the last 3 recovery action strings, color-coded by success (green) or failure (red). This answers "is recovery working for this node type" and "should I escalate."

---

## Pressure Trend Block

When `pressureTrace.length >= 4`, a trend indicator is computed from the last 4 pressure readings:
- `↑ rising` (amber) — last pressure > first + 0.10
- `↓ falling` (green) — first pressure > last + 0.10
- `→ stable` (grey)

This answers "is this node's instability getting worse or better."

---

## Remaining Forensic Blind Spots

1. **Decision chain records state transitions, not reasoning.** The chain shows `running → error → running` but not *why* the state changed (which line of code, which provider response, which tool call). True causal reasoning requires structured log correlation — not yet implemented.

2. **Failure reason strings** are log row text slices (first 80 chars). These may be cut in the middle of a sentence. A future improvement would use the last complete sentence or error type rather than a raw slice.

3. **Recovery history** records the replan action string from Z32's `z32.replan.applied` event. If Z32 does not fire this event (e.g., manual HITL recovery), recovery history remains empty.

4. **Pressure trend** uses a naive linear comparison over 4 points. A rolling 6-point linear regression would be more accurate for slowly rising pressure. Current implementation is adequate for fast-moving sessions.

5. **Stabilization confidence** is derived only from within-session recovery rate. Cross-session recovery rates (from Z31/Z34D continuity data) are not incorporated.
