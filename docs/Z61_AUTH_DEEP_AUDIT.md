# Z61A — Deep Auth Audit Report

## Real Problems Found & Fixed

### CRITICAL — Refresh Tokens Stored as Plaintext (FIXED)
**File:** `auth_system.py`
**Issue:** Refresh tokens were inserted directly into `auth_sessions.refresh_token` column as plaintext 128-char hex strings. If the database file was ever read by an unauthorized party, all active sessions could be hijacked.
**Fix:** All refresh tokens are now SHA-256 hashed before storage (same pattern used by password reset tokens in `account_recovery.py`). Raw token is returned to client, hash stored in DB. Functions updated: `create_session`, `refresh_access_token`, `revoke_session`.

### VERIFIED SAFE — Password Hashing
- `bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())` — correct, with per-record random salt
- Verification uses `bcrypt.checkpw` — no timing side-channels
- Hashes stored as decoded UTF-8 strings, not raw bytes — correct

### VERIFIED SAFE — JWT Token Security
- Access tokens: 15-minute expiry (configurable via `ACCESS_TOKEN_MINUTES`)
- Signed with `JWT_SECRET` env var — now properly set in Replit Secrets (no longer using default)
- Token type field validated (`data.get("type") != "access"`) — prevents refresh tokens being used as access tokens
- `ExpiredSignatureError` and `InvalidTokenError` caught and returned as 401

### VERIFIED SAFE — Session Management
- Refresh token rotation on every use — each refresh invalidates the old hash and issues a new one
- 30-day session TTL with DB-enforced expiry check
- `revoke_all_sessions(user_id)` called on password reset — confirmed in `account_recovery.py`
- Live ban check on every protected request (DB lookup per request, not just JWT decode)

### VERIFIED SAFE — Brute-Force Protection
- In-memory sliding window: 5 failed attempts per 60 seconds per IP+identifier key
- Covers both login and lockout; clears on success
- Note: resets on server restart (acceptable for beta; recommend Redis for production)

### VERIFIED SAFE — Signup Validation
- Minimum 8-character password enforced in both backend (`auth_signup`) and frontend
- Email format validated with `@` check server-side
- Duplicate email/username returns generic 400 (no enumeration)
- Rate limiting applied via `_auth_limiter` in `security.py`
- Name field stripped but not length-limited — low risk

### VERIFIED SAFE — OAuth Flow
- `get_or_create_oauth_user` uses email as unique key with UNIQUE INDEX
- No password set for OAuth users (correct — they authenticate via provider)
- Provider field tracked for audit trail

### VERIFIED SAFE — localStorage Exposure
- Access tokens in `localStorage` — acceptable (short-lived, 15min)
- Refresh tokens in HttpOnly cookie (`nx_refresh`) — cannot be read by JS
- Cross-tab logout sync via `storage` event listener

### VERIFIED SAFE — Dev Auth Bypass
- `ALLOW_DEV_AUTH` defaults to `"0"` — bypass is off unless explicitly enabled
- Bypass only activates with `?dev=1` query param — not a production risk

## Remaining Usability Gaps
- Brute-force store is in-memory and resets on restart — acceptable for beta
- No TOTP/2FA — out of scope for Z61
- OAuth callbacks (Google, GitHub) require client credentials not yet configured — feature is present but inactive

## Remaining Trust Risks
- None critical. Refresh token plaintext storage was the only high-severity issue and has been fixed.

## Beta Auth Security Score: 8.5/10
Refresh token hashing elevates this from a medium to high confidence auth system for beta. The remaining gaps are all non-critical and expected for a beta platform.
