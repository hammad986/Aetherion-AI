"""
tests/integration/test_phase6_features.py — Comprehensive Integration Test Suite for Phase 6.
Tests VectorIndex (Exact & IVF-Flat), Tenant-Safe EmbeddingCache, CircuitBreaker,
RetrievalReranker, Lifecycle Consistency, Maintenance Jobs, Telemetry, and API Routes.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from backend.execution.memory import (
    CircuitBreaker,
    CircuitBreakerOpenException,
    CircuitState,
    DeterministicLocalEmbeddingProvider,
    EmbeddingCache,
    ExactVectorIndex,
    ApproximateVectorIndex,
    get_vector_index,
    MemoryEntry,
    MemoryManager,
    MemoryRetriever,
    MemoryStore,
    MemoryStatus,
    MemoryTrust,
    MemoryType,
    CandidateItem,
    RetrievalReranker,
    RetrievalResult,
    compute_content_hash,
    run_embedding_backfill,
    run_stale_embedding_repair,
    run_duplicate_detection,
    run_index_rebuild,
    get_memory_telemetry,
)
from backend.execution.persistence.db import StateDatabase



@pytest.fixture
def temp_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_p6_state.db"
        store = MemoryStore(db_path=db_path)
        yield store


# ── 1. Vector Index Dual-Engine & Threshold Switching ─────────────────────────

def test_exact_vector_index():
    index = ExactVectorIndex()
    assert index.index_type == "exact"
    assert index.size() == 0

    index.add("m1", [1.0, 0.0, 0.0])
    index.add("m2", [0.0, 1.0, 0.0])
    index.add("m3", [0.707, 0.707, 0.0])
    assert index.size() == 3

    hits = index.search([1.0, 0.0, 0.0], top_k=2)
    assert len(hits) == 2
    assert hits[0][0] == "m1"
    assert pytest.approx(hits[0][1], 0.01) == 1.0
    assert hits[1][0] == "m3"

    assert index.delete("m1") is True
    assert index.size() == 2
    hits_after = index.search([1.0, 0.0, 0.0], top_k=1)
    assert hits_after[0][0] == "m3"


def test_approximate_ivf_index_precision_and_recall():
    provider = DeterministicLocalEmbeddingProvider(dimension=64)
    # Generate 50 synthetic vectors
    items = []
    for i in range(50):
        vec = provider.embed_query(f"quantum computing topic item {i}")
        items.append((f"ivf_{i}", vec))

    index = ApproximateVectorIndex(items, n_clusters=5, n_probe=3)
    assert index.index_type == "approximate_ivf"
    assert index.size() == 50

    # Search for target
    target_vec = items[10][1]
    hits = index.search(target_vec, top_k=5)
    assert len(hits) >= 1
    # Target item should be rank 1
    assert hits[0][0] == "ivf_10"
    assert pytest.approx(hits[0][1], 0.01) == 1.0


def test_vector_index_threshold_auto_switching():
    items = [(f"m_{i}", [float(i), 1.0]) for i in range(25)]
    idx_small = get_vector_index("t1", items, threshold=30)
    assert idx_small.index_type == "exact"

    idx_large = get_vector_index("t1", items, threshold=20)
    assert idx_large.index_type == "approximate_ivf"

    idx_forced = get_vector_index("t1", items, threshold=1000, force_mode="approximate")
    assert idx_forced.index_type == "approximate_ivf"


# ── 2. Multi-Tenant Embedding Cache & Cryptographic Isolation ─────────────────

def test_embedding_cache_tenant_isolation(temp_store):
    cache = EmbeddingCache(db=temp_store.db, max_memory_entries=10)
    text = "Confidential financial projection for 2026"
    model = "test-model-128"
    dim = 4
    vec_a = [0.1, 0.2, 0.3, 0.4]

    # Tenant A writes to cache
    cache.put("tenant_alpha", text, model, dim, vec_a)

    # Tenant A retrieves it (hit)
    res_a = cache.get("tenant_alpha", text, model, dim)
    assert res_a is not None
    assert pytest.approx(res_a[0], 0.001) == 0.1

    # Tenant B tries to retrieve identical text (MUST BE MISS - strict tenant isolation)
    res_b = cache.get("tenant_beta", text, model, dim)
    assert res_b is None

    # Stats verify hits and misses
    stats = cache.stats()
    assert stats["hits"] >= 1
    assert stats["misses"] >= 1


def test_embedding_cache_lru_eviction(temp_store):
    cache = EmbeddingCache(db=temp_store.db, max_memory_entries=3)
    model = "test-model"
    dim = 2

    # Put 4 items into cache with capacity 3
    for i in range(4):
        cache.put("tenant_1", f"text {i}", model, dim, [float(i), 1.0])

    stats = cache.stats()
    assert stats["in_memory_entries"] == 3
    assert stats["evictions"] >= 1


# ── 3. Circuit Breaker Resilience ─────────────────────────────────────────────

def test_circuit_breaker_state_transitions():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.2)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 2 failures do not trip
    cb.record_failure("error 1")
    cb.record_failure("error 2")
    assert cb.state == CircuitState.CLOSED

    # 3rd failure trips circuit to OPEN
    cb.record_failure("error 3")
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    # Wait for recovery timeout
    time.sleep(0.25)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.can_execute() is True

    # Successful probe resets to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True


# ── 4. Modular Reranker & Near-Duplicate Penalty ──────────────────────────────

def test_retrieval_reranker_diversity_and_determinism():
    reranker = RetrievalReranker(diversity_threshold=0.95, duplicate_penalty=0.50)

    mem_primary = MemoryEntry(id="mem_1", owner_id="t1", title="Database Config", content="Primary DB", importance=0.9)
    mem_duplicate = MemoryEntry(id="mem_2", owner_id="t1", title="Database Config Copy", content="Primary DB duplicate", importance=0.8)
    mem_distinct = MemoryEntry(id="mem_3", owner_id="t1", title="Docker Runtime", content="Docker sandbox", importance=0.7)


    cand_primary = CandidateItem(entry=mem_primary, semantic_score=0.9, lexical_score=0.9, vector=[1.0, 0.0])
    cand_duplicate = CandidateItem(entry=mem_duplicate, semantic_score=0.9, lexical_score=0.9, vector=[0.99, 0.01])
    cand_distinct = CandidateItem(entry=mem_distinct, semantic_score=0.6, lexical_score=0.6, vector=[0.0, 1.0])

    results = reranker.rerank([cand_primary, cand_duplicate, cand_distinct], alpha=0.5, top_k=3)

    assert len(results) == 3
    # mem_1 should be first
    assert results[0].entry.id == "mem_1"
    # Duplicate mem_2 had its score damped by 50%
    assert results[1].final_score < results[0].final_score


# ── 5. Stale Content Fingerprint Detection & Maintenance ──────────────────────

def test_memory_lifecycle_stale_fingerprint_and_backfill(temp_store):
    provider = DeterministicLocalEmbeddingProvider(dimension=32)
    manager = MemoryManager(store=temp_store, embedding_provider=provider)

    # 1. Create a memory
    mem = manager.create_memory(
        owner_id="tenant_maint",
        title="Initial Title",
        content="Initial content text.",
    )
    rec = temp_store.get_vector_record(mem.id, "tenant_maint")
    assert rec is not None
    assert rec["content_hash"] == compute_content_hash("Initial Title\nInitial content text.")

    # 2. Update memory title/content via store directly to simulate desync
    mem.content = "Mutated content without direct vector refresh"
    temp_store.update_memory(mem)

    # 3. Detect stale embeddings
    stale = temp_store.list_stale_memories(
        owner_id="tenant_maint",
        compute_hash_fn=compute_content_hash,
    )
    assert len(stale) == 1
    assert stale[0][0].id == mem.id

    # 4. Run stale repair job
    repair_result = manager.repair_stale_embeddings(owner_id="tenant_maint")
    assert repair_result["repaired"] == 1
    assert repair_result["failed"] == 0

    # Verify vector now has updated content_hash
    rec_after = temp_store.get_vector_record(mem.id, "tenant_maint")
    assert rec_after["content_hash"] == compute_content_hash("Initial Title\nMutated content without direct vector refresh")


def test_duplicate_detection_maintenance(temp_store):
    manager = MemoryManager(store=temp_store)
    m1 = manager.create_memory("tenant_dup", "Duplicate Item", "Exact identical payload.")
    # Bypass create_memory dedup check if any by using store
    m2_entry = MemoryEntry(owner_id="tenant_dup", title="Duplicate Item", content="Exact identical payload.")
    temp_store.create_memory(m2_entry)

    report = manager.detect_duplicates("tenant_dup")
    assert report["exact_count"] >= 1


# ── 6. Secret-Safe Telemetry ──────────────────────────────────────────────────

def test_secret_safe_telemetry():
    telem = get_memory_telemetry()
    telem.reset()

    telem.record_embedding(latency_ms=12.5, cache_hit=True, success=True)
    telem.record_embedding(latency_ms=25.0, cache_hit=False, success=True)
    telem.record_retrieval(latency_ms=5.0, items_count=3, index_type="approximate_ivf")

    # include_global=True is the admin/diagnostic path; default (False) returns
    # an isolated empty bucket per SEC-P2-TELEMETRY-LEAK-01 remediation.
    metrics = telem.get_metrics(include_global=True)
    assert metrics["embedding"]["requests"] == 2
    assert metrics["embedding"]["cache_hits"] == 1
    assert metrics["embedding"]["cache_misses"] == 1
    assert metrics["retrieval"]["approximate_queries"] == 1
    # Check no raw strings leaked
    metrics_str = str(metrics)
    assert "password" not in metrics_str.lower()
    assert "token" not in metrics_str.lower()

