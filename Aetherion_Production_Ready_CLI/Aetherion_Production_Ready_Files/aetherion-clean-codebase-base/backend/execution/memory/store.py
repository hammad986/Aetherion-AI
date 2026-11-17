"""
backend/execution/memory/store.py — Persistent Multi-Tenant Store for Memories.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..persistence.db import StateDatabase
from .models import (
    ConsolidationRecord,
    ContradictionRecord,
    LearningSignal,
    MemoryEntry,
    MemoryFeedbackRecord,
    MemoryStatus,
)

logger = logging.getLogger("aetherion.memory.store")


def _require_valid_owner_id(owner_id: Any) -> str:
    """Validate that owner_id is strictly a non-empty string, preventing service-layer BOLA."""
    if not isinstance(owner_id, str):
        raise KeyError("A valid non-empty string owner_id is required.")
    normalized_owner_id = owner_id.strip()
    if not normalized_owner_id:
        raise KeyError("A valid non-empty string owner_id is required.")
    return normalized_owner_id


class MemoryStore:
    """Multi-tenant SQLite store for persistent user memory entries."""

    def __init__(
        self,
        db_path: Optional[Path | str] = None,
        db: Optional[StateDatabase] = None,
    ):
        self.db = db or StateDatabase(db_path)

    def create_memory(self, memory: MemoryEntry) -> MemoryEntry:
        """Insert a newly created memory entry."""
        memory.validate()
        _require_valid_owner_id(memory.owner_id)
        conn = self.db.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO memories (
                    id, owner_id, type, title, content, source, trust_level,
                    importance, tags_json, metadata_json, status, created_at, updated_at,
                    confidence, provenance, created_by, last_validated_at,
                    expires_at, revision, content_hash, embedding_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.id,
                    memory.owner_id,
                    memory.type,
                    memory.title,
                    memory.content,
                    memory.source,
                    memory.trust_level,
                    memory.importance,
                    json.dumps(memory.tags),
                    json.dumps(memory.metadata),
                    memory.status,
                    memory.created_at,
                    memory.updated_at,
                    memory.confidence,
                    memory.provenance,
                    memory.created_by,
                    memory.last_validated_at,
                    memory.expires_at,
                    memory.revision,
                    memory.content_hash,
                    memory.embedding_version,
                ),
            )
            return memory
        finally:
            conn.close()

    def get_memory(self, memory_id: str, owner_id: str, include_deleted: bool = False) -> Optional[MemoryEntry]:
        """Fetch memory entry by ID with tenant scoping."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            query = "SELECT * FROM memories WHERE id = ? AND owner_id = ?"
            params: list[Any] = [memory_id, owner_id]
            if not include_deleted:
                query += f" AND status != '{MemoryStatus.DELETED.value}'"

            cur = conn.execute(query, params)
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_memory(row)
        finally:
            conn.close()

    def update_memory(
        self,
        memory: Optional[MemoryEntry] = None,
        memory_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        title: Optional[str] = None,
        content: Optional[str] = None,
        type: Optional[str] = None,
        importance: Optional[float] = None,
        tags: Optional[List[str]] = None,
        status: Optional[str] = None,
        confidence: Optional[float] = None,
        provenance: Optional[str] = None,
        last_validated_at: Optional[float] = None,
        expires_at: Optional[float] = None,
        revision: Optional[int] = None,
        content_hash: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[MemoryEntry]:
        """Update existing memory entry supporting both MemoryEntry object and individual attributes."""
        if memory is not None:
            entry = memory
        else:
            if not memory_id or not owner_id:
                raise ValueError("Either memory object or both memory_id and owner_id are required.")
            existing = self.get_memory(memory_id, owner_id)
            if not existing:
                return None
            entry = existing
            if title is not None:
                entry.title = title
            if content is not None:
                entry.content = content
            if type is not None:
                entry.type = type
            if importance is not None:
                entry.importance = importance
            if tags is not None:
                entry.tags = list(tags)
            if status is not None:
                entry.status = status
            if confidence is not None:
                entry.confidence = confidence
            if provenance is not None:
                entry.provenance = provenance
            if last_validated_at is not None:
                entry.last_validated_at = last_validated_at
            if expires_at is not None:
                entry.expires_at = expires_at
            if revision is not None:
                entry.revision = revision
            if content_hash is not None:
                entry.content_hash = content_hash
            if metadata is not None:
                entry.metadata.update(metadata)

        entry.validate()
        entry.updated_at = time.time()
        conn = self.db.get_connection()
        try:
            conn.execute(
                """
                UPDATE memories SET
                    type = ?,
                    title = ?,
                    content = ?,
                    source = ?,
                    trust_level = ?,
                    importance = ?,
                    tags_json = ?,
                    status = ?,
                    updated_at = ?,
                    confidence = ?,
                    provenance = ?,
                    created_by = ?,
                    last_validated_at = ?,
                    expires_at = ?,
                    revision = ?,
                    content_hash = ?,
                    embedding_version = ?,
                    metadata_json = ?
                WHERE id = ? AND owner_id = ?
                """,
                (
                    entry.type,
                    entry.title,
                    entry.content,
                    entry.source,
                    entry.trust_level,
                    entry.importance,
                    json.dumps(entry.tags),
                    entry.status,
                    entry.updated_at,
                    entry.confidence,
                    entry.provenance,
                    entry.created_by,
                    entry.last_validated_at,
                    entry.expires_at,
                    entry.revision,
                    entry.content_hash,
                    entry.embedding_version,
                    json.dumps(entry.metadata),
                    entry.id,
                    entry.owner_id,
                ),
            )
            return entry
        finally:
            conn.close()

    def soft_delete_memory(self, memory_id: str, owner_id: str) -> bool:
        """Soft delete a memory entry."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            now = time.time()
            cur = conn.execute(
                f"""
                UPDATE memories
                SET status = '{MemoryStatus.DELETED.value}', updated_at = ?
                WHERE id = ? AND owner_id = ? AND status != '{MemoryStatus.DELETED.value}'
                """,
                (now, memory_id, owner_id),
            )
            return cur.rowcount > 0
        finally:
            conn.close()

    def archive_memory(self, memory_id: str, owner_id: str) -> Optional[MemoryEntry]:
        """Move memory entry to ARCHIVED state."""
        mem = self.get_memory(memory_id, owner_id)
        if not mem:
            return None
        mem.status = MemoryStatus.ARCHIVED.value
        return self.update_memory(mem)

    def restore_memory(self, memory_id: str, owner_id: str) -> Optional[MemoryEntry]:
        """Restore an archived or deleted memory entry to ACTIVE state."""
        mem = self.get_memory(memory_id, owner_id, include_deleted=True)
        if not mem:
            return None
        mem.status = MemoryStatus.ACTIVE.value
        return self.update_memory(mem)

    def list_memories(
        self,
        owner_id: str,
        type: Optional[str] = None,
        source: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[MemoryEntry]:
        """List memories owned by a tenant, default ACTIVE only."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            query = "SELECT * FROM memories WHERE owner_id = ?"
            params: list[Any] = [owner_id]

            if status:
                query += " AND status = ?"
                params.append(status)
            else:
                query += f" AND status NOT IN ('{MemoryStatus.DELETED.value}', '{MemoryStatus.ARCHIVED.value}', '{MemoryStatus.EXPIRED.value}', '{MemoryStatus.STALE.value}')"

            if type:
                query += " AND type = ?"
                params.append(type)
            if source:
                query += " AND source = ?"
                params.append(source)

            query += " ORDER BY importance DESC, updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cur = conn.execute(query, params)
            return [self._row_to_memory(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def search_memories(
        self,
        owner_id: str,
        query: str = "",
        tags: Optional[List[str]] = None,
        type: Optional[str] = None,
        source: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[MemoryEntry]:
        """Multi-field keyword search across title, content, and tags."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            sql = "SELECT * FROM memories WHERE owner_id = ?"
            params: list[Any] = [owner_id]

            if status:
                sql += " AND status = ?"
                params.append(status)
            else:
                sql += f" AND status NOT IN ('{MemoryStatus.DELETED.value}', '{MemoryStatus.ARCHIVED.value}', '{MemoryStatus.EXPIRED.value}', '{MemoryStatus.STALE.value}')"

            if type:
                sql += " AND type = ?"
                params.append(type)
            if source:
                sql += " AND source = ?"
                params.append(source)

            if query and query.strip():
                sql += " AND (title LIKE ? OR content LIKE ? OR tags_json LIKE ?)"
                pattern = f"%{query.strip()}%"
                params.extend([pattern, pattern, pattern])

            if tags:
                for t in tags:
                    sql += " AND tags_json LIKE ?"
                    params.append(f"%{t.strip()}%")

            sql += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
            params.append(limit)

            cur = conn.execute(sql, params)
            return [self._row_to_memory(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def find_duplicate(self, owner_id: str, content: str, title: str) -> Optional[MemoryEntry]:
        """Find an existing memory with identical content or title for deduplication."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            cur = conn.execute(
                f"""
                SELECT * FROM memories
                WHERE owner_id = ?
                  AND status != '{MemoryStatus.DELETED.value}'
                  AND (LOWER(TRIM(content)) = LOWER(TRIM(?)) OR LOWER(TRIM(title)) = LOWER(TRIM(?)))
                LIMIT 1
                """,
                (owner_id, content, title),
            )
            row = cur.fetchone()
            return self._row_to_memory(row) if row else None
        finally:
            conn.close()

    # ── Helper Mappings ──────────────────────────────────────────────────────

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> MemoryEntry:
        keys = row.keys() if hasattr(row, "keys") else []
        return MemoryEntry(
            id=row["id"],
            owner_id=row["owner_id"],
            type=row["type"],
            title=row["title"],
            content=row["content"] or "",
            source=row["source"],
            trust_level=row["trust_level"],
            importance=row["importance"],
            tags=json.loads(row["tags_json"] or "[]"),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            confidence=float(row["confidence"]) if "confidence" in keys and row["confidence"] is not None else 1.0,
            provenance=str(row["provenance"]) if "provenance" in keys and row["provenance"] is not None else "",
            created_by=str(row["created_by"]) if "created_by" in keys and row["created_by"] is not None else "",
            last_validated_at=float(row["last_validated_at"]) if "last_validated_at" in keys and row["last_validated_at"] is not None else None,
            expires_at=float(row["expires_at"]) if "expires_at" in keys and row["expires_at"] is not None else None,
            revision=int(row["revision"]) if "revision" in keys and row["revision"] is not None else 1,
            content_hash=str(row["content_hash"]) if "content_hash" in keys and row["content_hash"] is not None else "",
            embedding_version=str(row["embedding_version"]) if "embedding_version" in keys and row["embedding_version"] is not None else "v1",
            metadata=json.loads(row["metadata_json"]) if "metadata_json" in keys and row["metadata_json"] else {},
        )

    # ── Vector Persistence (Phase 5 & 6 Semantic Embeddings) ─────────────────

    def save_vector(
        self,
        memory_id: str,
        owner_id: str,
        vector: List[float],
        embedding_model: str = "deterministic-local-v1",
        content_hash: str = "",
    ) -> None:
        """Persist or update vector embedding as a compact binary IEEE 754 float32 BLOB with content_hash."""
        owner_id = _require_valid_owner_id(owner_id)
        if not memory_id or not isinstance(memory_id, str) or not memory_id.strip():
            raise KeyError("A valid non-empty string memory_id is required.")
        if not vector or not isinstance(vector, list):
            raise ValueError("Vector must be a non-empty list of float values.")

        dim = len(vector)
        # Pack floats into compact binary BLOB
        vector_blob = struct.pack(f"{dim}f", *[float(x) for x in vector])
        now = time.time()

        conn = self.db.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO memory_vectors (
                    memory_id, owner_id, embedding_model, dimensions, vector_blob, content_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    embedding_model = excluded.embedding_model,
                    dimensions = excluded.dimensions,
                    vector_blob = excluded.vector_blob,
                    content_hash = excluded.content_hash,
                    updated_at = excluded.updated_at
                WHERE memory_vectors.owner_id = excluded.owner_id
                """,
                (memory_id.strip(), owner_id, embedding_model, dim, vector_blob, content_hash, now, now),
            )
        finally:
            conn.close()

    def get_vector(self, memory_id: str, owner_id: str) -> Optional[List[float]]:
        """Retrieve and unpack vector embedding for a specific memory under owner scoping."""
        owner_id = _require_valid_owner_id(owner_id)
        if not memory_id or not isinstance(memory_id, str):
            return None

        conn = self.db.get_connection()
        try:
            cur = conn.execute(
                "SELECT dimensions, vector_blob FROM memory_vectors WHERE memory_id = ? AND owner_id = ?",
                (memory_id.strip(), owner_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            dim = row["dimensions"]
            blob = row["vector_blob"]
            return list(struct.unpack(f"{dim}f", blob))
        finally:
            conn.close()

    def get_vector_record(self, memory_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve complete vector record including metadata and content_hash."""
        owner_id = _require_valid_owner_id(owner_id)
        if not memory_id or not isinstance(memory_id, str):
            return None

        conn = self.db.get_connection()
        try:
            cur = conn.execute(
                """
                SELECT memory_id, owner_id, embedding_model, dimensions, vector_blob, content_hash, created_at, updated_at
                FROM memory_vectors
                WHERE memory_id = ? AND owner_id = ?
                """,
                (memory_id.strip(), owner_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            dim = row["dimensions"]
            blob = row["vector_blob"]
            vec = list(struct.unpack(f"{dim}f", blob))
            return {
                "memory_id": row["memory_id"],
                "owner_id": row["owner_id"],
                "embedding_model": row["embedding_model"],
                "dimensions": dim,
                "vector": vec,
                "content_hash": row["content_hash"] or "",
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        finally:
            conn.close()

    def list_vectors(self, owner_id: str) -> List[Tuple[str, List[float]]]:
        """Fetch all unpacked vectors belonging strictly to owner_id."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            cur = conn.execute(
                """
                SELECT mv.memory_id, mv.dimensions, mv.vector_blob
                FROM memory_vectors mv
                JOIN memories m ON mv.memory_id = m.id
                WHERE mv.owner_id = ? AND m.status != ?
                """,
                (owner_id, MemoryStatus.DELETED.value),
            )
            results: List[Tuple[str, List[float]]] = []
            for row in cur.fetchall():
                dim = row["dimensions"]
                blob = row["vector_blob"]
                vec = list(struct.unpack(f"{dim}f", blob))
                results.append((row["memory_id"], vec))
            return results
        finally:
            conn.close()

    def delete_vector(self, memory_id: str, owner_id: str) -> bool:
        """Delete vector for a memory under owner scoping."""
        owner_id = _require_valid_owner_id(owner_id)
        if not memory_id or not isinstance(memory_id, str):
            return False

        conn = self.db.get_connection()
        try:
            cur = conn.execute(
                "DELETE FROM memory_vectors WHERE memory_id = ? AND owner_id = ?",
                (memory_id.strip(), owner_id),
            )
            return cur.rowcount > 0
        finally:
            conn.close()

    def list_unindexed_memories(self, owner_id: str, limit: int = 500) -> List[MemoryEntry]:
        """Find active memories that do not yet have a vector embedding."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            cur = conn.execute(
                """
                SELECT m.*
                FROM memories m
                LEFT JOIN memory_vectors mv ON m.id = mv.memory_id AND mv.owner_id = m.owner_id
                WHERE m.owner_id = ?
                  AND m.status = ?
                  AND mv.memory_id IS NULL
                ORDER BY m.updated_at DESC
                LIMIT ?
                """,
                (owner_id, MemoryStatus.ACTIVE.value, limit),
            )
            return [self._row_to_memory(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def list_stale_memories(
        self,
        owner_id: str,
        compute_hash_fn: Any,
        limit: int = 500,
    ) -> List[Tuple[MemoryEntry, str]]:
        """
        Find active memories whose current content hash does not match their stored vector content_hash.
        Returns list of (MemoryEntry, expected_content_hash).
        """
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            cur = conn.execute(
                """
                SELECT m.*, mv.content_hash as stored_vector_hash
                FROM memories m
                LEFT JOIN memory_vectors mv ON m.id = mv.memory_id AND mv.owner_id = m.owner_id
                WHERE m.owner_id = ?
                  AND m.status = ?
                ORDER BY m.updated_at DESC
                LIMIT ?
                """,
                (owner_id, MemoryStatus.ACTIVE.value, limit * 2),
            )
            stale: List[Tuple[MemoryEntry, str]] = []
            for r in cur.fetchall():
                entry = self._row_to_memory(r)
                expected_hash = compute_hash_fn(f"{entry.title}\n{entry.content}")
                stored_hash = r["stored_vector_hash"]
                if not stored_hash or stored_hash != expected_hash:
                    stale.append((entry, expected_hash))
                    if len(stale) >= limit:
                        break
            return stale
        finally:
            conn.close()

    # ── Phase 7 Intelligence Persistence ─────────────────────────────────────

    def create_contradiction(self, record: ContradictionRecord) -> ContradictionRecord:
        """Persist a detected contradiction record."""
        _require_valid_owner_id(record.owner_id)
        conn = self.db.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO memory_contradictions (
                    id, owner_id, memory_id_a, memory_id_b, contradiction_type,
                    description, state, confidence, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    description = excluded.description,
                    state = excluded.state,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                (
                    record.id,
                    record.owner_id,
                    record.memory_id_a,
                    record.memory_id_b,
                    record.contradiction_type,
                    record.description,
                    record.state,
                    record.confidence,
                    record.created_at,
                    record.updated_at,
                ),
            )
            return record
        finally:
            conn.close()

    def list_contradictions(self, owner_id: str, state: Optional[str] = None) -> List[ContradictionRecord]:
        """Fetch contradiction records for a tenant."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            sql = "SELECT * FROM memory_contradictions WHERE owner_id = ?"
            params: list[Any] = [owner_id]
            if state:
                sql += " AND state = ?"
                params.append(state)
            sql += " ORDER BY updated_at DESC"
            cur = conn.execute(sql, params)
            return [
                ContradictionRecord(
                    id=row["id"],
                    owner_id=row["owner_id"],
                    memory_id_a=row["memory_id_a"],
                    memory_id_b=row["memory_id_b"],
                    contradiction_type=row["contradiction_type"],
                    description=row["description"],
                    state=row["state"],
                    confidence=float(row["confidence"]),
                    created_at=float(row["created_at"]),
                    updated_at=float(row["updated_at"]),
                )
                for row in cur.fetchall()
            ]
        finally:
            conn.close()

    def update_contradiction_state(self, contradiction_id: str, owner_id: str, new_state: str) -> bool:
        """Update the state of a contradiction record."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            now = time.time()
            cur = conn.execute(
                "UPDATE memory_contradictions SET state = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                (new_state, now, contradiction_id, owner_id),
            )
            return cur.rowcount > 0
        finally:
            conn.close()

    def create_consolidation(self, record: ConsolidationRecord) -> ConsolidationRecord:
        """Persist a memory consolidation proposal."""
        _require_valid_owner_id(record.owner_id)
        conn = self.db.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO memory_consolidations (
                    id, owner_id, primary_memory_id, candidate_ids_json, consolidation_type,
                    reason, state, confidence, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    reason = excluded.reason,
                    state = excluded.state,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                (
                    record.id,
                    record.owner_id,
                    record.primary_memory_id,
                    json.dumps(record.candidate_ids),
                    record.consolidation_type,
                    record.reason,
                    record.state,
                    record.confidence,
                    record.created_at,
                    record.updated_at,
                ),
            )
            return record
        finally:
            conn.close()

    def list_consolidations(self, owner_id: str, state: Optional[str] = None) -> List[ConsolidationRecord]:
        """List consolidation records for an owner."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            sql = "SELECT * FROM memory_consolidations WHERE owner_id = ?"
            params: list[Any] = [owner_id]
            if state:
                sql += " AND state = ?"
                params.append(state)
            sql += " ORDER BY updated_at DESC"
            cur = conn.execute(sql, params)
            return [
                ConsolidationRecord(
                    id=row["id"],
                    owner_id=row["owner_id"],
                    primary_memory_id=row["primary_memory_id"],
                    candidate_ids=json.loads(row["candidate_ids_json"] or "[]"),
                    consolidation_type=row["consolidation_type"],
                    reason=row["reason"],
                    state=row["state"],
                    confidence=float(row["confidence"]),
                    created_at=float(row["created_at"]),
                    updated_at=float(row["updated_at"]),
                )
                for row in cur.fetchall()
            ]
        finally:
            conn.close()

    def update_consolidation_state(self, consolidation_id: str, owner_id: str, new_state: str) -> bool:
        """Update the state of a consolidation record."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            now = time.time()
            cur = conn.execute(
                "UPDATE memory_consolidations SET state = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                (new_state, now, consolidation_id, owner_id),
            )
            return cur.rowcount > 0
        finally:
            conn.close()

    def create_learning_signal(self, signal: LearningSignal) -> LearningSignal:
        """Persist an agent learning signal."""
        _require_valid_owner_id(signal.owner_id)
        conn = self.db.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO agent_learning_signals (
                    id, owner_id, session_id, signal_type, title, description,
                    validation_condition, confidence, status, memory_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.id,
                    signal.owner_id,
                    signal.session_id,
                    signal.signal_type,
                    signal.title,
                    signal.description,
                    signal.validation_condition,
                    signal.confidence,
                    signal.status,
                    signal.memory_id,
                    signal.created_at,
                ),
            )
            return signal
        finally:
            conn.close()

    def list_learning_signals(self, owner_id: str, status: Optional[str] = None) -> List[LearningSignal]:
        """Fetch learning signals for a tenant."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            sql = "SELECT * FROM agent_learning_signals WHERE owner_id = ?"
            params: list[Any] = [owner_id]
            if status:
                sql += " AND status = ?"
                params.append(status)
            sql += " ORDER BY created_at DESC"
            cur = conn.execute(sql, params)
            return [
                LearningSignal(
                    id=row["id"],
                    owner_id=row["owner_id"],
                    session_id=row["session_id"],
                    signal_type=row["signal_type"],
                    title=row["title"],
                    description=row["description"],
                    validation_condition=row["validation_condition"],
                    confidence=float(row["confidence"]),
                    status=row["status"],
                    memory_id=row["memory_id"],
                    created_at=float(row["created_at"]),
                )
                for row in cur.fetchall()
            ]
        finally:
            conn.close()

    def update_learning_signal_status(
        self, signal_id: str, owner_id: str, new_status: str, memory_id: Optional[str] = None
    ) -> bool:
        """Update status and optional linked memory_id for a learning signal."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            if memory_id:
                cur = conn.execute(
                    "UPDATE agent_learning_signals SET status = ?, memory_id = ? WHERE id = ? AND owner_id = ?",
                    (new_status, memory_id, signal_id, owner_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE agent_learning_signals SET status = ? WHERE id = ? AND owner_id = ?",
                    (new_status, signal_id, owner_id),
                )
            return cur.rowcount > 0
        finally:
            conn.close()

    def record_feedback(self, record: MemoryFeedbackRecord) -> MemoryFeedbackRecord:
        """Record execution outcome feedback for a memory."""
        _require_valid_owner_id(record.owner_id)
        conn = self.db.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO memory_feedback (
                    id, owner_id, memory_id, session_id, query, outcome, relevance_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.owner_id,
                    record.memory_id,
                    record.session_id,
                    record.query,
                    record.outcome,
                    record.relevance_score,
                    record.created_at,
                ),
            )
            return record
        finally:
            conn.close()

    create_feedback = record_feedback

    def list_feedback(self, owner_id: str, memory_id: Optional[str] = None) -> List[MemoryFeedbackRecord]:
        """Fetch feedback records for tenant's memories."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            sql = "SELECT * FROM memory_feedback WHERE owner_id = ?"
            params: list[Any] = [owner_id]
            if memory_id:
                sql += " AND memory_id = ?"
                params.append(memory_id)
            sql += " ORDER BY created_at DESC"
            cur = conn.execute(sql, params)
            return [
                MemoryFeedbackRecord(
                    id=row["id"],
                    owner_id=row["owner_id"],
                    memory_id=row["memory_id"],
                    session_id=row["session_id"],
                    query=row["query"],
                    outcome=row["outcome"],
                    relevance_score=float(row["relevance_score"]),
                    created_at=float(row["created_at"]),
                )
                for row in cur.fetchall()
            ]
        finally:
            conn.close()



