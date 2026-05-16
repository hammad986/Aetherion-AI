# Z13 — Data Retention Governance
**Aetherion AI · Phase Z13 · Account Lifecycle**
Date: 2026-05-16 | Status: IMPLEMENTED

---

## Overview

This document defines the retention policies for all user-linked data and
certifies the implementation of the background retention janitor
(`account_lifecycle.py:start_retention_janitor()`).

---

## 1. Retention Policies

### Auth Sessions
| Policy | Value | Env Var | Enforcement |
|---|---|---|---|
| Max session age | 90 days | `SESSION_TTL_DAYS` | Janitor: `cleanup_expired_auth_sessions()` |
| Session expiry | Set at creation (30 days) | `REFRESH_TOKEN_DAYS` | DB `expires_at` column |

Sessions past their `expires_at` are deleted by the janitor on each cycle.
The `SESSION_TTL_DAYS` hard cap ensures even sessions with extended expiry
are eventually cleaned.

---

### Agent Sessions
| Policy | Value | Env Var | Enforcement |
|---|---|---|---|
| Zombie session cleanup | 14 days in `running` state | Hard-coded | Janitor: `cleanup_zombie_agent_sessions()` |

Sessions stuck in `running` status for more than 14 days are marked
`zombie_cleaned`. This prevents old stuck sessions from appearing as active
in the UI.

---

### Soft-Deleted Accounts
| Policy | Value | Env Var | Enforcement |
|---|---|---|---|
| Grace period | 7 days | `DELETION_GRACE_DAYS` | Janitor: `purge_expired_deletions()` |

Users who schedule deletion get 7 days to cancel. After the grace period,
the janitor runs `_hard_delete_user(uid)` which cascades through all DBs.

---

### Workspace Artifacts (Future)
| Policy | Value | Status |
|---|---|---|
| Orphan workspace cleanup | Workspaces with no session row | V2 Target |
| Max workspace age | 30 days post-session close | V2 Target |

---

## 2. Background Janitor

### Module: `account_lifecycle.start_retention_janitor()`
- Daemon thread — does not block app shutdown.
- Run interval: `JANITOR_INTERVAL_H` env var (default: 6 hours).
- Started at application boot, wired into `web_app.py` startup block.

### Janitor Cycle Steps
```
1. purge_expired_deletions()
   → Query users WHERE deletion_grace_ends <= NOW
   → For each: _hard_delete_user(uid) across all 3 DBs

2. cleanup_expired_auth_sessions()
   → DELETE FROM auth_sessions WHERE expires_at < NOW

3. cleanup_zombie_agent_sessions()
   → UPDATE sessions SET status='zombie_cleaned'
     WHERE status='running' AND created_at < (NOW - 14 days)
```

### Error Handling
- Each step is independently try/excepted — one failure does not block others.
- Errors logged at ERROR level with `[Lifecycle]` prefix.
- Janitor restarts automatically on next cycle.

---

## 3. Soft-Delete Grace Period Flow

```
User requests deletion
        │
        ▼
POST /api/account/soft-delete
        │
        ▼
soft_delete_user(uid)
  → sets deletion_scheduled_at = NOW
  → sets deletion_grace_ends = NOW + 7 days
        │
        │  ← User can cancel any time during grace period ←
        │         via POST /api/account/cancel-deletion
        │
        ▼
Janitor runs (every 6 hours)
        │
        ▼
purge_expired_deletions()
  → finds users WHERE deletion_grace_ends <= NOW
  → _hard_delete_user(uid) for each
        │
        ▼
Account permanently gone from all DBs
```

---

## 4. GDPR Export Timeline

| Request | Endpoint | Response Time | Format |
|---|---|---|---|
| JSON export | `GET /api/account/export` | Synchronous (<1s) | JSON |
| ZIP export | `GET /api/account/export/zip` | Synchronous (<2s) | ZIP download |

Both endpoints are bounded to the authenticated user — no `user_id` parameter
accepted in body (IDOR prevention).

---

## 5. Compliance Notes

### GDPR Alignment
| Right | Implementation | Status |
|---|---|---|
| Right to Access (Art. 15) | `GET /api/account/export` | ✓ |
| Right to Portability (Art. 20) | `GET /api/account/export/zip` | ✓ |
| Right to Erasure (Art. 17) | Soft-delete + hard purge pipeline | ✓ |
| Right to Rectification (Art. 16) | Change email, name via account settings | ✓ |
| Erasure within 30 days | Grace: 7 days + max janitor delay: 6h | ✓ |

### Financial Records Caveat
Billing records (invoices, payment events) are deleted with the account.
In jurisdictions requiring financial record retention (typically 5-7 years),
operators should anonymize rather than delete billing rows. Future V2:
add `anonymize_billing_on_deletion` env var toggle.

---

## 6. Janitor Health Monitoring

The janitor logs all activity under the `[Lifecycle]` prefix. To verify it
is running, check `app.log`:
```
grep "\[Lifecycle\]" app.log
```

Expected output on healthy janitor:
```
[Lifecycle] Retention janitor started (interval=6h).
[Lifecycle] Expired auth sessions purged: 3
[Lifecycle] Zombie agent sessions cleaned: 0
[Lifecycle] Purge cycle: {"purged": 0, "errors": []}
```
