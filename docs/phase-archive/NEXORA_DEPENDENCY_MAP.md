# NEXORA_DEPENDENCY_MAP.md
# Forensic Dependency & Classification Audit — Phase Y
# Generated: 2026-05-15 | Auditor: Antigravity

---

## CLASSIFICATION LEGEND

| Tag | Meaning |
|-----|---------|
| `ACTIVE_RUNTIME` | Required at import/startup, in the hot path |
| `ACTIVE_FRONTEND` | Loaded by browser (index.html), JS/CSS asset |
| `ACTIVE_BACKEND` | Module imported by active backend Python files |
| `ACTIVE_DEPLOYMENT` | Required for prod deployment (Gunicorn, Docker, Nginx) |
| `ACTIVE_SECURITY` | Security/auth boundary — must NOT be touched |
| `LEGACY_COMPAT` | Present for compatibility; not in active hot path |
| `DEPRECATED_SAFE` | Confirmed unused; safe to archive |
| `ARCHIVE_ONLY` | Tooling/audit scripts; not runtime |
| `DANGEROUS_TO_DELETE` | Removal would break runtime or deployment |

---

## PART 1 — PYTHON BACKEND FILES

### ACTIVE_RUNTIME (Critical — Do Not Touch)

| File | Why Active |
|------|-----------|
| `web_app.py` | Flask app entrypoint, 328 routes, all Gunicorn workers import this |
| `streaming/sse_manager.py` | SSEManager class — all SSE delivery flows through here |
| `streaming/sse_redis.py` | RedisSSEBridge — multi-worker SSE router, initialized at startup |
| `infra/db_helper.py` | WAL monkey-patch — MUST run before any sqlite3.connect |
| `infra/__init__.py` | Exports db_adapter, event_bus, tenant, telemetry, resilience |
| `infra/db_adapter.py` | Centralized DB factory used by infra package |
| `infra/event_bus.py` | Internal event bus singleton |
| `infra/resilience.py` | Session reaper, degraded mode, recovery playbook |
| `infra/tenant.py` | Tenant registry — multi-tenant gating |
| `infra/telemetry.py` | Telemetry singleton |
| `auth_system.py` | All auth logic: JWT, bcrypt, session tokens |
| `security.py` | Rate limiting, CORS, sanitizers, kill switch |
| `config.py` | Platform config — all env vars |
| `config_manager.py` | Config manager shim |
| `scheduler.py` | TaskScheduler — cron-style job runner |
| `task_queue.py` | Background task queue |
| `payments.py` | Billing/subscription logic |
| `memory.py` | Agent memory system (120KB — large) |
| `long_term_memory.py` | LTM storage to memory.db |
| `agent.py` | Core AI agent orchestration (80KB) |
| `router.py` | Model routing, provider selection (48KB) |
| `tools.py` | Agent tool registry |
| `tool_executor.py` | Tool execution shim |
| `tool_decision.py` | Tool decision engine |
| `tool_integrations.py` | Tool API integrations |
| `code_runner.py` | Code execution engine |
| `code_intel.py` | Code analysis/intelligence |
| `code_testing.py` | Test runner integration |
| `command_layer.py` | Shell command layer |
| `terminal_backend.py` | xterm.js backend |
| `sandbox_manager.py` | Sandboxing |
| `deployment_engine.py` | Deploy pipelines |
| `model_router.py` | Model selection |
| `execution_planner.py` | Multi-step execution planning |
| `task_chains.py` | Task chain orchestration |
| `workflow_engine.py` | Workflow engine |
| `notifications.py` | Real-time notification system |
| `support.py` | Customer support system |
| `idempotency.py` | Request dedup / billing safety |
| `mcp_context.py` | MCP context manager |
| `nx_crash_recovery.py` | Crash recovery daemon |
| `nx_session_guard.py` | Session guard |
| `nx_startup_check.py` | Startup health checks |
| `nx_hitl_response.py` | HITL lifecycle routes |
| `nx_backup.py` | DB backup utility |
| `safety_layer.py` | Agent safety constraints |
| `governance_layer.py` | Policy governance |
| `trust_engine.py` | Trust scoring |
| `semantic_validator.py` | Output validation |
| `feedback.py` | User feedback collection |
| `artifact_registry.py` | Artifact tracking |
| `resource_tracker.py` | Resource usage tracking |
| `observability.py` | Observability hooks |
| `admin_config.py` | Admin config |
| `admin_routes.py` | Admin blueprint (registered separately from routes/admin.py) |
| `account_recovery.py` | Account recovery flows |
| `main.py` | CLI entry (also imported by web_app.py indirectly) |
| `app.py` | Alternative Flask entrypoint |
| `orchestrator.py` | Top-level orchestrator |
| `agents.py` | Agent registry |
| `vector_store.py` | Vector embedding store |
| `generate_docs.py` | Doc generation (imported at runtime) |
| `code_learning.py` | Online learning |
| `github_analyzer.py` | GitHub integration |
| `file_analyzer.py` | File analysis |
| `browser.py` | Browser automation |
| `browser_automation.py` | Browser tools |
| `browser_resilience.py` | Browser recovery |
| `asset_pipeline.py` | Frontend asset pipeline |
| `project_runner.py` | Project runner |
| `hardware_monitor.py` | Hardware metrics |

### ACTIVE_BACKEND (Route Modules / Blueprints)

| File | Why Active |
|------|-----------|
| `routes/health.py` | `/api/health` blueprint |
| `routes/workspace.py` | Workspace blueprint |
| `routes/execution.py` | Execution blueprint |
| `routes/admin.py` | Admin blueprint (routes/ version) |
| `middleware/observability.py` | Request observability middleware |
| `middleware/guards.py` | Auth guards |
| `middleware/api_response.py` | API response normalizer |
| `execution/hitl.py` | HITL tracker |
| `execution/job_manager.py` | Job manager |
| `execution/worker.py` | Execution worker |
| `execution/store.py` | Execution state store |
| `execution/events.py` | Execution event types |
| `execution/graph.py` | Execution DAG |
| `execution/orchestrator.py` | Execution orchestrator |
| `execution/agent_registry.py` | Agent registration |
| `execution/coordination_bus.py` | Agent coordination |
| `execution/delegation_engine.py` | Task delegation |
| `execution/memory_arbiter.py` | Memory arbitration |
| `execution/policy.py` | Execution policy |
| `execution/providers.py` | Provider selection in execution |
| `execution/recovery.py` | Execution recovery |
| `execution/replay.py` | Execution replay |
| `execution/resource_governor.py` | Resource limits |
| `execution/sandbox.py` | Execution sandbox |
| `execution/secrets.py` | Secret handling |
| `execution/workspace_lock.py` | Workspace locking |
| `execution/export.py` | Export utilities |

### ACTIVE_DEPLOYMENT

| File | Why Active |
|------|-----------|
| `gunicorn.conf.py` | Production WSGI config — Redis/worker logic |
| `Procfile` | Heroku/Railway entrypoint |
| `Dockerfile` | Container build |
| `docker-compose.yml` | Compose deployment |
| `nexora.service` | systemd service unit |
| `nx_deploy_start.sh` | Deployment startup script |
| `start.sh` | Dev/prod start script |
| `requirements.txt` | Python dependencies |
| `.env` | Environment config (gitignored) |
| `.env.example` | Env template |
| `nginx.conf.example` | Nginx reverse proxy config |

### ACTIVE_SECURITY

| File | Why Active |
|------|-----------|
| `auth_system.py` | JWT issuance, bcrypt, OAuth |
| `security.py` | Rate limiting, CORS, sanitizers |
| `security/` (dir) | Security policies |

---

## PART 2 — FRONTEND ASSETS

### ACTIVE_FRONTEND — JavaScript (ALL loaded by index.html)

All 44 files in `/static/js/` are referenced in `index.html`:

| File | Role |
|------|------|
| `boot.js` | Bootstrap/init sequence |
| `runtime.js` | Core runtime (205KB — primary engine) |
| `dashboard.js` | Dashboard (151KB) |
| `nx-bus.js` | NxBus event system (SSE consumer contract) |
| `nx-sse-runtime.js` | SSE client runtime |
| `nx-state.js` | State management |
| `nx-signals.js` | Signal system |
| `nx-orchestrator.js` | Frontend orchestrator |
| `nx-workspace-runtime.js` | Workspace runtime |
| `nx-mission.js` | Mission panel |
| `nx-dag.js` | DAG visualization |
| `nx-activity.js` | Activity feed |
| `nx-clarity.js` | Clarity panel |
| `nx-chunker.js` | Stream chunking |
| `nx-shim.js` | Compatibility shim |
| `nx-hardening.js` | Frontend hardening |
| `nx-diagnostics.js` | Diagnostics panel |
| `nx-devtools.js` | Dev tools |
| `nx-observability.js` | Observability panel |
| `nx-hitl-bridge.js` | HITL communication bridge |
| `nx-hitl-panel.js` | HITL UI panel |
| `nx-intelligence.js` | Intelligence surface |
| `nx-agi-surface.js` | AGI surface |
| `nx-surface-fusion.js` | Surface fusion |
| `nx-trust-intel.js` | Trust intelligence |
| `nx-trust-ui.js` | Trust UI |
| `nx-polish.js` | UI polish |
| `nx-onboard.js` | Onboarding flow |
| `nx-monaco.js` | Monaco editor integration |
| `nx-xterm.js` | xterm.js terminal |
| `nx-session-cleanup.js` | Session cleanup |
| `nx-timeline.js` | Timeline panel |
| `activity.js` | Activity module |
| `agent_mem.js` | Agent memory UI |
| `session.js` | Session management |
| `workspace.js` | Workspace UI |
| `ui.js` | UI utilities |
| `history.js` | History panel |
| `evolution.js` | Evolution panel |
| `execution_graph.js` | Execution graph |
| `feedback.js` | Feedback UI |
| `immersive.js` | Immersive mode |
| `stability.js` | Stability panel |
| `support.js` | Support UI |
| `ux_trust.js` | UX trust layer |

### ACTIVE_FRONTEND — CSS (ALL loaded by index.html)

| File | Loaded |
|------|--------|
| `base.css` | ✅ YES |
| `components.css` | ✅ YES |
| `forms.css` | ✅ YES |
| `graphs.css` | ✅ YES |
| `layout.css` | ✅ YES |
| `motion.css` | ✅ YES |
| `nds-tokens.css` | ✅ YES |
| `nds.css` | ✅ YES |
| `nx-agi-native.css` | ✅ YES |
| `nx-observability.css` | ✅ YES |
| `stability.css` | ✅ YES |
| `support.css` | ✅ YES |

### ⚠️ CSS NOT IN index.html (Potentially orphaned)

| File | Status |
|------|--------|
| `nx-shell.css` | NOT found in index.html — may be inlined or imported by JS |
| `nx-workspace-tokens.css` | NOT found in index.html — may be imported by nds-tokens.css or JS |

> **Action**: Verify these are not imported via `@import` inside other CSS files before archiving.

---

## PART 3 — ARCHIVE DIRECTORY (All ARCHIVE_ONLY)

The `/archive/` directory contains **66 files** — all are operational tooling
scripts from previous engineering phases. NONE are imported by runtime code.

### Sub-classification:

| Pattern | Files | Classification |
|---------|-------|----------------|
| `__inject_phase_*.py` (g→z) | 20 files | ARCHIVE_ONLY — past injection scripts |
| `__audit_*.py` | 5 files | ARCHIVE_ONLY — past audit scripts |
| `__find_dupes*.py`, `__fix_dupes*.py` | 4 files | ARCHIVE_ONLY |
| `__reconstruct_*.py` | 3 files | ARCHIVE_ONLY — shell reconstruction |
| `__update_*.py` | 4 files | ARCHIVE_ONLY |
| `__qa_scan.py`, `__scan.py` | 2 files | ARCHIVE_ONLY |
| `inject_*.py` (no dunder) | 5 files | ARCHIVE_ONLY |
| `extract*.py` | 3 files | ARCHIVE_ONLY |
| `nx_beta_cohort.py` | 1 file | ARCHIVE_ONLY |
| `nx_failure_taxonomy.py` | 1 file | ARCHIVE_ONLY |
| `nx_intelligence_report.py` | 1 file | ARCHIVE_ONLY |
| `nx_reliability_trend.py` | 1 file | ARCHIVE_ONLY |
| `nx_semantic_eval.py` | 1 file | ARCHIVE_ONLY |
| `optimize.py`, `refactor*.py` | 3 files | ARCHIVE_ONLY |
| `promote_admin.py` | 1 file | LEGACY_COMPAT — one-time admin promotion |
| `*.txt`, `*.json`, `*.md` | 7 files | ARCHIVE_ONLY — scan outputs |
| `test*.py`, `test*.js` | 3 files | ARCHIVE_ONLY |
| `app_stderr.txt`, `app_stdout.txt` | 2 files | ARCHIVE_ONLY |
| `workspace_reconstruction_master.md` | 1 file | ARCHIVE_ONLY |

---

## PART 4 — IDENTIFIED BUGS / RISKS

### 🔴 CRITICAL: Duplicate Blueprint Registration
- `admin_bp` is registered **twice**: once from `routes/admin.py` and once from `admin_routes.py`
- This causes Flask to log warnings and potentially route conflicts
- **File**: `web_app.py` lines 495–503 (routes extraction block) and lines 518–525 (admin_routes block)

### 🔴 CRITICAL: WAL Patch Bypass in web_app.py
- Lines 8327, 8370, 8403: use `import sqlite3 as _sqlite3` then `_sqlite3.connect(...)` directly
- This BYPASSES the `infra.db_helper.patch_sqlite_globally()` WAL monkey-patch
- These connections get default rollback journal mode, NO WAL, NO busy_timeout
- These are auth-critical paths (password change, account delete)

### 🟡 HIGH: SSE Circular Import Risk
- `streaming/sse_manager.py` line 204 does a deferred import of `streaming.sse_redis.RedisSSEBridge`
- This is inside `broadcast_to_session()` — deferred to avoid circular import at module load
- The deferred import is called in the hot path (every SSE broadcast)
- This is a latency risk and a stability risk if either module reloads

### 🟡 HIGH: SSEManager `broadcast_to_session` Double-Path Confusion
- `web_app.py` sets `_broadcast_to_session = RedisSSEBridge.broadcast_to_session` (line 533)
- BUT the HITL callback on line 965 calls `_SSEMgr.broadcast_to_session()` directly
- `SSEManager.broadcast_to_session()` itself calls `RedisSSEBridge.broadcast_to_session()`
- So HITL path: SSEManager → RedisSSEBridge → local (correct, but adds one hop)
- Execution modules (execution/worker.py etc.) call `SSEManager.broadcast_to_session()` directly (correct)
- **Verdict**: No duplication, but two different call paths exist — should be unified

### 🟡 MEDIUM: nx_stress_harness.py at root level
- A stress testing tool at project root — could be confused with production code
- Should be moved to `/tools/` or `/tests/`

### 🟢 LOW: Replit_UIUX_Reference directory
- Contains UI reference data — not runtime
- Safe to leave as-is or archive

---

## PART 5 — COMPLETE FILE COUNT

| Category | Count |
|----------|-------|
| ACTIVE_RUNTIME | ~75 Python files |
| ACTIVE_FRONTEND | 44 JS + 14 CSS = 58 files |
| ACTIVE_DEPLOYMENT | 12 files |
| ACTIVE_SECURITY | 3 files |
| ARCHIVE_ONLY (in /archive/) | 66 files |
| Templates | 7 HTML files |
| **Total (root + subdirs)** | **~250+ files** |
