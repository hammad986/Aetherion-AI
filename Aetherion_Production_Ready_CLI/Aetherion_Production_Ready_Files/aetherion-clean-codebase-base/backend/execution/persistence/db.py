"""
backend/execution/persistence/db.py — Unified Phase 4 SQLite Persistence Engine.
Manages schema initialization, migrations, and ACID connection handling
for Sessions, Memories, and Background Jobs across multiple tenants.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aetherion.persistence.db")

DEFAULT_STATE_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "aetherion_state.db"


class StateDatabase:
    """Thread-safe SQLite connection factory and schema migration runner for Phase 4."""

    _lock = threading.Lock()

    def __init__(self, db_path: Optional[Path | str] = None):
        if db_path is None:
            env_path = os.environ.get("AETHERION_STATE_DB")
            db_path = Path(env_path) if env_path else DEFAULT_STATE_DB_PATH
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Create a new SQLite connection configured for concurrent safety and foreign keys."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode by default; explicit transactions when needed
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self) -> None:
        """Run safe idempotent schema migrations for Phase 4 tables."""
        with self._lock:
            conn = self.get_connection()
            try:
                # ── 1. SESSIONS ──────────────────────────────────────────────
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        status TEXT NOT NULL,
                        model TEXT,
                        provider_id TEXT,
                        workspace_id TEXT,
                        summary TEXT,
                        message_count INTEGER DEFAULT 0,
                        token_usage INTEGER DEFAULT 0,
                        files_changed INTEGER DEFAULT 0,
                        metadata_json TEXT DEFAULT '{}',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        last_activity_at REAL NOT NULL,
                        archived_at REAL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS ix_sessions_owner ON sessions (owner_id, status);")
                conn.execute("CREATE INDEX IF NOT EXISTS ix_sessions_activity ON sessions (owner_id, last_activity_at DESC);")

                # ── 2. SESSION MESSAGES ──────────────────────────────────────
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS session_messages (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        owner_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        tool_name TEXT,
                        tool_call_id TEXT,
                        tool_result_json TEXT,
                        metadata_json TEXT DEFAULT '{}',
                        created_at REAL NOT NULL,
                        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS ix_session_messages ON session_messages (session_id, created_at ASC);")

                # ── 3. PERSISTENT MEMORIES ────────────────────────────────────
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS memories (
                        id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        source TEXT NOT NULL,
                        trust_level TEXT NOT NULL,
                        importance REAL DEFAULT 0.7,
                        tags_json TEXT DEFAULT '[]',
                        metadata_json TEXT DEFAULT '{}',
                        status TEXT NOT NULL DEFAULT 'ACTIVE',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS ix_memories_owner ON memories (owner_id, status);")
                conn.execute("CREATE INDEX IF NOT EXISTS ix_memories_type ON memories (owner_id, type);")

                # ── 3B. MEMORY VECTORS (Phase 5 & 6 Semantic Embeddings) ──────────
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS memory_vectors (
                        memory_id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        embedding_model TEXT NOT NULL,
                        dimensions INTEGER NOT NULL,
                        vector_blob BLOB NOT NULL,
                        content_hash TEXT DEFAULT '',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS ix_memory_vectors_owner ON memory_vectors (owner_id);")

                # Safe idempotent column migration for existing memories table (Phase 7 metadata)
                try:
                    cur = conn.execute("PRAGMA table_info(memories);")
                    mem_cols = {row[1] for row in cur.fetchall()}
                    if "confidence" not in mem_cols:
                        conn.execute("ALTER TABLE memories ADD COLUMN confidence REAL DEFAULT 1.0;")
                    if "provenance" not in mem_cols:
                        conn.execute("ALTER TABLE memories ADD COLUMN provenance TEXT DEFAULT '';")
                    if "created_by" not in mem_cols:
                        conn.execute("ALTER TABLE memories ADD COLUMN created_by TEXT DEFAULT '';")
                    if "last_validated_at" not in mem_cols:
                        conn.execute("ALTER TABLE memories ADD COLUMN last_validated_at REAL DEFAULT NULL;")
                    if "expires_at" not in mem_cols:
                        conn.execute("ALTER TABLE memories ADD COLUMN expires_at REAL DEFAULT NULL;")
                    if "revision" not in mem_cols:
                        conn.execute("ALTER TABLE memories ADD COLUMN revision INTEGER DEFAULT 1;")
                    if "content_hash" not in mem_cols:
                        conn.execute("ALTER TABLE memories ADD COLUMN content_hash TEXT DEFAULT '';")
                    if "embedding_version" not in mem_cols:
                        conn.execute("ALTER TABLE memories ADD COLUMN embedding_version TEXT DEFAULT 'v1';")
                    if "metadata_json" not in mem_cols:
                        conn.execute("ALTER TABLE memories ADD COLUMN metadata_json TEXT DEFAULT '{}';")
                except Exception as exc:
                    logger.debug("Migration check for memories Phase 7 columns: %s", exc)

                # Safe idempotent column migration for existing memory_vectors tables
                try:
                    cur = conn.execute("PRAGMA table_info(memory_vectors);")
                    cols = {row[1] for row in cur.fetchall()}
                    if "content_hash" not in cols:
                        conn.execute("ALTER TABLE memory_vectors ADD COLUMN content_hash TEXT DEFAULT '';")
                except Exception as exc:
                    logger.debug("Migration check for content_hash: %s", exc)

                # ── 3C. EMBEDDING CACHE (Phase 6 Multi-Tenant Cache & Phase 7 V2) ──
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS embedding_cache (
                        cache_key TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        model_name TEXT NOT NULL,
                        dimensions INTEGER NOT NULL,
                        vector_blob BLOB NOT NULL,
                        created_at REAL NOT NULL,
                        last_accessed_at REAL NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS ix_embedding_cache_owner ON embedding_cache (owner_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS ix_embedding_cache_lookup ON embedding_cache (owner_id, content_hash, model_name);")

                # Safe idempotent column migration for embedding_cache Phase 7 TTL and version
                try:
                    cur = conn.execute("PRAGMA table_info(embedding_cache);")
                    cache_cols = {row[1] for row in cur.fetchall()}
                    if "ttl" not in cache_cols:
                        conn.execute("ALTER TABLE embedding_cache ADD COLUMN ttl REAL DEFAULT NULL;")
                    if "version" not in cache_cols:
                        conn.execute("ALTER TABLE embedding_cache ADD COLUMN version TEXT DEFAULT 'v1';")
                except Exception as exc:
                    logger.debug("Migration check for embedding_cache Phase 7 columns: %s", exc)

                # ── 3D. MEMORY CONTRADICTIONS (Phase 7 Factual Consistency) ────────
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS memory_contradictions (
                        id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        memory_id_a TEXT NOT NULL,
                        memory_id_b TEXT NOT NULL,
                        contradiction_type TEXT NOT NULL,
                        description TEXT NOT NULL,
                        state TEXT NOT NULL DEFAULT 'conflicting',
                        confidence REAL DEFAULT 0.8,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        FOREIGN KEY (memory_id_a) REFERENCES memories(id) ON DELETE CASCADE,
                        FOREIGN KEY (memory_id_b) REFERENCES memories(id) ON DELETE CASCADE
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS ix_contradictions_owner ON memory_contradictions (owner_id, state);")

                # ── 3E. MEMORY CONSOLIDATIONS (Phase 7 Non-Destructive Consolidation) ──
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS memory_consolidations (
                        id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        primary_memory_id TEXT NOT NULL,
                        candidate_ids_json TEXT NOT NULL,
                        consolidation_type TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        state TEXT NOT NULL DEFAULT 'proposed',
                        confidence REAL DEFAULT 0.8,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        FOREIGN KEY (primary_memory_id) REFERENCES memories(id) ON DELETE CASCADE
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS ix_consolidations_owner ON memory_consolidations (owner_id, state);")

                # ── 3F. AGENT LEARNING SIGNALS (Phase 7 Validated Agent Learning) ────
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS agent_learning_signals (
                        id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        session_id TEXT,
                        signal_type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        validation_condition TEXT NOT NULL,
                        confidence REAL NOT NULL DEFAULT 0.7,
                        status TEXT NOT NULL DEFAULT 'pending_validation',
                        memory_id TEXT,
                        created_at REAL NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS ix_learning_signals_owner ON agent_learning_signals (owner_id, status);")

                # ── 3G. MEMORY FEEDBACK (Phase 7 Execution Outcome Tracking) ────────
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS memory_feedback (
                        id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        memory_id TEXT NOT NULL,
                        session_id TEXT,
                        query TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        relevance_score REAL NOT NULL DEFAULT 1.0,
                        created_at REAL NOT NULL,
                        FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS ix_memory_feedback_owner ON memory_feedback (owner_id, memory_id);")

                # ── 4. BACKGROUND JOBS ────────────────────────────────────────
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS background_jobs (
                        id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        status TEXT NOT NULL,
                        task TEXT NOT NULL,
                        workspace_id TEXT NOT NULL,
                        session_id TEXT,
                        created_at REAL NOT NULL,
                        started_at REAL,
                        updated_at REAL NOT NULL,
                        finished_at REAL,
                        error TEXT,
                        metadata_json TEXT DEFAULT '{}',
                        result_json TEXT,
                        attempt_number INTEGER DEFAULT 1,
                        previous_error TEXT
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS ix_jobs_owner ON background_jobs (owner_id, status);")
                conn.execute("CREATE INDEX IF NOT EXISTS ix_jobs_created ON background_jobs (owner_id, created_at DESC);")

                # ── 5. JOB ATTEMPTS ───────────────────────────────────────────
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS job_attempts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL,
                        owner_id TEXT NOT NULL,
                        attempt_number INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        started_at REAL NOT NULL,
                        finished_at REAL,
                        error TEXT,
                        result_json TEXT,
                        FOREIGN KEY (job_id) REFERENCES background_jobs(id) ON DELETE CASCADE
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS ix_job_attempts ON job_attempts (job_id, attempt_number ASC);")

                # ── 6. SHADOW QUERY CALIBRATION STATES (Phase 8 SQLite Persistence) ──
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS calibration_states (
                        tenant_id TEXT PRIMARY KEY,
                        corpus_size INTEGER NOT NULL DEFAULT 0,
                        index_generation INTEGER NOT NULL DEFAULT 0,
                        model_name TEXT NOT NULL DEFAULT '',
                        model_version TEXT NOT NULL DEFAULT 'v1',
                        config_fingerprint TEXT NOT NULL DEFAULT '',
                        samples INTEGER NOT NULL DEFAULT 0,
                        recall_sum REAL NOT NULL DEFAULT 0.0,
                        min_samples INTEGER NOT NULL DEFAULT 10,
                        last_updated_at REAL NOT NULL DEFAULT 0.0
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS ix_calibration_states_tenant ON calibration_states (tenant_id);")

            finally:
                conn.close()
