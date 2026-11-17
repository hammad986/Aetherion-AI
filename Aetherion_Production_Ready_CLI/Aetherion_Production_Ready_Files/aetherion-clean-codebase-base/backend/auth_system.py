"""
auth_system.py â€” Enterprise Authentication System (Phase 50 v2)
================================================================
Features:
- Email/password signup + login (bcrypt)
- OAuth (Google, GitHub) â€” callback routes in web_app.py
- Short-lived JWT access tokens (15 min)
- Long-lived refresh tokens (30 days) with rotation
- Multi-device session tracking in DB
- Brute-force / rate-limit protection (in-memory sliding window)
- Backward-compatible with existing username-based users
"""
import sqlite3
try:
    import psycopg2
    from psycopg2.extras import DictCursor
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False
import secrets
import uuid
import hashlib
import jwt
import datetime
import time
import bcrypt
import os
import logging
import threading
from collections import defaultdict, deque
from functools import wraps
from flask import request, jsonify, g, session
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_ALLOW_DEV_AUTH = os.environ.get("ALLOW_DEV_AUTH", "0").strip() == "1"

# â”€â”€ Dev auth bypass gating â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# The ?dev=1 bypass below grants user_id=1 with no credentials. Two guards for it
# already existed in the tree, but neither runs in a real deployment:
#   * web_app.py's check sits inside `if __name__ == "__main__":`, so it is skipped
#     entirely when a WSGI server (Gunicorn) imports the module and serves `app`.
#   * core/startup_validator.validate_startup() contains the same check but has no
#     caller outside its own `__main__` block.
# This module is imported on every request path, so the gate is enforced here too.
#
# The gate fails closed: the bypass engages only when the environment is
# *explicitly* one of the known non-production values. An unset ENVIRONMENT (a
# container that forgot to set it) therefore disables the bypass rather than
# silently allowing it.
_DEV_AUTH_ENVIRONMENTS = ("development", "dev", "testing", "test", "local")


def _current_environment() -> str:
    """Resolve the deployment environment the same way core/environment does."""
    try:
        from core.environment import get_raw_environment_string
        return get_raw_environment_string().lower()
    except Exception:
        raw = (
            os.environ.get("AETHERION_ENV")
            or os.environ.get("ENVIRONMENT")
            or os.environ.get("APP_ENV")
            or os.environ.get("ENV")
            or os.environ.get("FLASK_ENV")
            or ""
        )
        return raw.strip().lower()


def _is_production_environment() -> bool:
    try:
        from core.environment import is_fail_closed_environment
        return is_fail_closed_environment()
    except Exception:
        env = _current_environment()
        return env in ("production", "prod", "staging", "stage", "unknown")


# Fail fast at import time, mirroring the JWT_SECRET gate above. This runs under
# Gunicorn, uWSGI, flask run, and direct execution alike.
if _ALLOW_DEV_AUTH and _is_production_environment():
    raise ValueError(
        "[SECURITY] ALLOW_DEV_AUTH=1 is set while the environment is 'production'. "
        "This flag enables an unauthenticated ?dev=1 bypass that grants user_id=1. "
        "Remove ALLOW_DEV_AUTH from the production environment before starting."
    )

_dev_auth_refusal_logged = False


def _dev_auth_enabled() -> bool:
    """
    True only when the dev bypass is explicitly permitted.

    Re-read per request rather than cached at import so that tests and local
    tooling can toggle the environment, and so a mutated environment cannot
    leave a stale permissive value behind.
    """
    global _dev_auth_refusal_logged
    if os.environ.get("ALLOW_DEV_AUTH", "0").strip() != "1":
        return False

    env = _current_environment()
    if env not in _DEV_AUTH_ENVIRONMENTS:
        if not _dev_auth_refusal_logged:
            _dev_auth_refusal_logged = True
            logger.error(
                "[SECURITY] ALLOW_DEV_AUTH=1 but ENVIRONMENT/FLASK_ENV is %r. The "
                "?dev=1 bypass is REFUSED because the environment is not one of %s. "
                "Set ENVIRONMENT=development explicitly if you intend to use it.",
                env or "<unset>", ", ".join(_DEV_AUTH_ENVIRONMENTS),
            )
        return False
    return True

DATABASE_URL = os.environ.get("DATABASE_URL")
def _is_postgres():
    return bool(DATABASE_URL and (DATABASE_URL.startswith("postgresql") or DATABASE_URL.startswith("postgres")))
if DATABASE_URL and DATABASE_URL.startswith("sqlite:///") and DATABASE_URL != "sqlite:///:memory:":
    DB_PATH = DATABASE_URL[len("sqlite:///"):]
else:
    DB_PATH = os.environ.get("SQLITE_DB_PATH", "saas_platform.db")

_JWT_SECRET_RAW = os.environ.get("JWT_SECRET", "").strip()
if not _JWT_SECRET_RAW:
    if _is_production_environment() or _current_environment() in ("production", "prod", "staging", "stage"):
        raise ValueError(
            "[SECURITY] JWT_SECRET environment variable is missing or empty. "
            "Set a strong random secret (e.g. openssl rand -hex 32) in your .env file. "
            "Refusing to start with an ephemeral key — tokens would be invalidated on every restart."
        )
    _JWT_SECRET_RAW = "dev-insecure-jwt-secret-key-32chars-min!!"
SECRET_KEY = _JWT_SECRET_RAW

ACCESS_TOKEN_MINUTES = int(os.environ.get("ACCESS_TOKEN_MINUTES", "15"))
REFRESH_TOKEN_DAYS = int(os.environ.get("REFRESH_TOKEN_DAYS", "30"))

# â”€â”€ B4: Postgres Connection Pool â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SimpleConnectionPool is thread-safe for concurrent use across gthreads.
# minconn=2: always keep 2 warm connections open.
# maxconn=20: cap at 20 â€” well under Postgres default max_connections=100.
_pg_pool = None
_pg_pool_lock = threading.Lock()


def _get_pg_pool():
    """Lazily initialize and return the Postgres connection pool."""
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    with _pg_pool_lock:
        if _pg_pool is None:
            if not _PSYCOPG2_AVAILABLE:
                raise RuntimeError(
                    "psycopg2 is not installed but DATABASE_URL is set. "
                    "Run: pip install psycopg2-binary"
                )
            from psycopg2 import pool as pgpool
            min_conn = int(os.environ.get("DB_POOL_MIN", "2"))
            max_conn = int(os.environ.get("DB_POOL_MAX", "20"))
            _pg_pool = pgpool.SimpleConnectionPool(
                min_conn, max_conn, dsn=DATABASE_URL
            )
            logger.info(
                f"[Auth] Postgres pool initialized "
                f"(min={min_conn}, max={max_conn})"
            )
    return _pg_pool


def get_db_connection():
    """B4: Returns a pooled Postgres connection or direct SQLite connection."""
    if _is_postgres():
        global _pg_pool
        pool = _get_pg_pool()
        try:
            conn = pool.getconn()
            c = conn.cursor()
            c.execute("SELECT 1")
            c.close()
            return conn
        except Exception:
            # Postgres connection is dead (e.g. DB restarted). Recreate pool.
            try:
                if conn:
                    pool.putconn(conn, close=True)
            except Exception:
                pass
            with _pg_pool_lock:
                _pg_pool = None
            pool = _get_pg_pool()
            return pool.getconn()
    # SQLite: check_same_thread=False because we guard with WAL + application lock
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def _release_db_connection(conn):
    """B4: Return a Postgres connection to the pool (no-op for SQLite)."""
    if _is_postgres() and _pg_pool is not None:
        try:
            _pg_pool.putconn(conn)
        except Exception:
            pass
    else:
        try:
            conn.close()
        except Exception:
            pass


def execute_query(query: str, params: tuple = (), fetchone=False, fetchall=False, commit=False):
    """Abstracts SQLite (?) vs Postgres (%s) placeholders and connection handling.
    B4: Uses pooled connections for Postgres â€” no connection leaks."""
    conn = get_db_connection()
    try:
        c = conn.cursor()
        if _is_postgres():
            query = query.replace("?", "%s")
            if "AUTOINCREMENT" in query:
                query = query.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            if "DATETIME" in query:
                query = query.replace("DATETIME", "TIMESTAMP")
            if "INSERT OR IGNORE" in query:
                # Postgres uses INSERT ... ON CONFLICT DO NOTHING
                query = query.replace("INSERT OR IGNORE", "INSERT") + " ON CONFLICT DO NOTHING"
        c.execute(query, params)
        if commit:
            conn.commit()
        if fetchone:
            return c.fetchone()
        if fetchall:
            return c.fetchall()
        return None
    except Exception:
        if commit:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        _release_db_connection(conn)


# â”€â”€â”€ Brute-force protection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_bf_lock = threading.Lock()
_failed_attempts: dict = defaultdict(deque)
MAX_FAILED = int(os.environ.get("MAX_LOGIN_ATTEMPTS", "5"))
LOCKOUT_WINDOW = int(os.environ.get("LOGIN_LOCKOUT_SECS", "60"))


def _record_failed(key: str):
    now = time.time()
    with _bf_lock:
        dq = _failed_attempts[key]
        dq.append(now)
        cutoff = now - LOCKOUT_WINDOW
        while dq and dq[0] < cutoff:
            dq.popleft()


def _is_locked_out(key: str) -> bool:
    now = time.time()
    cutoff = now - LOCKOUT_WINDOW
    with _bf_lock:
        dq = _failed_attempts[key]
        while dq and dq[0] < cutoff:
            dq.popleft()
        return len(dq) >= MAX_FAILED



def _clear_failed(key: str):
    with _bf_lock:
        _failed_attempts.pop(key, None)


def init_db():
    if _is_postgres():
        # Postgres path — use execute_query which handles the connection
        execute_query('''CREATE TABLE IF NOT EXISTS users (
                      id SERIAL PRIMARY KEY,
                      username TEXT UNIQUE,
                      password TEXT,
                      email TEXT,
                      name TEXT,
                      provider TEXT DEFAULT 'local',
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      role TEXT DEFAULT 'user',
                      is_banned INTEGER DEFAULT 0,
                      plan TEXT DEFAULT 'free',
                      daily_tokens INTEGER DEFAULT 0,
                      quota_reset_ts REAL DEFAULT 0,
                      total_tasks INTEGER DEFAULT 0,
                      total_tokens INTEGER DEFAULT 0,
                      otp_code TEXT,
                      otp_expires_at REAL,
                      token_revoked_at REAL DEFAULT 0,
                      settings TEXT DEFAULT '{}'
                     )''', commit=True)

        pg_migration_cols = [
            ("email",          "TEXT"),
            ("name",           "TEXT"),
            ("provider",       "TEXT DEFAULT 'local'"),
            ("created_at",     "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ("updated_at",     "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ("role",           "TEXT DEFAULT 'user'"),
            ("is_banned",      "INTEGER DEFAULT 0"),
            ("plan",           "TEXT DEFAULT 'free'"),
            ("daily_tokens",   "INTEGER DEFAULT 0"),
            ("quota_reset_ts", "REAL DEFAULT 0"),
            ("otp_code",       "TEXT"),
            ("otp_expires_at", "REAL"),
            ("token_revoked_at", "REAL DEFAULT 0"),
            ("settings",       "TEXT DEFAULT '{}'"),
        ]
        for col_name, col_def in pg_migration_cols:
            try:
                execute_query(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_def}", commit=True)
            except Exception:
                pass
        execute_query('''CREATE TABLE IF NOT EXISTS auth_sessions (
                      id TEXT PRIMARY KEY,
                      user_id INTEGER NOT NULL,
                      refresh_token TEXT UNIQUE NOT NULL,
                      device_info TEXT DEFAULT '',
                      ip_address TEXT DEFAULT '',
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      expires_at TEXT NOT NULL
                     )''', commit=True)
        execute_query('''CREATE TABLE IF NOT EXISTS workflows (
                      id TEXT PRIMARY KEY,
                      user_id INTEGER,
                      task TEXT,
                      score INTEGER,
                      status TEXT,
                      timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                     )''', commit=True)
        execute_query('''CREATE TABLE IF NOT EXISTS revoked_tokens (
                      token_hash TEXT PRIMARY KEY,
                      expires_at REAL NOT NULL,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                     )''', commit=True)
        execute_query('''CREATE TABLE IF NOT EXISTS api_keys (
                      id TEXT PRIMARY KEY,
                      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                      name TEXT NOT NULL,
                      key_hash TEXT UNIQUE NOT NULL,
                      prefix TEXT NOT NULL,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      revoked_at REAL DEFAULT NULL,
                      last_used_at REAL DEFAULT NULL
                     )''', commit=True)
        execute_query('''CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash)''', commit=True)
        execute_query('''CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id)''', commit=True)
        try:
            from core.environment import get_environment_mode, EnvironmentMode
            if get_environment_mode() in (EnvironmentMode.LOCAL_DEVELOPMENT, EnvironmentMode.CI_TEST) or os.environ.get("SEED_ADMIN", "1") == "1":
                user_cnt = execute_query("SELECT COUNT(*) FROM users", fetchone=True)
                if user_cnt and user_cnt[0] == 0:
                    admin_email = os.environ.get("AETHERION_ADMIN_EMAIL", "admin@aetherion.ai")
                    admin_pass = os.environ.get("AETHERION_ADMIN_PASSWORD", "AdminPassword123!")
                    hashed = bcrypt.hashpw(admin_pass.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                    execute_query(
                        "INSERT INTO users (username, email, name, password, role, provider) "
                        "VALUES (%s, %s, %s, %s, 'admin', 'local')",
                        ("admin", admin_email, "Admin Operator", hashed),
                        commit=True
                    )
        except Exception:
            pass
        return

    # SQLite path â€” use a single persistent connection throughout
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("PRAGMA cache_size=-8000")

    c.execute('''CREATE TABLE IF NOT EXISTS users (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE,
                  password TEXT,
                  total_tasks INTEGER DEFAULT 0,
                  total_tokens INTEGER DEFAULT 0
                 )''')
    conn.commit()

    # Schema migrations â€” add columns if they don't exist yet
    c.execute("PRAGMA table_info(users)")
    existing_cols = {row[1] for row in c.fetchall()}

    migration_cols = [
        ("email",          "TEXT"),
        ("name",           "TEXT"),
        ("provider",       "TEXT DEFAULT 'local'"),
        ("created_at",     "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("updated_at",     "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("role",           "TEXT DEFAULT 'user'"),
        ("is_banned",      "INTEGER DEFAULT 0"),
        ("plan",           "TEXT DEFAULT 'free'"),
        ("daily_tokens",   "INTEGER DEFAULT 0"),
        ("quota_reset_ts", "REAL DEFAULT 0"),
        ("otp_code",       "TEXT"),
        ("otp_expires_at", "REAL"),
        ("token_revoked_at", "REAL DEFAULT 0"),
        ("settings",       "TEXT DEFAULT '{}'"),
    ]
    for col_name, col_def in migration_cols:
        if col_name not in existing_cols:
            c.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")

    try:
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email "
            "ON users(email) WHERE email IS NOT NULL"
        )
    except sqlite3.OperationalError:
        pass

    c.execute('''CREATE TABLE IF NOT EXISTS auth_sessions (
                  id TEXT PRIMARY KEY,
                  user_id INTEGER NOT NULL,
                  refresh_token TEXT UNIQUE NOT NULL,
                  device_info TEXT DEFAULT '',
                  ip_address TEXT DEFAULT '',
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  expires_at TEXT NOT NULL,
                  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                 )''')

    c.execute('''CREATE TABLE IF NOT EXISTS workflows (
                  id TEXT PRIMARY KEY,
                  user_id INTEGER,
                  task TEXT,
                  score INTEGER,
                  status TEXT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                 )''')

    c.execute('''CREATE TABLE IF NOT EXISTS revoked_tokens (
                  token_hash TEXT PRIMARY KEY,
                  expires_at REAL NOT NULL,
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                 )''')

    c.execute('''CREATE TABLE IF NOT EXISTS api_keys (
                  id TEXT PRIMARY KEY,
                  user_id INTEGER NOT NULL,
                  name TEXT NOT NULL,
                  key_hash TEXT UNIQUE NOT NULL,
                  prefix TEXT NOT NULL,
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  revoked_at REAL DEFAULT NULL,
                  last_used_at REAL DEFAULT NULL,
                  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                 )''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id)''')

    # Seed initial developer admin user if users table is empty in local-development / CI-test mode
    try:
        from core.environment import get_environment_mode, EnvironmentMode
        if get_environment_mode() in (EnvironmentMode.LOCAL_DEVELOPMENT, EnvironmentMode.CI_TEST):
            c.execute("SELECT COUNT(*) FROM users")
            count_row = c.fetchone()
            if count_row and count_row[0] == 0:
                admin_email = os.environ.get("AETHERION_ADMIN_EMAIL", "admin@aetherion.ai")
                admin_pass = os.environ.get("AETHERION_ADMIN_PASSWORD", "AdminPassword123!")
                hashed = bcrypt.hashpw(admin_pass.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                c.execute(
                    "INSERT INTO users (username, email, name, password, role, provider) "
                    "VALUES (?, ?, ?, ?, 'admin', 'local')",
                    ("admin", admin_email, "Admin Operator", hashed)
                )
    except Exception as e:
        logger.debug("Automatic initial developer seeding skipped: %s", e)

    conn.commit()
    conn.close()


init_db()


# â”€â”€â”€ Token helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _make_access_token(user_id: int, email: str = "", name: str = "", role: str = "user") -> str:
    payload = {
        "user_id": user_id,
        "email":   email or "",
        "name":    name or "",
        "role":    role or "user",
        "type":    "access",
        "jti":     str(uuid.uuid4()),
        "iat":     datetime.datetime.now(datetime.timezone.utc),
        "exp":     datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=ACCESS_TOKEN_MINUTES),
    }

    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def get_user_settings(user_id: int) -> dict:
    import json
    row = execute_query("SELECT settings FROM users WHERE id=?", (user_id,), fetchone=True)
    if not row or not row[0]:
        return {}
    try:
        return json.loads(row[0])
    except:
        return {}


def update_user_settings(user_id: int, settings: dict):
    import json
    settings_str = json.dumps(settings)
    execute_query("UPDATE users SET settings=? WHERE id=?", (settings_str, user_id), commit=True)


def _make_refresh_token() -> str:
    return secrets.token_hex(64)


def _hash_refresh_token(token: str) -> str:
    """SHA-256 hash of refresh token â€” only the hash is stored in DB."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _get_user_info(cursor, user_id: int):
    cursor.execute("SELECT id, email, name, provider, role, is_banned FROM users WHERE id = ?", (user_id,))
    return cursor.fetchone()


# â”€â”€â”€ Session management â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def create_session(user_id: int, device_info: str = "", ip_address: str = "") -> dict:
    user = execute_query("SELECT id, email, name, provider, role, is_banned FROM users WHERE id = ?", (user_id,), fetchone=True)

    session_id = str(uuid.uuid4())
    refresh = _make_refresh_token()
    refresh_hash = _hash_refresh_token(refresh)
    expires_at = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=REFRESH_TOKEN_DAYS)
    ).isoformat()

    execute_query(
        "INSERT INTO auth_sessions (id, user_id, refresh_token, device_info, ip_address, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, user_id, refresh_hash, device_info[:255], ip_address[:64], expires_at),
        commit=True
    )

    email = user[1] if user else ""
    name  = user[2] if user else ""
    role  = user[4] if user and len(user) > 4 else "user"
    access = _make_access_token(user_id, email or "", name or "", role or "user")
    return {
        "access_token":  access,
        "refresh_token": refresh,
        "session_id":    session_id,
        "expires_in":    ACCESS_TOKEN_MINUTES * 60,
    }


def refresh_access_token(refresh_token: str, ip_address: str = ""):
    """B2-Fixed: uses get_db_connection() â€” works for both SQLite and Postgres."""
    token_hash = _hash_refresh_token(refresh_token)
    row = execute_query(
        "SELECT id, user_id, expires_at, device_info FROM auth_sessions WHERE refresh_token = ?",
        (token_hash,), fetchone=True
    )
    if not row:
        return False, "Invalid refresh token"

    session_id, user_id, expires_at_str, device_info = row
    try:
        expires_at = datetime.datetime.fromisoformat(str(expires_at_str))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return False, "Session corrupt"

    if datetime.datetime.now(datetime.timezone.utc) > expires_at:
        execute_query("DELETE FROM auth_sessions WHERE id = ?", (session_id,), commit=True)
        return False, "Refresh token expired"

    new_refresh = _make_refresh_token()
    new_refresh_hash = _hash_refresh_token(new_refresh)
    new_expires = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=REFRESH_TOKEN_DAYS)
    ).isoformat()
    execute_query(
        "UPDATE auth_sessions SET refresh_token = ?, expires_at = ?, ip_address = ? WHERE id = ?",
        (new_refresh_hash, new_expires, ip_address[:64], session_id),
        commit=True
    )

    user = execute_query(
        "SELECT id, email, name, provider, role, is_banned FROM users WHERE id = ?",
        (user_id,), fetchone=True
    )
    email = user[1] if user else ""
    name  = user[2] if user else ""
    role  = user[4] if user and len(user) > 4 else "user"
    access = _make_access_token(user_id, email or "", name or "", role or "user")
    return True, {
        "access_token":  access,
        "refresh_token": new_refresh,
        "expires_in":    ACCESS_TOKEN_MINUTES * 60,
    }


def revoke_session(refresh_token: str):
    """B2-Fixed: uses get_db_connection() â€” works for both SQLite and Postgres."""
    token_hash = _hash_refresh_token(refresh_token)
    execute_query(
        "DELETE FROM auth_sessions WHERE refresh_token = ?",
        (token_hash,), commit=True
    )


def revoke_session_by_id(session_id: str, user_id: int):
    """B2-Fixed: uses get_db_connection() â€” works for both SQLite and Postgres."""
    execute_query(
        "DELETE FROM auth_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id), commit=True
    )


def revoke_all_sessions(user_id: int):
    """B2-Fixed: uses get_db_connection() — works for both SQLite and Postgres."""
    now = time.time()
    try:
        execute_query(
            "UPDATE users SET token_revoked_at = ? WHERE id = ?",
            (now, user_id), commit=True
        )
    except Exception as e:
        logger.warning("[AUTH] Failed to update token_revoked_at for user %s: %s", user_id, e)
    execute_query(
        "DELETE FROM auth_sessions WHERE user_id = ?",
        (user_id,), commit=True
    )


# ── Token Blacklist / Revocation (OWASP ASVS 3.3.1) ──────────────────────────

_LOCAL_REVOKED_TOKENS: dict[str, float] = {}
_LOCAL_REVOKED_LOCK = threading.Lock()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def revoke_token(token: str):
    """
    Revoke an access token across in-memory cache, Redis blacklist, and database.
    OWASP ASVS 3.3.1 compliance.
    """
    if not token or not isinstance(token, str):
        return
    token = token.strip()
    if token.startswith("Bearer "):
        token = token[7:].strip()
    if not token:
        return

    token_hash = _hash_token(token)
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
        exp = float(unverified.get("exp", time.time() + (ACCESS_TOKEN_MINUTES * 60)))
    except Exception:
        exp = time.time() + (ACCESS_TOKEN_MINUTES * 60)

    now = time.time()
    ttl = max(60, int(exp - now))

    # 1. Thread-safe in-memory cache
    with _LOCAL_REVOKED_LOCK:
        _LOCAL_REVOKED_TOKENS[token_hash] = exp
        # Prune expired tokens periodically
        for k in [k for k, v in list(_LOCAL_REVOKED_TOKENS.items()) if v <= now]:
            _LOCAL_REVOKED_TOKENS.pop(k, None)

    # 2. Redis coordination (if available)
    try:
        from redis_layer import get_nx_redis
        r = get_nx_redis()
        if r:
            r.set(f"token_blacklist:{token_hash}", "revoked", ex=ttl)
    except Exception as e:
        logger.debug("[AUTH] Redis token revoke failed: %s", e)

    # 3. Durable DB persistence
    try:
        if _is_postgres():
            execute_query(
                "INSERT INTO revoked_tokens (token_hash, expires_at) VALUES (%s, %s) "
                "ON CONFLICT (token_hash) DO UPDATE SET expires_at = EXCLUDED.expires_at",
                (token_hash, exp), commit=True
            )
        else:
            execute_query(
                "INSERT OR REPLACE INTO revoked_tokens (token_hash, expires_at) VALUES (?, ?)",
                (token_hash, exp), commit=True
            )
    except Exception as e:
        logger.debug("[AUTH] DB token revoke failed: %s", e)


def is_token_revoked(token: str) -> bool:
    """
    Check if a token has been explicitly revoked via logout or administrative action.
    """
    if not token or not isinstance(token, str):
        return False
    token = token.strip()
    if token.startswith("Bearer "):
        token = token[7:].strip()
    if not token:
        return False

    token_hash = _hash_token(token)
    now = time.time()

    # 1. In-memory check
    with _LOCAL_REVOKED_LOCK:
        if token_hash in _LOCAL_REVOKED_TOKENS:
            if _LOCAL_REVOKED_TOKENS[token_hash] > now:
                return True
            else:
                _LOCAL_REVOKED_TOKENS.pop(token_hash, None)

    # 2. Redis check
    try:
        from redis_layer import get_nx_redis
        r = get_nx_redis()
        if r and getattr(r, "available", True):
            if hasattr(r, "exists") and r.exists(f"token_blacklist:{token_hash}"):
                return True
            if r.get(f"token_blacklist:{token_hash}") is not None:
                return True
    except Exception:
        pass

    # 3. DB check
    try:
        row = execute_query(
            "SELECT expires_at FROM revoked_tokens WHERE token_hash = ?",
            (token_hash,), fetchone=True
        )
        if row:
            if float(row[0]) > now:
                return True
            else:
                execute_query("DELETE FROM revoked_tokens WHERE token_hash = ?", (token_hash,), commit=True)
    except Exception:
        pass

    # 4. User global revocation cutoff check (logout_everywhere / password change)
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
        uid = unverified.get("user_id")
        iat = unverified.get("iat", 0)
        if uid:
            u_row = execute_query("SELECT token_revoked_at FROM users WHERE id = ?", (uid,), fetchone=True)
            if u_row and u_row[0] and iat <= float(u_row[0]):
                return True
    except Exception:
        pass

    return False




def list_sessions(user_id: int) -> list:
    """B2-Fixed: uses get_db_connection() â€” works for both SQLite and Postgres."""
    rows = execute_query(
        "SELECT id, device_info, ip_address, created_at, expires_at "
        "FROM auth_sessions WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,), fetchall=True
    ) or []
    return [
        {
            "id":          r[0],
            "device_info": r[1] or "",
            "ip_address":  r[2] or "",
            "created_at":  str(r[3]),
            "expires_at":  str(r[4]),
        }
        for r in rows
    ]


# â”€â”€â”€ Signup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def auth_signup(identifier: str, password: str, name: str = "", email: str = None):
    """
    Unified signup. `identifier` may be an email or a legacy username.
    Returns (True, user_id) on success, (False, error_msg) on failure.
    """
    if not identifier or not password:
        return False, "Email and password required"
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    if len(identifier) > 120:
        return False, "Identifier too long"

    is_email = "@" in identifier
    resolved_email    = email or (identifier if is_email else None)
    resolved_username = identifier if not is_email else identifier.split("@")[0]

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        if resolved_email:
            execute_query(
                "INSERT INTO users (username, email, name, password, provider) "
                "VALUES (?, ?, ?, ?, 'local')",
                (resolved_username, resolved_email, name or resolved_username, hashed),
                commit=True
            )
        else:
            execute_query(
                "INSERT INTO users (username, password, provider) VALUES (?, ?, 'local')",
                (resolved_username, hashed),
                commit=True
            )
        # Fetch the newly created user_id
        user_id_row = execute_query(
            "SELECT id FROM users WHERE username = ?",
            (resolved_username,), fetchone=True
        )
        user_id = user_id_row[0] if user_id_row else None
        try:
            from observability import user_onboarding_total
            user_onboarding_total.labels(event="signup_success").inc()
        except Exception:
            pass
        return True, user_id
    except Exception as e:
        err = str(e).lower()
        if "email" in err or "unique" in err:
            if "email" in err:
                return False, "Email already registered"
            return False, "Username already exists"
        raise


# â”€â”€â”€ Login â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def auth_login(identifier: str, password: str, ip: str = ""):
    """
    Login with email or username.
    Returns (True, user_id) on success, (False, error_msg) on failure.
    """
    if not identifier or not password:
        return False, "Email and password required"

    bf_key = f"login:{ip}:{identifier.lower()}"
    if _is_locked_out(bf_key):
        return False, "Too many failed attempts. Please wait 1 minute."

    conn = get_db_connection()
    try:
        c = conn.cursor()
        query_field = "email" if "@" in identifier else "username"
        ph = "%s" if _is_postgres() else "?"
        c.execute(f"SELECT id, password FROM users WHERE {query_field} = {ph}", (identifier,))
        user = c.fetchone()
    finally:
        _release_db_connection(conn)

    if user and user[1] and bcrypt.checkpw(password.encode("utf-8"), user[1].encode("utf-8")):
        _clear_failed(bf_key)
        try:
            from observability import user_onboarding_total
            user_onboarding_total.labels(event="login_success").inc()
        except Exception:
            pass
        return True, user[0]

    _record_failed(bf_key)
    return False, "Invalid credentials"


# ── API Key Lifecycle ──────────────────────────────────────────────────────────

def create_api_key(user_id: int, name: str) -> tuple[bool, dict | str]:
    """Issue a new personal API key for user_id. Returns (ok, key_dict_or_error).
    The raw secret token is returned ONLY once in key_dict['key'].
    The database persists only the SHA-256 hash and masked prefix.
    """
    clean_name = (name or "").strip()
    if not clean_name or clean_name.lower() in ("nan", "none", "null", "undefined"):
        return False, "Key label cannot be empty or invalid"
    if len(clean_name) > 64:
        clean_name = clean_name[:64]

    raw_token = f"aeth_live_{secrets.token_hex(24)}"
    key_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    prefix = f"aeth_{raw_token[10:14]}****"
    key_id = f"key_{secrets.token_hex(8)}"
    created_str = time.strftime("%Y-%m-%d %H:%M")

    try:
        execute_query(
            "INSERT INTO api_keys (id, user_id, name, key_hash, prefix, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key_id, user_id, clean_name, key_hash, prefix, created_str),
            commit=True,
        )
        logger.info("[Auth] Issued API key %s (prefix=%s) for user_id=%s", key_id, prefix, user_id)
        return True, {
            "id": key_id,
            "name": clean_name,
            "prefix": prefix,
            "created": created_str,
            "last_used": "Never",
            "key": raw_token,
        }
    except Exception as exc:
        logger.error("Failed to issue API key for user %s: %s", user_id, exc)
        return False, "Failed to create API key"


def list_api_keys(user_id: int) -> list[dict]:
    """List active API keys owned by user_id. Raw keys are NEVER returned."""
    try:
        rows = execute_query(
            "SELECT id, name, prefix, created_at, last_used_at, revoked_at "
            "FROM api_keys WHERE user_id = ? AND (revoked_at IS NULL OR revoked_at = 0) "
            "ORDER BY created_at DESC",
            (user_id,),
            fetchall=True,
        ) or []
        res = []
        for r in rows:
            kid, kname, kpfx, kcreated, klast_used, krevoked = r
            if klast_used and isinstance(klast_used, (int, float)):
                used_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(klast_used))
            else:
                used_str = "Never"
            res.append({
                "id": str(kid),
                "name": kname or "unnamed",
                "prefix": kpfx or "aeth_****",
                "created": str(kcreated) if kcreated else "—",
                "last_used": used_str,
            })
        return res
    except Exception as exc:
        logger.error("Failed to list API keys for user %s: %s", user_id, exc)
        return []


def revoke_api_key(user_id: int, key_id: str) -> bool:
    """Revoke an API key. Enforces ownership: users can only revoke their own keys."""
    try:
        now = time.time()
        execute_query(
            "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND user_id = ? AND (revoked_at IS NULL OR revoked_at = 0)",
            (now, key_id, user_id),
            commit=True,
        )
        row = execute_query(
            "SELECT revoked_at FROM api_keys WHERE id = ? AND user_id = ?",
            (key_id, user_id),
            fetchone=True,
        )
        if row and row[0]:
            logger.info("[Auth] Revoked API key %s for user_id=%s", key_id, user_id)
            return True
        return False
    except Exception as exc:
        logger.error("Failed to revoke API key %s for user %s: %s", key_id, user_id, exc)
        return False


def verify_api_key(raw_key: str) -> tuple[bool, dict | None, str]:
    """Authenticate raw API key, enforcing active status and user account state."""
    if not raw_key or not raw_key.startswith("aeth_"):
        return False, None, "INVALID_API_KEY"

    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    try:
        row = execute_query(
            "SELECT k.id, k.user_id, k.revoked_at, u.email, u.name, u.role, u.is_banned, u.plan "
            "FROM api_keys k "
            "JOIN users u ON k.user_id = u.id "
            "WHERE k.key_hash = ?",
            (key_hash,),
            fetchone=True,
        )
        if not row:
            return False, None, "INVALID_API_KEY"

        key_id, user_id, revoked_at, email, name, role, is_banned, plan = row
        if revoked_at is not None and float(revoked_at or 0) > 0:
            return False, None, "API_KEY_REVOKED"

        if is_banned:
            return False, None, "BANNED"

        try:
            execute_query(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (time.time(), key_id),
                commit=True,
            )
        except Exception:
            pass

        return True, {
            "key_id": key_id,
            "user_id": user_id,
            "email": email or "",
            "name": name or "",
            "role": role or "user",
            "plan": plan or "free",
        }, ""
    except Exception as exc:
        logger.error("API key verification exception: %s", exc)
        return False, None, "AUTH_ERROR"


# ── Auth decorator ──────────────────────────────────────────────────────────────

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "").strip()
        x_api_key = request.headers.get("X-API-Key", "").strip()

        # 1. API key authentication path (X-API-Key or Bearer aeth_...)
        api_key_candidate = None
        if x_api_key:
            api_key_candidate = x_api_key
        elif auth_header.startswith("Bearer aeth_live_") or auth_header.startswith("Bearer aeth_"):
            api_key_candidate = auth_header[7:].strip()

        if api_key_candidate:
            ok, user_data, err_code = verify_api_key(api_key_candidate)
            if not ok:
                if err_code == "BANNED":
                    return jsonify({"ok": False, "error": {"code": "BANNED", "message": "Account suspended"}}), 403
                if err_code == "API_KEY_REVOKED":
                    return jsonify({"ok": False, "error": {"code": "API_KEY_REVOKED", "message": "API key has been revoked"}}), 401
                if err_code == "AUTH_ERROR":
                    return jsonify({"ok": False, "error": {"code": "AUTH_ERROR", "message": "Authorization service unavailable"}}), 503
                return jsonify({"ok": False, "error": {"code": "INVALID_API_KEY", "message": "Invalid API key"}}), 401

            g.user_id     = user_data["user_id"]
            g.user_email  = user_data["email"]
            g.user_name   = user_data["name"]
            g.user_role   = user_data["role"]
            g.auth_method = "api_key"
            g.api_key_id  = user_data["key_id"]
            return f(*args, **kwargs)

        # 2. Existing Bearer JWT path
        token = None
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

        # Support session-based auth for testing (Flask session_transaction).
        # BUGFIX (Phase 5): the prior check `hasattr(request, 'session')` was
        # always False (Flask exposes `session` as its own proxy, not an
        # attribute of `request`) AND `session` was never imported, so this
        # intended fallback was dead code.
        if not token and "user_id" in session:
            g.user_id = session.get('user_id')
            g.user_email = session.get('user_email', '')
            g.user_name = session.get('user_name', '')
            g.user_role = session.get('user_role', 'user')
            return f(*args, **kwargs)

        if not token and _dev_auth_enabled() and request.args.get("dev") == "1":
            logger.warning(
                "[AUTH] Unauthenticated dev=1 bypass granted for %s (environment=%s)",
                request.path, _current_environment() or "<unset>",
            )
            g.user_id    = 1
            g.user_email = ""
            g.user_name  = ""
            g.user_role  = "user"
            return f(*args, **kwargs)

        if not token:
            return jsonify({"ok": False, "error": {"code": "UNAUTHORIZED", "message": "Unauthorized: Token is missing"}}), 401

        try:
            data = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            if data.get("type") != "access":
                return jsonify({"ok": False, "error": {"code": "INVALID_TOKEN_TYPE", "message": "Invalid token type"}}), 401
            g.user_id    = data["user_id"]
            g.user_email = data.get("email", "")
            g.user_name  = data.get("name",  "")
            g.user_role  = data.get("role",  "user")
        except jwt.ExpiredSignatureError:
            return jsonify({"ok": False, "error": {"code": "TOKEN_EXPIRED", "message": "Token has expired"}}), 401
        except jwt.InvalidTokenError:
            return jsonify({"ok": False, "error": {"code": "INVALID_TOKEN", "message": "Token is invalid"}}), 401
        except Exception:
            return jsonify({"ok": False, "error": {"code": "AUTH_FAILED", "message": "Authentication failed"}}), 401

        if is_token_revoked(token):
            return jsonify({"ok": False, "error": {"code": "TOKEN_REVOKED", "message": "Token has been revoked"}}), 401

        # Ban check — live DB lookup so bans take effect without token expiry
        # B2-Fixed: uses get_db_connection() — works for both SQLite and Postgres.
        try:
            _row = execute_query(
                "SELECT is_banned, token_revoked_at FROM users WHERE id = ?",
                (g.user_id,), fetchone=True
            )
            if _row and _row[0]:
                return jsonify({"ok": False, "error": {"code": "BANNED", "message": "Account suspended"}}), 403
            if _row and len(_row) > 1 and _row[1]:
                token_iat = data.get("iat", 0)
                if token_iat <= float(_row[1]):
                    return jsonify({"ok": False, "error": {"code": "TOKEN_REVOKED", "message": "Session has been revoked"}}), 401
        except Exception:
            logger.error("Ban check failed", exc_info=True)
            # BUGFIX (Phase 5): status must be a tuple element, not a jsonify
            # argument — jsonify({...}, 503) serializes 503 INTO the body with
            # HTTP 200, so a DB outage in the ban check failed OPEN.
            return jsonify({"ok": False, "error": {"code": "AUTH_ERROR", "message": "Authorization service unavailable"}}), 503

        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        if getattr(g, "auth_method", "") == "api_key":
            return jsonify({"ok": False, "error": {"code": "FORBIDDEN", "message": "API keys cannot access administrative endpoints. Interactive session required."}}), 403
        role = getattr(g, "user_role", "user")
        if role != "admin":
            return jsonify({"ok": False, "error": {"code": "FORBIDDEN", "message": "Admin privileges required"}}), 403
        return f(*args, **kwargs)
    return decorated



# ── OAuth user ──────────────────────────────────────────────────────────────────

def get_or_create_oauth_user(email: str, name: str, provider: str) -> int:
    """B2-Fixed: uses get_db_connection() â€” works for both SQLite and Postgres."""
    row = execute_query("SELECT id FROM users WHERE email = ?", (email,), fetchone=True)
    if row:
        user_id = row[0]
        execute_query(
            "UPDATE users SET name = ?, provider = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name, provider, user_id), commit=True
        )
        return user_id

    username = email.split("@")[0] + "_" + secrets.token_hex(4)
    execute_query(
        "INSERT INTO users (username, email, name, provider) VALUES (?, ?, ?, ?)",
        (username, email, name, provider), commit=True
    )
    new_row = execute_query("SELECT id FROM users WHERE email = ?", (email,), fetchone=True)
    return new_row[0] if new_row else None


def verify_token(token: str) -> dict:
    if not token or is_token_revoked(token):
        return None
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        if data.get("type") != "access":
            return None
        user_id = data.get("user_id")
        if user_id:
            try:
                row = execute_query("SELECT is_banned, token_revoked_at FROM users WHERE id = ?", (user_id,), fetchone=True)
                if row:
                    if row[0]:
                        return None
                    revoked_at = row[1] if (len(row) > 1 and row[1]) else 0.0
                    token_iat = data.get("iat", 0)
                    if revoked_at and token_iat <= float(revoked_at):
                        return None
            except Exception:
                pass
        return data
    except Exception:
        return None

def _verify_token(token: str):
    if not token or is_token_revoked(token):
        raise Exception("Token has been revoked")
    data = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    if data.get("type") != "access":
        raise Exception("Invalid token type")
    user_id = data.get("user_id")
    if user_id:
        row = execute_query("SELECT is_banned, token_revoked_at FROM users WHERE id = ?", (user_id,), fetchone=True)
        if row:
            if row[0]:
                raise Exception("Account suspended")
            revoked_at = row[1] if (len(row) > 1 and row[1]) else 0.0
            token_iat = data.get("iat", 0)
            if revoked_at and token_iat <= float(revoked_at):
                raise Exception("Token has been revoked")
    return data


# â”€â”€â”€ Auth decorator â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€




# â”€â”€â”€ Usage tracking + dashboard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def check_quota(user_id: int) -> bool:
    """Checks if a user has exceeded their daily token quota."""
    row = execute_query(
        "SELECT plan, daily_tokens FROM users WHERE id = ?",
        (user_id,), fetchone=True
    )
    if not row:
        return False
    plan, daily_tokens = row[0], row[1]
    limits = {"free": 50000, "pro": 500000, "admin": float('inf')}
    if plan == "free" and daily_tokens > limits["free"] * 0.9:
        logger.warning(f"[Auth] User {user_id} nearing free tier limit.")
    return daily_tokens < limits.get(plan, 50000)

def increment_usage(user_id: int, tokens: int, tasks: int = 1):
    """Safely increments total AND daily usage metrics for a user.
    B2-Fixed: uses execute_query() â€” works for both SQLite and Postgres.
    """
    now = time.time()
    row = execute_query(
        "SELECT quota_reset_ts, daily_tokens FROM users WHERE id = ?",
        (user_id,), fetchone=True
    )
    if not row:
        return
    reset_ts = float(row[0] or 0)
    if now > reset_ts + 86400:
        execute_query(
            "UPDATE users SET daily_tokens = 0, quota_reset_ts = ? WHERE id = ?",
            (now, user_id), commit=True
        )
    execute_query(
        "UPDATE users SET total_tokens = total_tokens + ?, "
        "total_tasks = total_tasks + ?, daily_tokens = daily_tokens + ? "
        "WHERE id = ?",
        (tokens, tasks, tokens, user_id), commit=True
    )


def track_usage(user_id, wid, task, score, status, tokens=0):
    """B2-Fixed: uses execute_query() â€” works for both SQLite and Postgres."""
    try:
        execute_query(
            "INSERT INTO workflows (id, user_id, task, score, status) VALUES (?, ?, ?, ?, ?)",
            (wid, user_id, task, score, status), commit=True
        )
    except Exception:
        pass  # workflow already exists â€” ignore duplicate
    increment_usage(user_id, tokens, 1)


def get_dashboard(user_id):
    """B2-Fixed: uses execute_query() â€” works for both SQLite and Postgres."""
    stats = execute_query(
        "SELECT total_tasks, total_tokens, email, name, provider, username FROM users WHERE id = ?",
        (user_id,), fetchone=True
    )
    recent_rows = execute_query(
        "SELECT id, task, score, status, timestamp FROM workflows "
        "WHERE user_id = ? ORDER BY timestamp DESC LIMIT 10",
        (user_id,), fetchall=True
    ) or []
    recent = [
        {"id": r[0], "task": r[1], "score": r[2], "status": r[3], "timestamp": str(r[4])}
        for r in recent_rows
    ]
    if not stats:
        return {"total_tasks": 0, "total_tokens": 0, "success_rate": 0, "recent_workflows": []}
    successes = sum(
        1 for w in recent
        if w["status"] in ("PASSED", "Success", "PARTIAL SUCCESS", "excellent", "good")
    )
    rate = round((successes / len(recent)) * 100, 1) if recent else 0
    return {
        "user_id":          user_id,
        "email":            stats[2] or "",
        "name":             stats[3] or "",
        "provider":         stats[4] or "local",
        "username":         stats[5] or "",
        "total_tasks":      stats[0],
        "total_tokens":     stats[1],
        "success_rate":     rate,
        "recent_workflows": recent,
    }


def get_user_workspace(user_id):
    workspace = f"./workspace/{user_id}"
    artifacts = f"./artifacts/{user_id}"
    os.makedirs(workspace, exist_ok=True)
    os.makedirs(artifacts, exist_ok=True)
    return os.path.abspath(workspace), os.path.abspath(artifacts)

