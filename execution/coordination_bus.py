"""
execution/coordination_bus.py — Phase: Multi-Agent Coordination Bus
===================================================================
Central message bus that wires together the coordination layer components:
  • AgentRegistry     — active agent tracking
  • DelegationEngine  — task delegation graph
  • MemoryArbiter     — shared memory arbitration
  • ResourceGovernor  — resource budget enforcement
  • WorkspaceLock     — file-level conflict prevention

Also provides the SSE observability broadcast for the coordination UI panel.

Usage (in job_manager or agent.py):
    bus = CoordinationBus.for_session(session_id, emit_fn)
    bus.register_agent("primary")
    bus.charge_tokens(1000)
    bus.write_memory("task_type", "flask_app", confidence=0.9)
    bus.deregister_agent()
    bus.emit_coordination_snapshot()
"""

import threading
import time
import logging
from typing import Callable, Optional

from execution.agent_registry import (
    AgentRole, global_agent_registry, AgentInstance, ROLE_PERMISSIONS
)
from execution.resource_governor import global_resource_governor, ResourceExhaustedError
from execution.memory_arbiter import global_memory_arbiter, MemoryEntry
from execution.workspace_lock import global_lock_registry, file_write_lock, workspace_operation_lock
from execution.delegation_engine import DelegationEngine, DelegationContract, DelegationNode

logger = logging.getLogger("nexora.coordination_bus")


class CoordinationBus:
    """
    Per-session facade that connects an active agent to all coordination
    infrastructure. Handles registration, resource tracking, memory writes,
    and observability broadcasts.

    Design: One CoordinationBus per ExecutionTask. Shared singletons (registry,
    resource governor, memory arbiter) are injected, not owned.
    """

    # Interval between automatic coordination snapshots (seconds)
    SNAPSHOT_INTERVAL = 5.0

    def __init__(
        self,
        session_id: str,
        role: AgentRole = AgentRole.PRIMARY,
        emit_fn: Optional[Callable] = None,
        parent_agent_id: Optional[str] = None,
    ):
        self.session_id = session_id
        self.role = role
        self._emit = emit_fn or (lambda *a, **k: None)
        self._agent: Optional[AgentInstance] = None
        self._parent_agent_id = parent_agent_id

        # Per-session delegation engine (scoped to this bus)
        self.delegation = DelegationEngine(emit_fn=emit_fn, session_id=session_id)

        # Snapshot broadcaster
        self._snapshot_lock = threading.Lock()
        self._last_snapshot_ts: float = 0.0

    # ─────────────────────────────────────────────────────────────────────────
    # Factory
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def for_session(
        cls,
        session_id: str,
        emit_fn: Optional[Callable] = None,
        role: AgentRole = AgentRole.PRIMARY,
    ) -> "CoordinationBus":
        return cls(session_id=session_id, role=role, emit_fn=emit_fn)

    # ─────────────────────────────────────────────────────────────────────────
    # Agent lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def register_agent(self, current_step: str = "") -> AgentInstance:
        """Registers this session's agent in the global registry."""
        try:
            self._agent = global_agent_registry.register(
                role=self.role,
                session_id=self.session_id,
                parent_agent_id=self._parent_agent_id,
                current_step=current_step,
            )
            logger.info(
                f"[CoordinationBus] Registered {self.role.value} agent "
                f"{self._agent.agent_id} for session {self.session_id}"
            )
            self.emit_coordination_snapshot()
            return self._agent
        except ValueError as e:
            logger.warning(f"[CoordinationBus] Registration failed: {e}")
            raise

    def update_step(self, step: str) -> None:
        if self._agent:
            self._agent.current_step = step
            self._agent.heartbeat()

    def set_blocked(self, reason: str) -> None:
        if self._agent:
            self._agent.status = "blocked"
            self._agent.blocked_reason = reason
            self.emit_coordination_snapshot()

    def set_active(self) -> None:
        if self._agent:
            self._agent.status = "active"
            self._agent.blocked_reason = ""
            self._agent.heartbeat()

    def deregister_agent(self, status: str = "completed") -> None:
        if self._agent:
            global_agent_registry.deregister(self._agent.agent_id, final_status=status)
            global_memory_arbiter.remove_channel(self._agent.agent_id)
            global_resource_governor.reset_session(self.session_id)
            logger.info(
                f"[CoordinationBus] Deregistered agent {self._agent.agent_id} ({status})"
            )
            self._agent = None
            self.emit_coordination_snapshot()

    @property
    def agent_id(self) -> Optional[str]:
        return self._agent.agent_id if self._agent else None

    # ─────────────────────────────────────────────────────────────────────────
    # Resource governance passthroughs
    # ─────────────────────────────────────────────────────────────────────────

    def charge_tokens(self, count: int) -> None:
        """Charges token usage. Raises ResourceExhaustedError → triggers HITL escalation."""
        try:
            global_resource_governor.charge_tokens(self.session_id, count)
        except ResourceExhaustedError as e:
            logger.error(f"[CoordinationBus] Token budget exceeded: {e}")
            raise

    def record_retry(self) -> bool:
        """Returns True if retry storm detected. Caller should escalate to HITL."""
        is_storm = global_resource_governor.record_retry(self.session_id)
        if is_storm:
            self._emit("agent.trust_signal", {
                "type": "retry_storm",
                "verified": False,
                "confidence": 0.1,
                "message": "Retry storm detected — too many retries in 60 seconds.",
                "session_id": self.session_id,
            })
        return is_storm

    def browser_slot(self, timeout: float = 30.0):
        """Context manager for a browser session slot."""
        return global_resource_governor.browser_slot(self.session_id, timeout=timeout)

    def terminal_slot(self, timeout: float = 10.0):
        """Context manager for a terminal subprocess slot."""
        return global_resource_governor.terminal_slot(self.session_id, timeout=timeout)

    # ─────────────────────────────────────────────────────────────────────────
    # Memory arbitration passthroughs
    # ─────────────────────────────────────────────────────────────────────────

    def write_memory(self, key: str, value: str, confidence: float,
                     verified: bool = False) -> Optional[MemoryEntry]:
        """Writes to this agent's memory channel; propagates if above threshold."""
        if not self._agent:
            return None
        if not self._agent.permissions.can_write_memory:
            logger.warning(f"[CoordinationBus] Agent {self._agent.role} not permitted to write memory.")
            return None
        return global_memory_arbiter.write(
            agent_id=self._agent.agent_id,
            session_id=self.session_id,
            key=key,
            value=value,
            confidence=confidence,
            role=self._agent.role.value,
            verified=verified,
        )

    def read_shared_memory(self, key: str) -> Optional[MemoryEntry]:
        return global_memory_arbiter.get_shared(key)

    def check_permission(self, permission: str) -> bool:
        if not self._agent:
            return False
        return getattr(self._agent.permissions, permission, False)

    # ─────────────────────────────────────────────────────────────────────────
    # Workspace locking passthroughs
    # ─────────────────────────────────────────────────────────────────────────

    def file_write_lock(self, path: str, timeout: float = 15.0):
        return file_write_lock(path, self.session_id, timeout=timeout)

    def workspace_lock(self, operation: str, timeout: float = 30.0):
        return workspace_operation_lock(self.session_id, operation, timeout=timeout)

    # ─────────────────────────────────────────────────────────────────────────
    # Coordination observability SSE broadcast
    # ─────────────────────────────────────────────────────────────────────────

    def emit_coordination_snapshot(self) -> None:
        """
        Broadcasts the complete coordination state to the frontend.
        Rate-limited to SNAPSHOT_INTERVAL to prevent flooding.
        """
        now = time.time()
        with self._snapshot_lock:
            if now - self._last_snapshot_ts < self.SNAPSHOT_INTERVAL:
                return
            self._last_snapshot_ts = now

        try:
            snapshot = {
                "active_agents": global_agent_registry.snapshot(),
                "delegation_graph": self.delegation.snapshot(),
                "resource_usage": global_resource_governor.snapshot(),
                "active_locks": global_lock_registry.snapshot(),
                "memory_state": global_memory_arbiter.snapshot(),
                "session_id": self.session_id,
            }
            self._emit("agent.coordination_update", snapshot)
        except Exception as e:
            logger.debug(f"[CoordinationBus] Snapshot emit failed: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Safety boundary check
    # ─────────────────────────────────────────────────────────────────────────

    def should_use_single_agent(self) -> tuple[bool, str]:
        """
        Determines whether the complexity of coordination exceeds its benefit.
        Returns (use_single_agent: bool, reason: str).

        Triggers single-agent fallback when:
          1. Only one agent type is in use
          2. Resource governor reports low token headroom (<20% remaining)
          3. Delegation graph is empty (no actual delegation happening)
          4. Multiple retry storms active
        """
        resource = global_resource_governor.snapshot()
        sess = resource.get("sessions", {}).get(self.session_id, {})
        token_usage = sess.get("tokens_used", 0)
        token_limit = resource.get("global_token_limit", 200000)

        if token_usage / max(token_limit, 1) > 0.80:
            return True, "Token budget >80% exhausted — switching to single-agent mode."

        active = global_agent_registry.list_active(self.session_id)
        if len(active) <= 1:
            return True, "Only one agent active — coordination overhead not justified."

        delegation_nodes = self.delegation.snapshot()
        pending_and_running = [
            n for n in delegation_nodes
            if n["status"] in ("pending", "running", "blocked")
        ]
        if not pending_and_running:
            return True, "No active delegations — single-agent mode sufficient."

        return False, "Multi-agent coordination active and healthy."
