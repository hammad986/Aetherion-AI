# Z29 Runtime Recovery Audit

**Phase:** Z29D — Mission Recovery + Failure Control  
**Date:** 2026-05-16  
**Status:** OPERATIONAL

---

## Overview

The Z29D runtime recovery system provides continuous stability monitoring, failure taxonomy classification, and a recovery action dispatch layer. It is designed to detect and safely contain runaway execution before it causes unrecoverable state.

---

## Failure Detection Thresholds

| Anomaly Type | Default Threshold | Detection Method |
|-------------|------------------|-----------------|
| `runaway_retries` | 8 retries per step | `SessionHealth.retry_counts[step_id] >= 8` |
| `replan_storm` | 5 replans total | `SessionHealth.replan_count >= 5` |
| `infinite_loop` | 150 loop iterations | `SessionHealth.loop_count >= 150` |
| `provider_instability` | 4 consecutive failures | `SessionHealth.consecutive_failures >= 4` |
| `stuck_mission` | 180s no step progress | `time.time() - last_step_ts >= 180` |
| `sse_flood` | 300 events/minute | Rolling 60s event window |

All thresholds are configurable per-session via `SessionHealth.thresholds` dict.

---

## Stability Score Calculation

```python
score = 1.0
score -= min(max_retries / threshold_retries, 1.0) * 0.30
score -= min(replan_count / threshold_replans, 1.0) * 0.25
score -= min(consecutive_failures / threshold_failures, 1.0) * 0.25
score -= min(loop_count / threshold_loops, 1.0) * 0.20
score = max(0.0, score)
```

| Score | Interpretation |
|-------|---------------|
| 0.80–1.00 | Healthy execution |
| 0.55–0.79 | Mild pressure, monitor |
| 0.30–0.54 | Elevated risk, consider intervention |
| 0.00–0.29 | Critical — auto-pause triggered |

---

## Auto-Protection Flow

```
check_stability(sid, emit_fn, auto_pause=True)
  │
  ├─ No anomalies → return []
  │
  └─ Anomalies detected
       │
       ├─ Record in SessionHealth.detected_failures
       │
       ├─ If critical/high severity AND not yet auto_paused:
       │     ├─ Set SessionHealth.auto_paused = True
       │     ├─ Emit agent.stability_alert SSE event
       │     ├─ Call pause_mission(sid) → sets _signals[sid] = PAUSE
       │     └─ Submit governance approval_request (mission_recovery)
       │
       └─ Return anomaly list
```

---

## Recovery Actions

| Action | Effect | Safety |
|--------|--------|--------|
| `pause` | Sets mission signal to PAUSE | Safe — cooperative stop |
| `cancel` | Sets mission signal to CANCEL | Destructive — terminates session |
| `checkpoint_resume` | Resumes from last checkpoint + clears auto_paused | Safe |
| `reduce_retries` | Applies `retry_budget` override (default: 3) | Safe — reduces pressure |
| `switch_provider` | Applies `provider`/`model` override | Safe — takes effect next step |
| `compress_context` | Forces context compression | Safe — may lose some active window |
| `operator_review` | Submits governance approval request | Safe — non-blocking |

---

## Recording Points

The recovery system must be called from `agent.py` between steps. Currently recording is done via:

- `on_retry(sid, step_id)` — after each retry in error handling loop
- `on_replan(sid)` — after each adaptive replan
- `on_loop_tick(sid)` — on each main execution loop iteration  
- `on_failure(sid)` — after each tool execution failure
- `on_success(sid)` — after each successful step completion
- `on_sse_event(sid)` — in emit_fn wrapper (optional, for SSE flood detection)

---

## Remaining Recovery Weaknesses

1. **No checkpoint snapshots:** The current `checkpoint_resume` action resumes execution from the in-memory state, not a true replay-safe checkpoint snapshot. If the agent process has restarted, the checkpoint file in `data/sessions/` would need to be loaded separately.

2. **SSE flood detection is optional:** The `on_sse_event()` call is only effective if wired into the agent's `emit_fn`. In the current implementation, it is not automatically wired — the rate calculation will under-count.

3. **Branch execution not implemented:** The `branch` recovery action (listed in Z29D requirements) creates a new session that forks from the current state. This requires deep DAG snapshot support and is reserved for a future phase.

4. **Auto-pause race condition:** In a multi-step execution loop, two consecutive loop iterations could both detect instability and attempt to set `auto_paused = True` before the first pause signal propagates. The `auto_paused` flag check in `check_stability()` prevents duplicate pause attempts, but the flag is not under the session lock.

---

## Scaling Concerns

- `SessionHealth` is in-memory per-process. In a multi-worker deployment, each Gunicorn worker maintains independent stability state. The auto-pause signal via `_signals` dict is also per-process. Redis-backed signal propagation (using `_nx_redis`) would be needed for full multi-worker stability.
- `stability_dashboard()` aggregates all sessions in `_sessions` dict — at 1000+ concurrent sessions, this O(n) scan would need pagination.
