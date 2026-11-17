"""
backend/execution/memory/maintenance.py — Background Memory Maintenance Routines for Phase 7.
Provides durable tasks for embedding backfill, stale embedding repair,
duplicate detection, vector index reconstruction, contradiction scanning,
consolidation scanning, cache TTL pruning, and memory lifecycle/staleness cleanup.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from .cache import EmbeddingCache, compute_content_hash
from .consolidation import MemoryConsolidator
from .contradictions import ContradictionDetector
from .embeddings import EmbeddingProvider, cosine_similarity, get_default_embedding_provider
from .index import AutoIndexConfig, VectorIndex, get_vector_index
from .models import MemoryEntry, MemoryStatus
from .store import MemoryStore

logger = logging.getLogger("aetherion.memory.maintenance")


def _require_valid_owner_id(owner_id: Any) -> str:
    """Validate that owner_id is strictly a non-empty string."""
    if not isinstance(owner_id, str):
        raise KeyError("A valid non-empty string owner_id is required.")
    normalized = owner_id.strip()
    if not normalized:
        raise KeyError("A valid non-empty string owner_id is required.")
    return normalized


# ── Job 1: Embedding Backfill ──────────────────────────────────────────────────

def run_embedding_backfill(
    owner_id: str,
    store: Optional[MemoryStore] = None,
    provider: Optional[EmbeddingProvider] = None,
    cache: Optional[EmbeddingCache] = None,
    batch_size: int = 50,
) -> Dict[str, Any]:
    """
    Find active memories lacking vector embeddings and backfill them.
    Leverages embedding cache to avoid redundant provider invocations.
    """
    owner_id = _require_valid_owner_id(owner_id)
    store = store or MemoryStore()
    provider = provider or get_default_embedding_provider()
    cache = cache or EmbeddingCache(db=store.db)

    unindexed = store.list_unindexed_memories(owner_id, limit=batch_size)
    processed = 0
    succeeded = 0
    failed = 0

    for mem in unindexed:
        processed += 1
        text = f"{mem.title}\n{mem.content}"
        c_hash = compute_content_hash(text)

        try:
            vec = cache.get(owner_id, text, provider.model_name, provider.dimension)
            if vec is None:
                vec = provider.embed_query(text)
                cache.put(owner_id, text, provider.model_name, provider.dimension, vec)

            store.save_vector(
                memory_id=mem.id,
                owner_id=owner_id,
                vector=vec,
                embedding_model=provider.model_name,
                content_hash=c_hash,
            )
            succeeded += 1
        except Exception as exc:
            logger.warning("Embedding backfill error for memory '%s': %s", mem.id, exc)
            failed += 1

    remaining = len(store.list_unindexed_memories(owner_id, limit=1))
    return {
        "job": "embedding_backfill",
        "owner_id": owner_id,
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "has_more": remaining > 0,
    }


# ── Job 2: Stale Embedding Repair ──────────────────────────────────────────────

def run_stale_embedding_repair(
    owner_id: str,
    store: Optional[MemoryStore] = None,
    provider: Optional[EmbeddingProvider] = None,
    cache: Optional[EmbeddingCache] = None,
    batch_size: int = 50,
) -> Dict[str, Any]:
    """
    Identify memories whose content hash differs from stored vector content_hash and refresh them.
    """
    owner_id = _require_valid_owner_id(owner_id)
    store = store or MemoryStore()
    provider = provider or get_default_embedding_provider()
    cache = cache or EmbeddingCache(db=store.db)

    stale_items = store.list_stale_memories(
        owner_id=owner_id,
        compute_hash_fn=compute_content_hash,
        limit=batch_size,
    )

    repaired = 0
    failed = 0

    for mem, expected_hash in stale_items:
        text = f"{mem.title}\n{mem.content}"
        try:
            vec = cache.get(owner_id, text, provider.model_name, provider.dimension)
            if vec is None:
                vec = provider.embed_query(text)
                cache.put(owner_id, text, provider.model_name, provider.dimension, vec)

            store.save_vector(
                memory_id=mem.id,
                owner_id=owner_id,
                vector=vec,
                embedding_model=provider.model_name,
                content_hash=expected_hash,
            )
            repaired += 1
        except Exception as exc:
            logger.warning("Stale repair error for memory '%s': %s", mem.id, exc)
            failed += 1

    return {
        "job": "stale_embedding_repair",
        "owner_id": owner_id,
        "detected_stale": len(stale_items),
        "repaired": repaired,
        "failed": failed,
    }


# ── Job 3: Duplicate Detection ────────────────────────────────────────────────

def run_duplicate_detection(
    owner_id: str,
    store: Optional[MemoryStore] = None,
    similarity_threshold: float = 0.92,
) -> Dict[str, Any]:
    """
    Non-destructive audit finding exact and semantic near-duplicates within a tenant's corpus.
    Does NOT delete memories; returns report for user or autonomous review.
    """
    owner_id = _require_valid_owner_id(owner_id)
    store = store or MemoryStore()

    memories = store.list_memories(owner_id=owner_id, status=MemoryStatus.ACTIVE.value, limit=500)
    vectors = dict(store.list_vectors(owner_id))

    exact_duplicates: List[Dict[str, Any]] = []
    semantic_duplicates: List[Dict[str, Any]] = []

    # 1. Exact duplicate detection (O(N) via content hash)
    seen_hashes: Dict[str, MemoryEntry] = {}
    for mem in memories:
        h = compute_content_hash(f"{mem.title}\n{mem.content}")
        if h in seen_hashes:
            primary = seen_hashes[h]
            exact_duplicates.append({
                "primary_id": primary.id,
                "primary_title": primary.title,
                "duplicate_id": mem.id,
                "duplicate_title": mem.title,
                "similarity": 1.0,
            })
        else:
            seen_hashes[h] = mem

    # 2. Semantic near-duplicate detection via pairwise vector similarity
    mem_list = [m for m in memories if m.id in vectors]
    n = len(mem_list)
    for i in range(n):
        for j in range(i + 1, n):
            m1 = mem_list[i]
            m2 = mem_list[j]
            v1 = vectors[m1.id]
            v2 = vectors[m2.id]
            sim = cosine_similarity(v1, v2)
            if sim >= similarity_threshold:
                semantic_duplicates.append({
                    "id_a": m1.id,
                    "title_a": m1.title,
                    "id_b": m2.id,
                    "title_b": m2.title,
                    "cosine_similarity": round(sim, 4),
                })

    return {
        "job": "duplicate_detection",
        "owner_id": owner_id,
        "total_active_memories": len(memories),
        "exact_duplicates": exact_duplicates,
        "semantic_duplicates": semantic_duplicates,
        "exact_count": len(exact_duplicates),
        "semantic_count": len(semantic_duplicates),
    }


# ── Job 4: Vector Index Rebuild ───────────────────────────────────────────────

def run_index_rebuild(
    owner_id: str,
    store: Optional[MemoryStore] = None,
    force_mode: Optional[str] = None,
    # threshold is accepted but ignored for backward-compat; AutoIndexConfig is authoritative.
    threshold: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Rebuild the active vector index from SQLite persistent BLOBs and verify consistency.
    Uses canonical AutoIndexConfig for engine selection — never a raw threshold.
    """
    owner_id = _require_valid_owner_id(owner_id)
    store = store or MemoryStore()

    vectors = store.list_vectors(owner_id)
    start_time = time.time()
    index: VectorIndex = get_vector_index(
        owner_id=owner_id,
        vectors=vectors,
        config=AutoIndexConfig(),
        force_mode=force_mode,
    )
    duration_ms = (time.time() - start_time) * 1000.0

    return {
        "job": "index_rebuild",
        "owner_id": owner_id,
        "index_type": index.index_type,
        "vector_count": index.size(),
        "duration_ms": round(duration_ms, 2),
        "rebuilt": True,
    }


# ── Job 5: Contradiction Scan ─────────────────────────────────────────────────

def run_contradiction_scan(
    owner_id: str,
    store: Optional[MemoryStore] = None,
    limit: int = 200,
    persist: bool = True,
) -> Dict[str, Any]:
    """
    Scan active memories for pairwise factual or logical contradictions.
    Persists findings to memory_contradictions table for non-destructive review.
    """
    owner_id = _require_valid_owner_id(owner_id)
    store = store or MemoryStore()

    detector = ContradictionDetector(store=store)
    records = detector.scan_tenant_contradictions(owner_id=owner_id, limit=limit, persist=persist)

    return {
        "job": "contradiction_scan",
        "owner_id": owner_id,
        "detected_count": len(records),
        "contradictions": [r.to_dict() for r in records],
        "persisted": persist,
    }


# ── Job 6: Consolidation Scan ─────────────────────────────────────────────────

def run_consolidation_scan(
    owner_id: str,
    store: Optional[MemoryStore] = None,
    limit: int = 200,
    persist: bool = True,
) -> Dict[str, Any]:
    """
    Scan active memories for consolidation candidates across exact, normalized, and semantic tiers.
    Persists proposals to memory_consolidations table for review.
    """
    owner_id = _require_valid_owner_id(owner_id)
    store = store or MemoryStore()

    consolidator = MemoryConsolidator(store=store)
    proposals = consolidator.scan_tenant_consolidations(owner_id=owner_id, limit=limit, persist=persist)

    return {
        "job": "consolidation_scan",
        "owner_id": owner_id,
        "proposed_clusters": len(proposals),
        "proposals": [p.to_dict() for p in proposals],
        "persisted": persist,
    }


# ── Job 7: Cache TTL Prune ────────────────────────────────────────────────────

def run_cache_prune_expired(
    cache: Optional[EmbeddingCache] = None,
    store: Optional[MemoryStore] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Prune all expired embedding cache entries from in-memory LRU and SQLite persistence.
    """
    store = store or MemoryStore()
    cache = cache or EmbeddingCache(db=store.db)

    pruned = cache.prune_expired()
    return {
        "job": "cache_prune_expired",
        "pruned_entries": pruned,
    }


# ── Job 8: Memory Lifecycle & Staleness Cleanup ───────────────────────────────

def run_memory_lifecycle_cleanup(
    owner_id: str,
    store: Optional[MemoryStore] = None,
    staleness_freshness_threshold: float = 0.05,
    limit: int = 500,
) -> Dict[str, Any]:
    """
    Scans active memories for expiration and staleness:
    - If expires_at is in the past: transitions status to EXPIRED.
    - If freshness score falls below threshold: transitions status to STALE.
    Non-destructive: memories remain in store with updated status for auditing.
    """
    owner_id = _require_valid_owner_id(owner_id)
    store = store or MemoryStore()

    memories = store.list_memories(owner_id=owner_id, status=MemoryStatus.ACTIVE.value, limit=limit)
    now = time.time()
    expired_count = 0
    staled_count = 0

    for mem in memories:
        if mem.is_expired(now):
            store.update_memory(
                memory_id=mem.id,
                owner_id=owner_id,
                status=MemoryStatus.EXPIRED.value,
            )
            expired_count += 1
        elif mem.compute_freshness(now) < staleness_freshness_threshold:
            store.update_memory(
                memory_id=mem.id,
                owner_id=owner_id,
                status=MemoryStatus.STALE.value,
            )
            staled_count += 1

    return {
        "job": "memory_lifecycle_cleanup",
        "owner_id": owner_id,
        "total_evaluated": len(memories),
        "expired_count": expired_count,
        "staled_count": staled_count,
    }


# ── Unified Job Dispatcher ────────────────────────────────────────────────────

MAINTENANCE_JOBS = {
    "embedding_backfill": run_embedding_backfill,
    "stale_embedding_repair": run_stale_embedding_repair,
    "duplicate_detection": run_duplicate_detection,
    "index_rebuild": run_index_rebuild,
    "contradiction_scan": run_contradiction_scan,
    "consolidation_scan": run_consolidation_scan,
    "cache_prune_expired": run_cache_prune_expired,
    "memory_lifecycle_cleanup": run_memory_lifecycle_cleanup,
    "staleness_prune": run_memory_lifecycle_cleanup,
}


def dispatch_maintenance_job(
    job_name: str,
    owner_id: str = "default_tenant",
    store: Optional[MemoryStore] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Unified entry point for executing any of the 8 maintenance jobs.
    """
    if job_name not in MAINTENANCE_JOBS:
        raise ValueError(f"Unknown maintenance job '{job_name}'. Valid jobs: {sorted(list(MAINTENANCE_JOBS.keys()))}")

    job_fn = MAINTENANCE_JOBS[job_name]
    if job_name == "cache_prune_expired":
        return job_fn(store=store, **kwargs)
    return job_fn(owner_id=owner_id, store=store, **kwargs)
