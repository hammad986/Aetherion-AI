# Z28 Runtime Health Report

**Phase:** Z28D — Confidence + Execution Health Layer  
**Date:** 2026-05-16  
**Status:** OPERATIONAL

---

## Overview

The Z28D health layer provides operators with a continuous, non-intrusive view of session execution health. It synthesizes data from the confidence engine, decision records, and retry counters into a single health indicator with supporting detail.

---

## Health Level States

| Level | Score Range | Visual | Meaning |
|-------|-------------|--------|---------|
| `high` | 0.80–1.00 | Green bar | Normal operation, no anomalies |
| `medium` | 0.55–0.79 | Amber bar | Mild pressure — retries or slow steps detected |
| `low` | 0.30–0.54 | Orange bar | Elevated failures — operator should monitor |
| `critical` | 0.00–0.29 | Red bar | HITL threshold may be triggered |

---

## Data Sources

### Confidence Engine (`runtime/confidence_engine.py`)

The `SessionConfidenceTracker` maintains a rolling window of `ConfidenceReport` objects per session. Each report scores the step output on:

- **Evidence count** — how many completed steps support the current assertion
- **Retry count** — each retry subtracts from the base score
- **Tool failures** — binary tool failure indicator
- **Output entropy** — length/coherence heuristic on raw output

The `rolling_average()` method returns the mean score over the last N reports.

### Decision Records

Retry and escalation decisions are counted from the recent decision log for a session. This provides a cumulative failure signal independent of the rolling confidence score.

---

## API Contract

```
GET /api/z28/health?sid={session_id}

Response:
{
  "ok": true,
  "sid": "abc123",
  "confidence_level": "high",       // string: high/medium/low/critical
  "confidence_score": 0.82,         // float or null
  "confidence_history": [0.9, 0.85, ...],  // last 20 samples
  "sustained_low": false,           // bool: sustained low signal
  "sustained_critical": false,      // bool: requires immediate attention
  "retry_count": 2,                 // int
  "hitl_active": false,             // bool: HITL escalation in progress
  "active_provider": "gpt-4o",      // string
  "signals": [                      // recent decision signals
    {"type": "retry", "detail": "...", "penalty": 0.10}
  ]
}
```

---

## SSE Event: `agent.confidence_warning`

Emitted from `agent.py` whenever a `ConfidenceReport.requires_hitl` is `True`:

```json
{
  "score": 0.22,
  "level": "critical",
  "alert": "Sustained critical confidence — operator review required",
  "session_id": "abc123"
}
```

Routed by `nx-sse-runtime.js` to `NxBus.emit('nx:z28:health', payload)`.

---

## UI Components

- **Health Bar** — horizontal gradient bar, width driven by `confidence_score * 100%`
- **Level Badge** — color-coded text label (`high` / `medium` / `low` / `critical`)
- **Retry Counter** — integer badge, highlighted red when ≥ 3
- **HITL Indicator** — amber pulsing dot when `hitl_active: true`
- **Provider Badge** — shows active LLM provider name
- **Signal Log** — last 5 confidence-affecting events

---

## Polling Interval

The health widget polls every **8 seconds** via `GET /api/z28/health`. SSE events override immediately when received. Polling continues as long as the Intel tab is active.

---

## Failure Safety

All health computation is wrapped in try/except blocks in `web_app.py`. If the confidence engine is unavailable (e.g., first session before any steps), the API returns:

```json
{"ok": true, "confidence_level": "high", "confidence_score": null, "signals": []}
```

The UI renders this as an empty/idle state with no false alarms.
