# REPOSITORY FORENSIC CLASSIFICATION REPORT
# Phase Z5 — Production Discipline | Generated: 2026-05-15

## LEGEND
| Tag | Meaning |
|-----|---------|
| `RUNTIME-CRITICAL` | Invoked at startup or during agent execution |
| `ACTIVE-BACKEND` | Imported by web_app.py or routes at runtime |
| `ACTIVE-FRONTEND` | Loaded by index.html |
| `DEPLOYMENT-CRITICAL` | Required to boot/serve/containerize |
| `OPERATIONAL-TOOLING` | Administrative utility; not hot-path |
| `ARCHIVE-CANDIDATE` | Superseded; no longer imported |
| `DEAD-ASSET` | Zero live references; safe to delete |

---

## ROOT-LEVEL PYTHON FILES

| File | Class | Notes |
|------|-------|-------|
| `web_app.py` | RUNTIME-CRITICAL | Primary Flask app; 10,747 lines |
| `app.py` | DEPLOYMENT-CRITICAL | Dev entrypoint; `from web_app import app` |
| `main.py` | OPERATIONAL-TOOLING | CLI runner; Agent+Memory+Config |
| `agent.py` | RUNTIME-CRITICAL | Core agent loop |
| `router.py` | RUNTIME-CRITICAL | Model routing; 48,842 bytes |
| `memory.py` | RUNTIME-CRITICAL | Session + long-term memory; 120KB |
| `config.py` | RUNTIME-CRITICAL | Env-driven config |
| `tools.py` | RUNTIME-CRITICAL | Tool dispatch |
| `auth_system.py` | RUNTIME-CRITICAL | JWT, bcrypt, OAuth |
| `payments.py` | ACTIVE-BACKEND | Razorpay billing |
| `scheduler.py` | RUNTIME-CRITICAL | TaskScheduler; instantiated at web_app L38 |
| `idempotency.py` | ACTIVE-BACKEND | Billing dedup; imported at web_app L43 |
| `notifications.py` | ACTIVE-BACKEND | User notification delivery |
| `support.py` | ACTIVE-BACKEND | Support ticket system |
| `feedback.py` | ACTIVE-BACKEND | User feedback storage |
| `model_router.py` | ACTIVE-BACKEND | Provider-aware routing |
| `code_runner.py` | RUNTIME-CRITICAL | Subprocess sandbox |
| `code_intel.py` | ACTIVE-BACKEND | Code analysis |
| `code_testing.py` | ACTIVE-BACKEND | Test generation/execution |
| `command_layer.py` | ACTIVE-BACKEND | Shell command dispatch |
| `terminal_backend.py` | ACTIVE-BACKEND | xterm.js backend |
| `task_chains.py` | ACTIVE-BACKEND | Multi-step task chaining |
| `task_queue.py` | RUNTIME-CRITICAL | Queue management |
| `execution_planner.py` | ACTIVE-BACKEND | Pre-execution planning |
| `semantic_validator.py` | ACTIVE-BACKEND | Semantic validation |
| `trust_engine.py` | ACTIVE-BACKEND | Execution trust scoring |
| `safety_layer.py` | ACTIVE-BACKEND | Pre-flight safety checks |
| `governance_layer.py` | ACTIVE-BACKEND | Policy enforcement |
| `security.py` | ACTIVE-BACKEND | Security utilities |
| `long_term_memory.py` | ACTIVE-BACKEND | Persistent episodic memory |
| `artifact_registry.py` | ACTIVE-BACKEND | Execution artifact tracking |
| `asset_pipeline.py` | ACTIVE-BACKEND | Static asset generation |
| `project_runner.py` | ACTIVE-BACKEND | Project-scoped execution |
| `deployment_engine.py` | ACTIVE-BACKEND | Deployment orchestration |
| `sandbox_manager.py` | ACTIVE-BACKEND | Isolated sandbox lifecycle |
| `resource_tracker.py` | ACTIVE-BACKEND | CPU/mem resource tracking |
| `observability.py` | ACTIVE-BACKEND | Observability hooks |
| `browser.py` | ACTIVE-BACKEND | Playwright browser wrapper |
| `browser_automation.py` | ACTIVE-BACKEND | Browser task automation |
| `browser_resilience.py` | ACTIVE-BACKEND | Browser crash/reconnect |
| `generate_docs.py` | ACTIVE-BACKEND | Doc generation; python-docx |
| `tool_decision.py` | ACTIVE-BACKEND | Tool selection engine |
| `tool_integrations.py` | ACTIVE-BACKEND | External API integrations |
| `tool_executor.py` | ACTIVE-BACKEND | Tool execution wrapper |
| `vector_store.py` | ACTIVE-BACKEND | Embedding vector store |
| `workflow_engine.py` | ACTIVE-BACKEND | Workflow DAG execution |
| `account_recovery.py` | ACTIVE-BACKEND | Account recovery flows |
| `admin_config.py` | ACTIVE-BACKEND | Admin configuration |
| `admin_routes.py` | ACTIVE-BACKEND | Admin panel routes |
| `agents.py` | ACTIVE-BACKEND | Multi-agent orchestration |
| `mcp_context.py` | ACTIVE-BACKEND | MCP adapter |
| `code_learning.py` | ACTIVE-BACKEND | Pattern learning |
| `config_manager.py` | ACTIVE-BACKEND | Config persistence |
| `file_analyzer.py` | ACTIVE-BACKEND | Static file analysis |
| `github_analyzer.py` | ACTIVE-BACKEND | GitHub repo analysis |
| `hardware_monitor.py` | ACTIVE-BACKEND | System hardware stats |
| `orchestrator.py` | ACTIVE-BACKEND | Thin orchestrator shim |
| `nx_backup.py` | OPERATIONAL-TOOLING | Backup utility |
| `nx_crash_recovery.py` | RUNTIME-CRITICAL | Worker crash handler (gunicorn hook) |
| `nx_hitl_response.py` | RUNTIME-CRITICAL | HITL inject/response |
| `nx_session_guard.py` | RUNTIME-CRITICAL | Session lifecycle guard |
| `nx_startup_check.py` | OPERATIONAL-TOOLING | Pre-launch health validation |
| `nx_deploy_start.sh` | DEPLOYMENT-CRITICAL | Shell startup script |
| `gunicorn.conf.py` | DEPLOYMENT-CRITICAL | Production WSGI config |
| `Procfile` | DEPLOYMENT-CRITICAL | Process definition |
| `Dockerfile` | DEPLOYMENT-CRITICAL | Container build |
| `docker-compose.yml` | DEPLOYMENT-CRITICAL | Multi-service compose |
| `requirements.txt` | DEPLOYMENT-CRITICAL | Python dependencies |
| `package.json` | DEPLOYMENT-CRITICAL | Node deps (Playwright) |
| `playwright.config.js` | DEPLOYMENT-CRITICAL | Playwright config |
| `nexora.service` | DEPLOYMENT-CRITICAL | systemd unit (VPS) |
| `nginx.conf.example` | DEPLOYMENT-CRITICAL | Nginx config template |
| `start.sh` | DEPLOYMENT-CRITICAL | Startup script |
| `extract_z3.py` | **DEAD-ASSET** | Phase Z3 one-time script; zero imports; DELETE |
| `test_a.py` | **DEAD-ASSET** | Circular-import test artifact; DELETE |
| `test_b.py` | **DEAD-ASSET** | Circular-import test artifact; DELETE |

---

## `/routes/` DIRECTORY

| File | Class | Notes |
|------|-------|-------|
| `session_routes.py` | ARCHIVE-CANDIDATE | Blueprint shell only (13 lines, no routes extracted) |
| `provider_routes.py` | ARCHIVE-CANDIDATE | Blueprint shell only (13 lines, no routes extracted) |
| `memory_routes.py` | ACTIVE-BACKEND | Has memory route handlers |
| `diagnostics_routes.py` | ACTIVE-BACKEND | Diagnostics endpoints |
| `execution.py` | ACTIVE-BACKEND | Execution route handlers |
| `health.py` | ACTIVE-BACKEND | Health check endpoint |
| `telemetry_routes.py` | ACTIVE-BACKEND | Telemetry routes |
| `ui_routes.py` | ACTIVE-BACKEND | UI-serving routes |
| `workspace.py` | ACTIVE-BACKEND | Workspace management routes |
| `admin.py` | ACTIVE-BACKEND | Admin route handlers |

---

## `/runtime/`, `/infra/`, `/streaming/`

| File | Class | Notes |
|------|-------|-------|
| `runtime/state.py` | RUNTIME-CRITICAL | All shared locks/queues; Phase Z2 decoupling |
| `infra/db_helper.py` | RUNTIME-CRITICAL | SQLite WAL monkey-patch |
| `infra/db_adapter.py` | ACTIVE-BACKEND | Multi-DB abstraction |
| `infra/event_bus.py` | ACTIVE-BACKEND | NxBus event distribution |
| `infra/resilience.py` | ACTIVE-BACKEND | Circuit breaker + retry |
| `infra/telemetry.py` | ACTIVE-BACKEND | Telemetry collection |
| `infra/tenant.py` | ACTIVE-BACKEND | Multi-tenant isolation |
| `streaming/sse_manager.py` | RUNTIME-CRITICAL | SSE client queue management |
| `streaming/sse_redis.py` | RUNTIME-CRITICAL | Redis pub/sub SSE bridge |

---

## `/archive/` DIRECTORY — 67 files

**All files: DEAD-ASSET.** One-time phase injection, audit, and migration scripts (Phases G–Z).
Zero live imports from any production code path. Contents are preserved in git history.

Patterns confirmed dead:
- `__inject_phase_*.py` (16 files: g, h, i, j, k, l, m, n, o, p, q, r, s, t, u, v, w, z)
- `__audit_*.py` (5 files)
- `__find_dupes*.py`, `__fix_dupes*.py` (4 files)
- `__reconstruct_*.py` (3 files)
- `__update_*.py` (4 files)
- `inject_*.py` (5 files: cluster, devops, dock, infra, security routes)
- `refactor*.py`, `extract*.py`, `optimize.py` (6 files)
- `*.txt` scan output files (4 files)
- `test.py`, `test_dag.py`, `test_jsdom.js` (3 files)
- `nx_beta_cohort.py`, `nx_failure_taxonomy.py`, `nx_semantic_eval.py` (3 report scripts)

Files to RETAIN in archive:
- `promote_admin.py` — admin utility; may be needed operationally
- `workspace_reconstruction_master.md` — historical reference

---

## ROOT MARKDOWN FILES

| File | Class | Notes |
|------|-------|-------|
| `README.md` | OPERATIONAL-TOOLING | KEEP |
| `ACCOUNT_DELETION_GOVERNANCE_PLAN.md` | OPERATIONAL-TOOLING | KEEP |
| `NEXORA_STABILITY_CERTIFICATION.md` | OPERATIONAL-TOOLING | KEEP |
| `REDIS_SSE_VALIDATION.md` | OPERATIONAL-TOOLING | KEEP |
| `SQLITE_CONCURRENCY_AUDIT.md` | OPERATIONAL-TOOLING | KEEP |
| `SQLITE_WAL_CERTIFICATION.md` | OPERATIONAL-TOOLING | KEEP |
| `SSE_DISTRIBUTED_STABILIZATION_REPORT.md` | OPERATIONAL-TOOLING | KEEP |
| `STATE_DECOUPLING_CERTIFICATION.md` | OPERATIONAL-TOOLING | KEEP |
| `Z3_MODULARIZATION_CERTIFICATION.md` | OPERATIONAL-TOOLING | KEEP |
| `Z4_VISUAL_POLISH_CERTIFICATION.md` | OPERATIONAL-TOOLING | KEEP |
| `CLEANUP_CLASSIFICATION_REPORT.md` | ARCHIVE-CANDIDATE | Superseded by this doc |
| `EXTRACTION_VALIDATION_REPORT.md` | ARCHIVE-CANDIDATE | Phase Z3 artifact |
| `MODULARIZATION_PROGRESS_REPORT.md` | ARCHIVE-CANDIDATE | Phase Z3 artifact |
| `NEXORA_ARCHIVE_INDEX.md` | ARCHIVE-CANDIDATE | Historical index |
| `NEXORA_DEPENDENCY_MAP.md` | ARCHIVE-CANDIDATE | Phase Z2 artifact |
| `NEXORA_SAFE_CLEANUP_PLAN.md` | ARCHIVE-CANDIDATE | Superseded |
| `RUNTIME_ROUTE_DEPENDENCY_MAP.md` | ARCHIVE-CANDIDATE | Phase Z3 artifact |
| `RUNTIME_STATE_DEPENDENCY_AUDIT.md` | ARCHIVE-CANDIDATE | Phase Z2 artifact |
| `SAFE_EXTRACTION_MAP.md` | ARCHIVE-CANDIDATE | Phase Z3 artifact |
| `UI_VISUAL_DISCIPLINE_AUDIT.md` | ARCHIVE-CANDIDATE | Phase Z4 working doc |
| `WEBAPP_DEPENDENCY_TRACE.md` | ARCHIVE-CANDIDATE | Phase Z3 artifact |
| `WEBAPP_EXTRACTION_MAP.md` | ARCHIVE-CANDIDATE | Phase Z3 artifact |
| `WEBAPP_MODULARIZATION_REPORT.md` | ARCHIVE-CANDIDATE | Phase Z3 artifact |

---

## DATABASE & LOG FILES

| File | Class | Notes |
|------|-------|-------|
| `memory.db`, `memory.db-shm`, `memory.db-wal` | RUNTIME-CRITICAL | WAL database; never delete |
| `sessions.db`, `saas_platform.db`, `billing.db` | RUNTIME-CRITICAL | Operational databases |
| `scheduler.db`, `feedback.db`, `support.db` | RUNTIME-CRITICAL | Operational databases |
| `app.log` | DEAD-ASSET | 4.4MB; add to .gitignore |
| `agent.log` | DEAD-ASSET | Debug log; add to .gitignore |
| `checkpoint.json` | OPERATIONAL-TOOLING | Agent checkpoint state |
| `memory.json` | OPERATIONAL-TOOLING | Memory export snapshot |

---

## SUMMARY

| Classification | Count |
|---------------|-------|
| RUNTIME-CRITICAL | 21 |
| ACTIVE-BACKEND | 48 |
| ACTIVE-FRONTEND | 58 |
| DEPLOYMENT-CRITICAL | 16 |
| OPERATIONAL-TOOLING | 12 |
| ARCHIVE-CANDIDATE | 27 |
| DEAD-ASSET | ~73 |
