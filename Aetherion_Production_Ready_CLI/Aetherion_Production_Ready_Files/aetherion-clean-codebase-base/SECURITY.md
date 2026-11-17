# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |

---

## Fail-Closed Production Posture

Aetherion enforces strict security controls defined in `core/environment.py` and `core/startup_validator.py`:

1. **Independent Secrets**:
   `JWT_SECRET` and `SECRET_KEY` must be high-entropy, independent strings of at least 32 characters. If identical or default values are detected in `production`, startup aborts immediately.

2. **No Degraded Fallbacks in Production**:
   SQLite and in-memory Redis fallbacks are blocked in `production` and `staging`. The application requires PostgreSQL and Redis before serving traffic.

3. **Secure Credential Storage**:
   Tokens on developer workstations are stored in the operating system's native secure keyring (Windows Credential Manager, macOS Keychain, Linux Secret Service). On headless systems without keyring support, token files are saved with POSIX `0o600` permissions.

4. **Secret Redaction**:
   All CLI diagnostic reports, doctor runs, config exports, and logs automatically redact access tokens, authorization headers, database connection strings, and provider keys.

5. **Container Sandbox Enforcement**:
   Autonomous task runners isolate subprocess execution using container sandboxes (`runner_contract.py`).

---

## Reporting a Vulnerability

If you discover a potential security vulnerability in Aetherion, please report it via email to:
**security@aetherion.ai**

Please include:
- A description of the issue and potential impact
- Steps to reproduce or proof of concept
- Affected versions or deployment modes

We commit to acknowledging receipt within 24 hours and providing a remediation timeline within 72 hours.
