import pytest
import time
import threading
from execution.store import ExecutionStore
from execution.events import create_event, EventTypes
import os
import tempfile

@pytest.fixture
def temp_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = ExecutionStore(path)
    yield s
    try:
        os.remove(path)
    except OSError:
        pass

def test_stress_concurrent_writes(temp_store):
    """
    Simulates high-concurrency event logging from multiple active workers 
    to validate SQLite WAL integrity and threading lock safety.
    """
    worker_count = 10
    events_per_worker = 100
    
    def worker_loop(worker_id):
        exec_id = f"exec_stress_{worker_id}"
        temp_store.append_event(create_event(EventTypes.TASK_STARTED, "sess_1", exec_id))
        
        for i in range(events_per_worker):
            temp_store.append_event(create_event(EventTypes.TOOL_CALLED, "sess_1", exec_id, tool="mock_tool", seq=i))
            
        temp_store.append_event(create_event(EventTypes.TASK_COMPLETED, "sess_1", exec_id))

    threads = []
    start_time = time.time()
    for i in range(worker_count):
        t = threading.Thread(target=worker_loop, args=(i,))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    duration = time.time() - start_time
    
    # Validation: 10 workers * (1 start + 100 tools + 1 complete) = 1020 events
    import sqlite3
    with sqlite3.connect(temp_store.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
        
    assert count == 1020, f"Expected 1020 events, found {count}"
    assert duration < 5.0, f"Stress test took too long: {duration}s"

def test_queue_saturation_recovery(temp_store):
    """
    Simulates a queue saturation scenario where a massive burst of tasks 
    is submitted, testing the DB UPSERT handling limits.
    """
    burst_size = 500
    
    # Mass UPSERT simulation
    for i in range(burst_size):
        temp_store.append_event(create_event(EventTypes.TASK_STARTED, "sess_1", f"exec_burst_{i}"))
        
    import sqlite3
    with sqlite3.connect(temp_store.db_path) as conn:
        running_count = conn.execute("SELECT COUNT(*) FROM executions WHERE status='running'").fetchone()[0]
        
    assert running_count == burst_size
