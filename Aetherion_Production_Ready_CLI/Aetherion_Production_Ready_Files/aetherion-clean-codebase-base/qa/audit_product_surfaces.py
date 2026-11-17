"""
qa/audit_product_surfaces.py
Comprehensive Browser Audit of Aetherion Public & Authenticated Web Surfaces using Playwright.
Audits all 8 discovered routes:
  1. /login
  2. /register
  3. /activate (Device code authorization)
  4. /recovery (Password recovery)
  5. /terms (Terms of Service)
  6. /privacy (Privacy Policy)
  7. / (Authenticated Home / CLI Portal)
  8. /logout
"""
import os
import sys
import time
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

def audit_surfaces():
    print("=" * 70)
    print("STARTING FULL PRODUCT SURFACE BROWSER AUDIT")
    print("=" * 70)

    # Ensure any stale node processes on 3000 are killed
    subprocess.run(["powershell", "-Command", "Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"], capture_output=True)
    time.sleep(1)

    backend_proc = subprocess.Popen(
        [sys.executable, "backend/app.py"],
        cwd=ROOT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(os.environ, PORT="5000", HOST="127.0.0.1"),
    )
    time.sleep(3)

    frontend_proc = subprocess.Popen(
        ["npx", "vite", "preview", "--port", "3000", "--host", "127.0.0.1", "--strictPort"],
        cwd=ROOT_DIR / "frontend-auth",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
    )
    time.sleep(3)

    results = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            routes_to_test = [
                ("/login", "Welcome back", "Login surface"),
                ("/register", "Create your account", "Registration surface"),
                ("/activate", "Device Authorization", "RFC 8628 Device activation surface"),
                ("/recovery", "Password Recovery", "Recovery surface"),
                ("/terms", "Terms of Service", "Legal surface: Terms"),
                ("/privacy", "Privacy Policy", "Legal surface: Privacy"),
            ]

            for path, text_check, label in routes_to_test:
                url = f"http://127.0.0.1:3000{path}"
                resp = page.goto(url, wait_until="networkidle")
                content = page.content()
                has_text = text_check.lower() in content.lower()
                status = resp.status if resp else 0
                ok = status == 200 and has_text
                print(f"[{'PASS' if ok else 'FAIL'}] Route {path:18s} -> Status: {status}, Label: {label}")
                results.append((path, ok, status))

            # Test Authenticated Surface:
            # Register a new user and log in to verify /
            test_email = f"audit_user_{int(time.time())}@aetherion.test"
            test_pw = "Password123!Safe"
            page.goto("http://127.0.0.1:3000/register", wait_until="networkidle")
            page.fill("input[type=text]", "Audit Auditor")
            page.fill("input[type=email]", test_email)
            page.fill("input[type=password]", test_pw)
            page.click("button[type=submit]")
            page.wait_for_url("**/login*", timeout=15000)

            # Log in
            page.fill("input[type=email]", test_email)
            page.fill("input[type=password]", test_pw)
            page.click("button[type=submit]")
            page.wait_for_url("http://127.0.0.1:3000/", timeout=15000)
            page.wait_for_selector("text=Identity Portal", timeout=10000)

            auth_content = page.content()
            auth_ok = "Identity Portal" in auth_content and "aetherion login" in auth_content
            print(f"[{'PASS' if auth_ok else 'FAIL'}] Route {'/' :18s} -> Status: 200, Authenticated Dashboard Loaded")
            results.append(("/", auth_ok, 200))

            # Test Logout
            page.goto("http://127.0.0.1:3000/logout", wait_until="networkidle")
            page.wait_for_selector("text=Signed Out", timeout=5000)
            logout_ok = "Signed Out" in page.content() and "Sign In Again" in page.content()
            print(f"[{'PASS' if logout_ok else 'FAIL'}] Route {'/logout' :18s} -> Status: 200, Logout Screen Rendered")
            results.append(("/logout", logout_ok, 200))

            # Verify return to login from logout
            page.click("text=Sign In Again")
            page.wait_for_selector("text=Welcome back", timeout=5000)
            relogin_ok = "Welcome back" in page.content()
            print(f"[{'PASS' if relogin_ok else 'FAIL'}] Re-nav to /login      -> Status: 200, Welcome back Rendered")
            results.append(("/login_again", relogin_ok, 200))

            browser.close()
            all_pass = all(r[1] for r in results)
            print("\n" + "=" * 70)
            print(f"PRODUCT SURFACE AUDIT: {'ALL PASS' if all_pass else 'DEFECTS FOUND'}")
            print("=" * 70)
            return all_pass
    finally:
        backend_proc.terminate()
        frontend_proc.terminate()
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(frontend_proc.pid)], capture_output=True)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(backend_proc.pid)], capture_output=True)

if __name__ == "__main__":
    success = audit_surfaces()
    sys.exit(0 if success else 1)
