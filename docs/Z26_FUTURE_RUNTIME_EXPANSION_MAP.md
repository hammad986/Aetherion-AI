# Z26 — Future Runtime Expansion Map

## Purpose

This document defines the intended future integration boundaries, ownership definitions, and architectural constraints for runtime capabilities deferred beyond Phase Z26.

All future items below must be implemented as **loosely coupled, injectable modules**. None of these may be hardwired into existing runtime modules.

---

## FUTURE_RUNTIME_RETRIEVAL
**Semantic Long-Term Memory**

- Owner: `long_term_memory.py` (existing) + new retriever interface
- Integration boundary: injected as a `retrieve(query: str) -> list[str]` callable into `SessionContext`
- Warning: never import `vector_store` or `chromadb` from `context_compression.py`
- Migration path: add `LongTermRetriever` protocol; `SessionContext.build_prompt_context()` calls it if provided
- Deferred: v2

---

## FUTURE_RUNTIME_CALIBRATION
**Learned Confidence Calibration**

- Owner: separate `runtime/confidence_calibrator.py` module
- Integration boundary: injected as `calibrate(raw_score: float, context: dict) -> float` callable into `score_step()`
- Warning: never embed model weights or training loops in `confidence_engine.py`
- Migration path: `score_step()` already accepts `base_score` override — calibrator overrides this
- Deferred: v2

---

## FUTURE_RUNTIME_ASYNC_HITL
**Async Human Review Queue**

- Owner: new `runtime/hitl_queue.py` module
- Integration boundary: `ConfidenceReport.requires_hitl = True` → push to review queue
- Warning: do not build review queue into `confidence_engine.py`; it must remain a pure scorer
- Migration path: register a post-score callback via `register_hitl_handler(fn)`
- SLA tracking and timeout handling in dedicated queue module
- Deferred: v2

---

## FUTURE_RUNTIME_CAUSAL_GRAPH
**Structured Decision Causality Graph**

- Owner: new `runtime/causal_graph.py` module
- Integration boundary: `DecisionRecord` gets an optional `caused_by: list[str]` field
- Warning: never couple graph traversal logic to `explainability.py`
- Migration path: `record_decision()` accepts optional `caused_by` parameter; graph built externally
- Deferred: v2

---

## FUTURE_RUNTIME_SCHEDULER_PERSISTENCE
**Durable Schedule Storage**

- Owner: new `runtime/scheduler_store.py` module
- Integration boundary: `ScheduledMission` serialized to SQLite on write, loaded on startup
- Warning: never add DB imports to `scheduler.py`; inject a `store` interface
- Migration path: `schedule_mission()` accepts optional `store=` parameter
- Required before: multi-user production deployment
- Deferred: v1.5 / production hardening

---

## FUTURE_RUNTIME_DISTRIBUTED_SCHEDULER
**Multi-Worker Scheduling**

- Owner: separate worker service or APScheduler with Redis
- Integration boundary: current `scheduler.py` becomes a client stub
- Warning: never add Redis imports to `scheduler.py` directly
- Migration path: replace in-memory registry with Redis-backed implementation behind same interface
- Required before: autoscaling deployment with multiple Gunicorn workers
- Deferred: v2

---

## FUTURE_RUNTIME_PLUGIN_MARKETPLACE
**Third-Party Tool Plugins**

- Owner: new `runtime/plugin_registry.py`
- Integration boundary: plugins register via a well-defined `ToolPlugin` protocol
- Warning: plugin execution must be fully sandboxed — never allow raw subprocess access from plugin API
- Migration path: `tools.py` registry extended with plugin loader
- Security review required before any public plugin acceptance
- Deferred: v2+

---

## FUTURE_RUNTIME_MULTI_USER_COLLABORATION
**Shared Session / Collaborative Agents**

- Owner: new `runtime/collab_session.py`
- Integration boundary: `SessionContext` becomes shareable via a session token
- Warning: current `_sessions` dict is not thread-safe for write concurrency across users
- Migration path: introduce session locking per-user and conflict resolution protocol
- Deferred: v2+

---

## FUTURE_RUNTIME_LOCAL_PRIVACY
**Local-Only / Offline Mode**

- Owner: new `runtime/privacy_mode.py`
- Integration boundary: `router.py` checks privacy mode flag before dispatching to cloud providers
- Warning: do not add privacy checks to `context_compression.py` — keep concerns separated
- Migration path: `PrivacyMode` enum in router config; local model (Ollama) preferred when enabled
- Deferred: v2

---

## FUTURE_RUNTIME_ADAPTIVE_PERSONA
**Operator-Defined Persona System**

- Owner: new `runtime/persona.py`
- Integration boundary: persona injected as additional system message in `build_prompt_context()`
- Warning: persona definition must never affect routing logic or confidence scoring
- Migration path: `SessionContext` accepts optional `persona_prompt: str` at init
- Deferred: v2

---

## Architectural Constraints (Non-Negotiable)

1. **Loose coupling**: Every future module must be injectable, not imported directly
2. **Single responsibility**: Each module owns exactly one concern
3. **No circular imports**: Runtime modules must not import from `web_app.py`
4. **Interface-first**: Define the protocol/interface before implementation
5. **Audit by default**: Every new runtime module must write to a structured audit log
6. **Stability first**: No future expansion may degrade current beta stability
