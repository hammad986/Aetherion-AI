# Z24 — Long-Session Stability Certification

**Date:** 2026-05-16  
**Phase:** Z24 — Real-World Session Stress Validation  
**Result:** ✅ CERTIFIED

---

## 1. Stability Guarantees

| Guarantee | Mechanism | Threshold | Verified |
|-----------|-----------|-----------|---------|
| Log DOM bounded | Dual-layer ceiling | ≤ 1,500 rows | ✅ |
| Toast nodes bounded | MutationObserver + TTL | ≤ 5 alive | ✅ |
| SSE stale detection | EventSource patch | 45 s silence | ✅ |
| SSE storm detection | Sliding window counter | 5 reconnects / 60 s | ✅ |
| DOM node alarm | 30 s polling | warn 8k / crit 14k | ✅ |
| Heap trend detection | 30 s sampling, 5-sample window | monotonic growth | ✅ |
| Observer count tracking | MutationObserver patch | reported in HUD | ✅ |
| Session uptime tracking | `SESSION_UPTIME_START` | HUD display | ✅ |
| Focus restoration | All modal/palette close paths | always restored | ✅ |
| Modal listener cleanup | `_stack` with trapListener ref | per-close | ✅ |

---

## 2. New Z24 Hardening in `nx-runtime-hygiene.js`

Compared to the Z21 baseline, the following were added:

| Feature | Description |
|---------|-------------|
| Heap trend tracking | `sampleHeap()` every 30 s, `heapSamples[]` ring buffer of 20 |
| Heap delta display | `Δ+/-XMB` shown in perf HUD beside heap values |
| Monotonic growth warn | `console.warn` + `NxBus.emit('heapGrowthTrend')` on 5 consecutive upward samples |
| DOM node 30 s alarm | `checkDOMNodes()` polls document, alarms at warn/crit thresholds |
| SSE reconnect storm | Sliding window counter; storm detection at 5/60s threshold |
| Storm state tracking | `_state.sseStormActive` + `body[data-sse-storm]` |
| Storm resolution | Cleared on first successful SSE `open` event |
| Observer count patch | `MutationObserver` patched; `_state.observerCount` maintained |
| HUD: Observers row | `nxHudObs` shows live observer count, warns at >20 |
| HUD: Uptime row | `nxHudUptime` shows session age (h/m/s) |
| `_nxDiagSnapshot()` | Full JSON diagnostic snapshot for operator use |

---

## 3. Diagnostic Tool Reference

```javascript
// Toggle performance HUD
nxPerfHUD()              // toggle on/off
nxPerfHUD(true)          // force on
nxPerfHUD(false)         // force off

// Full diagnostic snapshot (JSON)
_nxDiagSnapshot()
// Returns:
// {
//   uptimeSec: 14400,
//   fps: 60,
//   logTrimCount: 3200,
//   sseConnected: true,
//   sseStaleSec: 2,
//   sseReconnects: 0,
//   sseStormActive: false,
//   observerCount: 10,
//   heapSamples: [{ts, usedMB}, ...],
//   domNodes: 5234,
//   logRows: 1498
// }

// Inspect raw state
window._nxPerfState

// Emergency timer eviction
_nxClearAllTimers()
```

---

## 4. Performance Budget — Z24 Compliance

| Metric | Budget | Mechanism | Certified |
|--------|--------|-----------|----------|
| Log DOM rows | ≤ 1,500 | Dual ceiling | ✅ |
| Toast nodes | ≤ 5 | Observer + TTL | ✅ |
| SSE stale threshold | 45 s | EventSource patch | ✅ |
| SSE storm threshold | 5/60s | Sliding window | ✅ |
| DOM node warn | 8,000 | 30 s poll | ✅ |
| DOM node crit | 14,000 | 30 s poll | ✅ |
| Heap trend warn | 5 monotonic | 30 s samples | ✅ |
| FPS warn | < 40 fps | rAF sampler | ✅ |
| FPS crit | < 20 fps | rAF sampler | ✅ |

---

## 5. Certification Statement

> The Nexora AI runtime satisfies the Z24 Long-Session Stress Validation Standard as of 2026-05-16.  
> All Z21 guarantees are preserved and strengthened with Z24 additions.  
> Memory trend tracking detects monotonically growing heap over a 30-second sampling window.  
> SSE reconnect storm detection activates at 5 reconnects within 60 seconds.  
> A DOM node auto-alarm fires at 14,000 nodes, visible in both console and NxBus.  
> A full diagnostic snapshot is available via `_nxDiagSnapshot()`.  
> Session uptime and observer count are tracked and visible in the perf HUD.

**Module:** `static/js/nx-runtime-hygiene.js` (updated Z21→Z24)
