# RUNTIME STATE DEPENDENCY AUDIT
# Generated: 2026-05-15

## 1. Executive Summary
This audit maps the globally scoped state within `web_app.py` to prepare for a safe extraction to `runtime/state.py`. Despite earlier nomenclature (`_sessions`, `_pending_tasks`), the forensic scan confirms the state is managed via specific domain variables (e.g., `pending_queue`, `running`, `queue_lock`) as well as a series of domain-specific `threading.Lock` and `dict` mappings. 

## 2. Global State Inventory

### 2.1 Core Orchestration & Lifecycle State
*   **`queue_lock`** (Lock): Guards the lifecycle of the task worker.
*   **`pending_queue`** (deque): Tracks `session_id`s waiting to be picked up by the background thread.
*   **`running`** (dict): `{"sid": None, "proc": None, "seq": 0}`. Tracks the currently executing subprocess.
*   **`managed_runs`** (deque): Timestamps of recent runs to enforce rate limits.

### 2.2 Domain-Specific Locks & Registries
*   **`_db_lock`** (Lock): Global SQLite concurrency guard.
*   **`_BROWSER_LOCK`** (Lock): Guards CDP/Browser interactions.
*   **`_REVIEW_LOCK`** (Lock): Guards human-in-the-loop review operations.
*   **`_p13_summarize_lock`** / **`_p13_in_progress`** (set): Guards Phase 13 LLM memory summarization to prevent duplicate tasks.
*   **`_chain_lock`** (Lock): Guards chain execution flows.
*   **`_editor_llm_lock`** (Lock): Guards LLM-based file edits.
*   **`_ROUTING_LOCK`** (Lock): Guards model routing changes.
*   **`_TERMINAL_MODE_LOCK`** / **`_terminal_lock`** (Locks): Guards PTY allocation and read/write states.
*   **`_hitl_state`** (dict) / **`_hitl_lock`** (Lock): Tracks `{sid: {paused, inject_queue}}` for Human-in-the-Loop workflows.
*   **`_STEP_STORE`** (dict) / **`_STEP_LOCK`** (Lock): Caches intermediate plan steps.
*   **`workflow_queues`** (dict): Phase 21 execution workflows.
*   **`_oauth_states`** (dict): Tracks temporary OAuth nonces/states.
*   **`ext_counts`** (dict): File extension analysis counts.
*   **`_P7_PIPELINES`** (dict) / **`_p7_lock`** (Lock): Phase 7 advanced pipelines.

## 3. Threat Model & Safety Boundaries
Extracting these variables requires preserving memory identity. If `web_app.py` were to redefine them locally, concurrent threads would mutate separate dictionaries, leading to catastrophic runtime desynchronization, SQLite WAL deadlocks, and missed SSE events.

**Safe State Boundary:** All the above variables can be safely extracted to `runtime/state.py` as pure definitions. `web_app.py` will import them via `from runtime.state import ...` or `import runtime.state as state`. Since Python modules are singletons, memory identity will be strictly preserved across the entire process space.

## 4. Crash Recovery & SSE Replay Ownership
Because `nx_crash_recovery.py` and the `RedisSSEBridge` act on these exact memory references, extracting them to a dedicated `runtime/state.py` actually improves reliability. Downstream tools can import `runtime/state.py` directly instead of reaching into the monolithic `web_app.py`.
