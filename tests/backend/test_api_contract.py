import pytest
from flask import Flask, session
from middleware.api_response import json_response, success_response, error_response
from middleware.observability import setup_observability
from middleware.guards import require_auth, require_plan
from routes.health import health_bp

@pytest.fixture
def app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'test-key'
    
    # Setup Observability
    setup_observability(app)
    
    # Register extracted Blueprint
    app.register_blueprint(health_bp)
    
    # Add a mock route for testing plan guards directly
    @app.route("/api/v2/test/pro")
    @require_plan("pro")
    def pro_route():
        return success_response(data={"feature": "pro_feature"})
        
    return app

@pytest.fixture
def client(app):
    return app.test_client()

def test_success_response_contract(app):
    with app.app_context():
        resp, code = success_response(data={"foo": "bar"}, meta={"test": True})
        data = resp.get_json()
        
        assert code == 200
        assert data["ok"] is True
        assert data["data"]["foo"] == "bar"
        assert "error" in data
        assert data["meta"]["test"] is True

def test_error_response_contract(app):
    with app.app_context():
        resp, code = error_response(message="Bad Request", code="BAD_REQ", status=400)
        data = resp.get_json()
        
        assert code == 400
        assert data["ok"] is False
        assert data["data"] is None
        assert data["error"]["code"] == "BAD_REQ"
        assert data["error"]["message"] == "Bad Request"

def test_health_blueprint_unauthenticated(client):
    res = client.get('/api/v2/health')
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["data"]["status"] == "ok"
    
def test_secure_health_blocked(client):
    res = client.get('/api/v2/health/secure')
    assert res.status_code == 401
    data = res.get_json()
    assert data["ok"] is False
    assert data["error"]["code"] == "UNAUTHORIZED"

def test_secure_health_allowed(client):
    with client.session_transaction() as sess:
        sess['user'] = 'test_user'
        
    res = client.get('/api/v2/health/secure')
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["data"]["status"] == "secure_ok"

def test_plan_guard_blocked(client):
    with client.session_transaction() as sess:
        sess['user'] = 'test_user'
        sess['plan'] = 'basic'
        
    res = client.get('/api/v2/test/pro')
    assert res.status_code == 403
    data = res.get_json()
    assert data["error"]["code"] == "PLAN_RESTRICTED"

def test_plan_guard_allowed(client):
    with client.session_transaction() as sess:
        sess['user'] = 'test_user'
        sess['plan'] = 'enterprise'
        
    res = client.get('/api/v2/test/pro')
    assert res.status_code == 200
    data = res.get_json()
    assert data["data"]["feature"] == "pro_feature"
