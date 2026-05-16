# Z11 — Canvas Execution System
**Aetherion AI · Phase Z11 · Visual Execution Architecture**
Status: DESIGN ONLY — No implementation. Future V3 target.

---

## Overview

The Canvas Execution System defines the future visual execution surface for
Aetherion AI — an interactive, zoomable canvas where users can observe, replay,
plan, and navigate agent execution as a living graph rather than a linear log.

---

## 1. Architecture Whiteboard

### Canvas Layer Stack (bottom → top)

```
Layer 0 — Background grid (static, CSS only)
Layer 1 — Dependency graph (read-only edges + nodes)
Layer 2 — Execution path overlay (animated, live)
Layer 3 — Replay cursor (time-travel seek handle)
Layer 4 — Selection + drag handles (interactive)
Layer 5 — Annotation overlays (user comments, bookmarks)
Layer 6 — UI controls (zoom, pan, minimap)
```

### Rendering Engine Choice (future decision)
Options ranked by fit:
1. **react-flow** — best for node/edge graphs, React-native, good performance.
2. **D3.js** — maximum flexibility, steeper integration cost.
3. **Xyflow** — modern fork of react-flow, maintained.
4. **Canvas API (raw)** — lowest overhead, highest implementation cost.

**Recommendation:** react-flow or Xyflow behind a feature flag.
**Decision gate:** V3 planning sprint.

---

## 2. Execution Graph Canvas

### Node Types

| Node Type | Shape | Color | Interaction |
|---|---|---|---|
| Task root | Rounded rect | `#bc8cff` (accent) | Click → expand |
| Tool call | Circle | `#79c0ff` (blue) | Hover → tool details |
| File write | Diamond | `#3fb950` (green) | Click → diff viewer |
| LLM call | Hexagon | `#f59e0b` (amber) | Hover → token count |
| HITL pause | Triangle (warning) | `#f85149` (red) | Click → approve/reject |
| Subprocess | Rounded rect | `#484f58` (muted) | Hover → stdout |
| Completed | Any + checkmark | Dimmed | — |
| Failed | Any + ✕ | `#f85149` | Click → error detail |

### Edge Types

| Edge | Style | Meaning |
|---|---|---|
| Sequential | Solid arrow | A completes → B starts |
| Parallel | Dashed arrow | A and B run concurrently |
| Delegation | Dotted arrow | Agent A spawned Agent B |
| Data flow | Thick solid | Output of A is input to B |
| Dependency | Gray dotted | B requires A's file output |

### Node Interaction Contracts
- **Single click**: select node, highlight connected edges.
- **Double click**: expand node detail panel (tool args, stdout, error).
- **Right click**: context menu (replay from here, copy execution_id, bookmark).
- **Drag**: reposition node (layout override persisted to localStorage).

---

## 3. Visual Replay Map

### Time-Travel Controls

```
[◀◀ Start]  [◀ Step Back]  ━━━━━●━━━━━━━━━━━━  [Step Forward ▶]  [End ▶▶]
             00:00                              12:34
```

### Seek Mechanism (future integration with `ExecutionReplayEngine`)
- Slider maps to `execution_store.event_log` timestamps.
- Seeking calls `ExecutionReplayEngine.seek_state(execution_id, target_timestamp)`.
- Canvas nodes animate to reflect state at seek point.
- File writes highlight in Code tab when selected.
- Token cost counter reflects cumulative cost to seek point.

### Replay Modes
1. **Step mode** — advance one event at a time (keyboard: ← →).
2. **Play mode** — animate at 2× or 4× speed (keyboard: Space to pause).
3. **Jump mode** — click any node to jump to its timestamp.
4. **Live mode** — tail the live execution as it runs (default during active run).

---

## 4. Dependency Visualization

### Dependency Graph Layout
- Algorithms: Dagre (left-to-right) or ELK (complex graphs).
- Groups: cluster by agent role (Planner, Coding, Testing, etc.).
- Zoom: 10% – 400%, default 100%.
- Pan: click+drag on canvas background.

### Dependency Node Data (from bootstrap analysis)
```json
{
  "id": "flask",
  "type": "python_package",
  "version": "3.0.3",
  "required_by": ["web_app.py", "auth_system.py"],
  "status": "installed",
  "health": "ok"
}
```

### Edge Rendering
- Thick edge = many dependents.
- Red edge = version conflict.
- Dashed edge = optional / extras dependency.

---

## 5. Mission Flow Visualization

### Mission Phases (rendered as swimlanes)
```
┌─────────────────────────────────────────────────────────────────────┐
│ PLAN     │ ●─────────────────────────────────────────────────       │
│ EXECUTE  │           ●──────────────────────────────────────────●   │
│ OBSERVE  │                      ●───────────────────────────        │
│ FIX      │                               ●──────────────────────●   │
└─────────────────────────────────────────────────────────────────────┘
```

### Swimlane Data Source
- Phase transitions read from `EventTypes.TASK_STARTED`, `TASK_COMPLETED`, `TASK_FAILED`.
- Agent role changes read from `AgentRegistry` snapshot history.
- HITL pauses rendered as red gap bars in the swimlane.

---

## 6. Drag Execution Planning

### Future Capability: Visual Task Decomposition
Users can drag components onto the canvas before execution to plan tasks:

- **Drag a file** → creates a "file context" node.
- **Drag a prompt card** → creates a task node.
- **Connect nodes** → defines execution order.
- **Press Run** → submits the visual plan to the execution engine.

### Integration with ExecutionPlanner
The visual plan serializes to:
```json
{
  "task_graph": [
    {"id": "t1", "type": "prompt", "content": "Build Flask API"},
    {"id": "t2", "type": "file_context", "path": "requirements.txt"},
    {"id": "t3", "type": "tool_constraint", "tool": "write_file"}
  ],
  "edges": [
    {"from": "t2", "to": "t1", "type": "context"},
    {"from": "t1", "to": "t3", "type": "sequential"}
  ]
}
```
Submitted to `POST /api/execute/plan-graph`.

---

## 7. Mounting Zones

### Canvas Mount Point
```html
<!-- FUTURE-V3: Visual execution canvas mount -->
<div id="nx-canvas-mount" class="nx-execution-surface" style="display:none;">
  <!-- react-flow / Xyflow mounts here -->
</div>
```

### Tab Integration
New tab added to the execution tab bar: **"Canvas"** (hidden by feature flag
`FEATURE_CANVAS_V3=true`).

### Sidebar Integration
Canvas minimap docked to right inspector panel when canvas is active.

---

## 8. Event Integration Points

| Canvas Event | Aetherion Signal | Direction |
|---|---|---|
| Node selected | `nx:canvas:node_selected` | Canvas → App |
| Seek position changed | `nx:canvas:seek` | Canvas → App |
| Plan submitted | `nx:canvas:plan_submit` | Canvas → App |
| Execution tick | `runtime.event` SSE | App → Canvas |
| Coordination snapshot | `agent.coordination_update` SSE | App → Canvas |
| HITL pause | `hitl.approval_required` SSE | App → Canvas |

All events flow through the existing `NxBus` (`/static/js/nx-bus.js`).
No new event bus required.

---

## 9. Rendering Constraints

| Constraint | Limit | Reason |
|---|---|---|
| Max visible nodes | 500 | Browser layout perf |
| Max visible edges | 1000 | SVG render perf |
| Minimap refresh rate | 10 Hz | CPU budget |
| Live tick rate | 4 Hz | Avoids animation jank |
| Zoom range | 10% – 400% | Readability at extremes |
| Canvas background rerender | On viewport change only | No continuous redraw |

---

## 10. Performance Limits

- Canvas initialized lazily (only on tab activation).
- Off-screen nodes virtualized (react-flow built-in).
- Event batching: SSE events buffered at 250 ms and replayed in batch.
- Web Worker for layout algorithm (Dagre/ELK) to avoid main thread blocking.
- IndexedDB caching for replay event logs > 10,000 events.

---

## 11. Security Boundaries

- Canvas renders only execution metadata — never raw file contents inline.
- File contents loaded on demand (explicit user interaction) via authenticated API.
- No external CDN dependencies; canvas library bundled and integrity-checked.
- Drag-to-plan inputs sanitized through the existing governance layer before submission.
- Node tooltip data HTML-escaped; no XSS surface from execution payloads.
