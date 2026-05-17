# Z50 Live Workspace Presence Report

## Phase Z50C — Runtime Warmth & Idle Heartbeat

**Date:** 2026-05-17  
**Status:** COMPLETE  

---

## Overview

Phase Z50C adds persistent low-level signals that communicate "this workspace is alive" even when no task is running. The goal is to eliminate the perception of a static/unresponsive UI during idle periods.

---

## Implemented Signals

### 1. Idle Hero Stat Strip — Live Polling

The four stat chips in the idle hero (`nxIdleModel`, `nxIdleConf`, `nxIdleCtx`, `nxIdleSched`) were previously hardcoded. They are now populated and refreshed every 12 seconds:

| Stat | Source | Endpoint |
|---|---|---|
| Active Model | First available provider | `GET /api/system/metrics` → `providers[]` |
| Confidence | `metrics.avg_confidence` | `GET /api/system/metrics` |
| Context Pressure | `metrics.context_pressure` | `GET /api/system/metrics` |
| Scheduled Tasks | `total_enabled` | `GET /api/scheduler/stats` (graceful fallback) |

When a stat value changes, the element receives class `z50-updated` which triggers a CSS fade-in animation (`z50-fade-in 260ms ease-out`). Unchanged values skip the animation to avoid visual noise.

### 2. Topbar Runtime Pulse

The existing `.z33-pulse-dot` / `.z33-runtime-pulse` elements now respond to execution state changes via `z50UpdateRuntimePulse(state)`:

| State | Dot behaviour |
|---|---|
| `idle` | Slow breathe (`z50-idle-breathe`, 4s, opacity 0.45→0.75) |
| `running` | Fast green pulse (`z50-queue-pulse`, 1.2s) |
| `error` | Solid red, no animation |

### 3. Queue Count Badge

`#nxQueueCount` gains class `z50-active` (blue colour + pulse animation) whenever its text value is greater than zero. Tracked via a lightweight `MutationObserver`.

### 4. Recent Executions in Idle Hero

`#nxIdleRecent` (if present) is populated from `/api/sessions?limit=5` on boot. Each row shows the session name, status dot, and status label, and is clickable to restore the session.

---

## CSS Animations Added

All animations use `prefers-reduced-motion: reduce` fallback (animations are disabled).

```
z50-idle-breathe   — opacity 0.45→0.75, 4s, infinite (idle pulse)
z50-queue-pulse    — scale 1→1.35→1, opacity 1→0.6→1 (active indicators)
z50-run-accepted   — box-shadow ripple, indigo, 0.5s (run button)
z50-run-complete   — box-shadow ripple, green, 0.6s (completion signal)
z50-fade-in        — opacity 0→1 (stat value update)
z50-slide-in-up    — translateY(6px)→0 + opacity (panel / bar entrances)
```

---

## Performance Notes

- The idle stat polling interval is 12 000ms — chosen to avoid adding to the existing heavy polling cadence (metrics polls every 8s, queue polls every 2s).
- Polling stops naturally when `#nxIdleHero` is hidden (agent is running) — checked at the top of `z50UpdateIdleStats()`.
- No new `MutationObserver` trees beyond the queue count badge observer.
