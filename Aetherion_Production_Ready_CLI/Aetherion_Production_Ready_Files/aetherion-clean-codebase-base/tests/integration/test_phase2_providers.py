"""
tests/integration/test_phase2_providers.py — Phase 2 Extensible Agent Platform Integration Suite.

20 comprehensive verification scenarios testing:
  1. Create custom OpenAI-compatible provider.
  2. Persist provider and reload after process restart.
  3. Update provider.
  4. Delete provider.
  5. Set default provider.
  6. Disable provider and verify router does not select it.
  7. Provider connection success with mocked HTTP server (CONNECTED).
  8. 401 provider failure (AUTH_FAILED).
  9. 404 endpoint failure (NOT_FOUND).
  10. Timeout handling (TIMEOUT).
  11. Tool schema normalization (all 13 tools across Anthropic and OpenAI schemas).
  12. Normalized tool-call response (ModelResponse / ToolCall dict compatibility).
  13. Multiple custom providers coexistence (OpenAI, Anthropic, Chat).
  14. Provider with arbitrary unknown base URL.
  15. Secret redaction and key masking (sk-...4a1b).
  16. Legacy environment-variable compatibility.
  17. Deterministic provider compatibility.
  18. Offline fallback compatibility (truthful NOT_VERIFIED).
  19. Model router selection order (explicit ID > custom model > default provider > env vars > offline).
  20. Duplicate provider functionality.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

# Ensure backend and cli are on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from execution.agent_runtime import (
    AnthropicRuntimeProvider,
    BaseModelProvider,
    DeterministicTestProvider,
    GenericChatProvider,
    ModelResponse,
    ModelRouter,
    NOT_VERIFIED,
    OfflineFallbackProvider,
    OpenAIRuntimeProvider,
    ProviderCapabilities,
    ProviderRegistry,
    ProviderStore,
    ProviderUnavailableException,
    ToolCall,
    decrypt_api_key,
    encrypt_api_key,
    get_anthropic_tools,
    get_openai_tools,
    get_provider,
    mask_api_key,
)


@pytest.fixture
def clean_registry(tmp_path):
    """Provides a fresh, isolated ProviderRegistry backed by an isolated SQLite DB."""
    db_file = tmp_path / "test_providers.db"
    store = ProviderStore(db_path=db_file)
    registry = ProviderRegistry(store=store)
    return registry


# ── Scenario 1: Create custom OpenAI-compatible provider ─────────────────────
def test_01_create_custom_openai_provider(clean_registry):
    data = {
        "name": "Local Ollama Qwen",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": "http://127.0.0.1:11434/v1/",
        "api_key": "sk-local-token-12345678",
        "default_model": "qwen2.5-coder:7b",
        "available_models": ["qwen2.5-coder:7b", "llama3.1:8b"],
        "is_default": False,
        "is_enabled": True,
    }
    rec = clean_registry.create_provider(data)

    assert rec["name"] == "Local Ollama Qwen"
    assert rec["provider_type"] == "OPENAI_COMPATIBLE"
    assert rec["base_url"] == "http://127.0.0.1:11434/v1"  # Stripped trailing slash
    assert rec["default_model"] == "qwen2.5-coder:7b"
    assert rec["available_models"] == ["qwen2.5-coder:7b", "llama3.1:8b"]
    assert rec["api_key_masked"] == "sk-...5678"
    assert "api_key" not in rec  # Plaintext key omitted by default


# ── Scenario 2: Persist provider and reload after restart ────────────────────
def test_02_persist_and_reload_provider(tmp_path):
    db_file = tmp_path / "persist_test.db"
    store1 = ProviderStore(db_path=db_file)
    reg1 = ProviderRegistry(store=store1)

    created = reg1.create_provider({
        "id": "prov_persist_1",
        "name": "Persistent DeepSeek",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-deepseek-secret-key-9988",
        "default_model": "deepseek-chat",
    })

    # Simulate process restart by creating new store and registry from same db_path
    store2 = ProviderStore(db_path=db_file)
    reg2 = ProviderRegistry(store=store2)

    loaded = reg2.get_provider("prov_persist_1", include_raw_key=True)
    assert loaded is not None
    assert loaded["name"] == "Persistent DeepSeek"
    assert loaded["base_url"] == "https://api.deepseek.com/v1"
    assert loaded["api_key"] == "sk-deepseek-secret-key-9988"
    assert loaded["api_key_masked"] == "sk-...9988"


# ── Scenario 3: Update provider ──────────────────────────────────────────────
def test_03_update_provider(clean_registry):
    created = clean_registry.create_provider({
        "id": "prov_update_1",
        "name": "Original Name",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": "https://api.original.com/v1",
        "api_key": "sk-original-key-1111",
        "default_model": "model-v1",
    })

    updated = clean_registry.update_provider("prov_update_1", {
        "name": "Updated Name",
        "base_url": "https://api.updated.com/v2/",
        "api_key": "sk-updated-key-2222",
        "default_model": "model-v2",
    })

    assert updated["name"] == "Updated Name"
    assert updated["base_url"] == "https://api.updated.com/v2"
    assert updated["default_model"] == "model-v2"
    assert updated["api_key_masked"] == "sk-...2222"

    raw = clean_registry.get_provider("prov_update_1", include_raw_key=True)
    assert raw["api_key"] == "sk-updated-key-2222"


# ── Scenario 4: Delete provider ──────────────────────────────────────────────
def test_04_delete_provider(clean_registry):
    created = clean_registry.create_provider({
        "id": "prov_del_1",
        "name": "To Be Deleted",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": "https://api.delete.com",
    })

    assert clean_registry.get_provider("prov_del_1") is not None
    deleted = clean_registry.delete_provider("prov_del_1")
    assert deleted is True

    assert clean_registry.get_provider("prov_del_1") is None
    # Deleting again returns False
    assert clean_registry.delete_provider("prov_del_1") is False


# ── Scenario 5: Set default provider ─────────────────────────────────────────
def test_05_set_default_provider(clean_registry):
    p1 = clean_registry.create_provider({
        "id": "prov_1",
        "name": "Provider One",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": "https://api.one.com",
        "is_default": True,
    })
    p2 = clean_registry.create_provider({
        "id": "prov_2",
        "name": "Provider Two",
        "provider_type": "ANTHROPIC_COMPATIBLE",
        "base_url": "https://api.two.com",
        "is_default": False,
    })

    def_prov = clean_registry.get_default_provider()
    assert def_prov["id"] == "prov_1"

    # Set prov_2 as default
    clean_registry.set_default("prov_2")

    p1_reloaded = clean_registry.get_provider("prov_1")
    p2_reloaded = clean_registry.get_provider("prov_2")

    assert p1_reloaded["is_default"] is False
    assert p2_reloaded["is_default"] is True
    assert clean_registry.get_default_provider()["id"] == "prov_2"


# ── Scenario 6: Disable provider and verify router behavior ──────────────────
def test_06_disable_provider_not_selected_by_router(clean_registry):
    clean_registry.create_provider({
        "id": "prov_dis",
        "name": "Disabled Provider",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": "https://api.disabled.com",
        "default_model": "disabled-model",
        "is_default": True,
        "is_enabled": True,
    })

    router = ModelRouter(registry=clean_registry)
    # When enabled, router resolves it as default
    inst = router.resolve()
    assert inst.name() == "openai:disabled-model"

    # Disable provider
    clean_registry.set_enabled("prov_dis", False)
    assert clean_registry.get_default_provider() is None

    # Router should no longer resolve it as default or for its model
    with pytest.raises(ProviderUnavailableException):
        router.resolve(provider_id="prov_dis")


# ── Scenario 7: Provider connection success (CONNECTED) ──────────────────────
def test_07_provider_connection_success_mock(clean_registry):
    clean_registry.create_provider({
        "id": "prov_conn_ok",
        "name": "Mock OK Provider",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": "https://api.mock-ok.com/v1",
        "api_key": "sk-valid-key",
    })

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"id": "gpt-4o"}]}

    with patch("httpx.Client.get", return_value=mock_resp):
        res = clean_registry.health_check("prov_conn_ok")
        assert res["status"] == "CONNECTED"
        assert res["latency_ms"] >= 0.0
        assert "Successfully connected" in res["diagnostics"]


# ── Scenario 8: 401 provider failure (AUTH_FAILED) ───────────────────────────
def test_08_provider_connection_401_auth_failed(clean_registry):
    clean_registry.create_provider({
        "id": "prov_auth_fail",
        "name": "Auth Fail Provider",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": "https://api.mock-auth-fail.com/v1",
        "api_key": "sk-bad-key",
    })

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized: Invalid API key"

    with patch("httpx.Client.get", return_value=mock_resp):
        res = clean_registry.health_check("prov_auth_fail")
        assert res["status"] == "AUTH_FAILED"
        assert "Authentication failed" in res["diagnostics"]


# ── Scenario 9: 404 endpoint failure (NOT_FOUND) ─────────────────────────────
def test_09_provider_connection_404_not_found(clean_registry):
    clean_registry.create_provider({
        "id": "prov_404",
        "name": "Not Found Provider",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": "https://api.mock-404.com/v1",
    })

    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "Not Found"

    with patch("httpx.Client.get", return_value=mock_resp), \
         patch("httpx.Client.post", return_value=mock_resp):
        res = clean_registry.health_check("prov_404")
        assert res["status"] == "NOT_FOUND"
        assert "not found" in res["diagnostics"].lower()


# ── Scenario 10: Timeout handling (TIMEOUT) ──────────────────────────────────
def test_10_provider_connection_timeout(clean_registry):
    clean_registry.create_provider({
        "id": "prov_timeout",
        "name": "Timeout Provider",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": "https://api.timeout-endpoint.com/v1",
    })

    with patch("httpx.Client.get", side_effect=httpx.TimeoutException("Read timed out")):
        res = clean_registry.health_check("prov_timeout")
        assert res["status"] == "TIMEOUT"
        assert "timed out" in res["diagnostics"].lower()


# ── Scenario 11: Tool schema normalization ───────────────────────────────────
def test_11_tool_schema_normalization():
    anthropic_tools = get_anthropic_tools()
    openai_tools = get_openai_tools()

    assert len(anthropic_tools) == 13
    assert len(openai_tools) == 13

    expected_tool_names = {
        "inspect_environment",
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "search_files",
        "delete_file",
        "run_command",
        "run_tests",
        "check_syntax",
        "git_status",
        "git_diff",
        "finish",
    }

    anthropic_names = {t["name"] for t in anthropic_tools}
    assert anthropic_names == expected_tool_names

    # Check OpenAI functions format
    for ot in openai_tools:
        assert ot["type"] == "function"
        assert "function" in ot
        func = ot["function"]
        assert func["name"] in expected_tool_names
        assert "parameters" in func


# ── Scenario 12: Normalized tool-call response (ModelResponse / ToolCall) ────
def test_12_normalized_model_response():
    tc = ToolCall(id="call_test_123", name="write_file", arguments={"path": "demo.py", "content": "print('ok')"})
    resp = ModelResponse(
        text="Planning to write demo.py",
        tool_calls=[tc],
        finish_reason="tool_calls",
        usage={"prompt_tokens": 50, "completion_tokens": 15},
    )

    # Object access
    assert resp.text == "Planning to write demo.py"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "write_file"
    assert resp.tool_calls[0].arguments["path"] == "demo.py"

    # Dict-like backward compatibility access
    assert resp["thought"] == "Planning to write demo.py"
    assert resp["tool"] == "write_file"
    assert resp["arguments"]["path"] == "demo.py"
    assert resp["tool_call_id"] == "call_test_123"

    # .get() support
    assert resp.get("thought") == "Planning to write demo.py"
    assert resp.get("non_existent", "default_val") == "default_val"

    # "in" operator support
    assert "tool" in resp
    assert "arguments" in resp
    assert "thought" in resp

    # to_dict conversion
    d = resp.to_dict()
    assert isinstance(d, dict)
    assert d["tool"] == "write_file"


# ── Scenario 13: Multiple custom providers coexistence ───────────────────────
def test_13_multiple_custom_providers_coexistence(clean_registry):
    clean_registry.create_provider({
        "name": "Coexist OpenAI",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": "https://api.openai-like.com/v1",
        "default_model": "gpt-4o",
    })
    clean_registry.create_provider({
        "name": "Coexist Anthropic",
        "provider_type": "ANTHROPIC_COMPATIBLE",
        "base_url": "https://api.anthropic-like.com",
        "default_model": "claude-3-5-sonnet",
    })
    clean_registry.create_provider({
        "name": "Coexist Generic Chat",
        "provider_type": "CHAT_COMPATIBLE",
        "base_url": "https://api.chat-like.com/v1",
        "default_model": "chat-model-v1",
    })

    all_provs = clean_registry.list_providers()
    assert len(all_provs) == 3

    types = {p["provider_type"] for p in all_provs}
    assert types == {"OPENAI_COMPATIBLE", "ANTHROPIC_COMPATIBLE", "CHAT_COMPATIBLE"}

    # Build live instances
    for p in all_provs:
        inst = clean_registry.build_instance(p)
        assert isinstance(inst, BaseModelProvider)
        assert inst.capabilities().supports_tools is True


# ── Scenario 14: Provider with arbitrary unknown base URL ────────────────────
def test_14_arbitrary_base_url_provider(clean_registry):
    custom_url = "http://internal-gateway.corp.net:8080/ai/v1/"
    rec = clean_registry.create_provider({
        "name": "Internal Enterprise Gateway",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": custom_url,
        "api_key": "sk-internal-token",
        "default_model": "corp-coder-34b",
    })

    inst = clean_registry.build_instance(rec)
    assert isinstance(inst, OpenAIRuntimeProvider)
    assert inst.base_url == "http://internal-gateway.corp.net:8080/ai/v1"
    assert inst.model == "corp-coder-34b"

    # Test HTTP dispatch uses the exact custom base URL
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "OK", "tool_calls": []}}]
    }

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        resp = inst.chat(messages=[{"role": "user", "content": "hi"}], tools=[])
        assert mock_post.called
        call_url = mock_post.call_args[0][0]
        assert call_url == "http://internal-gateway.corp.net:8080/ai/v1/chat/completions"


# ── Scenario 15: Secret redaction and key masking ────────────────────────────
def test_15_secret_redaction_and_masking(clean_registry):
    plain_secret = "sk-super-secret-production-token-9876543210"
    rec = clean_registry.create_provider({
        "name": "Secret Provider",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": "https://api.secure.com/v1",
        "api_key": plain_secret,
    })

    # Public list & get do not expose plaintext
    assert rec["api_key_masked"] == "sk-...3210"
    assert plain_secret not in str(rec)
    assert "api_key" not in rec

    listed = clean_registry.list_providers(include_raw_keys=False)
    for p in listed:
        assert plain_secret not in str(p)

    # In-memory encryption verification
    raw_record = clean_registry.get_provider(rec["id"], include_raw_key=True)
    assert raw_record["api_key"] == plain_secret

    # Encryption / Decryption helpers
    encrypted = encrypt_api_key("test-secret-1234")
    assert encrypted != "test-secret-1234"
    decrypted = decrypt_api_key(encrypted)
    assert decrypted == "test-secret-1234"
    assert mask_api_key("sk-test-secret-1234") == "sk-...1234"


# ── Scenario 16: Legacy environment-variable compatibility ───────────────────
def test_16_legacy_env_var_compatibility(tmp_path, monkeypatch):
    empty_store = ProviderStore(db_path=tmp_path / "empty.db")
    empty_reg = ProviderRegistry(store=empty_store)
    router = ModelRouter(registry=empty_reg)

    # Disable test mode to test live env routing
    monkeypatch.delenv("AETHERION_TEST_MODE", raising=False)

    # 1. ANTHROPIC_API_KEY
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-token-1234")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p_ant = router.resolve(model_name="claude-3-5-sonnet")
    assert isinstance(p_ant, AnthropicRuntimeProvider)
    assert p_ant.api_key == "sk-ant-test-token-1234"

    # 2. OPENAI_API_KEY
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-test-token-5678")
    p_oai = router.resolve(model_name="gpt-4o")
    assert isinstance(p_oai, OpenAIRuntimeProvider)
    assert p_oai.api_key == "sk-oai-test-token-5678"


# ── Scenario 17: Deterministic provider compatibility ────────────────────────
def test_17_deterministic_provider_compatibility():
    router = ModelRouter()
    provider = router.resolve(model_name="deterministic_test_provider")
    assert isinstance(provider, DeterministicTestProvider)
    assert provider.name() == "deterministic_test_provider"
    assert provider.is_available() is True

    # Test multi-turn deterministic reasoning
    tools = get_anthropic_tools()
    r1 = provider.chat(messages=[{"role": "user", "content": "Create hello.py that prints Hello Aetherion"}], tools=tools)
    assert r1["tool"] == "inspect_environment"

    r2 = provider.chat(messages=[{"role": "user", "content": "Create hello.py that prints Hello Aetherion"}], tools=tools)
    assert r2["tool"] == "write_file"
    assert r2["arguments"]["path"] == "hello.py"


# ── Scenario 18: Offline fallback compatibility ──────────────────────────────
def test_18_offline_fallback_compatibility(tmp_path, monkeypatch):
    empty_store = ProviderStore(db_path=tmp_path / "offline.db")
    empty_reg = ProviderRegistry(store=empty_store)
    router = ModelRouter(registry=empty_reg)

    monkeypatch.delenv("AETHERION_TEST_MODE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AETHERION_FORCE_OFFLINE", "1")

    provider = router.resolve(model_name="auto")
    assert isinstance(provider, OfflineFallbackProvider)
    assert provider.is_available() is True

    res = provider.chat(messages=[{"role": "user", "content": "write code"}], tools=[])
    assert res["tool"] == "finish"
    assert res["arguments"]["verification"] == NOT_VERIFIED
    assert "Offline fallback" in res["arguments"]["summary"]


# ── Scenario 19: Model router selection order ────────────────────────────────
def test_19_model_router_selection_order(tmp_path, monkeypatch):
    store = ProviderStore(db_path=tmp_path / "order.db")
    registry = ProviderRegistry(store=store)
    router = ModelRouter(registry=registry)

    monkeypatch.delenv("AETHERION_TEST_MODE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-token")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-env-token")

    # Register custom provider with explicit model
    p_custom = registry.create_provider({
        "id": "prov_custom_qwen",
        "name": "Custom Qwen",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": "http://127.0.0.1:8000/v1",
        "default_model": "qwen-special-2.5",
        "available_models": ["qwen-special-2.5"],
    })

    # Priority 1: Explicit provider_id wins
    p1 = router.resolve(provider_id="prov_custom_qwen")
    assert p1.name() == "openai:qwen-special-2.5"

    # Priority 2: Matching custom registered model wins over legacy env vars
    p2 = router.resolve(model_name="qwen-special-2.5")
    assert p2.name() == "openai:qwen-special-2.5"

    # Priority 3: Default provider
    registry.set_default("prov_custom_qwen")
    p3 = router.resolve(model_name="auto")
    assert p3.name() == "openai:qwen-special-2.5"

    # Unset default -> Priority 4: Legacy env vars win
    store.update("prov_custom_qwen", {"is_default": False})
    p4 = router.resolve(model_name="claude-3-5-sonnet")
    assert isinstance(p4, AnthropicRuntimeProvider)


# ── Scenario 20: Duplicate provider functionality ────────────────────────────
def test_20_duplicate_provider(clean_registry):
    orig = clean_registry.create_provider({
        "id": "prov_orig",
        "name": "Original Provider",
        "provider_type": "OPENAI_COMPATIBLE",
        "base_url": "https://api.orig.com/v1",
        "api_key": "sk-orig-key-1234",
        "default_model": "orig-model",
        "available_models": ["orig-model", "orig-model-fast"],
        "is_default": True,
        "is_enabled": True,
    })

    dup = clean_registry.duplicate_provider("prov_orig")
    assert dup is not None
    assert dup["id"] != "prov_orig"
    assert dup["name"] == "Original Provider (Copy)"
    assert dup["base_url"] == "https://api.orig.com/v1"
    assert dup["default_model"] == "orig-model"
    assert dup["available_models"] == ["orig-model", "orig-model-fast"]
    assert dup["is_default"] is False  # Cloned provider is not default
    assert dup["is_enabled"] is True

    # Check key cloned correctly
    raw_dup = clean_registry.get_provider(dup["id"], include_raw_key=True)
    assert raw_dup["api_key"] == "sk-orig-key-1234"


# ── Scenario 21: REST API provider CRUD endpoints ────────────────────────────
def test_21_rest_api_provider_crud_lifecycle():
    import auth_system
    from app import app

    token = auth_system._make_access_token(1, "tester@aetherion.ai")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    with app.test_client() as client:
        # 1. POST create
        p_data = {
            "name": "REST Test Provider",
            "provider_type": "OPENAI_COMPATIBLE",
            "base_url": "https://api.rest-test.com/v1",
            "api_key": "sk-rest-test-key-5566",
            "default_model": "test-gpt-4o",
        }
        res_create = client.post("/api/providers", headers=headers, json=p_data)
        assert res_create.status_code == 201
        body = res_create.json
        assert body["ok"] is True
        pid = body["provider"]["id"]
        assert body["provider"]["api_key_masked"] == "sk-...5566"

        # 2. GET list
        res_list = client.get("/api/providers", headers=headers)
        assert res_list.status_code == 200
        p_ids = [p["id"] for p in res_list.json["providers"]]
        assert pid in p_ids

        # 3. GET detail
        res_get = client.get(f"/api/providers/{pid}", headers=headers)
        assert res_get.status_code == 200
        assert res_get.json["provider"]["name"] == "REST Test Provider"
        assert "api_key" not in res_get.json["provider"]

        # 4. PUT update
        res_put = client.put(f"/api/providers/{pid}", headers=headers, json={"name": "REST Test Provider (Updated)"})
        assert res_put.status_code == 200
        assert res_put.json["provider"]["name"] == "REST Test Provider (Updated)"

        # 5. POST default
        res_def = client.post(f"/api/providers/{pid}/default", headers=headers)
        assert res_def.status_code == 200
        assert res_def.json["provider"]["is_default"] is True

        # 6. DELETE
        res_del = client.delete(f"/api/providers/{pid}", headers=headers)
        assert res_del.status_code == 200
        assert res_del.json["ok"] is True


# ── Scenario 22: REST API provider health check endpoint ─────────────────────
def test_22_rest_api_provider_health_check():
    import auth_system
    from app import app

    token = auth_system._make_access_token(1, "tester@aetherion.ai")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    with app.test_client() as client:
        # Create provider
        p_data = {
            "name": "REST Health Check Provider",
            "provider_type": "OPENAI_COMPATIBLE",
            "base_url": "https://api.mock-health.com/v1",
            "api_key": "sk-health-key-1234",
            "default_model": "gpt-4o",
        }
        res_create = client.post("/api/providers", headers=headers, json=p_data)
        pid = res_create.json["provider"]["id"]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}

        with patch("httpx.Client.get", return_value=mock_resp):
            res_test = client.post(f"/api/providers/{pid}/test", headers=headers)
            assert res_test.status_code == 200
            assert res_test.json["ok"] is True
            assert res_test.json["status"] == "CONNECTED"
            assert "latency_ms" in res_test.json

        # Cleanup
        client.delete(f"/api/providers/{pid}", headers=headers)


# ── Scenario 23: Multi-User Object-Level Authorization (BOLA Protection) ─────
def test_23_multi_user_bola_protection():
    import auth_system
    from app import app

    token_alice = auth_system._make_access_token(101, "alice@aetherion.ai")
    token_bob = auth_system._make_access_token(202, "bob@aetherion.ai")
    headers_alice = {"Authorization": f"Bearer {token_alice}", "Content-Type": "application/json"}
    headers_bob = {"Authorization": f"Bearer {token_bob}", "Content-Type": "application/json"}

    with app.test_client() as client:
        # Alice creates a private provider
        res_a = client.post("/api/providers", headers=headers_alice, json={
            "name": "Alice Secret Provider",
            "provider_type": "OPENAI_COMPATIBLE",
            "base_url": "https://api.alice.corp/v1",
            "api_key": "sk-alice-secret-token-778899",
            "default_model": "alice-gpt",
        })
        assert res_a.status_code == 201
        pid_a = res_a.json["provider"]["id"]

        # Bob attempts to list providers — Alice's provider must NOT be present
        res_b_list = client.get("/api/providers", headers=headers_bob)
        assert res_b_list.status_code == 200
        b_ids = [p["id"] for p in res_b_list.json.get("providers", [])]
        assert pid_a not in b_ids

        # Bob attempts to read Alice's provider detail — must return 404
        res_b_get = client.get(f"/api/providers/{pid_a}", headers=headers_bob)
        assert res_b_get.status_code == 404

        # Bob attempts to update Alice's provider — must return 404
        res_b_put = client.put(f"/api/providers/{pid_a}", headers=headers_bob, json={"name": "Hacked By Bob"})
        assert res_b_put.status_code == 404

        # Bob attempts to delete Alice's provider — must return 404
        res_b_del = client.delete(f"/api/providers/{pid_a}", headers=headers_bob)
        assert res_b_del.status_code == 404

        # Alice cleans up her own provider successfully
        res_a_del = client.delete(f"/api/providers/{pid_a}", headers=headers_alice)
        assert res_a_del.status_code == 200


# ── Scenario 24: Provider Capabilities Enforcement in RealAgentRuntime ───────
def test_24_provider_capabilities_enforcement(tmp_path):
    from execution.agent_runtime import RealAgentRuntime

    class NonToolProvider(BaseModelProvider):
        def __init__(self):
            self.passed_tools = None
            self.received_messages = None

        def name(self):
            return "non_tool_provider"

        def is_available(self):
            return True

        def capabilities(self):
            return ProviderCapabilities(supports_tools=False, supports_system_messages=False)

        def chat(self, messages, tools):
            self.passed_tools = tools
            self.received_messages = messages
            return ModelResponse(
                text="Executed without tools directly.",
                tool_calls=[],
            )

        def health_check(self):
            return {"status": "CONNECTED", "latency_ms": 1.0, "diagnostics": "ok"}

    prov = NonToolProvider()
    runtime = RealAgentRuntime(workspace_dir=tmp_path, session_id="cap_test_sess", provider=prov)
    events = list(runtime.run_stream("Test task with non-tool provider"))

    # Verify no tools were passed to provider
    assert prov.passed_tools == []
    # Verify system messages were coalesced into user message
    roles = [m["role"] for m in prov.received_messages]
    assert "system" not in roles
    assert "user" in roles
    # Verify execution finished
    assert any(e.get("type") in ("state_change", "done") for e in events)


# ── Scenario 25: Secret Redaction in Provider Error Messages ─────────────────
def test_25_secret_redaction_in_provider_errors():
    secret_key = "sk-sensitive-token-never-expose-123456"
    prov = OpenAIRuntimeProvider(api_key=secret_key, base_url="https://mock.api.com/v1")

    mock_401 = MagicMock()
    mock_401.status_code = 401
    mock_401.text = f"Invalid API key: {secret_key} provided"

    with patch("httpx.Client.post", return_value=mock_401):
        with pytest.raises(ProviderUnavailableException) as exc_info:
            prov.chat(messages=[{"role": "user", "content": "hello"}], tools=[])

        err_text = str(exc_info.value)
        assert secret_key not in err_text
        assert "[REDACTED_API_KEY]" in err_text


