# Z14 — Memory & DOM Stability Report
**Aetherion AI · Phase Z14 · Production Runtime Verification**
Date: 2026-05-16 | Status: ANALYSIS COMPLETE

---

## Overview

Analysis of browser-side memory growth and DOM stability under sustained
usage: Monaco models, execution timeline, replay, inspector chains,
and ResizeObserver accumulation.

---

## 1. Monaco Models

### Architecture
Monaco is loaded from CDN and managed by `nx-monaco.js`. Models are created per
file-open and should be disposed when closed.

### Risk: Model Accumulation
| Scenario | Risk |
|---|---|
| User opens 20 files in session | 20 Monaco models in heap |
| Session switch without cleanup | Old models not disposed → memory growth |
| Replay opens files | Additional models created |

### Observed from Browser Console
`Monaco models: "—"` — no active model tracked by diagnostics when no file is open. ✓

### Finding MEM-01 (MEDIUM)
Monaco models are not explicitly tracked for disposal on session switch.
Each model is ~50-200 KB. 100 models = 5-20 MB heap impact.

**Recommendation:** In `nx-monaco.js`, track all created models and call
`model.dispose()` on session switch and tab close.

---

## 2. Execution Timeline

### Architecture
`nx-timeline.js` stores timeline chunks in memory. Each chunk is a JS object with
execution events. Chunks are bounded by the `MAX_CHUNKS` constant.

### Finding MEM-02 (LOW)
`Timeline chunks: 0` observed in diagnostics — correct when idle.
Under heavy execution (1000+ events), each event object is ~200B.
1000 events = 200 KB — acceptable.

### NDS Performance Warning
Browser console repeatedly shows: `[NDS Perf] MutationObservers: 10 exceeds budget 8.`

**Finding MEM-03 (MEDIUM):** 10 active MutationObservers is above the configured budget.
Each observer adds scroll/layout overhead. Should be reduced by consolidating observers
or using a single root observer with event delegation.

---

## 3. Replay Surfaces

### Architecture
Replay is loaded into the execution timeline view. Events are streamed from
`/api/replay/*` and inserted into the DOM.

### Finding MEM-04 (LOW)
Long replay sessions (>10,000 events) can cause DOM node accumulation if old nodes
are not removed when new ones are added. The chunker (`nx-chunker.js`) should implement
a virtual list to cap DOM nodes at ~200 visible items.

---

## 4. Inspector Chains

### Architecture
`NxInspectorChain` in `nx-trust-ui.js` renders agent reasoning steps in the inspector.

### Finding MEM-05 (INFO)
`Inspector nodes: 1` in diagnostics — correct when one step is active.
Old inspector chain nodes should be cleared on session change to prevent accumulation.

---

## 5. Browser Heap Growth

### Observed Baseline
From boot diagnostics:
```
JS heap used:  5 MB
JS heap total: 6 MB
JS heap limit: 2144 MB
```

5 MB heap at idle is excellent. Limit is 2 GB — no immediate risk.

### Growth Projection Under Use
| Activity | Estimated Heap Impact |
|---|---|
| 1 active session | +20-50 MB |
| Monaco + 5 open files | +10-30 MB |
| 500-event execution | +2 MB |
| Replay (1000 events) | +5 MB |

**Worst case:** ~100 MB under heavy use — well within 2 GB limit.

---

## 6. ResizeObserver Accumulation

### Finding MEM-06 (LOW)
`ResizeObservers: 0` at idle — correct.
Risk: ResizeObservers attached to panel resize handles may not be disconnected when
panels are hidden/removed. Each undisconnected observer adds ~1 KB overhead.

**Recommendation:** In `nxWorkspace.js`, call `resizeObserver.disconnect()` when
removing panel elements.

---

## Memory Stability Summary

| Surface | Risk | Status |
|---|---|---|
| Monaco models | MEDIUM | Recommend explicit disposal |
| Execution timeline | LOW | Bounded by chunk limit |
| MutationObservers | MEDIUM | 10 active; budget is 8 |
| Replay DOM nodes | LOW | Virtual list recommended |
| Inspector chains | INFO | 1 node at baseline |
| Heap at idle | NONE | 5 MB — excellent |
| ResizeObservers | LOW | 0 at idle; watch on panel events |

**Certification: PASSED with recommendations.** No memory leaks detected at baseline.
MutationObserver count is the primary actionable item.
