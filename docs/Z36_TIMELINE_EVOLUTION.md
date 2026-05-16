# Z36 Timeline Evolution

**Phase:** Z36B — Timeline Intelligence Evolution  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — pressure indicators and replay reconstruction live

---

## What Changed

The Z33 timeline dock was a log stream: events appeared chronologically with no semantic differentiation. Z36 overlays three layers of intelligence:

### 1. Pressure Indicators
Each timeline row is checked against `NodeRegistry` (heat score) and `PressureMemory` (historical pressure score) for its associated node. CSS border and background classes are applied:

| Class | Threshold | Visual |
|-------|-----------|--------|
| `z36-tl-pressure-med`      | heat ≥ 0.25 | Faint amber left border |
| `z36-tl-pressure-high`     | heat ≥ 0.50 | Amber-red left border |
| `z36-tl-pressure-critical` | heat ≥ 0.75 | Strong red left border + background tint |

Additionally:
- `z36-tl-has-retries` — node had ≥1 retry
- `z36-tl-has-errors`  — node had ≥1 error
- `z36-tl-conf-low`    — confidence < 45%

### 2. Semantic Group Markers
Event type is detected from row text content:
- `⟳` prefix for recovery/replan events (`z36-tl-recovery-chain`)
- `↺` prefix for retry events (`z36-tl-retry-chain`)
- Reduced opacity (0.65) for completed events (`z36-tl-completed`)
- Italic style for low-confidence events (`z36-tl-conf-low`)

### 3. Replay Reconstruction on Hover
When the operator hovers over a timeline row with a `data-z36-node-id` attribute:
1. The forensic inspector soft-opens for that node (no cursor seek)
2. An execution pulse fires on the corresponding DAG node
3. The Z34 inspector body refreshes with the node's decision chain and recovery intelligence

This is a preview-on-hover, not a full replay seek. The operator can scan the timeline visually and see forensic context on any row without committing to a replay position.

---

## Remaining Replay Readability Weaknesses

1. **Row enrichment is applied once per row** — rows marked `_z36enriched = true` are never re-enriched. If Z33 re-renders the same rows after a replan, the new rows will be enriched but old rows (now stale DOM nodes) will have outdated pressure classes.

2. **Node id extraction from row text** is heuristic — regex matching `[plan|code|debug|tool|done|review|test]` in brackets. If Z33 timeline rows don't include these strings, `_extractNodeIdFromRow` returns null and rows get no pressure enrichment.

3. **Z34 timeline events array** (`_z34.getTimelineEvents()`) is used as a fallback for node id extraction by index. This assumes timeline row DOM order matches the Z34 events array order — true only if Z33 appends rows sequentially without reordering.

4. **Recovery chain and retry chain detection** uses text content — these markers may appear on rows that don't actually represent retry/recovery events if the log text happens to contain those words.

---

## Remaining Operator Overload Risks

- The `⟳` and `↺` prefixes are prepended via CSS `::before` on the row element. If Z33's row layout uses a fixed left padding that doesn't account for `::before` content, the prefix may overlap the first character of the log text.
- Multiple timeline rows for the same high-heat node will all have red borders — a long retry storm may produce 4–8 consecutive red-bordered rows, which is dense but accurate.
