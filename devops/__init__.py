"""
devops/__init__.py — Aetherion Autonomous DevOps Package
=========================================================
Exports the canonical singletons for all self-healing and operational
intelligence subsystems.

Dependency order (respect for import):
  health_monitor → playbook_engine → deployment_governor → disaster_recovery
"""

from devops.health_monitor      import HealthMonitor, get_health_monitor
from devops.playbook_engine      import PlaybookEngine, PlaybookResult, PlaybookStatus, global_playbook_engine
from devops.deployment_governor  import DeploymentGovernor, global_deployment_governor
from devops.disaster_recovery    import DisasterRecovery, global_disaster_recovery

__all__ = [
    "HealthMonitor", "get_health_monitor",
    "PlaybookEngine", "PlaybookResult", "PlaybookStatus", "global_playbook_engine",
    "DeploymentGovernor", "global_deployment_governor",
    "DisasterRecovery", "global_disaster_recovery",
]
