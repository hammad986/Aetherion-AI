# Z26 — Operational Risk Report

## Risk Assessment Summary

This report identifies remaining architectural weaknesses, execution isolation risks, and frontend instability risks as of Phase Z26.

---

## 1. Remaining Architectural Weaknesses

### AW-1: New Runtime Modules Not Integrated (HIGH)
`context_compression.py`, `confidence_engine.py`, `explainability.py`, and `scheduler.py` are implemented but not yet called from the main execution loop (`agent.py`). They exist as standalone libraries without active effect.

**Mitigation**: Wire each module at the appropriate integration point in `agent.py` and `router.py`.

### AW-2: In-Memory Runtime State (HIGH)
All four new runtime modules store state in process memory only. A server restart or crash loses all context windows, confidence history, decision records, and scheduled missions.

**Mitigation**: Add SQLite persistence layer before production deployment.

### AW-3: Single-Process Concurrency (MEDIUM)
The runtime state dicts use `threading.Lock()` which works correctly under a single Gunicorn worker but will not work under multiple workers without a shared state backend (Redis).

**Mitigation**: For multi-worker deployment, replace in-memory registries with Redis-backed stores.

### AW-4: No Test Coverage for Z26 Modules (MEDIUM)
All four new runtime modules lack automated test coverage. Regressions may not be caught.

**Mitigation**: Add unit tests in `tests/` before integration.

---

## 2. Execution Isolation Risks

### EI-1: Terminal Sandbox Not Enforced (CRITICAL for multi-user)
`terminal_backend.py` and `code_runner.py` execute arbitrary shell commands in the host environment. In single-user self-hosted mode this is acceptable. In a shared SaaS environment this is a critical isolation failure.

**Mitigation**: Add container/namespace isolation per session before public beta. See `sandbox_manager.py`.

### EI-2: File Write Operations (HIGH)
Agent-triggered file writes (`tools.py`) operate in the session workspace directory. Path traversal protection exists but relies on a single check. A flaw in path sanitization could allow writes outside the workspace.

**Mitigation**: Use `os.path.realpath()` comparison and chroot-style workspace isolation.

### EI-3: No Resource Quotas (MEDIUM)
Individual sessions have no enforced CPU, memory, or disk quotas. A runaway agent could exhaust host resources.

**Mitigation**: Add `psutil`-based resource monitoring with per-session limits via `resource_tracker.py`.

### EI-4: Subprocess Timeout Not Uniform (MEDIUM)
Subprocess timeouts are set in multiple places with varying values. Some tool calls may not have timeouts enforced.

**Mitigation**: Centralize timeout enforcement in `command_layer.py`.

---

## 3. Frontend Instability Risks

### FE-1: MutationObserver Budget Exceeded (LOW-MEDIUM)
10 MutationObserver instances are active against a budget of 8. This is a performance degradation risk on low-end devices and in long sessions.

**Mitigation**: Audit `activity.js` and `ui.js` for observer consolidation.

### FE-2: SSE Auto-Reconnect Under Load (MEDIUM)
The SSE reconnect logic has not been load-tested with multiple concurrent sessions. Under high load, reconnect storms could occur.

**Mitigation**: Add jitter to reconnect backoff in `nx-sse-runtime.js`.

### FE-3: Monaco Editor Memory (LOW)
Long sessions with many code edits accumulate Monaco editor model state. There is no periodic model cleanup.

**Mitigation**: `_guardMonacoModels()` in `nx-hardening.js` partially addresses this — verify it fires correctly.

---

## 4. What Is NOT Production-Ready

| Component                     | Why Not Production-Ready                        |
|-------------------------------|--------------------------------------------------|
| Terminal execution            | No per-session isolation                        |
| Runtime context compression   | Not wired; in-memory only                       |
| Confidence engine             | Not wired; no HITL queue                        |
| Scheduler                     | No persistence; single-process                  |
| OAuth login                   | Keys not configured                             |
| Billing                       | Keys not configured                             |
| Email delivery                | Not configured                                  |
| Multi-worker SSE              | Requires Redis                                  |

---

## 5. What Must Be Stabilized Before Public Beta

1. **Wire** the confidence engine into the agent execution loop
2. **Wire** context compression into the agent context building
3. **Establish** terminal execution sandboxing
4. **Configure** billing, OAuth, and email secrets
5. **Add** scheduler persistence (at minimum: re-load from SQLite on startup)
6. **Add** basic test coverage for all Z26 runtime modules
7. **Deploy** with Redis for multi-worker SSE support

---

## 6. What Should Be Deferred to v2

See `docs/Z26_FUTURE_RUNTIME_EXPANSION_MAP.md` for full deferred item registry.

Summary: vector memory integration, learned confidence calibration, async HITL queue, distributed scheduling, plugin marketplace, multi-user collaboration, audio/video ingestion, adaptive persona.

---

**Report status: COMPLETE**
**Classification: Internal operational use only**
