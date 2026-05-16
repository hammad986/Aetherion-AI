"""
infra/tenant.py — Phase: Tenant & Session Isolation
=====================================================
Production-grade multi-tenant isolation for Aetherion AI.

Isolation boundaries enforced:
  1. Namespace isolation — all storage keys scoped to tenant_id
  2. Resource quotas   — per-tenant token, browser, terminal limits
  3. Workspace isolation — filesystem paths never cross tenant boundaries
  4. Memory channel isolation — MemoryArbiter channels are tenant-scoped
  5. Governance log isolation — audit trails separated by tenant
  6. SSE isolation — cross-session contamination prevention

Tenant tiers:
  FREE       — 50k tokens/day, 1 browser, 2 terminals
  PRO        — 500k tokens/day, 2 browsers, 4 terminals
  ENTERPRISE — unlimited quotas (hard ceiling still enforced by ResourceGovernor)

Usage:
    from infra.tenant import TenantContext, get_tenant
    ctx = get_tenant(tenant_id, session_id)
    ctx.check_quota("tokens", 1000)     # raises QuotaExceededError if over
    ctx.charge("tokens", 500)
    path = ctx.workspace_path("app.py") # safely scoped to tenant
"""

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger("nexora.tenant")

_WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "./workspaces")


# ─────────────────────────────────────────────────────────────────────────────
# Tier definitions
# ─────────────────────────────────────────────────────────────────────────────

class TenantTier(str, Enum):
    FREE       = "free"
    PRO        = "pro"
    ENTERPRISE = "enterprise"


TIER_QUOTAS: Dict[TenantTier, Dict[str, int]] = {
    TenantTier.FREE: {
        "tokens_per_day":   50_000,
        "max_browsers":     1,
        "max_terminals":    2,
        "max_sessions":     3,
        "max_file_writes":  100,
        "hitl_escalations": 10,
    },
    TenantTier.PRO: {
        "tokens_per_day":   500_000,
        "max_browsers":     2,
        "max_terminals":    4,
        "max_sessions":     10,
        "max_file_writes":  1000,
        "hitl_escalations": 50,
    },
    TenantTier.ENTERPRISE: {
        "tokens_per_day":   5_000_000,
        "max_browsers":     4,
        "max_terminals":    8,
        "max_sessions":     50,
        "max_file_writes":  10_000,
        "hitl_escalations": 500,
    },
}


class QuotaExceededError(Exception):
    """Raised when a tenant exceeds their quota for a resource."""
    def __init__(self, tenant_id: str, resource: str, used: int, limit: int):
        self.tenant_id = tenant_id
        self.resource  = resource
        self.used      = used
        self.limit     = limit
        super().__init__(
            f"Tenant '{tenant_id}' quota exceeded: {resource} "
            f"({used}/{limit})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# TenantContext — per-tenant state and quota enforcement
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TenantContext:
    tenant_id: str
    tier: TenantTier = TenantTier.FREE
    active_sessions: int = 0
    _usage: Dict[str, int] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _day_start: float = field(default_factory=time.time)

    def __post_init__(self):
        # Rotate daily counters automatically
        self._reset_day_if_needed()

    def _reset_day_if_needed(self) -> None:
        if time.time() - self._day_start > 86400:
            with self._lock:
                self._usage = {}
                self._day_start = time.time()

    # ── Quota enforcement ─────────────────────────────────────────────────────

    def check_quota(self, resource: str, delta: int = 1) -> None:
        """Raises QuotaExceededError if adding `delta` would exceed daily quota."""
        self._reset_day_if_needed()
        quotas = TIER_QUOTAS.get(self.tier, TIER_QUOTAS[TenantTier.FREE])
        limit = quotas.get(resource, 0)
        if limit == 0:
            return   # No limit defined for this resource

        with self._lock:
            current = self._usage.get(resource, 0)
            if current + delta > limit:
                raise QuotaExceededError(self.tenant_id, resource, current + delta, limit)

    def charge(self, resource: str, amount: int = 1) -> None:
        """Increment usage counter for `resource`. Call after check_quota."""
        with self._lock:
            self._usage[resource] = self._usage.get(resource, 0) + amount

    def check_and_charge(self, resource: str, amount: int = 1) -> None:
        """Atomic check-then-charge. Raises QuotaExceededError before charging."""
        self._reset_day_if_needed()
        quotas = TIER_QUOTAS.get(self.tier, TIER_QUOTAS[TenantTier.FREE])
        limit = quotas.get(resource, 0)
        if limit == 0:
            return

        with self._lock:
            current = self._usage.get(resource, 0)
            if current + amount > limit:
                raise QuotaExceededError(self.tenant_id, resource, current + amount, limit)
            self._usage[resource] = current + amount

    # ── Namespace helpers ─────────────────────────────────────────────────────

    def namespace(self, key: str) -> str:
        """Prefix any key with the tenant namespace to prevent cross-contamination."""
        return f"tenant:{self.tenant_id}:{key}"

    def session_key(self, session_id: str, key: str) -> str:
        """Scopes a key to both tenant and session."""
        return f"tenant:{self.tenant_id}:session:{session_id}:{key}"

    def workspace_path(self, relative_path: str) -> str:
        """
        Returns an absolute workspace path for this tenant.
        Path traversal attacks mitigated: relative path is normalized and
        validated to be within the tenant workspace root.
        """
        tenant_root = os.path.realpath(
            os.path.join(_WORKSPACE_ROOT, self._safe_tenant_dir())
        )
        # Normalize and resolve any traversal attempts
        target = os.path.realpath(os.path.join(tenant_root, relative_path))
        if not target.startswith(tenant_root):
            raise PermissionError(
                f"Path traversal blocked: '{relative_path}' escapes tenant workspace "
                f"for tenant '{self.tenant_id}'"
            )
        return target

    def _safe_tenant_dir(self) -> str:
        """Returns a filesystem-safe directory name for the tenant."""
        return hashlib.sha256(self.tenant_id.encode()).hexdigest()[:16]

    def ensure_workspace(self) -> str:
        """Creates the tenant workspace directory if it doesn't exist."""
        root = os.path.join(_WORKSPACE_ROOT, self._safe_tenant_dir())
        os.makedirs(root, exist_ok=True)
        return root

    # ── Governance & isolation metadata ──────────────────────────────────────

    def governance_db_path(self) -> str:
        """Returns the tenant-scoped governance database path."""
        tenant_dir = os.path.join("./data", f"tenant_{self._safe_tenant_dir()}")
        os.makedirs(tenant_dir, exist_ok=True)
        return os.path.join(tenant_dir, "governance.db")

    def session_memory_channel_id(self, session_id: str) -> str:
        """Returns a globally unique memory channel ID scoped to tenant+session."""
        return f"{self.tenant_id}::{session_id}"

    # ── Snapshot for observability ────────────────────────────────────────────

    def snapshot(self) -> dict:
        with self._lock:
            quotas = TIER_QUOTAS.get(self.tier, {})
            return {
                "tenant_id":       self.tenant_id,
                "tier":            self.tier.value,
                "active_sessions": self.active_sessions,
                "usage":           dict(self._usage),
                "quotas":          quotas,
                "workspace_root":  os.path.join(_WORKSPACE_ROOT, self._safe_tenant_dir()),
            }


# ─────────────────────────────────────────────────────────────────────────────
# TenantRegistry — global registry of all tenants
# ─────────────────────────────────────────────────────────────────────────────

class TenantRegistry:
    """
    Tracks all active tenants and enforces cross-tenant isolation.
    Session → tenant mapping prevents session hijacking.
    """

    def __init__(self):
        self._tenants: Dict[str, TenantContext] = {}
        self._session_map: Dict[str, str] = {}   # session_id → tenant_id
        self._lock = threading.RLock()

    def register_tenant(
        self,
        tenant_id: str,
        tier: TenantTier = TenantTier.FREE,
    ) -> TenantContext:
        with self._lock:
            if tenant_id not in self._tenants:
                ctx = TenantContext(tenant_id=tenant_id, tier=tier)
                ctx.ensure_workspace()
                self._tenants[tenant_id] = ctx
                logger.info(f"[Tenant] Registered tenant '{tenant_id}' tier={tier.value}")
            return self._tenants[tenant_id]

    def register_session(self, tenant_id: str, session_id: str) -> TenantContext:
        """Maps a session to a tenant. Raises if session already mapped to different tenant."""
        with self._lock:
            existing = self._session_map.get(session_id)
            if existing and existing != tenant_id:
                raise PermissionError(
                    f"Session '{session_id}' is already associated with tenant "
                    f"'{existing}', cannot reassign to '{tenant_id}'"
                )
            self._session_map[session_id] = tenant_id
            ctx = self._tenants.setdefault(
                tenant_id, TenantContext(tenant_id=tenant_id)
            )
            ctx.active_sessions += 1
            return ctx

    def deregister_session(self, session_id: str) -> None:
        with self._lock:
            tenant_id = self._session_map.pop(session_id, None)
            if tenant_id and tenant_id in self._tenants:
                self._tenants[tenant_id].active_sessions = max(
                    0, self._tenants[tenant_id].active_sessions - 1
                )

    def get_by_session(self, session_id: str) -> Optional[TenantContext]:
        with self._lock:
            tenant_id = self._session_map.get(session_id)
            if not tenant_id:
                # Single-tenant mode: create a default context
                return TenantContext(tenant_id="default", tier=TenantTier.ENTERPRISE)
            return self._tenants.get(tenant_id)

    def get(self, tenant_id: str) -> Optional[TenantContext]:
        with self._lock:
            return self._tenants.get(tenant_id)

    def assert_session_isolation(self, session_a: str, session_b: str) -> None:
        """Raises PermissionError if sessions belong to different tenants."""
        with self._lock:
            ta = self._session_map.get(session_a)
            tb = self._session_map.get(session_b)
            if ta and tb and ta != tb:
                raise PermissionError(
                    f"Cross-tenant session access blocked: "
                    f"session {session_a} (tenant {ta}) ≠ session {session_b} (tenant {tb})"
                )

    def snapshot(self) -> list:
        with self._lock:
            return [ctx.snapshot() for ctx in self._tenants.values()]

    def session_count(self) -> int:
        with self._lock:
            return len(self._session_map)


# ─────────────────────────────────────────────────────────────────────────────
# Global singleton
# ─────────────────────────────────────────────────────────────────────────────

global_tenant_registry = TenantRegistry()


def get_tenant(session_id: str, tenant_id: str = "default",
               tier: TenantTier = TenantTier.ENTERPRISE) -> TenantContext:
    """
    Returns the TenantContext for the given session.
    Creates the tenant if not already registered.
    """
    registry = global_tenant_registry
    registry.register_tenant(tenant_id, tier=tier)
    return registry.register_session(tenant_id, session_id)
