import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger("nexora.orchestrator")

@dataclass
class OrchestrationNode:
    """Represents a discrete step or subtask in an orchestration DAG."""
    node_id: str
    agent_role: str
    payload: Dict[str, Any]
    dependencies: List[str]  # node_ids that must complete before this node starts

class BaseAgentOrchestrator(ABC):
    """
    Interface boundary for Multi-Agent execution DAGs.
    Separates the logic of 'how tasks are divided' from 'how threads are run' (worker.py).
    """
    
    @abstractmethod
    def plan(self, objective: str) -> List[OrchestrationNode]:
        """Decomposes a high-level objective into an execution graph."""
        pass
        
    @abstractmethod
    def evaluate_node_ready(self, node: OrchestrationNode, completed_nodes: List[str]) -> bool:
        """Determines if a subtask is unblocked and ready for worker dispatch."""
        pass

    @abstractmethod
    def resolve_context(self, node: OrchestrationNode, memory_store: Dict[str, Any]) -> Dict[str, Any]:
        """Merges shared execution context (memory) into the specific subtask payload."""
        pass

class LinearOrchestrator(BaseAgentOrchestrator):
    """Fallback implementation for standard single-agent sequential tasks."""
    def plan(self, objective: str) -> List[OrchestrationNode]:
        return [
            OrchestrationNode(
                node_id="root_task",
                agent_role="primary",
                payload={"objective": objective},
                dependencies=[]
            )
        ]
        
    def evaluate_node_ready(self, node: OrchestrationNode, completed_nodes: List[str]) -> bool:
        return all(dep in completed_nodes for dep in node.dependencies)
        
    def resolve_context(self, node: OrchestrationNode, memory_store: Dict[str, Any]) -> Dict[str, Any]:
        return {**node.payload, "context": memory_store}
