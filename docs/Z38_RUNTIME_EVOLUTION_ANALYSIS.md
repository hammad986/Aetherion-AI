# Z38 Runtime Evolution Analysis

**Phase:** Z38C — Execution Evolution Tracking  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — health trend sparkline, evolution snapshots, pattern panel live

---

## Evolution Tracking

Every 15 seconds during active execution (rate-limited by `S.lastEvolution` check), the frontend posts a snapshot to `POST /api/z38/evolution`:

```json
{
  "session_id":       "...",
  "avg_heat":         0.32,
  "total_retries":    3,
  "total_errors":     1,
  "total_recoveries": 2,
  "risk_level":       "ELEVATED",
  "node_count":       7
}
```

This creates a time-series of session health in `z38_evolution`. A single session generates ~4 snapshots per minute — approximately 240 rows per hour. The table is bounded to 500 rows by GC.

---

## Evolution Panel

Displayed inside the forensic inspector, below the Z37 causal section:

**Header:** `RUNTIME EVOLUTION` + trend indicator (↑ rising / → stable / ↓ improving)

**Sparkline:** Last 10 evolution rows rendered as 5-character monospace bar blocks, color-coded by risk level (grey→amber→orange→red). Each bar shows heat intensity as filled/unfilled block characters.

**Summary line:** Global totals — `N retries · M recoveries · K sessions`

**Pattern section — CHRONIC INSTABILITY:** Top 3 historically unstable nodes with error and retry counts.

**Pattern section — RECOVERY STRATEGIES:** Top 3 recovery types by success rate, color-coded green (≥70%) / amber (40–70%) / red (<40%).

---

## Health Trend Signals

The trend direction is computed from the last 5 `avg_heat` values:
- `rising` if last > avg(prev4) + 0.08 — "the session is accumulating pressure"
- `falling` if last < avg(prev4) - 0.08 — "the session is stabilizing"
- `stable` otherwise

The trend label appears in the evolution panel header and drives its color: rising→red, falling→green, stable→grey.

---

## Remaining Runtime Evolution Limitations

1. **Sparkline is text-based** — uses Unicode block characters (█░) at 7px monospace. On systems without monospace fallback fonts, bars may render incorrectly. A canvas or SVG sparkline would be more reliable but adds complexity.

2. **Evolution snapshots fire on `agent.log_row`** — if a session produces no log rows (e.g., very fast tasks), no evolution snapshots are posted. The 15-second rate limit also means a single-task session may produce only 1–2 snapshots.

3. **Global evolution query** (`GET /api/z38/evolution` without `session_id`) mixes data from all sessions chronologically. For long-running systems with many sessions, the sparkline will show cross-session noise rather than a coherent single-session trend. A per-session filter is the correct default for the inspector.

4. **Pattern section refreshes on every `z36.node.focus` event** — if the operator rapidly hovers over nodes, multiple parallel `GET /api/z38/patterns` requests fire. These are read-only and idempotent but add network load. A 5-second debounce on panel refresh would reduce this.

5. **No "improving branches" visualization** — Z38C specifies tracking improving vs degrading branches over time. Current implementation shows aggregate instability counts but does not display per-branch trend direction. This would require branch-level evolution snapshots, not yet implemented.
