# Z29 Frontend Governance Review

**Phase:** Z29E — UI Stability + Governance UX Audit  
**Date:** 2026-05-16  
**Status:** STABLE

---

## Scope

Full forensic review of the Z29 governance frontend: module architecture, CSS namespacing, operator cognitive load analysis, control discoverability, escalation visibility, control race conditions, and regression safety.

---

## File Inventory

| File | Role | Size |
|------|------|------|
| `static/js/nx-z29-governance.js` | Z29 governance UI — all four panels | ~430 lines |
| `static/css/nx-z29-governance.css` | Scoped styles for Z29 components | ~360 lines |
| `templates/index.html` (additions) | Govern tab button, content div, init script | +40 lines |
| `static/js/nx-sse-runtime.js` (additions) | 4 new SSE case handlers | +14 lines |

---

## Panel Cognitive Load Assessment

### Panel A: Mission Controls

**Discoverability:** HIGH  
Six clearly labeled control buttons in a 3×2 grid. Color coding follows universal conventions (green = go, amber = pause, red = stop). Button states are dynamically disabled based on mission state (pause disabled when not running, resume disabled when not paused).

**Risk of accidental activation:** MEDIUM  
Cancel button is red and clearly destructive, but there is no confirmation dialog. Recommendation: add a confirm step before cancel in a future iteration.

**Inject input:** CLEAR  
Text input with placeholder text and Enter key support. Send button is styled distinctly from control buttons.

### Panel B: Governance Queue

**Escalation discoverability:** HIGH  
The tab dot (orange pulse) activates when items are pending. The `z29-queue-badge` counter in the header shows pending count. CRITICAL badge shown in red for critical items.

**Approval clarity:** HIGH  
Each item shows: severity pill (color-coded), operation type, human-readable summary, age timestamp, and clearly separated Approve/Reject buttons.

**Empty state:** CLEAR  
Checkmark with "No pending approvals" message prevents confusion when queue is empty.

### Panel C: Override Controls

**Clarity:** HIGH  
Each override field shows label, input, and individual clear button. Active overrides highlighted with blue border. Apply and Clear All buttons are visually distinct.

**Risk of unintended override:** LOW  
Overrides require explicit "Apply Overrides" click. Empty fields are not applied. Individual "×" clear buttons allow granular cleanup.

### Panel D: Recovery Surface

**Cognitive load:** MEDIUM  
Stability score bar gives immediate visual health signal. Anomaly list (color-coded by severity) provides actionable detail. Recovery buttons are labeled in plain language. Recommended actions are highlighted in purple.

**Complexity risk:** LOW  
Recovery buttons are small and not immediately obvious — this is intentional (recovery actions should be deliberate, not accidental).

---

## CSS Namespacing Audit

All Z29 styles are scoped under `.z29-*`. No conflicts with existing `.nx-*` or `.z28-*` namespaces. CSS custom properties used are all existing Nexora design tokens (`--nx-bg-1` through `--nx-bg-4`, `--nx-text-1/2`, `--nx-border`, `--nx-accent`, `--nx-font`), ensuring automatic theme compatibility.

---

## Control Race Condition Analysis

### Pause + Resume race
- Pause sets `_signals[sid] = PAUSE` and `_states[sid] = PAUSED` under `_lock`
- Resume sets `_signals[sid] = RESUME` and `_states[sid] = RUNNING` under same `_lock`
- Both operations are atomic within the lock — no race condition possible on signal assignment
- The agent reads the signal with `check_signal()` which also holds `_lock` during read

### Multiple operator instances
- Two operators acting simultaneously on the same session could issue conflicting signals
- Last write wins — the Python `_lock` ensures sequential writes, but the outcome depends on arrival order
- The audit log records both actions with timestamps, enabling forensic review

### UI polling vs. SSE
- UI polls every 5 seconds for mission state
- SSE `agent.mission_control` events trigger immediate re-poll (within 200ms)
- If SSE is disconnected, the 5-second fallback poll prevents stale state for more than one cycle

---

## SSE Event Routing

| SSE Event | NxBus Channel | Handler |
|-----------|---------------|---------|
| `agent.mission_control` | `nx:z29:mission_control` | Triggers `_pollControls()` |
| `agent.governance_request` | `nx:z29:governance` | Triggers `_pollQueue()` |
| `agent.governance_resolved` | `nx:z29:governance` | Triggers `_pollQueue()` |
| `agent.override_applied` | `nx:z29:override` | Triggers `_pollOverrides()` |
| `agent.stability_alert` | `nx:z29:stability` | Triggers `_pollRecovery()`, pulses dot red |

---

## Regression Safety

### Existing Tabs
Govern tab inserted before `#nx-legacy-tabs`. Tab button follows identical pattern to Intel tab.

### NxBus Events
Four new SSE case branches added before `default:` — no existing handlers modified.

### Script Load Order
`nx-z29-governance.js` loaded with `defer` after all existing scripts. Init script uses same `NxBus.EVENTS` guard pattern as Z28.

---

## Operator Confusion Points

1. **Inject vs. HITL respond:** Operators familiar with HITL may be confused by the distinction between "inject instruction" (proactive, at any time) and "HITL respond" (reactive, only when HITL is waiting). A tooltip or help text on the inject input would reduce this confusion.

2. **Override persistence:** Operators may not realize that overrides survive tab switches. A "OVERRIDES ACTIVE" indicator in the tab header when active overrides exist would improve awareness.

3. **Recovery vs. mission controls:** The boundary between "Recovery" panel (D) and "Mission Controls" panel (A) is conceptually clear (reactive vs. proactive) but may not be obvious to first-time users.

---

## Beta Readiness Verdict

| Dimension | Assessment |
|-----------|-----------|
| Operator control completeness | ✅ All 6 required controls implemented |
| Governance queue readability | ✅ Clear severity-coded approval workflow |
| Override safety | ✅ Validated inputs, audit trail, cooperative application |
| Recovery tooling | ✅ Auto-pause + 6 recovery actions |
| Replay integrity | ✅ Cooperative signals preserve execution sequencing |
| UI stability | ✅ No regressions, scoped CSS, clean event bus |
| Multi-worker readiness | ⚠️ In-memory signals need Redis for full multi-worker support |
| Confirmation dialogs | ⚠️ Cancel action lacks confirmation step |

**Beta Readiness: QUALIFIED** — suitable for single-worker production deployments. Multi-worker Redis signal propagation is the primary remaining concern for horizontal scaling.
