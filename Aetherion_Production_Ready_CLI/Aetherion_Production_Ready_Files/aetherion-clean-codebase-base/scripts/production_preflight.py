#!/usr/bin/env python3
"""
scripts/production_preflight.py — Runtime verification hook

Two modes, both non-destructive:

  --config     Validate configuration only. No running server needed.
               Delegates to core/startup_validator.py, so CI and the container
               entrypoint enforce exactly one rule set.

  --runtime    Probe a running instance: /livez, /readyz, /startupz, /metrics.
               Verifies the contract an orchestrator depends on, including that
               liveness does NOT depend on the database (a DB outage must not
               restart-loop the fleet).

Exit status is the point: 0 = safe to route traffic, 1 = do not promote. Use it
as a deploy gate, a post-rollout smoke test, or a scheduled canary.

    python scripts/production_preflight.py --config
    python scripts/production_preflight.py --runtime --base-url http://localhost:5000
    python scripts/production_preflight.py --all
"""

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Runnable from anywhere: `python scripts/production_preflight.py` must work
# without the caller having exported PYTHONPATH.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

DEFAULT_BASE_URL = "http://localhost:5000"
TIMEOUT = 10

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

_results: list[tuple[str, str, str]] = []


def record(status: str, check: str, detail: str = "") -> None:
    _results.append((status, check, detail))
    icon = {PASS: "[PASS]", FAIL: "[FAIL]", WARN: "[WARN]"}[status]
    line = f"  {icon} {check}"
    if detail:
        line += f" — {detail}"
    print(line, flush=True)


# ── Config mode ──────────────────────────────────────────────────────────────

def _load_preflight():
    backend_dir = os.path.join(_REPO_ROOT, "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    try:
        from core.preflight import run_preflight
        return run_preflight
    except Exception:
        path = os.path.join(backend_dir, "core", "preflight.py")
        if not os.path.exists(path):
            path = os.path.join(_REPO_ROOT, "core", "preflight.py")
        if not os.path.exists(path):
            return None
        spec = importlib.util.spec_from_file_location("_aetherion_core_preflight", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return getattr(module, "run_preflight", None)


def _load_validator():
    """Load core/startup_validator.py directly from its path."""
    path = os.path.join(_REPO_ROOT, "backend", "core", "startup_validator.py")
    if not os.path.exists(path):
        path = os.path.join(_REPO_ROOT, "core", "startup_validator.py")
    spec = importlib.util.spec_from_file_location("_aetherion_startup_validator", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_startup


def check_config() -> None:
    """Run the authoritative preflight trust kernel in-process."""
    print("\n== Configuration ==")
    try:
        run_preflight = _load_preflight()
        if run_preflight is not None:
            report = run_preflight(probe_live=False)
            for check in report.checks:
                status = check.status.value
                detail = check.detail
                if check.remediation and status == "FAIL":
                    detail += f" [Hint: {check.remediation}]"
                record(status, check.name, detail)
            return
    except Exception as exc:
        record(WARN, "preflight kernel load", f"Falling back to startup validator: {exc}")

    try:
        validate_startup = _load_validator()
        validate_startup()
        record(PASS, "startup validation", "all gates passed")
    except SystemExit as exc:
        if exc.code:
            record(FAIL, "startup validation", "gate rejected the configuration (see BLOCK lines above)")
        else:
            record(PASS, "startup validation", "all gates passed")
    except Exception as exc:
        record(FAIL, "startup validation", f"{type(exc).__name__}: {exc}")


# ── Runtime mode ─────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = TIMEOUT):
    """GET url. Returns (status_code, body_text). Never raises for HTTP errors —
    a 503 from /readyz is a real answer, not a transport failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "aetherion-preflight"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def wait_for_boot(base_url: str, attempts: int = 30, delay: float = 2.0) -> bool:
    """Poll /livez until the process answers. Lets this run immediately after a
    deploy without a hand-tuned sleep."""
    print(f"\n== Waiting for {base_url} ==")
    for i in range(1, attempts + 1):
        code, _ = _get(f"{base_url}/livez", timeout=5)
        if code == 200:
            record(PASS, "process reachable", f"after {i} attempt(s)")
            return True
        time.sleep(delay)
    record(FAIL, "process reachable", f"no 200 from /livez after {attempts} attempts")
    return False


def check_runtime(base_url: str) -> None:
    base_url = base_url.rstrip("/")
    if not wait_for_boot(base_url):
        return

    print("\n== Probes ==")

    # Liveness must be cheap and dependency-free.
    code, body = _get(f"{base_url}/livez")
    if code == 200:
        record(PASS, "GET /livez", "200")
        try:
            payload = json.loads(body)
            if "pid" in payload and "uptime_seconds" in payload:
                record(PASS, "liveness payload", f"pid={payload['pid']}")
            else:
                record(WARN, "liveness payload", "missing pid/uptime fields")
        except json.JSONDecodeError:
            record(WARN, "liveness payload", "not JSON")
    else:
        record(FAIL, "GET /livez", f"expected 200, got {code}: {body[:120]}")

    # Readiness: 200 or 503 are both valid contract responses; 404 means the
    # probe blueprint is not registered, which is the bug this script guards.
    code, body = _get(f"{base_url}/readyz")
    if code == 200:
        record(PASS, "GET /readyz", "200 ready")
        try:
            checks = json.loads(body).get("checks", {})
            for dep, info in checks.items():
                status = PASS if info.get("ok") else (FAIL if info.get("required") else WARN)
                record(status, f"dependency: {dep}", str(info.get("detail", "")))
        except json.JSONDecodeError:
            record(WARN, "readiness payload", "not JSON")
    elif code == 503:
        record(FAIL, "GET /readyz", f"503 not ready: {body[:200]}")
    elif code == 404:
        record(FAIL, "GET /readyz", "404 — probe blueprint not registered")
    else:
        record(FAIL, "GET /readyz", f"unexpected {code}: {body[:120]}")

    code, _ = _get(f"{base_url}/startupz")
    if code == 200:
        record(PASS, "GET /startupz", "200 startup complete")
    elif code == 503:
        record(WARN, "GET /startupz", "503 — still booting")
    else:
        record(FAIL, "GET /startupz", f"unexpected {code}")

    code, body = _get(f"{base_url}/metrics")
    if code == 200 and ("http_requests_total" in body or "app_request_count" in body):
        record(PASS, "GET /metrics", "Prometheus exposition served")
    elif code == 200:
        record(WARN, "GET /metrics", "200 but no known counters found")
    else:
        record(FAIL, "GET /metrics", f"expected 200, got {code}")

    # Liveness must not depend on the database. If /readyz reports the DB down
    # while /livez still answers 200, that separation is working as designed.
    code_live, _ = _get(f"{base_url}/livez")
    code_ready, _ = _get(f"{base_url}/readyz")
    if code_ready == 503 and code_live == 200:
        record(PASS, "probe separation",
               "readiness fails while liveness holds — no restart loop on dependency outage")
    elif code_ready == 200 and code_live == 200:
        record(PASS, "probe separation", "both healthy")


# ── Report ───────────────────────────────────────────────────────────────────

def summarise(as_json: bool = False) -> int:
    failures = [r for r in _results if r[0] == FAIL]
    warnings = [r for r in _results if r[0] == WARN]
    passes = [r for r in _results if r[0] == PASS]

    if as_json:
        payload = {
            "status": "NOT_READY" if failures else "READY",
            "passed_count": len(passes),
            "warnings_count": len(warnings),
            "failures_count": len(failures),
            "results": [{"status": s, "check": c, "detail": d} for s, c, d in _results],
            "blocking": [{"check": c, "detail": d} for _, c, d in failures],
            "warnings": [{"check": c, "detail": d} for _, c, d in warnings],
        }
        print(json.dumps(payload, indent=2))
        return 1 if failures else 0

    print("\n" + "=" * 62)
    print(f"  {len(passes)} passed | {len(warnings)} warnings | {len(failures)} failed")
    print("=" * 62)

    if failures:
        print("\nBLOCKING:")
        for _, check, detail in failures:
            print(f"  - {check}: {detail}")
        print("\nRESULT: NOT PRODUCTION READY\n")
        return 1

    if warnings:
        print("\nNon-blocking warnings:")
        for _, check, detail in warnings:
            print(f"  - {check}: {detail}")

    print("\nRESULT: PRODUCTION READY\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Aetherion production preflight")
    parser.add_argument("--config", action="store_true", help="validate configuration only")
    parser.add_argument("--runtime", action="store_true", help="probe a running instance")
    parser.add_argument("--all", action="store_true", help="run both modes")
    parser.add_argument("--json", action="store_true", help="output results as JSON")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="instance base URL")
    args = parser.parse_args()

    if not (args.config or args.runtime or args.all):
        parser.error("choose --config, --runtime, or --all")

    if not args.json:
        print("=" * 62)
        print("  Aetherion AI — Production Preflight")
        print("=" * 62)

    if args.config or args.all:
        check_config()
    if args.runtime or args.all:
        check_runtime(args.base_url)

    return summarise(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
