# Z30 Operator Graph Usability Report

**Phase:** Z30E — UI Discipline + Forensic Audit  
**Status:** AUDITED  
**Date:** 2026-05-16

---

## Operator Cognitive Load Assessment

### Information Hierarchy (Live Tab)

The Live tab now presents information in the following priority order:

1. **Pipeline bar** (top) — High-level phase progress: Planning → Coding → Debugging → Done.
2. **Instability alert** — Only visible when a critical event occurs. Dismissible. No persistent noise.
3. **Health bar** — Compact severity badge + heatbars for retries, errors. Always visible but small.
4. **Replay controls** — Only visible when replay history exists. Hidden during fresh sessions.
5. **Execution DAG** — Collapsible. Default height 220px. Operator can collapse to 32px header.
6. **Code + Terminal streams** — Existing Phase 33 panes. Unchanged in behaviour.
7. **Status bar** — Lightweight counters. Stage, lines, code chunks, commands.

**Cognitive overload risk: LOW.** Most panels are collapsed or hidden by default until relevant.

---

## Graph Readability Assessment

| Criterion | Score | Notes |
|-----------|-------|-------|
| Node label clarity | Good | Labels truncated at 22 chars with ellipsis |
| State color discrimination | Good | 6 distinct colors, not relying on color alone (dot + border) |
| Edge routing | Adequate | Cubic bezier curves. May overlap in dense parallel layouts |
| Retry badge visibility | Good | Yellow circle with ×N count at corner |
| Confidence badge | Good | Circle badge green/yellow/red at node top-right |
| Running pulse animation | Good | Subtle SVG animate — not distracting |
| Critical path marker | Good | Purple left-edge glow on critical nodes |

---

## Node Density

- **Low density (≤8 nodes):** Excellent readability. Nodes well-spaced.
- **Medium density (9–20 nodes):** Readable. Some edge crossing possible.
- **High density (>20 nodes):** Zoom/pan required. Layout may need scroll.

**Operator action at high density:** Wheel zoom out to see full graph. Pan to inspect individual nodes.

---

## Replay Navigation Usability

- **Scrubber:** Range input provides continuous scrubbing. Works for keyboard navigation (arrow keys).
- **Step buttons:** ‹ / › for precise frame stepping.
- **Live button:** Clearly labeled "⏹ Live" to exit replay mode.
- **Mode indicator:** "REPLAY" / "LIVE" label in replay bar for orientation.
- **Export:** Single-click JSON export for sharing with teammates.

**Risk:** Operator may not notice scrubber is available if replay bar is hidden. Mitigated by auto-showing when history exists.

---

## DAG ↔ Timeline Sync

- Clicking a DAG node opens the Node Intelligence panel with logs from that phase.
- `data-timeline-phase` attribute on timeline rows enables reverse sync (timeline → DAG highlight + intel panel open).
- Sync indicator dot pulses blue briefly to confirm sync action.

---

## Accessibility Notes

- All interactive elements have `title` attributes for tooltip discovery.
- Alert banner has `role="alert"` for screen reader announcement.
- Replay toolbar has `role="toolbar"` and `aria-label`.
- Intel panel has `role="complementary"` and `aria-label`.
- DAG SVG is pointer-only (not keyboard navigable). This is a known gap.

---

## Remaining Operational Blind Spots

1. **DAG keyboard navigation**: SVG nodes are only click-accessible. Arrow key graph traversal not implemented.
2. **Node search**: No search/filter for large graphs. Future: search by node label or state.
3. **Multi-session comparison**: Only the active session is shown. Historical session comparison requires separate load.
4. **Escalation path labeling**: Escalation edges are visually identical to retry edges. Dedicated color/label needed.

---

## Final Usability Verdict

**OPERATIONALLY CLEAR for single-operator live sessions.** The information hierarchy is disciplined, with progressive disclosure preventing overload. The main gap is keyboard accessibility for the DAG surface.
