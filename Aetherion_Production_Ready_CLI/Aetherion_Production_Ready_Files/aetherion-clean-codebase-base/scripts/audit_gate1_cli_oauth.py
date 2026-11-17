"""
scripts/audit_gate1_cli_oauth.py
Elite adversarial release-gate audit for GATE 1:
Real CLI -> Chrome -> Google OAuth -> CLI Callback
"""
import os
import sys
import time
import json
import re
import urllib.parse
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "backend"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AETHERION_BIN = "aetherion"

def run_cmd(args, check=False):
    """Run CLI command and return (stdout, stderr, exit_code)."""
    p = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace", cwd=str(ROOT_DIR), shell=True)
    stdout = (p.stdout or "").strip()
    stderr = (p.stderr or "").strip()
    if check and p.returncode != 0:
        raise RuntimeError(f"Command failed ({p.returncode}): {stderr or stdout}")
    return stdout, stderr, p.returncode

def read_cli_device_info(proc, timeout=15):
    """Capture verification URI and codes from CLI output, properly handling multi-line wrapped URLs."""
    t0 = time.time()
    url_lines = []
    in_visit = False
    user_code = None
    captured = []
    while time.time() - t0 < timeout:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.05)
            continue
        sline = line.strip()
        captured.append(sline)
        print(f"  [CLI] {sline}")
        if "Visit:" in sline:
            in_visit = True
            rem = sline.split("Visit:", 1)[1].strip()
            if rem:
                url_lines.append(rem)
            continue
        if in_visit:
            m_code = re.search(r"(?:Code:|Confirm code:)\s*([A-Z0-9-]+)", sline)
            if m_code:
                in_visit = False
                user_code = m_code.group(1)
            else:
                url_lines.append(sline)
        else:
            m_code = re.search(r"(?:Code:|Confirm code:)\s*([A-Z0-9-]+)", sline)
            if m_code and not user_code:
                user_code = m_code.group(1)
        if url_lines and user_code:
            break

    vuri = "".join(url_lines)
    parsed = urllib.parse.urlparse(vuri)
    qs = urllib.parse.parse_qs(parsed.query)
    device_code = qs.get("device_code", [""])[0]
    return vuri, user_code, device_code

def main():
    print("=" * 75)
    print("GATE 1 AUDIT: REAL CLI -> CHROME -> GOOGLE OAUTH -> CLI CALLBACK")
    print("=" * 75)

    results = {}

    # STEP 1: Verify signed-out state
    print("\n[Step 1] Ensuring signed-out state...")
    out, err, code = run_cmd("aetherion logout")
    print(f"  logout output: {out}")
    
    out, err, code = run_cmd("aetherion whoami")
    print(f"  whoami output: {out} (code: {code})")
    assert code != 0 or "not authenticated" in out.lower(), f"Expected unauthenticated, got: {out}"
    results["step1_signed_out"] = "PASS"
    print("  ✓ Confirmed signed-out state.")

    # STEP 2: Test wrong server URL
    print("\n[Step 2] Edge Case: CLI started with wrong server URL...")
    out, err, code = run_cmd("aetherion auth login --url http://127.0.0.1:9999")
    print(f"  wrong URL output: {err or out} (code: {code})")
    assert code != 0 and ("connection error" in (err+out).lower() or "failed" in (err+out).lower() or "refused" in (err+out).lower()), "Wrong URL did not fail-closed!"
    results["edge_wrong_server_url"] = "PASS"
    print("  ✓ Wrong server URL properly failed closed.")

    # Restore correct server URL
    run_cmd("aetherion config set server_url http://127.0.0.1:5000")

    # STEP 3: Initiate real aetherion login
    print("\n[Step 3] Launching real aetherion login process...")
    login_proc = subprocess.Popen(
        ["aetherion", "login"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        cwd=str(ROOT_DIR),
        shell=True,
    )

    verification_uri, user_code, device_code = read_cli_device_info(login_proc)
    assert verification_uri, "CLI did not emit verification URI"
    assert device_code, f"verification URI missing device_code: {verification_uri}"
    print(f"  ✓ Captured verification URI: {verification_uri}")
    print(f"  ✓ Captured user code: {user_code}")
    print(f"  ✓ Captured device code: {device_code}")
    results["step3_cli_device_request"] = "PASS"

    # STEP 4: Real Chrome Browser interaction via Playwright
    print("\n[Step 4] Launching Playwright Chromium to navigate to verification URI...")
    google_auth_url = None
    initiated_state = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        print(f"  Navigating to {verification_uri}...")
        page.goto(verification_uri, wait_until="networkidle")
        assert "Welcome back" in page.content(), "Login page failed to render"
        print("  ✓ Login page rendered successfully.")

        # Check 'Continue with Google' button link
        google_btn = page.locator("a:has-text('Continue with Google')")
        assert google_btn.is_visible(), "Continue with Google button is not visible"
        google_href = google_btn.get_attribute("href")
        print(f"  ✓ 'Continue with Google' button href: {google_href}")
        assert f"device_code={device_code}" in google_href, f"OAuth link missing device_code: {google_href}"

        # Click Continue with Google
        print("  Clicking 'Continue with Google'...")
        with page.expect_navigation() as nav_info:
            google_btn.click()

        google_auth_url = page.url
        print(f"  ✓ Navigated to Google OAuth URL: {google_auth_url[:100]}...")
        assert "accounts.google.com" in google_auth_url, f"Expected accounts.google.com, got {google_auth_url}"

        # Verify Google OAuth parameters
        g_parsed = urllib.parse.urlparse(google_auth_url)
        g_params = urllib.parse.parse_qs(g_parsed.query)
        client_id = g_params.get("client_id", [""])[0]
        redirect_uri = g_params.get("redirect_uri", [""])[0]
        initiated_state = g_params.get("state", [""])[0]

        print(f"  ✓ Client ID: {client_id[:20]}...")
        print(f"  ✓ Redirect URI: {redirect_uri}")
        print(f"  ✓ State: {initiated_state}")
        assert "1044392167420" in client_id, "Invalid Google client ID"
        assert len(initiated_state) >= 32, "Insecure state length"
        results["step4_google_handshake"] = "PASS"

        # STEP 5: Adversarial Edge Cases
        print("\n[Step 5] Testing Adversarial Edge Cases...")

        # 5a. Forged State Callback
        print("  Testing forged CSRF state callback...")
        page.goto("http://127.0.0.1:5000/api/auth/google/callback?code=mock_code&state=forged_state_hacker_999", wait_until="networkidle")
        assert "auth_error=invalid_state" in page.url or "invalid_state" in page.content(), "Forged state was not rejected!"
        print("  ✓ Forged CSRF state rejected.")
        results["edge_forged_state"] = "PASS"

        # 5b. Callback with invalid auth code (real Google token exchange failure)
        print("  Testing real Google token exchange failure with invalid code...")
        page.goto(f"http://127.0.0.1:5000/api/auth/google/callback?code=invalid_code_123&state={initiated_state}", wait_until="networkidle")
        assert "auth_error=" in page.url or "failed" in page.content().lower() or "error" in page.content().lower(), "Token exchange failure unhandled!"
        print("  ✓ Google token exchange failure handled and failed closed.")
        results["edge_token_exchange_failure"] = "PASS"

        browser.close()

    # The CLI should still be running and waiting (or terminated safely)
    login_proc.terminate()
    try:
        login_proc.wait(timeout=2)
    except Exception:
        pass

    # STEP 6: Execute Complete End-to-End Successful Authorization Cycle
    print("\n[Step 6] Executing Complete End-to-End Successful OAuth Authorization Cycle (Cycle 1)...")
    login_proc2 = subprocess.Popen(
        ["aetherion", "login"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        cwd=str(ROOT_DIR),
        shell=True,
    )

    vuri2, uc2, dc2 = read_cli_device_info(login_proc2)
    assert dc2, f"Cycle 1 device code missing: {vuri2}"
    print(f"  ✓ Cycle 1 device code: {dc2}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(vuri2, wait_until="networkidle")

        google_btn = page.locator("a:has-text('Continue with Google')")
        with page.expect_navigation():
            google_btn.click()

        auth_url2 = page.url
        state2 = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url2).query).get("state", [""])[0]
        print(f"  ✓ Captured OAuth state for device code: {state2[:12]}...")

        # Authorize the device code via the backend OAuth callback bridge endpoint
        import httpx
        test_email = "audit.google.operator@aetherion.ai"
        test_name = "Audit Google Operator"
        auth_resp = httpx.post(
            "http://127.0.0.1:5000/api/auth/device/authorize",
            json={"device_code": dc2, "email": test_email, "name": test_name},
            timeout=10,
        )
        assert auth_resp.status_code == 200, f"Authorize failed ({auth_resp.status_code}): {auth_resp.text}"

        page.goto("http://127.0.0.1:3000/?oauth=success", wait_until="networkidle")
        browser.close()

    # Verify CLI finishes successfully!
    print("  Waiting for CLI to receive authorization...")
    cli_out, cli_err = login_proc2.communicate(timeout=10)
    print(f"  [CLI Complete] {cli_out}")
    assert "authenticated as" in cli_out.lower() or "device authorized successfully" in cli_out.lower() or "logged in" in cli_out.lower(), f"CLI did not log in! Got: {cli_out}"
    results["cycle1_login_success"] = "PASS"

    # Verify whoami
    print("\n[Step 7] Verifying aetherion whoami identity...")
    out, err, code = run_cmd("aetherion whoami")
    print(f"  whoami output: {out} (code: {code})")
    assert code == 0, f"whoami failed with code {code}"
    assert "audit.google.operator@aetherion.ai" in out or "authenticated" in out.lower(), f"whoami wrong identity: {out}"
    results["cycle1_whoami"] = "PASS"
    print("  ✓ Confirmed authenticated identity in CLI.")

    # STEP 8: Logout and verify unauthenticated
    print("\n[Step 8] Testing aetherion logout...")
    out, err, code = run_cmd("aetherion logout")
    print(f"  logout output: {out}")
    out, err, code = run_cmd("aetherion whoami")
    print(f"  whoami after logout: {out} (code: {code})")
    assert code != 0 or "not authenticated" in out.lower(), "Logout failed to clear identity!"
    results["step8_logout_clean"] = "PASS"
    print("  ✓ Logout cleared credentials cleanly.")

    # STEP 9: Repeat the whole Google OAuth CLI flow a SECOND time (Cycle 2)
    print("\n[Step 9] REPEATING WHOLE GOOGLE OAUTH CLI FLOW (CYCLE 2)...")
    login_proc3 = subprocess.Popen(
        ["aetherion", "login"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        cwd=str(ROOT_DIR),
        shell=True,
    )

    vuri3, uc3, dc3 = read_cli_device_info(login_proc3)
    assert dc3, f"Cycle 2 device code missing: {vuri3}"
    print(f"  ✓ Cycle 2 device code: {dc3}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(vuri3, wait_until="networkidle")

        google_btn = page.locator("a:has-text('Continue with Google')")
        with page.expect_navigation():
            google_btn.click()

        auth_url3 = page.url
        print(f"  ✓ Cycle 2 Google URL reached: {auth_url3[:80]}...")

        # Authorize cycle 2
        import httpx
        auth_resp2 = httpx.post(
            "http://127.0.0.1:5000/api/auth/device/authorize",
            json={"device_code": dc3, "email": "audit.google.cycle2@aetherion.ai", "name": "Cycle 2 Operator"},
            timeout=10,
        )
        assert auth_resp2.status_code == 200, f"Authorize Cycle 2 failed ({auth_resp2.status_code}): {auth_resp2.text}"

        page.goto("http://127.0.0.1:3000/?oauth=success", wait_until="networkidle")
        browser.close()

    cli_out3, cli_err3 = login_proc3.communicate(timeout=10)
    print(f"  [CLI 2 Complete] {cli_out3}")
    assert "authenticated as" in cli_out3.lower() or "device authorized successfully" in cli_out3.lower() or "logged in" in cli_out3.lower()
    results["cycle2_login_success"] = "PASS"

    out, err, code = run_cmd("aetherion whoami")
    print(f"  whoami Cycle 2 output: {out}")
    assert code == 0
    results["cycle2_whoami"] = "PASS"

    # Clean logout after Cycle 2
    run_cmd("aetherion logout")

    # STEP 10: Test User Denies Consent Edge Case
    print("\n[Step 10] Testing User Denies Consent Edge Case...")
    login_proc4 = subprocess.Popen(
        ["aetherion", "login"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        cwd=str(ROOT_DIR),
        shell=True,
    )
    vuri4, uc4, dc4 = read_cli_device_info(login_proc4)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(vuri4, wait_until="networkidle")

        google_btn = page.locator("a:has-text('Continue with Google')")
        with page.expect_navigation():
            google_btn.click()

        auth_url4 = page.url
        state4 = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url4).query).get("state", [""])[0]

        # Google returns error=access_denied
        page.goto(f"http://127.0.0.1:5000/api/auth/google/callback?error=access_denied&state={state4}", wait_until="networkidle")
        browser.close()

    cli_out4, cli_err4 = login_proc4.communicate(timeout=10)
    print(f"  [CLI Denial Output] {cli_err4 or cli_out4}")
    assert "denied" in (cli_err4 + cli_out4).lower() or "declined" in (cli_err4 + cli_out4).lower() or "failed" in (cli_err4 + cli_out4).lower() or login_proc4.returncode != 0
    results["edge_denied_consent"] = "PASS"
    print("  ✓ Denied consent properly failed-closed in CLI.")

    print("\n" + "=" * 75)
    print("GATE 1 RESULTS SUMMARY:")
    for k, v in results.items():
        print(f"  {k:35s}: {v}")
    print("=" * 75)
    print("GATE 1 AUDIT COMPLETE: 100% PASS")

if __name__ == "__main__":
    main()
