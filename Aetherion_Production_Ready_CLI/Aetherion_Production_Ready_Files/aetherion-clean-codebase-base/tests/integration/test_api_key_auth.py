"""
tests/integration/test_api_key_auth.py — Real Personal API-Key Integration Tests
================================================================================
Verifies the production database-backed personal API key lifecycle:
- Key issuance and raw token one-time display (never persisted plaintext)
- Authentication via X-API-Key and Authorization: Bearer aeth_live_...
- Immediate rejection upon revocation
- Tenant / user isolation (User B cannot access User A's data or revoke User A's key)
- Admin route enforcement (User key cannot access admin routes; Admin key can)
- Compatibility with existing JWT authentication
- Rejection of invalid / tampered keys
- Database migration validation
"""
import os
import sys
import json
import time
import pytest
from pathlib import Path

# Add backend to path
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from auth_system import (
    init_db,
    auth_signup,
    create_api_key,
    list_api_keys,
    revoke_api_key,
    verify_api_key,
    create_session,
    execute_query,
)
from app import create_app


@pytest.fixture(scope="module")
def app_client():
    """Create test client with initialized database."""
    init_db()
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture(scope="module")
def test_users(app_client):
    """Seed test regular user and admin user."""
    # User A (regular)
    u_a_email = f"user_a_{int(time.time())}@aetherion.test"
    ok, u_a_id = auth_signup(u_a_email, "Password123!", name="User Alpha")
    assert ok, f"User A signup failed: {u_a_id}"

    # User B (regular)
    u_b_email = f"user_b_{int(time.time())}@aetherion.test"
    ok, u_b_id = auth_signup(u_b_email, "Password123!", name="User Beta")
    assert ok, f"User B signup failed: {u_b_id}"

    # Admin User
    admin_email = f"admin_{int(time.time())}@aetherion.test"
    ok, admin_id = auth_signup(admin_email, "Password123!", name="Admin User")
    assert ok, f"Admin signup failed: {admin_id}"
    execute_query("UPDATE users SET role = 'admin' WHERE id = ?", (admin_id,), commit=True)

    # JWT tokens for API operations
    token_a = create_session(u_a_id)["access_token"]
    token_b = create_session(u_b_id)["access_token"]
    token_admin = create_session(admin_id)["access_token"]

    return {
        "user_a": {"id": u_a_id, "email": u_a_email, "token": token_a},
        "user_b": {"id": u_b_id, "email": u_b_email, "token": token_b},
        "admin": {"id": admin_id, "email": admin_email, "token": token_admin},
    }


def test_api_key_issuance_and_no_plaintext_persistence(test_users):
    """Verify raw key is returned at issuance and only SHA-256 hash is in DB."""
    uid = test_users["user_a"]["id"]
    ok, key_data = create_api_key(uid, "laptop-test")
    assert ok is True
    assert isinstance(key_data, dict)

    raw_key = key_data["key"]
    key_id = key_data["id"]
    prefix = key_data["prefix"]

    assert raw_key.startswith("aeth_live_")
    assert prefix.startswith("aeth_")
    assert prefix.endswith("****")

    # Inspect DB record directly
    row = execute_query(
        "SELECT key_hash, prefix FROM api_keys WHERE id = ?",
        (key_id,),
        fetchone=True,
    )
    assert row is not None
    stored_hash, stored_prefix = row
    assert stored_hash != raw_key, "Raw plaintext key was stored in the database!"
    assert len(stored_hash) == 64, "Key hash should be a 64-character SHA-256 hex string"
    assert stored_prefix == prefix


def test_api_key_authentication_via_headers(app_client, test_users):
    """Verify authentication using X-API-Key and Bearer aeth_live_..."""
    uid = test_users["user_a"]["id"]
    ok, key_data = create_api_key(uid, "ci-runner")
    assert ok is True
    raw_key = key_data["key"]

    # 1. Authenticate with X-API-Key
    resp = app_client.get("/api/auth/me", headers={"X-API-Key": raw_key})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["email"] == test_users["user_a"]["email"]

    # 2. Authenticate with Authorization: Bearer aeth_live_...
    resp2 = app_client.get("/api/auth/me", headers={"Authorization": f"Bearer {raw_key}"})
    assert resp2.status_code == 200
    body2 = resp2.get_json()
    assert body2["ok"] is True
    assert body2["email"] == test_users["user_a"]["email"]


def test_invalid_and_tampered_key_rejection(app_client):
    """Verify invalid or non-existent keys fail with HTTP 401."""
    # Random unknown key
    resp = app_client.get("/api/auth/me", headers={"X-API-Key": "aeth_live_0123456789abcdef0123456789abcdef"})
    assert resp.status_code == 401
    assert resp.get_json()["error"]["code"] == "INVALID_API_KEY"

    # Malformed key
    resp2 = app_client.get("/api/auth/me", headers={"X-API-Key": "malformed_key_123"})
    assert resp2.status_code == 401


def test_immediate_rejection_upon_revocation(app_client, test_users):
    """Verify key revocation immediately prevents subsequent API requests."""
    uid = test_users["user_a"]["id"]
    ok, key_data = create_api_key(uid, "short-lived-key")
    assert ok is True
    raw_key = key_data["key"]
    key_id = key_data["id"]

    # Verify key works before revocation
    resp_before = app_client.get("/api/auth/me", headers={"X-API-Key": raw_key})
    assert resp_before.status_code == 200

    # Revoke key
    rev_ok = revoke_api_key(uid, key_id)
    assert rev_ok is True

    # Immediate rejection
    resp_after = app_client.get("/api/auth/me", headers={"X-API-Key": raw_key})
    assert resp_after.status_code == 401
    assert resp_after.get_json()["error"]["code"] == "API_KEY_REVOKED"


def test_tenant_and_user_isolation(app_client, test_users):
    """Verify User B cannot access User A's data or revoke User A's keys."""
    uid_a = test_users["user_a"]["id"]
    uid_b = test_users["user_b"]["id"]

    # User A creates a key
    ok, key_a = create_api_key(uid_a, "user-a-secret-key")
    assert ok is True
    key_a_id = key_a["id"]

    # User B attempts to revoke User A's key via backend function
    rev_by_b = revoke_api_key(uid_b, key_a_id)
    assert rev_by_b is False, "User B was able to revoke User A's key!"

    # User B attempts to revoke User A's key via REST API
    resp_rev_api = app_client.delete(
        f"/api/auth/api-keys/{key_a_id}",
        headers={"Authorization": f"Bearer {test_users['user_b']['token']}"},
    )
    assert resp_rev_api.status_code == 404

    # Verify User A's key is still active
    resp_check = app_client.get("/api/auth/me", headers={"X-API-Key": key_a["key"]})
    assert resp_check.status_code == 200

    # User B listing keys should not see User A's keys
    resp_list_b = app_client.get(
        "/api/auth/api-keys",
        headers={"Authorization": f"Bearer {test_users['user_b']['token']}"},
    )
    assert resp_list_b.status_code == 200
    keys_b = resp_list_b.get_json()["api_keys"]
    assert not any(k["id"] == key_a_id for k in keys_b)


def test_admin_route_protection_with_api_keys(app_client, test_users):
    """Verify admin routes reject all API keys by default, while admin JWT sessions succeed."""
    # 1. Regular user API key cannot access admin route
    uid_a = test_users["user_a"]["id"]
    ok, reg_key = create_api_key(uid_a, "reg-key")
    assert ok is True

    resp_reg = app_client.post(
        "/api/memory/maintenance/backfill",
        headers={"X-API-Key": reg_key["key"]},
    )
    assert resp_reg.status_code == 403
    assert resp_reg.get_json()["error"]["code"] == "FORBIDDEN"

    # 2. Admin user API key is ALSO blocked on admin routes by default (no unrestricted admin API keys)
    admin_id = test_users["admin"]["id"]
    ok_admin, admin_key = create_api_key(admin_id, "admin-api-key")
    assert ok_admin is True

    resp_admin_key = app_client.post(
        "/api/memory/maintenance/backfill",
        headers={"X-API-Key": admin_key["key"]},
    )
    assert resp_admin_key.status_code == 403
    assert resp_admin_key.get_json()["error"]["code"] == "FORBIDDEN"
    assert "administrative endpoints" in resp_admin_key.get_json()["error"]["message"]

    # 3. Admin user JWT session CAN access admin route
    resp_admin_jwt = app_client.post(
        "/api/memory/maintenance/backfill",
        headers={"Authorization": f"Bearer {test_users['admin']['token']}"},
    )
    assert resp_admin_jwt.status_code in (200, 202)
    assert resp_admin_jwt.get_json()["ok"] is True

    # 4. Ordinary API key operations on authorized non-admin routes work fine
    resp_me = app_client.get("/api/auth/me", headers={"X-API-Key": admin_key["key"]})
    assert resp_me.status_code == 200
    assert resp_me.get_json()["email"] == test_users["admin"]["email"]


def test_api_key_cannot_create_or_modify_credentials(app_client, test_users):
    """Verify API-key authentication cannot create a privilege escalation chain."""
    admin_id = test_users["admin"]["id"]
    ok, admin_key = create_api_key(admin_id, "chain-test-key")
    assert ok is True

    # 1. API key cannot issue new API keys
    resp_create = app_client.post(
        "/api/auth/api-keys",
        headers={"X-API-Key": admin_key["key"]},
        json={"name": "escalated-key"},
    )
    assert resp_create.status_code == 403
    assert "cannot be used to issue new API keys" in resp_create.get_json()["error"]

    # 2. API key cannot revoke API keys
    resp_revoke = app_client.delete(
        f"/api/auth/api-keys/{admin_key['id']}",
        headers={"X-API-Key": admin_key["key"]},
    )
    assert resp_revoke.status_code == 403
    assert "cannot modify or revoke credentials" in resp_revoke.get_json()["error"]

    # 3. API key cannot change passwords
    resp_pwd = app_client.post(
        "/api/auth/change-password",
        headers={"X-API-Key": admin_key["key"]},
        json={"old_password": "Password123!", "new_password": "NewPassword123!"},
    )
    assert resp_pwd.status_code == 403
    assert "cannot change account passwords" in resp_pwd.get_json()["error"]


def test_jwt_auth_remains_functional(app_client, test_users):
    """Verify existing JWT authentication continues to work alongside API keys."""
    resp = app_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {test_users['user_a']['token']}"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["email"] == test_users["user_a"]["email"]


def test_database_migration_schema_status():
    """Verify scripts/db_migrate.py recognizes api_keys as a required table."""
    scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import db_migrate
    status = db_migrate.check_status()
    assert "api_keys" in status["tables"]
    assert "api_keys" not in status.get("missing_required", [])
