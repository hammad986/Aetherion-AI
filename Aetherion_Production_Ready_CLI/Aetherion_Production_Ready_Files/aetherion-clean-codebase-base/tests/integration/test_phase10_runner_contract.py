"""
tests/integration/test_phase10_runner_contract.py — Phase 10 Isolated Execution
Runner Contract Test Suite.

Invariants validated:
  INV-P10-RUN-01: check_mount_safety allows benign workspace and tmp paths.
  INV-P10-RUN-02: check_mount_safety raises RunnerDeniedError on forbidden paths / host sockets.
  INV-P10-RUN-03: redact() redacts Bearer tokens before persistence.
  INV-P10-RUN-04: redact() redacts api_key, token, password, and secret strings.
  INV-P10-RUN-05: ResourcePolicy enforces default-deny network and read-only filesystem.
  INV-P10-RUN-06: LocalSubprocessRunner is unavailable when ENVIRONMENT=production.
  INV-P10-RUN-07: LocalSubprocessRunner.submit raises RunnerDeniedError when ENVIRONMENT=production.
  INV-P10-RUN-08: get_runner('local') raises RunnerDeniedError in production mode.
  INV-P10-RUN-09: get_runner('unauthorized_backend') raises RunnerDeniedError in production mode.
  INV-P10-RUN-10: get_runner() fails closed with RunnerUnavailableError in production if backend unavailable.
  INV-P10-RUN-11: Custom runner registration via register_backend and reset_backends lifecycle.
  INV-P10-RUN-12: LocalSubprocessRunner in test mode runs python command, captures output, redacts secrets.
  INV-P10-RUN-13: LocalSubprocessRunner enforces max_time_sec and returns failure_class='timeout'.
  INV-P10-RUN-14: get_runner_status() reports complete metadata matrix and fail_closed_mode.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# ── Path bootstrap ────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from backend.execution.runner_contract import (
    APPROVED_PRODUCTION_BACKENDS,
    JobResult,
    JobSpec,
    LocalSubprocessRunner,
    ResourcePolicy,
    RunnerAdapter,
    RunnerDeniedError,
    RunnerUnavailableError,
    _FORBIDDEN_MOUNTS,
    _is_production_or_staging,
    check_mount_safety,
    get_runner,
    get_runner_status,
    redact,
    register_backend,
    reset_backends,
)


class TestRunnerContractSecurity(unittest.TestCase):
    """Security invariants: mount isolation, credential redaction, fail-closed production mode."""

    def tearDown(self):
        reset_backends()
        os.environ.pop("ENVIRONMENT", None)
        os.environ.pop("ENV", None)
        os.environ.pop("APP_ENV", None)
        os.environ.pop("AETHERION_RUNNER_BACKEND", None)

    def test_inv_p10_run_01_benign_mounts_permitted(self):
        """INV-P10-RUN-01: Benign workspace / tmp paths pass check_mount_safety."""
        check_mount_safety(["/workspace", "/tmp", "/data/project"])

    def test_inv_p10_run_02_forbidden_mounts_denied(self):
        """INV-P10-RUN-02: Forbidden host sockets or root filesystem mounts raise RunnerDeniedError."""
        for forbidden in _FORBIDDEN_MOUNTS:
            with self.assertRaises(RunnerDeniedError):
                check_mount_safety([forbidden])

        # Also subpaths under forbidden roots
        with self.assertRaises(RunnerDeniedError):
            check_mount_safety(["/etc/shadow"])
        with self.assertRaises(RunnerDeniedError):
            check_mount_safety(["/var/run/docker.sock/sub"])

    def test_inv_p10_run_03_redact_bearer_tokens(self):
        """INV-P10-RUN-03: redact() replaces Bearer tokens with <redacted>."""
        raw = "User session Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        redacted = redact(raw)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", redacted)
        self.assertIn("Bearer <redacted>", redacted)


    def test_inv_p10_run_04_redact_secrets(self):
        """INV-P10-RUN-04: redact() masks api_key, password, secret, token."""
        raw = 'Config: api_key="sk-live-1234567890abcdef" password: "supersecretpassword"'
        redacted = redact(raw)
        self.assertNotIn("sk-live-1234567890abcdef", redacted)
        self.assertNotIn("supersecretpassword", redacted)
        self.assertIn("<redacted>", redacted)

    def test_inv_p10_run_05_resource_policy_defaults(self):
        """INV-P10-RUN-05: ResourcePolicy default values are secure and bounded."""
        policy = ResourcePolicy()
        self.assertFalse(policy.network_enabled)
        self.assertTrue(policy.read_only_fs)
        self.assertEqual(policy.max_cpu, 1.0)
        self.assertEqual(policy.max_memory_mb, 512)
        self.assertEqual(policy.max_time_sec, 60)
        self.assertEqual(policy.max_processes, 64)

    def test_inv_p10_run_06_local_runner_unavailable_in_production(self):
        """INV-P10-RUN-06: LocalSubprocessRunner.is_available() is False when ENVIRONMENT=production."""
        os.environ["ENVIRONMENT"] = "production"
        self.assertTrue(_is_production_or_staging())
        runner = LocalSubprocessRunner()
        self.assertFalse(runner.is_available())

    def test_inv_p10_run_07_local_runner_submit_denied_in_production(self):
        """INV-P10-RUN-07: LocalSubprocessRunner.submit() raises RunnerDeniedError in production."""
        os.environ["ENVIRONMENT"] = "production"
        runner = LocalSubprocessRunner()
        spec = JobSpec(
            job_id="job-prod-denied",
            tenant_id="tenant-1",
            workspace_id="ws-1",
            language="python",
            command="print('unsafe')",
        )
        with self.assertRaises(RunnerDeniedError) as cm:
            runner.submit(spec)
        self.assertIn("FAIL CLOSED: LocalSubprocessRunner is strictly forbidden", str(cm.exception))

    def test_inv_p10_run_08_get_runner_local_denied_in_production(self):
        """INV-P10-RUN-08: get_runner('local') raises RunnerDeniedError in production."""
        os.environ["ENVIRONMENT"] = "production"
        with self.assertRaises(RunnerDeniedError) as cm:
            get_runner("local")
        self.assertIn("not in the approved production backends list", str(cm.exception))

    def test_inv_p10_run_09_unapproved_backend_denied_in_production(self):
        """INV-P10-RUN-09: Unapproved backend names raise RunnerDeniedError in production."""
        os.environ["ENVIRONMENT"] = "production"
        with self.assertRaises(RunnerDeniedError):
            get_runner("unauthorized_custom_backend")

    def test_inv_p10_run_10_production_fail_closed_if_unavailable(self):
        """INV-P10-RUN-10: get_runner() raises RunnerUnavailableError in production if no backend available."""
        os.environ["ENVIRONMENT"] = "production"
        os.environ["AETHERION_RUNNER_BACKEND"] = "none"
        from unittest.mock import patch
        from backend.execution.runner_contract import _BACKENDS
        with patch.object(_BACKENDS["hardened_container"], "is_available", return_value=False):
            with self.assertRaises(RunnerUnavailableError):
                get_runner()



class TestRunnerContractExecution(unittest.TestCase):
    """Execution invariants: local test execution, timeouts, custom adapter registry, and status matrix."""

    def tearDown(self):
        reset_backends()
        os.environ.pop("ENVIRONMENT", None)
        os.environ.pop("AETHERION_RUNNER_BACKEND", None)

    def test_inv_p10_run_11_register_and_reset_backends(self):
        """INV-P10-RUN-11: register_backend adds adapter; reset_backends restores default registry."""
        class DummyAdapter(RunnerAdapter):
            backend_name = "custom_test_backend"
            def is_available(self):
                return True
            def submit(self, spec):
                return JobResult(job_id=spec.job_id, ok=True, exit_code=0, stdout="dummy", stderr="")

        adapter = DummyAdapter()
        register_backend(adapter)
        runner = get_runner("custom_test_backend")
        self.assertEqual(runner.backend_name, "custom_test_backend")

        reset_backends()
        with self.assertRaises(RunnerUnavailableError):
            get_runner("custom_test_backend")

    def test_inv_p10_run_12_local_runner_execution_and_redaction(self):
        """INV-P10-RUN-12: LocalSubprocessRunner in test mode executes python code and redacts secrets."""
        # Ensure we are not in production
        os.environ.pop("ENVIRONMENT", None)
        os.environ["TESTING"] = "1"

        runner = LocalSubprocessRunner()
        self.assertTrue(runner.is_available())

        cmd = "import sys\nprint('HELLO_FROM_RUNNER')\nprint('api_key=\"secret_12345678\"')\n"
        spec = JobSpec(
            job_id="job-exec-12",
            tenant_id="tenant-1",
            workspace_id="ws-1",
            language="python",
            command=cmd,
        )
        res = runner.submit(spec)
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        self.assertIn("HELLO_FROM_RUNNER", res.stdout)
        self.assertNotIn("secret_12345678", res.stdout)
        self.assertIn("<redacted>", res.stdout)
        self.assertGreaterEqual(res.duration_ms, 0)

    def test_inv_p10_run_13_local_runner_timeout(self):
        """INV-P10-RUN-13: LocalSubprocessRunner enforces max_time_sec and returns failure_class='timeout'."""
        os.environ.pop("ENVIRONMENT", None)
        os.environ["TESTING"] = "1"

        runner = LocalSubprocessRunner()
        # Script sleeps longer than max_time_sec
        cmd = "import time\ntime.sleep(5)\nprint('DONE')\n"
        spec = JobSpec(
            job_id="job-timeout-13",
            tenant_id="tenant-1",
            workspace_id="ws-1",
            language="python",
            command=cmd,
            resource=ResourcePolicy(max_time_sec=1),
        )
        res = runner.submit(spec)
        self.assertFalse(res.ok)
        self.assertEqual(res.exit_code, -1)
        self.assertEqual(res.failure_class, "timeout")
        self.assertIn("Timed out after 1s", res.stderr)

    def test_inv_p10_run_14_get_runner_status_matrix(self):
        """INV-P10-RUN-14: get_runner_status() returns status dictionary with complete backend matrix."""
        status = get_runner_status()
        self.assertIn("active_backend", status)
        self.assertIn("available", status)
        self.assertIn("isolated", status)
        self.assertIn("degraded", status)
        self.assertIn("fail_closed_mode", status)
        self.assertIn("backends", status)

        # Check default backends are in the matrix
        backends = status["backends"]
        self.assertIn("hardened_container", backends)
        self.assertIn("docker", backends)
        self.assertIn("gvisor", backends)
        self.assertIn("runsc", backends)
        self.assertIn("kata", backends)
        self.assertIn("firecracker", backends)
        self.assertIn("local", backends)


if __name__ == "__main__":
    unittest.main()
