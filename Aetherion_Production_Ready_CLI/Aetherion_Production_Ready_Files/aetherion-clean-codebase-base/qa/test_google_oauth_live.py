"""
qa/test_google_oauth_live.py
Adversarial & Live Playwright browser audit of Google OAuth integration for Aetherion.
"""
import os
import sys
import time
import subprocess
import urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

def test_google_oauth_e2e():
    print("=" * 70)
    print("STARTING GOOGLE OAUTH LIVE BROWSER AUDIT")
    print("=" * 70)

    import urllib.request

    def is_port_listening(url: str) -> bool:
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=1):
                return True
        except Exception:
            try:
                with urllib.request.urlopen(url, timeout=1):
                    return True
            except Exception:
                return False

    backend_proc = None
    frontend_proc = None

    # 1. Start Backend Server if not already running
    if not is_port_listening("http://127.0.0.1:5000/api/health"):
        print("\n[Step 1] Starting backend server on http://localhost:5000...")
        backend_proc = subprocess.Popen(
            [sys.executable, "backend/app.py"],
            cwd=ROOT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(os.environ, PORT="5000", HOST="127.0.0.1"),
        )
        for _ in range(30):
            time.sleep(0.5)
            if is_port_listening("http://127.0.0.1:5000/api/health"):
                print("  Backend is ready!")
                break
    else:
        print("\n[Step 1] Backend already running on http://127.0.0.1:5000")

    # 2. Start Frontend Server if not already running
    if not is_port_listening("http://127.0.0.1:3000/login"):
        print("[Step 2] Starting frontend preview on http://localhost:3000...")
        frontend_proc = subprocess.Popen(
            ["npx", "vite", "preview", "--port", "3000", "--host", "127.0.0.1"],
            cwd=ROOT_DIR / "frontend-auth",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
        )
        for _ in range(30):
            time.sleep(0.5)
            if is_port_listening("http://127.0.0.1:3000/login"):
                print("  Frontend is ready!")
                break
    else:
        print("[Step 2] Frontend already running on http://127.0.0.1:3000")

    try:
        with sync_playwright() as p:
            print("\n[Step 3] Launching Chromium browser...")
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            # Record console logs and errors
            console_logs = []
            page.on("console", lambda m: console_logs.append(f"[{m.type}] {m.text}"))

            # Navigate to LoginPage
            print("  Navigating to http://127.0.0.1:3000/login ...")
            page.goto("http://127.0.0.1:3000/login", wait_until="networkidle")
            assert "Welcome back" in page.content(), "Login page failed to render"
            print("  Login page rendered successfully.")

            # Check Continue with Google button
            google_btn = page.locator("text=Continue with Google")
            assert google_btn.is_visible(), "'Continue with Google' button is not visible!"
            print("  'Continue with Google' button is visible.")

            # Click Continue with Google and intercept the redirect
            print("\n[Step 4] Clicking 'Continue with Google'...")
            with page.expect_navigation() as nav_info:
                google_btn.click()

            redirected_url = page.url
            print(f"  Browser redirected to: {redirected_url[:120]}...")
            assert "accounts.google.com" in redirected_url, f"Did not redirect to Google: {redirected_url}"

            # Parse Google OAuth parameters
            parsed = urllib.parse.urlparse(redirected_url)
            qs = urllib.parse.parse_qs(parsed.query)

            client_id = qs.get("client_id", [""])[0]
            redirect_uri = qs.get("redirect_uri", [""])[0]
            scope = qs.get("scope", [""])[0]
            state = qs.get("state", [""])[0]
            response_type = qs.get("response_type", [""])[0]

            print(f"  Verified client_id: {client_id[:20]}...")
            print(f"  Verified redirect_uri: {redirect_uri}")
            print(f"  Verified scope: {scope}")
            print(f"  Verified response_type: {response_type}")
            print(f"  Verified state (entropy): {len(state)} chars ({state[:8]}...)")

            assert "1044392167420" in client_id, "Wrong client_id"
            assert "callback" in redirect_uri, "Missing callback in redirect_uri"
            assert "email" in scope and "profile" in scope, "Missing required scopes"
            assert response_type == "code", "Wrong response_type"
            assert len(state) >= 32, "CSRF state token too short or insecure"

            # Check Google accounts response
            print("  Google accounts page title:", page.title())
            page_content = page.content()
            # If invalid client ID, Google displays an error banner immediately
            assert "Error 400" not in page_content, "Google reported Error 400 (Bad Client Configuration)"
            print("  Google OAuth handshake: VALID & ACTIVE")

            # Adversarial Check 1: Forged CSRF State on Callback
            print("\n[Step 5] Adversarial Check: Forged CSRF State Callback")
            cb_forged = page.goto("http://127.0.0.1:5000/api/auth/google/callback?state=forged_attacker_token&code=fake_code", wait_until="networkidle")
            print(f"  Forged callback landed at: {page.url}")
            assert "auth_error=invalid_state" in page.url or "invalid_state" in page.content(), "Forged state was NOT rejected!"
            print("  Forged CSRF state properly rejected with auth_error=invalid_state.")

            # Adversarial Check 2: Denied Consent Callback
            print("\n[Step 6] Adversarial Check: User Denied Consent Callback")
            cb_denied = page.goto("http://127.0.0.1:5000/api/auth/google/callback?error=access_denied", wait_until="networkidle")
            print(f"  Denied callback landed at: {page.url}")
            assert "auth_error=access_denied" in page.url or "access_denied" in page.content(), "Denied consent was NOT handled!"
            print("  Denied consent properly handled with auth_error=access_denied.")

            browser.close()
            print("\n" + "=" * 70)
            print("GOOGLE OAUTH AUDIT: 100% VERIFIED PASS")
            print("=" * 70)
            return True
    finally:
        print("\nStopping test servers...")
        if backend_proc is not None:
            backend_proc.terminate()
            backend_proc.wait(timeout=5)
        if frontend_proc is not None:
            frontend_proc.terminate()
            frontend_proc.wait(timeout=5)


if __name__ == "__main__":
    success = test_google_oauth_e2e()
    sys.exit(0 if success else 1)
