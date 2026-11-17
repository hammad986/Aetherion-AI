import logging
from typing import Dict, Any, Optional
from db.store_factory import get_store

logger = logging.getLogger('aetherion.session_store')
_SUCCESS_STATUS_ALIASES = {"success", "succeeded", "completed", "complete", "done"}

class SessionStore:
    def __init__(self, db_path: str = 'sessions.db'):
        self._store = get_store(db_path)
        
    def update_status(self, session_id: str, status: str, exit_code: Optional[int] = None) -> None:
        status = self._truth_guard_status(session_id, status)
        with self._store as s:
            if exit_code is not None:
                s.execute('UPDATE sessions SET status=?, exit_code=? WHERE id=?', (status, exit_code, session_id))
            else:
                s.execute('UPDATE sessions SET status=? WHERE id=?', (status, session_id))

    def _truth_guard_status(self, session_id: str, status: str) -> str:
        requested = (status or "").strip().lower()
        if requested not in _SUCCESS_STATUS_ALIASES:
            return status
        try:
            from execution.events import EventTypes
            from execution.truth_kernel import get_ledger
            ledger = get_ledger()
            latest = ledger.store.latest_execution_for_session(str(session_id))
            if latest:
                truth = ledger.derive_status(latest["execution_id"])
                if truth.status == "succeeded" and truth.verifier_backed:
                    return "success"
                ledger.append(
                    EventTypes.TASK_FAILED,
                    str(session_id),
                    latest["execution_id"],
                    reason="legacy_session_store_success_blocked_without_verifier_evidence",
                    requested_status=status,
                    actor="db.session_store",
                )
        except Exception as exc:
            logger.warning("Truth guard blocked legacy session success for %s: %s", session_id, exc)
        return "failed"

    def mark_zombie_cleaned(self, max_idle_hours: int) -> int:
        with self._store as s:
            cursor = s.execute("UPDATE sessions SET status='zombie_cleaned' WHERE status IN ('running','pending')")
            return cursor.rowcount

    def increment_metric(self, session_id: str, metric: str) -> None:
        if metric not in ('views', 'likes'):
            raise ValueError(f"Invalid metric {metric}")
        with self._store as s:
            s.execute(f"UPDATE sessions SET {metric} = COALESCE({metric}, 0) + 1 WHERE id = ?", (session_id,))

    def flag_session(self, session_id: str) -> None:
        with self._store as s:
            s.execute("UPDATE sessions SET is_flagged = 1 WHERE id = ?", (session_id,))

    def mark_saved(self, session_id: str) -> None:
        with self._store as s:
            s.execute("UPDATE sessions SET is_saved = 1 WHERE id = ?", (session_id,))

    def list_flagged(self) -> list[dict]:
        with self._store as s:
            return s.fetchall("SELECT * FROM sessions WHERE is_flagged = 1 ORDER BY created_at DESC")

    def list_public(self) -> list[dict]:
        with self._store as s:
            return s.fetchall("SELECT * FROM sessions WHERE is_saved = 1 AND is_flagged = 0 ORDER BY created_at DESC")
