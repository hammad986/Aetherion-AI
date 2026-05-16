# Z34 Runtime Continuity Analysis

**Phase:** Z34D — Execution Continuity Memory  
**Date:** 2026-05-16  
**Verdict:** FOUNDATIONAL — cross-session awareness established

---

## What Was Built

Z34D introduces cross-session continuity awareness without adding new backend infrastructure. It uses the existing Z31 forensic API (`/api/z31/sessions`) to:

1. **Group session threads** — sessions within 2 hours of each other are grouped into a "thread", representing a continuous work period.
2. **Build failure lineage** — the most recent failed sessions are rendered as a connected chain, showing how failure propagated over time.
3. **Map recovery outcomes** — each replan applied during the current session is recorded in `S.recoveryMap[nodeId]` with the trigger, action, and whether it succeeded.
4. **Skill-to-recovery correlation** — implicit: skills extracted by Z32D are associated with the current session's node recovery events via the shared node index.

---

## Persistent Execution Threads

Sessions are grouped by proximity in time (< 2 hour gap = same thread). Up to 3 threads are shown in the continuity panel, each rendered as a row of colored dots. The active session is highlighted in blue. Clicking any dot loads that session's DAG via Z31's `loadReplay()`.

This gives operators a visual "context stack" — they can see at a glance that they're continuing work from a previous session on the same problem.

---

## Failure Lineage

Up to 8 recent failed sessions are rendered as a connected chain with dots and age labels. The rightmost (most recent) failure is emphasized. Clicking loads the session for forensic inspection. This lets operators trace recurring failure patterns without manual cross-referencing.

---

## Recovery Success Mapping

During live execution, every `z32.replan.applied` event is recorded:

```javascript
S.recoveryMap[nodeId] = [
  { trigger: "confidence_low", success: true, details: "switched to fallback model", ts: ... },
  ...
]
```

The inspector displays the last 5 replan outcomes per node, giving operators a "does replanning work for this node type?" signal.

---

## Remaining Weaknesses

| Item | Notes |
|------|-------|
| Thread grouping uses age metadata from Z31, not actual timestamps | Approximate — age_s precision is integer seconds |
| Recovery outcomes are session-scoped and lost on page reload | Z31 snapshot persistence does not save `recoveryMap` |
| Failure lineage requires Z31 `filter=failed` to be implemented in the backend | Currently uses `filter=instability` as proxy |
| Skill-to-recovery correlation is implicit only — no explicit join between Z32D skills and Z34D recovery events | Future: explicit skill recall tagging |

---

## Data Sources

| Data | Source | Freshness |
|------|--------|-----------|
| Session threads | `GET /api/z31/sessions?limit=20` | Loaded on session start |
| Failure lineage | `GET /api/z31/sessions?filter=failed&limit=10` | Loaded on session start |
| Recovery outcomes | Live NxBus `z32.replan.applied` events | Real-time |
| Replan history | `S.recoveryMap` in-memory | Session-scoped |
