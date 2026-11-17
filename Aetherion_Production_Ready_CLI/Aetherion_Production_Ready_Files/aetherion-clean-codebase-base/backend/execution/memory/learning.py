"""
backend/execution/memory/learning.py — Agent Learning Engine for Phase 7.
Extracts validated solution patterns, user preferences, and system facts from agent executions.
Promotes validated signals into audited long-term agent memories.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from .cache import compute_content_hash
from .models import (
    LearningSignal,
    LearningSignalStatus,
    MemoryEntry,
    MemorySource,
    MemoryStatus,
    MemoryTrust,
    MemoryType,
)
from .store import MemoryStore

logger = logging.getLogger("aetherion.memory.learning")

PREFERENCE_PATTERNS = [
    re.compile(r"\b(?:always|prefer|never|don't|do not|please use|require)\s+([a-zA-Z0-9_\- ]{3,50})", re.IGNORECASE),
    re.compile(r"\b(?:use|target|configure)\s+([a-zA-Z0-9_\-]{2,20})\s+(?:for|as|instead of)\b", re.IGNORECASE),
]


class AgentLearningEngine:
    """
    Extracts, tracks, and validates learning signals from agent executions,
    promoting proven patterns into long-term memories.
    """

    def __init__(
        self,
        store: Optional[MemoryStore] = None,
        embedding_provider: Optional[Any] = None,
        cache: Optional[Any] = None,
    ):
        self.store = store
        self.embedding_provider = embedding_provider
        self.cache = cache

    def extract_learning_signals_from_run(
        self,
        owner_id: str,
        session_id: Optional[str],
        task_prompt: str,
        status: str,
        verification_status: str,
        created_or_edited_files: List[str],
        tool_action_count: int = 0,
    ) -> List[LearningSignal]:
        """
        Analyze run outcome and extract candidate learning signals.
        """
        signals: List[LearningSignal] = []
        now = time.time()

        # 1. Solution Pattern Signal (if task succeeded and verification passed)
        if status == "completed" and verification_status == "verified" and created_or_edited_files:
            file_list = ", ".join(created_or_edited_files[:5])
            title = f"Solution pattern for: {task_prompt[:60].strip()}..."
            desc = (
                f"Successfully completed task '{task_prompt[:120]}' with verified outcome. "
                f"Modified {len(created_or_edited_files)} files ({file_list}) over {tool_action_count} actions."
            )
            sig = LearningSignal(
                id=f"learn_{uuid.uuid4().hex[:12]}",
                owner_id=owner_id,
                session_id=session_id,
                signal_type="solution_pattern",
                title=title,
                description=desc,
                validation_condition="verified",
                confidence=0.85,
                status=LearningSignalStatus.VALIDATED.value,
                created_at=now,
            )
            signals.append(sig)

        # 2. User Preference Signal (if prompt contains explicit preference markers)
        for pattern in PREFERENCE_PATTERNS:
            match = pattern.search(task_prompt)
            if match:
                pref_text = match.group(0).strip()
                title = f"User preference directive: '{pref_text[:50]}'"
                desc = f"Observed preference pattern in user prompt: \"{pref_text}\""
                sig = LearningSignal(
                    id=f"learn_{uuid.uuid4().hex[:12]}",
                    owner_id=owner_id,
                    session_id=session_id,
                    signal_type="user_preference",
                    title=title,
                    description=desc,
                    validation_condition="user_instruction",
                    confidence=0.80,
                    status=LearningSignalStatus.VALIDATED.value,
                    created_at=now,
                )
                signals.append(sig)
                break  # Only extract one preference per prompt to prevent noise

        # Persist signals if store is available
        if self.store:
            for s in signals:
                try:
                    self.store.create_learning_signal(s)
                except Exception as ex:
                    # P3-A: escalate from debug-only — emit a warning so operations can detect
                    # systematic store failures via log monitoring and telemetry counters.
                    logger.warning(
                        "Failed to persist learning signal '%s' for owner '%s': %s",
                        s.id, s.owner_id, ex,
                    )

        return signals

    def promote_signal_to_memory(
        self,
        signal_id: str,
        owner_id: str,
        memory_type: str = MemoryType.AGENT_LEARNED.value,
        title: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Optional[MemoryEntry]:
        """
        Promote a validated learning signal into long-term persistent memory.
        Sets provenance and agent-generated trust level.
        """
        if not self.store:
            return None

        signals = self.store.list_learning_signals(owner_id=owner_id)
        sig = next((s for s in signals if s.id == signal_id), None)
        if not sig:
            return None

        now = time.time()
        final_title = title or sig.title
        content = sig.description
        c_hash = compute_content_hash(f"{final_title}\n{content}")

        mem_tags = tags or ["agent_learning", sig.signal_type]

        new_entry = MemoryEntry(
            id=f"mem_{uuid.uuid4().hex[:12]}",
            owner_id=owner_id,
            type=memory_type,
            title=final_title,
            content=content,
            source=MemorySource.AGENT_GENERATED.value,
            trust_level=MemoryTrust.AGENT_GENERATED.value,
            tags=mem_tags,
            status=MemoryStatus.ACTIVE.value,
            created_at=now,
            updated_at=now,
            confidence=sig.confidence,
            provenance=f"learning_signal:{sig.id}",
            created_by="agent_learning_engine",
            revision=1,
            content_hash=c_hash,
            embedding_version="v1",
            metadata={"source_session_id": sig.session_id, "signal_type": sig.signal_type},
        )

        persisted = self.store.create_memory(new_entry)

        # Generate and save vector embedding if provider available
        if self.embedding_provider:
            try:
                full_text = f"{persisted.title}\n{persisted.content}"
                vec = self.embedding_provider.embed_query(full_text)
                if self.cache:
                    self.cache.put(
                        owner_id=owner_id,
                        text=full_text,
                        model_name=self.embedding_provider.model_name,
                        dimensions=self.embedding_provider.dimension,
                        vector=vec,
                    )
                self.store.save_vector(
                    memory_id=persisted.id,
                    owner_id=owner_id,
                    vector=vec,
                    embedding_model=self.embedding_provider.model_name,
                    content_hash=c_hash,
                )
            except Exception as ex:
                logger.debug("Vector generation for promoted memory failed: %s", ex)

        # Update signal state to PROMOTED in DB
        conn = self.store.db.get_connection()
        try:
            conn.execute(
                "UPDATE agent_learning_signals SET status = ?, memory_id = ? WHERE id = ? AND owner_id = ?",
                (LearningSignalStatus.PROMOTED.value, persisted.id, sig.id, owner_id),
            )
        finally:
            conn.close()

        return persisted
