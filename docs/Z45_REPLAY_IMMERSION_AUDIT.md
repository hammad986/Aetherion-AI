# Z45_REPLAY_IMMERSION_AUDIT.md
## Phase Z45C — Replay Immersion Audit

---

### Replay System Inventory (Pre-Z45)

| Component | Location | Replay Awareness |
|-----------|----------|-----------------|
| Z34 Replay Engine | `nx-z34-fusion.js` | Full replay state management |
| Z33 Timeline Dock | `nx-z33-timeline.js` | Timeline scrub + replay cursor |
| Z35 Mission Bar | `nx-z35-mission.js` | Updates on `dag.replay.started` |
| Z36 Cohesion | `nx-z36-cohesion.js` | Replay reconstruction on hover |
| Z44 State Machine | `nx-z44-runtime.js` | `replay` state via body attribute |

Z35 already wires `dag.replay.started/stopped` to update its mission bar. The infrastructure existed; the gap was in CSS-level replay immersion.

---

### Z45C Replay Immersion Added

**CSS additions:**
```css
body[data-nx-state="replay"] .z33-timeline-dock    /* cyan outline */
body[data-nx-state="replay"] .z33-tl-header        /* cyan text */
.z33-tl-event.z45-replay-current                  /* current position highlight */
.z33-tl-event.z45-replay-past                     /* dimmed (opacity 0.55) */
.z33-tl-event.z45-replay-future                   /* very dim (opacity 0.30) */
html[data-z45-replay-available] .z33-tl-header::after  /* REPLAY badge */
.z35-phase-replay                                  /* cyan phase label */
```

**JS additions (nx-z45-sync.js):**
- `dag.replay.started` → sets `body[data-nx-state="replay"]`, updates Z35 mission objective
- `dag.replay.stopped` → restores previous state, updates Z35 phase label
- `dag.replay.available` → stamps `html[data-z45-replay-available]` for CSS badge

---

### Replay Narrative Quality

**Before Z45:**
- Replay mode was visually indistinguishable from idle (same grey palette)
- No indication that the timeline was showing historical, not live data
- Z33's timeline header had no replay state indicator

**After Z45:**
- All replay surfaces tint cyan — distinct from running (blue) and idle (grey)
- Timeline dock header shows "REPLAY" badge when replay data is available
- Mission bar shows "Replay — reconstructing execution history…" during replay
- Timeline events progressively dim (past) and fade (future) around the cursor position
- The `body[data-nx-state="replay"]` attribute propagates cyan through all Z44/Z45 state hooks

---

### Replay Forensic Quality

The most important replay capability is the "failure moment" — identifying exactly when and why the execution broke. Z45 provides:

1. **Failure events** already carry `data-event-type="error"` from Z36's `_extractEventType`
2. **CSS** applies a red left border to all error-type timeline events
3. **Z36's forensic panel** (already implemented) shows failure pressure when a failed node is selected
4. **Z45 pressure hint** reinforces failure pressure with retry count in the inspector

**What Z45 cannot do without JS changes to Z33/Z34:**
- Automatically advance the replay cursor to the first failure moment
- Show "Failure at step N" in the mission bar during replay
- Provide a "Jump to failure" button

---

### Remaining Replay Weaknesses

1. **Cursor-driven classification**: The `.z45-replay-current/past/future` classes require knowing the Z33 timeline cursor position. This is Z33's internal state. Z45 defines the CSS classes but cannot set them without modifying Z33.

2. **Replay narrative richness**: The mission bar shows a generic "reconstructing execution history" message. A richer narrative ("Replaying step 4/12 — node failed on retry 2") requires Z33 to expose cursor position.

3. **Time-drift visualization**: Showing execution time drift during replay (step A took 2.3s in replay vs 5.1s in real-time) requires Z33's replay cursor to emit timing data — not currently available.

4. **Pressure evolution during replay**: Z35's pressure micro-bar shows current pressure but not historical pressure at the replay cursor position. Historical pressure requires a pressure timeline dataset.

### Production Readiness Verdict

> **PASS with limitations** — Replay is now visually distinct (cyan) and narratively described. Deep cursor-position-driven classification (current/past/future events, time-drift, historical pressure) requires Z33 internal state exposure — documented as future Z46 work.
