# Z12 — Rate Limit Discipline Report
**Aetherion AI · Phase Z12 · Abuse Protection**
Audit Date: 2026-05-16 | Status: HARDENED

---

## Architecture

Rate limiting is implemented via `security.py:RateLimiter` — an in-memory
sliding-window limiter, keyed by client IP (`X-Forwarded-For` → `remote_addr`
fallback). Applied via `@app.before_request` hook `_p19_rate_limit()`.

**Limitation:** In-memory — not shared across Gunicorn workers. Each worker
maintains an independent window. Effective limit under N workers =
`max_calls × N`. Redis-backed limiter is the V2 target.

---

## Rate Limiter Inventory

### Pre-Z12 Limiters

| Limiter | Env Var | Default | Window | Endpoint Scope |
|---|---|---|---|---|
| `_auth_limiter` | `RATE_LIMIT_AUTH` | 10/min | 60s | `/api/auth/login`, `/api/auth/signup` |
| `_task_limiter` | `RATE_LIMIT_TASKS` | 20/min | 60s | `/api/queue-task`, `/api/goals/run-now`, `/api/chains` |
| `_scheduler_limiter` | `RATE_LIMIT_SCHEDULER` | 30/min | 60s | `/api/scheduler/*` |
| `_general_limiter` | `RATE_LIMIT_GENERAL` | 120/min | 60s | All `/api/*` (catch-all) |
| `_forgot_pw_limiter` | `RATE_LIMIT_FORGOT_PW` | 5/hr | 3600s | `/api/auth/forgot-password` |

### Z12 New Limiters

| Limiter | Env Var | Default | Window | Endpoint Scope |
|---|---|---|---|---|
| `_sse_conn_limiter` | `RATE_LIMIT_SSE` | 15/min | 60s | `/api/stream/*` |
| `_replay_limiter` | `RATE_LIMIT_REPLAY` | 30/min | 60s | `/api/replay/*` |
| `_hitl_limiter` | `RATE_LIMIT_HITL` | 60/min | 60s | `/api/hitl/*` |
| `_deletion_limiter` | `RATE_LIMIT_DELETE` | 5/hr | 3600s | `/api/account/delete*`, `/api/auth/delete-account` |

---

## Endpoint-by-Endpoint Analysis

### 1. Login Endpoints
- **`/api/auth/login`**: `_auth_limiter` (10/min) + brute-force lockout (5 fails/60s). ✓
- **`/api/auth/signup`**: `_auth_limiter` (10/min). ✓
- **Risk:** Credential stuffing. Mitigation: auth limiter + exponential backoff recommendation.

### 2. Execution Endpoints
- **`/api/queue-task`**: `_task_limiter` (20/min). ✓
- **Background RATE_LIMIT_STORE**: additional 20 req/min + 3 concurrent cap. ✓
- **Risk:** Resource exhaustion via task flooding. Double-guarded. ✓

### 3. SSE Streams *(Z12 hardened)*
- **`/api/stream/<sid>`**: `_sse_conn_limiter` (15/min). ✓
- **Risk:** SSE storm — repeatedly opening/closing connections to exhaust server
  file descriptors. Limited to 15 new connections/min/IP.
- **Note:** SSE stream itself is not rate-limited while open. Only the connect is.

### 4. Replay Endpoints *(Z12 hardened)*
- **`/api/replay/*`**: `_replay_limiter` (30/min). ✓
- **Risk:** Replay storm causing excessive DB reads. Limited at API layer.

### 5. HITL Approval Endpoints *(Z12 hardened)*
- **`/api/hitl/*`**: `_hitl_limiter` (60/min). ✓
- **Risk:** HITL flooding — spamming approve/reject to trigger agent state
  corruption. Limited but permissive (60/min) to allow fast legitimate responses.

### 6. Deletion Endpoints *(Z12 hardened)*
- **`/api/account/delete-request`**: `_deletion_limiter` (5/hr). ✓
- **`/api/account/delete`**: `_deletion_limiter` (5/hr). ✓
- **`/api/auth/delete-account`**: `_deletion_limiter` (5/hr). ✓
- **Risk:** Deletion spam — repeatedly triggering deletion flows to disrupt service.
  1-hour window with 5 max attempts per IP is strict.

### 7. Forgot Password
- **`/api/auth/forgot-password`**: `_forgot_pw_limiter` (5/hr). ✓
- Returns 200 always (no timing oracle for valid/invalid email). ✓

---

## Abuse Resistance Summary

| Attack Vector | Mitigated | Method |
|---|---|---|
| Credential stuffing | ✓ | `_auth_limiter` + brute-force lockout |
| Task queue flooding | ✓ | `_task_limiter` + concurrent cap |
| SSE connection storm | ✓ (Z12) | `_sse_conn_limiter` |
| Replay API abuse | ✓ (Z12) | `_replay_limiter` |
| HITL spam | ✓ (Z12) | `_hitl_limiter` |
| Deletion harassment | ✓ (Z12) | `_deletion_limiter` |
| Password reset harvesting | ✓ | `_forgot_pw_limiter` + fixed 200 response |

---

## V2 Hardening Targets

| Priority | Action |
|---|---|
| HIGH | Migrate rate limit state to Redis for multi-worker consistency |
| MEDIUM | Add exponential backoff hint headers (`Retry-After`) |
| MEDIUM | Add IP-level global block for repeated limit violations (abuse detection) |
| LOW | Add endpoint-level analytics for limit hit rates |
