# Z27 — Frontend Stability Report

## Summary

Frontend stability assessment as of Phase Z27.

---

## JavaScript Error Status

| Error | Status |
|-------|--------|
| `NxBus.EVENTS.PALETTE_OPEN` on cold start | Fixed (Z26 session) |
| `nxTogglePanel is not defined` | Fixed (Z26 session) |
| All 8 NxBus.EVENTS guards in 8 JS files | Fixed (Z26 session) |
| Permissions-Policy header with unquoted URL | Fixed (Z26 session) |

**Current console state**: Zero errors. One recurring warning (MutationObserver budget).

---

## Remaining Warning

```
[NDS Perf] MutationObservers: 10 exceeds budget 8.
```

This is a self-monitoring performance budget warning, not a crash. 10 MutationObserver instances are active. The budget of 8 is an internal soft limit. This warning fires every ~15 seconds.

**Impact**: None on functionality. Low performance risk on low-end devices during long sessions.

**Mitigation path**: Audit `activity.js`, `ui.js`, and `nx-hardening.js` for observer consolidation. Deferred to v1.5.

---

## Boot Performance

| Task | Timing |
|------|--------|
| Slowest boot task | `static/js/boot.js:161` — 67.8ms (Phase 7 agent system) |
| All other boot tasks | < 5ms |

Phase 7 agent initialization is the dominant boot cost. Acceptable for current usage.

---

## SSE Reliability

- SSE reconnect working correctly
- Auth sign-in detected: `[Auth] Signed in successfully`
- No SSE errors in console
- Browser console clean after sign-in

---

## Module Load Order

The NxBus race condition pattern (modules initializing before `NxBus.EVENTS` is populated) is fully mitigated across all 8 affected JS files with `!NxBus.EVENTS` guards.

---

## New SSE Events (Z27A)

The following new events are emitted from `agent.py` and must be consumed by the frontend:

| Event | Recommended UI action |
|-------|-----------------------|
| `agent.context_state` | Update context pressure indicator |
| `agent.confidence_warning` | Show confidence alert banner |
| `agent.runtime_telemetry` | Log to operator panel (silent) |

Frontend consumers for these events are not yet implemented. Events are emitted and received by SSE infrastructure without UI rendering. This is acceptable for beta.

---

## Verdict

Frontend is **stable and error-free** at the JS runtime level. Boot is clean. SSE is reliable. Zero unhandled errors after sign-in. Ready for beta operation.
