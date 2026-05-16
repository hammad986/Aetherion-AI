# Z32 Replanning Certification

**Phase:** Z32C — Adaptive DAG Replanning  
**Status:** CERTIFIED  
**Date:** 2026-05-16

---

## Replanning Trigger Matrix

| Trigger | Condition | Recommended Action |
|---------|-----------|-------------------|
| `retry_threshold` | retry_count > 3 | Insert fallback execution path |
| `validation_failure` | validation_failures > 2 | Replace failing node |
| `tool_instability` | tool_error_rate > 40% | Switch to backup provider |
| `dependency_broken` | blocked_count > 0 | Reroute dependency chain |
| `provider_failure` | provider_failures > 1 | Switch to backup provider |

---

## Replanning Actions

| Action | Description | DAG Modification |
|--------|-------------|-----------------|
| `fallback_execution_path` | Insert recovery branch, bypass failing node | New recovery edge from stuck node to handler |
| `node_replacement` | Replace failing node with re-verification variant | Node state set to `replanning`, new node inserted |
| `provider_switching` | Route next tool calls to backup provider | Stored as provider preference override in session |
| `dependency_rerouting` | Inject dependency-resolution node between blocked nodes | New edge: resolver → blocked |

---

## Replay Safety

- Every replanning event is stored in `replanning_events` with: trigger, action, node_id, before/after state JSON.
- Replanning events are included in forensic bundles (`GET /api/z31/export/<sid>`).
- `replayable: true` — all replanning actions can be stepped through in DAG replay.
- Replanning history viewable via `GET /api/z32/replan/<sid>/history`.

---

## Transparency + Inspectability

- DAG nodes undergoing replanning are marked with state `replanning` (yellow).
- Replaced nodes retain their history with state `replaced` (dim strikethrough).
- Recovery branches are drawn as dashed edges in the DAG.
- All replanning events are exported in forensic bundles.

---

## Remaining DAG Recovery Weaknesses

1. **No automatic replan application**: The replanning engine evaluates and recommends, but does not yet automatically apply the replanning action. The operator or the execution engine must read the `plan` from the API and apply it.
2. **Single-trigger evaluation**: `_evaluate_replan()` returns the first matching trigger only. Multiple simultaneous triggers are not co-evaluated. Mitigation: return all triggered replanning plans, prioritized by severity.
3. **No dependency graph awareness**: Replanning currently treats nodes as independent. It does not model inter-node dependencies for rerouting decisions. Mitigation: pass `edges` array to `_evaluate_replan()`.
4. **Provider switching without warm-up**: Switching providers mid-session may lose session state (system prompt, conversation history) for stateful LLM providers. Mitigation: emit a context-reconstruction event before the switch.
5. **Replanning loop**: Repeated replanning for the same trigger does not detect a replanning loop. Mitigation: add `replan_count` guard — after 3 replanning events for the same trigger, escalate to HITL.

---

## Production-Readiness Verdict

**PRODUCTION-READY as an advisory system.** Automatic replanning application requires careful integration with the execution engine and HITL approval gating.
