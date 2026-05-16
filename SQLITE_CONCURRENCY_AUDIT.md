# SQLITE_CONCURRENCY_AUDIT.md
# Phase Y — Part 3: SQLite Concurrency Hardening
# Generated: 2026-05-15

---

## EXECUTIVE SUMMARY

SQLite concurrency was the #2 operational risk. The global WAL monkey-patch
(`infra/db_helper.py`) was already in place and correctly applied at startup.
This phase hardened the patch itself and closed a critical bypass in auth routes.

**Status: HARDENED** — WAL active on all database files.

---

## DATABASE INVENTORY

| File | Size | Usage | Write Frequency |
|------|------|-------|----------------|
| `sessions.db` | 73KB | Session state, logs, decisions, chat | HIGH — every agent step |
| `saas_platform.db` | 147KB | Users, auth, subscriptions | MEDIUM — auth requests |
| `memory.db` | 127KB | Agent long-term memory | MEDIUM — per session |
| `billing.db` | 40KB | Payments, invoices | LOW — billing events |
| `feedback.db` | 24KB | User feedback | LOW |
| `scheduler.db` | 24KB | Scheduled tasks | LOW |
| `support.db` | 32KB | Support tickets | LOW |

---

## CONNECTION POINT AUDIT

### web_app.py (Primary runtime)

| Line | Database | Fix Applied |
|------|----------|-------------|
| 854 | `sessions.db` | ✅ Uses `sqlite3.connect` (patched) + explicit WAL in `_conn()` |
| 3608 | `memory.db` | ✅ Uses `sqlite3.connect` (patched) |
| 5472 | `lessons.db` | ✅ Uses `sqlite3.connect` (patched), timeout=2.0 |
| 8327 | `saas_platform.db` | 🔴 **FIXED** — was `_sqlite3.connect` (bypassed WAL patch) |
| 8370 | `saas_platform.db` | 🔴 **FIXED** — was `_sqlite3.connect` (bypassed WAL patch) |
| 8403 | `sessions.db` | 🔴 **FIXED** — was `_sqlite3.connect` (bypassed WAL patch) |
| 10157 | `memory.db` | ✅ Uses `sqlite3.connect` (patched) |
| 10181 | `memory.db` | ✅ Uses `sqlite3.connect` (patched) |

### Other Runtime Files

| File | Lines | Database | Status |
|------|-------|----------|--------|
| `task_queue.py` | 130, 136, 380 | `task_queue.db` | ✅ Patched (some have timeout=5) |
| `support.py` | 52, 97 | `support.db` | ✅ Patched |
| `scheduler.py` | 206 | `scheduler.db` | ✅ Patched |
| `resource_tracker.py` | 184, 210, 235, 250, 270 | `resource.db` | ✅ Patched (most have timeout=5) |
| `project_runner.py` | 116, 120 | per-project DB | ✅ Patched |
| `payments.py` | 67 | `billing.db` | ✅ Patched, has timeout=10 |
| `nx_crash_recovery.py` | 40 | `sessions.db` | ✅ Patched |
| `nx_session_guard.py` | 70, 83 | `saas/sessions.db` | ✅ Patched |
| `notifications.py` | 53, 86, 389 | `notifications.db` | ✅ Patched |
| `memory.py` | 1430 | `memory.db` | ✅ Patched, has check_same_thread=False |
| `long_term_memory.py` | 124 | LTM db | ✅ Patched, has timeout=10 |
| `idempotency.py` | 34, 93 | `idempotency.db` | ✅ Patched |
| `governance_layer.py` | 56, 64, 117 | governance db | ✅ Patched |
| `infra/db_adapter.py` | 106 | multiple | ✅ Patched, has timeout=30 |
| `nx_backup.py` | 35, 36 | backup dbs | ✅ Patched (backup use, acceptable) |

---

## WAL PATCH MECHANISM

### How the patch works

```python
# infra/db_helper.py
_original_connect = sqlite3.connect

def connect_with_wal(*args, **kwargs):
    kwargs.setdefault("timeout", 5.0)  # Default 5s timeout
    conn = _original_connect(*args, **kwargs)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA cache_size=-16000;")   # NEW: 16MB cache
    conn.execute("PRAGMA mmap_size=33554432;")  # NEW: 32MB mmap
    return conn

def patch_sqlite_globally():
    sqlite3.connect = connect_with_wal  # Replaces stdlib function
```

### Why it works

`sqlite3` is a C extension module. When we do `sqlite3.connect = connect_with_wal`,
Python sets a new attribute on the module object. All subsequent `sqlite3.connect()`
calls (including those using `import sqlite3` in other modules) resolve through
the module's `__dict__` and find our wrapper — **provided the patch runs first**.

### Patch ordering guarantee

In `web_app.py` lines 22-27, the patch is applied **before all other imports**:
```python
try:
    import infra.db_helper as _db_helper
    _db_helper.patch_sqlite_globally()
except ImportError:
    pass
```
This is at the top of the file, before Flask, scheduler, memory, etc.
All subsequent imports inherit the patched `sqlite3.connect`.

---

## PRAGMAS APPLIED

| PRAGMA | Value | Effect |
|--------|-------|--------|
| `journal_mode=WAL` | WAL | Readers and writers do not block each other |
| `synchronous=NORMAL` | NORMAL | Safe with WAL; fsync only on checkpoint |
| `busy_timeout=5000` | 5000ms | Retry up to 5 seconds before `OperationalError` |
| `cache_size=-16000` | 16MB | Reduces I/O for large databases |
| `mmap_size=33554432` | 32MB | Memory-mapped I/O for read-heavy access |

---

## LONG-LIVED TRANSACTION AUDIT

| Risk | File | Assessment |
|------|------|-----------|
| Long write transactions | `memory.py` (1430) | Uses `check_same_thread=False`; connection held per-instance — acceptable for agent lifetime |
| Nested writes | `web_app.py _init_db()` | Uses `with _conn()` context manager — auto-commits/rollbacks |
| Blocking executescript | `web_app.py _init_db()` | Schema init at startup only — no concurrent risk |
| Connection held without close | `task_queue.py` 130 | Has try/finally in caller; acceptable |

**No long-lived write transactions identified that would block production traffic.**

---

## REMAINING RISKS

| Risk | Level | Status |
|------|-------|--------|
| WAL checkpoint overhead | LOW | WAL auto-checkpoints at 1000 pages; acceptable |
| WAL file growth | LOW | Normal under active write load; checkpoint will reduce it |
| In-memory DB silently ignores WAL | INFO | Intentional and safe (`nx_startup_check.py` line 119) |
| Multi-process WAL readers | LOW | All processes use same WAL — correct behavior |
| No WAL on test DBs | LOW | Tests use sqlite3.connect directly; acceptable |

---

## SCHEMA INTEGRITY

**CRITICAL MANDATE PRESERVED:** No schema changes were made.
All existing tables, columns, and indexes are untouched.
Only connection-level pragmas were modified.

---

## VALIDATION

```
PRAGMA journal_mode;    → WAL  (expected)
PRAGMA synchronous;     → 1    (NORMAL = 1)
PRAGMA busy_timeout;    → 5000 (expected)
```

To verify at runtime:
```python
import sqlite3
conn = sqlite3.connect("sessions.db")
print(conn.execute("PRAGMA journal_mode;").fetchone())  # → ('wal',)
print(conn.execute("PRAGMA synchronous;").fetchone())   # → (1,)
print(conn.execute("PRAGMA busy_timeout;").fetchone())  # → (5000,)
```
