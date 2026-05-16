# Z26 — Temporal Runtime (Scheduler)

## Scope

Safe beta-grade scheduling for delayed execution, timeout enforcement, recurring missions, and deadline tracking.

This is **not** a replacement for the heavy task queue in `task_queue.py`. It is a lightweight temporal metadata layer.

## Features

| Feature                 | Status   | Notes                                      |
|-------------------------|----------|--------------------------------------------|
| Delayed execution       | ✅ Beta  | `delay_secs` or explicit `run_at` timestamp |
| Timeout enforcement     | ✅ Beta  | Configurable per mission                   |
| Deadline tracking       | ✅ Beta  | Hard expiry with alert                     |
| Deadline approaching alert | ✅ Beta | Fires at < 20% of deadline remaining    |
| Recurring (interval)    | ✅ Beta  | Fixed interval with optional count cap     |
| Count-limited recurrence | ✅ Beta | Run N times then stop                     |
| Persistent schedule     | ❌ Deferred | In-memory only; lost on restart         |
| Distributed scheduling  | ❌ Deferred | Single-process only                     |

## Usage

### Schedule a delayed mission
```python
from runtime.scheduler import schedule_mission

m = schedule_mission(
    sid="session-abc",
    task_description="Run nightly test suite",
    delay_secs=3600,           # run in 1 hour
    timeout_secs=600,          # 10 min timeout
    deadline_secs=7200,        # expire after 2 hours
)
```

### Schedule a recurring mission
```python
m = schedule_mission(
    sid="session-abc",
    task_description="Periodic health check",
    delay_secs=0,
    recurrence="interval",
    recurrence_interval_secs=300,   # every 5 min
    recurrence_count_max=12,        # max 12 runs
)
```

### Get due missions
```python
from runtime.scheduler import get_due_missions
due = get_due_missions()
for mission in due:
    # dispatch to execution engine
    mark_started(mission.mission_id)
```

### Register alert callbacks
```python
from runtime.scheduler import register_alert_callback

def on_alert(alert_type, mission):
    if alert_type == "execution_timeout":
        notify_operator(mission.sid, f"Mission timed out: {mission.task_description}")

register_alert_callback(on_alert)
```

## Background Checker

Start the background deadline/timeout checker on app startup:

```python
from runtime.scheduler import start_background_checker
start_background_checker(interval_secs=10.0)
```

This daemon thread calls `check_deadlines_and_timeouts()` every 10 seconds and fires registered alert callbacks.

## Mission Lifecycle

```
PENDING → RUNNING → COMPLETED
                  → TIMEOUT
        → EXPIRED (deadline passed before start)
        → CANCELLED (manually cancelled)
```

## Telemetry

```python
from runtime.scheduler import scheduler_telemetry
snapshot = scheduler_telemetry()
```

Returns: total missions, counts by status, pending-due-now count.

## FUTURE_RUNTIME markers

- `FUTURE_RUNTIME_SCHEDULER_PERSISTENCE`: SQLite/Redis persistence for schedule survival across restarts — required before production
- `FUTURE_RUNTIME_DISTRIBUTED_SCHEDULER`: Celery or APScheduler with Redis broker — required for multi-worker deployment
- `FUTURE_RUNTIME_CRON_SYNTAX`: cron expression support for daily/weekly recurrence — deferred to v2
- `FUTURE_RUNTIME_SCHEDULER_UI`: operator schedule management UI — deferred to v2
