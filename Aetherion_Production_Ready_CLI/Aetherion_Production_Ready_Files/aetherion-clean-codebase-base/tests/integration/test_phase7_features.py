"""
tests/integration/test_phase7_features.py — Comprehensive Integration Test Suite for Phase 7.
Verifies Enterprise Memory Intelligence, Contradiction Engine, Consolidation Tiering,
Agent Learning & Feedback, Scalable Vector Engine V2, Cache V2 with TTL,
Maintenance Orchestration (8 Jobs), and REST APIs.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from backend.execution.memory import (
    AgentLearningEngine,
    AutoIndexConfig,
    AutoIndexStrategy,
    ContradictionDetector,
    ContradictionRecord,
    ContradictionState,
    ConsolidationRecord,
    ConsolidationState,
    EmbeddingCache,
    ExactVectorIndex,
    LearningSignal,
    LearningSignalStatus,
    MemoryConsolidator,
    MemoryEntry,
    MemoryFeedbackRecord,
    MemoryFeedbackTracker,
    MemoryManager,
    MemorySource,
    MemoryStatus,
    MemoryStore,
    MemoryTrust,
    MemoryType,
    NativeVectorIndex,
    compute_cache_key,
    compute_content_hash,
    dispatch_maintenance_job,
    get_default_embedding_provider,
    get_vector_index,
    run_retrieval_benchmark,
)
from backend.execution.persistence.db import StateDatabase


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_phase7.db"
        db = StateDatabase(db_path)
        yield db


@pytest.fixture
def temp_store(temp_db):
    return MemoryStore(db=temp_db)


@pytest.fixture
def temp_cache(temp_db):
    return EmbeddingCache(db=temp_db, max_memory_entries=10, max_persistent_entries=50)


# ── 1. Contradiction Detection & Non-Destructive Resolution ───────────────────

def test_contradiction_detection_value_conflict(temp_store):
    detector = ContradictionDetector(store=temp_store)
    owner = "tenant_test_contra"

    mem_a = MemoryEntry(
        id="mem_db_port_5432",
        owner_id=owner,
        title="PostgreSQL Port Setting",
        content="Primary database port is 5432 for production database.",
        type=MemoryType.PROJECT_FACT.value,
        trust_level=MemoryTrust.USER_CONTROLLED.value,
        status=MemoryStatus.ACTIVE.value,
    )
    mem_b = MemoryEntry(
        id="mem_db_port_3306",
        owner_id=owner,
        title="Database Port Override",
        content="Primary database port is 3306 for production database.",
        type=MemoryType.PROJECT_FACT.value,
        trust_level=MemoryTrust.USER_CONTROLLED.value,
        status=MemoryStatus.ACTIVE.value,
    )

    temp_store.create_memory(mem_a)
    temp_store.create_memory(mem_b)

    conflicts = detector.detect_contradictions_for_memory(mem_a, [mem_b], persist=True)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.contradiction_type == "value_conflict"
    assert c.confidence >= 0.90
    assert c.state == ContradictionState.CONFLICTING.value

    # Non-destructive resolution via supersede
    ok = detector.resolve_contradiction(c.id, owner, resolution_strategy="supersede")
    assert ok is True

    # Check that older memory was marked STALE, NOT deleted
    stored_a = temp_store.get_memory(mem_a.id, owner)
    stored_b = temp_store.get_memory(mem_b.id, owner)
    assert stored_a is not None
    assert stored_b is not None
    assert stored_a.status == MemoryStatus.STALE.value


def test_contradiction_detection_polarity_negation(temp_store):
    detector = ContradictionDetector(store=temp_store)
    owner = "tenant_test_polarity"

    mem_a = MemoryEntry(
        id="mem_cache_enable",
        owner_id=owner,
        title="Redis Caching Configuration",
        content="Enable caching in production for fast query performance.",
        type=MemoryType.INSTRUCTION.value,
        trust_level=MemoryTrust.USER_CONTROLLED.value,
        status=MemoryStatus.ACTIVE.value,
    )
    mem_b = MemoryEntry(
        id="mem_cache_disable",
        owner_id=owner,
        title="Redis Caching Directive",
        content="Disable caching in production to prevent stale read issues.",
        type=MemoryType.INSTRUCTION.value,
        trust_level=MemoryTrust.USER_CONTROLLED.value,
        status=MemoryStatus.ACTIVE.value,
    )

    temp_store.create_memory(mem_a)
    temp_store.create_memory(mem_b)

    rec = detector.compare_memories(mem_a, mem_b)
    assert rec is not None
    assert rec.contradiction_type == "negation"
    assert rec.confidence >= 0.85


# ── 2. Memory Consolidation & Deduplication ───────────────────────────────────

def test_consolidation_exact_hash_and_apply(temp_store):
    consolidator = MemoryConsolidator(store=temp_store)
    owner = "tenant_test_consol"

    mem1 = MemoryEntry(
        id="mem_hash_1",
        owner_id=owner,
        title="Identical Documentation",
        content="All developers must write unit tests before merging pull requests.",
        tags=["tests", "docs"],
        type=MemoryType.INSTRUCTION.value,
        trust_level=MemoryTrust.USER_CONTROLLED.value,
        confidence=0.9,
    )
    mem2 = MemoryEntry(
        id="mem_hash_2",
        owner_id=owner,
        title="Duplicate Documentation",
        content="All developers must write unit tests before merging pull requests.",
        tags=["guidelines"],
        type=MemoryType.INSTRUCTION.value,
        trust_level=MemoryTrust.AGENT_GENERATED.value,
        confidence=0.7,
    )

    temp_store.create_memory(mem1)
    temp_store.create_memory(mem2)

    proposals = consolidator.scan_tenant_consolidations(owner, persist=True)
    assert len(proposals) >= 1
    prop = proposals[0]
    assert prop.consolidation_type == "exact_hash"
    assert prop.primary_memory_id == "mem_hash_1"  # USER_CONTROLLED elected over AGENT_GENERATED
    assert "mem_hash_2" in prop.candidate_ids

    # Apply consolidation non-destructively
    ok = consolidator.apply_consolidation(prop.id, owner, merge_tags=True)
    assert ok is True

    # Primary memory gets merged tags
    updated_primary = temp_store.get_memory(prop.primary_memory_id, owner)
    assert "guidelines" in updated_primary.tags
    assert "tests" in updated_primary.tags

    # Secondary memory is ARCHIVED with pointer, not deleted
    archived_sec = temp_store.get_memory("mem_hash_2", owner)
    assert archived_sec is not None
    assert archived_sec.status == MemoryStatus.ARCHIVED.value
    assert archived_sec.metadata.get("consolidated_into") == prop.primary_memory_id


def test_consolidation_semantic_near_duplicate(temp_store):
    consolidator = MemoryConsolidator(store=temp_store, semantic_threshold=0.75)
    owner = "tenant_test_sem_dup"

    mem1 = MemoryEntry(
        id="mem_sem_1",
        owner_id=owner,
        title="PostgreSQL Timeout",
        content="PostgreSQL timeout setting is 15 seconds.",
        type=MemoryType.PROJECT_FACT.value,
        trust_level=MemoryTrust.USER_CONTROLLED.value,
    )
    mem2 = MemoryEntry(
        id="mem_sem_2",
        owner_id=owner,
        title="Database Timeout Setting",
        content="PostgreSQL database timeout is configured to 15 seconds.",
        type=MemoryType.PROJECT_FACT.value,
        trust_level=MemoryTrust.USER_CONTROLLED.value,
    )

    temp_store.create_memory(mem1)
    temp_store.create_memory(mem2)

    prov = get_default_embedding_provider()
    v1 = prov.embed_query(f"{mem1.title}\n{mem1.content}")
    v2 = prov.embed_query(f"{mem2.title}\n{mem2.content}")
    temp_store.save_vector(mem1.id, owner, v1, prov.model_name)
    temp_store.save_vector(mem2.id, owner, v2, prov.model_name)

    proposals = consolidator.scan_tenant_consolidations(owner)
    assert len(proposals) >= 1
    assert proposals[0].consolidation_type in ("near_duplicate", "normalized_string")


# ── 3. Agent Learning Engine & Outcome Feedback ───────────────────────────────

def test_agent_learning_extraction_and_promotion(temp_store, temp_cache):
    prov = get_default_embedding_provider()
    learning = AgentLearningEngine(store=temp_store, embedding_provider=prov, cache=temp_cache)
    owner = "tenant_test_learning"

    signals = learning.extract_learning_signals_from_run(
        owner_id=owner,
        session_id="sess_abc123",
        task_prompt="Always prefer pytest over unittest for test execution.",
        status="completed",
        verification_status="verified",
        created_or_edited_files=["tests/test_foo.py", "backend/foo.py"],
        tool_action_count=4,
    )

    assert len(signals) >= 1
    # Check that solution pattern and user preference signals were extracted
    sig_types = [s.signal_type for s in signals]
    assert "solution_pattern" in sig_types or "user_preference" in sig_types

    target_sig = signals[0]
    promoted_mem = learning.promote_signal_to_memory(target_sig.id, owner)
    assert promoted_mem is not None
    assert promoted_mem.trust_level == MemoryTrust.AGENT_GENERATED.value
    assert promoted_mem.provenance.startswith("learning_signal:")

    # Verify vector was created for promoted memory
    vec = temp_store.get_vector(promoted_mem.id, owner)
    assert vec is not None
    assert len(vec) == prov.dimension


def test_memory_feedback_confidence_calibration(temp_store):
    feedback_tracker = MemoryFeedbackTracker(store=temp_store)
    owner = "tenant_test_feedback"

    mem = MemoryEntry(
        id="mem_fb_target",
        owner_id=owner,
        title="CI Build Script",
        content="Use make test to trigger the integration pipeline.",
        confidence=0.80,
        type=MemoryType.INSTRUCTION.value,
        trust_level=MemoryTrust.USER_CONTROLLED.value,
    )
    temp_store.create_memory(mem)

    # 1. Success feedback boosts confidence
    fb1 = feedback_tracker.record_feedback(
        owner_id=owner,
        memory_id=mem.id,
        session_id="sess_1",
        query="how to run tests",
        outcome="success",
        relevance_score=1.0,
    )
    assert fb1.outcome == "success"
    updated_1 = temp_store.get_memory(mem.id, owner)
    assert updated_1.confidence > 0.80

    # 2. Failure feedback decays confidence
    feedback_tracker.record_feedback(
        owner_id=owner,
        memory_id=mem.id,
        session_id="sess_2",
        query="run tests",
        outcome="failure",
    )
    updated_2 = temp_store.get_memory(mem.id, owner)
    assert updated_2.confidence < updated_1.confidence

    summary = feedback_tracker.get_memory_feedback_summary(owner, mem.id)
    assert summary["total"] == 2
    assert summary["success_count"] == 1
    assert summary["failure_count"] == 1


# ── 4. Embedding Cache V2: Versioning, TTL, & Bounded LRU ─────────────────────

def test_cache_v2_ttl_expiration(temp_store, temp_cache):
    owner = "tenant_test_ttl"
    text = "Ephemeral transient token"
    model = "test-model"
    dim = 4
    vec = [0.1, 0.2, 0.3, 0.4]

    # Put with very short TTL (0.1s)
    temp_cache.put(owner, text, model, dim, vec, ttl=0.1)

    # Immediate get is a hit
    hit = temp_cache.get(owner, text, model, dim)
    assert hit is not None

    # Wait for expiration
    time.sleep(0.2)
    miss = temp_cache.get(owner, text, model, dim)
    assert miss is None


def test_cache_v2_version_isolation(temp_store, temp_cache):
    owner = "tenant_test_ver"
    text = "Vector version isolation test"
    model = "test-model"
    dim = 4
    vec_v1 = [0.1, 0.2, 0.3, 0.4]
    vec_v2 = [0.9, 0.8, 0.7, 0.6]

    temp_cache.put(owner, text, model, dim, vec_v1, version=1)
    temp_cache.put(owner, text, model, dim, vec_v2, version=2)

    got_v1 = temp_cache.get(owner, text, model, dim, version=1)
    got_v2 = temp_cache.get(owner, text, model, dim, version=2)

    assert got_v1 is not None and got_v1[0] == pytest.approx(0.1, 0.001)
    assert got_v2 is not None and got_v2[0] == pytest.approx(0.9, 0.001)


def test_cache_v2_persistent_pruning(temp_store, temp_cache):
    owner = "tenant_test_prune"
    model = "test-model"
    dim = 2

    # Put 2 items with TTL=0.1
    temp_cache.put(owner, "item 1", model, dim, [1.0, 0.0], ttl=0.1)
    temp_cache.put(owner, "item 2", model, dim, [0.0, 1.0], ttl=0.1)
    time.sleep(0.2)

    pruned = temp_cache.prune_expired()
    assert pruned >= 2


# ── 5. Scalable Vector Engine V2 & AutoIndexStrategy ──────────────────────────

def test_scalable_vector_engine_auto_strategy():
    strategy = AutoIndexStrategy(AutoIndexConfig(approximate_threshold=200))

    # Small corpus selects exact
    st_small = strategy.evaluate_strategy(corpus_size=50)
    assert st_small.recommended_type == "exact"
    assert st_small.is_approximate is False

    # Large corpus selects native_ivf
    st_large = strategy.evaluate_strategy(corpus_size=1000)
    assert st_large.recommended_type == "native_ivf"
    assert st_large.is_approximate is True


def test_native_vector_index_search():
    dim = 8
    prov = get_default_embedding_provider()
    vectors = [
        (f"vec_{i}", prov.embed_query(f"Topic sentence number {i} regarding cloud infra"))
        for i in range(120)
    ]

    index = NativeVectorIndex(owner_id="tenant_native_idx", nlist=4)
    index.build(vectors)

    assert index.size() == 120
    assert index.index_type == "native_ivf"

    q_vec = prov.embed_query("Topic sentence number 10 regarding cloud infra")
    hits = index.search(query_vector=q_vec, top_k=5)
    assert len(hits) == 5
    # The exact or nearest vector should be rank 1
    assert hits[0][0] == "vec_10"
    assert hits[0][1] > 0.90


# ── 6. Maintenance Orchestration (8 Jobs) ─────────────────────────────────────

def test_maintenance_orchestration_all_jobs(temp_store):
    owner = "tenant_test_maint"
    manager = MemoryManager(store=temp_store)

    # 1. Backfill
    res1 = manager.run_maintenance("embedding_backfill", owner)
    assert res1["job"] == "embedding_backfill"

    # 2. Repair
    res2 = manager.run_maintenance("stale_embedding_repair", owner)
    assert res2["job"] == "stale_embedding_repair"

    # 3. Duplicate detection
    res3 = manager.run_maintenance("duplicate_detection", owner)
    assert res3["job"] == "duplicate_detection"

    # 4. Index rebuild
    res4 = manager.run_maintenance("index_rebuild", owner)
    assert res4["job"] == "index_rebuild"

    # 5. Contradiction scan
    res5 = manager.run_maintenance("contradiction_scan", owner)
    assert res5["job"] == "contradiction_scan"

    # 6. Consolidation scan
    res6 = manager.run_maintenance("consolidation_scan", owner)
    assert res6["job"] == "consolidation_scan"

    # 7. Cache prune
    res7 = manager.run_maintenance("cache_prune_expired", owner)
    assert res7["job"] == "cache_prune_expired"

    # 8. Memory lifecycle cleanup
    res8 = manager.run_maintenance("memory_lifecycle_cleanup", owner)
    assert res8["job"] == "memory_lifecycle_cleanup"


# ── 7. Retrieval Quality V3: 52 Fixtures ───────────────────────────────────────

def test_retrieval_quality_52_fixtures_evaluation(temp_store):
    eval_res = run_retrieval_benchmark(store=temp_store, eval_owner_id="tenant_eval_suite")
    assert eval_res["total_test_cases"] == 52
    assert eval_res["recall_at_5"] >= 0.85
    assert eval_res["mrr"] >= 0.80
    assert eval_res["ndcg_at_5"] >= 0.80
    assert eval_res["prompt_injection_safety_verified"] is True
    assert eval_res["all_thresholds_met"] is True


# ── 8. Memory Health Report ───────────────────────────────────────────────────

def test_memory_health_endpoint_contract(temp_store):
    manager = MemoryManager(store=temp_store)
    owner = "tenant_test_health"

    # Create active and stale memories
    m1 = MemoryEntry(id="m_act_1", owner_id=owner, title="A", content="Active content")
    m2 = MemoryEntry(id="m_stl_1", owner_id=owner, title="S", content="Stale content", status=MemoryStatus.STALE.value)
    temp_store.create_memory(m1)
    temp_store.create_memory(m2)

    health = manager.get_memory_health(owner)
    assert health["status"] == "healthy"
    assert health["memory_counts"]["active"] == 1
    assert health["memory_counts"]["stale"] == 1
    assert health["memory_counts"]["total"] == 2
    assert "vector_index" in health
    assert "intelligence" in health
    assert "cache" in health
    assert "telemetry" in health
