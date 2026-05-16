import logging
from typing import List, Dict, Any, Set
from dataclasses import dataclass, field
from execution.orchestrator import OrchestrationNode

logger = logging.getLogger("nexora.graph")

@dataclass
class DAGSchedule:
    """Represents a structured map of dependencies and executable nodes."""
    nodes: Dict[str, OrchestrationNode] = field(default_factory=dict)
    edges: List[Dict[str, str]] = field(default_factory=list) # {"source": id, "target": id}
    completed_nodes: Set[str] = field(default_factory=set)

class OrchestrationDAG:
    """
    Manages dependency resolution and subtask scheduling rules.
    Prepares the backend interface for future graph visualization and multi-agent roles.
    """
    def __init__(self):
        self.schedule = DAGSchedule()

    def add_node(self, node: OrchestrationNode):
        """Registers a node into the execution graph."""
        self.schedule.nodes[node.node_id] = node
        for dep in node.dependencies:
            if dep not in self.schedule.nodes:
                logger.warning(f"Node {node.node_id} declared unknown dependency {dep}")
            self.schedule.edges.append({"source": dep, "target": node.node_id})

    def mark_completed(self, node_id: str):
        """Records a node's completion, potentially unblocking downstreams."""
        if node_id in self.schedule.nodes:
            self.schedule.completed_nodes.add(node_id)

    def get_executable_nodes(self) -> List[OrchestrationNode]:
        """Identifies nodes whose dependencies are fully satisfied."""
        executable = []
        for node_id, node in self.schedule.nodes.items():
            if node_id in self.schedule.completed_nodes:
                continue
            
            # Check if all dependencies are in the completed set
            unmet_deps = [dep for dep in node.dependencies if dep not in self.schedule.completed_nodes]
            if not unmet_deps:
                executable.append(node)
                
        return executable

    def export_graph_visualization(self) -> Dict[str, Any]:
        """
        Exports the DAG topology for the frontend Observability Inspector.
        Contains lineage, ancestry, and runtime transitions.
        """
        nodes_out = []
        for node_id, node in self.schedule.nodes.items():
            nodes_out.append({
                "id": node_id,
                "role": node.agent_role,
                "status": "completed" if node_id in self.schedule.completed_nodes else "pending",
                "label": node.payload.get("objective", f"Task {node_id}")
            })
            
        return {
            "nodes": nodes_out,
            "edges": self.schedule.edges,
            "metrics": {
                "total_nodes": len(self.schedule.nodes),
                "completed": len(self.schedule.completed_nodes),
                "pending": len(self.schedule.nodes) - len(self.schedule.completed_nodes)
            }
        }
