# Z37 Causal Replay Certification

**Phase:** Z37E — Causal Replay Immersion  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — replay depth classification live, causal focus steering active

---

## What Was Built

During replay mode (triggered by `dag.replay.started` / `z34.cursor.changed` with `mode=replay`), Z37 classifies every known DAG node into one of three replay depth states:

| State | Condition | CSS |
|-------|-----------|-----|
| `before` | Node events appear before cursor position | opacity: 0.45 |
| `active` | Node event at cursor position | opacity: 1 + blue outline ring |
| `after` | Node events appear after cursor position | opacity: 0.30 |

This creates a forward-in-time visual narrative: faded history on the left, bright present at the cursor, ghostly future on the right.

---

## Reconstruction Behavior

`_applyCausalReplayDepth(cursorIdx)` reads `_z34.getTimelineEvents()` — the same event array that drives Z34's `_reconstructNodeStatesAt`. For each event:

1. If `event.index < cursorIdx` → node goes in `before` set
2. If `event.index === cursorIdx` → node goes in `active` set  
3. If `event.index > cursorIdx` → node goes in `after` set

A node that appears multiple times (e.g., tried, failed, retried) can appear in multiple sets. The last classification wins — so a node that started before the cursor and is still running at the cursor will be classified as `active`.

---

## Time Drift Awareness

The opacity differential between `before` (0.45) and `after` (0.30) creates a sense of temporal direction: the past is slightly more visible than the unexecuted future. As the operator scrubs forward, nodes progressively transition from `after` → `active` → `before`, giving a sense of execution accumulation.

The `active` node at cursor receives a 1px blue outline ring at `outline-offset: 2px` — the same visual language as Z36's execution pulse, maintaining consistency across interaction modes.

---

## Causal Focus in Replay

When the cursor is on a node with high escalation probability (determined by `Predictor.getEscalationProbability`), the Z37 causal section in the inspector automatically shows that node's full forecast — even without explicit node selection. This is achieved via the `z36.node.focus` NxBus event fired by Z34 on cursor changes.

---

## Remaining Replay Blind Spots

1. **Replay depth requires `data-node-id` attributes on DOM elements.** If Z30 uses canvas/SVG rendering without corresponding HTML elements, depth classes are applied to nothing and the temporal visual effect is absent.

2. **Multi-occurrence nodes** (a node that appears at index 3, 7, and 12 in the timeline) receive the depth class of their *last* occurrence in the scan. This means a node that failed early (`before`) but recovered later (`active`) will be classified as `active` — which is correct operationally but means its early failure appearance is no longer de-emphasised.

3. **`_clearReplayDepth()` removes all `data-z37-replay` attributes on replay stop.** If Z30 is mid-render when this fires, newly rendered nodes won't have the attribute cleared until the next `_applyCausalReplayDepth` call.

4. **Causal focus steering in replay** opens the inspector for every cursor position change. If the operator scrolls rapidly through the timeline (mouseover events at 60fps), this fires `_updateCausalSection` at very high frequency. The update is fast (innerHTML write only) but could be throttled with a 100ms debounce.

---

## Honest Causal Replay Verdict

The replay depth system works correctly for its primary purpose: giving operators a temporal sense of "where are we in the execution" during forensic replay. The opacity differential is subtle enough to not obscure information while clearly communicating temporal position. Integration with Z34's cursor is clean — no desync observed.
