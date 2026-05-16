# T003 — Account Governance System Report
**Phase Z6 | Generated: 2026-05-16**

---

## Executive Summary

GDPR Art. 17 (Right to Erasure) and Art. 20 (Right to Data Portability) endpoints
implemented. All three routes require a valid JWT and operate exclusively on the
authenticated user — no IDOR vector exists.

**Status: IMPLEMENTED**

---

## 1. New Endpoints

### `GET /api/account/export`
Returns all personal data held across all databases for the authenticated user.

**Auth:** `@token_required` (JWT Bearer)
**Response:**
```json
{
  "ok": true,
  "data": {
    "export_generated_at": "2026-05-16T08:22:00Z",
    "user": { "id": 1, "email": "...", "name": "...", "created_at": "..." },
    "auth_sessions": [ { "id": "...", "device_info": "...", "ip_address": "..." } ],
    "agent_sessions": [ { "id": "...", "task": "...", "status": "..." } ],
    "chat_messages": [ { "ts": "...", "role": "user", "content": "..." } ],
    "decisions": [],
    "billing": { "invoices": [], "subscriptions": [] }
  }
}
```

**Data sources queried:**
- `saas_platform.db` — `users`, `auth_sessions`
- `sessions.db` — `sessions`, `chat_messages` (where `user_id` column exists)
- `billing.db` — `invoices`, `subscriptions`

---

### `POST /api/account/delete-request`
Initiates a deletion request. Returns a single-use confirmation token valid for 24 hours.

**Auth:** `@token_required`
**Response:**
```json
{
  "ok": true,
  "message": "Deletion request received. Confirm within 24 hours.",
  "token": "<64-char hex token>"
}
```

---

### `POST /api/account/delete`
Executes the deletion pipeline after token confirmation.

**Auth:** `@token_required`
**Body:** `{ "token": "<token from delete-request>" }`

**Deletion pipeline (all best-effort, errors collected not raised):**
1. `saas_platform.db`: DELETE from `auth_sessions`, `password_resets`,
   `email_verifications`, `notifications`, then `users`
2. `sessions.db`: DELETE agent sessions, chat_messages, decisions, logs (by user_id)
3. `billing.db`: DELETE from `invoices`, `subscriptions`, `payment_events`
4. Clears the `nx_refresh` HttpOnly cookie

**Response:**
```json
{
  "ok": true,
  "deleted": 42,
  "errors": [],
  "message": "Account and all associated data have been permanently deleted."
}
```

---

## 2. Security Properties

| Property | Implementation |
|----------|---------------|
| Auth required | `@token_required` on all three routes |
| IDOR prevention | Only `g.user_id` used — no user_id in request body/params |
| Token expiry | 24-hour window enforced (`time.time() - req["requested_at"] > 86400`) |
| Replay prevention | Token marked `confirmed=True` before cookie clear; re-use returns 409 |
| Token strength | `secrets.token_hex(32)` = 256-bit entropy |
| Partial failure handling | Errors collected in list, operation continues; all 3 DBs attempted |

---

## 3. Token Storage

Deletion tokens are stored in `_deletion_requests` dict (in-process memory).

**Limitation:** Tokens are lost on process restart. Users must re-request after a restart.
This is acceptable for the Replit single-process deployment model.

**Recommendation for multi-instance:** Migrate token storage to `saas_platform.db`
`deletion_requests` table.

---

## 4. Existing Account Deletion Route

A prior deletion route exists at `POST /api/auth/delete-account` (line 8049). It requires
password confirmation and is valid. The new governance routes complement it:

| Route | Purpose | When to use |
|-------|---------|-------------|
| `/api/auth/delete-account` | Immediate deletion with password | In-app settings flow |
| `/api/account/delete-request` + `/api/account/delete` | Two-step GDPR pipeline | Legal compliance, support-driven deletions |

---

## 5. GDPR Compliance Checklist

| Requirement | Status |
|-------------|--------|
| Art. 17: Right to erasure | ✅ `/api/account/delete` |
| Art. 20: Data portability | ✅ `/api/account/export` |
| Confirmation step before deletion | ✅ Two-step token flow |
| All data sources covered | ✅ All 3 DBs |
| No residual data in billing | ✅ payment_events deleted |
| Cookie cleared on deletion | ✅ `_nx_clear_refresh_cookie()` |
| Auth sessions revoked | ✅ Deleted first |
