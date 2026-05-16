# Z37 Execution Memory Graph

**Phase:** Z37D — Execution Memory Graph  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — historical patterns tracked, semantic insights generated

---

## What Was Built

`ExecutionMemory` is a cross-session accumulator that tracks how nodes behave over time within the process lifetime. Unlike `NodeRegistry` (session-scoped), `ExecutionMemory` is **never cleared** on session start — it persists across sessions until page reload.

---

## Memory Stores

| Store | Key | Data |
|-------|-----|------|
| `_unstableHistory` | nodeId | Count of sessions where node was unstable (had errors/retries) |
| `_expensiveHistory` | nodeId | Cumulative `dur_ms` across all completions |
| `_recoveryPaths` | nodeId → recoveryType → `{count, successes}` | Per-strategy recovery outcomes |
| `_escalationHistory` | array | `{nodeId, ts, resolved}` — escalation events |

---

## Semantic Insight Generation

`getNodeHistory(nodeId)` produces a plain-language `insight` string by combining all available historical signals:

| Condition | Insight fragment |
|-----------|-----------------|
| `unstableCount >= 3` | `"historically unstable (N occurrences)"` |
| `totalDur > 30s cumulative` | `"execution-heavy (~Ns cumulative)"` |
| `escalations.length >= 1` | `"escalated N× in this session"` |
| Best recovery type with success rate | `"best recovery: 'replan' (75% success)"` |

Multiple fragments are joined with ` · `. The final string appears in the inspector under **Runtime Memory** with an italic left-border style — visually distinct from reactive forensic data.

This answers the Z37D requirement: *"this node historically causes instability"* — surfaced as text, not a metric.

---

## Historical Pattern Queries

| Query | Function |
|-------|----------|
| Most historically unstable nodes (top N) | `getMostUnstableNodes(n)` |
| Most execution-expensive nodes (top N) | `getMostExpensiveNodes(n)` |
| Full history for a node | `getNodeHistory(nodeId)` |

These are used internally by Z37C's `getSystemForecast()` for bottleneck ranking and by the inspector's causal section for insight display.

---

## Recovery Path Learning

Every `z32.replan.applied` event records `ExecutionMemory.recordRecovery(nodeId, action, success=true)`. Every `z29.hitl.resolved` event records the recovery as type `'hitl'`. This allows the system to track which recovery strategy works best for which node over time.

If a node has been recovered 5 times, 4 via `replan` (success rate 75%) and 1 via `hitl` (success rate 100%), the insight will surface: `"best recovery: 'hitl' (100% success)"`.

---

## Remaining Continuity Limitations

1. **Persistence is process-scoped** — `ExecutionMemory` lives in the JS module closure. A page reload clears all history. True cross-session memory requires a backend API (e.g., a Z37 `/api/z37/memory` endpoint persisting to SQLite).

2. **`_unstableHistory` increments on every session completion** — if the same session is re-loaded multiple times from Z31 forensics, the count will over-increment. A `session_id` guard would prevent double-counting.

3. **Recovery path attribution is event-based** — if Z32 fires `z32.replan.applied` without a `nodeId`, the recovery is not attributed to any node and is silently dropped.

4. **`getMostExpensiveNodes` uses cumulative `dur_ms`** — a node that ran 100 times for 100ms each will rank higher than a node that ran once for 8000ms. A per-execution average would be more meaningful for bottleneck identification.

5. **Maximum escalation history is 100 entries** — in very long process lifetimes (many sessions, frequent escalations), old escalation records are pruned. This is acceptable — recent history is more operationally relevant.
