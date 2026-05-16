# Z24 — Long Session Operational Matrix

**Date:** 2026-05-16  
**Phase:** Z24 — Real-World Session Stress Validation

This matrix defines expected system behaviour and defence responses at each stage of a long-running session.

---

## Session Timeline Matrix

| Time | Event | DOM nodes | Heap | Log rows | Observers | SSE | Defence |
|------|-------|-----------|------|----------|-----------|-----|---------|
| 0 min | Initial load | ~3,200 | ~45 MB | 0 | ~10 | Connected | None needed |
| 5 min | First task run | ~3,800 | ~52 MB | 200 | 10 | Streaming | Log ceiling idle |
| 30 min | 5 task cycles | ~4,500 | ~58 MB | ≤1,500 | 10 | Active | Log ceiling triggered × 2–3 |
| 1 h | 10 task cycles | ~4,800 | ~62 MB | ≤1,500 | 10 | Active | Log trim notice visible |
| 2 h | 20 task cycles | ~5,200 | ~66 MB | ≤1,500 | 10 | Active | Heap trend baseline established |
| 4 h | 40 task cycles | ~5,400 | ~70 MB | ≤1,500 | 10 | Active | Heap Δ < 30 MB (stable) |
| 6 h | 60 task cycles | ~5,600 | ~73 MB | ≤1,500 | 10 | Active | Continue normal |
| 8 h | 80 task cycles | ~5,800 | ~76 MB | ≤1,500 | 10 | Active | Session healthy ✅ |

---

## Stress Event Response Matrix

| Stress event | Trigger | Detection | Response | Recovery |
|-------------|---------|-----------|----------|---------|
| Log burst (10k lines/min) | Heavy task | enforceLogCeiling every 5 s | Trim oldest rows; show notice | Automatic — ceiling maintained |
| Toast flood | Rapid errors | MutationObserver on body | Prune oldest toasts (> 5) | Immediate fade-out |
| SSE silence (45 s) | Network hiccup | checkSSEStaleness (10 s poll) | body.nx-sse-reconnecting → CSS indicator | On next `open` event |
| SSE storm (5/60 s) | Flapping network | Sliding window counter | body[data-sse-storm], NxBus event, HUD storm label | On first successful `open` |
| Heap monotonic growth | Memory leak | 5 consecutive 30 s samples | `console.warn` + `NxBus.emit('heapGrowthTrend')` | Manual investigation |
| DOM nodes > 8k | Detached nodes | 30 s checkDOMNodes | `console.warn` | Manual cleanup |
| DOM nodes > 14k | Node leak | 30 s checkDOMNodes | `console.error` + NxBus event | Manual cleanup |
| Modal leak (unclosed) | Bug | `_stack` length check | `_stack` tracks every open modal | `NxModal.close()` |
| Tab listener accumulation | Bug | Not currently tracked | Arrow nav listener is single (tablist) | Re-init safe |

---

## Memory Trend Thresholds

| Heap % of limit | State | HUD colour | Action |
|-----------------|-------|-----------|--------|
| < 60% | Normal | — | None |
| 60–85% | Warning | Yellow | Alert operator |
| > 85% | Critical | Red | Urgent investigation |
| Monotonic 5 samples | Growth trend | — | `console.warn` + NxBus |

---

## Timer Accumulation Prevention

| Timer source | Stop mechanism | Long-session safe |
|-------------|---------------|------------------|
| enforceLogCeiling | None (permanent) | ✅ Low-cost, idempotent |
| Toast TTL sweep | None (permanent) | ✅ Low-cost, idempotent |
| SSE staleness check | None (permanent) | ✅ Low-cost, idempotent |
| HUD refresh | None (permanent) | ✅ Low-cost, idempotent |
| Heap sampler | None (permanent) | ✅ 30 s interval, low-cost |
| DOM node alarm | None (permanent) | ✅ 30 s interval, low-cost |
| Session status tick | Dynamic (3–8 s) | ✅ Stops when task done |
| P4 token poll | `clearInterval(p4TokenPollTimer)` | ✅ Ref tracked |
| P7 agent poll | `clearInterval(p7PollTimer)` | ✅ Ref tracked |
| Dashboard polls | All ref-tracked | ✅ |

**Emergency:** `_nxClearAllTimers()` evicts all timers tracked in `_timerIds` (hygiene module timers only — does not affect dashboard timers).

---

## SSE Reconnect Storm Behaviour

```
Reconnect 1 → _sseReconnects = [t1]           (1 in 60s — normal)
Reconnect 2 → _sseReconnects = [t1, t2]       (2 in 60s — normal)
Reconnect 3 → _sseReconnects = [t1, t2, t3]   (3 in 60s — normal)
Reconnect 4 → _sseReconnects = [t1..t4]       (4 in 60s — elevated)
Reconnect 5 → _sseReconnects = [t1..t5]       (5 in 60s — STORM)
    → body[data-sse-storm="true"]
    → console.warn('[NX:Z24] SSE reconnect storm detected')
    → NxBus.emit('sseReconnectStorm', { count: 5 })
    → HUD SSE row: "STORM (5 reconnects)" [yellow]

SSE open (t > t1 + 60s) → _sseReconnects pruned to [] → storm cleared
    → body removeAttribute('data-sse-storm')
    → HUD returns to normal
```

---

## Operator Response Playbook

### "Session running slow / janky"
1. `nxPerfHUD()` — check FPS
2. If FPS < 20: investigate DOM node count
3. If DOM nodes > 10k: check for detached node leaks
4. `_nxDiagSnapshot()` — full dump for support

### "SSE seems stuck"
1. Check `body.nx-sse-reconnecting` in DevTools
2. `_nxPerfState.sseStaleSec` — time since last message
3. `_nxPerfState.sseStormActive` — storm status
4. If storm: check network tab for frequent SSE failures

### "Memory growing over time"
1. `_nxDiagSnapshot().heapSamples` — review trend
2. `_nxPerfState.logTrimCount` — verify log ceiling working
3. Check `observerCount` — should be ~10
4. Hard-refresh if heap exceeds 85% of limit
