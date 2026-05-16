# Z28 Operator Intelligence Layer — Certification Report

**Phase:** Z28  
**Status:** CERTIFIED  
**Date:** 2026-05-16  
**Scope:** Z28A · Z28B · Z28C · Z28D · Z28E

---

## Executive Summary

Phase Z28 delivers the Operator Intelligence Layer — a complete live execution explainability system that surfaces real-time AI decision reasoning, execution phase timelines, context/memory pressure, and confidence health to operators without exposing internal chain-of-thought.

The system operates as a pure read-only overlay on the existing execution pipeline. All data flows through the live SSE stream (`agent.explain`) and four polling API endpoints. No execution logic is modified; all wiring is additive.

---

## Component Certification

### Z28A — Live Decision Feed

| Item | Status |
|------|--------|
| SSE event `agent.explain` broadcast from agent.py | PASS |
| NxBus routing `agent.explain` → `nx:z28:decision` | PASS |
| Decision feed UI — real-time item injection | PASS |
| Polling fallback `GET /api/z28/decisions` | PASS |
| Filter controls (All / Model / Retry / Escalation / Provider / Replan) | PASS |
| Decision type color coding | PASS |
| Confidence badge on each record | PASS |
| Contributing factors expandable display | PASS |

### Z28B — Execution Timeline Intelligence

| Item | Status |
|------|--------|
| Phase progression derived from decision sequence | PASS |
| Timeline bar with active/completed/error states | PASS |
| REST endpoint `GET /api/z28/timeline` | PASS |
| Session summary integration | PASS |
| Realtime phase advancement on new decisions | PASS |

### Z28C — Context + Memory Pressure Visibility

| Item | Status |
|------|--------|
| Token budget bar (0–100%) | PASS |
| SSE event `agent.context_state` → `nx:z28:context` | PASS |
| REST endpoint `GET /api/z28/context-pressure` | PASS |
| Episode count display | PASS |
| Compression event count | PASS |
| Critical notes count | PASS |
| Audit tail (last 10 compression events) | PASS |

### Z28D — Confidence + Execution Health Layer

| Item | Status |
|------|--------|
| Health bar with level indicator (high/medium/low/critical) | PASS |
| SSE event `agent.confidence_warning` → `nx:z28:health` | PASS |
| REST endpoint `GET /api/z28/health` | PASS |
| Rolling confidence score history (last 20 samples) | PASS |
| Retry count display | PASS |
| HITL active state indicator | PASS |
| Active provider display | PASS |
| Confidence signal log | PASS |

### Z28E — UI Stability + Forensic Review

| Item | Status |
|------|--------|
| Intel tab button in main tab bar | PASS |
| Tab content container `#nxTab-intel` | PASS |
| CSS loaded `/static/css/nx-z28-operator.css` | PASS |
| JS module loaded `/static/js/nx-z28-operator.js` | PASS |
| Lazy init: mounted only on first tab activation | PASS |
| Session sync: `_z28.setSid()` on SESSION_CREATED | PASS |
| Intel dot indicator on new decision | PASS |
| Dark/light theme compatible via CSS variables | PASS |
| Five documentation files created in `docs/` | PASS |

---

## Architecture Notes

- **No execution side-effects.** All Z28 components are read-only observers of the existing Z26 explainability pipeline.
- **Fail-safe.** All SSE broadcasts are wrapped in try/except; UI components use polling fallback if SSE is unavailable.
- **Session-scoped.** All API endpoints accept `?sid=` filter; absence returns global recent history.
- **Budget-safe.** Decision record memory is capped at 2,000 records (`_MAX_RECORDS = 2000` in `runtime/explainability.py`).

---

## Data Flow

```
agent.py
  └─ _z26_explain_model/retry/replan/provider/escalation()
       ├─ runtime/explainability.py → DecisionRecord → _records[]
       └─ emit_fn("agent.explain", record.to_dict())
            └─ streaming/sse_manager.py → SSE stream
                 └─ nx-sse-runtime.js → NxBus.emit('nx:z28:decision')
                      └─ nx-z28-operator.js → Decision Feed UI

Polling (every 8s):
  GET /api/z28/decisions  → Decision feed refresh
  GET /api/z28/health     → Confidence + health state
  GET /api/z28/context-pressure → Token pressure state
  GET /api/z28/timeline   → Phase timeline
```

---

## Certification Sign-off

All Z28 sub-phases (A through E) are implemented, wired, and stable. No regressions introduced to existing execution path. The Operator Intelligence Layer is certified for production use.
