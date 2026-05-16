# CLEANUP CLASSIFICATION REPORT
# Phase Z3 Modularization
# Generated: 2026-05-15

## 1. Active Runtime Files (KEEP)
Core orchestration and execution logic required for production:
- `web_app.py` (Monolith entrypoint)
- `runtime/state.py` (Centralized shared state)
- `routes/*_routes.py` (Extracted blueprints)
- `streaming/sse_redis.py`, `streaming/sse_manager.py`
- `nx_hitl_response.py`, `nx_crash_recovery.py`, `nx_session_guard.py`
- `infra/db_helper.py`
- `security/*`

## 2. Temporary Migration Scripts (DELETE)
Scripts built exclusively to execute the modularization mapping and slicing:
- `extract_z3.py`

## 3. Completed Audit Scripts & Reports (ARCHIVE)
Documentation and evidence generated during the stability audits:
- `RUNTIME_ROUTE_DEPENDENCY_MAP.md`
- `SQLITE_WAL_CERTIFICATION.md`
- `REDIS_SSE_VALIDATION.md`
- `STATE_DECOUPLING_CERTIFICATION.md`
- `EXTRACTION_VALIDATION_REPORT.md`

## 4. Deprecated Assets (ARCHIVE / DELETE LATER)
Code that has been safely obsoleted by the Phase Z architecture:
- Any scattered duplicate SQLite wrappers.
- Orphaned routing functions replaced by blueprint extractions.

## 5. Dangerous-To-Delete Files
Do NOT delete these files under any circumstances, as they control deployment and persistence:
- `start.sh` / `nx_deploy_start.sh`
- `docker-compose.yml`, `Dockerfile`
- `memory.db`, `sessions.db`, `saas_platform.db`

**NEXT STEPS:** After explicit user approval, proceed with deleting Phase 2 files and archiving Phase 3 and 4 files to an `archive/` directory to permanently clean the root workspace.
