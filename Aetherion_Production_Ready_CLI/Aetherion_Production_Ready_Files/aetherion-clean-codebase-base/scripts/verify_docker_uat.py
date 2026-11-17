"""
scripts/verify_docker_uat.py — Automated Live Docker UAT Test Runner
===================================================================
Executes HTTP smoke tests against the live Docker container at http://127.0.0.1:5000
Validates:
  - System health probes (/api/health, /livez, /readyz)
  - Authentication (signup, login, me with Bearer JWT)
  - API Key issuance and authentication (X-API-Key)
  - API Key privilege boundaries (rejection of key revocation via X-API-Key)
  - API Key revocation via JWT and subsequent denial
"""
import sys
import time
import uuid
import requests

BASE_URL = "http://127.0.0.1:5000"

def log(msg):
    print(f"[DOCKER-UAT] {msg}")

def run_tests():
    session = requests.Session()
    synthetic_id = uuid.uuid4().hex[:8]
    test_email = f"uat_{synthetic_id}@example.com"
    test_password = f"P@ssword123_{synthetic_id}!"
    test_name = f"UAT User {synthetic_id}"

    results = []

    def check(name, condition, detail=""):
        if condition:
            log(f"PASS: {name}")
            results.append((name, True, detail))
        else:
            log(f"FAIL: {name} - {detail}")
            results.append((name, False, detail))
            raise RuntimeError(f"Test assertion failed: {name} ({detail})")

    # 1. Health checks
    log("Probing system health endpoints...")
    r = session.get(f"{BASE_URL}/api/health", timeout=10)
    check("GET /api/health returns 200", r.status_code == 200, f"status={r.status_code}, body={r.text}")

    r = session.get(f"{BASE_URL}/livez", timeout=10)
    check("GET /livez returns 200", r.status_code == 200, f"status={r.status_code}")

    r = session.get(f"{BASE_URL}/readyz", timeout=10)
    check("GET /readyz returns 200", r.status_code == 200, f"status={r.status_code}")

    # 2. Signup
    log(f"Registering synthetic user: {test_email}...")
    r = session.post(
        f"{BASE_URL}/api/auth/signup",
        json={"email": test_email, "password": test_password, "name": test_name},
        timeout=10,
    )
    check("POST /api/auth/signup returns 200/201", r.status_code in (200, 201), f"status={r.status_code}, body={r.text}")
    signup_data = r.json()
    token = signup_data.get("token") or signup_data.get("access_token")

    # 3. Login
    log("Logging in with synthetic user credentials...")
    r = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": test_email, "password": test_password},
        timeout=10,
    )
    check("POST /api/auth/login returns 200", r.status_code == 200, f"status={r.status_code}")
    login_data = r.json()
    token = login_data.get("token") or login_data.get("access_token") or token
    check("JWT token present in login response", bool(token), "token acquired")

    # 4. Auth Me with JWT
    log("Fetching user profile with Bearer JWT...")
    r = session.get(
        f"{BASE_URL}/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    check("GET /api/auth/me returns 200 with JWT", r.status_code == 200, f"status={r.status_code}")
    me_data = r.json()
    check("Profile email matches synthetic user", me_data.get("email") == test_email or me_data.get("user", {}).get("email") == test_email, f"body={me_data}")

    # 5. Issue API Key via JWT
    log("Issuing personal API key via JWT...")
    r = session.post(
        f"{BASE_URL}/api/auth/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"label": "UAT Container Test Key"},
        timeout=10,
    )
    check("POST /api/auth/api-keys returns 200/201", r.status_code in (200, 201), f"status={r.status_code}")
    key_data = r.json()
    key_obj = key_data.get("api_key") if isinstance(key_data.get("api_key"), dict) else key_data
    api_key = key_obj.get("raw_key") or key_obj.get("key") or key_obj.get("api_key")
    key_id = key_obj.get("id") or key_obj.get("key_id")
    check("API key raw string returned", bool(api_key), f"key_data={key_data}")
    check("API key ID returned", bool(key_id), f"key_data={key_data}")

    # 6. Authenticate with X-API-Key
    log("Verifying /api/auth/me using X-API-Key...")
    r = session.get(
        f"{BASE_URL}/api/auth/me",
        headers={"X-API-Key": api_key},
        timeout=10,
    )
    check("GET /api/auth/me returns 200 with X-API-Key", r.status_code == 200, f"status={r.status_code}")

    # 7. Privilege boundary: Key revocation using X-API-Key must be DENIED (403)
    log("Testing privilege boundary: attempting key deletion using X-API-Key...")
    r = session.delete(
        f"{BASE_URL}/api/auth/api-keys/{key_id}",
        headers={"X-API-Key": api_key},
        timeout=10,
    )
    check("DELETE /api/auth/api-keys/:id rejected with X-API-Key (403)", r.status_code == 403, f"status={r.status_code}")

    # 8. Revoke key using valid JWT
    log("Revoking API key using Bearer JWT...")
    r = session.delete(
        f"{BASE_URL}/api/auth/api-keys/{key_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    check("DELETE /api/auth/api-keys/:id succeeds with JWT (200)", r.status_code == 200, f"status={r.status_code}")

    # 9. Verify revoked API key is rejected
    log("Verifying revoked API key is rejected...")
    r = session.get(
        f"{BASE_URL}/api/auth/me",
        headers={"X-API-Key": api_key},
        timeout=10,
    )
    check("GET /api/auth/me returns 401 with revoked key", r.status_code == 401, f"status={r.status_code}")

    log(f"All {len(results)} live Docker UAT tests passed successfully!")
    return results

if __name__ == "__main__":
    try:
        run_tests()
    except Exception as e:
        print(f"\n[ERROR] UAT test suite encountered an error: {e}")
        sys.exit(1)
