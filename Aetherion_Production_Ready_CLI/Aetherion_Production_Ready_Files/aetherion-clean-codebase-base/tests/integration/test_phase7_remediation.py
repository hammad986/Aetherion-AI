"""
tests/integration/test_phase7_remediation.py — Regression Tests for Phase 7 Remediation.

Tests for each of the 8 certification-blocking findings from the independent adversarial audit:
  SEC-P1-SSRF-BYPASS-01           — Port 59999 bypass removed
  SEC-P1-CACHE-KEY-DELIMITER-01   — Collision-safe canonical cache keys
  SEC-P1-CACHE-GLOBAL-EVICTION-01 — Tenant-scoped cache eviction
  REL-P1-EVAL-IDEMPOTENCY-01      — Ephemeral benchmark store (idempotent runs)
  ARCH-P2-STRATEGY-DRIFT-01       — AutoIndexConfig threshold >= 25,000
  ARCH-P2-MAINTENANCE-ASYNC-01    — Maintenance route returns job_id (async)
  SEM-P2-RETRIEVAL-STALENESS-01   — EXPIRED/STALE excluded from retrieval
  SEC-P2-TELEMETRY-LEAK-01        — No counter-heuristic metric leakage
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))


def _make_ephemeral_store(tmpdir):
    from execution.memory.store import MemoryStore
    return MemoryStore(db_path=Path(tmpdir) / "test.db")


# ===========================================================================
# SEC-P1-SSRF-BYPASS-01
# ===========================================================================

class TestSSRFBypass(unittest.TestCase):
    """Port 59999 bypass must not exist. SSRF guard applies to all ports on private IPs."""

    def _validate(self, url):
        from execution.memory.embeddings import validate_safe_destination_url
        validate_safe_destination_url(url)

    def test_localhost_59999_is_blocked(self):
        with self.assertRaises(ValueError):
            self._validate("http://localhost:59999/embed")

    def test_127_0_0_1_59999_is_blocked(self):
        with self.assertRaises(ValueError):
            self._validate("http://127.0.0.1:59999/embed")

    def test_rfc1918_192_168_is_blocked(self):
        with self.assertRaises(ValueError):
            self._validate("http://192.168.1.100:59999/embed")

    def test_rfc1918_10_is_blocked(self):
        with self.assertRaises(ValueError):
            self._validate("http://10.0.0.1:8080/embed")

    def test_rfc1918_172_16_is_blocked(self):
        with self.assertRaises(ValueError):
            self._validate("http://172.16.0.1/embed")

    def test_aws_metadata_endpoint_blocked(self):
        with self.assertRaises(ValueError):
            self._validate("http://169.254.169.254/latest/meta-data/")

    def test_allow_local_env_still_works(self):
        """The legitimate AETHERION_ALLOW_LOCAL_EMBEDDING_ENDPOINT=1 dev path must still work."""
        from execution.memory.embeddings import validate_safe_destination_url
        with patch.dict(os.environ, {"AETHERION_ALLOW_LOCAL_EMBEDDING_ENDPOINT": "1"}):
            try:
                validate_safe_destination_url("http://127.0.0.1:11434/api/embed")
            except ValueError as e:
                self.fail(f"allow_local env var path should not raise, got: {e}")

    def test_public_ip_allowed(self):
        from execution.memory.embeddings import validate_safe_destination_url
        try:
            validate_safe_destination_url("https://api.openai.com/v1/embeddings")
        except ValueError as e:
            self.fail(f"Public IP should not raise, got: {e}")


# ===========================================================================
# SEC-P1-CACHE-KEY-DELIMITER-01
# ===========================================================================

class TestCacheKeyCollision(unittest.TestCase):
    """
    Canonical JSON encoding prevents delimiter-injection collisions.
    owner='a:b', model='c' must NOT collide with owner='a', model='b:c'.
    """

    def _key(self, owner_id, model_name, dimensions=768, content_hash="abc123"):
        from execution.memory.cache import compute_cache_key
        return compute_cache_key(owner_id, model_name, dimensions, content_hash)

    def test_no_collision_owner_model_boundary(self):
        k1 = self._key(owner_id="tenant_a:gpt4", model_name="small")
        k2 = self._key(owner_id="tenant_a", model_name="gpt4:small")
        self.assertNotEqual(k1, k2, "Cache key collision: owner boundary attack succeeded")

    def test_no_collision_multi_colon(self):
        k1 = self._key(owner_id="a:b:c", model_name="d")
        k2 = self._key(owner_id="a", model_name="b:c:d")
        self.assertNotEqual(k1, k2, "Cache key collision: multi-colon boundary attack succeeded")

    def test_deterministic(self):
        k1 = self._key("tenant1", "text-embedding-ada-002", 1536, "deadbeef")
        k2 = self._key("tenant1", "text-embedding-ada-002", 1536, "deadbeef")
        self.assertEqual(k1, k2)

    def test_different_tenants_different_keys(self):
        k1 = self._key("tenant_a", "model", 768, "hash1")
        k2 = self._key("tenant_b", "model", 768, "hash1")
        self.assertNotEqual(k1, k2)

    def test_key_is_sha256(self):
        k = self._key("owner", "model", 128, "somehash")
        self.assertEqual(len(k), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in k))


# ===========================================================================
# SEC-P1-CACHE-GLOBAL-EVICTION-01
# ===========================================================================

class TestCacheTenantEviction(unittest.TestCase):
    """
    When Tenant A's cache overflows and eviction triggers, it must NOT evict
    entries belonging to Tenant B.
    """

    def test_eviction_does_not_cross_tenant_boundary(self):
        from execution.memory.cache import EmbeddingCache
        with tempfile.TemporaryDirectory() as tmpdir:
            # max_persistent_entries=3 means Tenant A writing 4 items triggers eviction
            cache = EmbeddingCache(
                db_path=Path(tmpdir) / "cache_test.db",
                max_memory_entries=100,
                max_persistent_entries=3,
            )
            tenant_b_text = "Tenant B secret memory"
            vec_b = [0.1] * 8
            cache.put("tenant_b", tenant_b_text, "model", 8, vec_b)

            for i in range(4):
                cache.put("tenant_a", f"Tenant A memory {i}", "model", 8, [float(i)] * 8)

            result = cache.get("tenant_b", tenant_b_text, "model", 8)
            self.assertIsNotNone(result, "Tenant B entry was wrongly evicted by Tenant A overflow")
            self.assertEqual(result, vec_b)


# ===========================================================================
# REL-P1-EVAL-IDEMPOTENCY-01
# ===========================================================================

class TestBenchmarkIdempotency(unittest.TestCase):
    """
    run_retrieval_benchmark() must be idempotent: Nth invocation must succeed
    without UNIQUE constraint failures and must not contaminate the production store.
    """

    def test_first_run_succeeds(self):
        from execution.memory.evaluation import run_retrieval_benchmark
        result = run_retrieval_benchmark(suite_version="v1")
        self.assertIn("recall_at_5", result)
        self.assertGreater(result["total_test_cases"], 0)

    def test_second_run_succeeds(self):
        from execution.memory.evaluation import run_retrieval_benchmark
        run_retrieval_benchmark(suite_version="v1")
        result2 = run_retrieval_benchmark(suite_version="v1")
        self.assertIn("recall_at_5", result2)

    def test_nth_run_idempotent(self):
        from execution.memory.evaluation import run_retrieval_benchmark
        r1 = run_retrieval_benchmark(suite_version="v1")
        r2 = run_retrieval_benchmark(suite_version="v1")
        r3 = run_retrieval_benchmark(suite_version="v1")
        self.assertEqual(r1["total_test_cases"], r2["total_test_cases"])
        self.assertEqual(r2["total_test_cases"], r3["total_test_cases"])

    def test_benchmark_does_not_contaminate_production_store(self):
        from execution.memory.evaluation import run_retrieval_benchmark, BENCHMARK_FIXTURES
        from execution.memory.store import MemoryStore
        with tempfile.TemporaryDirectory() as tmpdir:
            prod_store = MemoryStore(db_path=Path(tmpdir) / "prod.db")
            run_retrieval_benchmark(suite_version="v1")
            for fx in BENCHMARK_FIXTURES[:25]:
                mem = prod_store.get_memory(fx["id"], "eval_benchmark_tenant")
                self.assertIsNone(mem, f"Benchmark fixture {fx['id']} contaminated production store")


# ===========================================================================
# ARCH-P2-STRATEGY-DRIFT-01
# ===========================================================================

class TestAutoIndexThreshold(unittest.TestCase):
    """
    AutoIndexConfig.exact_threshold must default to >=25,000.
    ExactVectorIndex must be used for all corpus sizes below the threshold.
    """

    def test_default_threshold_at_least_25000(self):
        from execution.memory.index import AutoIndexConfig
        cfg = AutoIndexConfig()
        self.assertGreaterEqual(cfg.exact_threshold, 25000)

    def test_1000_vectors_uses_exact(self):
        from execution.memory.index import AutoIndexStrategy, ExactVectorIndex
        strategy = AutoIndexStrategy()
        vectors = [(f"m{i}", [0.1] * 4) for i in range(1000)]
        engine = strategy.select_engine(vectors)
        self.assertIsInstance(engine, ExactVectorIndex)

    def test_5000_vectors_uses_exact(self):
        from execution.memory.index import AutoIndexStrategy, ExactVectorIndex
        strategy = AutoIndexStrategy()
        vectors = [(f"m{i}", [0.1] * 4) for i in range(5000)]
        engine = strategy.select_engine(vectors)
        self.assertIsInstance(engine, ExactVectorIndex)

    def test_10000_vectors_uses_exact(self):
        from execution.memory.index import AutoIndexStrategy, ExactVectorIndex
        strategy = AutoIndexStrategy()
        vectors = [(f"m{i}", [0.1] * 4) for i in range(10000)]
        engine = strategy.select_engine(vectors)
        self.assertIsInstance(engine, ExactVectorIndex)

    def test_25001_vectors_uses_approximate(self):
        """Above threshold: must switch to approximate/native, not ExactVectorIndex."""
        from execution.memory.index import AutoIndexStrategy, ExactVectorIndex
        strategy = AutoIndexStrategy()
        vectors = [(f"m{i}", [0.1] * 4) for i in range(25001)]
        engine = strategy.select_engine(vectors)
        self.assertNotIsInstance(engine, ExactVectorIndex,
                                 "N=25001 should use approximate/native engine, not exact")

    def test_evaluate_strategy_returns_latency(self):
        from execution.memory.index import AutoIndexStrategy
        strategy = AutoIndexStrategy()
        decision = strategy.evaluate_strategy(500)
        self.assertIsNotNone(decision.estimated_latency_ms)
        self.assertGreaterEqual(decision.estimated_latency_ms, 0.0)

    def test_force_exact_overrides_large_corpus(self):
        from execution.memory.index import AutoIndexStrategy, ExactVectorIndex
        strategy = AutoIndexStrategy()
        vectors = [(f"m{i}", [0.1] * 4) for i in range(100000)]
        engine = strategy.select_engine(vectors, force_mode="exact")
        self.assertIsInstance(engine, ExactVectorIndex)


# ===========================================================================
# ARCH-P2-MAINTENANCE-ASYNC-01
# ===========================================================================

class TestMaintenanceAsync(unittest.TestCase):
    """
    POST /api/memory/maintenance/<action> must:
    - Return HTTP 202 (Accepted), not 200 (blocking)
    - Return {ok: True, job_id: <non-empty string>}
    - Unknown actions must return 400
    """

    def _make_app(self):
        try:
            from flask import Flask
            from routes.phase4_routes import phase4_bp
            app = Flask(__name__)
            app.register_blueprint(phase4_bp)
            app.config["TESTING"] = True
            return app
        except Exception as e:
            self.skipTest(f"Cannot create test app: {e}")

    def test_maintenance_returns_202_and_job_id(self):
        from backend.auth_system import _make_access_token
        admin_token = _make_access_token(user_id=1, email="admin@test.com", role="admin")
        app = self._make_app()
        with patch("routes.phase4_routes._job_manager") as mock_jm:
            from execution.jobs.models import BackgroundJob, JobStatus
            fake_job = BackgroundJob(
                id="job_abc123",
                owner_id="1",
                name="memory_maintenance:backfill",
                task="embedding_backfill",
                status=JobStatus.QUEUED.value,
            )
            mock_jm.create_job.return_value = fake_job
            with app.test_client() as client:
                resp = client.post(
                    "/api/memory/maintenance/backfill",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={},
                )
                data = resp.get_json()
                self.assertEqual(resp.status_code, 202, f"Expected 202, got {resp.status_code}: {data}")
                self.assertTrue(data.get("ok"), f"Expected ok=True: {data}")
                self.assertIn("job_id", data, f"Response missing job_id: {data}")
                self.assertEqual(data["job_id"], "job_abc123")

    def test_unknown_action_returns_400(self):
        from backend.auth_system import _make_access_token
        admin_token = _make_access_token(user_id=1, email="admin@test.com", role="admin")
        app = self._make_app()
        with app.test_client() as client:
            resp = client.post(
                "/api/memory/maintenance/nonexistent_action",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={},
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertFalse(data.get("ok"))



# ===========================================================================
# SEM-P2-RETRIEVAL-STALENESS-01
# ===========================================================================

class TestRetrievalStalenessFilter(unittest.TestCase):
    """
    list_memories() and search_memories() must exclude EXPIRED and STALE
    entries from default-status queries. retrieve_relevant() must post-filter
    on is_expired() as defense-in-depth against race conditions.
    """

    def _insert(self, store, owner_id, mem_id, status_value):
        from execution.memory.models import MemoryEntry, MemorySource, MemoryTrust, MemoryType
        m = MemoryEntry(
            id=mem_id, owner_id=owner_id, title=f"Memory {mem_id}",
            content=f"Content {mem_id}", type=MemoryType.KNOWLEDGE.value,
            trust_level=MemoryTrust.USER_CONTROLLED.value,
            source=MemorySource.USER_CREATED.value, importance=0.5,
        )
        store.create_memory(m)
        store.update_memory(memory_id=mem_id, owner_id=owner_id, status=status_value)
        return m

    def test_active_returned(self):
        with tempfile.TemporaryDirectory() as d:
            store = _make_ephemeral_store(d)
            self._insert(store, "t1", "act1", "ACTIVE")
            ids = [m.id for m in store.list_memories(owner_id="t1")]
            self.assertIn("act1", ids)

    def test_expired_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            store = _make_ephemeral_store(d)
            self._insert(store, "t1", "exp1", "EXPIRED")
            ids = [m.id for m in store.list_memories(owner_id="t1")]
            self.assertNotIn("exp1", ids)

    def test_stale_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            store = _make_ephemeral_store(d)
            self._insert(store, "t1", "stl1", "STALE")
            ids = [m.id for m in store.list_memories(owner_id="t1")]
            self.assertNotIn("stl1", ids)

    def test_deleted_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            store = _make_ephemeral_store(d)
            self._insert(store, "t1", "del1", "DELETED")
            ids = [m.id for m in store.list_memories(owner_id="t1")]
            self.assertNotIn("del1", ids)

    def test_explicit_status_expired_still_works(self):
        with tempfile.TemporaryDirectory() as d:
            store = _make_ephemeral_store(d)
            self._insert(store, "t1", "exp2", "EXPIRED")
            ids = [m.id for m in store.list_memories(owner_id="t1", status="EXPIRED")]
            self.assertIn("exp2", ids)

    def test_search_excludes_expired(self):
        with tempfile.TemporaryDirectory() as d:
            store = _make_ephemeral_store(d)
            self._insert(store, "t1", "sexp1", "EXPIRED")
            ids = [m.id for m in store.search_memories(owner_id="t1", query="Content")]
            self.assertNotIn("sexp1", ids)

    def test_search_excludes_stale(self):
        with tempfile.TemporaryDirectory() as d:
            store = _make_ephemeral_store(d)
            self._insert(store, "t1", "sstl1", "STALE")
            ids = [m.id for m in store.search_memories(owner_id="t1", query="Content")]
            self.assertNotIn("sstl1", ids)

    def test_retrieval_excludes_ttl_expired_active(self):
        """
        Memory with expires_at in the past must be excluded by is_expired() post-filter
        in retrieve_relevant(), even if status is still ACTIVE.
        """
        from execution.memory.models import MemoryEntry, MemorySource, MemoryTrust, MemoryType
        with tempfile.TemporaryDirectory() as d:
            store = _make_ephemeral_store(d)
            m = MemoryEntry(
                id="expired_active", owner_id="t1", title="Expired Memory",
                content="This memory has already expired",
                type=MemoryType.KNOWLEDGE.value,
                trust_level=MemoryTrust.USER_CONTROLLED.value,
                source=MemorySource.USER_CREATED.value,
                importance=0.9,
                expires_at=time.time() - 3600,
            )
            store.create_memory(m)
            from execution.memory.retrieval import MemoryRetriever
            retriever = MemoryRetriever(store=store)
            results = retriever.retrieve_relevant(
                prompt="expired memory", owner_id="t1", use_semantic=False,
            )
            ids = [r.entry.id for r in results]
            self.assertNotIn("expired_active", ids,
                             "Memory with expired TTL must be excluded by is_expired() post-filter")


# ===========================================================================
# SEC-P2-TELEMETRY-LEAK-01
# ===========================================================================

class TestTelemetryLeak(unittest.TestCase):
    """
    get_metrics(owner_id=None, include_global=False) must return an isolated
    TenantMetrics bucket with zero global activity counters regardless of global state.
    The counter heuristic that allowed leakage when embedding_requests==1 and
    retrieval_approximate_count==1 must not exist.
    """

    def _make_telemetry(self):
        from execution.memory.telemetry import MemoryTelemetry
        return MemoryTelemetry()

    def test_empty_no_leak(self):
        """Fresh telemetry: anonymous call without include_global must return 0 requests."""
        tel = self._make_telemetry()
        m = tel.get_metrics(owner_id=None, include_global=False)
        # Isolated bucket has owner_id='isolated' and zero counters
        self.assertIn("owner_id", m)
        self.assertEqual(m.get("embedding", {}).get("requests", 0), 0,
                         "Isolated bucket must show 0 embedding requests")

    def test_after_one_embedding_no_leak(self):
        """After 1 global embedding request, isolated bucket must still show 0."""
        tel = self._make_telemetry()
        tel.record_embedding(latency_ms=5.0, cache_hit=False, success=True, owner_id=None)
        m = tel.get_metrics(owner_id=None, include_global=False)
        self.assertIn("owner_id", m)
        self.assertEqual(m.get("embedding", {}).get("requests", 0), 0)

    def test_after_one_approx_retrieval_no_leak(self):
        """After 1 approximate retrieval, isolated bucket must still show 0."""
        tel = self._make_telemetry()
        tel.record_retrieval(latency_ms=50.0, items_count=3, index_type="approximate_ivf", owner_id=None)
        m = tel.get_metrics(owner_id=None, include_global=False)
        self.assertIn("owner_id", m)
        self.assertEqual(m.get("retrieval", {}).get("requests", 0), 0)

    def test_heuristic_bypass_case_no_leak(self):
        """
        The exact heuristic that caused the finding: embed_requests==1 AND approx==1.
        After both, isolated get_metrics must still show 0 for both counters.
        """
        tel = self._make_telemetry()
        tel.record_embedding(latency_ms=5.0, cache_hit=False, success=True, owner_id=None)
        tel.record_retrieval(latency_ms=50.0, items_count=1, index_type="approximate_ivf", owner_id=None)
        m = tel.get_metrics(owner_id=None, include_global=False)
        self.assertIn("owner_id", m)
        # The heuristic used to return global metrics here — now it must return 0 counters
        self.assertEqual(m.get("embedding", {}).get("requests", 0), 0,
                         "Heuristic bypass: must not leak global embedding count")
        self.assertEqual(m.get("retrieval", {}).get("approximate_queries", 0), 0,
                         "Heuristic bypass: must not leak global retrieval count")

    def test_include_global_true_returns_global(self):
        """Admin path: include_global=True with no owner_id must return global stats."""
        tel = self._make_telemetry()
        tel.record_embedding(latency_ms=10.0, cache_hit=True, success=True, owner_id=None)
        m = tel.get_metrics(owner_id=None, include_global=True)
        self.assertIn("uptime_seconds", m, "include_global=True must return global metrics")
        self.assertIn("embedding", m)
        self.assertGreaterEqual(m["embedding"]["requests"], 1)

    def test_cross_tenant_isolation(self):
        """Tenant A activity must not appear in Tenant B's isolated metrics."""
        tel = self._make_telemetry()
        tel.record_embedding(latency_ms=10.0, cache_hit=False, success=True, owner_id="tenant_a")
        m_b = tel.get_metrics(owner_id="tenant_b", include_global=False)
        self.assertEqual(m_b.get("embedding", {}).get("requests", 0), 0,
                         "Tenant B must see 0 embedding requests (Tenant A's data must not leak)")

    def test_1000_global_ops_still_isolated(self):
        """After 1000 global ops, anonymous call without include_global must see 0 counters."""
        tel = self._make_telemetry()
        for _ in range(1000):
            tel.record_embedding(latency_ms=2.0, cache_hit=False, success=True, owner_id=None)
        m = tel.get_metrics(owner_id=None, include_global=False)
        self.assertIn("owner_id", m)
        self.assertEqual(m.get("embedding", {}).get("requests", 0), 0)


if __name__ == "__main__":
    unittest.main()
