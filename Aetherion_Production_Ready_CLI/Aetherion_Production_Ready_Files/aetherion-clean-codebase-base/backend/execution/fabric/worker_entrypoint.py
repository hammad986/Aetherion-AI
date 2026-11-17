"""
backend/execution/fabric/worker_entrypoint.py — Production Worker Entrypoint & Preflight Contract.

Authoritative entrypoint for Execution Fabric container and process workers.
Enforces the Phase 23 worker startup contract:
1. Environment identity & secrets validation
2. Database connectivity & schema readiness
3. Redis coordination reachability
4. Control Plane & Execution Fabric readiness
5. Fail-fast non-zero exit on required check failure
6. Clean runtime initialization with signal handling and graceful drain
"""

from __future__ import annotations

import os
import sys

# Remove script directory if present at sys.path[0] to avoid shadowing standard library 'logging'
_current_dir = os.path.dirname(os.path.abspath(__file__))
if sys.path and os.path.abspath(sys.path[0]) == _current_dir:
    sys.path.pop(0)

_backend_dir = os.path.abspath(os.path.join(_current_dir, "..", ".."))
_repo_dir = os.path.abspath(os.path.join(_backend_dir, ".."))
_cli_dir = os.path.join(_repo_dir, "cli")

for p in (_backend_dir, _repo_dir, _cli_dir):
    if p not in sys.path and os.path.exists(p):
        sys.path.insert(0, p)

import argparse
import logging
import signal
import time
from typing import List, Optional, Set

try:
    from core.environment import is_fail_closed_environment, is_production
    from core.preflight import (
        CheckResult,
        CheckSeverity,
        CheckStatus,
        check_control_plane,
        check_database,
        check_environment_identity,
        check_redis,
    )
except ImportError:
    from backend.core.environment import is_fail_closed_environment, is_production
    from backend.core.preflight import (
        CheckResult,
        CheckSeverity,
        CheckStatus,
        check_control_plane,
        check_database,
        check_environment_identity,
        check_redis,
    )

try:
    from execution.fabric.models import WorkerStatus
    from execution.fabric.worker_runtime import WorkerRuntime
except ImportError:
    from backend.execution.fabric.models import WorkerStatus
    from backend.execution.fabric.worker_runtime import WorkerRuntime

logger = logging.getLogger("aetherion.fabric.worker_entrypoint")


def run_worker_preflight(probe_live: bool = True) -> tuple[bool, List[CheckResult]]:
    """
    Execute authoritative worker preflight checks.
    Returns (all_required_passed, list_of_results).
    """
    checks: List[CheckResult] = []

    # 1. Environment identity
    try:
        checks.append(check_environment_identity())
    except Exception as exc:
        checks.append(CheckResult(
            check_id="config.environment",
            name="Environment Identity",
            status=CheckStatus.FAIL,
            severity=CheckSeverity.CRITICAL,
            required=True,
            detail=f"Environment identity check threw: {exc}",
        ))

    # 2. Database readiness
    try:
        checks.append(check_database(probe_live=probe_live))
    except Exception as exc:
        checks.append(CheckResult(
            check_id="database.readiness",
            name="Database Readiness",
            status=CheckStatus.FAIL,
            severity=CheckSeverity.CRITICAL,
            required=True,
            detail=f"Database preflight check threw: {exc}",
        ))

    # 3. Redis reachability
    try:
        checks.append(check_redis(probe_live=probe_live))
    except Exception as exc:
        checks.append(CheckResult(
            check_id="redis.coordination",
            name="Redis Coordination",
            status=CheckStatus.WARN,
            severity=CheckSeverity.LOW,
            required=False,
            detail=f"Redis check threw: {exc}",
        ))

    # 4. Control Plane Fabric check
    try:
        checks.append(check_control_plane(probe_live=probe_live))
    except Exception as exc:
        checks.append(CheckResult(
            check_id="control_plane.fabric",
            name="Control Plane Fabric",
            status=CheckStatus.FAIL,
            severity=CheckSeverity.CRITICAL,
            required=True,
            detail=f"Control plane check threw: {exc}",
        ))

    # Fail closed evaluation: required checks must not be FAIL
    passed = not any(c.required and c.status == CheckStatus.FAIL for c in checks)
    return passed, checks


def main(argv: Optional[List[str]] = None) -> int:
    """Worker entrypoint CLI entry."""
    parser = argparse.ArgumentParser(description="Aetherion Execution Fabric Worker Entrypoint")
    parser.add_argument("--worker-id", default=os.getenv("WORKER_ID", ""), help="Unique worker ID")
    parser.add_argument(
        "--max-concurrency", "-c",
        type=int,
        default=int(os.getenv("MAX_CONCURRENCY", "4")),
        help="Max concurrency slots",
    )
    parser.add_argument(
        "--capabilities",
        default=os.getenv("CAPABILITIES", "DEFAULT,CPU"),
        help="Comma-separated capability tags",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("TIMEOUT")) if os.getenv("TIMEOUT") else None,
        help="Worker lifetime timeout in seconds",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Execute preflight checks only and exit (used for container HEALTHCHECK)",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Bypass preflight (testing only)",
    )

    args = parser.parse_args(argv)

    # Logging setup
    log_level = logging.INFO
    if os.getenv("DEBUG") == "1":
        log_level = logging.DEBUG
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    )

    # 1. Execute Preflight
    if not args.skip_preflight:
        logger.info("Executing worker startup preflight contract...")
        passed, checks = run_worker_preflight(probe_live=True)

        for c in checks:
            prefix = "[PASS]" if c.status == CheckStatus.PASS else (
                "[WARN]" if c.status == CheckStatus.WARN else "[FAIL]"
            )
            req_str = " (REQUIRED)" if c.required else " (OPTIONAL)"
            logger.info("Preflight %s %s%s: %s", prefix, c.check_id, req_str, c.detail)

        if args.check_only:
            logger.info("Check-only mode requested. Preflight verdict: %s", "READY" if passed else "NOT READY")
            return 0 if passed else 1

        if not passed:
            logger.critical("Worker preflight contract FAILED. Aborting startup (fail-closed).")
            return 1
    else:
        if args.check_only:
            return 0

    # 2. Parse capabilities
    caps: Set[str] = {c.strip() for c in args.capabilities.split(",") if c.strip()}
    if not caps:
        caps = {"DEFAULT"}

    worker_id = args.worker_id.strip() if args.worker_id else None

    logger.info(
        "Initializing WorkerRuntime (WorkerID: %s, Concurrency: %d, Caps: %s)...",
        worker_id or "<auto>",
        args.max_concurrency,
        sorted(list(caps)),
    )

    # 3. Instantiate and run WorkerRuntime
    try:
        runtime = WorkerRuntime(
            worker_id=worker_id,
            max_concurrency=args.max_concurrency,
            supported_capabilities=caps,
        )
        runtime.setup_signal_handlers()

        logger.info("WorkerRuntime %s running (PID %d)...", runtime.worker_id, runtime.process_id)
        runtime.run(timeout=args.timeout)

        logger.info(
            "WorkerRuntime %s finished. Status: %s, Processed: %d items.",
            runtime.worker_id,
            runtime.status.value,
            runtime.items_processed,
        )
        return 0
    except Exception as exc:
        logger.exception("WorkerRuntime unhandled error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
