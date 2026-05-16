# Z27 — Execution Visibility Report

## Purpose

Operators must understand what the system is doing in real time. This report documents what is now visible during execution and what remains deferred.

---

## What is Now Visible

### During Execution (Real-Time SSE)

| Signal | Event | Description |
|--------|-------|-------------|
| Current model/provider | `agent.budget_update` | Model used + token budget |
| Context token pressure | `agent.context_state` | % of token budget used, episode count |
| Confidence warning | `agent.confidence_warning` | Score, level, alert message |
| HITL required | `hitl.required` | Pause + operator prompt |
| Step thought | `agent.think` | Current thinking step |
| Step result | `step_result` | Success/failure + summary |
| Task complete | `agent.task_complete` | Final status + confidence |
| Runtime telemetry | `agent.runtime_telemetry` | Final context + confidence summary |

### In the Idle Workspace (Polled)

| Information | Source | Refresh |
|-------------|--------|---------|
| Active model | `/api/system/metrics` | On idle load |
| Recent sessions | `/api/sessions` | On idle load |
| Scheduled missions | `/api/runtime/telemetry` | On idle load |

### Via API (On-Demand)

| Endpoint | Information |
|----------|-------------|
| `/api/runtime/telemetry` | Full compression + confidence + scheduler snapshot |
| `/api/runtime/decisions` | Operator-safe decision log (model selection, retries, escalations, etc.) |
| `/api/runtime/context/{sid}` | Per-session context token usage |

---

## Execution Phase Indicators

The existing phase bar (`nx-exec-pipeline`) shows:
- Planning → Coding → Debugging → Done
- Active stage highlighted via `nx-stage-dot`

The Z27D additions extend this with:
- Context pressure indicator in the status strip
- Confidence level indicator in the status strip

---

## What Remains Deferred

| Visibility Gap | Deferred To |
|---------------|-------------|
| Inline decision explanation feed in UI | v2 |
| Live confidence chart/trend | v2 |
| Real-time context episode visualization | v2 |
| Scheduler queue management UI | v2 |
| Provider switch notification toast | v2 |

---

## HITL Visibility

When HITL escalation triggers:
1. `hitl.required` SSE event → UI shows HITL input panel
2. `agent.confidence_warning` SSE event → UI shows confidence alert
3. `explain_escalation()` → decision logged to explainability registry

The operator sees the reason, contributing factors, and confidence score that triggered the escalation.

---

Status: SHIPPED (Phase Z27D foundation)
