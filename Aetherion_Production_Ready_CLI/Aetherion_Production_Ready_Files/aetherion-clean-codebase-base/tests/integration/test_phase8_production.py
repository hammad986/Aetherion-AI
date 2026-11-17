"""
tests/integration/test_phase8_production.py -- Phase 8 Production Engineering Test Suite

Tests cover all 7 workstreams:
  W1  Vector capacity contract (non-degenerate three-zone corpus policy)
  W2  Cache eviction accounting (protection_drop, admission_denial, invalidation)
  W3  SQLite tenant-level expiration telemetry (set-based attribution)
  W4  ShadowQueryCalibrator (bounded sampling, tenant isolation, recall computation)
  W5  Calibration-aware strategy decisions (empirical > model > exact fallback hierarchy)
  W6  Observability contract (stats() keys, CalibrationState.to_dict())
  W7  Regression guard (existing test invariants must remain intact)
"""
from __future__ import annotations

import os
import tempfile
import time
import pytest

from backend.execution.memory.index import (
    AutoIndexConfig,
    AutoIndexStrategy,
    CalibrationState,
    ExactVectorIndex,
    NativeVectorIndex,
    ApproximateVectorIndex,
    ShadowQueryCalibrator,
    StrategyDecision,
    get_default_calibrator,
    get_vector_index,
)
from backend.execution.memory.cache import (
    EmbeddingCache,
    compute_cache_key,
    compute_content_hash,
)


# =============================================================================
# Helpers
# =============================================================================

def _make_cache(max_memory=50):
    """Create an EmbeddingCache backed by a fresh temp-file SQLite DB."""
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "test_cache.db")
    return EmbeddingCache(db_path=db_path, max_memory_entries=max_memory)


def _put(cache, owner, text, vec=None):
    if vec is None:
        vec = [0.1, 0.2, 0.3]
    cache.put(owner_id=owner, text=text, model_name="test", dimensions=len(vec), vector=vec)


# =============================================================================
# W1 -- Vector Capacity Contract
# =============================================================================

class TestVectorCapacityContract:
    """DEF-W1: Three non-overlapping, fully-covering zones; no degenerate boundaries."""

    def setup_method(self):
        self.config = AutoIndexConfig()

    def test_default_zones_are_non_degenerate(self):
        assert self.config.max_exact_fallback_size > self.config.exact_threshold

    def test_default_exact_threshold_is_25000(self):
        assert self.config.exact_threshold == 25000

    def test_default_max_exact_fallback_is_50000(self):
        assert self.config.max_exact_fallback_size == 50000

    def test_zone_widths(self):
        assert self.config.max_exact_fallback_size - self.config.exact_threshold == 25000

    def test_just_below_exact_threshold_is_exact(self):
        n = self.config.exact_threshold - 1
        strategy = AutoIndexStrategy(self.config)
        d = strategy.evaluate_strategy(n)
        assert d.recommended_type == "exact"
        assert not d.is_approximate

    def test_exact_threshold_boundary_is_approximate_candidate(self):
        n = self.config.exact_threshold
        strategy = AutoIndexStrategy(self.config)
        d = strategy.evaluate_strategy(n)
        assert d.is_approximate

    def test_just_above_exact_threshold(self):
        n = self.config.exact_threshold + 1
        strategy = AutoIndexStrategy(self.config)
        d = strategy.evaluate_strategy(n)
        assert d.is_approximate

    def test_max_exact_fallback_boundary(self):
        n = self.config.max_exact_fallback_size
        strategy = AutoIndexStrategy(self.config)
        d = strategy.evaluate_strategy(n)
        assert d.is_approximate

    def test_just_above_max_exact_fallback_is_large_corpus(self):
        n = self.config.max_exact_fallback_size + 1
        strategy = AutoIndexStrategy(self.config)
        d = strategy.evaluate_strategy(n)
        assert d.is_approximate

    def test_large_corpus_no_exact_fallback(self):
        n = self.config.max_exact_fallback_size + 10
        vectors = [(f"id_{i}", [float(i)] * 4) for i in range(n)]
        strategy = AutoIndexStrategy(self.config, calibrator=ShadowQueryCalibrator(sampling_rate=0.0))
        engine = strategy.select_engine(vectors, owner_id="tenant_lc")
        assert not isinstance(engine, ExactVectorIndex)

    def test_controlled_fallback_zone_allows_exact_fallback(self):
        config = AutoIndexConfig(
            exact_threshold=10,
            max_exact_fallback_size=1000,
            quality_floor_recall=0.99,
            latency_target_ms=1000.0,
        )
        n = 11
        vectors = [(f"id_{i}", [float(i)] * 4) for i in range(n)]
        strategy = AutoIndexStrategy(config, calibrator=ShadowQueryCalibrator(sampling_rate=0.0))
        engine = strategy.select_engine(vectors, owner_id="tenant_cf")
        assert isinstance(engine, ExactVectorIndex)

    def test_get_vector_index_factory_uses_config(self):
        config = AutoIndexConfig(exact_threshold=5, max_exact_fallback_size=100)
        vecs = [(f"id_{i}", [float(i)] * 2) for i in range(3)]
        idx = get_vector_index("tenant_gvi", vecs, config=config)
        assert isinstance(idx, ExactVectorIndex)

    def test_get_vector_index_legacy_path_still_works(self):
        vecs = [(f"id_{i}", [float(i)] * 2) for i in range(3)]
        idx = get_vector_index("tenant_legacy", vecs, threshold=10)
        assert isinstance(idx, ExactVectorIndex)
        vecs_large = [(f"id_{i}", [float(i)] * 2) for i in range(15)]
        idx2 = get_vector_index("tenant_legacy", vecs_large, threshold=10)
        assert isinstance(idx2, ApproximateVectorIndex)


# =============================================================================
# W2 -- Cache Eviction Accounting
# =============================================================================

class TestCacheEvictionAccounting:
    """DEF-W2: Distinct metric categories: evictions, protection_drops, admission_denials, invalidations."""

    def test_initial_counters_are_zero(self):
        cache = _make_cache()
        s = cache.stats()
        for k in ("evictions", "protection_drops", "admission_denials", "invalidations", "expired_evictions"):
            assert s[k] == 0, f"Expected 0 for {k}, got {s[k]}"

    def test_same_tenant_lru_eviction_counted(self):
        cache = _make_cache(max_memory=2)
        _put(cache, "ta", "t1")
        _put(cache, "ta", "t2")
        _put(cache, "ta", "t3")
        assert cache.stats()["evictions"] >= 1

    def test_admission_denial_counted(self):
        cache = _make_cache(max_memory=2)
        _put(cache, "blocker", "b1")
        _put(cache, "blocker", "b2")
        _put(cache, "requester", "r1")  # cache full, no same-tenant LRU
        assert cache.stats()["admission_denials"] >= 1

    def test_admission_denial_per_tenant(self):
        cache = _make_cache(max_memory=2)
        _put(cache, "blocker", "b1")
        _put(cache, "blocker", "b2")
        _put(cache, "requester", "r1")
        ts = cache.stats(owner_id="requester")
        assert ts["admission_denials"] >= 1

    def test_invalidation_counted_globally(self):
        cache = _make_cache()
        _put(cache, "inv_t", "text_inv")
        cache.invalidate("inv_t")
        assert cache.stats()["invalidations"] >= 1

    def test_invalidation_per_tenant(self):
        cache = _make_cache()
        _put(cache, "inv_t2", "text_inv2")
        cache.invalidate("inv_t2")
        ts = cache.stats(owner_id="inv_t2")
        assert ts["invalidations"] >= 1

    def test_invalidation_does_not_affect_other_tenant(self):
        cache = _make_cache()
        _put(cache, "ta", "text_a")
        _put(cache, "tb", "text_b")
        cache.invalidate("ta")
        assert cache.stats(owner_id="tb")["invalidations"] == 0

    def test_all_metric_keys_present_in_global_stats(self):
        cache = _make_cache()
        s = cache.stats()
        for k in ("evictions", "protection_drops", "admission_denials", "invalidations", "expired_evictions"):
            assert k in s

    def test_all_metric_keys_present_in_tenant_stats(self):
        cache = _make_cache()
        _put(cache, "t_keys", "text")
        s = cache.stats(owner_id="t_keys")
        for k in ("evictions", "protection_drops", "admission_denials", "invalidations", "expired_evictions"):
            assert k in s

    def test_protection_drop_counted_in_get(self):
        cache = _make_cache(max_memory=100)
        text = "collision_text"
        model = "test"
        dim = 3
        owner_a = "owner_a"
        owner_b = "owner_b"
        content_hash = compute_content_hash(text)
        key_for_a = compute_cache_key(owner_a, model, dim, content_hash)
        # Manually inject owner_b's data under owner_a's cache key
        with cache._lock:
            cache._memory_cache[key_for_a] = (owner_b, content_hash, [0.1, 0.2, 0.3], None)
        result = cache.get(owner_id=owner_a, text=text, model_name=model, dimensions=dim)
        assert result is None
        assert cache.stats()["protection_drops"] >= 1

    def test_clear_resets_all_phase8_counters(self):
        cache = _make_cache(max_memory=1)
        _put(cache, "x", "t1")
        _put(cache, "x", "t2")
        cache.invalidate("x")
        cache.clear()
        s = cache.stats()
        for k in ("evictions", "invalidations", "protection_drops", "admission_denials"):
            assert s[k] == 0


# =============================================================================
# W3 -- SQLite Tenant-Level Expiration Telemetry
# =============================================================================

class TestSQLiteTenantExpirationTelemetry:
    """DEF-W3: prune_expired() must attribute per-tenant expired counts via set-based SELECT."""

    def test_single_tenant_expiry_attributed(self):
        cache = _make_cache(max_memory=10)
        cache.put("tenant_exp", "exp_text", "m", 3, [0.1, 0.2, 0.3], ttl=0.001)
        with cache._lock:
            cache._memory_cache.clear()
        time.sleep(0.02)
        total = cache.prune_expired()
        assert total >= 1
        assert cache.stats(owner_id="tenant_exp")["expired_evictions"] >= 1

    def test_multiple_tenant_expiry_attributed_correctly(self):
        cache = _make_cache(max_memory=10)
        cache.put("t_alpha", "text_a", "m", 3, [1.0, 0.0, 0.0], ttl=0.001)
        cache.put("t_beta",  "text_b", "m", 3, [0.0, 1.0, 0.0], ttl=0.001)
        with cache._lock:
            cache._memory_cache.clear()
        time.sleep(0.02)
        total = cache.prune_expired()
        assert total >= 2
        assert cache.stats(owner_id="t_alpha")["expired_evictions"] >= 1
        assert cache.stats(owner_id="t_beta")["expired_evictions"] >= 1

    def test_non_expired_entries_not_counted(self):
        cache = _make_cache(max_memory=10)
        cache.put("tenant_fresh", "fresh_text", "m", 3, [0.5, 0.5, 0.0], ttl=9999.0)
        with cache._lock:
            cache._memory_cache.clear()
        cache.prune_expired()
        assert cache.stats(owner_id="tenant_fresh")["expired_evictions"] == 0

    def test_mixed_expired_and_fresh(self):
        cache = _make_cache(max_memory=10)
        cache.put("t_mixed", "exp",   "m", 3, [1.0, 0.0, 0.0], ttl=0.001)
        cache.put("t_mixed", "fresh", "m", 3, [0.0, 1.0, 0.0], ttl=9999.0)
        with cache._lock:
            cache._memory_cache.clear()
        time.sleep(0.02)
        cache.prune_expired()
        assert cache.stats(owner_id="t_mixed")["expired_evictions"] >= 1

    def test_repeated_prune_does_not_double_count(self):
        cache = _make_cache(max_memory=10)
        cache.put("t_rep", "text_r", "m", 3, [0.1, 0.2, 0.3], ttl=0.001)
        with cache._lock:
            cache._memory_cache.clear()
        time.sleep(0.02)
        cache.prune_expired()
        first = cache.stats(owner_id="t_rep")["expired_evictions"]
        cache.prune_expired()
        second = cache.stats(owner_id="t_rep")["expired_evictions"]
        assert second == first

    def test_empty_prune_result_is_safe(self):
        cache = _make_cache()
        assert cache.prune_expired() == 0


# =============================================================================
# W4 -- ShadowQueryCalibrator
# =============================================================================

class TestShadowQueryCalibrator:
    """DEF-W4: Bounded sampling, tenant isolation, recall computation, fallback semantics."""

    def setup_method(self):
        ShadowQueryCalibrator().reset()

    def test_default_calibrator_singleton_accessible(self):
        assert isinstance(get_default_calibrator(), ShadowQueryCalibrator)

    def test_constructor_validates_sampling_rate(self):
        with pytest.raises(ValueError):
            ShadowQueryCalibrator(sampling_rate=-0.01)
        with pytest.raises(ValueError):
            ShadowQueryCalibrator(sampling_rate=1.01)

    def test_disabled_calibrator_never_samples(self):
        cal = ShadowQueryCalibrator(sampling_rate=0.0)
        assert all(not cal.should_sample(100) for _ in range(50))

    def test_always_samples_when_rate_is_1(self):
        cal = ShadowQueryCalibrator(sampling_rate=1.0, shadow_max_corpus_size=99999)
        assert all(cal.should_sample(1000) for _ in range(10))

    def test_no_sampling_above_shadow_max_corpus_size(self):
        cal = ShadowQueryCalibrator(sampling_rate=1.0, shadow_max_corpus_size=500)
        assert not cal.should_sample(501)

    def test_record_observation_perfect_recall(self):
        cal = ShadowQueryCalibrator(min_samples=1)
        r = cal.record_observation("t1", ["a", "b", "c"], ["a", "b", "c"], k=3)
        assert r == pytest.approx(1.0)

    def test_record_observation_zero_recall(self):
        cal = ShadowQueryCalibrator(min_samples=1)
        r = cal.record_observation("t1", ["x", "y", "z"], ["a", "b", "c"], k=3)
        assert r == pytest.approx(0.0)

    def test_record_observation_partial_recall(self):
        cal = ShadowQueryCalibrator(min_samples=1)
        r = cal.record_observation("t1", ["a", "b", "c"], ["a", "b", "d"], k=3)
        assert r == pytest.approx(2.0 / 3.0)

    def test_empty_exact_results_returns_perfect(self):
        cal = ShadowQueryCalibrator(min_samples=1)
        r = cal.record_observation("t1", ["a"], [], k=3)
        assert r == pytest.approx(1.0)

    def test_insufficient_samples_returns_model_estimate(self):
        cal = ShadowQueryCalibrator(min_samples=10)
        for _ in range(5):
            cal.record_observation("t1", ["a"], ["a"], k=1)
        recall, is_empirical = cal.get_calibrated_recall("t1", model_recall=0.80)
        assert not is_empirical
        assert recall == pytest.approx(0.80)

    def test_sufficient_samples_returns_empirical(self):
        cal = ShadowQueryCalibrator(min_samples=3)
        for _ in range(3):
            cal.record_observation("t1", ["a", "b"], ["a", "b"], k=2)
        recall, is_empirical = cal.get_calibrated_recall("t1", model_recall=0.70)
        assert is_empirical
        assert recall == pytest.approx(1.0)

    def test_tenant_isolation_in_calibrator(self):
        cal = ShadowQueryCalibrator(min_samples=3)
        for _ in range(5):
            cal.record_observation("tenant_a", ["a"], ["a"], k=1)
        recall_b, is_empirical_b = cal.get_calibrated_recall("tenant_b", model_recall=0.77)
        assert not is_empirical_b
        assert recall_b == pytest.approx(0.77)

    def test_calibration_state_to_dict(self):
        state = CalibrationState(samples=5, recall_sum=4.5, min_samples=10)
        d = state.to_dict()
        assert d["samples"] == 5
        assert not d["is_empirical"]
        assert "measured_recall" in d
        assert d["measured_recall"] == pytest.approx(0.9, abs=1e-4)

    def test_calibration_state_is_empirical_when_sufficient(self):
        state = CalibrationState(samples=10, recall_sum=9.5, min_samples=10)
        assert state.is_empirical is True

    def test_get_calibration_state_returns_copy(self):
        cal = ShadowQueryCalibrator(min_samples=2)
        cal.record_observation("tc", ["a"], ["a"], k=1)
        state = cal.get_calibration_state("tc")
        assert state.samples == 1
        assert state.recall_sum == pytest.approx(1.0)

    def test_reset_clears_specific_tenant(self):
        cal = ShadowQueryCalibrator(min_samples=1)
        cal.record_observation("ta", ["a"], ["a"], k=1)
        cal.record_observation("tb", ["b"], ["b"], k=1)
        cal.reset(owner_id="ta")
        assert cal.get_calibration_state("ta").samples == 0
        assert cal.get_calibration_state("tb").samples == 1

    def test_reset_all_clears_all_tenants(self):
        cal = ShadowQueryCalibrator(min_samples=1)
        cal.record_observation("x", ["a"], ["a"], k=1)
        cal.record_observation("y", ["a"], ["a"], k=1)
        cal.reset()
        assert cal.get_calibration_state("x").samples == 0
        assert cal.get_calibration_state("y").samples == 0


# =============================================================================
# W5 -- Calibration-Aware Strategy Decisions
# =============================================================================

class TestCalibrationAwareStrategyDecisions:
    """DEF-W5: Semantic hierarchy: empirical > model > exact fallback."""

    def _cfg(self, quality_floor=0.85, max_fallback=50000):
        return AutoIndexConfig(
            exact_threshold=10,
            max_exact_fallback_size=max_fallback,
            quality_floor_recall=quality_floor,
            latency_target_ms=1000.0,
        )

    def test_model_estimate_used_when_no_calibration(self):
        config = self._cfg(quality_floor=0.70)
        cal = ShadowQueryCalibrator(sampling_rate=0.0)
        strategy = AutoIndexStrategy(config=config, calibrator=cal)
        vectors = [(f"id_{i}", [float(i)] * 4) for i in range(11)]
        engine = strategy.select_engine(vectors, owner_id="t_model")
        assert isinstance(engine, (NativeVectorIndex, ApproximateVectorIndex))

    def test_empirical_recall_above_floor_uses_ivf(self):
        config = self._cfg(quality_floor=0.80, max_fallback=1000)
        cal = ShadowQueryCalibrator(min_samples=3, sampling_rate=0.0)
        for _ in range(3):
            cal.record_observation("t_emp", ["a", "b"], ["a", "b"], k=2)
        strategy = AutoIndexStrategy(config=config, calibrator=cal)
        vectors = [(f"id_{i}", [float(i)] * 4) for i in range(15)]
        engine = strategy.select_engine(vectors, owner_id="t_emp")
        assert isinstance(engine, (NativeVectorIndex, ApproximateVectorIndex))

    def test_empirical_recall_below_floor_falls_back_to_exact(self):
        config = self._cfg(quality_floor=0.90, max_fallback=1000)
        cal = ShadowQueryCalibrator(min_samples=3, sampling_rate=0.0)
        for _ in range(3):
            cal.record_observation("t_low", ["x", "y"], ["a", "b"], k=2)
        strategy = AutoIndexStrategy(config=config, calibrator=cal)
        vectors = [(f"id_{i}", [float(i)] * 4) for i in range(15)]
        engine = strategy.select_engine(vectors, owner_id="t_low")
        assert isinstance(engine, ExactVectorIndex)

    def test_model_recall_below_floor_without_calibration_exact_fallback(self):
        config = self._cfg(quality_floor=0.999, max_fallback=1000)
        cal = ShadowQueryCalibrator(sampling_rate=0.0)
        strategy = AutoIndexStrategy(config=config, calibrator=cal)
        vectors = [(f"id_{i}", [float(i)] * 4) for i in range(11)]
        engine = strategy.select_engine(vectors, owner_id="t_no_cal")
        assert isinstance(engine, ExactVectorIndex)

    def test_no_owner_id_uses_model_estimate_no_crash(self):
        config = self._cfg(quality_floor=0.70)
        cal = ShadowQueryCalibrator(min_samples=1, sampling_rate=0.0)
        cal.record_observation("some_tenant", ["a"], ["a"], k=1)
        strategy = AutoIndexStrategy(config=config, calibrator=cal)
        vectors = [(f"id_{i}", [float(i)] * 4) for i in range(15)]
        engine = strategy.select_engine(vectors)
        assert engine is not None

    def test_force_mode_bypasses_calibration(self):
        config = self._cfg()
        cal = ShadowQueryCalibrator(min_samples=1, sampling_rate=0.0)
        for _ in range(5):
            cal.record_observation("force_tenant", ["x"], ["a", "b"], k=2)
        strategy = AutoIndexStrategy(config=config, calibrator=cal)
        vectors = [(f"id_{i}", [float(i)] * 4) for i in range(20)]
        exact = strategy.select_engine(vectors, force_mode="exact", owner_id="force_tenant")
        assert isinstance(exact, ExactVectorIndex)

    def test_calibration_not_shared_across_tenants(self):
        config = self._cfg(quality_floor=0.90, max_fallback=1000)
        cal = ShadowQueryCalibrator(min_samples=3, sampling_rate=0.0)
        for _ in range(3):
            cal.record_observation("tenant_a", ["x", "y"], ["a", "b"], k=2)
        strategy = AutoIndexStrategy(config=config, calibrator=cal)
        vectors = [(f"id_{i}", [float(i)] * 4) for i in range(15)]
        engine_b = strategy.select_engine(vectors, owner_id="tenant_b")
        # tenant_b has no calibration; model recall at N=15, threshold=10 is ~0.75 < floor=0.90
        assert isinstance(engine_b, ExactVectorIndex)

    def test_model_recall_estimate_range(self):
        config = AutoIndexConfig()
        strategy = AutoIndexStrategy(config=config)
        r_low = strategy._model_recall_estimate(config.exact_threshold)
        assert 0.84 <= r_low <= 0.86
        r_mid = strategy._model_recall_estimate(50000)
        assert r_mid == pytest.approx(0.95, abs=0.01)
        r_high = strategy._model_recall_estimate(200000)
        assert r_high == pytest.approx(0.98, abs=0.01)


# =============================================================================
# W6 -- Observability Contract
# =============================================================================

class TestObservabilityContract:
    """DEF-W6: Coherent non-leaking telemetry surface."""

    def test_global_stats_contains_all_phase8_keys(self):
        cache = _make_cache()
        s = cache.stats()
        required = {
            "hits", "misses", "evictions", "expired_evictions",
            "protection_drops", "admission_denials", "invalidations",
            "hit_rate", "in_memory_entries", "max_memory_entries",
            "persistent_entries", "max_persistent_entries",
        }
        missing = required - set(s.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_tenant_stats_contains_all_phase8_keys(self):
        cache = _make_cache()
        s = cache.stats(owner_id="obs_tenant")
        required = {
            "hits", "misses", "evictions", "expired_evictions",
            "protection_drops", "admission_denials", "invalidations",
            "hit_rate", "in_memory_entries", "max_memory_entries",
            "persistent_entries", "max_persistent_entries",
        }
        missing = required - set(s.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_tenant_stats_does_not_leak_other_tenant_data(self):
        cache = _make_cache(max_memory=2)
        _put(cache, "tenant_blocker", "b1")
        _put(cache, "tenant_blocker", "b2")
        _put(cache, "tenant_denied", "d1")
        s_blocker = cache.stats(owner_id="tenant_blocker")
        assert s_blocker["admission_denials"] == 0

    def test_calibration_state_dict_has_no_sensitive_data(self):
        state = CalibrationState(samples=7, recall_sum=6.3, min_samples=10)
        d = state.to_dict()
        for key in d:
            assert "vector" not in key.lower()
            assert "embedding" not in key.lower()
            assert "query" not in key.lower()

    def test_calibration_state_is_empirical_flag_is_accurate(self):
        cal = ShadowQueryCalibrator(min_samples=5)
        for _ in range(4):
            cal.record_observation("obs_t", ["a"], ["a"], k=1)
        assert not cal.get_calibration_state("obs_t").is_empirical
        cal.record_observation("obs_t", ["a"], ["a"], k=1)
        assert cal.get_calibration_state("obs_t").is_empirical

    def test_no_global_metrics_exposed_through_tenant_api(self):
        cache = _make_cache()
        _put(cache, "global_ta", "text_a")
        _put(cache, "global_tb", "text_b")
        cache.get("global_ta", "text_a", "test", 3)
        cache.get("global_tb", "text_b", "test", 3)
        s_ta = cache.stats(owner_id="global_ta")
        s_global = cache.stats()
        assert s_ta["hits"] == 1
        assert s_global["hits"] == 2


# =============================================================================
# W7 -- Regression Guard
# =============================================================================

class TestPhase8RegressionGuard:
    """DEF-W7: Phase 7 invariants must remain intact after Phase 8 changes."""

    def test_exact_index_below_threshold_unchanged(self):
        config = AutoIndexConfig()
        strategy = AutoIndexStrategy(config)
        for n in [0, 1, 100, 1000, 10000, 24999]:
            vecs = [(f"id_{i}", [0.1] * 4) for i in range(n)]
            engine = strategy.select_engine(vecs, owner_id="reg_tenant")
            assert isinstance(engine, ExactVectorIndex), f"Failed at N={n}"

    def test_force_mode_exact_always_overrides(self):
        config = AutoIndexConfig()
        strategy = AutoIndexStrategy(config)
        vecs = [(f"id_{i}", [0.1] * 4) for i in range(50001)]
        engine = strategy.select_engine(vecs, force_mode="exact", owner_id="reg_tenant")
        assert isinstance(engine, ExactVectorIndex)

    def test_force_mode_approximate_always_overrides(self):
        config = AutoIndexConfig()
        strategy = AutoIndexStrategy(config)
        vecs = [(f"id_{i}", [0.1] * 4) for i in range(3)]
        engine = strategy.select_engine(vecs, force_mode="approximate", owner_id="reg_tenant")
        assert isinstance(engine, ApproximateVectorIndex)

    def test_get_vector_index_legacy_threshold_unchanged(self):
        vecs_small = [("id_1", [0.1, 0.2])]
        vecs_large = [(f"id_{i}", [0.1, 0.2]) for i in range(600)]
        assert isinstance(get_vector_index("any", vecs_small, threshold=500), ExactVectorIndex)
        assert isinstance(get_vector_index("any", vecs_large, threshold=500), ApproximateVectorIndex)

    def test_strategy_decision_has_required_fields(self):
        config = AutoIndexConfig()
        strategy = AutoIndexStrategy(config)
        d = strategy.evaluate_strategy(100)
        assert hasattr(d, "recommended_type")
        assert hasattr(d, "is_approximate")
        assert hasattr(d, "estimated_latency_ms")

    def test_autoindexconfig_post_init_syncs_approximate_threshold(self):
        config = AutoIndexConfig(exact_threshold=5000)
        assert config.approximate_threshold == 5000

    def test_cache_put_get_roundtrip_unchanged(self):
        cache = _make_cache()
        vec = [0.1, 0.2, 0.3]
        cache.put("rt_tenant", "hello world", "m", 3, vec)
        result = cache.get("rt_tenant", "hello world", "m", 3)
        assert result is not None
        assert len(result) == 3

    def test_cache_cross_tenant_isolation_unchanged(self):
        cache = _make_cache()
        cache.put("tenant_A", "secret", "m", 3, [0.9, 0.9, 0.9])
        result = cache.get("tenant_B", "secret", "m", 3)
        assert result is None
