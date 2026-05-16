# Z13 — Data Ownership Audit
**Aetherion AI · Phase Z13 · Account Lifecycle & Data Governance**
Audit Date: 2026-05-16 | Status: COMPLETE

---

## Overview

Complete map of all user-linked data across every storage layer. This document
is the authoritative reference for the account deletion pipeline and GDPR export
system.

---

## Storage Layers

| DB / Layer | File | Engine | WAL Mode |
|---|---|---|---|
| Auth & profile | `saas_platform.db` | SQLite | Yes |
| Agent sessions | `sessions.db` | SQLite | Yes |
| Billing | `billing.db` | SQLite | Yes |
| Support | `support.db` | SQLite | No |
| Feedback | `feedback.db` | SQLite | No |
| Memory | `memory.db` | SQLite | Yes |
| Scheduler | `scheduler.db` | SQLite | No |
| Redis | `REDIS_URL` env | Redis / in-process | N/A |
| Filesystem | `workspace/{sid}/` | OS | N/A |

---

## 1. `saas_platform.db` — User Profile & Auth

### Table: `users`
| Column | Type | User-Linked | Export | Delete |
|---|---|---|---|---|
| id | INTEGER PK | — | ✓ | Hard delete |
| email | TEXT | ✓ | ✓ | Hard delete |
| name | TEXT | ✓ | ✓ | Hard delete |
| username | TEXT | ✓ | ✓ | Hard delete |
| password | TEXT | ✗ (hash) | ✗ | Hard delete |
| role | TEXT | ✓ | ✓ | Hard delete |
| provider | TEXT | ✓ | ✓ | Hard delete |
| created_at | TEXT | ✓ | ✓ | Hard delete |
| total_tasks | INTEGER | ✓ | ✓ | Hard delete |
| total_tokens | INTEGER | ✓ | ✓ | Hard delete |
| deletion_scheduled_at | TEXT | ✓ | ✓ | Hard delete |
| deletion_grace_ends | TEXT | ✓ | ✓ | Hard delete |

### Table: `auth_sessions`
| Column | User-Linked | Export | Delete |
|---|---|---|---|
| id | — | ✓ (metadata) | Hard delete |
| user_id | FK | ✓ | Hard delete |
| refresh_token | ✗ (secret) | ✗ | Hard delete |
| device_info | ✓ | ✓ | Hard delete |
| ip_address | ✓ | ✓ | Hard delete |
| created_at | ✓ | ✓ | Hard delete |
| expires_at | ✓ | ✓ | Hard delete |

### Table: `password_resets`
| Column | User-Linked | Export | Delete |
|---|---|---|---|
| user_id | FK | ✗ | Hard delete |
| token_hash | ✗ | ✗ | Hard delete |
| expires_at | — | ✗ | Hard delete |

### Table: `email_verifications`
| Column | User-Linked | Export | Delete |
|---|---|---|---|
| user_id | FK | ✗ | Hard delete |
| token_hash | ✗ | ✗ | Hard delete |

### Table: `notifications`
| Column | User-Linked | Export | Delete |
|---|---|---|---|
| user_id | FK | ✓ | Hard delete |
| message | ✓ | ✓ | Hard delete |

---

## 2. `sessions.db` — Agent Execution

### Table: `sessions`
| Column | User-Linked | Export | Delete |
|---|---|---|---|
| id | — | ✓ | Hard delete |
| user_id | FK (optional) | ✓ | Hard delete |
| task | ✓ | ✓ | Hard delete |
| status | — | ✓ | Hard delete |
| created_at | ✓ | ✓ | Hard delete |
| model | ✓ | ✓ | Hard delete |

### Table: `chat_messages`
| Column | User-Linked | Export | Delete |
|---|---|---|---|
| session_id | FK | ✓ | Cascade from sessions |
| role | — | ✓ | Cascade |
| content | ✓ | ✓ | Cascade |
| ts | ✓ | ✓ | Cascade |

### Table: `decisions`
| Column | User-Linked | Export | Delete |
|---|---|---|---|
| session_id | FK | ✓ | Cascade from sessions |
| decision | ✓ | ✓ | Cascade |

### Table: `logs`
| Column | User-Linked | Export | Delete |
|---|---|---|---|
| session_id | FK | ✗ (verbose) | Cascade from sessions |

---

## 3. `billing.db` — Payments

### Table: `invoices`
| Column | User-Linked | Export | Delete |
|---|---|---|---|
| user_id | FK | ✓ | Hard delete |
| amount | ✓ | ✓ | Hard delete |
| status | ✓ | ✓ | Hard delete |
| created_at | ✓ | ✓ | Hard delete |

### Table: `subscriptions`
| Column | User-Linked | Export | Delete |
|---|---|---|---|
| user_id | FK | ✓ | Hard delete |
| plan | ✓ | ✓ | Hard delete |

### Table: `payment_events`
| Column | User-Linked | Export | Delete |
|---|---|---|---|
| user_id | FK | ✓ | Hard delete |
| event_type | ✓ | ✓ | Hard delete |

> **Compliance Note:** Payment records may be subject to financial record
> retention laws (7 years in many jurisdictions). Consider anonymization
> rather than hard delete for billing records.

---

## 4. Redis — Session State

### Keys Linked to User Sessions

| Key Pattern | Content | Delete Action |
|---|---|---|
| `nx:run:<sid>` | Running flag | Auto-expires (TTL) |
| `nx:stop:<sid>` | Stop signal | Auto-expires (TTL) |
| `nx:proc:<sid>` | Process PID | Auto-expires (TTL) |
| `nx:stop_ack:<sid>` | Stop ack | Auto-expires (TTL) |
| `nx:orphan:<sid>` | Orphan marker | Auto-expires (TTL) |
| `nx:lock:<sid>` | Execution lock | Auto-expires (TTL) |
| `nx:coord:<sid>` | Coordination data | Auto-expires (TTL) |

**Action:** All Redis keys are session-scoped (not user-scoped) and have TTLs.
On session deletion, `redis_layer.release_running(sid)` clears the relevant keys.
No additional Redis cleanup needed for user deletion.

---

## 5. Filesystem — Workspace Artifacts

### Path Pattern
```
workspace/{session_id}/     ← all files written during a session
```

### Content
- Source code written by agent
- Scratch files, test outputs
- Downloaded assets

### Delete Action
`shutil.rmtree(f"workspace/{sid}")` per session owned by user.

> **Current Gap:** `api_account_delete` does not delete workspace directories.
> Added to the soft-delete pipeline in `account_lifecycle.py`.

---

## 6. Other Databases

### `support.db` — Support Tickets
- Contains `user_id`, `email`, `message`, `ai_response`.
- **Action:** Best-effort delete on account deletion.

### `feedback.db`
- May contain user-submitted feedback linked by session.
- **Action:** Best-effort delete.

### `memory.db`
- Short-term agent memory; session-scoped.
- **Action:** Best-effort delete on session cleanup.

### `scheduler.db`
- Background task queue. May contain user-initiated tasks.
- **Action:** Cancel and delete pending tasks for user on deletion.

---

## Dependency Map Summary

```
User (uid)
├── saas_platform.db
│   ├── users [hard delete]
│   ├── auth_sessions [hard delete]
│   ├── password_resets [hard delete]
│   ├── email_verifications [hard delete]
│   └── notifications [hard delete]
├── sessions.db
│   └── sessions [hard delete]
│       ├── chat_messages [cascade]
│       ├── decisions [cascade]
│       └── logs [cascade]
├── billing.db
│   ├── invoices [hard delete / consider anonymize]
│   ├── subscriptions [hard delete]
│   └── payment_events [hard delete]
├── Redis
│   └── nx:*:<sid> keys [auto-expire; release on session end]
└── filesystem
    └── workspace/{sid}/ [rmtree per session]
```
