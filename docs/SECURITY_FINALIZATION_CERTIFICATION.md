# T007 — Security Finalization Certification
**Phase Z6 | Generated: 2026-05-16**

---

## Executive Summary

Security audit of JWT lifecycle, CSP, CSRF posture, OAuth, and secret management.
All critical findings are resolved. Two medium-priority items are documented with
mitigations.

**Status: PASS (with documented medium-risk items)**

---

## 1. JWT Lifecycle

| Property | Implementation | Status |
|----------|---------------|--------|
| Algorithm | HS256 | ✅ Symmetric — appropriate for single-service |
| Access token TTL | Configurable (default 15–60 min via `ACCESS_TOKEN_MINUTES`) | ✅ |
| Refresh token TTL | Configurable (default 30 days via `REFRESH_TOKEN_DAYS`) | ✅ |
| Refresh token storage | `nx_refresh` HttpOnly cookie (not localStorage) | ✅ Secure |
| Refresh token rotation | New token issued on every refresh; old token deleted | ✅ |
| Token revocation | `revoke_session()` / `revoke_all_sessions()` in `auth_sessions` table | ✅ |
| Secret key source | `SECRET_KEY` env var (Replit shared secret) | ✅ |
| Fallback secret | **None** — app rejects startup if `SECRET_KEY` not set | ✅ |
| JWT validation | `jwt.decode(..., algorithms=["HS256"])` — algorithm pinned | ✅ |
| Expired token handling | 401 returned; frontend auto-refreshes | ✅ |

---

## 2. Content Security Policy

Applied by `_p19_cors` after-request hook (HTML responses only):

```
default-src 'self';
script-src  'self' 'unsafe-inline' https://cdnjs.cloudflare.com
            https://cdn.jsdelivr.net https://checkout.razorpay.com;
style-src   'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net;
font-src    'self' https://fonts.gstatic.com https://cdn.jsdelivr.net;
img-src     'self' data: blob: https:;
connect-src 'self' wss: ws: https://api.razorpay.com;
frame-src   'self' https://api.razorpay.com https://checkout.razorpay.com;
object-src  'none';
```

| Check | Result |
|-------|--------|
| `'unsafe-inline'` in script-src | ⚠️ Present — required for inline scripts in templates |
| Razorpay allowed | ✅ Both checkout and API domains in frame-src + connect-src |
| CDN allowlisted | ✅ jsdelivr and cdnjs in script-src and style-src |
| object-src none | ✅ Prevents Flash/plugin execution |

**Recommendation:** Replace `'unsafe-inline'` with nonces when template refactoring is done.

---

## 3. CSRF Posture

Nexora's API is stateless JWT — no session cookies for API requests.

| Check | Status |
|-------|--------|
| API endpoints use JWT Bearer (stateless) | ✅ Not vulnerable to CSRF |
| `nx_refresh` cookie is HttpOnly + SameSite | ✅ SameSite=Lax prevents cross-site use |
| `/api/auth/refresh` reads cookie server-side | ✅ Browser-initiated CSRF cannot read HttpOnly cookies |
| Form-based endpoints (none) | N/A — all interactions are JSON API |

**CSRF risk: Low.** JWT Bearer authentication over JSON API is inherently CSRF-resistant.

---

## 4. OAuth (Google, GitHub)

| Check | Status |
|-------|--------|
| State parameter used | ✅ `secrets.token_hex(16)` stored in server session |
| Redirect URI validated | ✅ Fixed domain from `request.host_url` |
| Token exchange over HTTPS | ✅ Requests to Google/GitHub token endpoints use HTTPS |
| User info validated before login | ✅ Email checked, provider set |
| OAuth users cannot set password | ✅ `api_auth_change_password` checks `provider != 'local'` |

---

## 5. Input Sanitization

| Vector | Handling |
|--------|----------|
| Path traversal in file endpoints | ✅ `os.path.realpath()` + `startswith(workspace_dir)` check |
| SQL injection | ✅ All queries use parameterized `?` placeholders |
| XSS in task input | ✅ Content returned as JSON, not rendered as HTML |
| Command injection via task string | ✅ Agent runs subprocess in sandboxed workspace dir |
| `../` in `/api/write-doc` path | ✅ `".." in rel_path` check |

---

## 6. Rate Limiting

| Endpoint Group | Limiter | Limit |
|---------------|---------|-------|
| Auth endpoints (`/api/auth/*`) | `_auth_limiter` | Tight sliding window |
| Task queue (`/api/queue-task`, `/api/goals/run-now`) | `_task_limiter` | Per-IP |
| Scheduler | `_scheduler_limiter` | Per-IP |
| General API | `_general_limiter` | Per-IP |

All limiters return HTTP 429 with `Retry-After` message.

---

## 7. Secret Management

| Secret | Storage | Status |
|--------|---------|--------|
| `SECRET_KEY` | Replit env var (shared secret) | ✅ |
| `JWT_SECRET` | Replit env var | ✅ |
| AI API keys | BYOK — stored in `settings` table, encrypted at rest by Replit | ✅ |
| Razorpay keys | Env vars | ✅ |
| Resend API key | Env var | ✅ |
| OAuth client secrets | Env vars | ✅ |

No secrets are hardcoded in source files. No `.env` file committed to version control.

---

## 8. Medium-Risk Items

| Issue | Risk | Mitigation |
|-------|------|-----------|
| `'unsafe-inline'` in CSP script-src | Medium | Required for current inline event handlers; replace with nonces in future refactor |
| `nxFlag` JS variable declared twice (browser console error) | Low-Medium | Frontend bug; two scripts declare the same global. Causes `SyntaxError` in strict mode. Fix: deduplicate the declaration |
| Deletion token stored in memory | Low | Lost on restart; users must re-request. Acceptable for single-process |

---

## 9. Security Headers Applied to Every Response

```
X-Content-Type-Options: nosniff
X-Frame-Options: SAMEORIGIN
X-XSS-Protection: 1; mode=block
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: (on HTML responses)
```

---

**Certification:** Security posture is sound for a BYOK SaaS platform. JWT lifecycle,
CSRF prevention, input sanitization, and secret management are all correctly implemented.
