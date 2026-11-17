"""
backend/execution/memory/telemetry.py — Secret-Safe Observability & Multi-Tenant Telemetry for Phase 7.
Tracks operational metrics, latencies, circuit breaker health, and memory intelligence signals
(contradictions, consolidations, learning signals, outcome feedback) without leaking secrets.
Enforces strict tenant isolation: ordinary tenants can only access their own metrics.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

try:
    from ..sessions.manager import redact_secrets
except Exception:
    try:
        from execution.sessions.manager import redact_secrets
    except Exception:
        def redact_secrets(s: str) -> str:
            return s

logger = logging.getLogger("aetherion.memory.telemetry")


class TenantMetrics:
    """Internal counter bucket for a single tenant."""

    def __init__(self, owner_id: str):
        self.owner_id = owner_id
        self._started_at = time.time()
        self.embedding_requests = 0
        self.embedding_cache_hits = 0
        self.embedding_cache_misses = 0
        self.embedding_failures = 0
        self.embedding_retries = 0
        self.embedding_time_total_ms = 0.0

        self.retrieval_requests = 0
        self.retrieval_exact_count = 0
        self.retrieval_approximate_count = 0
        self.retrieval_time_total_ms = 0.0
        self.retrieved_items_total = 0

        self.circuit_trips = 0
        self.degraded_lexical_fallbacks = 0

        # Phase 7 Intelligence Metrics
        self.contradictions_detected = 0
        self.contradictions_resolved = 0
        self.consolidations_proposed = 0
        self.consolidations_applied = 0
        self.learning_signals_extracted = 0
        self.feedback_count = 0
        self.feedback_success = 0
        self.feedback_failure = 0

        # Phase 8 Cache Observability & Shadow Calibration Metrics
        self.cache_protection_drops = 0
        self.cache_admission_denials = 0
        self.cache_invalidations = 0
        self.cache_expired_evictions = 0
        self.shadow_queries_sampled = 0
        self.shadow_queries_completed = 0
        self.last_shadow_recall = 0.0

    def record_embedding(
        self,
        latency_ms: float,
        cache_hit: bool,
        success: bool = True,
        retries: int = 0,
    ) -> None:
        self.embedding_requests += 1
        if cache_hit:
            self.embedding_cache_hits += 1
        else:
            self.embedding_cache_misses += 1

        if not success:
            self.embedding_failures += 1
        self.embedding_retries += retries
        self.embedding_time_total_ms += max(0.0, latency_ms)

    def record_retrieval(
        self,
        latency_ms: float,
        items_count: int,
        index_type: str = "exact",
        fallback_used: bool = False,
    ) -> None:
        self.retrieval_requests += 1
        self.retrieval_time_total_ms += max(0.0, latency_ms)
        self.retrieved_items_total += items_count
        if index_type == "approximate_ivf" or index_type == "native_ivf":
            self.retrieval_approximate_count += 1
        else:
            self.retrieval_exact_count += 1
        if fallback_used:
            self.degraded_lexical_fallbacks += 1

    def record_cache_metric(
        self,
        protection_drops: int = 0,
        admission_denials: int = 0,
        invalidations: int = 0,
        expired_evictions: int = 0,
    ) -> None:
        self.cache_protection_drops += protection_drops
        self.cache_admission_denials += admission_denials
        self.cache_invalidations += invalidations
        self.cache_expired_evictions += expired_evictions

    def record_shadow_query(
        self,
        sampled: bool = True,
        completed: bool = True,
        recall: float = 0.0,
    ) -> None:
        if sampled:
            self.shadow_queries_sampled += 1
        if completed:
            self.shadow_queries_completed += 1
            self.last_shadow_recall = recall

    def record_circuit_trip(self) -> None:
        self.circuit_trips += 1

    def record_contradiction(self, detected: bool = True) -> None:
        if detected:
            self.contradictions_detected += 1
        else:
            self.contradictions_resolved += 1

    def record_consolidation(self, proposed: bool = True) -> None:
        if proposed:
            self.consolidations_proposed += 1
        else:
            self.consolidations_applied += 1

    def record_learning_signal(self) -> None:
        self.learning_signals_extracted += 1

    def record_feedback(self, outcome: str) -> None:
        self.feedback_count += 1
        if outcome == "success":
            self.feedback_success += 1
        elif outcome == "failure":
            self.feedback_failure += 1

    def get_metrics(self) -> Dict[str, Any]:
        uptime = max(0.001, time.time() - self._started_at)
        total_cache = self.embedding_cache_hits + self.embedding_cache_misses
        cache_hit_rate = (self.embedding_cache_hits / total_cache) if total_cache > 0 else 0.0

        avg_emb_lat = (
            (self.embedding_time_total_ms / self.embedding_requests)
            if self.embedding_requests > 0 else 0.0
        )
        avg_ret_lat = (
            (self.retrieval_time_total_ms / self.retrieval_requests)
            if self.retrieval_requests > 0 else 0.0
        )

        return {
            "owner_id": self.owner_id,
            "uptime_seconds": round(uptime, 2),
            "embedding": {
                "requests": self.embedding_requests,
                "cache_hits": self.embedding_cache_hits,
                "cache_misses": self.embedding_cache_misses,
                "cache_hit_rate": round(cache_hit_rate, 4),
                "failures": self.embedding_failures,
                "retries": self.embedding_retries,
                "avg_latency_ms": round(avg_emb_lat, 2),
            },
            "retrieval": {
                "requests": self.retrieval_requests,
                "exact_queries": self.retrieval_exact_count,
                "approximate_queries": self.retrieval_approximate_count,
                "avg_latency_ms": round(avg_ret_lat, 2),
                "total_items_retrieved": self.retrieved_items_total,
                "degraded_fallbacks": self.degraded_lexical_fallbacks,
            },
            "cache": {
                "protection_drops": self.cache_protection_drops,
                "admission_denials": self.cache_admission_denials,
                "invalidations": self.cache_invalidations,
                "expired_evictions": self.cache_expired_evictions,
            },
            "shadow_calibration": {
                "queries_sampled": self.shadow_queries_sampled,
                "queries_completed": self.shadow_queries_completed,
                "last_recall": round(self.last_shadow_recall, 4),
            },
            "intelligence": {
                "contradictions_detected": self.contradictions_detected,
                "contradictions_resolved": self.contradictions_resolved,
                "consolidations_proposed": self.consolidations_proposed,
                "consolidations_applied": self.consolidations_applied,
                "learning_signals": self.learning_signals_extracted,
                "feedback_total": self.feedback_count,
                "feedback_success": self.feedback_success,
                "feedback_failure": self.feedback_failure,
            },
            "resilience": {
                "circuit_trips": self.circuit_trips,
            },
        }

    def reset(self) -> None:
        self.embedding_requests = 0
        self.embedding_cache_hits = 0
        self.embedding_cache_misses = 0
        self.embedding_failures = 0
        self.embedding_retries = 0
        self.embedding_time_total_ms = 0.0
        self.retrieval_requests = 0
        self.retrieval_exact_count = 0
        self.retrieval_approximate_count = 0
        self.retrieval_time_total_ms = 0.0
        self.retrieved_items_total = 0
        self.circuit_trips = 0
        self.degraded_lexical_fallbacks = 0
        self.contradictions_detected = 0
        self.contradictions_resolved = 0
        self.consolidations_proposed = 0
        self.consolidations_applied = 0
        self.learning_signals_extracted = 0
        self.feedback_count = 0
        self.feedback_success = 0
        self.feedback_failure = 0
        self.cache_protection_drops = 0
        self.cache_admission_denials = 0
        self.cache_invalidations = 0
        self.cache_expired_evictions = 0
        self.shadow_queries_sampled = 0
        self.shadow_queries_completed = 0
        self.last_shadow_recall = 0.0
        self._started_at = time.time()


class MemoryTelemetry:
    """
    Thread-safe metrics accumulator for memory retrieval, vector indexing, and intelligence pipelines.
    Supports both tenant-scoped metrics and global aggregate metrics.
    Guarantees secret-safety: raw text, queries, and credentials are never stored.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._started_at = time.time()
        self._tenants: Dict[str, TenantMetrics] = {}

        # Global aggregate counters (for admin views)
        self.embedding_requests = 0
        self.embedding_cache_hits = 0
        self.embedding_cache_misses = 0
        self.embedding_failures = 0
        self.embedding_retries = 0
        self.embedding_time_total_ms = 0.0

        self.retrieval_requests = 0
        self.retrieval_exact_count = 0
        self.retrieval_approximate_count = 0
        self.retrieval_time_total_ms = 0.0
        self.retrieved_items_total = 0

        self.circuit_trips = 0
        self.degraded_lexical_fallbacks = 0

        self.contradictions_detected = 0
        self.contradictions_resolved = 0
        self.consolidations_proposed = 0
        self.consolidations_applied = 0
        self.learning_signals_extracted = 0
        self.feedback_count = 0
        self.feedback_success = 0
        self.feedback_failure = 0

        # Phase 8 Cache Observability & Shadow Calibration Metrics
        self.cache_protection_drops = 0
        self.cache_admission_denials = 0
        self.cache_invalidations = 0
        self.cache_expired_evictions = 0
        self.shadow_queries_sampled = 0
        self.shadow_queries_completed = 0
        self.last_shadow_recall = 0.0

    def _get_or_create_tenant(self, owner_id: str) -> TenantMetrics:
        if owner_id not in self._tenants:
            self._tenants[owner_id] = TenantMetrics(owner_id)
        return self._tenants[owner_id]

    def record_embedding(
        self,
        latency_ms: float,
        cache_hit: bool,
        success: bool = True,
        retries: int = 0,
        owner_id: Optional[str] = None,
    ) -> None:
        """Record an embedding resolution attempt, scoped to owner_id if provided."""
        with self._lock:
            self.embedding_requests += 1
            if cache_hit:
                self.embedding_cache_hits += 1
            else:
                self.embedding_cache_misses += 1

            if not success:
                self.embedding_failures += 1
            self.embedding_retries += retries
            self.embedding_time_total_ms += max(0.0, latency_ms)

            if owner_id:
                t = self._get_or_create_tenant(owner_id)
                t.record_embedding(latency_ms, cache_hit, success, retries)

    def record_retrieval(
        self,
        latency_ms: float,
        items_count: int,
        index_type: str = "exact",
        fallback_used: bool = False,
        owner_id: Optional[str] = None,
    ) -> None:
        """Record a hybrid retrieval request, scoped to owner_id if provided."""
        with self._lock:
            self.retrieval_requests += 1
            self.retrieval_time_total_ms += max(0.0, latency_ms)
            self.retrieved_items_total += items_count
            if index_type == "approximate_ivf" or index_type == "native_ivf":
                self.retrieval_approximate_count += 1
            else:
                self.retrieval_exact_count += 1
            if fallback_used:
                self.degraded_lexical_fallbacks += 1

            if owner_id:
                t = self._get_or_create_tenant(owner_id)
                t.record_retrieval(latency_ms, items_count, index_type, fallback_used)

    def record_cache_metric(
        self,
        protection_drops: int = 0,
        admission_denials: int = 0,
        invalidations: int = 0,
        expired_evictions: int = 0,
        owner_id: Optional[str] = None,
    ) -> None:
        """Record cache observability metrics globally and per-tenant."""
        with self._lock:
            self.cache_protection_drops += protection_drops
            self.cache_admission_denials += admission_denials
            self.cache_invalidations += invalidations
            self.cache_expired_evictions += expired_evictions
            if owner_id:
                self._get_or_create_tenant(owner_id).record_cache_metric(
                    protection_drops, admission_denials, invalidations, expired_evictions
                )

    def record_shadow_query(
        self,
        sampled: bool = True,
        completed: bool = True,
        recall: float = 0.0,
        owner_id: Optional[str] = None,
    ) -> None:
        """Record shadow calibration metrics globally and per-tenant."""
        with self._lock:
            if sampled:
                self.shadow_queries_sampled += 1
            if completed:
                self.shadow_queries_completed += 1
                self.last_shadow_recall = recall
            if owner_id:
                self._get_or_create_tenant(owner_id).record_shadow_query(
                    sampled, completed, recall
                )

    def record_circuit_trip(self, owner_id: Optional[str] = None) -> None:
        """Record a circuit breaker opening event."""
        with self._lock:
            self.circuit_trips += 1
            if owner_id:
                t = self._get_or_create_tenant(owner_id)
                t.record_circuit_trip()

    def record_contradiction(self, detected: bool = True, owner_id: Optional[str] = None) -> None:
        with self._lock:
            if detected:
                self.contradictions_detected += 1
            else:
                self.contradictions_resolved += 1
            if owner_id:
                self._get_or_create_tenant(owner_id).record_contradiction(detected)

    def record_consolidation(self, proposed: bool = True, owner_id: Optional[str] = None) -> None:
        with self._lock:
            if proposed:
                self.consolidations_proposed += 1
            else:
                self.consolidations_applied += 1
            if owner_id:
                self._get_or_create_tenant(owner_id).record_consolidation(proposed)

    def record_learning_signal(self, owner_id: Optional[str] = None) -> None:
        with self._lock:
            self.learning_signals_extracted += 1
            if owner_id:
                self._get_or_create_tenant(owner_id).record_learning_signal()

    def record_feedback(self, outcome: str, owner_id: Optional[str] = None) -> None:
        with self._lock:
            self.feedback_count += 1
            if outcome == "success":
                self.feedback_success += 1
            elif outcome == "failure":
                self.feedback_failure += 1
            if owner_id:
                self._get_or_create_tenant(owner_id).record_feedback(outcome)

    def get_metrics(
        self,
        owner_id: Optional[str] = None,
        include_global: bool = False,
    ) -> Dict[str, Any]:
        """
        Return safe metrics dictionary.
        If owner_id is provided, returns ONLY that tenant's metrics.
        If owner_id is None and include_global is True, returns global aggregate metrics (admin only).
        If owner_id is None and include_global is False, returns zeroed isolated metrics (preventing disclosure).
        """
        with self._lock:
            if owner_id:
                if owner_id in self._tenants:
                    return self._tenants[owner_id].get_metrics()
                return TenantMetrics(owner_id).get_metrics()

            if not include_global:
                return TenantMetrics("isolated").get_metrics()

            # Global metrics
            uptime = max(0.001, time.time() - self._started_at)
            total_cache = self.embedding_cache_hits + self.embedding_cache_misses
            cache_hit_rate = (self.embedding_cache_hits / total_cache) if total_cache > 0 else 0.0

            avg_emb_lat = (
                (self.embedding_time_total_ms / self.embedding_requests)
                if self.embedding_requests > 0 else 0.0
            )
            avg_ret_lat = (
                (self.retrieval_time_total_ms / self.retrieval_requests)
                if self.retrieval_requests > 0 else 0.0
            )

            return {
                "uptime_seconds": round(uptime, 2),
                "embedding": {
                    "requests": self.embedding_requests,
                    "cache_hits": self.embedding_cache_hits,
                    "cache_misses": self.embedding_cache_misses,
                    "cache_hit_rate": round(cache_hit_rate, 4),
                    "failures": self.embedding_failures,
                    "retries": self.embedding_retries,
                    "avg_latency_ms": round(avg_emb_lat, 2),
                },
                "retrieval": {
                    "requests": self.retrieval_requests,
                    "exact_queries": self.retrieval_exact_count,
                    "approximate_queries": self.retrieval_approximate_count,
                    "avg_latency_ms": round(avg_ret_lat, 2),
                    "total_items_retrieved": self.retrieved_items_total,
                    "degraded_fallbacks": self.degraded_lexical_fallbacks,
                },
                "cache": {
                    "protection_drops": self.cache_protection_drops,
                    "admission_denials": self.cache_admission_denials,
                    "invalidations": self.cache_invalidations,
                    "expired_evictions": self.cache_expired_evictions,
                },
                "shadow_calibration": {
                    "queries_sampled": self.shadow_queries_sampled,
                    "queries_completed": self.shadow_queries_completed,
                    "last_recall": round(self.last_shadow_recall, 4),
                },
                "intelligence": {
                    "contradictions_detected": self.contradictions_detected,
                    "contradictions_resolved": self.contradictions_resolved,
                    "consolidations_proposed": self.consolidations_proposed,
                    "consolidations_applied": self.consolidations_applied,
                    "learning_signals": self.learning_signals_extracted,
                    "feedback_total": self.feedback_count,
                    "feedback_success": self.feedback_success,
                    "feedback_failure": self.feedback_failure,
                },
                "resilience": {
                    "circuit_trips": self.circuit_trips,
                },
            }

    def reset(self, owner_id: Optional[str] = None) -> None:
        """Reset metrics (for test environments)."""
        with self._lock:
            if owner_id:
                if owner_id in self._tenants:
                    self._tenants[owner_id].reset()
                return

            self._tenants.clear()
            self.embedding_requests = 0
            self.embedding_cache_hits = 0
            self.embedding_cache_misses = 0
            self.embedding_failures = 0
            self.embedding_retries = 0
            self.embedding_time_total_ms = 0.0
            self.retrieval_requests = 0
            self.retrieval_exact_count = 0
            self.retrieval_approximate_count = 0
            self.retrieval_time_total_ms = 0.0
            self.retrieved_items_total = 0
            self.circuit_trips = 0
            self.degraded_lexical_fallbacks = 0
            self.contradictions_detected = 0
            self.contradictions_resolved = 0
            self.consolidations_proposed = 0
            self.consolidations_applied = 0
            self.learning_signals_extracted = 0
            self.feedback_count = 0
            self.feedback_success = 0
            self.feedback_failure = 0
            self.cache_protection_drops = 0
            self.cache_admission_denials = 0
            self.cache_invalidations = 0
            self.cache_expired_evictions = 0
            self.shadow_queries_sampled = 0
            self.shadow_queries_completed = 0
            self.last_shadow_recall = 0.0
            self._started_at = time.time()


_global_telemetry = MemoryTelemetry()


def get_memory_telemetry() -> MemoryTelemetry:
    """Access the global memory telemetry collector."""
    return _global_telemetry
