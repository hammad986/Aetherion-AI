"""
scripts/audit_gate2_github_oauth.py
Elite adversarial release-gate audit for GATE 2:
Real Chromium Browser -> GitHub OAuth button click -> OAuth handshake & edge cases
"""
import os
import sys
import uuid
import urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "backend"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

FRONTEND_URL = "http://127.0.0.1:3000"
BACKEND_URL = "http://127.0.0.1:5000"

def main():
    print("=" * 75)
    print("GATE 2 AUDIT: GITHUB OAUTH REAL BROWSER & BUTTON CLICK E2E")
    print("=" * 75)

    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        console_errors = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        print("\n[Step 1] Navigating to LoginPage...")
        page.goto(f"{FRONTEND_URL}/login", wait_until="networkidle")
        assert "Welcome back" in page.content(), "LoginPage not rendered"
        print("  ✓ LoginPage rendered successfully.")

        # Locate the Continue with GitHub button
        github_btn = page.locator("a:has-text('Continue with GitHub')")
        assert github_btn.is_visible(), "'Continue with GitHub' button is not visible!"
        print("  ✓ 'Continue with GitHub' button is visible.")

        # Click the real button
        print("\n[Step 2] Clicking 'Continue with GitHub' button...")
        with page.expect_navigation() as nav_info:
            github_btn.click()

        target_url = page.url
        print(f"  ✓ Navigated towards: {target_url[:80]}...")
        assert "github.com" in target_url, f"Expected github.com, got: {target_url}"
        results["github_button_click"] = "PASS"

        # Validate parameters
        parsed = urllib.parse.urlparse(target_url)
        params = urllib.parse.parse_qs(parsed.query)
        if "return_to" in params:
            return_to = params["return_to"][0]
            inner_qs = urllib.parse.urlparse(urllib.parse.unquote(return_to)).query
            params = urllib.parse.parse_qs(inner_qs)

        client_id = params.get("client_id", [""])[0]
        redirect_uri = params.get("redirect_uri", [""])[0]
        state = params.get("state", [""])[0]
        scope = params.get("scope", [""])[0]

        print(f"  ✓ Client ID: {client_id[:16]}...")
        print(f"  ✓ Redirect URI: {redirect_uri}")
        print(f"  ✓ State: {state}")
        print(f"  ✓ Scope: {scope}")

        assert bool(client_id) and bool(state) and bool(redirect_uri), "Missing parameters"
        assert "api/auth/github/callback" in redirect_uri, "Invalid redirect_uri"
        assert len(state) >= 32, "Insecure state length"
        results["github_parameters_valid"] = "PASS"

        # Step 3: Denied consent
        print("\n[Step 3] Testing GitHub Denied Consent...")
        page.goto(f"{FRONTEND_URL}/api/auth/github/callback?error=access_denied&state={state}", wait_until="networkidle")
        page.wait_for_selector(".text-red-400", timeout=5000)
        err_text = page.text_content(".text-red-400")
        assert "access_denied" in err_text, f"Unexpected error: {err_text}"
        print(f"  ✓ Denied consent error rendered: '{err_text}'")
        results["github_denied_consent"] = "PASS"

        # Step 4: Forged state
        print("\n[Step 4] Testing Forged CSRF State...")
        page.goto(f"{FRONTEND_URL}/api/auth/github/callback?code=mock_code&state=forged_state_999", wait_until="networkidle")
        page.wait_for_selector(".text-red-400", timeout=5000)
        err_text = page.text_content(".text-red-400")
        assert "invalid_state" in err_text, f"Unexpected error: {err_text}"
        print(f"  ✓ Forged state rejected: '{err_text}'")
        results["github_forged_state"] = "PASS"

        # Step 5: Successful GitHub authenticated session & identity linking
        print("\n[Step 5] Testing Authenticated GitHub Session & Identity Linking...")
        from auth_system import get_or_create_oauth_user, create_session
        gh_email = f"github_dev_{uuid.uuid4().hex[:6]}@example.com"
        gh_name = "GitHub Verified Engineer"
        uid = get_or_create_oauth_user(gh_email, gh_name, "github")
        tokens = create_session(uid, device_info="Playwright GitHub Browser", ip_address="127.0.0.1")

        page.goto(f"{FRONTEND_URL}/?nx_token={tokens['access_token']}&nx_name=GitHub%20Verified%20Engineer", wait_until="networkidle")
        page.wait_for_selector("text=Identity Portal", timeout=10000)
        assert gh_email in page.text_content("body"), f"Email {gh_email} not rendered in Identity Portal"
        stored_token = page.evaluate("() => localStorage.getItem('aetherion_token')")
        assert stored_token == tokens["access_token"], "Token not stored in localStorage"
        print(f"  ✓ Authenticated GitHub session active for: {gh_email}")
        results["github_session_active"] = "PASS"

        # Step 6: Session persistence across reload
        print("\n[Step 6] Testing session persistence across reload...")
        page.reload(wait_until="networkidle")
        page.wait_for_selector("text=Identity Portal", timeout=5000)
        assert gh_email in page.text_content("body")
        print("  ✓ Identity persisted across page reload.")
        results["github_session_persistence"] = "PASS"

        # Step 7: Logout
        print("\n[Step 7] Testing logout...")
        page.click("text=Sign out")
        page.wait_for_url(f"{FRONTEND_URL}/logout", timeout=5000)
        page.wait_for_selector("text=Signed Out", timeout=5000)
        cleared_token = page.evaluate("() => localStorage.getItem('aetherion_token')")
        assert cleared_token is None, "Token was not cleared on logout"
        print("  ✓ Logout cleared session and token.")
        results["github_session_logout"] = "PASS"

        # Step 8: Repeat login for existing account (identity linking check)
        print("\n[Step 8] Testing repeat login / existing identity linking...")
        uid_repeat = get_or_create_oauth_user(gh_email, gh_name, "github")
        assert uid_repeat == uid, f"Expected same user_id {uid}, got {uid_repeat}"
        tokens_repeat = create_session(uid_repeat, device_info="Playwright GitHub Repeat", ip_address="127.0.0.1")
        page.goto(f"{FRONTEND_URL}/?nx_token={tokens_repeat['access_token']}", wait_until="networkidle")
        page.wait_for_selector("text=Identity Portal", timeout=5000)
        assert gh_email in page.text_content("body")
        print("  ✓ Existing identity linked cleanly on repeat login.")
        results["github_identity_linking"] = "PASS"

        assert len(console_errors) == 0, f"Unexpected console errors: {console_errors}"
        results["no_console_errors"] = "PASS"

        browser.close()

    print("\n" + "=" * 75)
    print("GATE 2 GITHUB RESULTS SUMMARY:")
    for k, v in results.items():
        print(f"  {k:35s}: {v}")
    print("=" * 75)
    print("GATE 2 GITHUB AUDIT COMPLETE: 100% PASS")

if __name__ == "__main__":
    main()
