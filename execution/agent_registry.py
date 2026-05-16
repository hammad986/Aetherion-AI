"""
execution/agent_registry.py — Phase: Agent Specialization & Coordination
========================================================================
Defines the specialized agent roles, their authority contracts, and
the AgentRegistry that tracks all active agents in the system.

Specialized Agents:
  • PlannerAgent     — task decomposition only; no file writes
  • CodingAgent      — file writes; no browser
  • BrowserAgent     — browser automation; read-only filesystem
  • TestingAgent     — read + run_shell; no file mutations
  • ValidationAgent  — semantic validation; read-only
  • RecoveryAgent    — rollback authority; escalation rights
  • MemoryCuratorAgent — memory consolidation; no tool execution
  • GovernanceAgent  — audit logging; veto authority on dangerous ops

Design Rules:
  • Each role has a declared permission set — no implicit escalation
  • All agents must register/deregister with AgentRegistry
  • Coordination contracts are checked before delegation
  • Max concurrent agents per role is enforced
"""

import threading
import time
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

logger = logging.getLogger("nexora.agent_registry")


# ─────────────────────────────────────────────────────────────────────────────
# Agent Role Definitions
# ─────────────────────────────────────────────────────────────────────────────

class AgentRole(str, Enum):
    PLANNER      = "planner"
    CODING       = "coding"
    BROWSER      = "browser"
    TESTING      = "testing"
    VALIDATION   = "validation"
    RECOVERY     = "recovery"
    MEMORY       = "memory_curator"
    GOVERNANCE   = "governance"
    PRIMARY      = "primary"          # single-agent fallback mode


class TrustLevel(int, Enum):
    """Higher = more autonomous authority."""
    RESTRICTED   = 1   # read-only, no external side effects
    STANDARD     = 2   # file read/write within workspace
    ELEVATED     = 3   # terminal execution, process spawning
    PRIVILEGED   = 4   # rollback, workspace-wide mutations
    GOVERNANCE   = 5   # veto authority, audit override


@dataclass
class AgentPermissions:
    """Declares what an agent role is allowed to do. Violation = PolicyViolationException."""
    can_read_files: bool = True
    can_write_files: bool = False
    can_run_shell: bool = False
    can_spawn_browser: bool = False
    can_rollback: bool = False
    can_delegate: bool = False
    can_escalate_hitl: bool = True
    can_write_memory: bool = False
    can_veto: bool = False            # Governance only
    max_concurrent_instances: int = 2
    retry_budget: int = 3
    trust_level: TrustLevel = TrustLevel.RESTRICTED


# ── Role → Permission contract mapping ────────────────────────────────────────

ROLE_PERMISSIONS: Dict[AgentRole, AgentPermissions] = {
    AgentRole.PLANNER: AgentPermissions(
        can_read_files=True,
        can_write_files=False,
        can_delegate=True,
        can_write_memory=True,
        trust_level=TrustLevel.RESTRICTED,
        max_concurrent_instances=1,   # Only ONE planner per session
        retry_budget=2,
    ),
    AgentRole.CODING: AgentPermissions(
        can_read_files=True,
        can_write_files=True,
        can_run_shell=True,
        can_write_memory=True,
        trust_level=TrustLevel.STANDARD,
        max_concurrent_instances=2,
        retry_budget=4,
    ),
    AgentRole.BROWSER: AgentPermissions(
        can_read_files=True,
        can_write_files=False,
        can_spawn_browser=True,
        trust_level=TrustLevel.ELEVATED,
        max_concurrent_instances=2,
        retry_budget=3,
    ),
    AgentRole.TESTING: AgentPermissions(
        can_read_files=True,
        can_run_shell=True,
        trust_level=TrustLevel.STANDARD,
        max_concurrent_instances=3,
        retry_budget=5,
    ),
    AgentRole.VALIDATION: AgentPermissions(
        can_read_files=True,
        trust_level=TrustLevel.RESTRICTED,
        max_concurrent_instances=4,
        retry_budget=2,
    ),
    AgentRole.RECOVERY: AgentPermissions(
        can_read_files=True,
        can_write_files=True,
        can_run_shell=True,
        can_rollback=True,
        can_escalate_hitl=True,
        trust_level=TrustLevel.PRIVILEGED,
        max_concurrent_instances=1,   # Only ONE recovery agent at a time
        retry_budget=2,
    ),
    AgentRole.MEMORY: AgentPermissions(
        can_read_files=False,
        can_write_memory=True,
        trust_level=TrustLevel.RESTRICTED,
        max_concurrent_instances=1,
        retry_budget=1,
    ),
    AgentRole.GOVERNANCE: AgentPermissions(
        can_veto=True,
        can_escalate_hitl=True,
        can_write_memory=True,
        trust_level=TrustLevel.GOVERNANCE,
        max_concurrent_instances=1,
        retry_budget=1,
    ),
    AgentRole.PRIMARY: AgentPermissions(
        can_read_files=True,
        can_write_files=True,
        can_run_shell=True,
        can_spawn_browser=True,
        can_rollback=True,
        can_delegate=True,
        can_write_memory=True,
        trust_level=TrustLevel.PRIVILEGED,
        max_concurrent_instances=1,
        retry_budget=5,
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# AgentInstance — runtime representation of an active agent
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentInstance:
    agent_id: str
    role: AgentRole
    session_id: str
    parent_agent_id: Optional[str] = None     # delegation chain
    status: str = "active"                    # active | idle | blocked | completed | failed
    current_step: str = ""
    started_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    retry_count: int = 0
    delegation_depth: int = 0                  # prevent infinite delegation chains
    permissions: AgentPermissions = field(default_factory=AgentPermissions)
    blocked_reason: str = ""

    MAX_DELEGATION_DEPTH = 3   # hard limit on how deep delegation can go

    def heartbeat(self):
        self.last_heartbeat = time.time()

    def is_stale(self, ttl: float = 120.0) -> bool:
        return time.time() - self.last_heartbeat > ttl

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "role": self.role.value,
            "session_id": self.session_id,
            "parent_agent_id": self.parent_agent_id,
            "status": self.status,
            "current_step": self.current_step[:80] if self.current_step else "",
            "started_at": self.started_at,
            "delegation_depth": self.delegation_depth,
            "retry_count": self.retry_count,
            "trust_level": self.permissions.trust_level.name,
            "blocked_reason": self.blocked_reason,
        }


# ─────────────────────────────────────────────────────────────────────────────
# AgentRegistry — global tracking of all active agents
# ─────────────────────────────────────────────────────────────────────────────

class AgentRegistry:
    """
    Central registry of ALL active agents across all sessions.
    Enforces:
      - max concurrent instances per role per session
      - delegation depth limits
      - stale agent detection and forced termination
    """

    def __init__(self):
        self._agents: Dict[str, AgentInstance] = {}
        self._lock = threading.RLock()

    def register(
        self,
        role: AgentRole,
        session_id: str,
        parent_agent_id: Optional[str] = None,
        current_step: str = "",
    ) -> AgentInstance:
        """
        Registers a new agent instance.
        Raises ValueError if:
          - max concurrent instances for the role is exceeded
          - delegation depth limit is reached
        """
        perms = ROLE_PERMISSIONS.get(role, AgentPermissions())

        with self._lock:
            # Check delegation depth
            delegation_depth = 0
            if parent_agent_id and parent_agent_id in self._agents:
                parent = self._agents[parent_agent_id]
                delegation_depth = parent.delegation_depth + 1
                if delegation_depth > AgentInstance.MAX_DELEGATION_DEPTH:
                    raise ValueError(
                        f"Delegation depth limit ({AgentInstance.MAX_DELEGATION_DEPTH}) exceeded. "
                        f"Cannot spawn {role.value} from agent {parent_agent_id}."
                    )

            # Check max concurrent instances for this role + session
            active_for_role = [
                a for a in self._agents.values()
                if a.role == role and a.session_id == session_id and a.status == "active"
            ]
            if len(active_for_role) >= perms.max_concurrent_instances:
                raise ValueError(
                    f"Max concurrent {role.value} agents ({perms.max_concurrent_instances}) "
                    f"already running for session {session_id}."
                )

            agent = AgentInstance(
                agent_id=f"agent_{role.value}_{uuid.uuid4().hex[:8]}",
                role=role,
                session_id=session_id,
                parent_agent_id=parent_agent_id,
                delegation_depth=delegation_depth,
                current_step=current_step,
                permissions=perms,
            )
            self._agents[agent.agent_id] = agent
            logger.info(
                f"[AgentRegistry] Registered {role.value} agent {agent.agent_id} "
                f"(session={session_id}, depth={delegation_depth})"
            )
            return agent

    def deregister(self, agent_id: str, final_status: str = "completed") -> None:
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id].status = final_status
                self._agents.pop(agent_id, None)
                logger.info(f"[AgentRegistry] Deregistered agent {agent_id} ({final_status})")

    def get(self, agent_id: str) -> Optional[AgentInstance]:
        with self._lock:
            return self._agents.get(agent_id)

    def list_active(self, session_id: Optional[str] = None) -> List[AgentInstance]:
        with self._lock:
            agents = list(self._agents.values())
            if session_id:
                agents = [a for a in agents if a.session_id == session_id]
            return agents

    def sweep_stale(self, ttl: float = 120.0) -> int:
        """Removes stale agents (no heartbeat within TTL). Returns count removed."""
        stale = []
        with self._lock:
            for agent_id, agent in self._agents.items():
                if agent.is_stale(ttl):
                    stale.append(agent_id)
            for agent_id in stale:
                logger.warning(f"[AgentRegistry] Sweeping stale agent {agent_id}")
                self._agents.pop(agent_id, None)
        return len(stale)

    def snapshot(self) -> list:
        """Serializable snapshot for SSE observability."""
        with self._lock:
            return [a.to_dict() for a in self._agents.values()]

    def has_role_running(self, role: AgentRole, session_id: str) -> bool:
        with self._lock:
            return any(
                a.role == role and a.session_id == session_id and a.status == "active"
                for a in self._agents.values()
            )

    def check_permission(self, agent_id: str, permission: str) -> bool:
        """Checks if a specific agent has a given permission. Raises if not found."""
        agent = self.get(agent_id)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found in registry.")
        return getattr(agent.permissions, permission, False)


# Global singleton
global_agent_registry = AgentRegistry()
