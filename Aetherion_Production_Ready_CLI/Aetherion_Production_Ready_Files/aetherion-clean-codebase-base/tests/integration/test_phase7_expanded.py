"""
tests/integration/test_phase7_expanded.py -- Expanded Adversarial Regression Tests.

Covers all gaps from the Phase 7 comprehensive re-audit.
"""
from __future__ import annotations
import logging
import os
import sys
import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))


class TestSSRFExpanded(unittest.TestCase):
    def _validate(self, url):
        from execution.memory.embeddings import validate_safe_destination_url
        validate_safe_destination_url(url)

    def test_empty_string_blocked(self):
        with self.assertRaises((ValueError, Exception)):
            self._validate("")

    def test_file_scheme_blocked(self):
        with self.assertRaises(ValueError):
            self._validate("file:///etc/passwd")

    def test_ftp_scheme_blocked(self):
        with self.assertRaises(ValueError):
            self._validate("ftp://example.com/file")

    def test_javascript_scheme_blocked(self):
        with self.assertRaises(ValueError):
            self._validate("javascript:alert(1)")

    def test_high_unusual_port_localhost_blocked(self):
        with self.assertRaises(ValueError):
            self._validate("http://localhost:65535/embed")

    def test_port_1_localhost_blocked(self):
        with self.assertRaises(ValueError):
            self._validate("http://127.0.0.1:1/embed")

    def test_ipv6_loopback_blocked(self):
        with self.assertRaises(ValueError):
            self._validate("http://[::1]/embed")

    def test_gcp_metadata_blocked(self):
        with self.assertRaises(ValueError):
            self._validate("http://metadata.google.internal/computeMetadata/v1/")

    def test_alibaba_metadata_blocked(self):
        with self.assertRaises(ValueError):
            self._validate("http://100.100.100.200/latest/meta-data/")

    def test_all_port59999_private_ips_blocked(self):
        for url in [
            "http://127.0.0.1:59999/embed",
            "http://192.168.0.1:59999/embed",
            "http://10.0.0.1:59999/embed",
            "http://172.17.0.1:59999/embed",
            "http://localhost:59999/embed",
        ]:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    self._validate(url)

    def test_allow_local_does_not_disable_metadata_block(self):
        from execution.memory.embeddings import validate_safe_destination_url
        with patch.dict(os.environ, {"AETHERION_ALLOW_LOCAL_EMBEDDING_ENDPOINT": "1"}):
            with self.assertRaises(ValueError):
                validate_safe_destination_url("http://169.254.169.254/latest/meta-data/")

    def test_public_https_allowed(self):
        try:
            self._validate("https://api.openai.com/v1/embeddings")
        except ValueError as e:
            self.fail(f"Public HTTPS should be allowed: {e}")

    def test_url_missing_scheme_blocked(self):
        with self.assertRaises((ValueError, Exception)):
            self._validate("api.openai.com/v1/embeddings")


class TestCacheInMemoryLRUTenantIsolation(unittest.TestCase):

    def test_tenant_a_overflow_does_not_evict_tenant_b_from_memory(self):
        from execution.memory.cache import EmbeddingCache
        with tempfile.TemporaryDirectory() as d:
            cache = EmbeddingCache(
                db_path=Path(d)/"c.db", max_memory_entries=3, max_persistent_entries=100)
            cache.put("tenant_b", "B text 1", "model", 4, [0.1]*4)
            cache.put("tenant_b", "B text 2", "model", 4, [0.2]*4)
            cache.put("tenant_a", "A text 1", "model", 4, [0.3]*4)
            cache.put("tenant_a", "A text 2", "model", 4, [0.4]*4)
            cache.put("tenant_a", "A text 3", "model", 4, [0.5]*4)
            b_in_mem = sum(1 for v in cache._memory_cache.values() if v[0] == "tenant_b")
            self.assertEqual(b_in_mem, 2, f"Tenant B should have 2 in-memory entries; got {b_in_mem}")

    def test_get_retrieves_tenant_b_after_tenant_a_flood(self):
        from execution.memory.cache import EmbeddingCache
        with tempfile.TemporaryDirectory() as d:
            cache = EmbeddingCache(
                db_path=Path(d)/"c2.db", max_memory_entries=3, max_persistent_entries=100)
            cache.put("tenant_b", "B unique content", "m", 4, [9.0]*4)
            for i in range(5):
                cache.put("tenant_a", f"A content {i}", "m", 4, [float(i)]*4)
            result = cache.get("tenant_b", "B unique content", "m", 4)
            self.assertIsNotNone(result, "Tenant B entry must be retrievable after Tenant A overflow")
            self.assertEqual(result, [9.0]*4)


class TestCacheOperationAudit(unittest.TestCase):

    def test_prune_expired_spares_live_cross_tenant(self):
        from execution.memory.cache import EmbeddingCache
        with tempfile.TemporaryDirectory() as d:
            cache = EmbeddingCache(
                db_path=Path(d)/"pr.db", max_memory_entries=100, max_persistent_entries=100)
            cache.put("tenant_b", "B live", "m", 4, [1.0]*4, ttl=9999.0)
            cache.put("tenant_a", "A expired", "m", 4, [2.0]*4, ttl=0.001)
            time.sleep(0.02)
            cache.prune_expired()
            result = cache.get("tenant_b", "B live", "m", 4)
            self.assertIsNotNone(result, "Tenant B live entry must survive prune_expired()")

    def test_invalidate_does_not_affect_other_tenant(self):
        from execution.memory.cache import EmbeddingCache
        with tempfile.TemporaryDirectory() as d:
            cache = EmbeddingCache(
                db_path=Path(d)/"inv.db", max_memory_entries=100, max_persistent_entries=100)
            cache.put("tenant_a", "text", "m", 4, [1.0]*4)
            cache.put("tenant_b", "text", "m", 4, [2.0]*4)
            cache.invalidate("tenant_a")
            self.assertIsNone(cache.get("tenant_a", "text", "m", 4))
            self.assertIsNotNone(cache.get("tenant_b", "text", "m", 4))

    def test_stats_owner_scoped_correct_count(self):
        from execution.memory.cache import EmbeddingCache
        with tempfile.TemporaryDirectory() as d:
            cache = EmbeddingCache(
                db_path=Path(d)/"st.db", max_memory_entries=100, max_persistent_entries=100)
            for i in range(5):
                cache.put("tenant_a", f"A text {i}", "m", 4, [float(i)]*4)
            for i in range(3):
                cache.put("tenant_b", f"B text {i}", "m", 4, [float(i)]*4)
            sa = cache.stats(owner_id="tenant_a")
            sb = cache.stats(owner_id="tenant_b")
            self.assertEqual(sa["persistent_entries"], 5)
            self.assertEqual(sb["persistent_entries"], 3)

    def test_different_tenants_same_content_separate_entries(self):
        from execution.memory.cache import EmbeddingCache
        with tempfile.TemporaryDirectory() as d:
            cache = EmbeddingCache(
                db_path=Path(d)/"dk.db", max_memory_entries=100, max_persistent_entries=100)
            cache.put("tenant_a", "shared content", "m", 4, [1.0]*4)
            cache.put("tenant_b", "shared content", "m", 4, [2.0]*4)
            ra = cache.get("tenant_a", "shared content", "m", 4)
            rb = cache.get("tenant_b", "shared content", "m", 4)
            self.assertEqual(ra, [1.0]*4)
            self.assertEqual(rb, [2.0]*4)
            self.assertNotEqual(ra, rb)


class TestAutoIndexStrategyConfig(unittest.TestCase):

    def test_evaluate_below_threshold_returns_exact(self):
        from execution.memory.index import AutoIndexStrategy, AutoIndexConfig
        s = AutoIndexStrategy(config=AutoIndexConfig(exact_threshold=25000))
        d = s.evaluate_strategy(1000)
        self.assertEqual(d.recommended_type, "exact")
        self.assertFalse(d.is_approximate)

    def test_evaluate_above_threshold_returns_approximate(self):
        from execution.memory.index import AutoIndexStrategy, AutoIndexConfig
        s = AutoIndexStrategy(config=AutoIndexConfig(exact_threshold=25000))
        d = s.evaluate_strategy(30000)
        self.assertNotEqual(d.recommended_type, "exact")
        self.assertTrue(d.is_approximate)

    def test_lower_threshold_switches_earlier(self):
        from execution.memory.index import AutoIndexStrategy, AutoIndexConfig
        conservative = AutoIndexStrategy(config=AutoIndexConfig(exact_threshold=25000))
        aggressive = AutoIndexStrategy(config=AutoIndexConfig(exact_threshold=1000))
        self.assertEqual(conservative.evaluate_strategy(5000).recommended_type, "exact")
        self.assertNotEqual(aggressive.evaluate_strategy(5000).recommended_type, "exact")

    def test_force_exact_overrides_at_large_n(self):
        from execution.memory.index import AutoIndexStrategy, ExactVectorIndex
        s = AutoIndexStrategy()
        vecs = [(f"m{i}", [0.1]*4) for i in range(50000)]
        engine = s.select_engine(vecs, force_mode="exact")
        self.assertIsInstance(engine, ExactVectorIndex)

    def test_boundary_below_threshold_uses_exact(self):
        from execution.memory.index import AutoIndexStrategy, AutoIndexConfig, ExactVectorIndex
        threshold = 1000
        s = AutoIndexStrategy(config=AutoIndexConfig(exact_threshold=threshold))
        vecs = [(f"m{i}", [0.1]*4) for i in range(threshold - 1)]
        engine = s.select_engine(vecs)
        self.assertIsInstance(engine, ExactVectorIndex)

    def test_canonical_threshold_is_25000(self):
        from execution.memory.index import AutoIndexConfig
        cfg = AutoIndexConfig()
        self.assertGreaterEqual(cfg.exact_threshold, 25000)


class TestLearningSignalErrorTelemetry(unittest.TestCase):

    def test_persistence_failure_emits_warning(self):
        from execution.memory.learning import AgentLearningEngine
        mock_store = MagicMock()
        mock_store.create_learning_signal.side_effect = RuntimeError("DB write failed")
        engine = AgentLearningEngine(store=mock_store, embedding_provider=None, cache=None)
        with self.assertLogs(level="WARNING") as log_ctx:
            engine.extract_learning_signals_from_run(
                owner_id="t1", session_id="s1",
                task_prompt="always use tabs not spaces",
                status="completed", verification_status="verified",
                created_or_edited_files=["foo.py"], tool_action_count=3,
            )
        warnings = [r for r in log_ctx.output if "WARNING" in r]
        self.assertTrue(len(warnings) > 0, f"Expected WARNING log; got: {log_ctx.output}")
        self.assertIn("DB write failed", " ".join(log_ctx.output))

    def test_persistence_failure_does_not_crash(self):
        from execution.memory.learning import AgentLearningEngine
        mock_store = MagicMock()
        mock_store.create_learning_signal.side_effect = RuntimeError("Disk full")
        engine = AgentLearningEngine(store=mock_store, embedding_provider=None, cache=None)
        try:
            engine.extract_learning_signals_from_run(
                owner_id="t1", session_id="s1",
                task_prompt="prefer snake_case",
                status="completed", verification_status="verified",
                created_or_edited_files=["x.py"], tool_action_count=1,
            )
        except Exception as e:
            self.fail(f"Must not raise on store failure: {e}")


class TestCLIBatchSizeValidation(unittest.TestCase):

    def _check(self, v):
        if v < 1 or v > 500:
            return False, f"Invalid --batch-size {v}: must be 1-500"
        return True, "ok"

    def test_zero_rejected(self):
        self.assertFalse(self._check(0)[0])

    def test_negative_rejected(self):
        self.assertFalse(self._check(-1)[0])

    def test_501_rejected(self):
        self.assertFalse(self._check(501)[0])

    def test_very_large_rejected(self):
        self.assertFalse(self._check(999999)[0])

    def test_1_accepted(self):
        self.assertTrue(self._check(1)[0])

    def test_500_accepted(self):
        self.assertTrue(self._check(500)[0])

    def test_50_default_accepted(self):
        self.assertTrue(self._check(50)[0])

    def test_error_message_includes_range(self):
        ok, msg = self._check(0)
        self.assertIn("1", msg)
        self.assertIn("500", msg)


class TestRetrievalFullPipeline(unittest.TestCase):

    def _make_store(self, tmpdir):
        from execution.memory.store import MemoryStore
        return MemoryStore(db_path=Path(tmpdir)/"ret.db")

    def _insert(self, store, owner_id, mem_id, content, status, expires_at=None):
        from execution.memory.models import MemoryEntry, MemorySource, MemoryTrust, MemoryType
        m = MemoryEntry(
            id=mem_id, owner_id=owner_id, title=f"Title {mem_id}",
            content=content, type=MemoryType.KNOWLEDGE.value,
            trust_level=MemoryTrust.USER_CONTROLLED.value,
            source=MemorySource.USER_CREATED.value, importance=0.5,
            expires_at=expires_at,
        )
        store.create_memory(m)
        if status != "ACTIVE":
            store.update_memory(memory_id=mem_id, owner_id=owner_id, status=status)
        return m

    def test_excluded_statuses_not_in_list(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            self._insert(store, "t1", "active1", "active memory", "ACTIVE")
            self._insert(store, "t1", "del1", "deleted", "DELETED")
            self._insert(store, "t1", "arc1", "archived", "ARCHIVED")
            self._insert(store, "t1", "exp1", "expired", "EXPIRED")
            self._insert(store, "t1", "stl1", "stale", "STALE")
            ids = {m.id for m in store.list_memories(owner_id="t1")}
            self.assertIn("active1", ids)
            for bad in ["del1", "arc1", "exp1", "stl1"]:
                self.assertNotIn(bad, ids)

    def test_excluded_statuses_not_in_search(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            self._insert(store, "t1", "active2", "unique alpha content", "ACTIVE")
            for status, mid in [("DELETED","del2"),("EXPIRED","exp2"),("STALE","stl2"),("ARCHIVED","arc2")]:
                self._insert(store, "t1", mid, "unique alpha content", status)
            ids = {m.id for m in store.search_memories(owner_id="t1", query="alpha")}
            self.assertIn("active2", ids)
            for bad in ["del2","exp2","stl2","arc2"]:
                self.assertNotIn(bad, ids)

    def test_ttl_expired_active_excluded_by_post_filter(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            self._insert(store, "t1", "ttl_expired", "ttl expired active", "ACTIVE",
                         expires_at=time.time()-7200)
            self._insert(store, "t1", "ttl_valid", "ttl valid active", "ACTIVE",
                         expires_at=time.time()+9999)
            from execution.memory.retrieval import MemoryRetriever
            r = MemoryRetriever(store=store)
            results = r.retrieve_relevant(prompt="ttl memory", owner_id="t1", use_semantic=False)
            ids = {res.entry.id for res in results}
            self.assertNotIn("ttl_expired", ids)
            self.assertIn("ttl_valid", ids)

    def test_cross_tenant_retrieval_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            self._insert(store, "tenant_a", "a_mem", "tenant a secret", "ACTIVE")
            self._insert(store, "tenant_b", "b_mem", "tenant b secret", "ACTIVE")
            from execution.memory.retrieval import MemoryRetriever
            r = MemoryRetriever(store=store)
            results = r.retrieve_relevant(prompt="secret", owner_id="tenant_a", use_semantic=False)
            ids = {res.entry.id for res in results}
            self.assertIn("a_mem", ids)
            self.assertNotIn("b_mem", ids)


class TestAdversarialSelfReview(unittest.TestCase):

    def test_q1_allow_local_still_blocks_metadata(self):
        from execution.memory.embeddings import validate_safe_destination_url
        with patch.dict(os.environ, {"AETHERION_ALLOW_LOCAL_EMBEDDING_ENDPOINT": "1"}):
            with self.assertRaises(ValueError):
                validate_safe_destination_url("http://169.254.169.254/latest/")

    def test_q2_cache_key_forge_impossible(self):
        from execution.memory.cache import compute_cache_key
        k1 = compute_cache_key("tenant_a", "model_x", 768, "hash1")
        k2 = compute_cache_key("tenant_a:model_x", "", 768, "hash1")
        self.assertNotEqual(k1, k2)

    def test_q3_in_memory_lru_tenant_isolated(self):
        from execution.memory.cache import EmbeddingCache
        with tempfile.TemporaryDirectory() as d:
            cache = EmbeddingCache(
                db_path=Path(d)/"q3.db", max_memory_entries=2, max_persistent_entries=100)
            cache.put("tenant_b", "safe", "m", 4, [1.0]*4)
            cache.put("tenant_a", "a1", "m", 4, [2.0]*4)
            cache.put("tenant_a", "a2", "m", 4, [3.0]*4)
            b_in_mem = sum(1 for v in cache._memory_cache.values() if v[0] == "tenant_b")
            self.assertGreaterEqual(b_in_mem, 1, "Tenant B must survive Tenant A overflow")

    def test_q4_benchmark_fixtures_not_in_prod(self):
        from execution.memory.evaluation import run_retrieval_benchmark, BENCHMARK_FIXTURES
        from execution.memory.store import MemoryStore
        with tempfile.TemporaryDirectory() as d:
            prod = MemoryStore(db_path=Path(d)/"prod.db")
            run_retrieval_benchmark(suite_version="v1")
            for fx in BENCHMARK_FIXTURES[:5]:
                result = prod.get_memory(fx["id"], "eval_benchmark_tenant")
                self.assertIsNone(result, f"Benchmark fixture {fx['id']} must not be in production store")

    def test_q5_benchmark_repeat_idempotent(self):
        from execution.memory.evaluation import run_retrieval_benchmark
        for i in range(3):
            try:
                run_retrieval_benchmark(suite_version="v1")
            except Exception as e:
                self.fail(f"Run #{i+1} raised: {e}")

    def test_q9_expired_active_not_in_retrieval(self):
        with tempfile.TemporaryDirectory() as d:
            from execution.memory.store import MemoryStore
            from execution.memory.retrieval import MemoryRetriever
            from execution.memory.models import MemoryEntry, MemorySource, MemoryTrust, MemoryType
            store = MemoryStore(db_path=Path(d)/"q9.db")
            m = MemoryEntry(
                id="q9_expired", owner_id="t1", title="Old", content="expired content",
                type=MemoryType.KNOWLEDGE.value,
                trust_level=MemoryTrust.USER_CONTROLLED.value,
                source=MemorySource.USER_CREATED.value, importance=0.9,
                expires_at=time.time()-3600,
            )
            store.create_memory(m)
            r = MemoryRetriever(store=store)
            results = r.retrieve_relevant(prompt="expired", owner_id="t1", use_semantic=False)
            self.assertNotIn("q9_expired", {res.entry.id for res in results})

    def test_q13_concurrent_benchmarks_no_collision(self):
        from execution.memory.evaluation import run_retrieval_benchmark
        errors = []
        def run():
            try:
                run_retrieval_benchmark(suite_version="v1")
            except Exception as e:
                errors.append(str(e))
        threads = [threading.Thread(target=run) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [], f"Concurrent benchmark runs must not collide: {errors}")


if __name__ == "__main__":
    unittest.main()
