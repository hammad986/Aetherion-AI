"""
tests/integration/test_phase8_cycle2_remediation.py — Comprehensive test suite for Phase 8 Remediation Cycle 2.
Covers:
  1. Top-K determinism, k=0 guard, tie-break, min_score semantics, and 1,000-trial reference parity.
  2. Content-aware vector fingerprinting (raw values, magnitude mutation detection, dimension sensitivity).
  3. Real SQLite calibration persistence across instances, lifecycle drift, fail-closed corruption recovery, and tenant isolation.
  4. Authoritative vector index strategy boundaries (N=499, 500, 24999, 25000, 25001, 50000, 50001, 75000).
"""
from __future__ import annotations

import os
import random
import tempfile
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple
import pytest

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    np = None
    _NUMPY_AVAILABLE = False

from backend.execution.memory.index import (
    _select_top_k_deterministic,
    ApproximateVectorIndex,
    AutoIndexConfig,
    AutoIndexStrategy,
    CalibrationState,
    ExactVectorIndex,
    NativeVectorIndex,
    ShadowQueryCalibrator,
    get_vector_index,
)
from backend.execution.persistence.db import StateDatabase

_compute_corpus_fingerprint = ApproximateVectorIndex._compute_corpus_fingerprint


# ── Reference Implementation for Parity Verification ──────────────────────────

def ref_select_top_k(
    sims: Sequence[float],
    row_to_id: Sequence[str],
    indices: Optional[Sequence[int]],
    top_k: int,
    min_score: float = 0.0,
) -> List[Tuple[str, float]]:
    if top_k <= 0 or len(sims) == 0:
        return []

    eligible = []
    for i, s in enumerate(sims):
        if s >= min_score:
            actual_row = indices[i] if indices is not None else i
            eligible.append((row_to_id[actual_row], float(s)))

    eligible.sort(key=lambda x: (x[1], x[0]), reverse=True)
    return eligible[:top_k]


# ==============================================================================
# 1. Top-K Correctness & Drop-In Parity
# ==============================================================================

class TestTopKDeterminismAndParity:
    """Missions 1, 2, 3: Top-K determinism, k=0 guard, min_score, and 1,000-trial parity."""

    def test_k_zero_returns_empty_list_without_exception(self):
        sims = np.array([0.9, 0.5, 0.2], dtype=np.float32)
        mids = ['m1', 'm2', 'm3']
        assert _select_top_k_deterministic(sims, mids, None, 0) == []
        assert _select_top_k_deterministic(sims, mids, None, -1) == []
        assert _select_top_k_deterministic(sims, mids, None, -100) == []

    def test_empty_candidates_returns_empty_list(self):
        sims = np.array([], dtype=np.float32)
        mids: List[str] = []
        assert _select_top_k_deterministic(sims, mids, None, 5) == []

    def test_k_one_returns_single_highest(self):
        sims = np.array([0.3, 0.85, 0.6], dtype=np.float32)
        mids = ['m1', 'm2', 'm3']
        res = _select_top_k_deterministic(sims, mids, None, 1)
        assert len(res) == 1
        assert res[0][0] == 'm2'
        assert res[0][1] == pytest.approx(0.85)

    def test_k_less_than_n(self):
        sims = np.array([0.1, 0.9, 0.4, 0.7, 0.3], dtype=np.float32)
        mids = [f'm{i}' for i in range(5)]
        res = _select_top_k_deterministic(sims, mids, None, 2)
        assert len(res) == 2
        assert [r[0] for r in res] == ['m1', 'm3']

    def test_k_equal_to_n(self):
        sims = np.array([0.2, 0.8, 0.5], dtype=np.float32)
        mids = ['a', 'b', 'c']
        res = _select_top_k_deterministic(sims, mids, None, 3)
        assert len(res) == 3
        assert [r[0] for r in res] == ['b', 'c', 'a']

    def test_k_greater_than_n(self):
        sims = np.array([0.2, 0.8], dtype=np.float32)
        mids = ['a', 'b']
        res = _select_top_k_deterministic(sims, mids, None, 10)
        assert len(res) == 2
        assert [r[0] for r in res] == ['b', 'a']

    def test_min_score_zero_excludes_negative_similarities(self):
        sims = np.array([-1.0, -0.5, 0.0, 0.3], dtype=np.float32)
        mids = ['neg1', 'neg2', 'zero', 'pos']
        res = _select_top_k_deterministic(sims, mids, None, 10, min_score=0.0)
        retrieved_ids = [r[0] for r in res]
        assert 'neg1' not in retrieved_ids
        assert 'neg2' not in retrieved_ids
        assert 'zero' in retrieved_ids
        assert 'pos' in retrieved_ids
        for mid, score in res:
            assert score >= 0.0

    def test_min_score_negative_allows_valid_negatives(self):
        sims = np.array([-0.8, -0.2, 0.5], dtype=np.float32)
        mids = ['a', 'b', 'c']
        res = _select_top_k_deterministic(sims, mids, None, 10, min_score=-0.5)
        retrieved_ids = [r[0] for r in res]
        assert 'a' not in retrieved_ids
        assert 'b' in retrieved_ids
        assert 'c' in retrieved_ids
        b_score = next(score for mid, score in res if mid == 'b')
        assert b_score == pytest.approx(-0.2)

    def test_min_score_positive_filters_correctly(self):
        sims = np.array([0.1, 0.49, 0.50, 0.8], dtype=np.float32)
        mids = ['a', 'b', 'c', 'd']
        res = _select_top_k_deterministic(sims, mids, None, 10, min_score=0.50)
        retrieved_ids = [r[0] for r in res]
        assert retrieved_ids == ['d', 'c']

    def test_exact_vector_index_search_negative_exclusion(self):
        corpus = [
            ('pos_1', [1.0, 0.0]),
            ('neg_1', [-1.0, 0.0]),
            ('zero_1', [0.0, 1.0]),
        ]
        idx = ExactVectorIndex(corpus)
        hits = idx.search([1.0, 0.0], top_k=5, min_score=0.0)
        hit_ids = [h[0] for h in hits]
        assert 'neg_1' not in hit_ids
        assert 'pos_1' in hit_ids
        assert 'zero_1' in hit_ids

    def test_deterministic_tie_break_ordering(self):
        sims = np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float32)
        mids = ['alpha', 'beta', 'gamma', 'delta']
        res = _select_top_k_deterministic(sims, mids, None, 3, min_score=0.0)
        assert [r[0] for r in res] == ['gamma', 'delta', 'beta']

    def test_one_thousand_adversarial_trials_match_reference_parity(self):
        mismatches = 0
        for trial in range(1000):
            rng = random.Random(trial + 42)
            np_rng = np.random.RandomState(trial + 42)
            n = rng.randint(1, 80)
            k = rng.randint(0, n + 5)
            min_score = rng.choice([-0.8, -0.2, 0.0, 0.0, 0.1, 0.5])
            
            mode = trial % 4
            if mode == 0:
                sims = np_rng.randn(n).astype(np.float32)
            elif mode == 1:
                sims = np_rng.choice([-0.5, 0.0, 0.2, 0.2, 0.7, 0.7], size=n).astype(np.float32)
            elif mode == 2:
                sims = np.full(n, 0.42, dtype=np.float32)
            else:
                sims = np.round(np_rng.randn(n).astype(np.float32), 1)

            mids = [f'mem_{j:04d}' for j in rng.sample(range(1000), n)]
            use_indices = (trial % 2 == 0)
            indices = list(range(n)) if use_indices else None

            expected = ref_select_top_k(sims, mids, indices, k, min_score)
            actual = _select_top_k_deterministic(sims, mids, indices, k, min_score)

            if len(expected) != len(actual):
                mismatches += 1
                continue

            for (exp_id, exp_s), (act_id, act_s) in zip(expected, actual):
                if exp_id != act_id or abs(exp_s - act_s) > 1e-5:
                    mismatches += 1
                    break

        assert mismatches == 0, f'Found {mismatches}/1000 mismatches against reference implementation'


# ==============================================================================
# 2. Content-Aware Vector Corpus Fingerprinting
# ==============================================================================

class TestCorpusFingerprintContentAware:
    """Mission 4: Fingerprint raw representations, dimension changes, and magnitude mutations."""

    def test_magnitude_only_mutation_detected(self):
        c1 = [('m0', [0.0, 1.0])]
        c2 = [('m0', [0.0, 0.999])]
        fp1, gen1 = _compute_corpus_fingerprint(c1)
        fp2, gen2 = _compute_corpus_fingerprint(c2)
        assert fp1 != fp2, 'Magnitude mutation ([0, 1.0] vs [0, 0.999]) produced identical fingerprint!'
        assert gen1 != gen2

    def test_scaled_vector_mutation_detected(self):
        c1 = [('m0', [1.0, 2.0]), ('m1', [3.0, 4.0])]
        c2 = [('m0', [1.0, 2.0]), ('m1', [6.0, 8.0])]
        fp1, _ = _compute_corpus_fingerprint(c1)
        fp2, _ = _compute_corpus_fingerprint(c2)
        assert fp1 != fp2

    def test_dimension_change_detected(self):
        c1 = [('m0', [1.0, 2.0])]
        c2 = [('m0', [1.0, 2.0, 0.0])]
        fp1, _ = _compute_corpus_fingerprint(c1)
        fp2, _ = _compute_corpus_fingerprint(c2)
        assert fp1 != fp2

    def test_order_invariance(self):
        c1 = [('a', [1.0, 2.0]), ('b', [3.0, 4.0]), ('c', [5.0, 6.0])]
        c2 = [('c', [5.0, 6.0]), ('a', [1.0, 2.0]), ('b', [3.0, 4.0])]
        fp1, gen1 = _compute_corpus_fingerprint(c1)
        fp2, gen2 = _compute_corpus_fingerprint(c2)
        assert fp1 == fp2
        assert gen1 == gen2

    def test_duplicate_id_overwrite_semantics(self):
        c1 = [('a', [1.0, 2.0]), ('a', [5.0, 6.0])]
        c2 = [('a', [5.0, 6.0])]
        fp1, _ = _compute_corpus_fingerprint(c1)
        fp2, _ = _compute_corpus_fingerprint(c2)
        assert fp1 == fp2

    def test_id_addition_and_deletion(self):
        base = [('a', [1.0, 2.0]), ('b', [3.0, 4.0])]
        added = [('a', [1.0, 2.0]), ('b', [3.0, 4.0]), ('c', [5.0, 6.0])]
        fp_base, _ = _compute_corpus_fingerprint(base)
        fp_added, _ = _compute_corpus_fingerprint(added)
        assert fp_base != fp_added


# ==============================================================================
# 3. SQLite Calibration Persistence & Invalidation
# ==============================================================================

class TestSQLiteCalibrationPersistence:
    """Mission 6: Real SQLite calibration persistence across instances, drift, and isolation."""

    def test_calibration_state_survives_process_restart(self, tmp_path):
        db_path = tmp_path / 'test_cal_persist.db'
        db = StateDatabase(db_path)

        cal1 = ShadowQueryCalibrator(sampling_rate=1.0, min_samples=3, db=db, async_mode=False)
        for _ in range(5):
            cal1.record_observation(
                owner_id='tenant_p1',
                ivf_results=['a', 'b'],
                exact_results=['a', 'b'],
                k=2,
                corpus_size=20,
                index_generation=12345,
                model_name='test-model',
            )
        state1 = cal1.get_calibration_state('tenant_p1')
        assert state1.samples == 5
        assert state1.is_empirical
        cal1.shutdown()

        cal2 = ShadowQueryCalibrator(sampling_rate=1.0, min_samples=3, db=db, async_mode=False)
        state2 = cal2.get_calibration_state('tenant_p1')
        assert state2.samples == 5
        assert state2.is_empirical
        assert state2.measured_recall == pytest.approx(1.0)
        assert state2.corpus_size == 20
        assert state2.index_generation == 12345
        assert state2.model_name == 'test-model'
        cal2.shutdown()

    def test_tenant_isolation_in_persistence(self, tmp_path):
        db_path = tmp_path / 'test_cal_iso.db'
        db = StateDatabase(db_path)

        cal = ShadowQueryCalibrator(sampling_rate=1.0, min_samples=2, db=db, async_mode=False)
        cal.record_observation('tenant_a', ['a'], ['a'], k=1, corpus_size=10)
        
        state_a = cal.get_calibration_state('tenant_a')
        state_b = cal.get_calibration_state('tenant_b')
        assert state_a.samples == 1
        assert state_b.samples == 0
        cal.shutdown()

        cal_reloaded = ShadowQueryCalibrator(db=db, async_mode=False)
        assert cal_reloaded.get_calibration_state('tenant_a').samples == 1
        assert cal_reloaded.get_calibration_state('tenant_b').samples == 0
        cal_reloaded.shutdown()

    def test_corpus_drift_invalidates_persisted_state(self, tmp_path):
        db_path = tmp_path / 'test_cal_drift.db'
        db = StateDatabase(db_path)

        cal = ShadowQueryCalibrator(db=db, async_mode=False)
        cal.record_observation('tenant_drift', ['a'], ['a'], k=1, corpus_size=100)
        assert cal.get_calibration_state('tenant_drift').samples == 1

        cal.record_observation('tenant_drift', ['a'], ['a'], k=1, corpus_size=150)
        state = cal.get_calibration_state('tenant_drift')
        assert state.samples == 1
        assert state.corpus_size == 150
        cal.shutdown()

    def test_model_change_invalidates_persisted_state(self, tmp_path):
        db_path = tmp_path / 'test_cal_model.db'
        db = StateDatabase(db_path)

        cal = ShadowQueryCalibrator(db=db, async_mode=False)
        cal.record_observation('tenant_model', ['a'], ['a'], k=1, model_name='model_v1')
        assert cal.get_calibration_state('tenant_model').samples == 1

        cal.record_observation('tenant_model', ['a'], ['a'], k=1, model_name='model_v2')
        state = cal.get_calibration_state('tenant_model')
        assert state.samples == 1
        assert state.model_name == 'model_v2'
        cal.shutdown()

    def test_invalidate_tenant_deletes_persisted_row(self, tmp_path):
        db_path = tmp_path / 'test_cal_inv.db'
        db = StateDatabase(db_path)

        cal = ShadowQueryCalibrator(db=db, async_mode=False)
        cal.record_observation('tenant_del', ['a'], ['a'], k=1)
        assert cal.get_calibration_state('tenant_del').samples == 1

        cal.invalidate_tenant('tenant_del')
        assert cal.get_calibration_state('tenant_del').samples == 0

        cal2 = ShadowQueryCalibrator(db=db, async_mode=False)
        assert cal2.get_calibration_state('tenant_del').samples == 0
        cal.shutdown()
        cal2.shutdown()

    def test_corrupt_row_fails_closed_to_fresh_state(self, tmp_path):
        db_path = tmp_path / 'test_cal_corrupt.db'
        db = StateDatabase(db_path)

        conn = db.get_connection()
        conn.execute(
            'INSERT INTO calibration_states (tenant_id, samples, recall_sum) VALUES (?, ?, ?);',
            ('tenant_corrupt', 'NOT_AN_INT', 'NOT_A_FLOAT'),
        )
        conn.close()

        cal = ShadowQueryCalibrator(db=db, async_mode=False)
        state = cal.get_calibration_state('tenant_corrupt')
        assert state.samples == 0
        assert not state.is_empirical
        cal.shutdown()


# ==============================================================================
# 4. Authoritative Vector Index Strategy & Boundaries
# ==============================================================================

class TestVectorStrategyThresholdBoundaries:
    """Mission 7: Verify authoritative strategy boundaries under production config."""

    def test_boundary_499_exact(self):
        vecs = [(f'm_{i}', [1.0, 0.0]) for i in range(499)]
        idx = get_vector_index(owner_id='t_499', vectors=vecs)
        assert isinstance(idx, ExactVectorIndex)

    def test_boundary_500_authoritative_exact(self):
        vecs = [(f'm_{i}', [1.0, 0.0]) for i in range(500)]
        idx = get_vector_index(owner_id='t_500', vectors=vecs)
        assert isinstance(idx, ExactVectorIndex)

    def test_legacy_threshold_override_works(self):
        vecs = [(f'm_{i}', [1.0, 0.0]) for i in range(500)]
        idx = get_vector_index(owner_id='t_legacy', vectors=vecs, threshold=500)
        assert isinstance(idx, ApproximateVectorIndex)

    def test_boundary_24999_exact(self):
        vecs = [(f'm_{i}', [1.0, 0.0]) for i in range(24999)]
        config = AutoIndexConfig()
        idx = get_vector_index(owner_id='t_24999', vectors=vecs, config=config)
        assert isinstance(idx, ExactVectorIndex)

    def test_boundary_25000_controlled_fallback_zone(self):
        vecs = [(f'm_{i}', [1.0, 0.0]) for i in range(25000)]
        config = AutoIndexConfig()
        idx = get_vector_index(owner_id='t_25000', vectors=vecs, config=config)
        assert isinstance(idx, (ApproximateVectorIndex, NativeVectorIndex))

    def test_boundary_25001_controlled_fallback_zone(self):
        vecs = [(f'm_{i}', [1.0, 0.0]) for i in range(25001)]
        idx = get_vector_index(owner_id='t_25001', vectors=vecs)
        assert isinstance(idx, (ApproximateVectorIndex, NativeVectorIndex))

    def test_boundary_50000_controlled_fallback_upper_bound(self):
        vecs = [(f'm_{i}', [1.0, 0.0]) for i in range(50000)]
        idx = get_vector_index(owner_id='t_50000', vectors=vecs)
        assert isinstance(idx, (ApproximateVectorIndex, NativeVectorIndex))

    def test_boundary_50001_large_corpus_unconditional_ivf(self):
        vecs = [(f'm_{i}', [1.0, 0.0]) for i in range(50001)]
        idx = get_vector_index(owner_id='t_50001', vectors=vecs)
        assert isinstance(idx, (ApproximateVectorIndex, NativeVectorIndex))

    def test_boundary_75000_large_corpus_unconditional_ivf(self):
        vecs = [(f'm_{i}', [1.0, 0.0]) for i in range(75000)]
        idx = get_vector_index(owner_id='t_75000', vectors=vecs)
        assert isinstance(idx, (ApproximateVectorIndex, NativeVectorIndex))
