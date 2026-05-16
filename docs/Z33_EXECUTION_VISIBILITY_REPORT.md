# Z33 Execution Visibility Report

**Phase:** Z33B — Live Execution Experience  
**Status:** CERTIFIED  
**Date:** 2026-05-16

---

## Timeline Dock

| Capability | Implementation | Notes |
|-----------|---------------|-------|
| Event recording | NxBus event listeners, no polling | Zero network overhead |
| Max retention | 500 events/session, trimmed FIFO | Memory bounded |
| Semantic grouping | ≥3 consecutive same-type events → collapsed group row | Prevents retry storm spam |
| Display window | Last 200 events rendered (grouped) | Performance bounded |
| Auto-scroll | Opt-in, detects if user scrolled up | Respects user intent |
| Expand/collapse | Single-click header toggle, CSS transition | 0.2s ease |

## Event Types Tracked

| Type | Dot Color | Badge | Source |
|------|-----------|-------|--------|
| `node-done` | Green | — | `dag.node.done` NxBus |
| `node-error` | Red | — | `dag.node.error` NxBus |
| `retry` | Orange | ↺N | `agent.log_row` (regex) |
| `replan` | Purple | ⬡N | `dag.replan.triggered` NxBus |
| `hitl` | Yellow | ⏸N | `hitl.escalation` NxBus |
| `conf-drop` | Blue | ◉N | `z32.confidence.update` NxBus |
| `compress` | Purple (dim) | — | `z32.context.compressed` NxBus |
| `recovery` | Teal | — | `recovery` NxBus |

## Semantic Event Grouping

Grouping collapses ≥3 consecutive same-type events into `TypeName ×N`. This prevents:
- Retry storm spam (50 retry rows → "Retry ×50")
- Node completion floods in parallelized sessions
- Log noise drowning out signal events

Session-start / session-done events are never grouped.

## Replay Scrubbing Integration

Timeline dock is event-driven (NxBus). It does not currently drive DAG state scrubbing directly. The Z30 replay scrubber (`z30ReplayScrubber`) handles DAG state sync. The timeline dock and the replay scrubber are complementary — timeline for event-level history, scrubber for DAG state history.

## Remaining Replay Readability Weaknesses

1. **Retry detection is regex-based**: Detecting retries from `agent.log_row` text using `/retry\s*#?\d+/i` is fragile. If the log format changes, retries won't appear in the timeline.  
   Mitigation: emit a `agent.retry` NxBus event from the execution engine directly.
2. **No timestamp scrubbing**: Clicking a timeline event does not sync the DAG replay scrubber to that point in time. Mitigation: emit `timeline.event.clicked` → Z30 replay seeks to matching snapshot index.
3. **conf-drop threshold is hardcoded to `level === 'LOW'`**: Moderate confidence degradation (MEDIUM dropping toward LOW) does not appear in timeline. Mitigation: track drift direction and emit on significant negative drift.

## Production-Readiness Verdict

**PRODUCTION-READY.** Timeline dock provides clear execution chronology without visual overload. Semantic grouping effectively suppresses retry storm noise.
