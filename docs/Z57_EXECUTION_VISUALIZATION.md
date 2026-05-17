# Z57_EXECUTION_VISUALIZATION.md
Phase Z57C — Execution Visualization Maturity Audit
Date: 2026-05-17

## Objective
Improve the execution storytelling surfaces so that running sessions feel intelligent,
legible, and alive — without adding visual noise.

---

## Execution Surface Inventory

| Surface | Element | Location |
|---|---|---|
| Compact pipeline bar | `#nxLogsPipeline` `.nx-exec-pipeline` | Logs tab header |
| Stage dots | `.nx-exec-stage` `.nx-stage-dot` | Inside pipeline bar |
| Log lines | `#logArea .log-line` | Logs tab body |
| Error card | `.nx-error-card #nxErrorCard` | Logs tab (shown on error) |
| Z55 execution presence card | `.z55-exec-card` | Injected into Logs tab by z55.js |
| HITL strip | `#nxHitlStrip .nx-hitl-strip` | Left panel (hidden during idle) |
| Z50 exec feedback bar | `#z50ExecFeedback .z50-exec-feedback` | Injected by z50.js above activity bar |

---

## Pre-Z57 Deficiencies

| Surface | Problem |
|---|---|
| Pipeline bar | Stages had no active/done visual differentiation beyond CSS class injection |
| Stage dots | No animation on active stage |
| Log lines | No `border-left` color coding; all lines same opacity |
| Error card | Near-invisible: no background treatment, matched the void |
| HITL strip | No visual separation from the main left panel body |
| Exec feedback bar | Shown by default even with no execution → removed in Z57F |

---

## Z57C Improvements

### Pipeline Bar
- Compact padding: `7px 14px` (vs inline style)
- Background: `rgba(255,255,255,0.015)` — separated from log area but not intrusive
- Stage container: `border-radius: 4px` + colored background on `.active`

### Stage Dots and States
- `.nx-exec-stage.active` → `color: --z57-blue; background: rgba(56,139,253,0.08)`
- `.nx-exec-stage.done` → `color: --z57-green`
- `.nx-exec-stage.failed` → `color: --z57-red`
- Active pulse animation: `z57-stage-pulse` (opacity 1→0.35 at 50%, 1.2s infinite)
  — confirms something is actively happening without being distracting

### Log Lines
Color coding via class selectors:
- `.error / .err` → `--z57-red`, `border-left: 2px rgba(248,81,73,0.35)`, subtle `background: rgba(248,81,73,0.03)`
- `.warn` → `--z57-amber`, left border
- `.success / .ok / .done` → `--z57-green`
- `.info / .debug` → `rgba(255,255,255,0.40)` — quiet, not default weight
- `.ai / .thought` → `rgba(188,140,255,0.75)` italic — AI thinking lines are distinguishable

### Error Card
- `background: rgba(248,81,73,0.05)` — faint red wash
- `border: 1px solid rgba(248,81,73,0.18)` — visible but not alarming
- `border-radius: 7px; margin: 10px 14px` — properly spaced

### HITL Strip
- `background: rgba(210,153,34,0.06)` — amber tint signals attention required
- `border-bottom: 1px solid rgba(210,153,34,0.15)` — clear boundary
- Input field styled to match the amber warning palette

---

## Remaining Visualization Gaps

1. **No log-line timestamps** — Log lines don't show when each event occurred. Adding
   a `time` attribute-based micro-timestamp column would significantly improve the
   execution narrative. Deferred (requires log format changes in web_app.py).

2. **No file-write events in the Logs tab** — File activity (writes, reads) is tracked
   by the activity bar but not surfaced as log lines. A `[FILE] wrote src/app.py`
   entry class would complete the narrative.

3. **Live tab idle state** — The Live tab still shows a black void when idle.

4. **Z55 exec card: animation timing** — The `z55-pulse` runs at 1.1s; the new
   `z57-stage-pulse` runs at 1.2s. These are intentionally slightly different to
   avoid visual entrainment but could be unified to a single token in Z58.

---

## Beta Maturity Score — Execution Visualization

| Dimension | Score (1–10) | Notes |
|---|---|---|
| Pipeline stage clarity | 7/10 | Active/done/failed all visually distinct |
| Log readability | 7/10 | Color coding by severity works cleanly |
| Error state legibility | 7/10 | Red wash + border makes errors unmissable |
| Active execution feel | 6/10 | Pulse animation + stage color; no timestamp column |
| Completion state | 6/10 | `.done` on stage dot works; no completion summary card |
| **Overall** | **6.6/10** | Solid improvement from ~4.5 pre-Z57 |

---

## Files Modified
- `static/css/nx-z57.css` — Z57C section: pipeline bar, stage dots, log lines, error card, HITL
