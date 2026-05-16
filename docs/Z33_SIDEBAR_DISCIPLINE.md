# Z33 Sidebar Discipline

**Phase:** Z33C — Operational Sidebar Refinement  
**Status:** AUDITED  
**Date:** 2026-05-16

---

## Workspace Memory Surface

The `z33-sidebar-memory` section surfaces three categories of operational memory in the left sidebar:

| Category | Source | Max Items | Action |
|----------|--------|-----------|--------|
| Recent skills | `GET /api/z32/skills?limit=3` | 3 | Opens skill panel in Live tab |
| Unstable sessions | `GET /api/z31/sessions?filter=instability&limit=2` | 2 | Loads session in forensic panel |

This is intentionally minimal — the goal is ambient awareness, not a full dashboard.

## Contextual Sidebar Adaptation

The sidebar memory panel refreshes:
- On page load (passive)
- On `session.done` (execution complete — update skills)
- On `session.error` (update unstable sessions)
- On demand via `_z33ux.loadMemory()`

## Sidebar Auto-Collapse Logic

Auto-collapse is not implemented as an automatic behavior — collapsing the sidebar without explicit user intent would be disorienting during active execution. Instead, the `z33-sidebar-collapsed-hint` class provides a visual cue when panels are manually collapsed.

Recommendation: implement auto-collapse only for low-priority panels (e.g., the skill memory list) when `pressure_level === 'CRITICAL'` and the user is on the Live tab.

## Remaining Sidebar Instability Zones

1. **No replay bookmark implementation**: Replay bookmarks (marked DAG states worth revisiting) are not yet extracted from Z30 replay events. Mitigation: Z30 `replayExport()` JSON contains all snapshots — parse and surface notable states.
2. **Sidebar memory item count is fixed at 5**: No pagination. If more than 5 items are relevant, older items are silently dropped. Mitigation: add "Show more" pagination.
3. **No unstable session severity sorting**: Unstable sessions appear in chronological order, not by severity. A CORRUPT session from yesterday may appear after a DEGRADED session from today. Mitigation: sort by `integrity_verdict` (CORRUPT first) then by `ts DESC`.

## Production-Readiness Verdict

**PRODUCTION-READY for ambient operational awareness.** Full adaptive sidebar behavior (auto-collapse, bookmark persistence) is a future enhancement.
