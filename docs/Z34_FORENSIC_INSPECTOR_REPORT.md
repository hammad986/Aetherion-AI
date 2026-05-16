# Z34 Forensic Inspector Report

**Phase:** Z34C — Forensic Inspector Evolution  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — reasoning surface complete

---

## What Changed

The Z30 intel panel showed a flat list of metrics for a selected node. The Z34 forensic inspector replaces this with a structured reasoning surface that reconstructs *why* execution changed paths, not just what happened.

---

## Inspector Surface Components

### 1. Timeline Position Context
Shows the operator exactly where in the execution replay the selected node sits (`N / Total`). Clicking a node from the DAG during live execution shows the most recent event index; clicking during replay shows the reconstructed position.

### 2. Core Metrics Grid (2-column)
State · Duration · Retries (highlighted red if > 2) · Confidence · Provider · Tokens

### 3. Confidence Drift Strip
A miniature bar chart showing confidence history for the node across up to 20 data points. Green = high (≥75%), amber = medium (45–74%), red = low (< 45%). Only renders if ≥2 data points exist.

### 4. Recovery Narrative (Z34C.4)
Four-step structured narrative:
- **Before failure** — last known good log line
- **Failure** — error message extracted from node state
- **Replan** — adaptive replan action (from Z32C events)
- **Recovery** — recovery outcome message

Only steps with known data are rendered; empty narratives are suppressed.

### 5. Replan History
Last 5 replan outcomes for the node sourced from `S.recoveryMap`, showing trigger → success/failure.

### 6. Dependency Lineage
All nodes that preceded the selected node in execution order, rendered as a breadcrumb chain. Derived from `S.timelineEvents` order. Final node highlighted in blue.

### 7. Retry History
Last 10 retry log lines with timestamp, numbered sequentially.

---

## Remaining Operator Confusion Risks

- The inspector panel slides over the right edge of the DAG surface at 260px width. On very narrow live tab viewports this may obscure DAG nodes near the right edge. Recommend adding a CSS media-query guard at `< 600px`.
- The recovery narrative only builds from flags set during the *current* session. Historical sessions loaded from Z31 will show an empty narrative because `S.nodeIndex` is session-scoped.
- "Dependency lineage" reflects event ingestion order, not the actual DAG edge topology. On parallelized execution graphs this will be inaccurate.

---

## Remaining Forensic Blind Spots

- Semantic pressure snapshots (Z32E) are not included in the inspector — only semantic confidence. Full pressure data available via Z32 panel.
- No inline log stream in the Z34 inspector (that remains in Z30 intel panel). The Z34 inspector is complementary, not a replacement.
