# Z33 Runtime UX Audit

**Phase:** Z33A + Z33E  
**Status:** AUDITED  
**Date:** 2026-05-16

---

## Idle State Assessment

| Element | Before Z33 | After Z33 | Risk |
|---------|-----------|-----------|------|
| Center workspace | Static "Ready for execution" title, no live data | Z32 confidence + pressure injected into status strip | LOW |
| Status strip | Model name only | Model + Confidence + Context% + Scheduled | LOW |
| Recent forensic alerts | Not surfaced | Z31 session alerts injected as idle signals | LOW |
| Replay resume | Not surfaced | Last forensic session card shown | LOW |
| Pending approvals | Not surfaced | HITL queue count shown when >0 | LOW |
| Quick actions | Static chip set | Unchanged — suitable for most use cases | LOW |

## Runtime Pulse

- Location: topbar, right of the run/stop group
- States: Idle (grey dot) → Active (green, 2.4s beat) → Degraded (yellow) → Critical (red, 0.8s beat)
- Pulse label uses mono font, no animation on the label itself — only the dot animates
- Source: Z32 pressure_level (live from API) + session lifecycle events

## Remaining UX Instability Zones

1. **Idle hero refresh latency**: The hero refreshes every 45s and on session end. A session that fails mid-execution and doesn't fire `session.error` correctly will leave a stale idle state.
2. **HITL queue polling**: `/api/hitl/pending` is fetched on idle refresh. If the endpoint is unavailable, approvals row silently stays hidden — operator may miss pending items.
3. **Z32 data in idle strip**: Confidence and context pressure are populated from the last Z32 poll — they reflect the last active session, not the current state if no session is active.

## Production-Readiness Verdict

**OPERATIONALLY COMPLETE.** Idle state now surfaces meaningful runtime signals without visual noise. All animations are calm (≥2.4s cycles). No neon effects.
