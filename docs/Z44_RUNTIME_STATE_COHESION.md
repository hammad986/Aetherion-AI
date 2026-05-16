# Z44_RUNTIME_STATE_COHESION.md
## Phase Z44A — Unified Runtime State Cohesion Report

---

### State Machine Architecture

```
                      ┌─────────────────────────────────┐
                      │   MutationObserver Network       │
                      │  #runBtn .is-running             │
                      │  #stStatus textContent           │
                      │  #nxHitlStrip style.display      │
                      │  #nxErrorCard style.display      │
                      └────────────┬────────────────────┘
                                   │
                             classifyState()
                                   │
                         ┌─────────┴──────────┐
                         │  body[data-nx-state]│
                         └─────────┬──────────┘
                                   │ drives
         ┌─────────────────────────┼─────────────────────────┐
         │                         │                         │
   CSS selectors            --z44-state-color           Advisory
  (topbar, panels,       (CSS custom property           (JS text
   statusbar, dots)       injected by JS)               injection)
```

**Primary authority**: `body[data-nx-state]` — set by `nx-z44-runtime.js`
**Backward compatibility**: `body[data-nx-exec]` — maintained from Z43 as an alias

---

### Nine-State Inventory

| State | Trigger | Accent | When |
|-------|---------|--------|------|
| `idle` | No session active | #3A3D48 grey | Default state |
| `running` | `#runBtn.is-running` | #0079F2 blue | AI executing task |
| `queued` | `#stStatus` text = "Queued" | #525566 mid-grey | Task waiting for slot |
| `paused` | `#stStatus` includes "paus" | #C28A00 amber | Manual pause |
| `hitl` | `is-running` + `#nxHitlStrip` visible | #7C5DB7 soft purple | Waiting for human input |
| `recovery` | `is-running` + status includes "recover" | #D97706 warm amber | Auto-recovering from error |
| `stabilizing` | status includes "stabiliz" | #16A34A green | Post-recovery stabilization |
| `failed` | `#nxErrorCard` visible or status = "Error" | #C0392B red | Execution ended with error |
| `replay` | status includes "replay" | #0891B2 cyan | Historical execution replay |

---

### Surfaces That React to State (13 signals)

| Surface | CSS Target | What changes |
|---------|-----------|-------------|
| Topbar bottom border | `.nx-shell-topbar::after` | color + opacity |
| Run dot | `.nx-run-dot` | color + pulse animation |
| Run button | `.nx-topbar-run-btn` | bg tint + border + text |
| Z33 runtime pulse pill | `.z33-runtime-pulse` | border + bg + text color |
| Z33 pulse dot | `.z33-pulse-dot` | color |
| Center panel left border | `.nx-panel.nx-center` | border-left color |
| Inspector status badge | `.nx-status-badge` | color + border + bg |
| Statusbar model dot | `.nx-model-dot` | color + pulse |
| P9 status dot | `.p9-status-dot` | color (from --z44-state-color) |
| DAG panel | `.z30-dag-panel` | border-left tint |
| Timeline dock | `.z33-timeline-dock` | border-top tint |
| Advisory strip | `#nx-advisory` | shown/hidden + bg color |
| Mission strip | `#nx-mission-strip` | state label + color |

---

### State Transition Priority

When multiple signals conflict (e.g., `is-running` = true but `#nxErrorCard` visible):

```javascript
Priority order:
1. HITL      — if is-running AND hitl strip visible
2. RECOVERY  — if is-running AND status includes "recover"
3. STABILIZING — if is-running AND status includes "stabiliz"
4. PAUSED    — if is-running AND status includes "paus"
5. RUNNING   — if is-running (default)
6. REPLAY    — if not running AND status includes "replay"
7. QUEUED    — if not running AND status includes "queue"
8. FAILED    — if not running AND (error card OR status includes "error/fail")
9. IDLE      — default fallback
```

---

### Detection Robustness

| Signal | Method | Latency |
|--------|--------|---------|
| `is-running` class | MutationObserver | < 1 frame |
| Status pill text | MutationObserver on childList+characterData | < 1 frame |
| HITL strip visibility | MutationObserver on style attribute | < 1 frame |
| Error card visibility | MutationObserver on style attribute | < 1 frame |
| Log stream events | MutationObserver on #logArea childList | < 1 frame |

**All observers are zero-cost when idle** — MutationObserver fires only on change.

---

### Remaining State Cohesion Gaps

1. **`paused` signal**: The backend sets `is-running = true` during HITL waits but the status text may not explicitly say "paused". The HITL strip visibility is the more reliable signal and is given higher priority.
2. **`stabilizing` direct trigger**: Currently requires the status text to contain "stabiliz" — if the backend uses a different word, this state won't fire. Backend normalization of status values would resolve this.
3. **`replay` signal**: Requires statusbar text containing "replay" — the Z33 timeline replay system may not write this text. A dedicated JS event (`nxZ44.setState('replay')`) allows external triggering.
4. **`queued` sub-state during running**: A queued task that starts running immediately doesn't visually show the queued state (it jumps to `running`). This is correct behavior — not a gap.

### Production Readiness Verdict

> **PASS** — 9-state machine implemented with MutationObserver-based detection across all observable signals. 13 UI surfaces react coherently. Zero polling cost. Backward compatible with Z43 `data-nx-exec` system.
