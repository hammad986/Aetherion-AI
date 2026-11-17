"""
backend/execution/memory/consolidation.py — Multi-Tier Memory Consolidation & Deduplication.
Groups exact duplicates, normalized duplicates, and semantic near-duplicates into auditable proposals.
Follows strict non-destructive intelligence principles: candidate memories are archived/staled, never deleted.
"""
from __future__ import annotations

import collections
import hashlib
import json
import logging
import math
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from .cache import compute_content_hash
from .models import (
    ConsolidationRecord,
    ConsolidationState,
    MemoryEntry,
    MemoryStatus,
    MemoryTrust,
)
from .store import MemoryStore

logger = logging.getLogger("aetherion.memory.consolidation")

# Trust level priority ordering: higher is more trusted
TRUST_PRIORITY = {
    MemoryTrust.USER_CONTROLLED.value: 3,
    MemoryTrust.IMPORTED.value: 2,
    MemoryTrust.AGENT_GENERATED.value: 1,
}


def _normalize_text(text: str) -> str:
    """Normalize text by lowercasing, removing punctuation, and collapsing whitespace."""
    cleaned = re.sub(r"[^\w\s]", "", text.lower())
    return " ".join(cleaned.split())


def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


class MemoryConsolidator:
    """
    Tiered deduplication and consolidation engine:
    Tier 1: Exact content hash matches
    Tier 2: Normalized string matches
    Tier 3: Semantic near-duplicates (cosine similarity >= threshold)
    """

    def __init__(
        self,
        store: Optional[MemoryStore] = None,
        semantic_threshold: float = 0.92,
    ):
        self.store = store
        self.semantic_threshold = max(0.5, min(1.0, semantic_threshold))

    def _select_primary(self, memories: List[MemoryEntry]) -> MemoryEntry:
        """
        Deterministically elect the primary memory in a cluster based on:
        1. Trust level (USER_CONTROLLED > IMPORTED > AGENT_GENERATED)
        2. Confidence score
        3. Revision count
        4. Most recent update time
        5. Stable ID tie-breaker
        """
        def rank_key(m: MemoryEntry) -> Tuple[int, float, int, float, str]:
            trust_val = TRUST_PRIORITY.get(m.trust_level, 0)
            return (trust_val, m.confidence, m.revision, m.updated_at, m.id)

        return max(memories, key=rank_key)

    def find_consolidation_candidates(
        self,
        memories: List[MemoryEntry],
        vectors: Optional[Dict[str, List[float]]] = None,
    ) -> List[ConsolidationRecord]:
        """
        Analyze a list of memories (all belonging to same owner) and detect consolidation clusters.
        Returns a list of proposed ConsolidationRecord objects.
        """
        if len(memories) < 2:
            return []

        owner_id = memories[0].owner_id
        proposals: List[ConsolidationRecord] = []
        assigned_ids: Set[str] = set()

        # ── Tier 1: Exact Content Hash Deduplication ────────────────────────
        hash_clusters: Dict[str, List[MemoryEntry]] = collections.defaultdict(list)
        for m in memories:
            ch = m.content_hash or compute_content_hash(m.content)
            hash_clusters[ch].append(m)

        for ch, cluster in hash_clusters.items():
            if len(cluster) > 1:
                primary = self._select_primary(cluster)
                candidates = [m.id for m in cluster if m.id != primary.id]
                rec = ConsolidationRecord(
                    id=f"consol_{uuid.uuid4().hex[:12]}",
                    owner_id=owner_id,
                    primary_memory_id=primary.id,
                    candidate_ids=candidates,
                    consolidation_type="exact_hash",
                    reason=f"Identical content hash '{ch[:12]}' across {len(cluster)} memories.",
                    state=ConsolidationState.PROPOSED.value,
                    confidence=1.0,
                    created_at=time.time(),
                    updated_at=time.time(),
                )
                proposals.append(rec)
                assigned_ids.update(m.id for m in cluster)

        # Remaining memories not yet assigned
        remaining = [m for m in memories if m.id not in assigned_ids]
        if len(remaining) < 2:
            return proposals

        # ── Tier 2: Normalized String Deduplication ─────────────────────────
        norm_clusters: Dict[str, List[MemoryEntry]] = collections.defaultdict(list)
        for m in remaining:
            norm = _normalize_text(m.content)
            if norm:
                norm_clusters[norm].append(m)

        for norm_text, cluster in norm_clusters.items():
            if len(cluster) > 1:
                primary = self._select_primary(cluster)
                candidates = [m.id for m in cluster if m.id != primary.id]
                rec = ConsolidationRecord(
                    id=f"consol_{uuid.uuid4().hex[:12]}",
                    owner_id=owner_id,
                    primary_memory_id=primary.id,
                    candidate_ids=candidates,
                    consolidation_type="normalized_string",
                    reason=f"Normalized content exact match across {len(cluster)} memories.",
                    state=ConsolidationState.PROPOSED.value,
                    confidence=0.98,
                    created_at=time.time(),
                    updated_at=time.time(),
                )
                proposals.append(rec)
                assigned_ids.update(m.id for m in cluster)

        # Remaining memories
        remaining = [m for m in memories if m.id not in assigned_ids]
        if len(remaining) < 2 or not vectors:
            return proposals

        # ── Tier 3: Semantic Near-Duplicates via Vector Cosine Similarity ────
        visited: Set[str] = set()
        for i in range(len(remaining)):
            mem_a = remaining[i]
            if mem_a.id in visited or mem_a.id not in vectors:
                continue

            cluster = [mem_a]
            vec_a = vectors[mem_a.id]

            for j in range(i + 1, len(remaining)):
                mem_b = remaining[j]
                if mem_b.id in visited or mem_b.id not in vectors:
                    continue

                sim = _cosine_similarity(vec_a, vectors[mem_b.id])
                if sim >= self.semantic_threshold:
                    cluster.append(mem_b)
                    visited.add(mem_b.id)

            if len(cluster) > 1:
                visited.add(mem_a.id)
                primary = self._select_primary(cluster)
                candidates = [m.id for m in cluster if m.id != primary.id]
                rec = ConsolidationRecord(
                    id=f"consol_{uuid.uuid4().hex[:12]}",
                    owner_id=owner_id,
                    primary_memory_id=primary.id,
                    candidate_ids=candidates,
                    consolidation_type="near_duplicate",
                    reason=(
                        f"Semantic near-duplicate cluster of {len(cluster)} memories "
                        f"with cosine similarity >= {self.semantic_threshold}."
                    ),
                    state=ConsolidationState.PROPOSED.value,
                    confidence=round(self.semantic_threshold, 2),
                    created_at=time.time(),
                    updated_at=time.time(),
                )
                proposals.append(rec)

        return proposals

    def scan_tenant_consolidations(
        self,
        owner_id: str,
        limit: int = 200,
        persist: bool = False,
    ) -> List[ConsolidationRecord]:
        """
        Fetch active memories and vectors for tenant, detect candidates, and optionally persist proposals.
        """
        if not self.store:
            return []

        memories = self.store.list_memories(
            owner_id=owner_id,
            status=MemoryStatus.ACTIVE.value,
            limit=limit,
        )
        if len(memories) < 2:
            return []

        raw_vectors = self.store.list_vectors(owner_id)
        vectors = {mid: vec for mid, vec in raw_vectors}

        proposals = self.find_consolidation_candidates(memories, vectors)

        if persist:
            for prop in proposals:
                try:
                    self.store.create_consolidation(prop)
                except Exception as ex:
                    logger.error("Failed persisting consolidation proposal %s: %s", prop.id, ex)

        return proposals

    def apply_consolidation(
        self,
        consolidation_id: str,
        owner_id: str,
        merge_tags: bool = True,
        merge_metadata: bool = True,
    ) -> bool:
        """
        Accept and apply a consolidation proposal non-destructively:
        1. Merges tags and metadata from candidate memories into the primary memory.
        2. Transitions candidate memories to ARCHIVED status with 'consolidated_into' pointer.
        3. Never deletes any memories.
        4. Transitions consolidation record to ACCEPTED.
        """
        if not self.store:
            return False

        records = self.store.list_consolidations(owner_id=owner_id)
        record = next((r for r in records if r.id == consolidation_id), None)
        if not record:
            return False

        primary = self.store.get_memory(record.primary_memory_id, owner_id)
        if not primary:
            return False

        merged_tags = set(primary.tags)
        merged_meta = dict(primary.metadata)

        for cid in record.candidate_ids:
            cand = self.store.get_memory(cid, owner_id)
            if not cand:
                continue

            if merge_tags:
                merged_tags.update(cand.tags)
            if merge_metadata:
                for k, v in cand.metadata.items():
                    if k not in merged_meta:
                        merged_meta[k] = v

            # Non-destructively archive the candidate memory
            cand_meta = dict(cand.metadata)
            cand_meta["consolidated_into"] = primary.id
            cand_meta["consolidated_at"] = time.time()
            self.store.update_memory(
                memory_id=cand.id,
                owner_id=owner_id,
                status=MemoryStatus.ARCHIVED.value,
                metadata=cand_meta,
            )

        # Update primary memory with consolidated attributes and revision bump
        self.store.update_memory(
            memory_id=primary.id,
            owner_id=owner_id,
            tags=sorted(list(merged_tags)),
            metadata=merged_meta,
        )

        return self.store.update_consolidation_state(
            consolidation_id=consolidation_id,
            owner_id=owner_id,
            new_state=ConsolidationState.ACCEPTED.value,
        )

    def reject_consolidation(
        self,
        consolidation_id: str,
        owner_id: str,
    ) -> bool:
        """Reject a consolidation proposal, keeping all memories unchanged."""
        if not self.store:
            return False

        return self.store.update_consolidation_state(
            consolidation_id=consolidation_id,
            owner_id=owner_id,
            new_state=ConsolidationState.REJECTED.value,
        )
