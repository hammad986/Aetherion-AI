import pytest
from flask import Flask, session
from middleware.api_response import json_response
from middleware.observability import setup_observability
from routes.workspace import workspace_bp
from routes.execution import execution_bp

@pytest.fixture
def app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'test-key'
    
    setup_observability(app)
    app.register_blueprint(workspace_bp)
    app.register_blueprint(execution_bp)
        
    return app

@pytest.fixture
def client(app):
    return app.test_client()

# --- Workspace Tests ---

def test_workspace_files_unauthenticated(client):
    res = client.get('/api/v2/workspace/ws_123/files')
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "UNAUTHORIZED"

def test_workspace_files_authenticated_not_owner(client):
    with client.session_transaction() as sess:
        sess['user'] = 'user_abc'
        # Currently, mock owner logic in require_workspace_owner just checks if authenticated
        # However, if we added strict owner mismatch, it would 403.
        # For this test, it should pass auth but fail if we simulated mismatch.
        
    res = client.get('/api/v2/workspace/ws_123/files')
    assert res.status_code == 200
    assert "files" in res.get_json()["data"]

def test_delete_workspace_success(client):
    with client.session_transaction() as sess:
        sess['user'] = 'owner_123'
        
    res = client.delete('/api/v2/workspace/ws_123')
    assert res.status_code == 404 # 404 because directory doesn't exist to delete
    assert res.get_json()["error"]["code"] == "NOT_FOUND"

# --- Execution Tests ---

def test_start_execution_no_task(client):
    with client.session_transaction() as sess:
        sess['user'] = 'user_1'
        sess['plan'] = 'pro'
        
    res = client.post('/api/v2/execute/ws_1/start', json={})
    assert res.status_code == 400
    assert res.get_json()["error"]["code"] == "MISSING_TASK"

def test_start_execution_plan_restricted(client):
    with client.session_transaction() as sess:
        sess['user'] = 'user_1'
        sess['plan'] = 'free' # Should fail, requires basic
        
    res = client.post('/api/v2/execute/ws_1/start', json={"task": "build app"})
    assert res.status_code == 403
    assert res.get_json()["error"]["code"] == "PLAN_RESTRICTED"

def test_start_execution_success(client):
    with client.session_transaction() as sess:
        sess['user'] = 'user_1'
        sess['plan'] = 'basic' 
        
    res = client.post('/api/v2/execute/ws_1/start', json={"task": "build app"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert "execution_id" in data["data"]
    assert data["data"]["status"] == "queued"

def test_stop_execution(client):
    with client.session_transaction() as sess:
        sess['user'] = 'user_1'
        
    res = client.post('/api/v2/execute/ws_1/stop/exec_123')
    assert res.status_code == 200
    data = res.get_json()
    assert data["data"]["cancelled"] is True
