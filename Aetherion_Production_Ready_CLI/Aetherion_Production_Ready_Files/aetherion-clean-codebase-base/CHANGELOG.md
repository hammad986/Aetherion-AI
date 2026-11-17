# Changelog

All notable changes to the Aetherion AI CLI-First release will be documented in this file.

## [Phase 11] — Hardening & Parity Release

### Added
- **Phase 11: Root `app.py` Forwarding Module & Module Shadowing Immunity** (`app.py`):
  - Converted root `app.py` from an obsolete 3-line stub into an authoritative re-exporting entry point for `backend.app`.
  - Exports `app` and `create_app` directly from `backend.app`, guaranteeing namespace parity across `app` and `backend.app`.
  - Fixes module shadowing under `python -m pytest` when the repository root is prepended to `sys.path`.
  - Restores flawless execution for REST API provider tests (e.g. `test_21_rest_api_provider_crud_lifecycle`) without requiring PYTHONPATH workarounds.
- **Phase 11: Platform-Aware Mount Path Canonicalization & Container Isolation** (`backend/execution/runner_contract.py`):
  - Added host `"/home"` to `_FORBIDDEN_MOUNTS` along with `"/root"`, preventing container escaped access to host user home directories.
  - Hardened `check_mount_safety()` with multi-stage platform-aware canonicalization:
    - Normalizes slashes, strips Windows NT object/device namespaces (`//./pipe/`, `\\.\pipe\`) and extended prefixes (`//?/`, `\\?\`).
    - Resolves symlinks and NTFS junction points via `os.path.realpath`.
    - Normalizes relative parent directory traversal (`/..`) and strips Windows drive prefixes (`C:`, `D:`).
    - Performs case-insensitive matching (`.lower()`) against forbidden mounts, blocking case variations (`/ETC`, `/Proc`, `/SYS`).
    - Preserves non-colliding path prefixes (`/etcetera`, `/device_data`, `/workspace`).
- **Phase 11: Enterprise UTC Modernization** (`backend/account_recovery.py`, `backend/db/sqlalchemy_models.py`):
  - Completely eradicated remaining deprecated `datetime.datetime.utcnow()` calls across the entire codebase (zero occurrences remaining).
  - Modernized `account_recovery.py` to use `datetime.datetime.now(datetime.timezone.utc)` for password reset and email verification token lifecycles.
  - Added seamless backwards compatibility for legacy naive ISO timestamp strings in SQLite tokens.
  - Updated SQLAlchemy ORM models in `sqlalchemy_models.py` to use a timezone-aware `_utc_now` callable default for all timestamp columns.
- **Phase 11 Hardening Test Suite** (`tests/integration/test_phase11_hardening.py`):
  - 19 invariant integration tests covering INV-P11-APP-01 through INV-P11-APP-03, INV-P11-MOUNT-01 through INV-P11-MOUNT-09, and INV-P11-UTC-01 through INV-P11-UTC-07.
  - Full repository test pass count: 738 (575 integration + 163 CLI) with zero regressions, zero failures, and zero warnings under `-W error::DeprecationWarning`.

### Remediation (Post-Audit Cycle)
- **FINDING-P11-01 Resolved (Module Identity Duality & Double Initialization)**:
  - Eliminated dual module registration between `app` and `backend.app` via bidirectional `sys.modules` aliasing and package-level binding in `app.py` and `backend/app.py`.
  - Added process-level singleton protection (`_AETHERION_BACKEND_APP_INITIALIZED`) in `backend/app.py`, ensuring exactly one application instance and preventing redundant startup validation.
  - Added `backend/__init__.py` to establish explicit package boundary.
  - Verified identical object and module identities across all permutations of import order (`app -> backend.app` and `backend.app -> app`) and `sys.path` configuration.
- **FINDING-P11-02 Resolved (Bare Root & Drive Roots Mount Policy)**:
  - Added `"/"` to `_FORBIDDEN_MOUNTS` and implemented explicit boundary checks in `check_mount_safety()` for bare root (`"/"`) and Windows drive roots (`"C:/"`, `"C:\\"`, `"C:"`, `"D:/"`).
- **FINDING-P11-04 Resolved (Pytest Collection Reliability & Standalone Invocation)**:
  - Added authoritative `pytest.ini` with `pythonpath = . backend cli`, `testpaths = tests/integration cli/tests`, `norecursedirs`, and `asyncio_default_fixture_loop_scope = function`.
  - Added `BASE_DIR` bootstrapping to `test_phase10_hitl_lifecycle.py`, `test_phase10_replay_engine_store.py`, and `test_phase10_runner_contract.py`.
  - Validated standalone `pytest` invocation passing 738/738 tests cleanly without extra flags.

## [Phase 10] — Certified Baseline

### Added
- **Phase 10: Human-in-the-Loop (HITL) Gate & Durable Task Approval Integration** (`backend/execution/hitl.py`, `backend/routes/tasks.py`):
  - Implemented `resolve_gate(execution_id: str, approved: bool, comments: str = "") -> bool` in `execution/hitl.py`.
  - Updated `provide_approval` in `HITLEventTracker` to persist approvals to `sessions.db` with connection lifecycle management via `contextlib.closing`, returning success whether unblocking in-memory threads or recording asynchronous approvals.
  - Wired `/api/task/<tid>/approve` to transition durable execution state (`WAITING_FOR_APPROVAL` / `AWAITING_APPROVAL` → `EXECUTING` on approval, → `CANCELLED` on rejection) and safely resolve HITL gates.
  - Added test suite `tests/integration/test_phase10_hitl_lifecycle.py` covering 12 lifecycle invariants (INV-P10-HITL-01 through INV-P10-HITL-12).
- **Phase 10: Isolated Execution Runner Contract & Security Test Suite** (`backend/execution/runner_contract.py`):
  - Added comprehensive test suite `tests/integration/test_phase10_runner_contract.py` covering 14 security and execution invariants (INV-P10-RUN-01 through INV-P10-RUN-14).
  - Validated mount safety (`_FORBIDDEN_MOUNTS` rejection, subpath traversal protection), credential redaction (Bearer tokens, API keys, passwords), fail-closed production mode (`RunnerDeniedError` on local/unapproved backends, `RunnerUnavailableError` when backend unreachable), and status capability matrix.
- **Phase 10: ReplayEngine Store Integration Test Suite** (`backend/execution/replay.py`):
  - Addressed Phase 9 audit residual finding FINDING-P9-02 by creating `tests/integration/test_phase10_replay_engine_store.py` covering 13 invariants (INV-P10-REP-01 through INV-P10-REP-13).
  - Validated `ReplayEngine` projections over an `ExecutionStore` adapter across timeline pagination (`limit`/`offset`), kinds filtering, frame retrieval, seek addressing by sequence and timestamp, diff calculation and order normalization, transcript aggregation, schema-valid portable bundle export, and error resilience.

### Changed
- **Python 3.12+/3.14 Datetime Deprecation Resolution** (`backend/auth_system.py`):
  - Migrated deprecated `datetime.datetime.utcnow()` to standard `datetime.datetime.now(datetime.timezone.utc)` and `time.time()`.
  - Added timezone awareness handling to `refresh_access_token` for seamless comparison across legacy naive and modern timezone-aware timestamps.
  - Eliminated all 44 pytest deprecation warnings from repository test executions.

## [Phase 9] — Certified Baseline


### Added
- **Phase 9: Verifier-Backed Task Completion Status Machine** (`execution/replay.py`):
  - Formal three-way task terminal status distinction:
    - `completed` — task finished without any verifier involvement (unverified terminal).
    - `verifying` — verifier started but outcome not yet resolved (non-terminal; can transition).
    - `succeeded` — verifier explicitly passed; `verifier_backed=True` (verified terminal).
  - `ReplayState.verifier_backed` and `verifier_started` flags fully wired into the fold.
  - `verifier.passed` promotes `"completed"` or `"verifying"` → `"succeeded"` but cannot
    resurrect `"cancelled"`, `"failed"`, or `"dead_lettered"` (terminal latch preserved).
  - `"verifying"` is excluded from `_TERMINAL_STATUSES`; `"completed"` and `"succeeded"` are included.
- **Phase 9 Integration Test Suite** (`tests/integration/test_phase9_verifier_lifecycle.py`):
  - 46 invariant tests covering INV-P9-01 through INV-P9-20.
  - Exercises `_apply()` fold, `_fold()` pipeline, `ReplayState.snapshot()`, and
    `durable_state.TRANSITIONS` graph edges for `VERIFYING → SUCCEEDED/COMPLETED/REPAIRING/FAILED/CANCELLED`
    and `EXECUTING → VERIFYING`.
  - All 46 tests pass with zero regressions against the 634-test Phase 1–8 certified baseline.

## [1.0.0] - 2026-09-10

### Added
- **Unified CLI & TUI**:
  - Converged command structure: `aetherion version`, `doctor`, `config`, `auth`, `workspace`, `task`, `logs`, `diff`, `artifacts`, `tui`.
  - Rich interactive terminal UI powered by Textual.
  - Standardized structured JSON output envelopes (`{"ok": bool, "data": ..., "correlation_id": ...}`).
  - Built-in sensitive credential redaction for DSNs, Bearer tokens, and JWTs.
- **RFC 8628 Device Authorization**:
  - Secure browser-based terminal authentication (`aetherion auth login`).
  - Terminal displays 8-character user code; browser authenticates and returns credentials via OS keyring.
- **Minimal Standalone Auth Portal**:
  - Dedicated, lightweight Vite + React + Tailwind application (`frontend-auth/`).
  - Login, register, device code confirmation, and account recovery.
  - Zero dashboard or admin clutter.
- **Hardened Fail-Closed Production Backend**:
  - Strict environment modes (`local-development`, `CI-test`, `staging`, `production`).
  - Mandatory PostgreSQL and Redis in production; fallback SQLite allowed only in local-dev.
  - Durable state management (`durable_state.py`) with cancel-wins task semantics.
- **Deployment Manifests**:
  - Multi-stage non-root Dockerfiles for backend and frontend-auth.
  - Production Docker Compose stack (`deploy/compose/docker-compose.yml`).
  - Production Kubernetes manifests (`deploy/k8s/`).
- **Operational Scripts**:
  - Production preflight validator (`scripts/production_preflight.py`).
  - Database schema migration (`scripts/db_migrate.py`).
  - Database backup and restore verification (`scripts/db_backup.py`, `scripts/db_restore_verify.py`).
