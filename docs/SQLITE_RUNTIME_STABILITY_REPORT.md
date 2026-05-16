# T004 — SQLite Runtime Stability Report
**Phase Z6 | Generated: 2026-05-16**

---

## Executive Summary

Three SQLite databases (`sessions.db`, `saas_platform.db`, `billing.db`) are all operated
in WAL mode with concurrency hardening applied at the connection level. Write contention
and checkpoint behavior have been audited.

**Status: STABLE**

---

## 1. Database Inventory

| Database | Primary Tables | WAL Enabled | Write Lock | Used By |
|----------|---------------|------------|-----------|---------|
| `sessions.db` | sessions, logs, decisions, chat_messages, settings | ✅ | `_db_lock` threading.Lock | `web_app.py` core routes |
| `saas_platform.db` | users, auth_sessions, notifications, support_tickets | ✅* | per-connection | auth_system, web_app |
| `billing.db` | subscriptions, invoices, payment_events | ✅* | per-connection | payments.py |
| `memory.db` | learnings, tasks, snippets | ✅* | none (read-heavy) | memory routes |

*WAL enabled via global `sqlite3.connect` patch (see section 3).

---

## 2. WAL Patch (Applied by Nexora V2 infra layer)

Startup log confirms:
```
[DB Helper] Global sqlite3.connect patched with WAL concurrency hardening
(WAL + NORMAL sync + 5s busy_timeout + 16MB cache + 32MB mmap).
```

This patch applies to every `sqlite3.connect()` call in the process:
```python
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;    # fsync reduced for speed, WAL provides crash safety
PRAGMA busy_timeout=5000;     # 5-second wait instead of immediate SQLITE_BUSY
PRAGMA cache_size=-16384;     # 16 MB page cache
PRAGMA mmap_size=33554432;    # 32 MB memory-mapped I/O
```

---

## 3. Concurrency Model

### `sessions.db`
- All writes go through `_db_lock` (threading.Lock at module level in `web_app.py`)
- Context manager `_conn()` wraps `sqlite3.connect` with `row_factory = sqlite3.Row`
- WAL allows concurrent reads while a write is in progress
- `busy_timeout=5000` prevents `SQLITE_BUSY` from surfacing as 500 errors under load

### `saas_platform.db`
- No global lock — each request opens and closes its own connection
- WAL patch applies so concurrent reads don't block
- Auth routes are low-frequency (login/logout) — lock contention not a concern

### `billing.db`
- Managed by `payments.py` which uses its own connection lifecycle
- Webhook endpoint is rate-limited (Razorpay source IP validation)

---

## 4. Rollback Safety

| Scenario | Behaviour |
|----------|-----------|
| Flask worker crash mid-write | WAL log replayed on next open; no data loss |
| Power loss during write | WAL journal ensures atomic commit |
| Two threads writing `sessions.db` simultaneously | `_db_lock` serializes writes; no corruption |
| Session INSERT + UPDATE race | Handled by `INSERT OR REPLACE` / `UPDATE WHERE` patterns |

---

## 5. WAL Checkpoint Policy

SQLite auto-checkpoints the WAL when it reaches 1000 pages (~4 MB default). With
`PRAGMA synchronous=NORMAL`, this is safe. No manual checkpoint is required for
single-process Replit deployment.

For production VPS with multiple processes: configure `PRAGMA wal_autocheckpoint=200`
to more aggressively checkpoint, preventing WAL files from growing unbounded.

---

## 6. Known Limitations

| Issue | Impact | Recommendation |
|-------|--------|----------------|
| No explicit WAL pragma on `saas_platform.db` / `billing.db` direct connections | Low — patched globally | Verify `_HAS_WAL_PATCH` flag in startup |
| `memory.db` opened without `_db_lock` | Low — only updated during agent runs, not concurrent | Add lock if agent parallelism increases |
| No connection pooling | Low for single-worker | Use SQLAlchemy pool for multi-worker |
| `sessions.db` `settings` table used as a KV store for many features | Medium — no row-level locking | Migrate to dedicated tables for high-frequency keys |

---

## 7. Live Validation

```bash
# WAL confirmed in startup log:
[DB Helper] Global sqlite3.connect patched with WAL concurrency hardening

# sessions.db schema check:
sqlite3 sessions.db "PRAGMA journal_mode;"  # → wal
sqlite3 sessions.db "PRAGMA integrity_check;"  # → ok
```

**Status:** No database corruption or `SQLITE_BUSY` errors observed in runtime logs.
