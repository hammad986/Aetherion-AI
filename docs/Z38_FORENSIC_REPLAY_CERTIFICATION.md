# Z38 Forensic Replay Certification

**Phase:** Z38D — Persistent Forensic Replay  
**Date:** 2026-05-16  
**Verdict:** OPERATIONAL — replay hydration live, persistent replay identity via replayId, bounded compression via retention limits

---

## Replay Hydration

When Z34's `dag.replay.started` event fires, Z38 reads all nodeIds from `_z34.getTimelineEvents()` and calls `POST /api/z38/replay/hydrate` with the full node list. The backend queries all matching records from `z38_node_memory` and `z38_recovery_events` in a single request, returning:

```json
{
  "nodes": {
    "plan": { "node_id": "plan", "unstable_count": 2, "insight": "historically unstable...", ... },
    "code": { ... },
    ...
  },
  "hydrated": 4
}
```

Each returned record is passed to `_applyHydration()`, which:
1. Merges historical retries/errors into NodeRegistry (only if current session hasn't accumulated its own)
2. Blends historical heat at 50% weight (historical doesn't override live session heat)
3. Injects recovery history into Z37's `ExecutionMemory`
4. Applies historical presence CSS classes (`z38-hist-risky`, `z38-hist-stable`, etc.) to DAG elements

This means a replay of a past session (loaded via Z34's forensic replay) immediately shows which nodes were historically problematic — even before the replay cursor reaches those nodes.

---

## Persistent Replay Identity

Z37's `replayId` (`${nodeId}:${generation}`) provides within-session generation tracking. For cross-session persistence, `z38_node_memory` records include both `node_id` (stable) and `session_id`. The `_build_node_summary()` function aggregates all records for a node across all sessions — so a node's historical record is a cross-session aggregate by default.

To query a specific session's records, `GET /api/z38/memory?session_id=<sid>` filters by session.

---

## Long-Session Replay Compression

Bounded retention (50 rows per node, 30 recovery events per node) acts as implicit replay compression. The oldest records are pruned first, so the retained records always represent the most recent execution history. This preserves causal continuity (recent patterns) while bounding DB growth.

The `pressure_trace` stored in the last record is already bounded to 60 points (last 8 minutes of pressure history at 8s intervals). This is the primary long-session signal — it compresses an arbitrarily long session into a fixed-size pressure fingerprint.

---

## Remaining Replay Blind Spots

1. **Bulk hydration fires once on `dag.replay.started`** — if the timeline renders new events after the initial hydration call, those nodes are not automatically hydrated. A follow-up hydration on `z33.timeline.rendered` would close this gap.

2. **`_applyHydration` merges at 50% heat weight** — this blending factor was chosen conservatively to avoid historical data dominating live session state. For pure forensic replay (no active session), a 100% weight would be more appropriate. There is no mode switch between live and pure forensic.

3. **`z38_node_memory` records the node state at write time** — during replay, the replayed state is the live session state, not the historical state from the replayed session. True historical state reconstruction would require Z31's snapshot mechanism to store full NodeRegistry state per snapshot.

4. **Replay hydration is fire-and-forget** — if the server is unavailable during replay start, no hydration occurs and historical presence classes are never applied. The replay still works but loses historical context.
