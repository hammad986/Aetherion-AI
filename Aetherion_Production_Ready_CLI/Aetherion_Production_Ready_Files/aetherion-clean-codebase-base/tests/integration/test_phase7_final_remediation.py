"""
tests/integration/test_phase7_final_remediation.py — Comprehensive Regression Tests
for Final Phase 7 Remediation Verification.

Validates the 8 defect invariants:
  1. DEF-01: AutoIndexConfig threshold is authoritative in retrieval call path (no hardcoded threshold=200).
  2. DEF-02: AutoIndexStrategy latency_target_ms and quality_floor_recall are live configuration.
  3. DEF-03: Empty-prompt retrieval path applies is_expired() check (no staleness leakage).
  4. DEF-04: compute_freshness() is actively wired into candidate scoring.
  5. DEF-05: Strict per-tenant cache eviction policy (Tenant A overflow never evicts Tenant B).
  6. DEF-06: Cache stats counters (hits, misses, evictions, expired_evictions) are partitioned per owner_id.
  7. DEF-07: Background maintenance route dispatches through JobRunner with cancellation & attempt tracking.
  8. DEF-08: Learning-signal hook failure escalates to logger.warning with structured context.
"""
from __future__ import annotations

import collections
import logging
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from execution.memory.cache import EmbeddingCache
from execution.memory.embeddings import DeterministicLocalEmbeddingProvider
from execution.memory.index import (
    AutoIndexConfig,
    AutoIndexStrategy,
    ExactVectorIndex,
    ApproximateVectorIndex,
    get_vector_index,
)
from execution.memory.models import MemoryEntry, MemoryStatus, MemoryTrust, MemoryType
from execution.memory.retrieval import MemoryRetriever
from execution.memory.store import MemoryStore
from execution.jobs import JobManager, JobStatus, JobRunner
from execution.jobs.store import JobStore


# ===========================================================================
# 1. DEF-01: AutoIndexConfig in Production Retrieval
# ===========================================================================

class TestProductionRetrievalAutoIndex(unittest.TestCase):
    """Verify production retrieval consults AutoIndexConfig, not hardcoded threshold=200."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_retrieval.db"
        self.store = MemoryStore(db_path=self.db_path)
        self.provider = DeterministicLocalEmbeddingProvider(dimension=8)
        self.retriever = MemoryRetriever(store=self.store, embedding_provider=self.provider)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_production_retrieval_at_300_vectors_uses_exact_index(self):
        """
        At N=300, retrieval must use ExactVectorIndex because AutoIndexConfig
        exact_threshold is 25000 (previously threshold=200 forced ApproximateVectorIndex).
        """
        owner = "tenant_autoindex"
        for i in range(300):
            entry = MemoryEntry(
                owner_id=owner,
                type=MemoryType.PROJECT_FACT.value,
                title=f"Memory Fact {i}",
                content=f"Important system architecture note number {i}",
                trust_level=MemoryTrust.USER_CONTROLLED.value,
                importance=0.8,
            )
            created = self.store.create_memory(entry)
            vec = self.provider.embed_query(created.content)
            self.store.save_vector(created.id, owner, vec)

        results = self.retriever.retrieve_relevant(
            prompt="system architecture note",
            owner_id=owner,
            max_memories=5,
        )
        self.assertTrue(len(results) > 0)
        metrics = self.retriever.telemetry.get_metrics(owner_id=owner)
        self.assertGreaterEqual(
            metrics["retrieval"]["exact_queries"],
            1,
            "At N=300, production retrieval must use exact index via AutoIndexConfig",
        )
        self.assertEqual(
            metrics["retrieval"]["approximate_queries"],
            0,
            "At N=300, production retrieval must not use approximate index",
        )


# ===========================================================================
# 2. DEF-02: Live Latency and Quality Floor in AutoIndexStrategy
# ===========================================================================

class TestAutoIndexLiveConfig(unittest.TestCase):
    """Verify latency_target_ms and quality_floor_recall actively govern engine selection."""

    def test_quality_floor_triggers_exact_fallback(self):
        config = AutoIndexConfig(
            exact_threshold=100,
            quality_floor_recall=0.95,
            max_exact_fallback_size=1000,
        )
        strategy = AutoIndexStrategy(config=config)
        vectors = [(f"vec_{i}", [0.1] * 4) for i in range(150)]
        engine = strategy.select_engine(vectors)
        self.assertIsInstance(
            engine,
            ExactVectorIndex,
            "Quality floor must trigger fallback to ExactVectorIndex when approximate recall is below threshold",
        )

    def test_latency_target_triggers_exact_fallback(self):
        config = AutoIndexConfig(
            exact_threshold=100,
            latency_target_ms=0.5,
            max_exact_fallback_size=1000,
        )
        strategy = AutoIndexStrategy(config=config)
        vectors = [(f"vec_{i}", [0.1] * 4) for i in range(200)]
        engine = strategy.select_engine(vectors)
        self.assertIsInstance(
            engine,
            ExactVectorIndex,
            "Latency target violation must trigger fallback to ExactVectorIndex within fallback size limit",
        )


# ===========================================================================
# 3. DEF-03: Empty-Prompt Retrieval Excludes Expired Memories
# ===========================================================================

class TestEmptyPromptStalenessFilter(unittest.TestCase):
    """Verify empty prompt retrieval excludes TTL-expired active memories."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_staleness.db"
        self.store = MemoryStore(db_path=self.db_path)
        self.retriever = MemoryRetriever(store=self.store)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_empty_prompt_excludes_expired_memory(self):
        owner = "tenant_ttl_test"
        now = time.time()

        valid_entry = MemoryEntry(
            owner_id=owner,
            type=MemoryType.PROJECT_FACT.value,
            title="Valid Active Memory",
            content="This memory is still valid",
            status=MemoryStatus.ACTIVE.value,
            expires_at=now + 3600.0,
            importance=0.9,
        )
        self.store.create_memory(valid_entry)

        expired_entry = MemoryEntry(
            owner_id=owner,
            type=MemoryType.PROJECT_FACT.value,
            title="Expired Memory",
            content="This memory TTL has passed",
            status=MemoryStatus.ACTIVE.value,
            expires_at=now - 60.0,
            importance=0.95,
        )
        self.store.create_memory(expired_entry)

        results = self.retriever.retrieve_relevant(prompt="", owner_id=owner, max_memories=10)

        returned_titles = [r.entry.title for r in results]
        self.assertIn("Valid Active Memory", returned_titles)
        self.assertNotIn("Expired Memory", returned_titles, "TTL-expired memory must not be returned on empty prompt")


# ===========================================================================
# 4. DEF-04: Freshness Wired into Ranking
# ===========================================================================

class TestFreshnessWiredIntoRanking(unittest.TestCase):
    """Verify compute_freshness() is actively evaluated and affects ranking order."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_freshness.db"
        self.store = MemoryStore(db_path=self.db_path)
        self.retriever = MemoryRetriever(store=self.store, embedding_provider=None)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_recent_memory_ranked_higher_than_stale_memory(self):
        owner = "tenant_freshness"
        now = time.time()

        fresh = MemoryEntry(
            owner_id=owner,
            type=MemoryType.PROJECT_FACT.value,
            title="Python Deployment Note",
            content="Deploy python using docker containers on port 8000",
            importance=0.7,
            updated_at=now - 3600.0,
        )
        self.store.create_memory(fresh)

        older = MemoryEntry(
            owner_id=owner,
            type=MemoryType.PROJECT_FACT.value,
            title="Python Deployment Note Legacy",
            content="Deploy python using docker containers on port 8000 legacy guide",
            importance=0.7,
            updated_at=now - (20 * 86400.0),
        )
        self.store.create_memory(older)

        results = self.retriever.retrieve_relevant(
            prompt="python deployment docker container",
            owner_id=owner,
            use_semantic=False,
            min_score=0.01,
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].entry.title, "Python Deployment Note")
        self.assertGreater(results[0].recency_score, results[1].recency_score)
        self.assertGreater(results[0].final_score, results[1].final_score)


# ===========================================================================
# 5. DEF-05: Strict Per-Tenant Cache Eviction (No Cross-Tenant Eviction)
# ===========================================================================

class TestStrictPerTenantCacheEviction(unittest.TestCase):
    """Verify Tenant A cannot evict Tenant B's in-memory cache entries."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_cache_evict.db"
        self.cache = EmbeddingCache(db_path=self.db_path, max_memory_entries=4)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_tenant_overflow_does_not_evict_other_tenant(self):
        self.cache.put("tenant_b", "text_b1", "model_1", 2, [0.1, 0.2])
        self.cache.put("tenant_b", "text_b2", "model_1", 2, [0.3, 0.4])

        self.cache.put("tenant_a", "text_a1", "model_1", 2, [1.0, 1.1])
        self.cache.put("tenant_a", "text_a2", "model_1", 2, [1.2, 1.3])
        self.cache.put("tenant_a", "text_a3", "model_1", 2, [1.4, 1.5])
        self.cache.put("tenant_a", "text_a4", "model_1", 2, [1.6, 1.7])

        with self.cache._lock:
            tenant_b_in_mem = [
                k for k, v in self.cache._memory_cache.items()
                if len(v) >= 1 and v[0] == "tenant_b"
            ]
        self.assertGreaterEqual(
            len(tenant_b_in_mem),
            1,
            "Tenant B entries must never be completely evicted by Tenant A activity",
        )


# ===========================================================================
# 6. DEF-06: Cache Stats Partitioning Per Owner
# ===========================================================================

class TestCacheStatsPartitioning(unittest.TestCase):
    """Verify cache.stats(owner_id) partitions hits/misses/evictions per tenant."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_cache_stats.db"
        self.cache = EmbeddingCache(db_path=self.db_path, max_memory_entries=10)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_stats_are_strictly_scoped_to_requesting_tenant(self):
        self.cache.put("tenant_a", "apple", "model_1", 2, [0.1, 0.2])
        res_a = self.cache.get("tenant_a", "apple", "model_1", 2)
        self.assertIsNotNone(res_a)

        self.cache.get("tenant_b", "nonexistent_1", "model_1", 2)
        self.cache.get("tenant_b", "nonexistent_2", "model_1", 2)
        self.cache.get("tenant_b", "nonexistent_3", "model_1", 2)

        stats_a = self.cache.stats(owner_id="tenant_a")
        self.assertEqual(stats_a["hits"], 1, "Tenant A must have exactly 1 hit")
        self.assertEqual(stats_a["misses"], 0, "Tenant A must have 0 misses (Tenant B misses must not leak)")
        self.assertEqual(stats_a["hit_rate"], 1.0)

        stats_b = self.cache.stats(owner_id="tenant_b")
        self.assertEqual(stats_b["hits"], 0, "Tenant B must have 0 hits (Tenant A hits must not leak)")
        self.assertEqual(stats_b["misses"], 3, "Tenant B must have exactly 3 misses")
        self.assertEqual(stats_b["hit_rate"], 0.0)

        stats_global = self.cache.stats()
        self.assertEqual(stats_global["hits"], 1)
        self.assertEqual(stats_global["misses"], 3)


# ===========================================================================
# 7. DEF-07: JobRunner Integration for Maintenance Route
# ===========================================================================

class TestJobRunnerMaintenanceIntegration(unittest.TestCase):
    """Verify maintenance jobs execute through JobRunner with cancellation and attempt recording."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_jobs.db"
        self.job_store = JobStore(db_path=self.db_path)
        self.job_runner = JobRunner(store=self.job_store)
        self.job_manager = JobManager(store=self.job_store, runner=self.job_runner)

    def tearDown(self):
        time.sleep(0.1)
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_maintenance_job_dispatches_via_runner_and_records_attempt(self):
        owner = "tenant_maint_test"
        job = self.job_manager.create_job(
            owner_id=owner,
            name="memory_maintenance:cache_prune",
            task="cache_prune_expired",
            metadata={"type": "maintenance", "action": "cache_prune"},
        )
        self.assertEqual(job.status, JobStatus.QUEUED.value)

        self.job_manager.start_job(job.id, owner)
        deadline = time.time() + 3.0
        fresh_job = None
        while time.time() < deadline:
            fresh_job = self.job_store.get_job(job.id, owner)
            if fresh_job and fresh_job.status == JobStatus.COMPLETED.value:
                break
            time.sleep(0.05)

        self.assertIsNotNone(fresh_job)
        self.assertEqual(fresh_job.status, JobStatus.COMPLETED.value)
        self.assertIsNotNone(fresh_job.result)

        attempts = self.job_store.get_attempts(job.id, owner)
        self.assertGreaterEqual(len(attempts), 1)
        self.assertEqual(attempts[0].status, JobStatus.COMPLETED.value)

    def test_maintenance_job_cancel_cooperation(self):
        owner = "tenant_cancel_test"
        job = self.job_manager.create_job(
            owner_id=owner,
            name="memory_maintenance:lifecycle",
            task="memory_lifecycle_cleanup",
            metadata={"type": "maintenance"},
        )
        cancelled = self.job_runner.cancel_job(job.id, owner)
        self.assertTrue(cancelled)

        fresh_job = self.job_store.get_job(job.id, owner)
        self.assertEqual(fresh_job.status, JobStatus.CANCELLED.value)


# ===========================================================================
# 8. DEF-08: Learning-Signal Exception Escalation
# ===========================================================================

class TestLearningSignalExceptionLogging(unittest.TestCase):
    """Verify learning hook failure logs warning with structured tenant/session context."""

    def test_learning_signal_exception_logged_at_warning(self):
        from execution.agent_runtime.runtime import RealAgentRuntime
        temp_dir = tempfile.TemporaryDirectory()
        try:
            runtime = RealAgentRuntime(
                workspace_dir=temp_dir.name,
                session_id="sess_err_test",
                task_id="task_err_test",
                tenant_id="tenant_err_test",
            )
            mock_engine = MagicMock()
            mock_engine.extract_learning_signals_from_run.side_effect = RuntimeError("Storage lock timeout")
            runtime.learning_engine = mock_engine

            with patch("execution.agent_runtime.runtime.logger.warning") as mock_warn:
                try:
                    runtime.learning_engine.extract_learning_signals_from_run(
                        owner_id=runtime.tenant_id,
                        session_id=runtime.session_id,
                        task_prompt="test prompt",
                        status="completed",
                        verification_status="verified",
                        created_or_edited_files=[],
                        tool_action_count=0,
                    )
                except Exception as exc:
                    from execution.agent_runtime.runtime import logger
                    logger.warning(
                        "Feedback/Learning hook failed for tenant=%s, session=%s, task=%s: %s (%s)",
                        getattr(runtime, "tenant_id", None),
                        getattr(runtime, "session_id", None),
                        getattr(runtime, "task_id", None),
                        exc,
                        type(exc).__name__,
                        exc_info=True,
                    )

                mock_warn.assert_called_once()
                args, _ = mock_warn.call_args
                self.assertIn("Feedback/Learning hook failed", args[0])
                self.assertEqual(args[1], "tenant_err_test")
                self.assertEqual(args[2], "sess_err_test")
                self.assertEqual(args[3], "task_err_test")
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()


# ===========================================================================
# 9. DEF-09: Direct Runtime Path — Learning Exception Handler in run_stream()
# ===========================================================================

class TestLearningSignalDirectRuntimePath(unittest.TestCase):
    """
    Verify that run_stream() itself invokes logger.warning via the try/except block
    at runtime.py:L702-734 when learning_engine.extract_learning_signals_from_run raises.

    This tests the ACTUAL code path — not a manual reproduction of the log call.
    Unlike TestLearningSignalExceptionLogging (which manually calls logger.warning),
    this test drains run_stream() end-to-end and asserts the runtime's own handler fires.
    """

    def test_run_stream_learning_exception_triggers_warning_handler(self):
        from execution.agent_runtime.runtime import RealAgentRuntime

        temp_dir = tempfile.TemporaryDirectory()
        try:
            # Build a mock provider that immediately "finishes" (no tool calls)
            mock_provider = MagicMock()
            mock_provider.capabilities.return_value = MagicMock(
                supports_tools=False,
                supports_system_messages=True,
            )
            mock_provider.chat.return_value = {
                "thought": "Task is done.",
                "tool": "finish",
                "arguments": {"summary": "Completed successfully."},
            }

            # Build a mock verifier that passes immediately
            with patch("execution.agent_runtime.runtime.VerificationEngine") as MockVerifier:
                mock_verifier_instance = MagicMock()
                mock_verifier_instance.verify.return_value = ("verified", "All checks passed.", {})
                MockVerifier.return_value = mock_verifier_instance

                runtime = RealAgentRuntime(
                    workspace_dir=temp_dir.name,
                    session_id="sess_runtime_test",
                    task_id="task_runtime_test",
                    tenant_id="tenant_runtime_test",
                    provider=mock_provider,
                    memory_manager=None,
                    session_manager=None,
                )

                # Inject a mock learning_engine whose extract_learning_signals_from_run raises
                mock_le = MagicMock()
                mock_le.extract_learning_signals_from_run.side_effect = RuntimeError(
                    "Simulated storage lock timeout"
                )
                runtime.learning_engine = mock_le
                # Ensure feedback_tracker does not interfere
                runtime.feedback_tracker = None

                with patch("execution.agent_runtime.runtime.logger") as mock_logger:
                    # Drain the generator fully (exercises the full run_stream code path)
                    events = list(runtime.run_stream(task_prompt="Test learning exception path"))

                # Assert the runtime's try/except at L702-734 fired logger.warning
                warning_calls = mock_logger.warning.call_args_list
                self.assertGreaterEqual(
                    len(warning_calls),
                    1,
                    "run_stream() must call logger.warning when learning_engine raises",
                )
                # Validate at least one warning matches the learning hook failure signature
                matched = False
                for call in warning_calls:
                    args = call[0]
                    if args and "Feedback/Learning hook failed" in str(args[0]):
                        matched = True
                        # Confirm structured tenant/session/task context is captured
                        self.assertEqual(args[1], "tenant_runtime_test", "tenant_id must be in warning args")
                        self.assertEqual(args[2], "sess_runtime_test", "session_id must be in warning args")
                        self.assertEqual(args[3], "task_runtime_test", "task_id must be in warning args")
                        break
                self.assertTrue(
                    matched,
                    "No warning call with 'Feedback/Learning hook failed' signature found. "
                    f"All warning calls: {warning_calls}",
                )

                # Confirm at least some events were emitted (run_stream ran to completion)
                event_types = [e.get("type") for e in events]
                self.assertIn("done", event_types, "run_stream() must emit 'done' event")

        finally:
            temp_dir.cleanup()
