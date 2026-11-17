"""
tests/integration/test_phase11_hardening.py — Phase 11 Systems Hardening Integration Suite.

Invariants validated:
  INV-P11-APP-01:   Root app.py re-exports app instance from backend.app with identical identity.
  INV-P11-APP-02:   Root app.py exports create_app factory.
  INV-P11-APP-03:   from app import app resolves reliably when sys.path[0] is repository root.
  INV-P11-MOUNT-01: check_mount_safety denies uppercase and mixed-case forbidden paths (/ETC, /Proc, /SYS).
  INV-P11-MOUNT-02: check_mount_safety denies host /home and /root mounts.
  INV-P11-MOUNT-03: check_mount_safety denies Windows drive paths pointing to forbidden roots (C:/etc, C:\\proc).
  INV-P11-MOUNT-04: check_mount_safety denies relative traversal escaping sandbox bounds (../etc, ../../proc).
  INV-P11-MOUNT-05: check_mount_safety denies bare relative forbidden names (etc, etc/shadow, proc).
  INV-P11-MOUNT-06: check_mount_safety denies Windows named pipes and extended paths (\\\\.\\pipe\\docker_engine).
  INV-P11-MOUNT-07: check_mount_safety permits non-colliding prefix paths (/etcetera, /device_data, /workspace).
  INV-P11-MOUNT-08: check_mount_safety permits benign Windows drive paths (C:/workspace, C:/tmp/project).
  INV-P11-MOUNT-09: check_mount_safety detects and blocks symlinks resolving to forbidden targets.
  INV-P11-UTC-01:   Password reset token generates UTC-aware expiration and verifies successfully.
  INV-P11-UTC-02:   Password reset token with past expiration fails closed as expired.
  INV-P11-UTC-03:   Password reset verification is backwards-compatible with legacy naive ISO timestamp strings.
  INV-P11-UTC-04:   Email verification token generates UTC-aware expiration and verifies successfully.
  INV-P11-UTC-05:   Email verification token with past expiration fails closed as expired.
  INV-P11-UTC-06:   Email verification is backwards-compatible with legacy naive ISO timestamp strings.
  INV-P11-UTC-07:   SQLAlchemy models _utc_now callable produces timezone-aware UTC datetime.
"""
from __future__ import annotations

import datetime
import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

# Ensure backend and root are on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.execution.runner_contract import (
    _FORBIDDEN_MOUNTS,
    RunnerDeniedError,
    check_mount_safety,
)
import backend.account_recovery as account_recovery
import backend.db.sqlalchemy_models as sqlalchemy_models


class TestRootAppResolution(unittest.TestCase):
    """Validation of root app.py forwarding module and import resolution."""

    def test_inv_p11_app_01_root_app_parity(self):
        """INV-P11-APP-01: Root app.py exports the identical Flask application instance and module identity as backend.app."""
        import app as root_app_mod
        import backend.app as backend_app_mod
        import subprocess

        self.assertIs(root_app_mod.app, backend_app_mod.app)
        self.assertIsNotNone(root_app_mod.app)
        self.assertIs(sys.modules["app"], sys.modules["backend.app"])

        # Validate that in isolated subprocesses, bidirectional import orders
        # (app -> backend.app and backend.app -> app) produce identical module & app identities
        for code in [
            'import sys; sys.path.insert(0, "backend"); import app, backend.app; assert app is backend.app; assert app.app is backend.app.app; assert app.create_app is backend.app.create_app; print("OK")',
            'import sys; sys.path.insert(0, "backend"); import backend.app, app; assert app is backend.app; assert app.app is backend.app.app; assert app.create_app is backend.app.create_app; print("OK")',
            'import app, backend.app; assert app is backend.app; assert app.app is backend.app.app; assert app.create_app is backend.app.create_app; print("OK")',
            'import backend.app, app; assert app is backend.app; assert app.app is backend.app.app; assert app.create_app is backend.app.create_app; print("OK")',
        ]:
            res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Subprocess import check failed: {res.stderr}")
            self.assertIn("OK", res.stdout)

    def test_inv_p11_app_02_create_app_factory(self):
        """INV-P11-APP-02: Root app.py exports create_app factory."""
        import app as root_app_mod
        import backend.app as backend_app_mod

        self.assertIs(root_app_mod.create_app, backend_app_mod.create_app)
        self.assertTrue(callable(root_app_mod.create_app))

    def test_inv_p11_app_03_python_m_pytest_immunity(self):
        """INV-P11-APP-03: from app import app works when sys.path[0] is root directory."""
        # Ensure root directory is at index 0 of sys.path
        orig_sys_path = list(sys.path)
        try:
            sys.path.insert(0, str(ROOT_DIR))
            # Test direct import
            import app
            self.assertTrue(hasattr(app, "app"))
            self.assertTrue(hasattr(app.app, "test_client"))
            with app.app.test_client() as client:
                res = client.get("/")
                self.assertEqual(res.status_code, 200)
                data = res.get_json()
                self.assertTrue(data.get("ok"))
        finally:
            sys.path = orig_sys_path


class TestHardenedMountSafety(unittest.TestCase):
    """Validation of platform-aware, case-normalized, symlink-resolving mount safety."""

    def test_inv_p11_mount_01_case_variance(self):
        """INV-P11-MOUNT-01: check_mount_safety denies uppercase and mixed-case forbidden paths."""
        case_variants = [
            "/ETC",
            "/Etc",
            "/Proc",
            "/PROC",
            "/SYS",
            "/Sys",
            "/DEV",
            "/Dev",
            "/ROOT",
            "/Root",
            "/HOME",
            "/Home",
            "/VAR/RUN/DOCKER.SOCK",
            "/Var/Run/Docker.Sock",
            "/sys/fs/cgroup",
            "/Sys/Fs/Cgroup",
            "/etc/shadow",
            "/Etc/Shadow",
        ]
        for path in case_variants:
            with self.assertRaises(RunnerDeniedError, msg=f"Should deny: {path}"):
                check_mount_safety([path])

    def test_inv_p11_mount_02_home_directory_denied(self):
        """INV-P11-MOUNT-02: check_mount_safety denies host /home, /root, and bare root mounts."""
        self.assertIn("/", _FORBIDDEN_MOUNTS)
        self.assertIn("/home", _FORBIDDEN_MOUNTS)
        self.assertIn("/root", _FORBIDDEN_MOUNTS)

        for path in ["/", "/home", "/home/user", "/root", "/root/.ssh", "C:/", "C:\\", "C:"]:
            with self.assertRaises(RunnerDeniedError, msg=f"Should deny: {path}"):
                check_mount_safety([path])

    def test_inv_p11_mount_03_windows_drive_paths(self):
        """INV-P11-MOUNT-03: check_mount_safety denies Windows drive paths to forbidden roots."""
        drive_paths = [
            "C:/etc",
            r"C:\etc\passwd",
            "D:/proc",
            r"d:\sys",
            "C:/home/user",
            r"C:\root\.ssh",
            r"\\?\C:\etc\shadow",
        ]
        for path in drive_paths:
            with self.assertRaises(RunnerDeniedError, msg=f"Should deny: {path}"):
                check_mount_safety([path])

    def test_inv_p11_mount_04_relative_traversal_denied(self):
        """INV-P11-MOUNT-04: check_mount_safety denies relative traversal escaping sandbox bounds."""
        traversal_paths = [
            "/tmp/../etc",
            "/workspace/../../proc",
            "/workspace/sub/../../../var/run/docker.sock",
            "../etc",
            "../../proc",
            "../../../sys",
        ]
        for path in traversal_paths:
            with self.assertRaises(RunnerDeniedError, msg=f"Should deny: {path}"):
                check_mount_safety([path])

    def test_inv_p11_mount_05_bare_relative_forbidden_names(self):
        """INV-P11-MOUNT-05: check_mount_safety denies bare relative forbidden names."""
        relative_names = ["etc", "etc/shadow", "proc", "sys", "home", "root"]
        for path in relative_names:
            with self.assertRaises(RunnerDeniedError, msg=f"Should deny: {path}"):
                check_mount_safety([path])

    def test_inv_p11_mount_06_docker_socket_named_pipes(self):
        """INV-P11-MOUNT-06: check_mount_safety denies Windows named pipes and extended paths."""
        pipe_paths = [
            "//./pipe/docker_engine",
            r"\\.\pipe\docker_engine",
            "//./pipe/DOCKER_ENGINE",
            "/var/run/docker.sock",
            "/run/docker.sock",
        ]
        for path in pipe_paths:
            with self.assertRaises(RunnerDeniedError, msg=f"Should deny: {path}"):
                check_mount_safety([path])

    def test_inv_p11_mount_07_prefix_collision_permitted(self):
        """INV-P11-MOUNT-07: check_mount_safety permits non-colliding prefix paths."""
        benign_paths = [
            "/workspace",
            "/tmp",
            "/data/project",
            "/etcetera",
            "/device_data",
            "/rootkit_analysis",
            "/home_automation",
            "/workspace/build",
            "/tmp/sandbox_123",
        ]
        # None of these should raise RunnerDeniedError
        check_mount_safety(benign_paths)

    def test_inv_p11_mount_08_windows_drive_benign_permitted(self):
        """INV-P11-MOUNT-08: check_mount_safety permits benign Windows drive paths."""
        benign_windows_paths = [
            "C:/workspace",
            r"C:\workspace\project",
            "C:/tmp/output",
            r"D:\data\build",
        ]
        check_mount_safety(benign_windows_paths)

    def test_inv_p11_mount_09_symlink_traversal_denied(self):
        """INV-P11-MOUNT-09: check_mount_safety resolves symlinks and blocks forbidden targets."""
        with tempfile.TemporaryDirectory() as tmpdir:
            link_path = os.path.join(tmpdir, "innocent_symlink")
            try:
                # Target an existing system path (e.g. C:\Windows or /etc)
                target = "/etc" if os.name != "nt" else os.environ.get("SystemRoot", r"C:\Windows")
                # On Windows, creating symlinks might require privileges; test try/except
                os.symlink(target, link_path)
                # If symlink creation succeeded, check that check_mount_safety detects it
                if os.name != "nt":
                    with self.assertRaises(RunnerDeniedError):
                        check_mount_safety([link_path])
            except (OSError, NotImplementedError):
                # Symlink creation not permitted on this platform/privilege level; pass gracefully
                pass


class TestUTCApiModernization(unittest.TestCase):
    """Validation of UTC-aware timestamp modernization in account recovery and models."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.orig_db_path = account_recovery.DB_PATH
        account_recovery.DB_PATH = self.temp_db.name
        conn = sqlite3.connect(self.temp_db.name)
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            username TEXT UNIQUE,
            name TEXT,
            email_verified INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()
        conn.close()
        account_recovery.init_db()

    def tearDown(self):
        account_recovery.DB_PATH = self.orig_db_path
        try:
            if os.path.exists(self.temp_db.name):
                os.remove(self.temp_db.name)
        except Exception:
            pass

    def test_inv_p11_utc_01_password_reset_token_lifecycle(self):
        """INV-P11-UTC-01: Password reset token generates UTC-aware expiration and verifies successfully."""
        conn = sqlite3.connect(self.temp_db.name)
        c = conn.cursor()
        c.execute("INSERT INTO users (id, email, username) VALUES (101, 'alice@example.com', 'alice')")
        conn.commit()
        conn.close()

        ok = account_recovery.request_password_reset("alice@example.com", "http://localhost:5000")
        self.assertTrue(ok)

        # Retrieve token hash and expires_at from DB
        conn = sqlite3.connect(self.temp_db.name)
        c = conn.cursor()
        c.execute("SELECT token_hash, expires_at FROM password_resets WHERE user_id = 101")
        row = c.fetchone()
        conn.close()

        self.assertIsNotNone(row)
        tok_hash, expires_at_str = row
        dt = datetime.datetime.fromisoformat(expires_at_str)
        self.assertIsNotNone(dt.tzinfo, "Expiration timestamp must be timezone-aware UTC")
        self.assertEqual(dt.tzinfo, datetime.timezone.utc)

    def test_inv_p11_utc_02_password_reset_expired(self):
        """INV-P11-UTC-02: Password reset token with past expiration fails closed as expired."""
        conn = sqlite3.connect(self.temp_db.name)
        c = conn.cursor()
        c.execute("INSERT INTO users (id, email, username) VALUES (102, 'bob@example.com', 'bob')")
        past_utc = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat()
        token = "expired_token_12345"
        tok_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        c.execute(
            "INSERT INTO password_resets (id, user_id, token_hash, expires_at) VALUES ('r-exp', 102, ?, ?)",
            (tok_hash, past_utc),
        )
        conn.commit()
        conn.close()

        valid, msg, uid = account_recovery.verify_reset_token(token)
        self.assertFalse(valid)
        self.assertIn("expired", msg.lower())
        self.assertIsNone(uid)

    def test_inv_p11_utc_03_password_reset_legacy_naive_datetime(self):
        """INV-P11-UTC-03: Password reset verification handles legacy naive ISO timestamp strings."""
        conn = sqlite3.connect(self.temp_db.name)
        c = conn.cursor()
        c.execute("INSERT INTO users (id, email, username) VALUES (103, 'carol@example.com', 'carol')")

        # Future naive timestamp (legacy format without tzinfo)
        future_naive = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        token_valid = "valid_naive_token"
        tok_hash_valid = hashlib.sha256(token_valid.encode("utf-8")).hexdigest()
        c.execute(
            "INSERT INTO password_resets (id, user_id, token_hash, expires_at) VALUES ('r-naive-val', 103, ?, ?)",
            (tok_hash_valid, future_naive),
        )

        # Past naive timestamp
        past_naive = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        token_expired = "expired_naive_token"
        tok_hash_exp = hashlib.sha256(token_expired.encode("utf-8")).hexdigest()
        c.execute(
            "INSERT INTO password_resets (id, user_id, token_hash, expires_at) VALUES ('r-naive-exp', 103, ?, ?)",
            (tok_hash_exp, past_naive),
        )
        conn.commit()
        conn.close()

        # Valid naive token must succeed without TypeError
        valid, msg, uid = account_recovery.verify_reset_token(token_valid)
        self.assertTrue(valid, f"Expected valid, got error: {msg}")
        self.assertEqual(uid, 103)

        # Expired naive token must fail cleanly without TypeError
        valid, msg, uid = account_recovery.verify_reset_token(token_expired)
        self.assertFalse(valid)
        self.assertIn("expired", msg.lower())

    def test_inv_p11_utc_04_email_verification_token_lifecycle(self):
        """INV-P11-UTC-04: Email verification token generates UTC-aware expiration."""
        conn = sqlite3.connect(self.temp_db.name)
        c = conn.cursor()
        c.execute("INSERT INTO users (id, email, username, email_verified) VALUES (104, 'dave@example.com', 'dave', 0)")
        conn.commit()
        conn.close()

        ok = account_recovery.send_verification_email(104, "dave@example.com", "Dave", "http://localhost:5000")
        self.assertTrue(ok)

        conn = sqlite3.connect(self.temp_db.name)
        c = conn.cursor()
        c.execute("SELECT expires_at FROM email_verifications WHERE user_id = 104")
        row = c.fetchone()
        conn.close()

        self.assertIsNotNone(row)
        dt = datetime.datetime.fromisoformat(row[0])
        self.assertIsNotNone(dt.tzinfo)
        self.assertEqual(dt.tzinfo, datetime.timezone.utc)

    def test_inv_p11_utc_05_email_verification_expired(self):
        """INV-P11-UTC-05: Email verification token with past expiration fails closed as expired."""
        conn = sqlite3.connect(self.temp_db.name)
        c = conn.cursor()
        c.execute("INSERT INTO users (id, email, username, email_verified) VALUES (105, 'eve@example.com', 'eve', 0)")
        past_utc = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat()
        token = "expired_email_token"
        tok_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        c.execute(
            "INSERT INTO email_verifications (id, user_id, token_hash, expires_at) VALUES ('ev-exp', 105, ?, ?)",
            (tok_hash, past_utc),
        )
        conn.commit()
        conn.close()

        ok, msg = account_recovery.do_verify_email(token)
        self.assertFalse(ok)
        self.assertIn("expired", msg.lower())

    def test_inv_p11_utc_06_email_verification_legacy_naive_datetime(self):
        """INV-P11-UTC-06: Email verification handles legacy naive ISO timestamp strings."""
        conn = sqlite3.connect(self.temp_db.name)
        c = conn.cursor()
        c.execute("INSERT INTO users (id, email, username, email_verified) VALUES (106, 'frank@example.com', 'frank', 0)")

        future_naive = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        token_val = "val_email_naive"
        tok_hash_val = hashlib.sha256(token_val.encode("utf-8")).hexdigest()
        c.execute(
            "INSERT INTO email_verifications (id, user_id, token_hash, expires_at) VALUES ('ev-naive-val', 106, ?, ?)",
            (tok_hash_val, future_naive),
        )
        conn.commit()
        conn.close()

        ok, msg = account_recovery.do_verify_email(token_val)
        self.assertTrue(ok, f"Expected success for naive valid email token, got: {msg}")

    def test_inv_p11_utc_07_sqlalchemy_models_default_callable(self):
        """INV-P11-UTC-07: SQLAlchemy models _utc_now callable produces timezone-aware UTC datetime."""
        utc_val = sqlalchemy_models._utc_now()
        self.assertIsInstance(utc_val, datetime.datetime)
        self.assertIsNotNone(utc_val.tzinfo)
        self.assertEqual(utc_val.tzinfo, datetime.timezone.utc)


if __name__ == "__main__":
    unittest.main()
