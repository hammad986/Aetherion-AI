# Z12 — CSRF & Session Governance Report
**Aetherion AI · Phase Z12 · Endpoint Audit**
Audit Date: 2026-05-16 | Status: AUDITED

---

## Scope

All POST, DELETE, PATCH, PUT endpoints audited for CSRF exposure, unsafe cookie
usage, and insecure localStorage persistence.

---

## CSRF Threat Model

**Applicable surfaces:** Any state-changing endpoint callable from a browser with
`credentials: 'include'` where the session is in a cookie.

**Current auth architecture:**
- Access token: `Authorization: Bearer <jwt>` header (JS-controlled, not a cookie).
- Refresh token: HttpOnly cookie `nx_refresh`.
- All state-changing API calls require the `Authorization` header.

**Key Finding:**  
Because all state-changing endpoints require a JWT in the `Authorization` header,
and the JWT is stored in JS memory (not a cookie), **standard cookie-based CSRF
attacks do not apply** — a forged cross-origin request cannot include the
`Authorization` header.

---

## Cookie Usage Audit

### `nx_refresh` HttpOnly Cookie

| Property | Value | Verdict |
|---|---|---|
| HttpOnly | Yes | ✓ JS cannot read it |
| Secure | Set when `HTTPS_ONLY=1` env var | ⚠ Must be set in production |
| SameSite | `Lax` | ✓ Blocks cross-site POSTs |
| Path | `/api/auth/refresh` | ✓ Narrowly scoped |
| Expiry | 30 days | Acceptable |

**Finding CSRF-01 (LOW):** `SameSite=Lax` prevents most CSRF. Consider `SameSite=Strict`
for the refresh cookie if cross-site redirects are not needed.

**Finding CSRF-02 (MEDIUM):** If `HTTPS_ONLY` is not set in production, the refresh
cookie is sent over HTTP. Set `SESSION_COOKIE_SECURE=True` on the Flask app in
production.

---

## State-Changing Endpoint Audit

All routes audited for CSRF exposure. Routes that require `@token_required` are
protected by the Bearer token header requirement.

### Auth Routes

| Endpoint | Method | CSRF Protected | Protection |
|---|---|---|---|
| `/api/auth/login` | POST | ✓ | JWT not needed; CSRF irrelevant (login) |
| `/api/auth/signup` | POST | ✓ | Login form; CSRF irrelevant |
| `/api/auth/refresh` | POST | ✓ | HttpOnly cookie + SameSite=Lax |
| `/api/auth/logout` | POST | ✓ | `@token_required` |
| `/api/auth/logout-all` | POST | ✓ | `@token_required` |
| `/api/auth/delete-account` | POST | ✓ | `@token_required` + password confirmation |
| `/api/auth/change-password` | POST | ✓ | `@token_required` + old password |
| `/api/auth/sessions/<id>` | DELETE | ✓ | `@token_required` |

### Account Routes

| Endpoint | Method | CSRF Protected | Protection |
|---|---|---|---|
| `/api/account/export` | GET | ✓ | `@token_required` (read-only) |
| `/api/account/delete-request` | POST | ✓ | `@token_required` |
| `/api/account/delete` | POST | ✓ | `@token_required` + token confirmation |
| `/api/account/soft-delete` | POST | ✓ | `@token_required` |
| `/api/account/cancel-deletion` | POST | ✓ | `@token_required` |

### Execution Routes

| Endpoint | Method | CSRF Protected | Protection |
|---|---|---|---|
| `/api/queue-task` | POST | ✓ | `@token_required` |
| `/api/stop/<sid>` | POST | ✓ | `@token_required` |
| `/api/hitl/respond` | POST | ✓ | `@token_required` |
| `/api/replay/*` | POST | ✓ | `@token_required` |

### File System Routes

| Endpoint | Method | CSRF Protected | Protection |
|---|---|---|---|
| `/api/write-file` | POST | ✓ | `@token_required` + path guard |
| `/api/delete-file` | POST | ✓ | `@token_required` + path guard |
| `/api/rename-file` | POST | ✓ | `@token_required` + path guard |
| `/api/write-doc` | POST | ✓ | `@token_required` + path guard |

---

## localStorage Security Audit

### Current State
- Access tokens were formerly stored in localStorage; migrated to JS memory. ✓
- Refresh tokens are in HttpOnly cookie — not accessible to JS. ✓
- Session ID (`nxCurrentSession`) stored in `sessionStorage` (tab-isolated). ✓
- No auth credentials remain in `localStorage`.

### Findings

| Ref | Severity | Finding |
|---|---|---|
| CSRF-03 | INFO | `localStorage` contains: `nxActiveSession`, workspace layout preferences, theme. None are security-sensitive. |
| CSRF-04 | INFO | `sessionStorage` contains: active session ID for SSE reconnect. Not sensitive. |

---

## Summary

**CSRF surface is minimal.** The Bearer token pattern means:
- Cross-origin forged requests cannot include the `Authorization` header.
- The refresh cookie uses `SameSite=Lax` which blocks top-level POST CSRF.
- No direct cookie-auth endpoints exist that would allow a forged state change.

### Required Actions Before Production

1. Set `HTTPS_ONLY=1` env var to enable `Secure` flag on `nx_refresh` cookie.
2. Consider `SameSite=Strict` for refresh cookie (CSRF-01).
3. Verify `SESSION_COOKIE_SECURE=True` is configured for Flask session cookie.
