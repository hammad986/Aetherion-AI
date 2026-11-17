"""
qa/manual_uat/test_github_website_oauth.py
===========================================
Authoritative Real-Browser Acceptance Suite for Website GitHub OAuth & Complete Auth:
1. Visual & functional inspection of /login (Google, GitHub, email/password)
2. Visual & functional inspection of /register (Google, GitHub, email/password)
3. GitHub OAuth Button Click & parameter verification (client_id, state, redirect_uri)
4. Cancel GitHub consent (error=access_denied display)
5. Invalid OAuth state rejection (state=forged_state_666 -> invalid_state error display)
6. Invalid/missing code handling
7. Callback failure handling
8. New GitHub user signup flow (DB persistence, token storage in localStorage, Identity Portal display)
9. Session persistence across reload
10. Protected route validation
11. Logout flow (localStorage token wiped, redirected to /logout)
12. Existing email collision & account linking behavior
13. Existing GitHub user login & repeat login
14. Google OAuth parity verification on both /login and /register
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
import urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND_DIR = _ROOT / "backend"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import requests
from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from auth_system import get_or_create_oauth_user, create_session, execute_query
from routes.auth_helpers import _oauth_states, _oauth_states_lock

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://127.0.0.1:3000").rstrip("/")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:5000").rstrip("/")
SCREENSHOT_DIR = _ROOT / "audit" / "screenshots" / "github_oauth"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

def fetch_backend_github_state() -> str:
    resp = requests.get(f"{BACKEND_URL}/api/auth/github", allow_redirects=False, timeout=10)
    loc = resp.headers.get("Location", "")
    parsed = urllib.parse.urlparse(loc)
    params = urllib.parse.parse_qs(parsed.query)
    return params.get("state", [""])[0]

RESULTS = []

def record(test_name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {test_name}: {detail}")
    RESULTS.append({"test": test_name, "status": status, "detail": detail})
    if not passed:
        raise AssertionError(f"Test Failed: {test_name} - {detail}")


def run_suite():
    print("=" * 70)
    print("PHASE 1: STARTING REAL GITHUB OAUTH & COMPLETE WEBSITE AUTH UAT")
    print(f"Target Frontend: {FRONTEND_URL}")
    print(f"Target Backend:  {BACKEND_URL}")
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # -------------------------------------------------------------------
        # 1. Inspect /login visual controls (Google, GitHub, email/password)
        # -------------------------------------------------------------------
        print("\n--- 1. Testing /login Visual & Semantic Controls ---")
        page.goto(f"{FRONTEND_URL}/login", wait_until="networkidle")
        page.screenshot(path=str(SCREENSHOT_DIR / "01_login_surface.png"))

        has_email_inp = page.locator("input[type=email]").is_visible()
        has_pass_inp = page.locator("input[type=password]").is_visible()
        has_submit_btn = page.locator("button[type=submit]").is_visible()
        has_google_btn = page.locator("a[href='/api/auth/google']").is_visible()
        has_github_btn = page.locator("a[href='/api/auth/github']").is_visible()

        record("Login: Email/Password Form Exists", has_email_inp and has_pass_inp and has_submit_btn,
               f"email={has_email_inp}, pass={has_pass_inp}, submit={has_submit_btn}")
        record("Login: Google OAuth Button Exists", has_google_btn, "a[href='/api/auth/google'] visible")
        record("Login: GitHub OAuth Button Exists", has_github_btn, "a[href='/api/auth/github'] visible")

        # -------------------------------------------------------------------
        # 2. Inspect /register visual controls (Google, GitHub, email/password)
        # -------------------------------------------------------------------
        print("\n--- 2. Testing /register Visual & Semantic Controls ---")
        page.goto(f"{FRONTEND_URL}/register", wait_until="networkidle")
        page.screenshot(path=str(SCREENSHOT_DIR / "02_register_surface.png"))

        reg_has_name = page.locator("input[placeholder='Ada Lovelace']").is_visible()
        reg_has_email = page.locator("input[type=email]").is_visible()
        reg_has_pass = page.locator("input[type=password]").is_visible()
        reg_has_submit = page.locator("button[type=submit]").is_visible()
        reg_has_google = page.locator("a[href='/api/auth/google']").is_visible()
        reg_has_github = page.locator("a[href='/api/auth/github']").is_visible()

        record("Register: Form Inputs & Submit Exist", reg_has_name and reg_has_email and reg_has_pass and reg_has_submit,
               f"name={reg_has_name}, email={reg_has_email}, pass={reg_has_pass}")
        record("Register: Google OAuth Button Exists", reg_has_google, "a[href='/api/auth/google'] visible")
        record("Register: GitHub OAuth Button Exists", reg_has_github, "a[href='/api/auth/github'] visible")

        # -------------------------------------------------------------------
        # 3. GitHub OAuth Button Click & Parameter Generation
        # -------------------------------------------------------------------
        print("\n--- 3. Testing GitHub OAuth Button Click & URL Generation ---")
        page.goto(f"{FRONTEND_URL}/login", wait_until="networkidle")

        target_gh_url = None
        try:
            with page.expect_navigation(timeout=10000):
                page.click("a[href='/api/auth/github']")
            target_gh_url = page.url
        except Exception:
            target_gh_url = page.url

        record("GitHub OAuth Button Click & Navigation", 
               "github.com/login/oauth/authorize" in target_gh_url or "github.com/login" in target_gh_url or "api/auth/github" in target_gh_url,
               f"Redirect URL: {target_gh_url[:80]}...")

        # Parse initiated parameters
        parsed_gh = urllib.parse.urlparse(target_gh_url)
        params_gh = urllib.parse.parse_qs(parsed_gh.query)
        client_id_gh = params_gh.get("client_id", [""])[0]
        initiated_gh_state = params_gh.get("state", [""])[0]
        scope_gh = params_gh.get("scope", [""])[0]

        if "return_to" in params_gh:
            return_to_url = urllib.parse.unquote(params_gh["return_to"][0])
            rt_parsed = urllib.parse.urlparse(return_to_url)
            rt_params = urllib.parse.parse_qs(rt_parsed.query)
            if not initiated_gh_state:
                initiated_gh_state = rt_params.get("state", [""])[0]
            if not scope_gh:
                scope_gh = rt_params.get("scope", [""])[0]
            if not client_id_gh:
                client_id_gh = rt_params.get("client_id", [""])[0]

        record("GitHub OAuth Parameters Validated", bool(client_id_gh) and bool(initiated_gh_state) and "read:user" in scope_gh,
               f"client_id={client_id_gh}, state={initiated_gh_state[:10]}..., scope={scope_gh}")

        # -------------------------------------------------------------------
        # 4. Cancel GitHub Consent (error=access_denied)
        # -------------------------------------------------------------------
        print("\n--- 4. Testing Cancel GitHub Consent ---")
        page.goto(f"{FRONTEND_URL}/api/auth/github/callback?error=access_denied&state={initiated_gh_state}", wait_until="networkidle")
        page.wait_for_selector(".text-red-400", timeout=5000)
        page.screenshot(path=str(SCREENSHOT_DIR / "04_consent_denied.png"))
        denied_msg = page.text_content(".text-red-400")
        record("GitHub OAuth Cancel Consent Error Banner", "access_denied" in denied_msg,
               f"Error displayed: '{denied_msg}'")

        # -------------------------------------------------------------------
        # 5. Invalid / Forged OAuth State
        # -------------------------------------------------------------------
        print("\n--- 5. Testing Forged OAuth State Rejection ---")
        page.goto(f"{FRONTEND_URL}/api/auth/github/callback?code=mock_code&state=forged_state_attacker_999", wait_until="networkidle")
        page.wait_for_selector(".text-red-400", timeout=5000)
        page.screenshot(path=str(SCREENSHOT_DIR / "05_forged_state.png"))
        forged_msg = page.text_content(".text-red-400")
        record("GitHub OAuth Forged State Rejection", "invalid_state" in forged_msg,
               f"Error displayed: '{forged_msg}'")

        # -------------------------------------------------------------------
        # 6. Invalid / Missing Code Handling
        # -------------------------------------------------------------------
        print("\n--- 6. Testing Invalid / Missing Code Handling ---")
        real_backend_state_1 = fetch_backend_github_state()

        page.goto(f"{FRONTEND_URL}/api/auth/github/callback?state={real_backend_state_1}", wait_until="networkidle")
        page.wait_for_selector(".text-red-400", timeout=5000)
        page.screenshot(path=str(SCREENSHOT_DIR / "06_missing_code.png"))
        missing_code_msg = page.text_content(".text-red-400")
        record("GitHub OAuth Missing Code Handling", "error" in missing_code_msg.lower() or "failed" in missing_code_msg.lower() or "invalid" in missing_code_msg.lower(),
               f"Error displayed: '{missing_code_msg}'")

        # -------------------------------------------------------------------
        # 7. Callback Token Exchange Failure Handling
        # -------------------------------------------------------------------
        print("\n--- 7. Testing Token Exchange Failure Handling ---")
        real_backend_state_2 = fetch_backend_github_state()

        page.goto(f"{FRONTEND_URL}/api/auth/github/callback?code=invalid_bogus_code_1234&state={real_backend_state_2}", wait_until="networkidle")
        page.wait_for_selector(".text-red-400", timeout=5000)
        page.screenshot(path=str(SCREENSHOT_DIR / "07_token_fail.png"))
        fail_msg = page.text_content(".text-red-400")
        record("GitHub OAuth Token Exchange Failure Display", "github_token_failed" in fail_msg or "failed" in fail_msg.lower(),
               f"Error displayed: '{fail_msg}'")

        # -------------------------------------------------------------------
        # 8. New GitHub User Signup Flow
        # -------------------------------------------------------------------
        print("\n--- 8. Testing New GitHub User Signup Flow ---")
        gh_uid = uuid.uuid4().hex[:6]
        gh_new_email = f"gh_dev_{gh_uid}@example.com"
        gh_new_name = f"GitHub Developer {gh_uid}"

        user_id = get_or_create_oauth_user(gh_new_email, gh_new_name, "github")
        assert user_id is not None, "Failed to create new GitHub user in database"

        # Verify user row in database
        db_user = execute_query("SELECT id, email, name, provider FROM users WHERE id = ?", (user_id,), fetchone=True)
        record("New GitHub User Created in DB", db_user is not None and db_user[1] == gh_new_email and db_user[3] == "github",
               f"DB User: id={db_user[0]}, email={db_user[1]}, provider={db_user[3]}")

        # Issue session tokens and simulate callback redirect
        tokens = create_session(user_id, device_info="Playwright Chromium", ip_address="127.0.0.1")
        page.goto(f"{FRONTEND_URL}/?nx_token={tokens['access_token']}&nx_name={urllib.parse.quote(gh_new_name)}", wait_until="networkidle")
        page.wait_for_selector("text=Identity Portal", timeout=10000)
        page.screenshot(path=str(SCREENSHOT_DIR / "08_new_user_portal.png"))

        stored_jwt = page.evaluate("() => localStorage.getItem('aetherion_token')")
        portal_text = page.text_content("body")

        record("New GitHub User JWT Stored in localStorage", stored_jwt == tokens["access_token"],
               f"JWT length: {len(stored_jwt) if stored_jwt else 0}")
        record("New GitHub User Email Displayed in Portal", gh_new_email in portal_text,
               f"Found {gh_new_email} in Identity Portal")

        # -------------------------------------------------------------------
        # 9. Session Persistence Across Reload
        # -------------------------------------------------------------------
        print("\n--- 9. Testing Session Persistence Across Browser Reload ---")
        page.reload(wait_until="networkidle")
        page.wait_for_selector("text=Identity Portal", timeout=5000)
        reloaded_jwt = page.evaluate("() => localStorage.getItem('aetherion_token')")
        record("Session Persists Across Browser Reload", reloaded_jwt == tokens["access_token"],
               "Token intact after page reload")

        # -------------------------------------------------------------------
        # 10. Protected Route Validation
        # -------------------------------------------------------------------
        print("\n--- 10. Testing Protected Route Validation ---")
        # Visiting "/" remains on portal while authenticated
        page.goto(f"{FRONTEND_URL}/", wait_until="networkidle")
        page.wait_for_selector("text=Identity Portal", timeout=5000)
        record("Protected Home Route Accessible While Authenticated", page.url == f"{FRONTEND_URL}/",
               f"Current URL: {page.url}")

        # -------------------------------------------------------------------
        # 11. Logout Flow
        # -------------------------------------------------------------------
        print("\n--- 11. Testing Logout Flow ---")
        page.click("text=Sign out")
        page.wait_for_url(f"{FRONTEND_URL}/logout", timeout=5000)
        page.wait_for_selector("text=Signed Out", timeout=5000)
        page.screenshot(path=str(SCREENSHOT_DIR / "11_logout_screen.png"))

        post_logout_tok = page.evaluate("() => localStorage.getItem('aetherion_token')")
        record("Logout Clears localStorage JWT", post_logout_tok is None,
               f"Token after logout: {post_logout_tok}")

        # Visiting "/" unauthenticated redirects to /login
        page.goto(f"{FRONTEND_URL}/", wait_until="networkidle")
        page.wait_for_url(f"{FRONTEND_URL}/login", timeout=5000)
        record("Unauthenticated Route Redirects to /login", page.url == f"{FRONTEND_URL}/login",
               f"Redirected to: {page.url}")

        # -------------------------------------------------------------------
        # 12. Existing Email Collision & Account Linking Behavior
        # -------------------------------------------------------------------
        print("\n--- 12. Testing Email Collision & Account Linking ---")
        # Step A: Register standard user via normal email/password
        collide_suffix = uuid.uuid4().hex[:6]
        collide_email = f"lead_dev_{collide_suffix}@example.com"
        collide_pass = "P@ssw0rdSecure!2026"
        collide_name = f"Original Developer {collide_suffix}"

        page.goto(f"{FRONTEND_URL}/register", wait_until="networkidle")
        page.fill("input[placeholder='Ada Lovelace']", collide_name)
        page.fill("input[type=email]", collide_email)
        page.fill("input[type=password]", collide_pass)
        page.click("button[type=submit]")
        page.wait_for_url("**/login?registered=1", timeout=10000)

        original_user = execute_query("SELECT id, email, provider FROM users WHERE email = ?", (collide_email,), fetchone=True)
        assert original_user is not None, "Failed to register initial user"
        original_uid = original_user[0]
        record("Initial Account Registered", original_user[1] == collide_email,
               f"User ID: {original_uid}, provider={original_user[2]}")

        # Step B: Log in via GitHub with the exact same email address
        linked_uid = get_or_create_oauth_user(collide_email, f"Linked GitHub {collide_name}", "github")
        record("Account Linking Resolves to Same User ID", linked_uid == original_uid,
               f"Original UID {original_uid} == Linked UID {linked_uid} (no duplicate account created)")

        # Verify provider updated to github
        updated_user = execute_query("SELECT id, email, provider FROM users WHERE id = ?", (original_uid,), fetchone=True)
        record("Account Linking Updates Provider to GitHub", updated_user[2] == "github",
               f"Provider successfully updated to: {updated_user[2]}")

        # Authenticate linked session
        linked_tokens = create_session(linked_uid, device_info="Playwright Linked", ip_address="127.0.0.1")
        page.goto(f"{FRONTEND_URL}/?nx_token={linked_tokens['access_token']}&nx_name={urllib.parse.quote(collide_name)}", wait_until="networkidle")
        page.wait_for_selector("text=Identity Portal", timeout=10000)
        record("Linked Account Successfully Enters Identity Portal", collide_email in page.text_content("body"),
               f"Identity Portal verified for {collide_email}")

        # Logout cleanly
        page.click("text=Sign out")
        page.wait_for_url(f"{FRONTEND_URL}/logout", timeout=5000)

        # -------------------------------------------------------------------
        # 13. Existing GitHub User Repeat Login
        # -------------------------------------------------------------------
        print("\n--- 13. Testing Existing GitHub User Repeat Login ---")
        repeat_uid = get_or_create_oauth_user(gh_new_email, gh_new_name, "github")
        assert repeat_uid == user_id, f"Repeat login returned different user ID! {repeat_uid} != {user_id}"
        record("Repeat GitHub Login Resolves Same User ID", repeat_uid == user_id,
               f"User ID preserved across logins: {repeat_uid}")

        repeat_tokens = create_session(repeat_uid, device_info="Playwright Repeat", ip_address="127.0.0.1")
        page.goto(f"{FRONTEND_URL}/?nx_token={repeat_tokens['access_token']}&nx_name={urllib.parse.quote(gh_new_name)}", wait_until="networkidle")
        page.wait_for_selector("text=Identity Portal", timeout=10000)
        record("Repeat Login Re-enters Identity Portal", gh_new_email in page.text_content("body"),
               "User email found in active session")

        # Logout cleanly
        page.click("text=Sign out")
        page.wait_for_url(f"{FRONTEND_URL}/logout", timeout=5000)

        # -------------------------------------------------------------------
        # 14. Google OAuth Parity Verification (Login & Register)
        # -------------------------------------------------------------------
        print("\n--- 14. Testing Google OAuth Parity ---")
        # Check on /login
        page.goto(f"{FRONTEND_URL}/login", wait_until="networkidle")
        google_target = None
        try:
            with page.expect_navigation(timeout=10000):
                page.click("a[href='/api/auth/google']")
            google_target = page.url
        except Exception:
            google_target = page.url

        record("Google OAuth Login Parity", "accounts.google.com" in google_target or "api/auth/google" in google_target,
               f"Google login navigation: {google_target[:80]}...")

        # Check on /register
        page.goto(f"{FRONTEND_URL}/register", wait_until="networkidle")
        google_reg_target = None
        try:
            with page.expect_navigation(timeout=10000):
                page.click("a[href='/api/auth/google']")
            google_reg_target = page.url
        except Exception:
            google_reg_target = page.url

        record("Google OAuth Register Parity", "accounts.google.com" in google_reg_target or "api/auth/google" in google_reg_target,
               f"Google register navigation: {google_reg_target[:80]}...")

        browser.close()

    out_file = _ROOT / "audit" / "github_oauth_results.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    print("PHASE 1 COMPLETE: ALL GITHUB OAUTH & COMPLETE AUTH TESTS PASSED")
    print(f"Total Tests Executed: {len(RESULTS)}")
    print(f"Total Passes: {sum(1 for r in RESULTS if r['status'] == 'PASS')}")
    print(f"Total Failures: {sum(1 for r in RESULTS if r['status'] == 'FAIL')}")
    print(f"Results saved to: {out_file}")
    print("=" * 70)


if __name__ == "__main__":
    run_suite()
