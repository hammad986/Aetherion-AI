# Z50 Execution Feedback States Report

## Phase Z50D — Accepted → Running → Completed → Failed → Retrying

**Date:** 2026-05-17  
**Status:** COMPLETE  

---

## Problem Statement

Before Z50, the only feedback on task submission was:
1. The run button label changing from `▶ Run` to `■ Stop`
2. A status badge changing to "Running"

There was no visual confirmation that the task had been *accepted* before the agent started, no elapsed timer, and no distinct completion/failure micro-animation on the run button itself.

---

## Feedback Bar (`#z50ExecFeedback`)

A new inline feedback bar is injected before the Activity Bar in the center panel. It is invisible by default and becomes visible when state changes:

| State | Class | Dot | Label | Auto-dismiss |
|---|---|---|---|---|
| `accepted` | `.accepted` | Indigo pulse | "Task accepted — queuing…" | No |
| `running` | `.running` | Green fast pulse | "Agent is executing…" | No |
| `completed` | `.completed` | Green solid | "Task completed" | 6 000ms |
| `failed` | `.failed` | Red solid | "Task failed — check output for details" | 10 000ms |
| `retrying` | `.retrying` | Yellow fast pulse | Custom message | Configurable |

The bar is accessible: `role="status"` + `aria-live="polite"`.

---

## Run Button Micro-animations

Two CSS animation classes are applied to `#runBtn` (`.nx-topbar-run-btn`):

- **`z50-accepted`** — Applied immediately on click when status is not already running. Indigo box-shadow ripple (0.5s). Removed after 600ms.
- **`z50-completed`** — Applied when `nxSetGlobalStatus('idle')` fires after a `running` period. Green box-shadow ripple (0.6s). Removed after 700ms.

---

## Elapsed Timer

When a task starts (accepted or running state), `z50StartElapsedTimer()` begins a 1-second interval updating `#z50ExecElapsed`:

- Under 60s: `42s`
- Over 60s: `1m 12s`

Uses `font-variant-numeric: tabular-nums` to prevent layout shift as digits change.

Timer is cleared via `z50StopElapsedTimer()` on completion or failure.

---

## State Machine Integration

Z50 hooks into `nxSetGlobalStatus()` (defined in `ui.js`) using a non-destructive wrapper pattern:

```javascript
const origSetStatus = window.nxSetGlobalStatus;
window.nxSetGlobalStatus = function (status) {
  origSetStatus.call(this, status);   // preserve all original behaviour
  z50UpdateRuntimePulse(status);      // update topbar pulse dot
  // ... feedback bar + button animation logic
};
```

This preserves all existing behaviour (log banners, panel open/close, error card visibility) and layers Z50 signals on top.

---

## Reconnect Awareness Banner (`#z50ReconnectBar`)

A warning bar (`z50-reconnect-bar`) is injected below the topbar. It becomes visible when:

1. Custom DOM event `nx:sse:disconnected` is fired
2. `#nx-obs-conn-label` text contains "reconnect" (via MutationObserver fallback)

It hides again on `nx:sse:reconnected` or `nx:sse:connected`.
