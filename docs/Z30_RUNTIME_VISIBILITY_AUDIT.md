# Z30 Runtime Visibility Audit

**Phase:** Z30 — Structural Operational Visibility  
**Status:** AUDITED  
**Date:** 2026-05-16

---

## Audit Scope

Examines the completeness of runtime visibility across all execution subsystems.

---

## Visibility Coverage Matrix

| Subsystem | Visible | Mechanism | Gap |
|-----------|---------|-----------|-----|
| Task lifecycle (queued→running→done) | ✅ | SSE events → NxBus → DAG nodes | None |
| Tool calls | ✅ | `agent.tool_call` / `agent.tool_result` NxBus events | None |
| File modifications | ✅ | `FILE_WRITE_RE` detection in log rows | Regex-based, not event-sourced |
| Retry branches | ✅ | Retry count badge on DAG nodes + dashed edges | Count only, not per-retry detail |
| Provider/model routing | ✅ | Regex extraction from log text | Structured event preferred |
| Token usage | ✅ | Regex extraction from log text | Not per-node, cumulative only |
| Confidence scores | ✅ | `confidence_engine.py` output parsed from logs | — |
| Context pressure | ✅ | Instability heatmap (`z30-health-bar`) | — |
| SSE degradation | ⚠ | Instability counter only | No per-stream health visibility |
| Stuck nodes | ✅ | 90s inactivity watcher | Timer-based, not event-sourced |
| Escalation paths | ⚠ | Detected via severity pattern matching | No structured escalation events |
| Recovery branches | ⚠ | `mission_recovery.py` logs detected heuristically | No dedicated recovery event type |

---

## Operational Blind Spots

1. **Cross-worker visibility**: In multi-worker deployments, DAG synthesis is per-session local only. Cross-worker node states are not aggregated.
2. **Background agent tasks**: `TaskScheduler` background agents emit logs but no structured DAG events. Synthesized nodes may be missing stage context.
3. **Redis SSE failover**: When Redis SSE bridge is disabled, instability detection for SSE degradation falls back to absence-of-logs heuristic.
4. **Sub-node granularity**: Nodes are grouped by execution phase. Individual tool call sequences within a phase are not individually tracked as sub-nodes.

---

## Recommendations

- Add `EventTypes.ESCALATED`, `EventTypes.RECOVERED` to `execution/events.py`.
- Emit structured `dag_update` SSE events from `orchestrator.py` on each state transition.
- Add per-provider SSE health counters to instability report.

---

## Verdict

Runtime visibility is **OPERATIONALLY ADEQUATE** for single-worker sessions. Multi-worker and background-agent visibility requires structured event emission from the orchestration layer.
