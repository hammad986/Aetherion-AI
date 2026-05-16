# Nexora AI v1.0: Product Lock

This document defines the strict feature boundaries for Nexora AI version 1.0. 
The objective of v1.0 is a stable, observable, deterministic AI orchestration platform.

## What is Included (Stable)

### 1. Event-Sourced Execution Engine
*   Append-only SQLite event log.
*   Deterministic state tracking.
*   Crash recovery and DB retention cleanup policies.

### 2. Operator Control Center (DevTools UI)
*   **Live DAG Visualization**: Active tracking of agent nodes and execution ancestry.
*   **Timeline Replay Engine**: Scrubbable UI to review tool calls, system prompts, and file changes.
*   **HITL (Human-in-the-Loop)**: Ability to Pause, Resume, and Authorize blocked executions.
*   **Emergency Quarantine**: Hard task abort functionality bypassing graceful cleanup.
*   **Audit Export**: Downloadable JSON execution timelines.

### 3. Production Deployment Hardening
*   Docker container isolation utilizing an unprivileged user space.
*   WSGI deployment via Gunicorn with specific thread scaling constraints.
*   Automated `/api/v2/health` probe readiness.
*   Deployment Profiles (`local_dev`, `workstation`, `enterprise`).
*   Secure scoped API Secret Injection (no environment leaks to agents).
*   Role-Based Access Control (`viewer`, `operator`, `admin`).

## What is Experimental (v1.x Deferred)
These features exist in the architecture but are subject to stability testing and are NOT guaranteed SLA features for v1.0.
*   CGroups bounded memory enforcement (currently relies solely on Python `subprocess.run` TTL/Timeout boundaries).
*   Multi-Node Orchestration (Platform remains single-node scaled vertically for v1).

## What is Rejected / Excluded
*   Distributed Message Queues (Kafka/RabbitMQ).
*   Heavyweight persistence layers (PostgreSQL/Redis).
*   Speculative self-modifying "AGI" routines.

## Focus Until Shipping
No new features will be merged. All work is strictly limited to:
1.  **Bug Elimination**: Resolving interaction glitches, memory leaks, or SSE stream disconnects.
2.  **UX Polish**: Refining CSS gradients, easing interaction states, and clarifying error messages.
3.  **Documentation**: Completing inline comments and updating operator manuals.
