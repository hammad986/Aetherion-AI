"""
cluster/__init__.py — Aetherion Distributed Cluster Package
============================================================
Exports all cluster-level orchestration singletons.

Import order matters:
  control_plane → distributed_lock → task_orchestrator → event_fabric → resource_governor
"""

from cluster.control_plane      import ControlPlane, NodeRole, NodeState, get_control_plane
from cluster.distributed_lock   import DistributedLockManager, LockError, get_lock_manager
from cluster.task_orchestrator   import TaskOrchestrator, LeaseStatus, get_task_orchestrator
from cluster.event_fabric        import EventFabric, get_event_fabric
from cluster.resource_governor   import ClusterResourceGovernor, get_cluster_resource_governor

__all__ = [
    "ControlPlane", "NodeRole", "NodeState", "get_control_plane",
    "DistributedLockManager", "LockError", "get_lock_manager",
    "TaskOrchestrator", "LeaseStatus", "get_task_orchestrator",
    "EventFabric", "get_event_fabric",
    "ClusterResourceGovernor", "get_cluster_resource_governor",
]
