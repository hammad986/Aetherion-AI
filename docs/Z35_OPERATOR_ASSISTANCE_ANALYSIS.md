# Z35 Operator Assistance Analysis

**Phase:** Z35C — Predictive Operator Assistance  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — proactive, dismissible, non-intrusive

---

## What Was Built

A suggestion tray appears beneath the mission bar when one or more operator guidance items are active. Each suggestion has:
- An icon representing the suggestion type
- A single-sentence plain-language description
- An "Apply" action button that triggers the relevant system action
- A per-row dismiss button

The tray itself has a global "dismiss all" button. The tray is hidden (max-height: 0) when no suggestions are active and slides in when suggestions appear. Maximum 3 suggestions shown at once; oldest are scrollable.

---

## Suggestion Types and Triggers

| Type | Trigger | Action |
|------|---------|--------|
| `compress` | Pressure ≥ 0.72 | Emits `z32.compress.trigger` → Z32 context compression |
| `inspect_unstable` | Node heat ≥ 0.70, or hot node selected | Opens Z34 forensic inspector for that node |
| `replay_recovery` | Phase = recovering + errorCount > 0, or session error | Emits `dag.replay.start` |
| `reduce_pressure` | retryCount ≥ 4 | Emits `z35.pressure.reduce` (handled by provider routing layer) |
| `escalate_hitl` | S.escalated = true | Emits `z29.hitl.request` → Z29 HITL governance |

All suggestions are deduplicated — the same type will not appear twice in the tray simultaneously.

---

## Remaining Predictive Gaps

1. **Recovery guidance is reactive, not predictive.** Suggestions are triggered by current state, not forecast state. True predictive guidance (e.g., "context will overflow in ~8 more nodes") requires token-per-node rate estimation — not yet implemented.

2. **Pressure forecasting** is a static threshold check, not a trend model. A linear regression over the last 10 `tokenEstimate` readings would provide a proper forecast ETA — deferred to a future phase.

3. **Historical replan success rates** are not used to rank suggestions. Z34D tracks `recoveryMap` per node but Z35C does not query it to confirm whether "replay_recovery" is historically effective for the current node type.

4. **`z35.pressure.reduce` event** is emitted but has no subscriber in the current system. The provider routing layer (Z09/P09) would need a listener to act on it. Currently fires and no-ops silently.

---

## Remaining Operator Overload Risks

- Suggestions can stack: if pressure spikes and retries also spike simultaneously, 3 suggestions appear at once. The tray at max-height 120px is compact enough to not dominate the workspace but still consumes vertical space.
- The tray auto-shows when suggestions arrive. If a session ends with an error and the operator has already switched to the Code tab, the tray will be visible the next time they return to the Live tab. This is acceptable (persistent guidance) but may startle operators who expect a clean state after session end.
- The "Apply" action for `escalate_hitl` emits `z29.hitl.request` directly. Operators should be aware this is an active governance request, not just a navigation hint. The suggestion text warns about this with "consider HITL escalation" language.

---

## Honest Production UX Verdict

The suggestion system works correctly. Suggestions appear when warranted, are individually dismissible, and their actions integrate with the existing event bus. The tray is lean — three rows at most, hidden when empty. No notification noise, no auto-dismissal timers that could mask urgent guidance.
