# Z35 Mission Presence Audit

**Phase:** Z35A — Mission-Centered Workspace  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — minimal, contextual, mission-driven

---

## What Was Built

A persistent mission bar (`#z35MissionBar`) sits at the top of the live workspace. It shows four contextual signals without clutter:

| Signal | Content | Notes |
|--------|---------|-------|
| MISSION | Truncated objective text (72 chars max) | Extracted from first `Task:` / `Goal:` log line |
| PHASE | Current execution phase | One of: idle / planning / executing / validating / recovering / escalating / replay |
| CONF | Semantic confidence % | From Z32 `z32.confidence.update` events |
| PRESSURE | 3-pixel micro-bar | Composite of token load, retry count, error count, confidence drop |

A 2px left-edge accent on the mission bar changes color with the current phase — blue for planning, green for executing, amber for recovering, red for escalating. This is the primary "at a glance" phase signal. It is subtle, not dominant.

---

## Heat Mapping

DAG nodes are classified by heat score:

| Class | Score | Visual |
|-------|-------|--------|
| `z35-heat-low`      | 0.01–0.24 | Faint amber inset ring |
| `z35-heat-med`      | 0.25–0.49 | Visible amber inset ring |
| `z35-heat-high`     | 0.50–0.74 | Red inset ring |
| `z35-heat-critical` | 0.75–1.00 | Red inset ring + background tint |

Heat accumulates from: retries (+0.15), errors (+0.35), confidence drops below 45% (+0.20). Successful node completion cools heat by 0.10. Heat persists across replans within a session; it is cleared on new session start.

---

## Remaining Execution Clarity Gaps

1. **Objective extraction is heuristic** — looks for `Task:` / `Goal:` / `Objective:` prefix in log rows. If the platform never emits such a prefix, the mission bar shows `—`. No backend API backing objective state.
2. **Phase detection is log-based** — regex pattern matching on `agent.log_row` text. Rapid phase transitions (planning → executing in one token) may briefly show the wrong phase.
3. **Heat scores are session-scoped in memory** — not persisted. Historical heat patterns are lost on reload. Z31 replay does not restore heat.
4. **Heat applied via CSS class injection on `[data-node-id]` elements** — requires Z30 DAG nodes to carry this attribute. If Z30 renders nodes differently, heat classes will have no visible target.

---

## Remaining Replay Readability Weaknesses

- Mission bar shows `replay` phase correctly, but the objective text during replay still shows the live session objective, not the replayed session objective.
- Heat map during replay is not reconstructed from historical events — it reflects only the current session's live data.

---

## Remaining Operator Overload Risks

- The mission bar adds 28px of height to the live tab. On small viewports (< 500px height) this reduces available DAG surface area noticeably.
- PRESSURE micro-bar at 3px height and 36px width is very small. Operators who need precise pressure readings should use Z32's dedicated pressure bar instead.
- Escalation pulse animation runs continuously while escalated. Acceptable at 2s interval; it represents a real state requiring operator action.

---

## Honest Production UX Verdict

The mission bar works as intended. It is minimal — 28px, two lines of context, no popups, no tooltips on hover unless specifically needed. The phase accent communicates operational state without requiring the operator to look at logs. The heat map surfaces node instability without adding a separate panel. No regressions to existing surfaces.
