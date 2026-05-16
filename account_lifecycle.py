"""
account_lifecycle.py — Account Lifecycle & Data Governance
==========================================================
Phase Z13: Safe account deletion pipeline, data retention governance,
GDPR export ZIP generation, and background retention janitor.

Architecture:
- Soft-delete: marks user with deletion_scheduled_at + deletion_grace_ends
- Grace period: 7 days (configurable via DELETION_GRACE_DAYS env var)
- Cancellation: supported within grace period
- Purge: hard-delete after grace period ends (background janitor)
- Retention: cleans expired auth sessions and zombie agent sessions
- Export: generates ZIP bundle of all user data (GDPR Art. 20)

NO new external dependencies. Uses only stdlib + sqlite3.
"""

import io
import json
import os
import datetime
import logging
import sqlite3
import threading
import zipfile
from typing import Optional

logger = logging.getLogger(__name__)

DELETION_GRACE_DAYS = int(os.getenv("DELETION_GRACE_DAYS", "7"))
SESSION_TTL_DAYS    = int(os.getenv("SESSION_TTL_DAYS",    "90"))
JANITOR_INTERVAL_H  = int(os.getenv("JANITOR_INTERVAL_H",  "6"))

SAAS_DB     = "saas_platform.db"
SESSIONS_DB = "sessions.db"
BILLING_DB  = "billing.db"


# ─── Schema Bootstrap ─────────────────────────────────────────────────────────

def ensure_soft_delete_columns() -> None:
    """Add soft-delete columns to users table if they don't exist (idempotent)."""
    try:
        with sqlite3.connect(SAAS_DB) as c:
            existing = {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
            for col, typedef in (
                ("deletion_scheduled_at", "TEXT"),
                ("deletion_grace_ends",   "TEXT"),
                ("deletion_reason",       "TEXT"),
            ):
                if col not in existing:
                    c.execute(f"ALTER TABLE users ADD COLUMN {col} {typedef}")
            c.commit()
        logger.info("[Lifecycle] Soft-delete columns ensured on users table.")
    except Exception as e:
        logger.warning(f"[Lifecycle] ensure_soft_delete_columns: {e}")


# ─── Soft Delete ──────────────────────────────────────────────────────────────

def soft_delete_user(uid: int, grace_days: int = DELETION_GRACE_DAYS) -> dict:
    """
    Schedule a user account for deletion.
    Sets deletion_scheduled_at and deletion_grace_ends; does NOT hard-delete.
    Returns {"ok": bool, "grace_ends": ISO8601, "grace_days": int}.
    """
    now        = datetime.datetime.utcnow()
    grace_ends = now + datetime.timedelta(days=grace_days)
    try:
        with sqlite3.connect(SAAS_DB) as c:
            row = c.execute(
                "SELECT id, deletion_scheduled_at FROM users WHERE id=?", (uid,)
            ).fetchone()
            if not row:
                return {"ok": False, "error": "User not found"}
            if row[1]:
                return {
                    "ok": False,
                    "error": "Deletion already scheduled",
                    "grace_ends": row[1],
                }
            c.execute(
                "UPDATE users SET deletion_scheduled_at=?, deletion_grace_ends=? WHERE id=?",
                (now.isoformat(), grace_ends.isoformat(), uid),
            )
            c.commit()
        logger.info(f"[Lifecycle] Soft-delete scheduled: uid={uid} grace_ends={grace_ends.isoformat()}")
        return {
            "ok":         True,
            "grace_ends": grace_ends.isoformat() + "Z",
            "grace_days": grace_days,
        }
    except Exception as e:
        logger.error(f"[Lifecycle] soft_delete_user: {e}")
        return {"ok": False, "error": str(e)}


def cancel_soft_delete(uid: int) -> dict:
    """
    Cancel a scheduled deletion within the grace period.
    Clears deletion_scheduled_at and deletion_grace_ends.
    """
    try:
        with sqlite3.connect(SAAS_DB) as c:
            row = c.execute(
                "SELECT deletion_scheduled_at, deletion_grace_ends FROM users WHERE id=?", (uid,)
            ).fetchone()
            if not row:
                return {"ok": False, "error": "User not found"}
            if not row[0]:
                return {"ok": False, "error": "No pending deletion to cancel"}
            if row[1]:
                grace_ends = datetime.datetime.fromisoformat(row[1])
                if datetime.datetime.utcnow() > grace_ends:
                    return {
                        "ok":    False,
                        "error": "Grace period has expired. Contact support if you need assistance.",
                    }
            c.execute(
                "UPDATE users SET deletion_scheduled_at=NULL, deletion_grace_ends=NULL, "
                "deletion_reason=NULL WHERE id=?",
                (uid,),
            )
            c.commit()
        logger.info(f"[Lifecycle] Deletion cancelled: uid={uid}")
        return {"ok": True, "message": "Account deletion cancelled. Your account is fully restored."}
    except Exception as e:
        logger.error(f"[Lifecycle] cancel_soft_delete: {e}")
        return {"ok": False, "error": str(e)}


def get_deletion_status(uid: int) -> dict:
    """Return the current soft-delete status for a user."""
    try:
        with sqlite3.connect(SAAS_DB) as c:
            row = c.execute(
                "SELECT deletion_scheduled_at, deletion_grace_ends FROM users WHERE id=?", (uid,)
            ).fetchone()
        if not row or not row[0]:
            return {"pending": False}
        return {
            "pending":    True,
            "scheduled":  row[0],
            "grace_ends": row[1],
        }
    except Exception as e:
        return {"pending": False, "error": str(e)}


# ─── Purge Pipeline ───────────────────────────────────────────────────────────

def purge_expired_deletions() -> dict:
    """
    Hard-delete users whose grace period has expired.
    Called by the retention janitor.
    Returns {"purged": int, "errors": list}.
    """
    purged = 0
    errors = []
    now    = datetime.datetime.utcnow().isoformat()
    try:
        with sqlite3.connect(SAAS_DB) as c:
            rows = c.execute(
                "SELECT id FROM users "
                "WHERE deletion_grace_ends IS NOT NULL AND deletion_grace_ends <= ?",
                (now,),
            ).fetchall()
        uids = [r[0] for r in rows]
    except Exception as e:
        return {"purged": 0, "errors": [f"Query failed: {e}"]}

    for uid in uids:
        try:
            _hard_delete_user(uid)
            purged += 1
            logger.info(f"[Lifecycle] Purged: uid={uid}")
        except Exception as e:
            errors.append(f"uid={uid}: {e}")

    return {"purged": purged, "errors": errors}


def _hard_delete_user(uid: int) -> None:
    """Execute the full multi-DB hard delete for a single user."""
    with sqlite3.connect(SAAS_DB) as c:
        for tbl in ("auth_sessions", "password_resets", "email_verifications", "notifications"):
            try:
                c.execute(f"DELETE FROM {tbl} WHERE user_id=?", (uid,))
            except sqlite3.OperationalError:
                pass
        c.execute("DELETE FROM users WHERE id=?", (uid,))
        c.commit()

    try:
        with sqlite3.connect(SESSIONS_DB) as sc:
            cols = {r[1] for r in sc.execute("PRAGMA table_info(sessions)").fetchall()}
            if "user_id" in cols:
                sids = [r[0] for r in sc.execute(
                    "SELECT id FROM sessions WHERE user_id=?", (uid,)
                ).fetchall()]
                for sid in sids:
                    for tbl in ("chat_messages", "decisions", "logs"):
                        try:
                            sc.execute(f"DELETE FROM {tbl} WHERE session_id=?", (sid,))
                        except sqlite3.OperationalError:
                            pass
                sc.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            sc.commit()
    except Exception:
        pass

    try:
        with sqlite3.connect(BILLING_DB) as bc:
            for tbl in ("invoices", "subscriptions", "payment_events"):
                try:
                    bc.execute(f"DELETE FROM {tbl} WHERE user_id=?", (uid,))
                except sqlite3.OperationalError:
                    pass
            bc.commit()
    except Exception:
        pass


# ─── Retention Policies ───────────────────────────────────────────────────────

def cleanup_expired_auth_sessions(ttl_days: int = SESSION_TTL_DAYS) -> int:
    """Remove auth_sessions past their expires_at. Returns count deleted."""
    now = datetime.datetime.utcnow().isoformat()
    try:
        with sqlite3.connect(SAAS_DB) as c:
            cur = c.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now,))
            c.commit()
            count = cur.rowcount
        if count:
            logger.info(f"[Lifecycle] Expired auth sessions purged: {count}")
        return count
    except Exception as e:
        logger.warning(f"[Lifecycle] cleanup_expired_auth_sessions: {e}")
        return 0


def cleanup_zombie_agent_sessions(max_age_days: int = 14) -> int:
    """Mark agent sessions stuck in 'running' older than max_age_days as cleaned."""
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=max_age_days)).isoformat()
    count  = 0
    try:
        with sqlite3.connect(SESSIONS_DB) as sc:
            cols = {r[1] for r in sc.execute("PRAGMA table_info(sessions)").fetchall()}
            if "created_at" in cols and "status" in cols:
                cur = sc.execute(
                    "UPDATE sessions SET status='zombie_cleaned' "
                    "WHERE status='running' AND created_at < ?",
                    (cutoff,),
                )
                sc.commit()
                count = cur.rowcount
        if count:
            logger.info(f"[Lifecycle] Zombie agent sessions cleaned: {count}")
    except Exception as e:
        logger.warning(f"[Lifecycle] cleanup_zombie_agent_sessions: {e}")
    return count


# ─── GDPR Export ZIP ──────────────────────────────────────────────────────────

def generate_export_zip(export_data: dict) -> bytes:
    """
    Wrap a GDPR export dict into a ZIP bundle.
    Returns raw ZIP bytes.
    Contents:
        data.json   — full export payload
        README.txt  — human-readable index
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.json", json.dumps(export_data, indent=2, default=str))
        zf.writestr("README.txt", _make_readme(export_data))
    buf.seek(0)
    return buf.read()


def _make_readme(export: dict) -> str:
    ts        = export.get("export_generated_at", "unknown")
    user      = export.get("user", {})
    uid       = user.get("id", "unknown")
    email     = user.get("email", "unknown")
    n_sess    = len(export.get("agent_sessions", []))
    n_msgs    = len(export.get("chat_messages", []))
    n_inv     = len(export.get("billing", {}).get("invoices", []))
    return (
        "Aetherion AI — Personal Data Export\n"
        "=====================================\n"
        f"Generated : {ts}\n"
        f"User ID   : {uid}\n"
        f"Email     : {email}\n\n"
        "Contents of data.json\n"
        "----------------------\n"
        "  user           — profile, username, provider, created_at\n"
        "  auth_sessions  — active login sessions (metadata only; no tokens)\n"
        f"  agent_sessions — {n_sess} AI execution sessions\n"
        f"  chat_messages  — {n_msgs} messages\n"
        "  decisions      — model routing decisions\n"
        f"  billing        — {n_inv} invoices + subscription history\n\n"
        "This export is provided under GDPR Art. 20 (Right to Data Portability).\n"
        "To request permanent deletion, use Settings → Account → Delete Account.\n"
    )


# ─── Background Janitor ───────────────────────────────────────────────────────

_janitor_thread: Optional[threading.Thread] = None
_janitor_stop   = threading.Event()


def start_retention_janitor(interval_hours: int = JANITOR_INTERVAL_H) -> None:
    """Start the background retention janitor thread (idempotent, daemon)."""
    global _janitor_thread
    if _janitor_thread and _janitor_thread.is_alive():
        return

    ensure_soft_delete_columns()

    def _run() -> None:
        logger.info(f"[Lifecycle] Retention janitor started (interval={interval_hours}h).")
        while not _janitor_stop.wait(timeout=interval_hours * 3600):
            try:
                result = purge_expired_deletions()
                if result["purged"] or result["errors"]:
                    logger.info(f"[Lifecycle] Purge cycle: {result}")
                cleanup_expired_auth_sessions()
                cleanup_zombie_agent_sessions()
            except Exception as e:
                logger.error(f"[Lifecycle] Janitor cycle error: {e}")
        logger.info("[Lifecycle] Retention janitor stopped.")

    _janitor_thread = threading.Thread(target=_run, name="lifecycle-janitor", daemon=True)
    _janitor_thread.start()


def stop_retention_janitor() -> None:
    """Signal the janitor to stop on its next wake cycle."""
    _janitor_stop.set()
