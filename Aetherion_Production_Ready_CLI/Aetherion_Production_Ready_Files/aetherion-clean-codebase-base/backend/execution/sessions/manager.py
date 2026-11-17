"""
backend/execution/sessions/manager.py — Core Lifecycle and Bounded Context Orchestrator for Sessions.
"""
from __future__ import annotations

import copy
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Session, SessionMessage, SessionStatus
from .store import SessionStore

logger = logging.getLogger("aetherion.sessions.manager")

_SECRET_PATTERNS = [
    # 1. Authorization Bearer header variants: Authorization: Bearer <token>, Bearer <token>
    (re.compile(r"((?:Authorization\s*:\s*)?Bearer\s+)([a-zA-Z0-9_\-\.]{8,})", re.IGNORECASE), r"\1***REDACTED***"),
    # 2. Anthropic API keys (sk-ant-api03-...)
    (re.compile(r"\bsk-ant-[a-zA-Z0-9_\-\.]{16,}\b", re.IGNORECASE), "***REDACTED***"),
    # 3. OpenAI and generic sk- keys
    (re.compile(r"\bsk-[a-zA-Z0-9_\-\.]{20,}\b", re.IGNORECASE), "***REDACTED***"),
    # 4. GitHub tokens (classic & extended, variable length & underscore-delimited)
    (re.compile(r"\bgh[pousr]_[a-zA-Z0-9_]{16,}\b", re.IGNORECASE), "***REDACTED***"),
    # 5. GitHub modern fine-grained personal access tokens
    (re.compile(r"\bgithub_pat_[a-zA-Z0-9_]{20,}\b", re.IGNORECASE), "***REDACTED***"),
    # 6. Slack tokens
    (re.compile(r"\bxox[baprs]-[a-zA-Z0-9-]{10,}\b", re.IGNORECASE), "***REDACTED***"),
    # 7. AWS Access Keys
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "***REDACTED***"),
    # 8. Key-value credentials: token: VALUE, token=VALUE, secret: VALUE, credential=VALUE, password: VALUE, password 'VALUE', etc.
    (
        re.compile(
            r"((?:api[-_]?key|secret|token|password|credential|auth(?:entication)?(?:[-_]?token)?|access[-_]?token)(?:\s*[:=]\s*['\"]?|\s+['\"]))([^\s'\";,]{6,})(['\"]?)",
            re.IGNORECASE,
        ),
        r"\1***REDACTED***\3",
    ),
    # 9. Natural phrases: "my password is ...", "token is ...", "key is ..."
    (
        re.compile(
            r"((?:(?:api[-_]?key|secret|token|password|credential|auth(?:entication)?(?:[-_]?token)?|access[-_]?token)|(?:my\s+(?:password|secret|token|key|credential)))\s+is\s+['\"]?)([^\s'\";,]{6,})(['\"]?)",
            re.IGNORECASE,
        ),
        r"\1***REDACTED***\3",
    ),
]


def redact_secrets(text: str) -> str:
    """Scrub raw API keys, tokens, and credentials from exported text."""
    if not isinstance(text, str):
        return text
    sanitized = text
    for pattern, replacement in _SECRET_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized



def _require_owner_id(owner_id: Any) -> str:
    """Validate that owner_id is strictly a non-empty string, preventing service-layer BOLA."""
    if not isinstance(owner_id, str):
        raise KeyError("A valid non-empty string owner_id is required.")
    normalized_owner_id = owner_id.strip()
    if not normalized_owner_id:
        raise KeyError("A valid non-empty string owner_id is required.")
    return normalized_owner_id

class SessionManager:
    """Manages creation, retrieval, bounding, archiving, duplication, and safe export of sessions."""

    def __init__(self, store: Optional[SessionStore] = None):
        self.store = store or SessionStore()

    def create_session(
        self,
        owner_id: str,
        title: Optional[str] = None,
        model: str = "claude-3-5-sonnet",
        provider_id: str = "anthropic",
        workspace_id: str = "default_workspace",
        metadata: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Session:
        """Create and persist a new session."""
        owner_id = _require_owner_id(owner_id)
        now = time.time()
        title_str = (title or "").strip() or f"Session {time.strftime('%Y-%m-%d %H:%M')}"
        session = Session(
            id=session_id or f"sess_{uuid.uuid4().hex[:12]}",
            owner_id=str(owner_id),
            title=title_str,
            status=SessionStatus.ACTIVE.value,
            model=model,
            provider_id=provider_id,
            workspace_id=workspace_id,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
            last_activity_at=now,
        )
        return self.store.create_session(session)

    def open_session(self, session_id: str, owner_id: str) -> Session:
        """Open an existing session. Rejects deleted sessions."""
        owner_id = _require_owner_id(owner_id)
        session = self.store.get_session(session_id, owner_id, include_deleted=True)
        if not session:
            raise KeyError(f"Session '{session_id}' not found.")
        if session.status == SessionStatus.DELETED.value:
            raise ValueError(f"Session '{session_id}' has been deleted and cannot be reopened.")

        # Update last activity
        session.last_activity_at = time.time()
        session.status = SessionStatus.ACTIVE.value
        return self.store.update_session(session)

    def rename_session(self, session_id: str, owner_id: str, new_title: str) -> Session:
        """Rename an existing session."""
        owner_id = _require_owner_id(owner_id)
        session = self.open_session(session_id, owner_id)
        if not new_title or not new_title.strip():
            raise ValueError("New title cannot be empty.")
        session.title = new_title.strip()
        return self.store.update_session(session)

    def archive_session(self, session_id: str, owner_id: str) -> Session:
        """Move a session to ARCHIVED state."""
        owner_id = _require_owner_id(owner_id)
        session = self.open_session(session_id, owner_id)
        session.status = SessionStatus.ARCHIVED.value
        session.archived_at = time.time()
        return self.store.update_session(session)

    def delete_session(self, session_id: str, owner_id: str) -> bool:
        """Soft delete a session."""
        owner_id = _require_owner_id(owner_id)
        return self.store.soft_delete_session(session_id, owner_id)

    def duplicate_session(self, session_id: str, owner_id: str, new_title: Optional[str] = None) -> Session:
        """Create a deep copy of an existing session and all its messages."""
        owner_id = _require_owner_id(owner_id)
        original = self.open_session(session_id, owner_id)
        now = time.time()
        title = new_title or f"Copy of {original.title}"

        new_sess = Session(
            id=f"sess_{uuid.uuid4().hex[:12]}",
            owner_id=str(owner_id),
            title=title,
            status=SessionStatus.ACTIVE.value,
            model=original.model,
            provider_id=original.provider_id,
            workspace_id=original.workspace_id,
            summary=original.summary,
            metadata=copy.deepcopy(original.metadata),
            created_at=now,
            updated_at=now,
            last_activity_at=now,
        )
        created = self.store.create_session(new_sess)

        # Clone messages
        messages = self.store.get_messages(session_id, owner_id, limit=500, ascending=True)
        for msg in messages:
            cloned_msg = SessionMessage(
                id=f"msg_{uuid.uuid4().hex[:12]}",
                session_id=created.id,
                owner_id=str(owner_id),
                role=msg.role,
                content=msg.content,
                tool_name=msg.tool_name,
                tool_call_id=msg.tool_call_id,
                tool_result=copy.deepcopy(msg.tool_result),
                metadata=copy.deepcopy(msg.metadata),
                created_at=msg.created_at,
            )
            self.store.add_message(cloned_msg)

        return self.store.get_session(created.id, owner_id)

    def search_sessions(
        self,
        owner_id: str,
        query: str = "",
        model: Optional[str] = None,
        provider_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Session]:
        """Search sessions by keyword and metadata."""
        owner_id = _require_owner_id(owner_id)
        return self.store.search_sessions(
            owner_id=owner_id,
            query=query,
            model=model,
            provider_id=provider_id,
            workspace_id=workspace_id,
            status=status,
            limit=limit,
        )

    def list_sessions(
        self,
        owner_id: str,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Session]:
        """List active sessions for an owner."""
        owner_id = _require_owner_id(owner_id)
        return self.store.list_sessions(owner_id=owner_id, status=status, limit=limit)

    def get_session(self, session_id: str, owner_id: str) -> Optional[Session]:
        """Retrieve single session if accessible."""
        owner_id = _require_owner_id(owner_id)
        return self.store.get_session(session_id, owner_id)

    # ── Context Management & Bounding ─────────────────────────────────────────

    def record_message(
        self,
        session_id: str,
        owner_id: str,
        role: str,
        content: str,
        tool_name: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        tool_result: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionMessage:
        """Append an interaction message to the session history."""
        owner_id = _require_owner_id(owner_id)
        # Ensure session exists and is active
        session = self.open_session(session_id, owner_id)
        msg = SessionMessage(
            session_id=session_id,
            owner_id=str(owner_id),
            role=role,
            content=content,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_result=tool_result,
            metadata=metadata or {},
            created_at=time.time(),
        )
        saved = self.store.add_message(msg)

        # Context compaction check: trigger rolling compaction when uncompacted messages accumulate
        all_count = session.message_count + 1
        last_compacted = (session.metadata or {}).get("last_compacted_count", 0)
        if all_count >= 15 and (all_count - last_compacted >= 10 or not session.summary):
            self._compact_session_history(session_id, owner_id)

        return saved

    def _compact_session_history(self, session_id: str, owner_id: str, retain_recent: int = 8) -> None:
        """Compact older messages into a structured summary to prevent infinite context bloat."""
        owner_id = _require_owner_id(owner_id)
        session = self.store.get_session(session_id, owner_id)
        if not session:
            return

        messages = self.store.get_messages(session_id, owner_id, limit=500, ascending=True)
        if len(messages) <= retain_recent:
            return

        older_messages = messages[:-retain_recent]
        summary_snippets = []
        for m in older_messages:
            snippet = f"[{m.role.upper()}]: {m.content[:150]}"
            if m.tool_name:
                snippet += f" (called {m.tool_name})"
            summary_snippets.append(snippet)

        compacted_summary = "Prior conversation summary:\n" + "\n".join(summary_snippets)
        MAX_SUMMARY_CHARS = 3000
        if len(compacted_summary) > MAX_SUMMARY_CHARS:
            trunc_prefix = "Prior conversation summary (earlier turns compacted):\n[... earlier turns truncated ...] \n"
            budget = MAX_SUMMARY_CHARS - len(trunc_prefix)
            kept_snippets = []
            cur_len = 0
            for snip in reversed(summary_snippets):
                if cur_len + len(snip) + 1 > budget:
                    break
                kept_snippets.append(snip)
                cur_len += len(snip) + 1
            kept_snippets.reverse()
            compacted_summary = trunc_prefix + "\n".join(kept_snippets)

        session.summary = compacted_summary
        if not session.metadata:
            session.metadata = {}
        session.metadata["last_compacted_count"] = len(messages)
        self.store.update_session(session)

    def build_bounded_context(
        self,
        session_id: str,
        owner_id: str,
        max_messages: int = 10,
        max_chars: int = 4000,
    ) -> Dict[str, Any]:
        """Build bounded conversational context for model input (recent messages chronologically + summary)."""
        owner_id = _require_owner_id(owner_id)
        session = self.open_session(session_id, owner_id)
        # Fetch the newest messages using descending order
        recent_candidates = self.store.get_messages(session_id, owner_id, limit=max_messages, ascending=False)

        bounded_messages: List[Dict[str, Any]] = []
        char_count = 0

        for m in recent_candidates:
            m_len = len(m.content)
            if char_count + m_len > max_chars and bounded_messages:
                break
            bounded_messages.append({
                "role": m.role,
                "content": m.content,
                "tool_name": m.tool_name,
            })
            char_count += m_len

        # Reverse so messages are in chronological order (oldest to newest among the recent set)
        bounded_messages.reverse()

        # Ensure summary returned does not exceed half of max_chars budget
        summary_bounded = session.summary or ""
        max_summary_len = max(500, max_chars // 2)
        if len(summary_bounded) > max_summary_len:
            summary_bounded = summary_bounded[-max_summary_len:]

        return {
            "session_id": session.id,
            "title": session.title,
            "summary": summary_bounded,
            "model": session.model,
            "provider_id": session.provider_id,
            "workspace_id": session.workspace_id,
            "recent_messages": bounded_messages,
            "total_messages": session.message_count,
        }

    # ── Export with Redaction ─────────────────────────────────────────────────

    def export_session(self, session_id: str, owner_id: str) -> Dict[str, Any]:
        """Export session data in clean JSON with all credentials scrubbed."""
        owner_id = _require_owner_id(owner_id)
        session = self.store.get_session(session_id, owner_id, include_deleted=True)
        if not session:
            raise KeyError(f"Session '{session_id}' not found.")

        messages = self.store.get_messages(session_id, owner_id, limit=1000, ascending=True)

        redacted_messages = []
        for m in messages:
            res_dict = None
            if m.tool_result:
                res_dict = json.loads(redact_secrets(json.dumps(m.tool_result)))
            redacted_messages.append({
                "id": m.id,
                "role": m.role,
                "content": redact_secrets(m.content),
                "tool_name": m.tool_name,
                "tool_call_id": m.tool_call_id,
                "tool_result": res_dict,
                "created_at": m.created_at,
            })

        return {
            "version": "1.0",
            "exported_at": time.time(),
            "session": {
                "id": session.id,
                "title": redact_secrets(session.title),
                "status": session.status,
                "model": session.model,
                "provider_id": session.provider_id,
                "workspace_id": session.workspace_id,
                "summary": redact_secrets(session.summary),
                "message_count": session.message_count,
                "token_usage": session.token_usage,
                "files_changed": session.files_changed,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            },
            "messages": redacted_messages,
        }
