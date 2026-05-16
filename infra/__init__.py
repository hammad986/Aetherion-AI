"""
infra/__init__.py — Aetherion Production Infrastructure Package
===============================================================
Exports the canonical singletons for all production infra components.

Import order is intentional (db_adapter must init before others).
"""

from infra.db_adapter import get_db, db_sessions, db_governance, db_exec_store, db_resource
from infra.event_bus import get_event_bus
from infra.tenant import global_tenant_registry, get_tenant, TenantTier
from infra.telemetry import get_telemetry
from infra.resilience import (
    global_degraded_mode,
    global_recovery_playbook,
    global_session_reaper,
)

__all__ = [
    "get_db", "db_sessions", "db_governance", "db_exec_store", "db_resource",
    "get_event_bus",
    "global_tenant_registry", "get_tenant", "TenantTier",
    "get_telemetry",
    "global_degraded_mode", "global_recovery_playbook", "global_session_reaper",
]
