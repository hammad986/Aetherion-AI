"""
backend/execution/memory/__init__.py — Persistent Memory, Vector Indexing & Retrieval Subsystem for Phase 6.
"""
from .models import (
    MemoryEntry,
    MemorySource,
    MemoryStatus,
    MemoryTrust,
    MemoryType,
    ContradictionRecord,
    ContradictionState,
    ConsolidationRecord,
    ConsolidationState,
    LearningSignal,
    LearningSignalStatus,
    MemoryFeedbackRecord,
)
from .store import MemoryStore
from .importer import MemoryImporter, MemoryImportException
from .retrieval import MemoryRetriever, HybridMemoryRetriever, HybridSearchResult
from .manager import MemoryManager
from .embeddings import (
    EmbeddingProvider,
    DeterministicLocalEmbeddingProvider,
    RemoteAPIEmbeddingProvider,
    get_default_embedding_provider,
    cosine_similarity,
    CircuitBreaker,
    CircuitState,
    CircuitBreakerOpenException,
)
from .index import (
    VectorIndex,
    ExactVectorIndex,
    ApproximateVectorIndex,
    NativeVectorIndex,
    AutoIndexConfig,
    AutoIndexStrategy,
    get_vector_index,
)
from .cache import (
    EmbeddingCache,
    compute_content_hash,
    compute_cache_key,
)
from .contradictions import ContradictionDetector
from .consolidation import MemoryConsolidator
from .learning import AgentLearningEngine
from .feedback import MemoryFeedbackTracker
from .reranker import (
    CandidateItem,
    RetrievalReranker,
    RetrievalResult,
)
from .maintenance import (
    run_embedding_backfill,
    run_stale_embedding_repair,
    run_duplicate_detection,
    run_index_rebuild,
    run_contradiction_scan,
    run_consolidation_scan,
    run_cache_prune_expired,
    run_memory_lifecycle_cleanup,
    dispatch_maintenance_job,
)
from .telemetry import (
    MemoryTelemetry,
    get_memory_telemetry,
)
from .evaluation import (
    run_retrieval_benchmark,
    BENCHMARK_FIXTURES,
)

__all__ = [
    "MemoryEntry",
    "MemorySource",
    "MemoryStatus",
    "MemoryTrust",
    "MemoryType",
    "ContradictionRecord",
    "ContradictionState",
    "ConsolidationRecord",
    "ConsolidationState",
    "LearningSignal",
    "LearningSignalStatus",
    "MemoryFeedbackRecord",
    "MemoryStore",
    "MemoryImporter",
    "MemoryImportException",
    "MemoryRetriever",
    "HybridMemoryRetriever",
    "HybridSearchResult",
    "MemoryManager",
    "EmbeddingProvider",
    "DeterministicLocalEmbeddingProvider",
    "RemoteAPIEmbeddingProvider",
    "get_default_embedding_provider",
    "cosine_similarity",
    "CircuitBreaker",
    "CircuitState",
    "CircuitBreakerOpenException",
    "VectorIndex",
    "ExactVectorIndex",
    "ApproximateVectorIndex",
    "NativeVectorIndex",
    "AutoIndexConfig",
    "AutoIndexStrategy",
    "get_vector_index",
    "EmbeddingCache",
    "compute_content_hash",
    "compute_cache_key",
    "ContradictionDetector",
    "MemoryConsolidator",
    "AgentLearningEngine",
    "MemoryFeedbackTracker",
    "CandidateItem",
    "RetrievalReranker",
    "RetrievalResult",
    "run_embedding_backfill",
    "run_stale_embedding_repair",
    "run_duplicate_detection",
    "run_index_rebuild",
    "run_contradiction_scan",
    "run_consolidation_scan",
    "run_cache_prune_expired",
    "run_memory_lifecycle_cleanup",
    "dispatch_maintenance_job",
    "MemoryTelemetry",
    "get_memory_telemetry",
    "run_retrieval_benchmark",
    "BENCHMARK_FIXTURES",
]
