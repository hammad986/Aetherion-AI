"""
backend/db/test_target_policy.py — Explicit Non-Production Test Target Enforcement
===================================================================================
Enforces strict test database isolation policies for test and audit scripts:
  1. Requires AETHERION_TEST_DATABASE_URL explicitly for test/audit entrypoints.
  2. Forbids fallback to generic DATABASE_URL or SQLite in test scripts.
  3. Rejects universal heuristics (localhost, docker, CI names alone are not proof).
  4. Requires independently configured allowlists:
       - AETHERION_TEST_ALLOWED_HOSTS (e.g. "127.0.0.1,localhost,postgres")
       - AETHERION_TEST_ALLOWED_DBS (e.g. "aetherion_test,aetherion_uat,aetherion")
  5. Fails closed if allowlists are missing, empty, or contain wildcards ('*').
  6. Fails closed if parsed host or database identity is not in the approved allowlists.
  7. Redacts credentials: error messages NEVER disclose passwords, usernames, or raw URLs.
"""
from __future__ import annotations

import os
import re
import urllib.parse
from typing import Set, Tuple


class TestTargetPolicyError(RuntimeError):
    """Raised when test database target policy validation fails."""
    pass


def get_approved_test_hosts() -> Set[str]:
    """
    Retrieve independently configured approved test hosts.
    Fails closed if AETHERION_TEST_ALLOWED_HOSTS is unset or empty.
    Wildcards ('*') are strictly forbidden.
    """
    raw = os.environ.get("AETHERION_TEST_ALLOWED_HOSTS", "").strip()
    if not raw:
        raise TestTargetPolicyError(
            "Test target policy violation: AETHERION_TEST_ALLOWED_HOSTS is not configured. "
            "An explicit, independently configured allowlist of approved test hosts is required "
            "to prevent accidental connections to non-test environments."
        )
    hosts: Set[str] = set()
    for h in raw.split(","):
        clean = h.strip().lower()
        if not clean:
            continue
        if clean == "*":
            raise TestTargetPolicyError(
                "Test target policy violation: Wildcard '*' is strictly forbidden in AETHERION_TEST_ALLOWED_HOSTS."
            )
        hosts.add(clean)
    if not hosts:
        raise TestTargetPolicyError(
            "Test target policy violation: AETHERION_TEST_ALLOWED_HOSTS contains no valid host entries."
        )
    return hosts


def get_approved_test_dbs() -> Set[str]:
    """
    Retrieve independently configured approved test database names.
    Fails closed if AETHERION_TEST_ALLOWED_DBS is unset or empty.
    Wildcards ('*') are strictly forbidden.
    """
    raw = os.environ.get("AETHERION_TEST_ALLOWED_DBS", "").strip()
    if not raw:
        raise TestTargetPolicyError(
            "Test target policy violation: AETHERION_TEST_ALLOWED_DBS is not configured. "
            "An explicit, independently configured allowlist of approved test database names is required."
        )
    dbs: Set[str] = set()
    for d in raw.split(","):
        clean = d.strip()
        if not clean:
            continue
        if clean == "*":
            raise TestTargetPolicyError(
                "Test target policy violation: Wildcard '*' is strictly forbidden in AETHERION_TEST_ALLOWED_DBS."
            )
        dbs.add(clean)
    if not dbs:
        raise TestTargetPolicyError(
            "Test target policy violation: AETHERION_TEST_ALLOWED_DBS contains no valid database name entries."
        )
    return dbs


def parse_and_validate_test_target(url: str) -> Tuple[str, str]:
    """
    Parse database target identity and validate against approved allowlists.
    Returns (hostname, db_name) on success.
    Never exposes passwords, tokens, or full connection strings in errors.
    """
    if not url or not url.strip():
        raise TestTargetPolicyError(
            "Isolated test database URL not configured. "
            "Please set AETHERION_TEST_DATABASE_URL in your environment. "
            "Generic DATABASE_URL is not accepted for test/audit scripts."
        )
    clean_url = url.strip()

    try:
        parsed = urllib.parse.urlparse(clean_url)
    except Exception:
        raise TestTargetPolicyError("Failed to parse test database URL structure safely.")

    if parsed.scheme not in ("postgresql", "postgres"):
        raise TestTargetPolicyError(
            f"Unsupported database scheme '{parsed.scheme}'. "
            "Only postgresql:// or postgres:// is supported for test targets."
        )

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise TestTargetPolicyError("Test database target policy violation: Target URL lacks a valid hostname.")

    db_name = (parsed.path or "").lstrip("/").strip()
    if not db_name:
        raise TestTargetPolicyError("Test database target policy violation: Target URL lacks a database name.")

    # Validate host against approved allowlist
    approved_hosts = get_approved_test_hosts()
    if hostname not in approved_hosts:
        raise TestTargetPolicyError(
            f"Test target policy violation: Host '{hostname}' is not in approved AETHERION_TEST_ALLOWED_HOSTS."
        )

    # Validate database name against approved allowlist
    approved_dbs = get_approved_test_dbs()
    if db_name not in approved_dbs:
        raise TestTargetPolicyError(
            f"Test target policy violation: Database '{db_name}' is not in approved AETHERION_TEST_ALLOWED_DBS."
        )

    return hostname, db_name


def validate_test_database_url(url: str) -> str:
    """Validate a given URL string against the test target policy. Returns the url if valid."""
    parse_and_validate_test_target(url)
    return url


def validate_and_get_test_database_url() -> str:
    """
    Retrieve and validate AETHERION_TEST_DATABASE_URL from environment.
    Strictly forbids generic DATABASE_URL fallback.
    Returns the validated connection string.
    """
    test_url = os.environ.get("AETHERION_TEST_DATABASE_URL", "").strip()
    if not test_url:
        raise TestTargetPolicyError(
            "Isolated test database URL not configured. "
            "Please set AETHERION_TEST_DATABASE_URL in your environment. "
            "Generic DATABASE_URL is not accepted for test/audit scripts."
        )

    validate_test_database_url(test_url)
    return test_url


# Backward-compatible alias for existing script callers
get_test_database_url = validate_and_get_test_database_url
