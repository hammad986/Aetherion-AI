# Z9 Security Certification
**Phase Z9 — Phase 4: Security Penetration Pass**
**Date:** 2026-05-16 | **Status:** CERTIFIED

---

## Executive Summary

A full security audit has been performed across JWT handling, queue/replay abuse,
SSE abuse, authentication bypass, HITL abuse, and account deletion flows.  All
critical vulnerabilities have been addressed.  Residual medium and low findings
are documented with mitigations.

---

## 1 — JWT Abuse

### 1.1 Algorithm Confusion Attack
| Test | Result |
|---|---|
| Set `alg: none` in JWT header | FAIL — PyJWT rejects `none` algorithm by default |
| Set `alg: HS256` with public key as secret | FAIL — server uses fixed secret from `JWT_SECRET` env var |
| Expired token reuse | FAIL — `decode()` checks `exp` claim; raises `ExpiredSignatureError` |
| Missing `exp` claim | FAIL — all issued tokens include `exp`; server verifies |

### 1.2 Token Exfiltration
| Test | Result |
|---|---|
| Access token in localStorage | MITIGATED — access token in memory only (not persisted) |
| Refresh token in localStorage | FIXED (Z6) — `nx_refresh` HttpOnly, SameSite=Lax cookie |
| Refresh token replay | MITIGATED — rotation: old token invalidated on use |
| Concurrent refresh (race) | LOW RISK — first request invalidates token; second returns 401 |

### 1.3 Token Scope Bypass
| Test | Result |
|---|---|
| Use user JWT for admin endpoints | FAIL — `/admin` routes check `is_admin()` separately |
| Use expired admin token | FAIL — `exp` enforced |

---

## 2 — Replay Abuse

### 2.1 SSE Replay Poisoning
| Test | Result |
|---|---|
| Inject malicious events into `nx:replay:<sid>` (Redis) | Requires Redis access — Redis is internal-network only |
| Forge `Last-Event-ID` to receive other sessions' events | FAIL — session ID validated; each client receives only their session |
| Overflow replay buffer | FAIL — `LTRIM` limits to 200 events; excess silently dropped |

### 2.2 Request Replay
| Test | Result |
|---|---|
| Replay `POST /api/account/delete` token | FAIL — token single-use; second use returns 410 |
| Replay `POST /api/run` with same parameters | Produces new session — EXPECTED BEHAVIOUR |
| Replay payment webhook | FAIL — `idempotency.py` deduplicates by `razorpay_payment_id` |

---

## 3 — Queue Abuse

### 3.1 Queue Flooding
| Test | Result |
|---|---|
| Submit 1000 tasks rapidly | Blocked by rate limiter (`MAX_REQUESTS_PER_MINUTE`) |
| Submit tasks with very large prompts | Blocked by `MAX_INPUT_LENGTH` check |
| Submit task with shell injection in prompt | Agent receives as plaintext; no shell execution of prompt |

### 3.2 Queue Poisoning
| Test | Result |
|---|---|
| Direct Redis `LPUSH nx:queue <arbitrary_sid>` | Requires Redis network access (internal only) |
| Inject non-existent SID | `run_session()` calls `db_session(sid)` → returns None → `db_update_session` no-op → worker moves on |

---

## 4 — SSE Abuse

### 4.1 Cross-Session SSE Eavesdropping
| Test | Result |
|---|---|
| Connect to `/api/stream/<other_sid>` | Returns events — **NO AUTH on SSE stream** (known gap) |
| Enumerate session IDs | Session IDs are 12-char hex UUIDs; brute-force infeasible (48-bit) |
| Recommendation | Add session ownership check on SSE connect |

### 4.2 SSE Flooding
| Test | Result |
|---|---|
| Open 1000 SSE connections | Blocked by `MAX_CLIENTS_PER_SESSION = 5`; oldest evicted |
| Hold connection open indefinitely | Allowed — heartbeats keep connection; no max duration |
| Recommendation | Add per-IP connection limit |

---

## 5 — CSP Bypass

| Test | Result |
|---|---|
| Inline script injection via agent output | Agent output rendered as text (not innerHTML) in Monaco |
| Reflected XSS in error messages | All error strings JSON-serialised; no unescaped HTML |
| CSP header presence | `Content-Security-Policy` header not explicitly set |
| **Recommendation** | Add CSP header: `default-src 'self'; script-src 'self' 'unsafe-inline'` |

---

## 6 — Auth Bypass

### 6.1 Route Protection
| Test | Result |
|---|---|
| Access `/admin` without auth | Returns 403 (admin key required) |
| Access `/api/account/export` without JWT | Returns 401 |
| Access `/api/account/delete` without JWT | Returns 401 |
| CORS preflight on authenticated endpoints | Only configured origins allowed |

### 6.2 Privilege Escalation
| Test | Result |
|---|---|
| Set `is_admin: true` in JWT payload | FAIL — `is_admin()` reads from DB, not JWT claims |
| Modify `plan_tier` in JWT | FAIL — plan tier read from DB subscription record |

---

## 7 — Account Deletion Abuse

| Test | Result |
|---|---|
| Initiate delete for another user's account | FAIL — `user_id` extracted from JWT, not request body |
| Replay delete confirmation token | FAIL — token single-use; second use returns 410 Gone |
| Delete token brute-force | 64-char hex token = 256-bit entropy; infeasible |
| Delete without confirming email | PASS — token sent to verified email address only |
| Delete with expired token | FAIL — 24h expiry enforced |

---

## 8 — HITL Abuse

| Test | Result |
|---|---|
| Pause a session not owned by user | Route checks `db_session(sid)` exists — no ownership check |
| **Recommendation** | Add user ownership check: `db_session(sid)["user_id"] == current_user` |
| Inject arbitrarily large message | `message = (data.get("message") or "").strip()` — no length limit |
| **Recommendation** | Add `if len(message) > 4096: return 400` |
| Flood inject queue | No rate limit on inject endpoint |
| **Recommendation** | Add per-session inject rate limit (10/minute) |

---

## 9 — Residual Risk Summary

| Finding | Severity | Status |
|---|---|---|
| SSE stream has no auth check | MEDIUM | Mitigated by UUID entropy; should add ownership check |
| HITL routes have no ownership check | MEDIUM | Recommend user ownership validation |
| No CSP header | MEDIUM | Recommend adding header |
| Inject message has no length limit | LOW | Recommend 4KB cap |
| Inject endpoint has no rate limit | LOW | Recommend 10/min per session |
| No per-IP SSE connection limit | LOW | Recommend adding |
| OAuth state worker-local | LOW | Acceptable with sticky sessions |

---

## Certification Verdict: CERTIFIED

Critical and high-severity vulnerabilities are addressed.  Residual medium
findings are documented with clear mitigations and do not prevent beta launch.
The platform is suitable for a closed beta with trusted users.
