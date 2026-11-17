"""
backend/execution/memory/manager.py — Memory System Orchestrator Facade for Phase 6.
Coordinates persistence, vector indexing, multi-tenant caching, hybrid retrieval,
background maintenance routines, and secret-safe telemetry.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .cache import EmbeddingCache, compute_content_hash
from .consolidation import MemoryConsolidator
from .contradictions import ContradictionDetector
from .embeddings import EmbeddingProvider, get_default_embedding_provider
from .evaluation import run_retrieval_benchmark
from .feedback import MemoryFeedbackTracker
from .importer import MemoryImporter
from .learning import AgentLearningEngine
from .maintenance import (
    dispatch_maintenance_job,
    run_cache_prune_expired,
    run_consolidation_scan,
    run_contradiction_scan,
    run_duplicate_detection,
    run_embedding_backfill,
    run_index_rebuild,
    run_memory_lifecycle_cleanup,
    run_stale_embedding_repair,
)
from .models import MemoryEntry, MemorySource, MemoryStatus, MemoryTrust, MemoryType
from .reranker import RetrievalResult
from .retrieval import MemoryRetriever
from .store import MemoryStore
from .telemetry import MemoryTelemetry, get_memory_telemetry

logger = logging.getLogger("aetherion.memory.manager")


def _require_valid_owner_id(owner_id: Any) -> str:
    """Validate that owner_id is strictly a non-empty string, preventing service-layer BOLA."""
    if not isinstance(owner_id, str):
        raise KeyError("A valid non-empty string owner_id is required.")
    normalized_owner_id = owner_id.strip()
    if not normalized_owner_id:
        raise KeyError("A valid non-empty string owner_id is required.")
    return normalized_owner_id


class MemoryManager:
    """Coordinates persistence, vector embeddings, hybrid retrieval, caching, maintenance, and import."""

    def __init__(
        self,
        store: Optional[MemoryStore] = None,
        retriever: Optional[MemoryRetriever] = None,
        importer: Optional[MemoryImporter] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
        cache: Optional[EmbeddingCache] = None,
        telemetry: Optional[MemoryTelemetry] = None,
    ):
        self.store = store or MemoryStore()
        self.embedding_provider = embedding_provider or get_default_embedding_provider()
        self.cache = cache or EmbeddingCache(db=self.store.db)
        self.telemetry = telemetry or get_memory_telemetry()
        self.retriever = retriever or MemoryRetriever(
            store=self.store,
            embedding_provider=self.embedding_provider,
            cache=self.cache,
            telemetry=self.telemetry,
        )
        self.importer = importer or MemoryImporter(store=self.store)
        self.contradiction_detector = ContradictionDetector(store=self.store)
        self.consolidator = MemoryConsolidator(store=self.store)
        self.learning_engine = AgentLearningEngine(
            store=self.store,
            embedding_provider=self.embedding_provider,
            cache=self.cache,
        )
        self.feedback_tracker = MemoryFeedbackTracker(store=self.store)

    def create_memory(
        self,
        owner_id: str,
        title: str,
        content: str,
        type: str = MemoryType.KNOWLEDGE.value,
        importance: float = 0.7,
        tags: Optional[List[str]] = None,
        source: str = MemorySource.USER_CREATED.value,
        trust_level: str = MemoryTrust.USER_CONTROLLED.value,
    ) -> MemoryEntry:
        """Create and persist a new user-controlled memory with vector embedding and caching."""
        owner_id = _require_valid_owner_id(owner_id)
        if trust_level == MemoryTrust.USER_CONTROLLED.value:
            if source == MemorySource.AGENT_GENERATED.value:
                trust_level = MemoryTrust.AGENT_GENERATED.value
            elif source in (
                MemorySource.IMPORTED.value,
                MemorySource.IMPORTED_FILE.value,
                MemorySource.IMPORTED_TEXT.value,
                MemorySource.IMPORTED_AI_EXPORT.value,
            ):
                trust_level = MemoryTrust.IMPORTED.value

        mem = MemoryEntry(
            owner_id=owner_id,
            title=title,
            content=content,
            type=type,
            importance=importance,
            tags=tags or [],
            source=source,
            trust_level=trust_level,
        )
        saved = self.store.create_memory(mem)

        # Generate and persist vector embedding with content_hash and caching
        if self.embedding_provider:
            try:
                text_to_embed = f"{saved.title}\n{saved.content}"
                c_hash = compute_content_hash(text_to_embed)

                # Check cache first
                vec = self.cache.get(
                    owner_id=owner_id,
                    text=text_to_embed,
                    model_name=self.embedding_provider.model_name,
                    dimensions=self.embedding_provider.dimension,
                )
                if vec is None:
                    vec = self.embedding_provider.embed_query(text_to_embed)
                    self.cache.put(
                        owner_id=owner_id,
                        text=text_to_embed,
                        model_name=self.embedding_provider.model_name,
                        dimensions=self.embedding_provider.dimension,
                        vector=vec,
                    )

                self.store.save_vector(
                    memory_id=saved.id,
                    owner_id=owner_id,
                    vector=vec,
                    embedding_model=self.embedding_provider.model_name,
                    content_hash=c_hash,
                )
            except Exception as exc:
                logger.warning("Failed to generate vector embedding for memory '%s': %s", saved.id, exc)

        return saved

    def get_memory(self, memory_id: str, owner_id: str) -> Optional[MemoryEntry]:
        """Fetch memory entry if owned by tenant."""
        return self.store.get_memory(memory_id, owner_id)

    def update_memory(
        self,
        memory_id: str,
        owner_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        type: Optional[str] = None,
        importance: Optional[float] = None,
        tags: Optional[List[str]] = None,
    ) -> MemoryEntry:
        """User edit of an existing memory entry and refresh of its vector embedding and cache."""
        mem = self.store.get_memory(memory_id, owner_id)
        if not mem:
            raise KeyError(f"Memory '{memory_id}' not found.")

        old_text = f"{mem.title}\n{mem.content}"
        content_changed = False

        if title is not None:
            mem.title = title.strip()
            content_changed = True
        if content is not None:
            mem.content = content.strip()
            content_changed = True
        if type is not None:
            mem.type = type
        if importance is not None:
            mem.importance = float(importance)
        if tags is not None:
            mem.tags = list(tags)

        updated = self.store.update_memory(mem)

        # Refresh embedding if text content changed
        if content_changed and self.embedding_provider:
            try:
                new_text = f"{updated.title}\n{updated.content}"
                c_hash = compute_content_hash(new_text)

                # Invalidate old cache entry for this text
                self.cache.invalidate(owner_id, text=old_text)

                vec = self.embedding_provider.embed_query(new_text)
                self.cache.put(
                    owner_id=owner_id,
                    text=new_text,
                    model_name=self.embedding_provider.model_name,
                    dimensions=self.embedding_provider.dimension,
                    vector=vec,
                )
                self.store.save_vector(
                    memory_id=updated.id,
                    owner_id=owner_id,
                    vector=vec,
                    embedding_model=self.embedding_provider.model_name,
                    content_hash=c_hash,
                )
            except Exception as exc:
                logger.warning("Failed to refresh vector embedding for memory '%s': %s", updated.id, exc)

        return updated

    def delete_memory(self, memory_id: str, owner_id: str) -> bool:
        """Soft delete a memory entry and remove its vector and cache."""
        mem = self.store.get_memory(memory_id, owner_id)
        ok = self.store.soft_delete_memory(memory_id, owner_id)
        if ok:
            if mem:
                text = f"{mem.title}\n{mem.content}"
                try:
                    self.cache.invalidate(owner_id, text=text)
                except Exception:
                    pass
            try:
                self.store.delete_vector(memory_id, owner_id)
            except Exception as exc:
                logger.debug("Vector deletion notice for memory '%s': %s", memory_id, exc)
        return ok

    def archive_memory(self, memory_id: str, owner_id: str) -> Optional[MemoryEntry]:
        """Archive a memory entry."""
        return self.store.archive_memory(memory_id, owner_id)

    def restore_memory(self, memory_id: str, owner_id: str) -> Optional[MemoryEntry]:
        """Restore an archived or deleted memory entry to active and reindex."""
        restored = self.store.restore_memory(memory_id, owner_id)
        if restored and self.embedding_provider:
            try:
                text = f"{restored.title}\n{restored.content}"
                c_hash = compute_content_hash(text)
                vec = self.cache.get(
                    owner_id=owner_id,
                    text=text,
                    model_name=self.embedding_provider.model_name,
                    dimensions=self.embedding_provider.dimension,
                )
                if vec is None:
                    vec = self.embedding_provider.embed_query(text)
                    self.cache.put(
                        owner_id=owner_id,
                        text=text,
                        model_name=self.embedding_provider.model_name,
                        dimensions=self.embedding_provider.dimension,
                        vector=vec,
                    )
                self.store.save_vector(
                    memory_id=restored.id,
                    owner_id=owner_id,
                    vector=vec,
                    embedding_model=self.embedding_provider.model_name,
                    content_hash=c_hash,
                )
            except Exception as exc:
                logger.debug("Failed to re-index vector for restored memory '%s': %s", restored.id, exc)
        return restored

    def list_memories(
        self,
        owner_id: str,
        type: Optional[str] = None,
        status: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 50,
    ) -> List[MemoryEntry]:
        """List memories for an owner."""
        return self.store.list_memories(owner_id=owner_id, type=type, status=status, source=source, limit=limit)

    def search_memories(
        self,
        owner_id: str,
        query: str = "",
        tags: Optional[List[str]] = None,
        type: Optional[str] = None,
        limit: int = 50,
    ) -> List[MemoryEntry]:
        """Search memories by keyword, tags, or type."""
        return self.store.search_memories(owner_id=owner_id, query=query, tags=tags, type=type, limit=limit)

    def search_hybrid(
        self,
        owner_id: str,
        query: str,
        limit: int = 10,
        alpha: float = 0.6,
        threshold: Optional[float] = None,
        min_score: float = 0.05,
        type_filter: Optional[str] = None,
        trust_filter: Optional[str] = None,
        filter_type: Optional[str] = None,
        weights: Optional[Dict[str, float]] = None,
        index_mode: Optional[str] = "auto",
    ) -> List[Tuple[MemoryEntry, float]]:
        """Perform hybrid lexical + semantic search with deterministic relevance ranking."""
        effective_min_score = threshold if threshold is not None else min_score
        effective_type_filter = filter_type or type_filter
        if weights is None and alpha is not None:
            clamped_alpha = max(0.0, min(1.0, float(alpha)))
            weights = {
                "semantic": clamped_alpha * 0.8,
                "lexical": (1.0 - clamped_alpha) * 0.8,
                "importance": 0.10,
                "recency": 0.10,
            }
        return self.retriever.retrieve_relevant(
            prompt=query,
            owner_id=owner_id,
            max_memories=limit,
            min_score=effective_min_score,
            use_semantic=True,
            weights=weights,
            type_filter=effective_type_filter,
            trust_filter=trust_filter,
            index_mode=index_mode,
        )

    def preview_import(
        self,
        raw_data: str | bytes | Dict[str, Any] | List[Any],
        owner_id: str,
        source_format: str = "auto",
    ) -> Dict[str, Any]:
        """Inspect and preview candidate memories before committing."""
        return self.importer.preview_import(raw_data, owner_id=owner_id, source_format=source_format)

    def commit_import(
        self,
        preview_id_or_entries: str | List[Dict[str, Any]],
        owner_id: str,
        skip_duplicates: bool = True,
    ) -> List[MemoryEntry]:
        """Persist previewed memory entries into storage, generate embeddings, and update cache."""
        committed = self.importer.commit_import(
            preview_id_or_entries,
            owner_id=owner_id,
            skip_duplicates=skip_duplicates,
        )
        if self.embedding_provider and committed:
            for entry in committed:
                try:
                    text_to_embed = f"{entry.title}\n{entry.content}"
                    c_hash = compute_content_hash(text_to_embed)
                    vec = self.cache.get(
                        owner_id=owner_id,
                        text=text_to_embed,
                        model_name=self.embedding_provider.model_name,
                        dimensions=self.embedding_provider.dimension,
                    )
                    if vec is None:
                        vec = self.embedding_provider.embed_query(text_to_embed)
                        self.cache.put(
                            owner_id=owner_id,
                            text=text_to_embed,
                            model_name=self.embedding_provider.model_name,
                            dimensions=self.embedding_provider.dimension,
                            vector=vec,
                        )
                    self.store.save_vector(
                        memory_id=entry.id,
                        owner_id=owner_id,
                        vector=vec,
                        embedding_model=self.embedding_provider.model_name,
                        content_hash=c_hash,
                    )
                except Exception as exc:
                    logger.debug("Failed to index vector for imported memory '%s': %s", entry.id, exc)
        return committed

    def build_system_memory_context(
        self,
        prompt: str = "",
        owner_id: str = "",
        max_memories: int = 5,
        max_chars: int = 3000,
        use_semantic: bool = True,
        query: Optional[str] = None,
        index_mode: Optional[str] = "auto",
    ) -> str:
        """Generate bounded, framed context string to pass into agent runtime."""
        effective_prompt = query if query is not None else prompt
        return self.retriever.build_system_memory_context(
            prompt=effective_prompt,
            owner_id=owner_id,
            max_memories=max_memories,
            max_chars=max_chars,
            use_semantic=use_semantic,
            index_mode=index_mode,
        )

    # ── Phase 6 Maintenance & Observability Methods ───────────────────────────

    def backfill_embeddings(self, owner_id: str, batch_size: int = 50) -> Dict[str, Any]:
        """Trigger background backfill of missing memory vector embeddings."""
        return run_embedding_backfill(
            owner_id=owner_id,
            store=self.store,
            provider=self.embedding_provider,
            cache=self.cache,
            batch_size=batch_size,
        )

    def repair_stale_embeddings(self, owner_id: str, batch_size: int = 50) -> Dict[str, Any]:
        """Trigger background repair of out-of-sync or stale embeddings."""
        return run_stale_embedding_repair(
            owner_id=owner_id,
            store=self.store,
            provider=self.embedding_provider,
            cache=self.cache,
            batch_size=batch_size,
        )

    def detect_duplicates(self, owner_id: str, similarity_threshold: float = 0.92) -> Dict[str, Any]:
        """Run non-destructive audit for exact and semantic near-duplicates."""
        return run_duplicate_detection(
            owner_id=owner_id,
            store=self.store,
            similarity_threshold=similarity_threshold,
        )

    def rebuild_vector_index(
        self,
        owner_id: str,
        force_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reconstruct the in-memory/approximate vector index from persistent storage."""
        return run_index_rebuild(
            owner_id=owner_id,
            store=self.store,
            force_mode=force_mode,
        )

    def get_telemetry(self, owner_id: Optional[str] = None, include_global: bool = False) -> Dict[str, Any]:
        """Retrieve operational metrics and health gauges (tenant-scoped or global)."""
        return self.telemetry.get_metrics(owner_id=owner_id, include_global=include_global)

    def get_cache_stats(self, owner_id: Optional[str] = None) -> Dict[str, Any]:
        """Retrieve embedding cache performance and size stats (tenant-scoped or global)."""
        return self.cache.stats(owner_id=owner_id)

    # ── Phase 7 Intelligence & Life-Cycle Methods ─────────────────────────────

    def scan_contradictions(self, owner_id: str, limit: int = 100, persist: bool = True) -> List[Dict[str, Any]]:
        owner_id = _require_valid_owner_id(owner_id)
        records = self.contradiction_detector.scan_tenant_contradictions(owner_id=owner_id, limit=limit, persist=persist)
        return [r.to_dict() for r in records]

    def list_contradictions(self, owner_id: str, state: Optional[str] = None) -> List[Dict[str, Any]]:
        owner_id = _require_valid_owner_id(owner_id)
        records = self.store.list_contradictions(owner_id=owner_id, state=state)
        return [r.to_dict() for r in records]

    def resolve_contradiction(
        self,
        contradiction_id: str,
        owner_id: str,
        strategy: str,
        winner_id: Optional[str] = None,
    ) -> bool:
        owner_id = _require_valid_owner_id(owner_id)
        return self.contradiction_detector.resolve_contradiction(
            contradiction_id=contradiction_id,
            owner_id=owner_id,
            resolution_strategy=strategy,
            winner_memory_id=winner_id,
        )

    def scan_consolidations(self, owner_id: str, limit: int = 200, persist: bool = True) -> List[Dict[str, Any]]:
        owner_id = _require_valid_owner_id(owner_id)
        records = self.consolidator.scan_tenant_consolidations(owner_id=owner_id, limit=limit, persist=persist)
        return [r.to_dict() for r in records]

    def list_consolidations(self, owner_id: str, state: Optional[str] = None) -> List[Dict[str, Any]]:
        owner_id = _require_valid_owner_id(owner_id)
        records = self.store.list_consolidations(owner_id=owner_id, state=state)
        return [r.to_dict() for r in records]

    def apply_consolidation(
        self,
        consolidation_id: str,
        owner_id: str,
        merge_tags: bool = True,
        merge_metadata: bool = True,
    ) -> bool:
        owner_id = _require_valid_owner_id(owner_id)
        return self.consolidator.apply_consolidation(
            consolidation_id=consolidation_id,
            owner_id=owner_id,
            merge_tags=merge_tags,
            merge_metadata=merge_metadata,
        )

    def reject_consolidation(self, consolidation_id: str, owner_id: str) -> bool:
        owner_id = _require_valid_owner_id(owner_id)
        return self.consolidator.reject_consolidation(consolidation_id=consolidation_id, owner_id=owner_id)

    def evaluate_retrieval(self, eval_owner_id: str = "eval_benchmark_tenant") -> Dict[str, Any]:
        return run_retrieval_benchmark(retriever=self.retriever, store=self.store, eval_owner_id=eval_owner_id)

    def get_memory_health(self, owner_id: str) -> Dict[str, Any]:
        owner_id = _require_valid_owner_id(owner_id)
        active_memories = self.store.list_memories(owner_id=owner_id, status=MemoryStatus.ACTIVE.value)
        stale_memories = self.store.list_memories(owner_id=owner_id, status=MemoryStatus.STALE.value)
        expired_memories = self.store.list_memories(owner_id=owner_id, status=MemoryStatus.EXPIRED.value)
        vectors = self.store.list_vectors(owner_id=owner_id)
        conflicts = self.store.list_contradictions(owner_id=owner_id)
        consolidations = self.store.list_consolidations(owner_id=owner_id)
        cache_stats = self.cache.stats(owner_id=owner_id)
        telemetry_stats = self.telemetry.get_metrics(owner_id=owner_id)

        return {
            "owner_id": owner_id,
            "status": "healthy",
            "memory_counts": {
                "active": len(active_memories),
                "stale": len(stale_memories),
                "expired": len(expired_memories),
                "total": len(active_memories) + len(stale_memories) + len(expired_memories),
            },
            "vector_index": {
                "indexed_vectors": len(vectors),
                "coverage_pct": round(len(vectors) / max(1, len(active_memories)) * 100.0, 1),
            },
            "intelligence": {
                "open_conflicts": sum(1 for c in conflicts if c.state == "conflicting"),
                "total_conflicts": len(conflicts),
                "proposed_consolidations": sum(1 for c in consolidations if c.state == "proposed"),
                "total_consolidations": len(consolidations),
            },
            "cache": cache_stats,
            "telemetry": telemetry_stats,
        }

    def run_maintenance(self, job_name: str, owner_id: str, **kwargs: Any) -> Dict[str, Any]:
        owner_id = _require_valid_owner_id(owner_id)
        return dispatch_maintenance_job(job_name=job_name, owner_id=owner_id, store=self.store, **kwargs)

