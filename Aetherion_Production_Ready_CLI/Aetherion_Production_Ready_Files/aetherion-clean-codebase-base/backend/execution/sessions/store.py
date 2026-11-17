"""
backend/execution/sessions/store.py — Persistent SQLite Store for Sessions and Messages.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..persistence.db import StateDatabase
from .models import Session, SessionMessage, SessionStatus

logger = logging.getLogger("aetherion.sessions.store")


def _require_valid_owner_id(owner_id: Any) -> str:
    """Validate that owner_id is strictly a non-empty string, preventing service-layer BOLA."""
    if not isinstance(owner_id, str):
        raise KeyError("A valid non-empty string owner_id is required.")
    normalized_owner_id = owner_id.strip()
    if not normalized_owner_id:
        raise KeyError("A valid non-empty string owner_id is required.")
    return normalized_owner_id


class SessionStore:
    """Multi-tenant SQLite store for persistent execution and conversation sessions."""

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db = StateDatabase(db_path)

    def create_session(self, session: Session) -> Session:
        """Insert a newly created session into storage."""
        session.validate()
        _require_valid_owner_id(session.owner_id)
        conn = self.db.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, owner_id, title, status, model, provider_id, workspace_id,
                    summary, message_count, token_usage, files_changed, metadata_json,
                    created_at, updated_at, last_activity_at, archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.owner_id,
                    session.title,
                    session.status,
                    session.model,
                    session.provider_id,
                    session.workspace_id,
                    session.summary,
                    session.message_count,
                    session.token_usage,
                    session.files_changed,
                    json.dumps(session.metadata),
                    session.created_at,
                    session.updated_at,
                    session.last_activity_at,
                    session.archived_at,
                ),
            )
            return session
        finally:
            conn.close()

    def get_session_owner(self, session_id: str) -> Optional[str]:
        """Lookup only the owner identity of a session for authorization checks. Does not return session content."""
        if not session_id or not isinstance(session_id, str) or not session_id.strip():
            return None
        conn = self.db.get_connection()
        try:
            cur = conn.execute("SELECT owner_id FROM sessions WHERE id = ?", (session_id.strip(),))
            row = cur.fetchone()
            return str(row["owner_id"]) if row else None
        finally:
            conn.close()

    def get_session(self, session_id: str, owner_id: str, include_deleted: bool = False) -> Optional[Session]:
        """Fetch session by ID with mandatory tenant scoping. By default, ignores soft-deleted sessions."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            query = "SELECT * FROM sessions WHERE id = ? AND owner_id = ?"
            params: list[Any] = [str(session_id).strip(), owner_id]

            if not include_deleted:
                query += f" AND status != '{SessionStatus.DELETED.value}'"

            cur = conn.execute(query, params)
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_session(row)
        finally:
            conn.close()

    def update_session(self, session: Session) -> Session:
        """Update existing session fields."""
        session.validate()
        session.updated_at = time.time()
        conn = self.db.get_connection()
        try:
            conn.execute(
                """
                UPDATE sessions SET
                    title = ?,
                    status = ?,
                    model = ?,
                    provider_id = ?,
                    workspace_id = ?,
                    summary = ?,
                    message_count = ?,
                    token_usage = ?,
                    files_changed = ?,
                    metadata_json = ?,
                    updated_at = ?,
                    last_activity_at = ?,
                    archived_at = ?
                WHERE id = ? AND owner_id = ?
                """,
                (
                    session.title,
                    session.status,
                    session.model,
                    session.provider_id,
                    session.workspace_id,
                    session.summary,
                    session.message_count,
                    session.token_usage,
                    session.files_changed,
                    json.dumps(session.metadata),
                    session.updated_at,
                    session.last_activity_at,
                    session.archived_at,
                    session.id,
                    session.owner_id,
                ),
            )
            return session
        finally:
            conn.close()

    def list_sessions(
        self,
        owner_id: str,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Session]:
        """List sessions owned by a tenant, sorted by last activity."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            query = "SELECT * FROM sessions WHERE owner_id = ?"
            params: list[Any] = [owner_id]

            if status:
                query += " AND status = ?"
                params.append(status)
            else:
                query += f" AND status NOT IN ('{SessionStatus.DELETED.value}', '{SessionStatus.ARCHIVED.value}')"

            query += " ORDER BY last_activity_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cur = conn.execute(query, params)
            return [self._row_to_session(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def search_sessions(
        self,
        owner_id: str,
        query: str,
        model: Optional[str] = None,
        provider_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Session]:
        """Search sessions by keyword matching across title and summary."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            sql = "SELECT * FROM sessions WHERE owner_id = ?"
            params: list[Any] = [owner_id]

            if status:
                sql += " AND status = ?"
                params.append(status)
            else:
                sql += f" AND status NOT IN ('{SessionStatus.DELETED.value}', '{SessionStatus.ARCHIVED.value}')"

            if model:
                sql += " AND model = ?"
                params.append(model)
            if provider_id:
                sql += " AND provider_id = ?"
                params.append(provider_id)
            if workspace_id:
                sql += " AND workspace_id = ?"
                params.append(workspace_id)

            if query and query.strip():
                sql += " AND (title LIKE ? OR summary LIKE ?)"
                pattern = f"%{query.strip()}%"
                params.extend([pattern, pattern])

            sql += " ORDER BY last_activity_at DESC LIMIT ?"
            params.append(limit)

            cur = conn.execute(sql, params)
            return [self._row_to_session(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def soft_delete_session(self, session_id: str, owner_id: str) -> bool:
        """Mark session as DELETED without destroying historical records."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            now = time.time()
            cur = conn.execute(
                f"""
                UPDATE sessions
                SET status = '{SessionStatus.DELETED.value}', updated_at = ?
                WHERE id = ? AND owner_id = ? AND status != '{SessionStatus.DELETED.value}'
                """,
                (now, session_id, owner_id),
            )
            return cur.rowcount > 0
        finally:
            conn.close()

    # ── Messages ─────────────────────────────────────────────────────────────

    def add_message(self, message: SessionMessage) -> SessionMessage:
        """Append a message to a session and increment message count."""
        _require_valid_owner_id(message.owner_id)
        conn = self.db.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO session_messages (
                    id, session_id, owner_id, role, content, tool_name,
                    tool_call_id, tool_result_json, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.session_id,
                    message.owner_id,
                    message.role,
                    message.content,
                    message.tool_name,
                    message.tool_call_id,
                    json.dumps(message.tool_result) if message.tool_result is not None else None,
                    json.dumps(message.metadata),
                    message.created_at,
                ),
            )
            # Update session last_activity_at and message_count
            conn.execute(
                """
                UPDATE sessions SET
                    message_count = message_count + 1,
                    last_activity_at = ?,
                    updated_at = ?
                WHERE id = ? AND owner_id = ?
                """,
                (message.created_at, message.created_at, message.session_id, message.owner_id),
            )
            return message
        finally:
            conn.close()

    def get_messages(
        self,
        session_id: str,
        owner_id: str,
        limit: int = 100,
        ascending: bool = True,
    ) -> List[SessionMessage]:
        """Fetch messages belonging to this session and owner."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            order = "ASC" if ascending else "DESC"
            query = f"""
                SELECT * FROM session_messages
                WHERE session_id = ? AND owner_id = ?
                ORDER BY created_at {order}
                LIMIT ?
            """
            cur = conn.execute(query, (session_id, owner_id, limit))
            return [self._row_to_message(r) for r in cur.fetchall()]
        finally:
            conn.close()

    # ── Helper Mappings ──────────────────────────────────────────────────────

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            owner_id=row["owner_id"],
            title=row["title"],
            status=row["status"],
            model=row["model"] or "claude-3-5-sonnet",
            provider_id=row["provider_id"] or "anthropic",
            workspace_id=row["workspace_id"] or "default_workspace",
            summary=row["summary"] or "",
            message_count=row["message_count"] or 0,
            token_usage=row["token_usage"] or 0,
            files_changed=row["files_changed"] or 0,
            metadata=json.loads(row["metadata_json"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_activity_at=row["last_activity_at"],
            archived_at=row["archived_at"],
        )

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> SessionMessage:
        return SessionMessage(
            id=row["id"],
            session_id=row["session_id"],
            owner_id=row["owner_id"],
            role=row["role"],
            content=row["content"] or "",
            tool_name=row["tool_name"],
            tool_call_id=row["tool_call_id"],
            tool_result=json.loads(row["tool_result_json"]) if row["tool_result_json"] else None,
            metadata=json.loads(row["metadata_json"] or "{}"),
            created_at=row["created_at"],
        )
