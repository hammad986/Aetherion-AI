import os
import sys
import json
import urllib.request
import urllib.error
import psycopg2

sys.path.insert(0, os.path.abspath("."))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_URL = "http://127.0.0.1:5000"

def make_req(path, method="GET", body=None, headers=None):
    url = f"{BACKEND_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    all_headers = {"Content-Type": "application/json"}
    if headers:
        all_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}
    except Exception as exc:
        return -1, {"error": str(exc)}

def main():
    print("=== SECURITY & TENANCY AUDIT ===")

    # 1. Unauthenticated / Invalid / Forged JWT Tests
    print("\n--- TEST 1: JWT Authentication & Tampering ---")
    
    # 1a. Missing Auth Header on protected route
    code, res = make_req("/api/auth/me")
    print(f"Missing Auth -> HTTP {code} (expect 401): {res}")
    assert code == 401, f"Expected 401, got {code}"

    # 1b. Malformed Auth Header
    code, res = make_req("/api/auth/me", headers={"Authorization": "Bearer not_a_jwt"})
    print(f"Malformed Auth -> HTTP {code} (expect 401): {res}")
    assert code == 401, f"Expected 401, got {code}"

    # 1c. Forged JWT (signed with wrong secret)
    import jwt
    import time
    forged_token = jwt.encode(
        {"user_id": 9999, "email": "hacker@evil.com", "role": "admin", "exp": time.time() + 3600},
        "wrong_secret_key_12345",
        algorithm="HS256"
    )
    code, res = make_req("/api/auth/me", headers={"Authorization": f"Bearer {forged_token}"})
    print(f"Forged Signature -> HTTP {code} (expect 401): {res}")
    assert code == 401, f"Expected 401, got {code}"

    # 1d. Expired JWT
    JWT_SECRET = "6ee0a28bb8fac83294ae22c438a82cfe671fdc62ab9725615d842e811a3a8b14"
    expired_token = jwt.encode(
        {"user_id": 1, "email": "expired@example.com", "role": "user", "exp": time.time() - 3600},
        JWT_SECRET,
        algorithm="HS256"
    )
    code, res = make_req("/api/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
    print(f"Expired JWT -> HTTP {code} (expect 401): {res}")
    assert code == 401, f"Expected 401, got {code}"

    # 2. Cross-Tenant Data Isolation Tests
    print("\n--- TEST 2: Cross-Tenant Isolation ---")
    
    # Create User Alpha
    import uuid
    email_a = f"tenant_alpha_{uuid.uuid4().hex[:6]}@example.com"
    code, reg_a = make_req("/api/auth/register", method="POST", body={"name": "Alpha", "email": email_a, "password": "Password123!"})
    token_a = reg_a["access_token"]
    user_a_id = reg_a["session_id"] # or decoded user_id

    # Create User Beta
    email_b = f"tenant_beta_{uuid.uuid4().hex[:6]}@example.com"
    code, reg_b = make_req("/api/auth/register", method="POST", body={"name": "Beta", "email": email_b, "password": "Password123!"})
    token_b = reg_b["access_token"]

    print(f"Tenant Alpha token: {token_a[:15]}...")
    print(f"Tenant Beta token: {token_b[:15]}...")

    # Tenant Alpha creates a task
    code, task_a = make_req(
        "/api/tasks",
        method="POST",
        body={"task": "Alpha confidential task", "workspace_id": "ws_alpha_1"},
        headers={"Authorization": f"Bearer {token_a}"}
    )
    print(f"Alpha created task -> HTTP {code}: {task_a}")
    task_a_id = task_a.get("task_id") or task_a.get("execution_id")

    # Tenant Beta attempts to view Alpha's task
    code, task_read_b = make_req(
        f"/api/tasks/{task_a_id}",
        method="GET",
        headers={"Authorization": f"Bearer {token_b}"}
    )
    print(f"Beta reads Alpha task -> HTTP {code}: {task_read_b}")
    assert code in (403, 404), f"Cross-tenant leak! Expected 403 or 404, got {code}: {task_read_b}"

    # Tenant Beta attempts to stop/cancel Alpha's task
    code, task_stop_b = make_req(
        f"/api/tasks/{task_a_id}/stop",
        method="POST",
        body={"reason": "Malicious cancellation"},
        headers={"Authorization": f"Bearer {token_b}"}
    )
    print(f"Beta stops Alpha task -> HTTP {code}: {task_stop_b}")
    assert task_stop_b.get("ok") is False, f"Cross-tenant mutation succeeded! {task_stop_b}"
    print("Cross-tenant task cancellation safely rejected (mutation prevented)")

    # Tenant Alpha creates a private Kanban card
    code, card_a = make_req(
        "/api/kanban/cards",
        method="POST",
        body={"title": "Alpha Secret Card", "column": "backlog"},
        headers={"Authorization": f"Bearer {token_a}"}
    )
    print(f"Alpha created Kanban card -> HTTP {code}: {card_a}")
    card_a_id = card_a.get("card", {}).get("id")

    # Tenant Beta lists Kanban cards
    code, beta_board = make_req(
        "/api/kanban/board",
        method="GET",
        headers={"Authorization": f"Bearer {token_b}"}
    )
    all_cards_b = []
    if isinstance(beta_board, dict) and "columns" in beta_board:
        for col, cards in beta_board["columns"].items():
            all_cards_b.extend(cards)
    print(f"Beta sees {len(all_cards_b)} cards. Alpha card present? {any(c.get('id') == card_a_id for c in all_cards_b)}")
    assert not any(c.get("id") == card_a_id for c in all_cards_b), "Cross-tenant Kanban leak! Alpha card visible to Beta"

    # 3. Path Traversal & Workspace Isolation Tests
    print("\n--- TEST 3: Path Traversal Containment ---")
    
    # Attempt directory traversal in worktree operations
    code, wt_trav = make_req(
        "/api/worktrees",
        method="POST",
        body={"branch": "../../traversal_test", "commit": "HEAD"},
        headers={"Authorization": f"Bearer {token_a}"}
    )
    print(f"Worktree branch traversal -> HTTP {code}: {wt_trav}")

    # Attempt directory traversal in workspace files
    code, ws_trav = make_req(
        "/api/workspace/files",
        method="GET",
        headers={"Authorization": f"Bearer {token_a}", "X-Workspace-Path": "../../../Windows/win.ini"}
    )
    print(f"Workspace path traversal -> HTTP {code}: {ws_trav}")

    print("\n=== ALL SECURITY & TENANCY AUDIT CHECKS COMPLETE ===")

if __name__ == "__main__":
    main()
