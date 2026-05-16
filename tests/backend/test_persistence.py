import pytest
import time
import os
import sqlite3
import tempfile
from execution.store import ExecutionStore
from execution.recovery import RuntimeRecovery
from execution.events import create_event, EventTypes

@pytest.fixture
def store():
    # Use a real tempfile because ":memory:" databases don't persist across separate connect() calls
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = ExecutionStore(path)
    yield s
    try:
        os.remove(path)
    except OSError:
        pass

def test_event_sourcing_append_and_retrieve(store):
    """Verifies the append-only event log maintains chronologic state."""
    evt1 = create_event(EventTypes.TASK_STARTED, "sess_1", "exec_A")
    evt2 = create_event(EventTypes.TOOL_CALLED, "sess_1", "exec_A", tool="file_write")
    
    # Store events
    assert store.append_event(evt1, correlation_id="req_123") is True
    time.sleep(0.01) # ensure timestamp difference
    assert store.append_event(evt2, correlation_id="req_123") is True
    
    # Retrieve and verify order
    history = store.get_events("exec_A")
    assert len(history) == 2
    assert history[0]["event_type"] == EventTypes.TASK_STARTED
    assert history[1]["event_type"] == EventTypes.TOOL_CALLED
    assert history[1]["payload"]["tool"] == "file_write"

def test_execution_snapshot_upsert(store):
    """Verifies that the executions table correctly maintains the latest state."""
    store.upsert_execution("exec_B", "sess_2", "queued", {})
    store.upsert_execution("exec_B", "sess_2", "running", {"progress": 10})
    
    # Raw query to verify UPSERT logic worked
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM executions WHERE execution_id='exec_B'").fetchone()
        
    assert row is not None
    assert row["status"] == "running"
    assert "progress" in row["payload"]

def test_recovery_stale_job_sweep(store):
    """Verifies that the recovery layer correctly identifies and terminates orphaned executions."""
    recovery = RuntimeRecovery(store)
    
    # Create an old running execution (simulating a crash 2 hours ago)
    old_time = time.time() - 7200
    store.upsert_execution("exec_C", "sess_3", "running", {}, started_at=old_time)
    
    # We must manually set updated_at to the past because upsert sets it to now()
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE executions SET updated_at = ? WHERE execution_id = 'exec_C'", (old_time,))
        
    # Create a fresh execution
    store.upsert_execution("exec_D", "sess_3", "running", {})
    
    stale = store.get_stale_executions(timeout_seconds=3600)
    assert "exec_C" in stale
    assert "exec_D" not in stale
    
    # Trigger Sweep
    recovery.sweep_stale_jobs()
    
    # Verify the snapshot was updated
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute("SELECT status FROM executions WHERE execution_id='exec_C'").fetchone()
    assert row[0] == "failed"
    
    # Verify the event log recorded the failure reason
    events = store.get_events("exec_C")
    assert len(events) == 1
    assert events[0]["event_type"] == EventTypes.TASK_FAILED
    assert "orphaned" in events[0]["payload"]["error"]
