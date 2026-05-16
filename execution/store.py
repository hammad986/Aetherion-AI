import sqlite3
import json
import logging
import time
from typing import List, Dict, Any, Optional
from execution.events import RuntimeEvent

logger = logging.getLogger("nexora.store")

class ExecutionStore:
    """
    Persistent SQLite-backed store for execution snapshots and event-sourced history.
    Enables crash recovery, replay, and lineage tracking.
    """
    def __init__(self, db_path: str = "workspace/execution_store.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Append-only event history
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS event_log (
                    event_id TEXT PRIMARY KEY,
                    execution_id TEXT,
                    session_id TEXT,
                    event_type TEXT,
                    payload TEXT,
                    timestamp REAL,
                    correlation_id TEXT
                )
            ''')
            # Execution state snapshots
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    status TEXT,
                    payload TEXT,
                    started_at REAL,
                    updated_at REAL,
                    parent_execution_id TEXT
                )
            ''')
            # Indices for quick lineage and replay queries
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_exec_id ON event_log(execution_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sess_id ON executions(session_id)')
            conn.commit()

    def append_event(self, event: RuntimeEvent, correlation_id: str = None) -> bool:
        """Appends an immutable runtime event to the log."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    '''INSERT INTO event_log 
                       (event_id, execution_id, session_id, event_type, payload, timestamp, correlation_id) 
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (event.event_id, event.execution_id, event.session_id, event.type,
                     json.dumps(event.payload), event.timestamp, correlation_id)
                )
            return True
        except Exception as e:
            logger.error(f"Failed to append event {event.event_id}: {e}")
            return False

    def get_events(self, execution_id: str) -> List[Dict[str, Any]]:
        """Retrieves chronological event history for replay/debugging."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                'SELECT * FROM event_log WHERE execution_id = ? ORDER BY timestamp ASC', 
                (execution_id,)
            ).fetchall()
            
        return [
            {
                "event_id": r["event_id"],
                "execution_id": r["execution_id"],
                "event_type": r["event_type"],
                "payload": json.loads(r["payload"]),
                "timestamp": r["timestamp"],
                "correlation_id": r["correlation_id"]
            } for r in rows
        ]

    def upsert_execution(self, execution_id: str, session_id: str, status: str, payload: dict, 
                         parent_execution_id: str = None, started_at: float = None):
        """Maintains the latest snapshot of an execution for fast querying and recovery."""
        now = time.time()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    '''INSERT INTO executions 
                       (execution_id, session_id, status, payload, started_at, updated_at, parent_execution_id) 
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(execution_id) DO UPDATE SET 
                       status=excluded.status, 
                       updated_at=excluded.updated_at,
                       payload=excluded.payload''',
                    (execution_id, session_id, status, json.dumps(payload), started_at or now, now, parent_execution_id)
                )
            return True
        except Exception as e:
            logger.error(f"Failed to upsert execution {execution_id}: {e}")
            return False
            
    def get_stale_executions(self, timeout_seconds: int = 3600) -> List[str]:
        """Identifies active executions that haven't received updates within the TTL boundary."""
        threshold = time.time() - timeout_seconds
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                'SELECT execution_id FROM executions WHERE status IN ("queued", "running") AND updated_at < ?',
                (threshold,)
            ).fetchall()
        return [r[0] for r in rows]

    def cleanup_expired_artifacts(self, retention_days: int = 30) -> int:
        """
        Secure Workspace Lifecycle Management.
        Purges execution snapshots and event logs older than the retention policy to maintain DB health.
        """
        threshold = time.time() - (retention_days * 86400)
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Purge event logs for old executions
                cursor = conn.execute(
                    '''DELETE FROM event_log WHERE execution_id IN 
                       (SELECT execution_id FROM executions WHERE updated_at < ?)''',
                    (threshold,)
                )
                events_deleted = cursor.rowcount
                
                # Purge executions
                cursor = conn.execute('DELETE FROM executions WHERE updated_at < ?', (threshold,))
                execs_deleted = cursor.rowcount
                
            logger.info(f"[Lifecycle] Cleanup complete. Removed {events_deleted} events and {execs_deleted} executions.")
            return execs_deleted
        except Exception as e:
            logger.error(f"[Lifecycle] Database cleanup failed: {e}")
            return 0
