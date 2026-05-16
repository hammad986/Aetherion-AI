# Z44_OPERATOR_GUIDANCE_ANALYSIS.md
## Phase Z44E — Operator Guidance System Analysis

---

### Guidance Design Principles

1. **Operational language only** — no AI hype, no marketing copy
2. **State-appropriate** — guidance changes with execution state
3. **Non-intrusive** — advisory is in the inspector, not a modal or popup
4. **Actionable** — each advisory points toward what to do, not just what's happening
5. **Dismissible by action** — the advisory disappears when the state resolves

---

### Advisory Message Inventory

| State | Advisory Message | Tone | Action implied |
|-------|-----------------|------|----------------|
| idle | (none — inspector is clean) | — | — |
| running | (none — mission strip handles this) | — | — |
| queued | "Task queued — execution will begin when resources are available." | Calm, informational | Wait |
| paused | "Execution paused. Resume when ready or inject a correction below." | Calm, directive | Resume or inject |
| hitl | "Agent is waiting for your input. Review the request and respond." | Urgent, clear | Respond to agent |
| recovery | "Recovery in progress. Monitoring for stabilization." | Calm, monitoring | Observe |
| stabilizing | "System is stabilizing after a disruption. No action required." | Reassuring | Wait |
| failed | "Execution ended with an error. Review logs and retry or adjust the task." | Factual, directive | Diagnose and retry |
| replay | "Replay mode active. Timeline is showing historical execution." | Informational | Observe timeline |

---

### Advisory Placement and Visibility

- **Position**: Top of the inspector right panel (`#nxRightBody`)
- **Display**: `flex` when active, `none` when idle/running (mission strip handles running)
- **Transition**: Background and border color shift smoothly with state changes (0.25s)
- **Height**: `auto` — wraps to content, no fixed height
- **Typography**: 11px body text — readable without being prominent

---

### Guidance Hierarchy

```
Level 1 — Immediate action required:
  HITL:    "Agent is waiting for your input…"
  FAILED:  "Execution ended with an error…"

Level 2 — Monitoring required:
  PAUSED:    "Execution paused…"
  RECOVERY:  "Recovery in progress…"
  QUEUED:    "Task queued…"

Level 3 — No action required:
  STABILIZING: "System is stabilizing…"
  REPLAY:      "Replay mode active…"
```

---

### Operator Confusion Prevention

**What confused operators before Z44:**
- The workspace showed no visual difference between idle and running
- "Error" state was only visible if the error card happened to be in view
- HITL waiting looked like normal running — only the left panel strip indicated it
- Queued tasks had no workspace-level signal

**What Z44 resolves:**
- All 9 states produce distinct visual signatures across 13 surfaces
- Advisory strip gives explicit textual guidance for all actionable states
- HITL state produces a distinct purple signature (not confused with blue running)
- Failed state produces red signature visible across topbar, panel, and statusbar
- The mission strip always shows what the last action was, even after failure

---

### Remaining Guidance Gaps

1. **Suggested actions**: Beyond the textual advisory, Z44 does not inject clickable "suggested action" buttons (e.g., a "Retry" button appearing in the advisory when `failed`). This would require wiring the advisory to the existing action buttons. Architecture is ready — `data-nx-advisory-state` attribute can be used to conditionally show buttons.
2. **Escalation summaries**: When multiple consecutive failures occur, there is no escalation signal (e.g., "3 consecutive failures — consider adjusting the task"). This would require tracking failure count in the JS state machine.
3. **Stability hints**: "This model often times out on long tasks — consider switching to a faster model" type hints require model performance data from the backend.
4. **Operator overload prevention**: The inspector can become dense during long runs (many sections visible). No progressive disclosure system (auto-collapse inactive sections) is implemented in Z44.

### Production Readiness Verdict

> **PASS** — Advisory system covers all 9 runtime states with appropriate, operational-language guidance. State-specific styling distinguishes advisory severity. Non-intrusive positioning in the inspector ensures guidance is available without demanding attention.
