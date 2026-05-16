import pytest
import time
from execution.job_manager import JobManager
from execution.worker import ResourceGovernance

@pytest.fixture
def job_manager():
    return JobManager()

def test_task_lifecycle_events(job_manager):
    """Verifies execution correctly flows from queued to completed."""
    # We bypass SSE broadcasting logic for raw unit testing by replacing it if needed,
    # but the mock runner uses safe default dicts.
    task = job_manager.submit_task(
        session_id="test_sess_1", 
        payload={"cmd": "test"}
    )
    
    assert task.status in ["queued", "running"]
    
    # Wait for the mock runner to finish (5 loops * 0.5s = ~2.5s)
    # We'll poll up to 3 seconds
    timeout = 4.0
    start_t = time.time()
    while time.time() - start_t < timeout:
        status = job_manager.get_status(task.execution_id)
        if status["status"] in ["completed", "failed", "cancelled"]:
            break
        time.sleep(0.1)
        
    final_status = job_manager.get_status(task.execution_id)
    # Task gets removed from active dictionary after completion
    assert task.status == "completed"
    assert task.started_at is not None
    assert task.completed_at is not None

def test_task_cancellation(job_manager):
    """Verifies execution threads respect cancellation signals."""
    task = job_manager.submit_task(
        session_id="test_sess_2", 
        payload={"cmd": "test_long"}
    )
    
    time.sleep(0.6) # Let it start and run 1 loop
    
    assert job_manager.get_status(task.execution_id)["status"] == "running"
    
    # Issue Cancel
    res = job_manager.cancel_task(task.execution_id)
    assert res is True
    
    # Wait for worker thread to acknowledge and exit
    time.sleep(0.2)
    
    final_status = job_manager.get_status(task.execution_id)
    # The status check falls back to "unknown_or_archived" if removed from active dict,
    # so we assert the task object directly since we hold a reference in the test.
    assert task.status == "cancelled"

def test_ttl_enforcement(job_manager):
    """Verifies the task runner raises TimeoutError if TTL is exceeded."""
    # Create very strict limits
    limits = ResourceGovernance(ttl_seconds=0.7) 
    
    task = job_manager.submit_task(
        session_id="test_sess_3", 
        payload={"cmd": "test_ttl"},
        limits=limits
    )
    
    # Wait for execution past the 0.7s TTL
    time.sleep(1.5)
    
    # Our mock_runner calls `t.check_ttl()`, so it should raise an exception and fail
    assert task.status == "failed"
