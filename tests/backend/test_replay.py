import pytest
import os
import tempfile
from execution.store import ExecutionStore
from execution.events import create_event, EventTypes
from execution.replay import ExecutionReplayEngine
from execution.orchestrator import OrchestrationNode
from execution.graph import OrchestrationDAG

@pytest.fixture
def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = ExecutionStore(path)
    yield s
    try:
        os.remove(path)
    except OSError:
        pass

def test_execution_replay_timeline_reconstruction(store):
    engine = ExecutionReplayEngine(store)
    
    # 1. Seed the store with chronological events
    store.append_event(create_event(EventTypes.TASK_STARTED, "sess_1", "exec_1"))
    store.append_event(create_event(EventTypes.TOOL_CALLED, "sess_1", "exec_1", tool="file_read"))
    store.append_event(create_event(EventTypes.FILE_MODIFIED, "sess_1", "exec_1", file_path="main.py"))
    store.append_event(create_event(EventTypes.TASK_COMPLETED, "sess_1", "exec_1"))
    
    # 2. Reconstruct timeline
    replay_data = engine.reconstruct_timeline("exec_1")
    
    # Verify Summary
    assert replay_data["summary"]["final_status"] == "completed"
    assert replay_data["summary"]["total_tools_called"] == 1
    assert replay_data["summary"]["total_files_modified"] == 1
    assert replay_data["summary"]["has_errors"] is False
    
    # Verify Timeline
    timeline = replay_data["timeline"]
    assert len(timeline) == 4
    assert timeline[0]["event"] == EventTypes.TASK_STARTED
    assert timeline[1]["tool"] == "file_read"
    assert timeline[2]["file"] == "main.py"
    assert timeline[3]["event"] == EventTypes.TASK_COMPLETED

def test_dag_dependency_resolution():
    dag = OrchestrationDAG()
    
    # Node A: No dependencies
    node_a = OrchestrationNode("A", "researcher", {}, [])
    # Node B: Depends on A
    node_b = OrchestrationNode("B", "coder", {}, ["A"])
    # Node C: Depends on B
    node_c = OrchestrationNode("C", "reviewer", {}, ["B"])
    
    dag.add_node(node_a)
    dag.add_node(node_b)
    dag.add_node(node_c)
    
    # Initially, only A is executable
    exec_nodes = dag.get_executable_nodes()
    assert len(exec_nodes) == 1
    assert exec_nodes[0].node_id == "A"
    
    # Complete A -> B should unlock
    dag.mark_completed("A")
    exec_nodes = dag.get_executable_nodes()
    assert len(exec_nodes) == 1
    assert exec_nodes[0].node_id == "B"
    
    # Export Visualization
    vis = dag.export_graph_visualization()
    assert vis["metrics"]["total_nodes"] == 3
    assert vis["metrics"]["completed"] == 1
    assert vis["metrics"]["pending"] == 2
    assert len(vis["edges"]) == 2 # A->B, B->C
