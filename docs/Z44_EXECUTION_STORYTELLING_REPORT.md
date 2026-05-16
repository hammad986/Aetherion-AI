# Z44_EXECUTION_STORYTELLING_REPORT.md
## Phase Z44B — Execution Storytelling Layer Report

---

### Storytelling Design Philosophy

The operator should always know:
1. **What is happening** — mission strip (last meaningful action)
2. **Why it happened** — causal section from Z37
3. **What failed** — error card + failed state
4. **What recovered** — recovery state + mission strip
5. **What the runtime is currently trying** — mission strip narrates execution

**No AI hype language. No marketing copy. Operational text only.**

---

### Mission Strip Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ ● [last meaningful log line text…]            [STATE LABEL] │
└─────────────────────────────────────────────────────────────┘
  ↑                ↑                               ↑
state dot      narrative text                 state badge
(5px, pulsing   (10px mono,                   (9px, uppercase,
 when running)   truncated 120ch)              hidden when idle)
```

**Location**: Between `.nx-composer` and `.nx-tab-bar` in the center panel
**Height**: 24px fixed — no layout impact
**Font**: JetBrains Mono 10px — operational, not editorial

---

### Narrative Signal Filter

**Skipped (noise):**
- Empty/divider lines (`───`, `===`)
- Heartbeat/ping/poll/tick markers
- Lines with only `[Nms]` timing
- Debug-level lines

**Promoted (signal):**
- File operations: writing, creating, updating, modifying
- Execution: executing, running, calling
- Analysis: analyzing, inspecting, reading
- Planning: planning, deciding, routing
- Fix/patch: fixing, patching, correcting
- Validation: testing, validating, verifying
- Completion: completed, finished, done
- Error/recovery: error, failed, exception, recovered, retrying, fallback
- Stage/phase progression: stage, step, phase
- Build operations: installing, building, compiling

**Log extraction**: `MutationObserver` on `#logArea` — captures `.log-line` elements as they appear. Strips level prefix `[LEVEL]` from line text before displaying.

---

### Storytelling Feed (Inspector)

The storytelling feed in the inspector shows the last **5 meaningful log events** with timestamps, newest first:

```
EXECUTION NARRATIVE
─────────────────────────────────────────────────────
Creating /src/auth/token.py                  12s ago
Analyzing import graph in /src/             45s ago
Planning route: /api/login → auth module    2m ago
Installed flask-jwt-extended                4m ago
```

- Max 5 entries (circular buffer)
- Timestamps auto-refresh every 30 seconds
- "No activity yet." when feed is empty

---

### State-Specific Narrative Behavior

| State | Mission strip | Advisory | Story feed |
|-------|--------------|----------|-----------|
| idle | Faded (opacity 0.4) | Hidden | Preserved |
| running | Active, last action | Hidden | Accumulating |
| queued | "Ready." | Shown (queued msg) | Preserved |
| paused | Amber text | Shown (pause msg) | Preserved |
| hitl | Purple text | Shown (input needed) | Preserved |
| recovery | Pulsing dot | Shown (recovery msg) | Accumulating |
| stabilizing | Active | Shown (stable msg) | Accumulating |
| failed | Red text | Shown (error msg) | Preserved (last run) |
| replay | Cyan border | Shown (replay msg) | Historical |

---

### Execution Causal Breadcrumbs

The Z37 causal section (mounted by `nx-z37-causal.js`) provides dependency trace when active. Z44 styles these sections as a proper cognition surface rather than a debug output.

The `z37-causal-section` elements now have:
- `border-left: 2px solid var(--z44-state-color)` — state-aware causal framing
- Structured `z37-dep-chain` layout
- Forensic typography (10px mono)

---

### Remaining Storytelling Gaps

1. **Why did this execute?**: The causal reasoning breadcrumbs depend on Z37's causal section being populated with data — Z44 provides the styling but not the data. Z37's data pipeline is separate.
2. **Recovery probability**: Would require backend ML inference data — not currently available in the status API.
3. **Runtime decision summaries**: The planning phase decisions are logged but not extracted into a dedicated "decision summary" surface — they appear in the story feed when they match signal patterns.
4. **Mission strip on log replay**: When older logs are loaded (`loadOlderLogs()`), they don't flow through the MutationObserver pathway and won't update the mission strip. Only live-appended log lines are captured.

### Production Readiness Verdict

> **PASS** — Mission strip provides live operational narrative with minimal visual footprint (24px). Story feed accumulates last 5 meaningful events in the inspector. Advisory system provides state-appropriate guidance. No AI marketing language anywhere.
