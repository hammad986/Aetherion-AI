# JS RUNTIME DEPENDENCY GRAPH
# Phase Z5 — Dead JS Detection | Generated: 2026-05-15

## DEPENDENCY RESOLUTION METHOD
Audit based on: index.html `<script>` load order, NxBus event subscriptions,
SSE consumption patterns, and cross-module function references.

---

## CORE DEPENDENCY CHAIN

```
nx-bus.js                        ← Foundation: all nx-* modules depend on this
  └── nx-signals.js              ← Reactive signal layer; consumed by orchestrator
  └── nx-state.js                ← Client state store; consumed by surface modules
  └── nx-sse-runtime.js          ← SSE connection/reconnect; consumed by runtime.js
        └── runtime.js           ← Core 205KB execution runtime; top-level SSE consumer
              └── dashboard.js   ← 151KB; primary UI logic; consumes runtime events
              └── session.js     ← Session lifecycle; consumed by workspace.js
              └── boot.js        ← App boot sequence; initializes all subsystems
```

---

## MODULE STATUS TABLE

| File | Size | Status | SSE Consumer | NxBus Listener | DOM Mutations | Risk |
|------|------|--------|-------------|----------------|---------------|------|
| `nx-bus.js` | 9.8KB | ✅ ACTIVE-CORE | No | Foundation | No | Critical |
| `nx-sse-runtime.js` | 17.7KB | ✅ ACTIVE-CORE | **YES** | Yes | Yes | Critical |
| `runtime.js` | 205KB | ✅ ACTIVE-CORE | **YES** | Yes | Yes | Critical |
| `dashboard.js` | 151KB | ✅ ACTIVE-CORE | Indirect | Yes | Yes | Critical |
| `boot.js` | 20.3KB | ✅ ACTIVE | No | Yes | Yes | High |
| `session.js` | 36.4KB | ✅ ACTIVE | Indirect | Yes | Yes | High |
| `nx-orchestrator.js` | 16.7KB | ✅ ACTIVE | No | Yes | Yes | High |
| `nx-signals.js` | 17KB | ✅ ACTIVE | No | Yes | No | High |
| `nx-state.js` | 8.1KB | ✅ ACTIVE | No | Yes | No | High |
| `nx-workspace-runtime.js` | 20.3KB | ✅ ACTIVE | No | Yes | Yes | High |
| `nx-monaco.js` | 20.9KB | ✅ ACTIVE | No | Yes | Yes | High |
| `nx-xterm.js` | 15.7KB | ✅ ACTIVE | No | Yes | Yes | High |
| `nx-hitl-bridge.js` | 5.9KB | ✅ ACTIVE | **YES** | Yes | No | High |
| `nx-hitl-panel.js` | 7.4KB | ✅ ACTIVE | No | Yes | Yes | High |
| `nx-chunker.js` | 10.7KB | ✅ ACTIVE | No | Yes | Yes | Medium |
| `nx-trust-intel.js` | 19.5KB | ✅ ACTIVE | No | Yes | Yes | Medium |
| `nx-trust-ui.js` | 32.5KB | ✅ ACTIVE | No | Yes | Yes | Medium |
| `nx-mission.js` | 18.3KB | ✅ ACTIVE | No | Yes | Yes | Medium |
| `nx-surface-fusion.js` | 17.1KB | ✅ ACTIVE | No | Yes | Yes | Medium |
| `nx-dag.js` | 26.1KB | ✅ ACTIVE | No | Yes | Yes | Medium |
| `nx-diagnostics.js` | 10.8KB | ✅ ACTIVE | No | Yes | Yes | Medium |
| `nx-observability.js` | 10.8KB | ✅ ACTIVE | No | Yes | Yes | Medium |
| `nx-devtools.js` | 15.6KB | ✅ ACTIVE | No | Yes | Yes | Low |
| `nx-clarity.js` | 15.2KB | ✅ ACTIVE | No | Yes | Yes | Low |
| `nx-hardening.js` | 14.1KB | ✅ ACTIVE | No | No | Yes | Low |
| `nx-activity.js` | 23.8KB | ✅ ACTIVE | No | Yes | Yes | Medium |
| `nx-session-cleanup.js` | 11.5KB | ✅ ACTIVE | No | Yes | No | Medium |
| `nx-shim.js` | 9.7KB | ✅ ACTIVE | No | No | Yes | Low |
| `nx-polish.js` | 15.9KB | ✅ ACTIVE | No | No | Yes | Low |
| `nx-intelligence.js` | 8.8KB | ✅ ACTIVE | No | Yes | Yes | Low |
| `nx-onboard.js` | 7.6KB | ✅ ACTIVE | No | No | Yes | Low |
| `nx-agi-surface.js` | 28.8KB | ✅ ACTIVE | No | Yes | Yes | Medium |
| `nx-timeline.js` | 4.4KB | ✅ ACTIVE | No | Yes | Yes | Low |
| `workspace.js` | 44.6KB | ✅ ACTIVE | No | Yes | Yes | High |
| `ui.js` | 51.6KB | ✅ ACTIVE | No | No | Yes | High |
| `activity.js` | 63KB | ✅ ACTIVE | No | Yes | Yes | Medium |
| `history.js` | 59.9KB | ✅ ACTIVE | No | Yes | Yes | Medium |
| `stability.js` | 20.3KB | ✅ ACTIVE | No | Yes | Yes | Medium |
| `support.js` | 15.9KB | ✅ ACTIVE | No | No | Yes | Low |
| `feedback.js` | 3.5KB | ✅ ACTIVE | No | No | Yes | Low |
| `execution_graph.js` | 17.3KB | ✅ ACTIVE | No | Yes | Yes | Medium |
| `evolution.js` | 13.8KB | ✅ ACTIVE | No | Yes | Yes | Low |
| `immersive.js` | 11.1KB | ✅ ACTIVE | No | No | Yes | Low |
| `agent_mem.js` | 9.1KB | ✅ ACTIVE | No | Yes | Yes | Low |
| `ux_trust.js` | 30.4KB | ✅ ACTIVE | No | Yes | Yes | Medium |

---

## SSE CONSUMERS (Requires Strict Ordering)

These modules directly consume SSE events. Load order is critical.

1. `nx-sse-runtime.js` — establishes EventSource; manages reconnect with exponential backoff
2. `nx-hitl-bridge.js` — listens for `hitl_pause` / `hitl_resume` events
3. `runtime.js` — primary event fan-out; distributes to all other modules via NxBus

> **VERDICT**: No duplicate SSE consumers detected. All three serve distinct roles.
> No consolidation needed. Ordering must be preserved.

---

## NXBUS EVENT HANDLER AUDIT

Potential duplicate handler risk areas (flagged for monitoring, not deletion):

| Event Name | Registered In | Count | Risk |
|-----------|--------------|-------|------|
| `nx:session:start` | runtime.js, session.js | 2 | LOW — different scopes |
| `nx:task:update` | dashboard.js, nx-mission.js | 2 | LOW — different scopes |
| `nx:agent:state` | nx-trust-intel.js, nx-orchestrator.js | 2 | LOW — different scopes |
| `nx:sse:reconnect` | nx-sse-runtime.js, nx-hardening.js | 2 | LOW — hardening is observer only |

> **VERDICT**: No confirmed duplicate handlers causing double-execution.
> All multi-registrations serve distinct consumers (UI vs. logic layers).

---

## PROVABLY UNUSED MODULES

**None identified.** All 45 JS files have at least one confirmed live reference
in either `index.html` script tags, NxBus event subscriptions in `runtime.js`,
or direct DOM manipulation tied to active HTML elements.

---

## RECOMMENDATIONS

1. **No JS deletions warranted** at this time — all modules are active.
2. **Monitor** the 4 dual-registered NxBus events above for future race conditions.
3. **Technical debt**: `runtime.js` (205KB) and `dashboard.js` (151KB) are candidates
   for future code-splitting — but NOT in this phase (architecture locked).
4. **Load order is binding**: `nx-bus.js` → `nx-sse-runtime.js` → `runtime.js`
   must remain strictly ordered in index.html.
