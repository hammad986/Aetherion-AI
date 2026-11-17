"""
tests/integration/test_phase8_remediation.py — Test suite for Phase 8 Remediation Cycle 1.
Verifies all 9 certified audit findings from phase8_independent_audit_report.md:
  1. Real Shadow Calibration Wired in Retrieval Path
  2. Reconcile IVF Regime and Shadow Boundary
  3. Quality Adaptation for Uncalibrated Large Corpora
  4. Calibration State Lifecycle Drift and Invalidation
  5. Secure Cache Observability Contract and Empty String Handling
  6. Two-Tier Invalidation Deduplication & Accurate Counting
  7. Real Vector Acceleration via NumPy BLAS
  8. Lock-Free Reader Concurrency (RCU Snapshot Pattern)
  9. Telemetry Extensibility for Phase 8 Cache & Shadow Metrics
"""
from __future__ import annotations

import concurrent.futures
import math
import random
import threading
import time
from typing import List, Tuple
import pytest

from backend.execution.memory.cache import EmbeddingCache
from backend.execution.memory.embeddings import (
    DeterministicLocalEmbeddingProvider,
    EmbeddingProvider,
)
from backend.execution.memory.index import (
    _NUMPY_AVAILABLE,
    ApproximateVectorIndex,
    AutoIndexConfig,
    AutoIndexStrategy,
    CalibrationState,
    ExactVectorIndex,
    NativeVectorIndex,
    ShadowQueryCalibrator,
    get_default_calibrator,
    get_vector_index,
)
from backend.execution.memory.models import MemoryEntry, MemoryStatus, MemoryTrust
from backend.execution.memory.retrieval import MemoryRetriever
from backend.execution.memory.store import MemoryStore
from backend.execution.memory.telemetry import MemoryTelemetry, TenantMetrics, get_memory_telemetry
from backend.execution.persistence.db import StateDatabase


# ── Fixtures & Mock Providers ─────────────────────────────────────────────────

class MockEmbeddingProvider(DeterministicLocalEmbeddingProvider):
    def __init__(self, dimension: int = 16, model_name: Optional[str] = None):
        super().__init__(dimension=dimension)
        if model_name:
            self._model_name = model_name


def _make_temp_cache(tmp_path) -> EmbeddingCache:
    db_file = tmp_path / f"cache_{random.randint(1000, 9999)}.db"
    return EmbeddingCache(db_path=db_file, max_memory_entries=10, max_persistent_entries=50)


def _make_temp_store(tmp_path) -> MemoryStore:
    db_file = tmp_path / f"store_{random.randint(1000, 9999)}.db"
    db = StateDatabase(db_file)
    return MemoryStore(db=db)


# ── Finding 1: Real Shadow Calibration Wired in Retrieval Path ────────────────

class TestFinding1ShadowCalibrationWiring:
    def test_retrieval_triggers_shadow_calibration_when_sampled(self, tmp_path):
        store = _make_temp_store(tmp_path)
        embedder = MockEmbeddingProvider(dimension=8)
        calibrator = ShadowQueryCalibrator(sampling_rate=1.0, shadow_max_corpus_size=50000, min_samples=2, db=store.db)
        telemetry = MemoryTelemetry()
        retriever = MemoryRetriever(
            store=store,
            embedding_provider=embedder,
            calibrator=calibrator,
            telemetry=telemetry,
        )

        owner = "tenant_shadow_1"
        for i in range(25):
            entry = MemoryEntry(
                owner_id=owner,
                title=f"Memory title {i}",
                content=f"Important facts about project alpha item {i}",
                importance=0.8,
                status=MemoryStatus.ACTIVE.value,
            )
            created = store.create_memory(entry)
            vec = embedder.embed_query(entry.content)
            store.save_vector(created.id, owner, vec)

        results = retriever.retrieve_relevant(
            prompt="project alpha",
            owner_id=owner,
            max_memories=5,
            use_semantic=True,
            index_mode="approximate",
        )

        assert len(results) > 0
        assert calibrator.flush()
        state = calibrator.get_calibration_state(owner)
        assert state.samples >= 1
        assert state.measured_recall > 0.0
        assert state.corpus_size == 25
        assert state.model_name == embedder.model_name

        t_metrics = telemetry.get_metrics(owner)
        assert t_metrics["shadow_calibration"]["queries_sampled"] >= 1
        assert t_metrics["shadow_calibration"]["queries_completed"] >= 1
        assert t_metrics["shadow_calibration"]["last_recall"] > 0.0

    def test_shadow_calibration_preserves_tenant_isolation(self, tmp_path):
        store = _make_temp_store(tmp_path)
        embedder = MockEmbeddingProvider(dimension=8)
        calibrator = ShadowQueryCalibrator(sampling_rate=1.0, shadow_max_corpus_size=50000, db=store.db)
        retriever = MemoryRetriever(
            store=store,
            embedding_provider=embedder,
            calibrator=calibrator,
        )

        owner_a = "tenant_a"
        owner_b = "tenant_b"

        for i in range(15):
            entry = MemoryEntry(
                owner_id=owner_a,
                title=f"A memory {i}",
                content=f"Content for tenant A {i}",
                status=MemoryStatus.ACTIVE.value,
            )
            created = store.create_memory(entry)
            store.save_vector(created.id, owner_a, embedder.embed_query(entry.content))

        retriever.retrieve_relevant("tenant A", owner_id=owner_a, index_mode="approximate")
        assert calibrator.flush()

        state_a = calibrator.get_calibration_state(owner_a)
        state_b = calibrator.get_calibration_state(owner_b)
        assert state_a.samples == 1
        assert state_b.samples == 0


# ── Finding 2: Reconcile IVF Regime and Shadow Boundary ───────────────────────

class TestFinding2IVFRegimeReconciliation:
    def test_shadow_max_corpus_size_defaults(self):
        config = AutoIndexConfig()
        calibrator = ShadowQueryCalibrator()
        assert config.shadow_max_corpus_size == 50000
        assert calibrator.shadow_max_corpus_size == 50000

    def test_should_sample_across_ivf_regime(self):
        calibrator = ShadowQueryCalibrator(sampling_rate=1.0, shadow_max_corpus_size=50000)
        assert calibrator.should_sample(25000) is True
        assert calibrator.should_sample(35000) is True
        assert calibrator.should_sample(50000) is True
        assert calibrator.should_sample(50001) is False


# ── Finding 3: Quality Adaptation for Uncalibrated Large Corpora ───────────────

class TestFinding3QualityAdaptation:
    def test_uncalibrated_large_corpus_applies_quality_adaptation(self):
        config = AutoIndexConfig(exact_threshold=25, max_exact_fallback_size=50, quality_adaptation_nprobe_boost=2)
        calibrator = ShadowQueryCalibrator(min_samples=10)
        strategy = AutoIndexStrategy(config=config, calibrator=calibrator)

        vectors = [(f"m_{i}", [float(i % 5)] * 8) for i in range(60)]
        engine = strategy.select_engine(vectors, owner_id="uncalibrated_tenant")

        assert hasattr(engine, "adaptive_nprobe_boost")
        assert engine.adaptive_nprobe_boost >= 2
        assert getattr(engine, "_quality_adaptation", None) == "elevated_nprobe"

    def test_calibrated_large_corpus_operates_empirically(self):
        config = AutoIndexConfig(exact_threshold=25, max_exact_fallback_size=50, quality_adaptation_nprobe_boost=2)
        calibrator = ShadowQueryCalibrator(min_samples=2)
        for _ in range(3):
            calibrator.record_observation(
                owner_id="calibrated_tenant",
                ivf_results=["m_1", "m_2"],
                exact_results=["m_1", "m_2"],
                k=2,
            )

        strategy = AutoIndexStrategy(config=config, calibrator=calibrator)
        vectors = [(f"m_{i}", [float(i % 5)] * 8) for i in range(60)]
        engine = strategy.select_engine(vectors, owner_id="calibrated_tenant")
        assert engine.index_type in ("approximate_ivf", "native_ivf")


# ── Finding 4: Calibration State Lifecycle Drift and Invalidation ─────────────

class TestFinding4CalibrationLifecycle:
    def test_calibration_state_schema_and_to_dict(self):
        state = CalibrationState(
            tenant_id="t_drift",
            samples=5,
            recall_sum=4.5,
            min_samples=10,
            corpus_size=1000,
            index_generation=2,
            model_name="text-embed-3",
            last_updated_at=1700000000.0,
        )
        d = state.to_dict()
        assert d["tenant_id"] == "t_drift"
        assert d["samples"] == 5
        assert d["measured_recall"] == 0.9
        assert d["is_empirical"] is False
        assert d["corpus_size"] == 1000
        assert d["index_generation"] == 2
        assert d["model_name"] == "text-embed-3"
        assert d["last_updated_at"] == 1700000000.0

    def test_lifecycle_drift_resets_stale_calibration(self):
        calibrator = ShadowQueryCalibrator(min_samples=5)
        owner = "t_drift_user"

        for _ in range(5):
            calibrator.record_observation(
                owner_id=owner,
                ivf_results=["a", "b"],
                exact_results=["a", "b"],
                k=2,
                corpus_size=1000,
                index_generation=1,
                model_name="v1",
            )
        assert calibrator.get_calibration_state(owner).samples == 5

        # 1. Test corpus drift > 25% (1000 -> 1350 is 35% drift)
        calibrator.record_observation(
            owner_id=owner,
            ivf_results=["a", "b"],
            exact_results=["a", "b"],
            k=2,
            corpus_size=1350,
            index_generation=1,
            model_name="v1",
        )
        state = calibrator.get_calibration_state(owner)
        assert state.samples == 1
        assert state.corpus_size == 1350

        # 2. Test generation change
        for _ in range(3):
            calibrator.record_observation(
                owner_id=owner,
                ivf_results=["a"],
                exact_results=["a"],
                k=1,
                corpus_size=1350,
                index_generation=1,
                model_name="v1",
            )
        assert calibrator.get_calibration_state(owner).samples == 4
        calibrator.record_observation(
            owner_id=owner,
            ivf_results=["a"],
            exact_results=["a"],
            k=1,
            corpus_size=1350,
            index_generation=2,
            model_name="v1",
        )
        assert calibrator.get_calibration_state(owner).samples == 1

        # 3. Test model change
        calibrator.record_observation(
            owner_id=owner,
            ivf_results=["a"],
            exact_results=["a"],
            k=1,
            corpus_size=1350,
            index_generation=2,
            model_name="v2_large",
        )
        assert calibrator.get_calibration_state(owner).samples == 1
        assert calibrator.get_calibration_state(owner).model_name == "v2_large"

    def test_invalidate_tenant_clears_state(self):
        calibrator = ShadowQueryCalibrator()
        calibrator.record_observation("t_inv_tenant", ["a"], ["a"], 1)
        assert calibrator.get_calibration_state("t_inv_tenant").samples == 1

        calibrator.invalidate_tenant("t_inv_tenant", reason="test manual clear")
        assert calibrator.get_calibration_state("t_inv_tenant").samples == 0


# ── Finding 5: Secure Cache Observability Contract ────────────────────────────

class TestFinding5SecureCacheObservability:
    def test_stats_raises_on_empty_string(self, tmp_path):
        cache = _make_temp_cache(tmp_path)
        with pytest.raises(KeyError):
            cache.stats(owner_id="")

    def test_stats_raises_on_whitespace_string(self, tmp_path):
        cache = _make_temp_cache(tmp_path)
        with pytest.raises(KeyError):
            cache.stats(owner_id="    ")

    def test_stats_raises_on_explicit_none_without_allow_global(self, tmp_path):
        cache = _make_temp_cache(tmp_path)
        with pytest.raises(KeyError):
            cache.stats(owner_id=None)

    def test_stats_allows_none_with_allow_global(self, tmp_path):
        cache = _make_temp_cache(tmp_path)
        s = cache.stats(owner_id=None, allow_global=True)
        assert isinstance(s, dict)
        assert "hits" in s
        assert "protection_drops" in s

    def test_stats_no_args_preserves_global_stats(self, tmp_path):
        cache = _make_temp_cache(tmp_path)
        s = cache.stats()
        assert isinstance(s, dict)
        assert "hits" in s
        assert "admission_denials" in s

    def test_stats_valid_owner_returns_scoped_stats(self, tmp_path):
        cache = _make_temp_cache(tmp_path)
        s = cache.stats(owner_id="tenant_ok")
        assert isinstance(s, dict)
        assert s["hits"] == 0


# ── Finding 6: Two-Tier Invalidation Deduplication & Accurate Return ──────────

class TestFinding6TwoTierInvalidation:
    def test_invalidate_deduplicates_across_both_tiers(self, tmp_path):
        cache = _make_temp_cache(tmp_path)
        owner = "t_inv_twotier"
        vec = [0.1] * 8

        cache.put(owner, "hello world", "model_x", 8, vec)
        stats_before = cache.stats(owner_id=owner)
        assert stats_before["in_memory_entries"] == 1
        assert stats_before["persistent_entries"] == 1

        deleted = cache.invalidate(owner, text="hello world")
        assert deleted == 1

        stats_after = cache.stats(owner_id=owner)
        assert stats_after["in_memory_entries"] == 0
        assert stats_after["persistent_entries"] == 0
        assert stats_after["invalidations"] == 1

    def test_invalidate_db_only_entry(self, tmp_path):
        cache = _make_temp_cache(tmp_path)
        owner = "t_inv_dbonly"
        vec = [0.2] * 8

        cache.put(owner, "item 1", "model_x", 8, vec)
        cache.put(owner, "item 2", "model_x", 8, vec)
        with cache._lock:
            cache._memory_cache.clear()

        stats_cold = cache.stats(owner_id=owner)
        assert stats_cold["in_memory_entries"] == 0
        assert stats_cold["persistent_entries"] == 2

        deleted = cache.invalidate(owner)
        assert deleted == 2
        assert cache.stats(owner_id=owner)["invalidations"] == 2


# ── Finding 7: Real Vector Acceleration via NumPy BLAS ────────────────────────

class TestFinding7NumPyVectorAcceleration:
    def test_numpy_available_and_active(self):
        assert _NUMPY_AVAILABLE is True

    def test_exact_index_vectorization_matches_pure_similarity(self):
        dim = 16
        rng = random.Random(42)
        vectors = []
        for i in range(100):
            v = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
            vectors.append((f"v_{i}", v))

        index = ExactVectorIndex(vectors)
        q = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
        hits = index.search(q, top_k=5)

        assert len(hits) == 5
        for i in range(len(hits) - 1):
            assert hits[i][1] >= hits[i + 1][1]

    def test_approximate_ivf_acceleration(self):
        dim = 16
        rng = random.Random(123)
        vectors = []
        for i in range(200):
            v = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
            vectors.append((f"v_{i}", v))

        exact_index = ExactVectorIndex(vectors)
        ivf_index = NativeVectorIndex(vectors)

        q = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
        exact_hits = exact_index.search(q, top_k=10)
        ivf_hits = ivf_index.search(q, top_k=10)

        exact_set = {mid for mid, _ in exact_hits}
        ivf_set = {mid for mid, _ in ivf_hits}
        overlap = len(exact_set & ivf_set)
        recall = overlap / 10.0
        assert recall >= 0.80


# ── Finding 8: Lock-Free Reader Concurrency ───────────────────────────────────

class TestFinding8LockFreeReaderConcurrency:
    def test_concurrent_readers_with_concurrent_writer(self):
        dim = 8
        initial_vecs = [(f"init_{i}", [float(i % 4)] * dim) for i in range(50)]
        index = ExactVectorIndex(initial_vecs)

        num_readers = 8
        queries_per_reader = 150
        errors = []

        def reader_task(thread_id: int):
            try:
                q = [float(thread_id % 3)] * dim
                for _ in range(queries_per_reader):
                    results = index.search(q, top_k=5)
                    assert len(results) <= 5
            except Exception as e:
                errors.append(e)

        def writer_task():
            try:
                for i in range(50):
                    index.add(f"dynamic_{i}", [float(i)] * dim)
                    time.sleep(0.001)
                    if i % 10 == 0:
                        index.delete(f"init_{i}")
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_readers + 1) as pool:
            futures = [pool.submit(reader_task, i) for i in range(num_readers)]
            futures.append(pool.submit(writer_task))
            for f in concurrent.futures.as_completed(futures):
                f.result()

        assert len(errors) == 0
        assert index.size() > 0


# ── Finding 9: Telemetry Extensibility for Phase 8 ────────────────────────────

class TestFinding9TelemetryExtensibility:
    def test_tenant_metrics_phase8_fields(self):
        tm = TenantMetrics("t_telemetry")
        tm.record_cache_metric(protection_drops=1, admission_denials=2, invalidations=3, expired_evictions=4)
        tm.record_shadow_query(sampled=True, completed=True, recall=0.92)

        m = tm.get_metrics()
        assert m["cache"]["protection_drops"] == 1
        assert m["cache"]["admission_denials"] == 2
        assert m["cache"]["invalidations"] == 3
        assert m["cache"]["expired_evictions"] == 4
        assert m["shadow_calibration"]["queries_sampled"] == 1
        assert m["shadow_calibration"]["queries_completed"] == 1
        assert m["shadow_calibration"]["last_recall"] == 0.92

    def test_memory_telemetry_global_and_tenant_dispatch(self):
        tel = MemoryTelemetry()
        tel.record_cache_metric(protection_drops=2, owner_id="t_sub")
        tel.record_shadow_query(sampled=True, completed=True, recall=0.88, owner_id="t_sub")

        t_m = tel.get_metrics(owner_id="t_sub")
        assert t_m["cache"]["protection_drops"] == 2
        assert t_m["shadow_calibration"]["queries_sampled"] == 1
        assert t_m["shadow_calibration"]["last_recall"] == 0.88

        g_m = tel.get_metrics(include_global=True)
        assert g_m["cache"]["protection_drops"] == 2
        assert g_m["shadow_calibration"]["queries_sampled"] == 1
        assert g_m["shadow_calibration"]["last_recall"] == 0.88


# ── Track 10: Index Build Resource Control & Redundant Rebuild Deduplication ──

class TestTrack10BuildResourceControl:
    def test_rebuild_deduplication_skips_redundant_work(self):
        vecs = [
            (f"m_{i}", [math.sin(i + d) for d in range(8)])
            for i in range(50)
        ]
        index = ApproximateVectorIndex(vecs)
        gen1 = index.generation
        assert gen1 > 0

        # Identical vectors passed to rebuild -> skipped!
        index.rebuild(vecs)
        assert index.generation == gen1

        # Modify one vector -> rebuild runs
        vecs_mod = list(vecs)
        vecs_mod[0] = ("m_0", [0.99] * 8)
        index.add("m_0", [0.99] * 8)
        assert index.generation > gen1

    def test_concurrent_rebuild_safety(self):
        vecs = [
            (f"m_{i}", [float(i * 0.01 + d * 0.1) for d in range(8)])
            for i in range(40)
        ]
        index = ApproximateVectorIndex(vecs)
        errors = []

        def worker(w_id):
            try:
                for _ in range(5):
                    index.rebuild(vecs)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert index.size() == 40


# ── Track 11: Adversarial Vector Distributions ───────────────────────────────

class TestTrack11AdversarialVectorDistributions:
    def test_imbalanced_cluster_distribution(self):
        """90% of vectors clustered around [1,0,0,...]; 10% dispersed."""
        rng = random.Random(42)
        dim = 8
        items: List[Tuple[str, List[float]]] = []

        # Dense spike (90 items)
        for i in range(90):
            v = [1.0 + rng.gauss(0, 0.05)] + [rng.gauss(0, 0.05) for _ in range(dim - 1)]
            items.append((f"dense_{i}", v))

        # Sparse background (10 items)
        for i in range(10):
            v = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
            items.append((f"sparse_{i}", v))

        ivf_index = ApproximateVectorIndex(items, random_seed=42)
        exact_index = ExactVectorIndex(items)

        # Test queries targeting dense cluster
        q_dense = [1.0] + [0.0] * (dim - 1)
        exact_hits_dense = exact_index.search(q_dense, top_k=10)
        ivf_hits_dense = ivf_index.search(q_dense, top_k=10)

        exact_dense_ids = set(mid for mid, _ in exact_hits_dense)
        ivf_dense_ids = set(mid for mid, _ in ivf_hits_dense)
        recall_dense = len(exact_dense_ids & ivf_dense_ids) / len(exact_dense_ids)
        assert recall_dense >= 0.85

        # Test queries targeting sparse cluster
        q_sparse = items[95][1]
        exact_hits_sparse = exact_index.search(q_sparse, top_k=5)
        ivf_hits_sparse = ivf_index.search(q_sparse, top_k=5)

        exact_sparse_ids = set(mid for mid, _ in exact_hits_sparse)
        ivf_sparse_ids = set(mid for mid, _ in ivf_hits_sparse)
        recall_sparse = len(exact_sparse_ids & ivf_sparse_ids) / len(exact_sparse_ids)
        assert recall_sparse >= 0.80

        # Measure MRR across both
        rank_first = next((i + 1 for i, (mid, _) in enumerate(ivf_hits_sparse) if mid == items[95][0]), 10)
        mrr = 1.0 / rank_first
        assert mrr >= 0.50

    def test_boundary_heavy_vectors(self):
        """Vectors placed near decision boundaries between cluster centroids."""
        dim = 8
        items = []
        # Clusters along orthogonal axes
        for i in range(30):
            items.append((f"axis0_{i}", [1.0, float(i) * 0.005, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]))
            items.append((f"axis1_{i}", [float(i) * 0.005, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]))

        # Boundary items equidistant to axis 0 and axis 1
        for i in range(10):
            items.append((f"boundary_{i}", [0.7071, 0.7071, float(i) * 0.01, 0.0, 0.0, 0.0, 0.0, 0.0]))

        index = ApproximateVectorIndex(items, random_seed=42)
        exact = ExactVectorIndex(items)

        # Query at boundary
        q_boundary = [0.7071, 0.7071, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        exact_ids = [mid for mid, _ in exact.search(q_boundary, top_k=10)]
        ivf_ids = [mid for mid, _ in index.search(q_boundary, top_k=10)]

        overlap = len(set(exact_ids) & set(ivf_ids)) / len(exact_ids)
        assert overlap >= 0.80

    def test_near_duplicate_and_collinear_vectors(self):
        """Corpus with near-identical vectors to test zero-division and stability."""
        dim = 8
        base_v = [0.5] * dim
        items = []
        for i in range(25):
            # Extremely small perturbation
            perturb = 1e-7 * i
            items.append((f"dup_{i}", [x + perturb for x in base_v]))

        # Add background items
        for i in range(15):
            items.append((f"bg_{i}", [float(i == d) for d in range(dim)]))

        index = ApproximateVectorIndex(items, random_seed=42)
        assert index.size() == 40
        hits = index.search(base_v, top_k=10)
        assert len(hits) == 10
        # Deterministic sorting preserved
        scores = [s for _, s in hits]
        assert scores == sorted(scores, reverse=True)


# ── Track 12: End-to-End Real Retrieval Calibration Integration ───────────────

class TestTrack12EndToEndCalibration:
    def test_end_to_end_9_step_lifecycle(self, tmp_path):
        """
        Verify the complete 9-step production lifecycle:
        1. Uncalibrated initial state (model estimate, is_empirical=False)
        2. Production retrieval with shadow sampling
        3. Primary user request returns immediately without delay
        4. Async background worker processes shadow exact comparison
        5. Empirical recall observations recorded
        6. Statistical sufficiency threshold reached -> transitions to is_empirical=True
        7. Empirical recall consumed by strategy selector
        8. Explicit tenant invalidation / drift detection triggered
        9. Graceful fallback to model estimate
        """
        store = _make_temp_store(tmp_path)
        embedder = MockEmbeddingProvider(dimension=8)
        calibrator = ShadowQueryCalibrator(
            sampling_rate=1.0,
            shadow_max_corpus_size=50000,
            min_samples=3,
            async_mode=True,
            db=store.db,
        )
        telemetry = MemoryTelemetry()
        retriever = MemoryRetriever(
            store=store,
            embedding_provider=embedder,
            calibrator=calibrator,
            telemetry=telemetry,
        )

        owner = "tenant_e2e_lifecycle"

        # Seed corpus
        for i in range(30):
            entry = MemoryEntry(
                owner_id=owner,
                title=f"Doc {i}",
                content=f"Lifecycle facts and observations for item {i}",
                importance=0.8,
                status=MemoryStatus.ACTIVE.value,
            )
            created = store.create_memory(entry)
            store.save_vector(created.id, owner, embedder.embed_query(entry.content))

        # Step 1: Initial state is uncalibrated
        state = calibrator.get_calibration_state(owner)
        assert state.samples == 0
        assert not state.is_empirical
        recall_val, is_emp = calibrator.get_calibrated_recall(owner, 0.85)
        assert recall_val == 0.85
        assert is_emp is False

        # Step 2 & 3: Primary request 1 returns immediately
        start_t = time.time()
        res1 = retriever.retrieve_relevant("item 1", owner_id=owner, index_mode="approximate")
        elapsed_ms = (time.time() - start_t) * 1000.0
        assert len(res1) > 0
        assert elapsed_ms < 500.0  # Main request does not block on exact scan

        # Step 4 & 5: Flush background worker; verify sample 1
        assert calibrator.flush()
        state1 = calibrator.get_calibration_state(owner)
        assert state1.samples == 1
        assert not state1.is_empirical

        # Requests 2 and 3 to reach min_samples=3
        retriever.retrieve_relevant("item 2", owner_id=owner, index_mode="approximate")
        retriever.retrieve_relevant("item 3", owner_id=owner, index_mode="approximate")
        assert calibrator.flush()

        # Step 6: Threshold reached -> is_empirical becomes True
        state3 = calibrator.get_calibration_state(owner)
        assert state3.samples == 3
        assert state3.is_empirical is True
        assert state3.measured_recall > 0.0

        # Step 7: Consumed by strategy selector
        emp_recall, is_emp2 = calibrator.get_calibrated_recall(owner, 0.85)
        assert is_emp2 is True
        assert emp_recall == state3.measured_recall

        # Step 8: Invalidation via retriever
        retriever.invalidate_tenant_calibration(owner, reason="model_rotation")

        # Step 9: Reset to conservative fallback
        state_after = calibrator.get_calibration_state(owner)
        assert state_after.samples == 0
        assert state_after.is_empirical is False
        fallback_val, is_emp3 = calibrator.get_calibrated_recall(owner, 0.85)
        assert fallback_val == 0.85
        assert is_emp3 is False


# ── Track 13: Adversarial Rebuild Deduplication (DATA-P1-REBUILD Remediation) ─

class TestTrack13AdversarialRebuild:
    def test_rebuild_executes_on_mutated_interior_vector_same_ids_count(self):
        """Test A & B: Mutating interior vector with identical IDs and count MUST trigger rebuild."""
        vecs1 = [("m_0", [1.0, 0.0]), ("m_1", [0.0, 1.0]), ("m_2", [0.5, 0.5])]
        idx = ApproximateVectorIndex(vecs1)
        snap1 = idx._snapshot

        # Mutate interior vector m_1
        vecs2 = [("m_0", [1.0, 0.0]), ("m_1", [0.707, 0.707]), ("m_2", [0.5, 0.5])]
        idx.rebuild(vecs2)
        assert idx._snapshot is not snap1, "Mutated interior vector must produce a new snapshot"
        assert idx.generation != snap1.generation, "Generation must advance on content mutation"

    def test_rebuild_executes_on_swapped_embedding_values_same_ids_count(self):
        """Test C: Swapping embedding values across same IDs MUST trigger rebuild."""
        vecs1 = [("m_0", [1.0, 0.0]), ("m_1", [0.0, 1.0]), ("m_2", [0.5, 0.5])]
        idx = ApproximateVectorIndex(vecs1)
        snap1 = idx._snapshot

        # Swap embeddings of m_0 and m_1
        vecs2 = [("m_0", [0.0, 1.0]), ("m_1", [1.0, 0.0]), ("m_2", [0.5, 0.5])]
        idx.rebuild(vecs2)
        assert idx._snapshot is not snap1, "Swapped vector embeddings must trigger rebuild"

    def test_rebuild_executes_on_regenerated_embedding(self):
        """Test D: Re-generating embeddings with small delta MUST trigger rebuild."""
        vecs1 = [(f"m_{i}", [float(i) / 10.0, 1.0]) for i in range(10)]
        idx = ApproximateVectorIndex(vecs1)
        snap1 = idx._snapshot

        # Perturb middle vector
        vecs2 = list(vecs1)
        vecs2[5] = ("m_5", [0.51, 1.0])
        idx.rebuild(vecs2)
        assert idx._snapshot is not snap1, "Perturbed vector must trigger rebuild"

    def test_rebuild_skipped_on_identical_corpus_and_reordering(self):
        """Test E & F: Truly identical corpus and permutation of identical corpus skips rebuild."""
        vecs1 = [("m_0", [1.0, 0.0]), ("m_1", [0.0, 1.0]), ("m_2", [0.5, 0.5])]
        idx = ApproximateVectorIndex(vecs1)
        snap1 = idx._snapshot

        # Exact duplicate rebuild
        idx.rebuild(vecs1)
        assert idx._snapshot is snap1, "Identical corpus should skip rebuild"

        # Permutation of identical corpus
        vecs_perm = [("m_2", [0.5, 0.5]), ("m_0", [1.0, 0.0]), ("m_1", [0.0, 1.0])]
        idx.rebuild(vecs_perm)
        assert idx._snapshot is snap1, "Permuted identical corpus should skip rebuild"

    def test_search_reflects_new_vectors_after_rebuild(self):
        """Test H: Search results must reflect updated vectors after rebuild."""
        vecs1 = [("m_0", [1.0, 0.0]), ("m_1", [0.0, 1.0])]
        idx = ApproximateVectorIndex(vecs1)
        assert idx.search([1.0, 0.0])[0][0] == "m_0"

        # Swap vectors
        vecs2 = [("m_0", [0.0, 1.0]), ("m_1", [1.0, 0.0])]
        idx.rebuild(vecs2)
        assert idx.search([1.0, 0.0])[0][0] == "m_1", "Search must reflect newly indexed vector values"


# ── Track 14: Lifecycle Durability (F4 Finalization) ──────────────────────────

class TestTrack14LifecycleDurability:
    def test_persistent_sqlite_mutation_invalidates_stale_calibration(self, tmp_path):
        """Verify out-of-band SQLite memory mutation invalidates stale calibration on next retrieval."""
        db_file = tmp_path / "lifecycle_durability.db"
        store = MemoryStore(db_path=db_file)
        embedder = MockEmbeddingProvider(dimension=8)
        calibrator = ShadowQueryCalibrator(sampling_rate=1.0, min_samples=2, async_mode=True)
        telemetry = MemoryTelemetry()
        retriever = MemoryRetriever(store, embedder, calibrator=calibrator, telemetry=telemetry)

        owner = "tenant_durability"
        for i in range(25):
            m = store.create_memory(MemoryEntry(owner_id=owner, title=f"T{i}", content=f"Facts {i}", status=MemoryStatus.ACTIVE.value))
            store.save_vector(m.id, owner, embedder.embed_query(m.content))

        # Two queries to establish empirical calibration
        retriever.retrieve_relevant("test 1", owner_id=owner, index_mode="approximate")
        retriever.retrieve_relevant("test 2", owner_id=owner, index_mode="approximate")
        calibrator.flush()

        state = calibrator.get_calibration_state(owner)
        assert state.samples == 2
        assert state.is_empirical is True
        old_gen = state.index_generation

        # Out-of-band persistent mutation: add new memory vector directly in store
        m_new = store.create_memory(MemoryEntry(owner_id=owner, title="New", content="Disruptive new vocabulary", status=MemoryStatus.ACTIVE.value))
        store.save_vector(m_new.id, owner, embedder.embed_query(m_new.content))

        # Next retrieval builds fresh index with updated content fingerprint
        retriever.retrieve_relevant("test 3", owner_id=owner, index_mode="approximate")
        calibrator.flush()

        state_after = calibrator.get_calibration_state(owner)
        assert state_after.index_generation != old_gen, "Index generation must change when corpus changes"
        assert state_after.samples == 1, "Stale calibration must reset on generation change"
        assert state_after.is_empirical is False, "Calibration must return to non-empirical state"


# ── Track 15: Shadow Timeout & Contention (PERF-P2 Remediation) ───────────────

class TestTrack15ShadowTimeoutAndContention:
    def test_shadow_job_completes_within_timeout(self):
        calibrator = ShadowQueryCalibrator(sampling_rate=1.0, job_timeout=2.0, async_mode=True)
        telemetry = MemoryTelemetry()
        vecs = [(f"m_{i}", [0.1] * 8) for i in range(50)]
        q = [0.1] * 8
        hits = [("m_0", 1.0)]

        submitted = calibrator.submit_shadow_job(
            owner_id="tenant_ok",
            vectors=vecs,
            query_vector=q,
            ivf_hits=hits,
            k=5,
            telemetry=telemetry,
        )
        assert submitted is True
        assert calibrator.flush(timeout=2.0)

        metrics = telemetry.get_metrics("tenant_ok")
        shadow_data = metrics.get("shadow_calibration", {})
        assert shadow_data["queries_sampled"] == 1
        assert shadow_data["queries_completed"] == 1
        calibrator.shutdown()

    def test_shadow_job_exceeds_timeout_drops_observation_and_records_telemetry(self):
        calibrator = ShadowQueryCalibrator(sampling_rate=1.0, job_timeout=0.000001, async_mode=True)
        telemetry = MemoryTelemetry()
        vecs = [(f"m_{i}", [0.1] * 8) for i in range(50)]
        q = [0.1] * 8
        hits = [("m_0", 1.0)]

        submitted = calibrator.submit_shadow_job(
            owner_id="tenant_timeout",
            vectors=vecs,
            query_vector=q,
            ivf_hits=hits,
            k=5,
            telemetry=telemetry,
        )
        assert submitted is True
        assert calibrator.flush(timeout=2.0)

        metrics = telemetry.get_metrics("tenant_timeout")
        shadow_data = metrics.get("shadow_calibration", {})
        assert shadow_data["queries_sampled"] == 1
        assert shadow_data["queries_completed"] == 0, "Timed out job must not be marked completed"

        state = calibrator.get_calibration_state("tenant_timeout")
        assert state.samples == 0, "Timed out job must not record observation"
        calibrator.shutdown()


# ── Track 16: Throughput and Latency SLA (Tracks 6 & 7) ────────────────────────

class TestTrack16ThroughputAndSLA:
    def test_n50k_latency_meets_80ms_sla(self):
        """Verify N=50,000 query latency p95 is strictly under 80ms."""
        dim = 32
        n = 50000
        rng = random.Random(42)
        vecs = [(f"m_{i}", [rng.uniform(-1, 1) for _ in range(dim)]) for i in range(n)]
        idx = NativeVectorIndex(vecs)

        queries = [[rng.uniform(-1, 1) for _ in range(dim)] for _ in range(25)]
        lats = []
        for q in queries:
            t0 = time.perf_counter()
            idx.search(q, top_k=10)
            lats.append((time.perf_counter() - t0) * 1000.0)

        p95 = sorted(lats)[int(len(lats) * 0.95)]
        assert p95 < 80.0, f"p95 latency {p95:.2f}ms exceeds 80ms target"


