"""
cli/tests/test_closure_regressions.py
Regression tests for:
- Session renaming and title updating
- TokenStore & Settings token/access_token fallback and JWT expiration
- Offline banner dismissability
- Personal API key label validation and prefix masking
- Network error classification for connection errors
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest
from aetherion_cli.auth.token_store import TokenStore
from aetherion_cli.config import get_token, set_token, clear_token
from aetherion_cli.session_store import create_session, get_session, update_session_title
from aetherion_cli.tui.gateway import gateway, classify_network_error
from aetherion_cli.tui.state import RuntimeState, Screen, state


def test_session_rename_lifecycle(tmp_path, monkeypatch):
    """Verify session renaming persists to DB and updates gateway/state."""
    # Create test session
    session_id = create_session(model="test-model", title="Initial Title")
    assert session_id is not None
    s = get_session(session_id)
    assert s["title"] == "Initial Title"

    # Update via session_store directly
    ok = update_session_title(session_id, "Renamed Direct")
    assert ok is True
    s = get_session(session_id)
    assert s["title"] == "Renamed Direct"

    # Update via Gateway
    ok_gw = gateway.rename_session(session_id, "Renamed via Gateway")
    assert ok_gw is True
    s = get_session(session_id)
    assert s["title"] == "Renamed via Gateway"


def test_token_store_access_token_fallback(tmp_path, monkeypatch):
    """Verify TokenStore reads 'access_token' when 'token' is missing."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"access_token": "aeth_test_token_123"}), encoding="utf-8")
    monkeypatch.setattr("aetherion_cli.auth.token_store.CONFIG_FILE", cfg_file)
    monkeypatch.setattr("aetherion_cli.config.settings.CONFIG_FILE", cfg_file)
    monkeypatch.setattr("aetherion_cli.auth.token_store._check_keyring_available", lambda: False)

    # Both TokenStore and config.get_token should resolve the access_token
    assert TokenStore.get_access_token() == "aeth_test_token_123"
    assert get_token() == "aeth_test_token_123"

    # Clear token removes both keys
    TokenStore.clear_tokens()
    assert TokenStore.get_access_token() is None
    assert get_token() is None


def test_jwt_expiration_handling(tmp_path, monkeypatch):
    """Verify TokenStore accurately detects expired JWT tokens."""
    cfg_file = tmp_path / "config.json"
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    # Expired 1 hour ago
    expired_payload = base64.urlsafe_b64encode(
        json.dumps({"sub": "u123", "exp": int(time.time() - 3600)}).encode()
    ).decode().rstrip("=")
    expired_jwt = f"{header}.{expired_payload}.signature"

    cfg_file.write_text(json.dumps({"token": expired_jwt}), encoding="utf-8")
    monkeypatch.setattr("aetherion_cli.auth.token_store.CONFIG_FILE", cfg_file)
    monkeypatch.setattr("aetherion_cli.auth.token_store._check_keyring_available", lambda: False)

    status = TokenStore.get_status()
    assert status["expired"] is True
    assert status["authenticated"] is False


def test_offline_banner_dismissal():
    """Verify offline banner dismissal flag in TUI state."""
    state.set_offline_mode(True)
    assert state.offline_mode is True
    assert state.offline_banner_dismissed is False

    # Dismiss banner
    state.offline_banner_dismissed = True
    assert state.offline_banner_dismissed is True
    # Reset
    state.offline_banner_dismissed = False
    state.set_offline_mode(False)


def test_personal_api_key_generation_and_masking(tmp_path, monkeypatch):
    """Verify personal API key generation, storage, and prefix masking."""
    keys_file = tmp_path / "api_keys.json"
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    entry = gateway.generate_personal_api_key("ci-worker")
    assert entry["name"] == "ci-worker"
    assert entry["key"].startswith("aeth_live_")
    assert entry["prefix"].startswith("aeth_")
    assert entry["prefix"].endswith("****")
    assert entry["type"] == "Local / Offline"

    keys = gateway.list_personal_api_keys()
    assert len(keys) == 1
    assert keys[0]["id"] == entry["id"]
    assert keys[0]["type"] == "Local / Offline"

    # Revoke
    ok = gateway.revoke_personal_api_key(entry["id"])
    assert ok is True
    assert len(gateway.list_personal_api_keys()) == 0


def test_server_vs_local_api_key_distinction(tmp_path, monkeypatch):
    """Verify distinction between server-registered keys and local/offline keys."""
    keys_dir = tmp_path / ".aetherion"
    keys_dir.mkdir(parents=True, exist_ok=True)
    keys_file = keys_dir / "api_keys.json"
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    # Simulate cached server key and local offline key
    mock_keys = [
        {
            "id": "key_9876543210abcdef",
            "name": "prod-server-key",
            "prefix": "aeth_1234****",
            "created": "2026-10-10 12:00",
            "last_used": "Never",
            "type": "Server Key"
        },
        {
            "id": "dev_12345678",
            "name": "offline-dev-key",
            "prefix": "aeth_5678****",
            "created": "2026-10-10 12:05",
            "last_used": "Never",
            "type": "Local / Offline"
        }
    ]
    keys_file.write_text(json.dumps(mock_keys), encoding="utf-8")

    listed = gateway.list_personal_api_keys()
    assert len(listed) == 2
    assert listed[0]["type"] == "Server Key"
    assert listed[0]["id"].startswith("key_")
    assert listed[1]["type"] == "Local / Offline"
    assert listed[1]["id"].startswith("dev_")


def test_network_error_classification_connection_refused():
    """Verify socket 10061 and connection refused map to user-friendly messages."""
    raw_error = "ConnectError: [WinError 10061] No connection could be made because the target machine actively refused it"
    rs, msg = classify_network_error(raw_error)
    assert rs == RuntimeState.OFFLINE
    assert "Cannot connect to server" in msg or "backend" in msg.lower()


def test_device_poll_rfc8628_handling(monkeypatch):
    """Verify device_poll recognizes RFC 8628 HTTP 400 authorization_pending."""
    from unittest.mock import MagicMock
    import httpx

    fake_response = MagicMock()
    fake_response.status_code = 400
    fake_response.json.return_value = {"error": "authorization_pending"}

    fake_client = MagicMock()
    fake_client.get.return_value = fake_response
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = None

    monkeypatch.setattr(gateway, "_client", lambda **kwargs: fake_client)
    res = gateway.device_poll("test_code_123")
    assert res == {"status": "pending"}


def test_device_poll_authorized_handoff(monkeypatch):
    """Verify complete_device_auth saves tokens and updates state."""
    tokens_saved = {}
    monkeypatch.setattr("aetherion_cli.config.set_token", lambda t: tokens_saved.update(access=t))
    monkeypatch.setattr("aetherion_cli.config.set_refresh_token", lambda rt: tokens_saved.update(refresh=rt))

    gateway.complete_device_auth(
        access_token="test_access_jwt",
        refresh_token="test_refresh_jwt",
        user={"id": 42, "email": "operator@aetherion.test", "plan": "Pro"},
    )

    assert tokens_saved.get("access") == "test_access_jwt"
    assert tokens_saved.get("refresh") == "test_refresh_jwt"
    assert state.user_email == "operator@aetherion.test"
    assert state.user_plan == "Pro"
    assert state.connected is True


def test_device_poll_states_deterministic(monkeypatch):
    """Verify gateway.device_poll accurately parses all RFC 8628 device flow states."""
    from unittest.mock import MagicMock

    # 1. Denied
    fake_resp_denied = MagicMock()
    fake_resp_denied.status_code = 200
    fake_resp_denied.json.return_value = {"status": "denied"}
    fake_resp_denied.raise_for_status = MagicMock()

    client_mock = MagicMock()
    client_mock.get.return_value = fake_resp_denied
    client_mock.__enter__.return_value = client_mock
    client_mock.__exit__.return_value = None

    monkeypatch.setattr(gateway, "_client", lambda **kwargs: client_mock)
    res = gateway.device_poll("code_denied")
    assert res.get("status") == "denied"

    # 2. Expired
    fake_resp_expired = MagicMock()
    fake_resp_expired.status_code = 200
    fake_resp_expired.json.return_value = {"status": "expired"}
    fake_resp_expired.raise_for_status = MagicMock()
    client_mock.get.return_value = fake_resp_expired

    res = gateway.device_poll("code_expired")
    assert res.get("status") == "expired"

    # 3. Authorized
    fake_resp_auth = MagicMock()
    fake_resp_auth.status_code = 200
    fake_resp_auth.json.return_value = {
        "status": "authorized",
        "access_token": "aeth_access_jwt_valid",
        "refresh_token": "aeth_refresh_jwt_valid",
        "user": {"id": 101, "email": "dev@aetherion.test", "role": "admin"},
    }
    fake_resp_auth.raise_for_status = MagicMock()
    client_mock.get.return_value = fake_resp_auth

    res = gateway.device_poll("code_auth")
    assert res.get("status") == "authorized"
    assert res.get("access_token") == "aeth_access_jwt_valid"


def test_device_auth_flow_deterministic_states(tmp_path, monkeypatch):
    """Verify DeviceAuthFlow.poll handles authorized, denied, and expired states deterministically."""
    from unittest.mock import MagicMock
    import httpx
    from aetherion_cli.auth.device_flow import DeviceAuthFlow

    flow = DeviceAuthFlow("http://test.aetherion.local")
    monkeypatch.setattr("time.sleep", lambda s: None)

    # Mock TokenStore methods to use memory
    stored = {}
    monkeypatch.setattr(TokenStore, "set_access_token", lambda t: stored.update(access=t) or "memory")
    monkeypatch.setattr(TokenStore, "set_refresh_token", lambda t: stored.update(refresh=t))

    # Case A: 200 Authorized
    resp_auth = MagicMock(spec=httpx.Response)
    resp_auth.status_code = 200
    resp_auth.json.return_value = {
        "access_token": "live_jwt_tok",
        "refresh_token": "live_ref_tok",
        "user": {"email": "user@test.org"},
    }

    client_mock = MagicMock(spec=httpx.Client)
    client_mock.post.return_value = resp_auth
    client_mock.__enter__.return_value = client_mock
    client_mock.__exit__.return_value = None
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_mock)

    result_auth = flow.poll("test_dev_code", interval=1, expires_in=10, quiet=True)
    assert result_auth["success"] is True
    assert stored["access"] == "live_jwt_tok"
    assert stored["refresh"] == "live_ref_tok"

    # Case B: 200 Denied
    resp_denied = MagicMock(spec=httpx.Response)
    resp_denied.status_code = 200
    resp_denied.json.return_value = {"status": "denied"}
    client_mock.post.return_value = resp_denied

    result_denied = flow.poll("test_dev_code", interval=1, expires_in=10, quiet=True)
    assert result_denied["success"] is False
    assert "declined" in result_denied["error"].lower()

    # Case C: 200 Expired
    resp_expired = MagicMock(spec=httpx.Response)
    resp_expired.status_code = 200
    resp_expired.json.return_value = {"status": "expired"}
    client_mock.post.return_value = resp_expired

    result_expired = flow.poll("test_dev_code", interval=1, expires_in=10, quiet=True)
    assert result_expired["success"] is False
    assert "expired" in result_expired["error"].lower()

    # Case D: RFC 8628 400 error codes
    resp_400_declined = MagicMock(spec=httpx.Response)
    resp_400_declined.status_code = 400
    resp_400_declined.headers = {"content-type": "application/json"}
    resp_400_declined.json.return_value = {"error": "authorization_declined"}
    client_mock.post.return_value = resp_400_declined

    result_400 = flow.poll("test_dev_code", interval=1, expires_in=10, quiet=True)
    assert result_400["success"] is False
    assert "declined" in result_400["error"].lower()

