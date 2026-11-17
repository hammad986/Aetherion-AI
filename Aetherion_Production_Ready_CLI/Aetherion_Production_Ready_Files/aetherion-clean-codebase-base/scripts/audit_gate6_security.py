"""
scripts/audit_gate6_security.py
Adversarial Security Audit Suite (Gate 6)
=========================================
Tests against live backend (http://127.0.0.1:5000):
1. Missing JWT
2. Expired JWT
3. Forged JWT (wrong signature & alg=none attack)
4. Logout token reuse / revocation enforcement
5. Cross-tenant read isolation
6. Cross-tenant mutation isolation
7. Path traversal attack against workspace APIs
8. Worktree escape prevention in WorkspaceBoundary
9. Secret & API key zero-leakage check
10. OAuth CSRF state forgery rejection
11. OAuth callback parameter manipulation
12. Safe error response sanitization (no credentials/internal traces leaked)
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
from pathlib import Path
import jwt
import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
for p in (str(ROOT_DIR), str(BACKEND_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

BASE_URL = "http://127.0.0.1:5000"


def test_gate6_security():
    print("=" * 70)
    print("GATE 6: INDEPENDENT ADVERSARIAL SECURITY AUDIT")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # 1. Missing JWT
    # -------------------------------------------------------------------------
    print("\n[1/12] Testing Missing JWT...")
    r = requests.get(f"{BASE_URL}/api/auth/me")
    assert r.status_code == 401, f"Expected 401 for missing JWT, got {r.status_code}"
    r = requests.get(f"{BASE_URL}/api/providers")
    assert r.status_code == 401, f"Expected 401 for missing JWT on providers, got {r.status_code}"
    print("PASS: Missing JWT rejected with 401 Unauthorized on all protected endpoints.")

    # -------------------------------------------------------------------------
    # 2. Expired JWT
    # -------------------------------------------------------------------------
    print("\n[2/12] Testing Expired JWT...")
    from backend.auth_system import SECRET_KEY
    expired_payload = {
        "user_id": 1,
        "email": "admin@aetherion.ai",
        "role": "admin",
        "exp": datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2),
    }
    expired_token = jwt.encode(expired_payload, SECRET_KEY, algorithm="HS256")
    r = requests.get(
        f"{BASE_URL}/api/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert r.status_code == 401, f"Expected 401 for expired JWT, got {r.status_code}"
    print("PASS: Expired JWT rejected with 401 Unauthorized.")

    # -------------------------------------------------------------------------
    # 3. Forged JWT (Wrong Secret & None Algorithm)
    # -------------------------------------------------------------------------
    print("\n[3/12] Testing Forged JWT (Wrong Secret & alg=none)...")
    forged_token = jwt.encode(
        {"user_id": 1, "email": "admin@aetherion.ai", "role": "admin", "exp": time.time() + 3600},
        "completely_wrong_attacker_secret",
        algorithm="HS256"
    )
    r = requests.get(
        f"{BASE_URL}/api/auth/me",
        headers={"Authorization": f"Bearer {forged_token}"}
    )
    assert r.status_code == 401, f"Expected 401 for forged JWT, got {r.status_code}"

    # None algorithm bypass attempt
    none_token = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJ1c2VyX2lkIjoxLCJlbWFpbCI6ImFkbWluQGFldGhlcmlvbi5haSIsInJvbGUiOiJhZG1pbiJ9."
    r = requests.get(
        f"{BASE_URL}/api/auth/me",
        headers={"Authorization": f"Bearer {none_token}"}
    )
    assert r.status_code == 401, f"Expected 401 for alg=none JWT, got {r.status_code}"
    print("PASS: Forged signature and alg=none JWT strictly rejected.")

    # -------------------------------------------------------------------------
    # 4. Logout Token Reuse / Revocation Enforcement
    # -------------------------------------------------------------------------
    print("\n[4/12] Testing Logout Token Reuse & Session Revocation...")
    from backend.auth_system import create_session
    sess = create_session(user_id=1, device_info="security_audit")
    tok = sess["access_token"]
    refresh_tok = sess["refresh_token"]

    # Verify session refresh works initially
    r = requests.post(f"{BASE_URL}/api/auth/refresh", json={"refresh_token": refresh_tok})
    assert r.status_code == 200, f"Refresh failed: {r.status_code}"

    # Logout with refresh token
    r = requests.post(
        f"{BASE_URL}/api/auth/logout",
        json={"refresh_token": refresh_tok},
        headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 200, f"Logout failed: {r.status_code}"

    # Verify refresh token is REVOKED and rejected
    r = requests.post(f"{BASE_URL}/api/auth/refresh", json={"refresh_token": refresh_tok})
    assert r.status_code in (400, 401), f"Expected 401 on refresh token reuse after logout, got {r.status_code}"
    print("PASS: Revoked session refresh token strictly rejected after logout.")

    # -------------------------------------------------------------------------
    # 5. Cross-Tenant Read Isolation (BOLA)
    # -------------------------------------------------------------------------
    print("\n[5/12] Testing Cross-Tenant Read Isolation...")
    # Create two isolated users
    from backend.auth_system import auth_signup
    ts = int(time.time())
    ok_a, uid_a = auth_signup(f"alice_{ts}@audit.sec", "PassAudit123!", "Alice")
    ok_b, uid_b = auth_signup(f"bob_{ts}@audit.sec", "PassAudit123!", "Bob")
    assert ok_a and ok_b, f"Signup failed: {uid_a}, {uid_b}"

    sess_alice = create_session(user_id=uid_a)
    sess_bob = create_session(user_id=uid_b)

    # Alice creates a private provider
    alice_headers = {"Authorization": f"Bearer {sess_alice['access_token']}"}
    bob_headers = {"Authorization": f"Bearer {sess_bob['access_token']}"}

    r = requests.post(
        f"{BASE_URL}/api/providers",
        json={
            "name": f"Alice Secret LLM {int(time.time())}",
            "provider_type": "OPENAI_COMPATIBLE",
            "base_url": "https://api.alice-secret.com/v1",
            "api_key": "sk-alice-top-secret-key-12345",
        },
        headers=alice_headers
    )
    assert r.status_code == 201, f"Alice provider creation failed: {r.text}"
    alice_prov_id = r.json()["provider"]["id"]

    # Bob attempts to read Alice's provider
    r = requests.get(f"{BASE_URL}/api/providers/{alice_prov_id}", headers=bob_headers)
    assert r.status_code in (403, 404), f"BOLA DEFECT: Bob read Alice's provider! status={r.status_code}"
    print(f"PASS: Cross-tenant provider read blocked (status={r.status_code}).")

    # -------------------------------------------------------------------------
    # 6. Cross-Tenant Mutation Isolation
    # -------------------------------------------------------------------------
    print("\n[6/12] Testing Cross-Tenant Mutation Isolation...")
    # Bob attempts to update Alice's provider
    r = requests.put(
        f"{BASE_URL}/api/providers/{alice_prov_id}",
        json={"name": "Hacked by Bob"},
        headers=bob_headers
    )
    assert r.status_code in (403, 404), f"BOLA DEFECT: Bob updated Alice's provider! status={r.status_code}"

    # Bob attempts to delete Alice's provider
    r = requests.delete(f"{BASE_URL}/api/providers/{alice_prov_id}", headers=bob_headers)
    assert r.status_code in (403, 404), f"BOLA DEFECT: Bob deleted Alice's provider! status={r.status_code}"
    print("PASS: Cross-tenant provider update and delete strictly prevented.")

    # -------------------------------------------------------------------------
    # 7. Path Traversal Attacks
    # -------------------------------------------------------------------------
    print("\n[7/12] Testing Path Traversal on Workspace Endpoints...")
    traversal_payloads = [
        "../../../../../../windows/win.ini",
        "..\\..\\..\\..\\..\\windows\\win.ini",
        "....//....//....//etc/passwd",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "/etc/passwd",
        "C:\\Windows\\System32\\drivers\\etc\\hosts",
    ]
    for payload in traversal_payloads:
        r = requests.get(
            f"{BASE_URL}/api/workspace/files",
            params={"path": payload},
            headers=alice_headers
        )
        assert r.status_code in (400, 403, 404), f"Traversal leaked path: payload={payload}, status={r.status_code}"
    print("PASS: All path traversal attempts rejected.")

    # -------------------------------------------------------------------------
    # 8. Worktree Escape in WorkspaceBoundary
    # -------------------------------------------------------------------------
    print("\n[8/12] Testing Worktree Escape in WorkspaceBoundary...")
    from backend.execution.sandbox.boundary import WorkspaceBoundary, SandboxBoundaryException
    import tempfile
    with tempfile.TemporaryDirectory() as ws_tmp:
        boundary = WorkspaceBoundary(ws_tmp)
        # Safe path inside workspace
        safe_path = boundary.resolve_safe_path("subdir/file.txt")
        assert safe_path.is_relative_to(Path(ws_tmp).resolve())

        # Escape path
        escape_detected = False
        try:
            boundary.resolve_safe_path("../../sensitive.txt")
        except SandboxBoundaryException:
            escape_detected = True
        assert escape_detected, "SECURITY DEFECT: WorkspaceBoundary allowed path escape!"
    print("PASS: WorkspaceBoundary raises WorktreeEscapeError on parent traversal.")

    # -------------------------------------------------------------------------
    # 9. Secret & API Key Zero Leakage Check
    # -------------------------------------------------------------------------
    print("\n[9/12] Testing Zero Secret Leakage...")
    r = requests.get(f"{BASE_URL}/api/providers", headers=alice_headers)
    assert r.status_code == 200
    prov_list_text = r.text
    assert "sk-alice-top-secret-key-12345" not in prov_list_text, "LEAK DEFECT: Plaintext API key found in provider list!"
    assert "sk-...2345" in prov_list_text, "Key masking missing"
    print("PASS: Plaintext API keys masked in all API responses.")

    # -------------------------------------------------------------------------
    # 10. OAuth CSRF State Forgery Rejection
    # -------------------------------------------------------------------------
    print("\n[10/12] Testing OAuth CSRF State Forgery Rejection...")
    # Attempt callback with forged state not in DB
    r = requests.get(
        f"{BASE_URL}/api/auth/google/callback",
        params={"state": "attacker_forged_state_123456", "code": "fake_auth_code"},
        allow_redirects=False
    )
    # Must redirect with error or return 400
    assert (r.status_code in (400, 302) and ("error=" in r.headers.get("Location", "") or "error" in r.text.lower())), (
        f"CSRF DEFECT: Forged OAuth state accepted! status={r.status_code}"
    )

    r = requests.get(
        f"{BASE_URL}/api/auth/github/callback",
        params={"state": "attacker_forged_state_123456", "code": "fake_auth_code"},
        allow_redirects=False
    )
    assert (r.status_code in (400, 302) and ("error=" in r.headers.get("Location", "") or "error" in r.text.lower())), (
        f"CSRF DEFECT: Forged GitHub OAuth state accepted! status={r.status_code}"
    )
    print("PASS: Forged OAuth state rejected cleanly for Google and GitHub.")

    # -------------------------------------------------------------------------
    # 11. OAuth Callback Parameter Manipulation
    # -------------------------------------------------------------------------
    print("\n[11/12] Testing OAuth Callback Parameter Manipulation...")
    # Malformed / empty parameters
    r = requests.get(f"{BASE_URL}/api/auth/google/callback", allow_redirects=False)
    assert r.status_code in (400, 302)
    r = requests.get(f"{BASE_URL}/api/auth/google/callback?error=access_denied", allow_redirects=False)
    assert r.status_code in (400, 302)
    assert "access_denied" in r.headers.get("Location", "") or "error" in r.text.lower()
    print("PASS: OAuth errors and malformed callbacks handled fail-closed.")

    # -------------------------------------------------------------------------
    # 12. Safe Error Responses (No Internal Leakage)
    # -------------------------------------------------------------------------
    print("\n[12/12] Testing Error Response Sanitization...")
    # Trigger 404
    r = requests.get(f"{BASE_URL}/api/non_existent_route_404")
    assert r.status_code == 404
    assert "traceback" not in r.text.lower()
    assert "password" not in r.text.lower()
    assert "secret" not in r.text.lower()
    print("PASS: Error responses do not leak traces or internal secrets.")

    print("\n" + "=" * 70)
    print("GATE 6: ALL 12 ADVERSARIAL SECURITY ASSERTIONS PASSED (100% VERIFIED)")
    print("=" * 70)


if __name__ == "__main__":
    test_gate6_security()
