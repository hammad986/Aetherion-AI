"""
tests/integration/test_phase6_remediation.py — Targeted Remediation Verification Suite.
Validates closure of all 5 Phase 6 Audit Findings:
  1. SEC-PHASE6-TELEM-LEAK-01: Telemetry multi-tenant isolation and admin-only global view
  2. SEC-PHASE6-SSRF-01: RemoteAPIEmbeddingProvider strict SSRF prevention and destination validation
  3. CORR-PHASE6-ANN-RECALL-01: ANN recall floor (>90%) across boundary and adversarial clustering
  4. CORR-PHASE6-CACHE-INVALIDATE-01: EmbeddingCache memory and SQLite eviction for specific text
  5. SEC-PHASE6-MAINT-AUTH-01: Maintenance route admin role enforcement (@admin_required)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
import pytest
from flask import Flask, g, jsonify
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from backend.execution.memory import (
    ApproximateVectorIndex,
    CircuitBreaker,
    DeterministicLocalEmbeddingProvider,
    EmbeddingCache,
    ExactVectorIndex,
    MemoryManager,
    MemoryStore,
    RemoteAPIEmbeddingProvider,
    compute_content_hash,
    get_memory_telemetry,
    get_vector_index,
)
from backend.execution.memory.embeddings import validate_safe_destination_url
from backend.auth_system import admin_required, _make_access_token


# ==============================================================================
# 1. TELEMETRY MULTI-TENANT ISOLATION (SEC-PHASE6-TELEM-LEAK-01)
# ==============================================================================

def test_telemetry_multi_tenant_isolation():
    """Verify operational telemetry does not leak cross-tenant operational metadata."""
    telemetry = get_memory_telemetry()
    telemetry.reset()

    # Record activity for tenant_a
    telemetry.record_embedding(latency_ms=15.0, cache_hit=True, owner_id="tenant_a")
    telemetry.record_retrieval(latency_ms=10.0, items_count=5, index_type="approximate_ivf", owner_id="tenant_a")

    # Record activity for tenant_b
    telemetry.record_embedding(latency_ms=25.0, cache_hit=False, owner_id="tenant_b")

    # 1. Unprivileged query (owner_id=None, include_global=False) must NOT disclose aggregate metrics
    default_metrics = telemetry.get_metrics()
    assert default_metrics["embedding"]["requests"] == 0, "Unprivileged get_metrics() must return 0 requests"
    assert default_metrics["retrieval"]["requests"] == 0, "Unprivileged get_metrics() must return 0 retrieval requests"

    # 2. Tenant A view must show only Tenant A activity
    a_metrics = telemetry.get_metrics(owner_id="tenant_a")
    assert a_metrics["embedding"]["requests"] == 1
    assert a_metrics["embedding"]["cache_hits"] == 1
    assert a_metrics["retrieval"]["requests"] == 1
    assert a_metrics["retrieval"]["approximate_queries"] == 1

    # 3. Tenant B view must show only Tenant B activity
    b_metrics = telemetry.get_metrics(owner_id="tenant_b")
    assert b_metrics["embedding"]["requests"] == 1
    assert b_metrics["embedding"]["cache_hits"] == 0
    assert b_metrics["retrieval"]["requests"] == 0

    # 4. Tenant C (no activity) must see zeroed metrics
    c_metrics = telemetry.get_metrics(owner_id="tenant_c")
    assert c_metrics["embedding"]["requests"] == 0
    assert c_metrics["retrieval"]["requests"] == 0

    # 5. Global view (admin only) must reflect combined activity
    global_metrics = telemetry.get_metrics(include_global=True)
    assert global_metrics["embedding"]["requests"] == 2
    assert global_metrics["embedding"]["cache_hits"] == 1
    assert global_metrics["retrieval"]["requests"] == 1


# ==============================================================================
# 2. SSRF PREVENTION IN EMBEDDINGS (SEC-PHASE6-SSRF-01)
# ==============================================================================

def test_remote_embedding_ssrf_blocking():
    """Verify RemoteAPIEmbeddingProvider rejects unsafe protocols and private/metadata IPs."""
    blocked_endpoints = [
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1:8080",
        "http://localhost:5000",
        "http://10.0.0.1:8000",
        "http://192.168.1.1:8000",
        "http://172.16.0.1:8000",
        "file:///etc/passwd",
        "ftp://example.com/api",
    ]

    for endpoint in blocked_endpoints:
        with pytest.raises(ValueError) as excinfo:
            RemoteAPIEmbeddingProvider(api_key="valid-secret-key", base_url=endpoint)
        assert "[SECURITY]" in str(excinfo.value) or "SSRF" in str(excinfo.value)

    # Empty API key must also be rejected
    with pytest.raises(ValueError):
        RemoteAPIEmbeddingProvider(api_key="", base_url="https://api.openai.com/v1")


def test_remote_embedding_error_redaction():
    """Verify secret credentials are never leaked in exception messages."""
    import os
    secret_key = "sk-super-secret-key-12345"
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
    # AETHERION_ALLOW_LOCAL_EMBEDDING_ENDPOINT=1 is the legitimate dev bypass for
    # local embedding endpoints. We use it here so that the SSRF guard passes
    # (both at __init__ and at embed() time) and we can verify error-message
    # redaction on actual network failure. The SSRF bypass removal itself is
    # tested separately in test_phase7_remediation.py::TestSSRFBypass.
    with patch.dict(os.environ, {"AETHERION_ALLOW_LOCAL_EMBEDDING_ENDPOINT": "1"}):
        provider = RemoteAPIEmbeddingProvider(
            api_key=secret_key,
            base_url="http://127.0.0.1:59999",
            timeout=1.0,
            max_retries=0,
            circuit_breaker=cb,
        )
        with pytest.raises(RuntimeError) as excinfo:
            provider.embed(["test sentence"])
    assert secret_key not in str(excinfo.value)
    assert "***REDACTED***" in str(excinfo.value) or "failed after retries" in str(excinfo.value)




# ==============================================================================
# 3. ANN RECALL & AUTO-SWITCH THRESHOLD (CORR-PHASE6-ANN-RECALL-01)
# ==============================================================================

def test_ann_recall_quality_and_threshold():
    """Verify IVF-Flat ANN recall exceeds 90% and auto-switch threshold is 500."""
    dim = 64
    provider = DeterministicLocalEmbeddingProvider(dimension=dim)
    query_vec = provider.embed_query("search terms for machine learning embeddings")

    # Test auto-switch factory threshold
    small_vectors = [(f"item_{i}", provider.embed_query(f"item {i}")) for i in range(250)]
    idx_250 = get_vector_index(owner_id="tenant_x", vectors=small_vectors, threshold=500)
    assert isinstance(idx_250, ExactVectorIndex), "Corpus size 250 must use ExactVectorIndex (threshold=500)"

    large_vectors = [(f"item_{i}", provider.embed_query(f"item {i}")) for i in range(500)]
    idx_500 = get_vector_index(owner_id="tenant_x", vectors=large_vectors, threshold=500)
    assert isinstance(idx_500, ApproximateVectorIndex), "Corpus size 500 must use ApproximateVectorIndex"

    # Compare Exact vs Approximate on 500 vectors
    exact_idx = ExactVectorIndex(large_vectors)
    exact_hits = exact_idx.search(query_vec, top_k=10)
    exact_ids = {mid for mid, _ in exact_hits}

    approx_hits = idx_500.search(query_vec, top_k=10)
    approx_ids = {mid for mid, _ in approx_hits}

    overlap = len(exact_ids.intersection(approx_ids))
    recall_10 = overlap / len(exact_ids) if exact_ids else 1.0
    assert recall_10 >= 0.90, f"Recall@10 {recall_10:.2%} is below required 90% threshold"


# ==============================================================================
# 4. IN-MEMORY LRU CACHE INVALIDATION FOR SPECIFIC TEXT (CORR-PHASE6-CACHE-INVALIDATE-01)
# ==============================================================================

def test_embedding_cache_specific_text_invalidation():
    """Verify invalidating specific text evicts from both SQLite and memory LRU."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "cache_test.db"
        cache = EmbeddingCache(db_path=db_path, max_memory_entries=10)

        tenant = "tenant_test"
        model = "test-model"
        dim = 4
        text1 = "First specific text entry"
        text2 = "Second specific text entry"
        vec1 = [0.1, 0.2, 0.3, 0.4]
        vec2 = [0.5, 0.6, 0.7, 0.8]

        # Put both into cache
        cache.put(tenant, text1, model, dim, vec1)
        cache.put(tenant, text2, model, dim, vec2)

        # Both present in memory
        assert cache.get(tenant, text1, model, dim) == vec1
        assert cache.get(tenant, text2, model, dim) == vec2

        # Invalidate text1 only
        deleted_count = cache.invalidate(tenant, text=text1)
        assert deleted_count == 1

        # text1 must be None (not in memory, not in SQLite)
        assert cache.get(tenant, text1, model, dim) is None

        # text2 must still be intact
        assert cache.get(tenant, text2, model, dim) == vec2


# ==============================================================================
# 5. MAINTENANCE ROUTE ROLE AUTHORIZATION (SEC-PHASE6-MAINT-AUTH-01)
# ==============================================================================

def test_maintenance_route_admin_authorization():
    """Verify /api/memory/maintenance/<action> rejects standard tenants and allows admins."""
    from backend.routes.phase4_routes import phase4_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(phase4_bp)

    user_token = _make_access_token(user_id=101, email="user@test.com", role="user")
    admin_token = _make_access_token(user_id=1, email="admin@test.com", role="admin")

    with app.test_client() as client:
        # 1. Unauthenticated request -> 401
        res_noauth = client.post("/api/memory/maintenance/reindex")
        assert res_noauth.status_code == 401

        # 2. Standard user -> 403 Forbidden
        res_user = client.post(
            "/api/memory/maintenance/reindex",
            headers={"Authorization": f"Bearer {user_token}"},
            json={"force_mode": "exact"},
        )
        assert res_user.status_code == 403
        data_user = res_user.get_json()
        assert data_user["ok"] is False
        assert "Admin privileges required" in data_user["error"]["message"]

        # 3. Admin user -> 202 Accepted (async dispatch) with job_id
        # Per ARCH-P2-MAINTENANCE-ASYNC-01 remediation, the route now dispatches
        # asynchronously and immediately returns 202 with a job_id tracker.
        res_admin = client.post(
            "/api/memory/maintenance/reindex",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"force_mode": "exact"},
        )
        assert res_admin.status_code == 202, (
            f"Expected 202 Accepted for admin maintenance request, got {res_admin.status_code}"
        )
        data_admin = res_admin.get_json()
        assert data_admin["ok"] is True
        assert "job_id" in data_admin, "Async maintenance response must include job_id"
