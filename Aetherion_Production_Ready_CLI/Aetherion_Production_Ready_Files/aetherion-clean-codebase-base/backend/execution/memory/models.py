"""
backend/execution/memory/models.py — Persistent Memory Models, Types, Sources, and Trust Tiers.
"""
from __future__ import annotations

import dataclasses
import enum
import json
import math
import time
import uuid
from typing import Any, Dict, List, Optional


class MemoryType(str, enum.Enum):
    USER_PREFERENCE = "USER_PREFERENCE"
    PROJECT_FACT = "PROJECT_FACT"
    INSTRUCTION = "INSTRUCTION"
    KNOWLEDGE = "KNOWLEDGE"
    SESSION_SUMMARY = "SESSION_SUMMARY"
    IMPORTED_MEMORY = "IMPORTED_MEMORY"
    AGENT_LEARNED = "AGENT_LEARNED"


class MemorySource(str, enum.Enum):
    USER_CREATED = "USER_CREATED"
    SESSION = "SESSION"
    IMPORTED = "IMPORTED"
    IMPORTED_FILE = "IMPORTED_FILE"
    IMPORTED_TEXT = "IMPORTED_TEXT"
    IMPORTED_AI_EXPORT = "IMPORTED_AI_EXPORT"
    AGENT_GENERATED = "AGENT_GENERATED"


class MemoryTrust(str, enum.Enum):
    USER_CONTROLLED = "USER_CONTROLLED"
    IMPORTED = "IMPORTED"
    AGENT_GENERATED = "AGENT_GENERATED"


class MemoryStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"
    STALE = "STALE"
    EXPIRED = "EXPIRED"


class ContradictionState(str, enum.Enum):
    CONSISTENT = "consistent"
    CONFLICTING = "conflicting"
    SUPERSEDED = "superseded"
    UNCERTAIN = "uncertain"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ConsolidationState(str, enum.Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class LearningSignalStatus(str, enum.Enum):
    PENDING_VALIDATION = "pending_validation"
    VALIDATED = "validated"
    REJECTED = "rejected"
    PERSISTED = "persisted"
    PROMOTED = "promoted"


@dataclasses.dataclass
class MemoryEntry:
    id: str = dataclasses.field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:12]}")
    owner_id: str = ""
    type: str = MemoryType.KNOWLEDGE.value
    title: str = "Untitled Memory"
    content: str = ""
    source: str = MemorySource.USER_CREATED.value
    trust_level: str = MemoryTrust.USER_CONTROLLED.value
    importance: float = 0.7
    tags: List[str] = dataclasses.field(default_factory=list)
    status: str = MemoryStatus.ACTIVE.value
    created_at: float = dataclasses.field(default_factory=time.time)
    updated_at: float = dataclasses.field(default_factory=time.time)

    # Phase 7 Semantic & Intelligence Metadata
    confidence: float = 1.0
    provenance: str = ""
    created_by: str = ""
    last_validated_at: Optional[float] = None
    expires_at: Optional[float] = None
    revision: int = 1
    content_hash: str = ""
    embedding_version: str = "v1"
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def validate(self) -> None:
        if not self.id or not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Memory id cannot be empty.")
        if not isinstance(self.owner_id, str) or not self.owner_id.strip():
            raise ValueError("Memory owner_id cannot be empty.")
        if not self.title or not self.title.strip():
            raise ValueError("Memory title cannot be empty.")
        if not self.content or not self.content.strip():
            raise ValueError("Memory content cannot be empty.")
        if self.type not in {t.value for t in MemoryType}:
            raise ValueError(f"Invalid MemoryType: '{self.type}'.")
        if self.source not in {s.value for s in MemorySource}:
            raise ValueError(f"Invalid MemorySource: '{self.source}'.")
        if self.trust_level not in {t.value for t in MemoryTrust}:
            raise ValueError(f"Invalid MemoryTrust: '{self.trust_level}'.")
        if self.status not in {s.value for s in MemoryStatus}:
            raise ValueError(f"Invalid MemoryStatus: '{self.status}'.")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"Memory confidence must be in [0.0, 1.0], got {self.confidence}.")
        if self.revision < 1:
            raise ValueError(f"Memory revision must be >= 1, got {self.revision}.")

    def is_expired(self, current_time: Optional[float] = None) -> bool:
        now = current_time if current_time is not None else time.time()
        return self.expires_at is not None and now >= self.expires_at

    def compute_freshness(self, current_time: Optional[float] = None, half_life_days: float = 30.0) -> float:
        """Calculate temporal freshness score in [0.0, 1.0]."""
        now = current_time if current_time is not None else time.time()
        if self.is_expired(now):
            return 0.0
        ref_time = self.last_validated_at or self.updated_at
        age_seconds = max(0.0, now - ref_time)
        age_days = age_seconds / 86400.0
        decay = math.exp(-0.693147 * age_days / max(1.0, half_life_days))
        return max(0.0, min(1.0, decay))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "type": self.type,
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "trust_level": self.trust_level,
            "importance": self.importance,
            "tags": list(self.tags),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "confidence": self.confidence,
            "provenance": self.provenance,
            "created_by": self.created_by,
            "last_validated_at": self.last_validated_at,
            "expires_at": self.expires_at,
            "revision": self.revision,
            "content_hash": self.content_hash,
            "embedding_version": self.embedding_version,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MemoryEntry:
        tags_raw = data.get("tags")
        if isinstance(tags_raw, str):
            try:
                tags = json.loads(tags_raw)
            except Exception:
                tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        elif isinstance(tags_raw, list):
            tags = list(tags_raw)
        else:
            tags = []

        entry = cls(
            id=data.get("id") or f"mem_{uuid.uuid4().hex[:12]}",
            owner_id=data.get("owner_id", ""),
            type=data.get("type", MemoryType.KNOWLEDGE.value),
            title=data.get("title", "Untitled Memory"),
            content=data.get("content", ""),
            source=data.get("source", MemorySource.USER_CREATED.value),
            trust_level=data.get("trust_level", MemoryTrust.USER_CONTROLLED.value),
            importance=float(data.get("importance", 0.7)),
            tags=tags,
            status=data.get("status", MemoryStatus.ACTIVE.value),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            confidence=float(data.get("confidence", 1.0)),
            provenance=data.get("provenance", ""),
            created_by=data.get("created_by", ""),
            last_validated_at=data.get("last_validated_at"),
            expires_at=data.get("expires_at"),
            revision=int(data.get("revision", 1)),
            content_hash=data.get("content_hash", ""),
            embedding_version=data.get("embedding_version", "v1"),
            metadata=dict(data.get("metadata", {})),
        )
        return entry


@dataclasses.dataclass
class ContradictionRecord:
    id: str = dataclasses.field(default_factory=lambda: f"contra_{uuid.uuid4().hex[:12]}")
    owner_id: str = ""
    memory_id_a: str = ""
    memory_id_b: str = ""
    contradiction_type: str = "factual_mismatch"
    description: str = ""
    state: str = ContradictionState.CONFLICTING.value
    confidence: float = 0.8
    created_at: float = dataclasses.field(default_factory=time.time)
    updated_at: float = dataclasses.field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class ConsolidationRecord:
    id: str = dataclasses.field(default_factory=lambda: f"consol_{uuid.uuid4().hex[:12]}")
    owner_id: str = ""
    primary_memory_id: str = ""
    candidate_ids: List[str] = dataclasses.field(default_factory=list)
    consolidation_type: str = "near_duplicate"
    reason: str = ""
    state: str = ConsolidationState.PROPOSED.value
    confidence: float = 0.8
    created_at: float = dataclasses.field(default_factory=time.time)
    updated_at: float = dataclasses.field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class LearningSignal:
    id: str = dataclasses.field(default_factory=lambda: f"learn_{uuid.uuid4().hex[:12]}")
    owner_id: str = ""
    session_id: Optional[str] = None
    signal_type: str = "solution_pattern"
    title: str = ""
    description: str = ""
    validation_condition: str = "tests_passed"
    confidence: float = 0.7
    status: str = LearningSignalStatus.PENDING_VALIDATION.value
    memory_id: Optional[str] = None
    created_at: float = dataclasses.field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class MemoryFeedbackRecord:
    id: str = dataclasses.field(default_factory=lambda: f"fb_{uuid.uuid4().hex[:12]}")
    owner_id: str = ""
    memory_id: str = ""
    session_id: Optional[str] = None
    query: str = ""
    outcome: str = "success"
    relevance_score: float = 1.0
    created_at: float = dataclasses.field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)
