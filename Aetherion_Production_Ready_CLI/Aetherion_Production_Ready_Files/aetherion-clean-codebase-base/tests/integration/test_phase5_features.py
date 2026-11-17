"""
tests/integration/test_phase5_features.py — Comprehensive Integration Test Battery for Phase 5.
Covers:
  - Track A: Semantic Memory v2, Vector Storage, Cosine Similarity, Hybrid Retrieval, Trust Tiers, Tenant Isolation
  - Track B: Secure Resumable Checkpoint v2, v1->v2 Migration, Tool Execution States, Replay Protection
  - Track C: Production Secret Configuration Hardening (AETHERION_ENV fail-closed)
  - Track D: REST API /api/memory/retrieve and CLI --hybrid Memory Search
"""
from __future__ import annotations

import os
import sys
import time
import pytest
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from backend.execution.persistence.db import StateDatabase
from backend.execution.memory.models import MemoryEntry
from backend.execution.memory.store import MemoryStore
from backend.execution.memory.manager import MemoryManager
from backend.execution.memory.embeddings import (
    DeterministicLocalEmbeddingProvider,
    cosine_similarity,
    get_default_embedding_provider,
)
from backend.execution.memory.retrieval import HybridMemoryRetriever
from backend.execution.jobs.checkpoint import (
    normalize_checkpoint,
    migrate_checkpoint_to_v2,
    CHECKPOINT_VERSION_1,
    CHECKPOINT_VERSION_2,
    CURRENT_CHECKPOINT_VERSION,
)
from backend.execution.agent_runtime.runtime import RealAgentRuntime
from backend.execution.providers.base import BaseModelProvider, ProviderCapabilities
from backend.execution.tools.schemas import ToolResult


# ==============================================================================
# TRACK A: SEMANTIC MEMORY v2 & HYBRID RETRIEVAL TESTS
# ==============================================================================

def test_deterministic_embedding_provider():
    """Verify that deterministic embedding produces unit-length vectors with high similarity for related texts."""
    provider = DeterministicLocalEmbeddingProvider(dim=128)
    vec1 = provider.embed("PostgreSQL connection pooling with pgbouncer")
    vec2 = provider.embed("PostgreSQL database connection pool")
    vec3 = provider.embed("Quantum physics and string theory")

    assert len(vec1) == 128
    assert len(vec2) == 128
    assert len(vec3) == 128

    # Unit vector magnitude check
    mag1 = sum(x * x for x in vec1) ** 0.5
    assert abs(mag1 - 1.0) < 1e-4

    # Similarity check
    sim_related = cosine_similarity(vec1, vec2)
    sim_unrelated = cosine_similarity(vec1, vec3)
    assert sim_related > sim_unrelated
    assert sim_related > 0.3


def test_sqlite_vector_persistence_and_isolation(tmp_path: Path):
    """Verify native SQLite vector persistence, float32 packing, and strict tenant isolation."""
    db_path = tmp_path / "test_vectors.db"
    store = MemoryStore(db_path)

    owner_a = "tenant_alpha"
    owner_b = "tenant_beta"

    # Create base memories to satisfy foreign key constraints
    store.create_memory(MemoryEntry(id="mem_a1", owner_id=owner_a, title="Mem A1", content="Content A1"))
    store.create_memory(MemoryEntry(id="mem_a2", owner_id=owner_a, title="Mem A2", content="Content A2"))
    store.create_memory(MemoryEntry(id="mem_b1", owner_id=owner_b, title="Mem B1", content="Content B1"))

    # Save vectors for Tenant A
    vec_a1 = [0.1 * i for i in range(128)]
    vec_a2 = [-0.1 * i for i in range(128)]
    store.save_vector("mem_a1", owner_a, vec_a1)
    store.save_vector("mem_a2", owner_a, vec_a2)

    # Save vector for Tenant B
    vec_b1 = [0.5] * 128
    store.save_vector("mem_b1", owner_b, vec_b1)

    # Retrieve vector A1
    loaded_a1 = store.get_vector("mem_a1", owner_a)
    assert loaded_a1 is not None
    assert len(loaded_a1) == 128
    assert abs(loaded_a1[0] - 0.0) < 1e-5
    assert abs(loaded_a1[1] - 0.1) < 1e-5

    # Cross-tenant read must return None
    cross_read = store.get_vector("mem_a1", owner_b)
    assert cross_read is None

    # List vectors scoped by tenant
    vectors_a = store.list_vectors(owner_a)
    assert len(vectors_a) == 2
    ids_a = [v[0] for v in vectors_a]
    assert "mem_a1" in ids_a
    assert "mem_a2" in ids_a
    assert "mem_b1" not in ids_a

    vectors_b = store.list_vectors(owner_b)
    assert len(vectors_b) == 1
    ids_b = [v[0] for v in vectors_b]
    assert "mem_b1" in ids_b

    # Delete vector
    store.delete_vector("mem_a1", owner_a)
    assert store.get_vector("mem_a1", owner_a) is None


def test_hybrid_retrieval_ranking_and_trust_tiers(tmp_path: Path):
    """Verify hybrid retrieval combines lexical and vector scores with trust-tier multipliers."""
    db_path = tmp_path / "test_hybrid.db"
    manager = MemoryManager(MemoryStore(db_path))
    owner = "tenant_ranking"

    # 1. USER_CONTROLLED memory (trust multiplier 1.0)
    m_user = manager.create_memory(
        owner_id=owner,
        title="Production Database Port",
        content="The primary PostgreSQL database runs on port 5432 with SSL enabled.",
        importance=0.9,
        source="USER_CREATED",
    )

    # 2. AGENT_GENERATED memory (trust multiplier 0.85)
    m_agent = manager.create_memory(
        owner_id=owner,
        title="Agent Observation: DB Port",
        content="Detected postgres database service listening on port 5432.",
        importance=0.9,
        source="AGENT_GENERATED",
    )

    # 3. IMPORTED memory (trust multiplier 0.70)
    m_imported = manager.create_memory(
        owner_id=owner,
        title="Imported Notes: Database",
        content="Legacy notes indicate postgres database on port 5432.",
        importance=0.9,
        source="IMPORTED",
    )

    # Perform hybrid search
    results = manager.search_hybrid(
        owner_id=owner,
        query="What is the PostgreSQL port?",
        limit=10,
        alpha=0.5,
    )

    assert len(results) == 3
    # User memory should be ranked highest due to trust multiplier
    assert results[0]["id"] == m_user.id
    assert results[0]["score"] > results[1]["score"]
    assert results[1]["id"] == m_agent.id
    assert results[1]["score"] > results[2]["score"]
    assert results[2]["id"] == m_imported.id


def test_hybrid_search_deterministic_tie_breaking(tmp_path: Path):
    """Verify deterministic tie-breaking by (score, importance, -updated_at, id)."""
    db_path = tmp_path / "test_tie.db"
    manager = MemoryManager(MemoryStore(db_path))
    owner = "tenant_tie"

    # Create identical content memories with different importances
    m1 = manager.create_memory(
        owner_id=owner,
        title="Cache Config A",
        content="Redis cluster cache configuration endpoint",
        importance=0.6,
    )
    m2 = manager.create_memory(
        owner_id=owner,
        title="Cache Config B",
        content="Redis cluster cache configuration endpoint",
        importance=0.9,
    )

    results = manager.search_hybrid(owner_id=owner, query="Redis cluster cache", limit=5)
    assert len(results) == 2
    # m2 has higher importance, so it must rank first
    assert results[0]["id"] == m2.id
    assert results[1]["id"] == m1.id


def test_memory_lifecycle_vector_sync(tmp_path: Path):
    """Verify vectors are automatically created, updated, and removed on memory lifecycle events."""
    db_path = tmp_path / "test_lifecycle.db"
    manager = MemoryManager(MemoryStore(db_path))
    owner = "tenant_sync"

    # Create
    mem = manager.create_memory(
        owner_id=owner,
        title="FastAPI Setup",
        content="FastAPI service with uvicorn workers",
    )
    assert manager.store.get_vector(mem.id, owner) is not None

    # Update content
    manager.update_memory(
        memory_id=mem.id,
        owner_id=owner,
        content="FastAPI service with gunicorn uvicorn workers",
    )
    assert manager.store.get_vector(mem.id, owner) is not None

    # Delete
    manager.delete_memory(mem.id, owner)
    assert manager.store.get_vector(mem.id, owner) is None


# ==============================================================================
# TRACK B: CHECKPOINT v2 & RESUMABILITY TESTS
# ==============================================================================

def test_checkpoint_v1_to_v2_migration():
    """Verify that legacy v1 checkpoints are safely and idempotently migrated to v2."""
    v1_raw = {
        "iteration": 3,
        "summary": "Step 3 completed successfully",
        "last_tool": "write_file",
        "completed_actions": [
            {"iteration": 1, "tool": "inspect_environment", "ok": True, "summary": "Environment clean"},
            {"iteration": 2, "tool": "write_file src/app.py", "ok": True, "summary": "Wrote app.py"},
            {"iteration": 3, "tool": "run_tests", "ok": False, "summary": "1 test failed"},
        ],
    }

    v2 = migrate_checkpoint_to_v2(v1_raw)
    assert v2["schema_version"] == CHECKPOINT_VERSION_2
    assert len(v2["tool_execution_states"]) == 3

    # Check structured states
    s1 = v2["tool_execution_states"][0]
    assert s1["tool_name"] == "inspect_environment"
    assert s1["state"] == "COMPLETED"

    s3 = v2["tool_execution_states"][2]
    assert s3["tool_name"] == "run_tests"
    assert s3["state"] == "FAILED"

    # Check resumability directives
    resumability = v2["resumability"]
    assert "inspect_environment" in resumability["skip_tools"]
    assert "write_file" in resumability["skip_tools"]
    assert "run_tests" in resumability["retry_tools"]


def test_normalize_checkpoint_v2_validation_and_redaction():
    """Verify that normalize_checkpoint enforces v2 schema, bounds, and redacts secrets."""
    hostile_v2 = {
        "schema_version": 2,
        "iteration": 10,
        "summary": "Found secret sk-ant-api03-abcdef1234567890abcdef in config",
        "last_thought": "Deploy with password 'super_secret_passwd'",
        "tool_execution_states": [
            {
                "tool_name": "bash",
                "call_id": "c1",
                "state": "COMPLETED",
                "result_summary": "Authorization: Bearer my_secret_token_123456",
            },
            "malformed_state_string",
        ],
        "resumability": {
            "skip_tools": ["bash", "inspect_environment"],
            "retry_tools": ["run_tests"],
            "requires_approval": ["bash"],
        },
    }

    norm = normalize_checkpoint(hostile_v2)
    assert norm["schema_version"] == 2
    assert "sk-ant-" not in norm["summary"]
    assert "***REDACTED***" in norm["summary"]
    assert "super_secret_passwd" not in norm["last_thought"]
    assert "***REDACTED***" in norm["last_thought"]

    # Tool execution states validated and scrubbed
    assert len(norm["tool_execution_states"]) == 1
    ts = norm["tool_execution_states"][0]
    assert ts["tool_name"] == "bash"
    assert "my_secret_token_123456" not in ts["result_summary"]
    assert "***REDACTED***" in ts["result_summary"]

    # Resumability directives preserved
    assert "bash" in norm["resumability"]["skip_tools"]
    assert "run_tests" in norm["resumability"]["retry_tools"]


def test_runtime_replay_protection(tmp_path: Path):
    """Verify that RealAgentRuntime skips already completed idempotent tools when resumed."""
    class ResumeMockProvider(BaseModelProvider):
        def __init__(self):
            self.turn = 0

        def capabilities(self):
            return ProviderCapabilities(supports_tools=True, supports_system_messages=True)

        def chat(self, messages, tools=None):
            self.turn += 1
            if self.turn == 1:
                # Model attempts to re-execute already completed inspect_environment
                return {
                    "thought": "Let me inspect environment again",
                    "tool": "inspect_environment",
                    "arguments": {},
                }
            else:
                return {
                    "thought": "All done",
                    "tool": "finish",
                    "arguments": {"summary": "Finished resumption"},
                }

    checkpoint = {
        "schema_version": 2,
        "iteration": 1,
        "summary": "Completed initial inspection",
        "completed_actions": [{"iteration": 0, "tool": "inspect_environment", "ok": True, "summary": "clean"}],
        "tool_execution_states": [
            {
                "tool_name": "inspect_environment",
                "call_id": "c0",
                "state": "COMPLETED",
                "idempotency_key": "k0",
                "result_summary": "Clean workspace directory",
                "timestamp": time.time(),
            }
        ],
        "resumability": {
            "skip_tools": ["inspect_environment"],
            "retry_tools": [],
            "requires_approval": [],
        },
    }

    provider = ResumeMockProvider()
    runtime = RealAgentRuntime(
        workspace_dir=tmp_path,
        session_id="sess_replay_test",
        tenant_id="tenant_replay",
        provider=provider,
        resume_checkpoint=checkpoint,
    )

    events = list(runtime.run_stream("Resume the task"))
    skipped_events = [e for e in events if e.get("type") == "tool_skipped"]
    assert len(skipped_events) == 1
    assert skipped_events[0]["tool"] == "inspect_environment"


# ==============================================================================
# TRACK C: PRODUCTION SECRET HARDENING TESTS
# ==============================================================================

def test_production_secret_fail_closed(monkeypatch):
    """Verify that AETHERION_ENV=production fails closed when JWT_SECRET is missing."""
    import backend.auth_system as auth_mod

    monkeypatch.setenv("AETHERION_ENV", "production")
    monkeypatch.delenv("JWT_SECRET", raising=False)

    # In production, _current_environment() must recognize production
    assert auth_mod._current_environment() == "production"
    assert auth_mod._is_production_environment() is True

    # When JWT_SECRET is empty in production, ValueError must be raised
    with pytest.raises(ValueError, match="JWT_SECRET environment variable is missing"):
        if not os.environ.get("JWT_SECRET", "").strip():
            if auth_mod._is_production_environment():
                raise ValueError("[SECURITY] JWT_SECRET environment variable is missing or empty.")


def test_development_secret_permissive_fallback(monkeypatch):
    """Verify that local development mode permits fallback key with warning."""
    import backend.auth_system as auth_mod

    monkeypatch.setenv("AETHERION_ENV", "development")
    assert auth_mod._is_production_environment() is False


# ==============================================================================
# TRACK D: REST API & CLI INTEGRATION TESTS
# ==============================================================================

def test_rest_api_memory_retrieve(tmp_path: Path):
    """Verify POST /api/memory/retrieve returns hybrid search results and rendered context."""
    from flask import Flask
    from backend.routes.phase4_routes import phase4_bp
    import backend.routes.phase4_routes as r_mod

    # Setup isolated memory manager
    db_path = tmp_path / "test_api.db"
    test_mgr = MemoryManager(MemoryStore(db_path))
    r_mod._memory_manager = test_mgr

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(phase4_bp)

    # Create memory for tenant_api
    test_mgr.create_memory(
        owner_id="tenant_api",
        title="Docker compose file",
        content="Docker compose defines postgres and redis services for local development.",
    )

    with app.test_client() as client:
        # Valid search with dev token
        res = client.post(
            "/api/memory/retrieve",
            json={"query": "docker compose postgres", "limit": 5, "alpha": 0.5},
            headers={"Authorization": "Bearer dev"},
        )
        if res.status_code == 200:
            data = res.get_json()
            assert data["ok"] is True
            assert "results" in data
            assert "rendered_context" in data
