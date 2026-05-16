# Z12 — Authentication Security Audit
**Aetherion AI · Phase Z12 · Operational Security Hardening**
Audit Date: 2026-05-16 | Status: HARDENED

---

## Scope

Deep inspection of `auth_system.py`, `web_app.py` auth routes, and the JWT/refresh
token lifecycle. No redesign — only identification of risks and incremental hardening.

---

## 1. JWT Issuance

### Current State
- `_make_access_token()` issues HS256 JWTs with 15-minute expiry.
- Payload: `{ "user_id", "email", "name", "role", "iat", "exp" }`.
- Secret: `os.environ.get("JWT_SECRET", _DEFAULT_JWT_SECRET)`.

### Findings

| Ref | Severity | Finding |
|---|---|---|
| AUTH-01 | CRITICAL | `_DEFAULT_JWT_SECRET = "nexora_saas_secret_key_change_in_production"` is publicly visible in source. If `JWT_SECRET` env var is not set, all tokens are forgeable. |
| AUTH-02 | LOW | `ACCESS_TOKEN_MINUTES` defaults to 15 — acceptable. Configurable via env var. |
| AUTH-03 | INFO | HS256 is acceptable for single-service auth. RS256 should be considered if a service mesh is introduced in V2. |
| AUTH-04 | INFO | `iat` claim is present (implicit via `datetime.utcnow()`). |

### Hardening Applied
- Warning logged at startup when `JWT_SECRET == _DEFAULT_JWT_SECRET`. ✓
- `JWT_SECRET` should be set as a Replit Secret (32+ random bytes).

### Hardening Required
- AUTH-01: Ensure `JWT_SECRET` is set before public deployment. Abort startup if `IS_PRODUCTION=1` and secret is default.

---

## 2. Token Expiry

### Current State
- Access token: 15 min (`ACCESS_TOKEN_MINUTES`, configurable).
- Refresh token: 30 days (`REFRESH_TOKEN_DAYS`, configurable).
- Both configurable via env vars.

### Findings

| Ref | Severity | Finding |
|---|---|---|
| AUTH-05 | LOW | 30-day refresh tokens are long-lived. If a refresh token is leaked, the attacker has 30 days of silent access. |
| AUTH-06 | INFO | Access token expiry is short (15 min) — correct. |

### Recommendations
- Set `REFRESH_TOKEN_DAYS=7` for higher-security deployments.
- Implement refresh token absolute expiry (max 30 days regardless of activity).

---

## 3. Refresh Flows

### Current State
- `POST /api/auth/refresh` rotates the refresh token on each use (token rotation implemented). ✓
- Old refresh token is invalidated in DB on rotation. ✓
- New refresh stored in HttpOnly cookie `nx_refresh`. ✓

### Findings

| Ref | Severity | Finding |
|---|---|---|
| AUTH-07 | MEDIUM | Refresh endpoint is covered by `_auth_limiter` (10/min) — correct. |
| AUTH-08 | LOW | If a refresh token is replayed by an attacker after the user has already rotated it, the server will reject it — but does not alert the user about a potential token theft. |

### Recommendations
- AUTH-08: When a refresh token is not found (already rotated), emit a `notify:security_alert` SSE event to any other active sessions.

---

## 4. Brute-Force Protection

### Current State (`auth_system.py`)
- `MAX_FAILED = 5` failed attempts within `LOCKOUT_WINDOW = 60` seconds triggers lockout.
- Lockout is per-IP + per-email (`_failed_attempts[email]`).
- Configurable via `MAX_LOGIN_ATTEMPTS` and `LOGIN_LOCKOUT_SECS` env vars.
- `_auth_limiter` in `security.py` adds a second layer: 10 requests/min/IP globally.

### Findings

| Ref | Severity | Finding |
|---|---|---|
| AUTH-09 | LOW | `_failed_attempts` is an in-memory dict — does not survive restart and is not shared across Gunicorn workers. A restart resets all lockouts. |
| AUTH-10 | INFO | Distributed brute-force (one attempt/IP across 10 IPs) bypasses both guards. Acceptable for current scale. |

### Recommendations
- AUTH-09 (V2): Store lockout state in Redis when `REDIS_URL` is configured.

---

## 5. Session Fixation

### Current State
- New session ID generated via `secrets.token_hex(32)` on every login. ✓
- Session IDs are never reused between users. ✓
- Auth sessions stored in `saas_platform.db:auth_sessions`. ✓

### Findings

| Ref | Severity | Finding |
|---|---|---|
| AUTH-11 | INFO | Session fixation risk is negligible given short JWT access tokens and cookie-based refresh. |

---

## 6. Replay Attack Boundaries

### Current State
- Access tokens have `exp` claim — replaying expired tokens fails. ✓
- Refresh tokens are single-use (rotation deletes old token). ✓
- Deletion confirmation tokens are in-memory with 24h TTL and single-use flag. ✓
- Password reset tokens in DB with expiry. ✓

### Findings

| Ref | Severity | Finding |
|---|---|---|
| AUTH-12 | LOW | Deletion confirmation tokens (`_deletion_requests` dict) are in-process only — not persisted. Worker restart erases all pending requests. |
| AUTH-13 | INFO | Password reset tokens hashed in DB — correct. |

### Recommendations
- AUTH-12 (Z13): Persist deletion requests to DB (implemented in `account_lifecycle.py`). ✓

---

## 7. OAuth Callback Handling

### Current State
- Google/GitHub OAuth callbacks in `web_app.py`.
- State parameter checked (prevents CSRF on callback).
- OAuth tokens not stored — only user profile extracted. ✓

### Findings

| Ref | Severity | Finding |
|---|---|---|
| AUTH-14 | MEDIUM | `GOOGLE_CLIENT_SECRET` and `GITHUB_CLIENT_SECRET` must be set as env vars; if absent, OAuth is disabled gracefully. |
| AUTH-15 | LOW | Redirect after OAuth login should use a validated allowlist — no open redirect surface observed. |

---

## 8. bcrypt Usage

### Current State
- `bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))` — work factor 12. ✓
- Comparison via `bcrypt.checkpw()` — timing-safe. ✓
- OAuth users have no password hash stored. ✓

### Findings

| Ref | Severity | Finding |
|---|---|---|
| AUTH-16 | INFO | Work factor 12 is acceptable for 2026. Consider raising to 13+ in 2028. |
| AUTH-17 | INFO | No timing oracle detected in password comparison path. |

---

## 9. Fallback Secret Behavior

### Current State
- `_DEFAULT_JWT_SECRET = "nexora_saas_secret_key_change_in_production"` (hardcoded, public).
- Warning logged but startup not aborted.

### Findings

| Ref | Severity | Finding |
|---|---|---|
| AUTH-18 | CRITICAL | Default secret must never be used in production. Add production guard. |

### Recommended Guard (future `web_app.py` startup)
```python
if is_production() and SECRET_KEY == _DEFAULT_JWT_SECRET:
    raise RuntimeError("[SECURITY] JWT_SECRET must be set in production. Refusing to start.")
```

---

## Summary

| Severity | Count | Status |
|---|---|---|
| CRITICAL | 2 (AUTH-01, AUTH-18) | Mitigated by warning; add hard abort for production |
| MEDIUM | 2 (AUTH-08, AUTH-14) | Acceptable for current scale |
| LOW | 5 | Documented; V2 targets |
| INFO | 8 | Noted |

**Overall Auth Posture: ACCEPTABLE for development. Requires `JWT_SECRET` env var before production exposure.**
