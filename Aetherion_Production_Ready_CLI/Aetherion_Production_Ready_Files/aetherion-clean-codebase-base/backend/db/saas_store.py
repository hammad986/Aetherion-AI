import logging
from typing import List, Optional, Any
from db.store_factory import get_store

logger = logging.getLogger("aetherion.db.saas")

DB_PATH = "saas_platform.db"
_store = get_store(DB_PATH)

def _ensure_tables():
    with _store as s:
        s.execute('''
            CREATE TABLE IF NOT EXISTS security_state (
                id INTEGER PRIMARY KEY,
                kill_switch_active INTEGER DEFAULT 0
            )
        ''')
        s.execute('''
            INSERT OR IGNORE INTO security_state (id, kill_switch_active) VALUES (1, 0)
        ''')

def provider_health_rows(query: str) -> List[dict]:
    with _store as s:
        try:
            return s.fetchall(query)
        except Exception as e:
            logger.warning("provider_health_rows failed: %s", e)
            return []

def causal_telemetry_summary():
    with _store as s:
        try:
            row = s.fetchone("SELECT SUM(drift_metric), SUM(recovery_metric) FROM causal_telemetry")
            if row:
                return list(row.values())
        except Exception:
            return None
    return None

def fetch_saas_metrics() -> dict:
    with _store as s:
        try:
            row = s.fetchone("SELECT * FROM saas_metrics ORDER BY ts DESC LIMIT 1")
            return row or {}
        except Exception:
            return {}

def insert_blueprint(blueprint_id: str, payload: str):
    with _store as s:
        s.execute("INSERT INTO blueprints (id, payload) VALUES (?, ?)", (blueprint_id, payload))

def get_blueprint(blueprint_id: str) -> Optional[str]:
    with _store as s:
        row = s.fetchone("SELECT payload FROM blueprints WHERE id = ?", (blueprint_id,))
        return row['payload'] if row else None

def get_banned_users() -> set:
    with _store as s:
        rows = s.fetchall("SELECT username FROM user_bans")
        return {r['username'] for r in rows}

def ban_user(username: str):
    with _store as s:
        s.execute("INSERT INTO user_bans (username) VALUES (?)", (username,))
