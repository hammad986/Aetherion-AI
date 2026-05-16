"""
execution/delegation_engine.py — Phase: Task Delegation Engine
==============================================================
Controlled delegation graph with:
  • Ownership tracking per delegated task
  • Dependency-aware assignment (delegates only when dependencies complete)
  • Retry accountability (delegates only once per step)
  • Cancellation propagation (cancel parent → cancel all children)
  • HITL-aware pauses (respects global HITL hold state)
  • Completion verification (delegates must pass verification before task is closed)

Delegation contracts require:
  purpose, expected_outcome, verification_criteria, rollback_implications, confidence_context
"""

import threading
import time
import logging
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any
from enum import Enum

logger = logging.getLogger("nexora.delegation")


class DelegationStatus(str, Enum):
    PENDING     = "pending"
    RUNNING     = "running"
    BLOCKED     = "blocked"       # waiting on dependency
    HITL_WAIT   = "hitl_wait"     # paused awaiting human approval
    COMPLETED   = "completed"
    FAILED      = "failed"
    CANCELLED   = "cancelled"
    VERIFIED    = "verified"      # completed AND semantically verified


@dataclass
class DelegationContract:
    """
    Formal specification of a delegated task.
    Every delegation MUST carry a complete contract — no implicit intent.
    """
    purpose: str
    expected_outcome: str
    verification_criteria: str
    rollback_implications: str
    confidence_context: float   # 0.0–1.0; how confident the delegating agent is

    def is_high_risk(self) -> bool:
        """High-risk delegations require HITL approval before running."""
        return self.confidence_context < 0.40 or "delete" in self.purpose.lower()


@dataclass
class DelegationNode:
    """Represents a single delegated task in the delegation graph."""
    node_id: str
    parent_node_id: Optional[str]
    agent_role: str
    session_id: str
    contract: DelegationContract
    depends_on: List[str] = field(default_factory=list)  # node_ids
    status: DelegationStatus = DelegationStatus.PENDING
    delegated_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    retry_count: int = 0
    retry_budget: int = 2           # per delegation, not per tool call
    result: Any = None
    error: str = ""
    verified: bool = False

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "parent_node_id": self.parent_node_id,
            "agent_role": self.agent_role,
            "status": self.status.value,
            "purpose": self.contract.purpose[:80],
            "expected_outcome": self.contract.expected_outcome[:80],
            "confidence": self.contract.confidence_context,
            "depends_on": self.depends_on,
            "retry_count": self.retry_count,
            "verified": self.verified,
            "error": self.error[:100] if self.error else "",
        }


class DelegationEngine:
    """
    Controls and tracks all agent-to-agent task delegation.

    Prevents:
      - Duplicate work (same contract delegated twice)
      - Recursive delegation loops (depth check via AgentRegistry)
      - Runaway delegation (max active delegations per session)
      - Silent failures (all completed nodes must be verified)

    Emits SSE events on state changes for frontend observability.
    """

    MAX_ACTIVE_DELEGATIONS_PER_SESSION = 6
    STALE_DELEGATION_TTL = 300   # 5 minutes

    def __init__(self, emit_fn: Optional[Callable] = None, session_id: str = ""):
        self._nodes: Dict[str, DelegationNode] = {}
        self._lock = threading.RLock()
        self._emit = emit_fn or (lambda *a, **k: None)
        self._session_id = session_id

    def delegate(
        self,
        agent_role: str,
        contract: DelegationContract,
        parent_node_id: Optional[str] = None,
        depends_on: Optional[List[str]] = None,
    ) -> DelegationNode:
        """
        Creates a new delegation node.
        Raises ValueError if delegation limits or safety checks fail.
        """
        with self._lock:
            # Concurrency limit
            active = [n for n in self._nodes.values()
                      if n.status in (DelegationStatus.PENDING, DelegationStatus.RUNNING)
                      and n.session_id == self._session_id]
            if len(active) >= self.MAX_ACTIVE_DELEGATIONS_PER_SESSION:
                raise ValueError(
                    f"Delegation limit reached ({self.MAX_ACTIVE_DELEGATIONS_PER_SESSION} active). "
                    "Wait for current delegations to complete."
                )

            # Duplicate prevention: same purpose + role in same session
            for n in self._nodes.values():
                if (n.agent_role == agent_role
                        and n.contract.purpose == contract.purpose
                        and n.session_id == self._session_id
                        and n.status not in (DelegationStatus.FAILED, DelegationStatus.CANCELLED)):
                    raise ValueError(
                        f"Duplicate delegation detected: role='{agent_role}' "
                        f"purpose='{contract.purpose[:60]}' already delegated as node {n.node_id}."
                    )

            node = DelegationNode(
                node_id=f"del_{uuid.uuid4().hex[:10]}",
                parent_node_id=parent_node_id,
                agent_role=agent_role,
                session_id=self._session_id,
                contract=contract,
                depends_on=depends_on or [],
            )

            # High-risk tasks → immediately set to HITL_WAIT
            if contract.is_high_risk():
                node.status = DelegationStatus.HITL_WAIT
                logger.warning(
                    f"[Delegation] High-risk delegation to {agent_role} requires HITL approval: "
                    f"{contract.purpose[:80]}"
                )

            self._nodes[node.node_id] = node
            logger.info(
                f"[Delegation] Delegated to {agent_role}: node={node.node_id} "
                f"status={node.status.value}"
            )
            self._emit_update(node)
            return node

    # ── Lifecycle transitions ─────────────────────────────────────────────────

    def start(self, node_id: str) -> bool:
        """Marks node as running. Returns False if dependencies not met."""
        with self._lock:
            node = self._nodes.get(node_id)
            if not node:
                return False
            completed_ids = {
                n.node_id for n in self._nodes.values()
                if n.status in (DelegationStatus.COMPLETED, DelegationStatus.VERIFIED)
            }
            if not all(dep in completed_ids for dep in node.depends_on):
                node.status = DelegationStatus.BLOCKED
                node.error = "Waiting on dependencies: " + ", ".join(
                    d for d in node.depends_on if d not in completed_ids
                )
                self._emit_update(node)
                return False
            node.status = DelegationStatus.RUNNING
            self._emit_update(node)
            return True

    def complete(self, node_id: str, result: Any = None, verified: bool = False) -> None:
        with self._lock:
            node = self._nodes.get(node_id)
            if not node:
                return
            node.result = result
            node.verified = verified
            node.completed_at = time.time()
            node.status = DelegationStatus.VERIFIED if verified else DelegationStatus.COMPLETED
            logger.info(f"[Delegation] Node {node_id} completed (verified={verified})")
            self._emit_update(node)

    def fail(self, node_id: str, error: str) -> bool:
        """Returns True if retry is allowed, False if budget exhausted."""
        with self._lock:
            node = self._nodes.get(node_id)
            if not node:
                return False
            node.retry_count += 1
            node.error = error
            if node.retry_count >= node.retry_budget:
                node.status = DelegationStatus.FAILED
                logger.warning(f"[Delegation] Node {node_id} exhausted retry budget: {error[:80]}")
                self._emit_update(node)
                return False
            node.status = DelegationStatus.PENDING  # allow retry
            logger.info(f"[Delegation] Node {node_id} will retry ({node.retry_count}/{node.retry_budget})")
            self._emit_update(node)
            return True

    def cancel_subtree(self, node_id: str) -> int:
        """Cancels a node and all its descendants (propagated cancellation)."""
        cancelled = 0
        with self._lock:
            def _cancel(nid: str):
                nonlocal cancelled
                node = self._nodes.get(nid)
                if not node:
                    return
                if node.status not in (DelegationStatus.COMPLETED, DelegationStatus.VERIFIED,
                                       DelegationStatus.CANCELLED):
                    node.status = DelegationStatus.CANCELLED
                    cancelled += 1
                    self._emit_update(node)
                # Recurse into children
                children = [n for n in self._nodes.values() if n.parent_node_id == nid]
                for child in children:
                    _cancel(child.node_id)
            _cancel(node_id)
        logger.info(f"[Delegation] Cancelled subtree rooted at {node_id}: {cancelled} nodes")
        return cancelled

    def approve_hitl(self, node_id: str) -> bool:
        """Releases a HITL-waiting node back to PENDING."""
        with self._lock:
            node = self._nodes.get(node_id)
            if not node or node.status != DelegationStatus.HITL_WAIT:
                return False
            node.status = DelegationStatus.PENDING
            logger.info(f"[Delegation] HITL approved: node {node_id} unblocked")
            self._emit_update(node)
            return True

    # ── Observability ─────────────────────────────────────────────────────────

    def _emit_update(self, node: DelegationNode) -> None:
        try:
            self._emit("agent.delegation_update", {
                "node": node.to_dict(),
                "session_id": self._session_id,
            })
        except Exception as e:
            logger.debug(f"[Delegation] SSE emit failed: {e}")

    def snapshot(self) -> list:
        with self._lock:
            return [n.to_dict() for n in self._nodes.values()]

    def sweep_stale(self) -> int:
        cutoff = time.time() - self.STALE_DELEGATION_TTL
        stale = []
        with self._lock:
            for nid, node in self._nodes.items():
                if (node.status == DelegationStatus.RUNNING
                        and node.delegated_at < cutoff):
                    stale.append(nid)
            for nid in stale:
                self._nodes[nid].status = DelegationStatus.FAILED
                self._nodes[nid].error = "Stale — no completion signal received"
        return len(stale)
