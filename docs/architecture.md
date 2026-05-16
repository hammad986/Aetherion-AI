# Nexora AI: Platform Architecture

## Core Philosophy
Nexora AI is built on the premise of **Deterministic Event-Sourced Orchestration**. We prioritize stability, observability, and operability over complex, premature distributed infrastructure.

## Component Overview

1. **Frontend Event Bus (`nx-bus.js`)**
   * Acts as the canonical cross-module communication layer.
   * Decouples components (`nx-state`, `nx-timeline`) from direct global (`window.*`) coupling.

2. **Replay & Timeline Visualization (`nx-timeline.js`, `nx-dag.js`)**
   * Translates the backend's deterministic JSON execution payloads into a visual, scrubbable timeline.
   * Provides real-time DAG (Directed Acyclic Graph) tracking for multi-agent loops.

3. **Backend App Factory (`web_app.py`)**
   * A modular Flask setup containing isolated Blueprints (`workspace.py`, `admin.py`).
   * Avoids routing monoliths by separating administrative, auth, and execution logic.

4. **Event-Sourced Execution Store (`execution/store.py`)**
   * The single source of truth for runtime execution.
   * Append-only SQlite design ensures crash recovery and precise auditing.

5. **Sandbox & Security Boundaries (`execution/sandbox.py`)**
   * Hardened subprocess boundaries for executing tasks.
   * Supervised through strict OS TTL and memory boundaries.

6. **DevTools / Operator Control (`nx-devtools.js`)**
   * Human-In-The-Loop (HITL) pause/resume logic.
   * Quarantine execution and Policy Overrides.

## Flow of Execution
1. User submits a prompt.
2. Request hits the Orchestrator, storing an `EventTypes.TASK_STARTED`.
3. Agent loops execute within the Sandbox, persisting state updates to the DB.
4. If a threshold is crossed, HITL pauses the thread. DevTools highlights this.
5. Operator approves, the loop resumes.
6. Execution terminates, and a complete forensic bundle is available for export.
