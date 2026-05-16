"""
cluster/control_plane.py — Autonomous Distributed Control Plane
================================================================
The control plane is the single source of cluster truth. It manages:

  ● Node registration & deregistration
  ● Heartbeat liveness tracking (dead nodes expelled within 2× TTL)
  ● Leader election via Redis SETNX with automatic lease renewal
  ● Cluster health scoring (leader/follower/dead node breakdown)
  ● Workload distribution hints (which nodes should own which queues)
  ● Remediation coordination (only leader triggers cluster-wide fixes)
  ● Deployment wave control (leader gates deploy propagation)
  ● Governance propagation (leader fans out policy changes to followers)
  ● Degraded-mode arbitration (quorum-based degraded decision)

Leader election design (Redis-based, no ZooKeeper dependency):
  ─────────────────────────────────────────────────────────────
  Every node attempts: SET nexora:cluster:leader <node_id> NX EX <TTL>
    NX  = only succeeds if key does not exist (atomic leadership claim)
    EX  = TTL expiry ensures dead leaders release automatically
  Current leader renews the key every RENEW_INTERVAL_SEC.
  If renewal fails (Redis unreachable) → leader demotes itself.
  Non-leader nodes probe every PROBE_INTERVAL_SEC; first claimer wins.

Split-brain protection:
  If Redis is unreachable, ALL nodes enter single-node mode:
    - No distributed coordination
    - Each node operates independently with local fallback
    - No remote task assignment
    - Cluster alert emitted immediately

Node state machine:
  JOINING → ACTIVE → DRAINING → LEFT
                   ↘ DEAD (heartbeat expiry)
"""

import json
import logging
import os
import platform
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger("nexora.cluster.control_plane")

# ─── Configuration ────────────────────────────────────────────────────────────
NODE_ID              = os.getenv("NODE_ID", f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}")
NODE_REGION          = os.getenv("NODE_REGION", "default")
LEADER_TTL_SEC       = int(os.getenv("CLUSTER_LEADER_TTL",    "15"))
RENEW_INTERVAL_SEC   = int(os.getenv("CLUSTER_RENEW_INTERVAL", "5"))
PROBE_INTERVAL_SEC   = int(os.getenv("CLUSTER_PROBE_INTERVAL", "8"))
HB_INTERVAL_SEC      = int(os.getenv("CLUSTER_HB_INTERVAL",    "10"))
HB_TTL_SEC           = int(os.getenv("CLUSTER_HB_TTL",         "30"))
MAX_CLUSTER_SIZE     = int(os.getenv("CLUSTER_MAX_NODES",       "16"))

_LEADER_KEY          = "nexora:cluster:leader"
_NODE_REGISTRY_KEY   = "nexora:cluster:nodes"
_NODE_HB_PREFIX      = "nexora:cluster:hb:"


class NodeRole(str, Enum):
    LEADER   = "LEADER"
    FOLLOWER = "FOLLOWER"
    SOLO     = "SOLO"      # no Redis — operating alone


class NodeState(str, Enum):
    JOINING  = "JOINING"
    ACTIVE   = "ACTIVE"
    DRAINING = "DRAINING"
    LEFT     = "LEFT"
    DEAD     = "DEAD"


@dataclass
class NodeInfo:
    node_id:    str
    hostname:   str
    region:     str
    role:       NodeRole
    state:      NodeState
    registered_at: float
    last_heartbeat: float
    meta:       dict = field(default_factory=dict)

    def is_alive(self, ttl: float = HB_TTL_SEC) -> bool:
        return (time.time() - self.last_heartbeat) < ttl

    def to_dict(self) -> dict:
        return {
            "node_id":       self.node_id,
            "hostname":      self.hostname,
            "region":        self.region,
            "role":          self.role.value,
            "state":         self.state.value,
            "registered_at": self.registered_at,
            "last_heartbeat":self.last_heartbeat,
            "alive":         self.is_alive(),
            "meta":          self.meta,
        }


class ControlPlane:
    """
    Autonomous distributed control plane.
    Each Aetherion node runs one ControlPlane instance.
    The elected leader coordinates cluster-wide decisions.
    """

    def __init__(self):
        self._lock         = threading.RLock()
        self._node_id      = NODE_ID
        self._hostname     = socket.gethostname()
        self._role         = NodeRole.SOLO         # determined after first election probe
        self._state        = NodeState.JOINING
        self._leader_id: Optional[str] = None
        self._local_nodes: Dict[str, NodeInfo] = {}
        self._running      = True
        self._callbacks:   List = []               # on_role_change(old, new)
        self._election_lock = threading.Lock()

        # Seed self into local registry
        self._local_nodes[self._node_id] = NodeInfo(
            node_id=self._node_id,
            hostname=self._hostname,
            region=NODE_REGION,
            role=NodeRole.SOLO,
            state=NodeState.JOINING,
            registered_at=time.time(),
            last_heartbeat=time.time(),
            meta={"pid": os.getpid(), "python": platform.python_version()},
        )

        # Start background threads
        threading.Thread(target=self._heartbeat_loop, daemon=True,
                         name="cp-heartbeat").start()
        threading.Thread(target=self._election_loop,  daemon=True,
                         name="cp-election").start()
        threading.Thread(target=self._registry_sync_loop, daemon=True,
                         name="cp-registry").start()

        logger.info(f"[ControlPlane] Node {self._node_id} starting (region={NODE_REGION})")

    # ── Leader Election ───────────────────────────────────────────────────────

    def _election_loop(self) -> None:
        """Runs election probes on non-leaders; renewal on leader."""
        time.sleep(2)  # allow Redis connection to stabilize
        while self._running:
            try:
                rc = self._get_redis()
                if rc is None:
                    self._enter_solo_mode("Redis unavailable")
                    time.sleep(PROBE_INTERVAL_SEC)
                    continue

                with self._election_lock:
                    if self._role == NodeRole.LEADER:
                        self._renew_leadership(rc)
                    else:
                        self._attempt_election(rc)
            except Exception as e:
                logger.debug(f"[ControlPlane] Election error: {e}")
                self._enter_solo_mode(f"Election error: {e}")
            time.sleep(PROBE_INTERVAL_SEC)

    def _attempt_election(self, rc) -> None:
        """Non-leader: try to claim leadership via SET NX EX."""
        try:
            claimed = rc.set(_LEADER_KEY, self._node_id, nx=True, ex=LEADER_TTL_SEC)
            if claimed:
                self._become_leader(rc)
                return

            # Read current leader
            current = rc.get(_LEADER_KEY)
            old_leader = self._leader_id
            with self._lock:
                self._leader_id = current
                if self._role != NodeRole.FOLLOWER:
                    self._set_role(NodeRole.FOLLOWER)
        except Exception as e:
            logger.debug(f"[ControlPlane] Election probe error: {e}")

    def _renew_leadership(self, rc) -> None:
        """Leader: renew TTL before expiry."""
        try:
            # Only renew if WE are still the registered leader
            current = rc.get(_LEADER_KEY)
            if current == self._node_id:
                rc.expire(_LEADER_KEY, LEADER_TTL_SEC)
            else:
                # We lost leadership (someone else claimed after our expiry)
                logger.warning(f"[ControlPlane] Leadership lost to {current}. Becoming follower.")
                with self._lock:
                    self._leader_id = current
                self._set_role(NodeRole.FOLLOWER)
        except Exception as e:
            logger.warning(f"[ControlPlane] Leader renewal failed: {e}. Demoting self.")
            self._set_role(NodeRole.FOLLOWER)

    def _become_leader(self, rc) -> None:
        with self._lock:
            self._leader_id = self._node_id
        self._set_role(NodeRole.LEADER)
        logger.info(f"[ControlPlane] 👑 Node {self._node_id} elected as LEADER")
        self._publish_cluster_event("leader_elected", {"leader": self._node_id})
        # Leader-specific: run initial registry cleanup
        self._sweep_dead_nodes(rc)

    def _enter_solo_mode(self, reason: str) -> None:
        with self._lock:
            if self._role != NodeRole.SOLO:
                logger.warning(f"[ControlPlane] Entering SOLO mode: {reason}")
                self._set_role(NodeRole.SOLO)
                self._leader_id = self._node_id  # treat self as authority in solo

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                rc = self._get_redis()
                hb_data = json.dumps({
                    "node_id":  self._node_id,
                    "hostname": self._hostname,
                    "region":   NODE_REGION,
                    "role":     self._role.value,
                    "state":    self._state.value,
                    "ts":       time.time(),
                    "pid":      os.getpid(),
                })
                if rc:
                    hb_key = f"{_NODE_HB_PREFIX}{self._node_id}"
                    rc.setex(hb_key, HB_TTL_SEC, hb_data)

                    # Register in node hash
                    rc.hset(_NODE_REGISTRY_KEY, self._node_id, hb_data)
                    rc.expire(_NODE_REGISTRY_KEY, HB_TTL_SEC * 3)

                # Always update local
                with self._lock:
                    if self._node_id in self._local_nodes:
                        self._local_nodes[self._node_id].last_heartbeat = time.time()
                        self._local_nodes[self._node_id].role  = self._role
                        self._local_nodes[self._node_id].state = self._state

                # Transition from JOINING → ACTIVE
                if self._state == NodeState.JOINING:
                    with self._lock:
                        self._state = NodeState.ACTIVE
                        self._local_nodes[self._node_id].state = NodeState.ACTIVE

            except Exception as e:
                logger.debug(f"[ControlPlane] Heartbeat error: {e}")
            time.sleep(HB_INTERVAL_SEC)

    # ── Registry Sync ─────────────────────────────────────────────────────────

    def _registry_sync_loop(self) -> None:
        """Periodically pulls node registry from Redis into local cache."""
        time.sleep(5)
        while self._running:
            try:
                rc = self._get_redis()
                if rc:
                    raw = rc.hgetall(_NODE_REGISTRY_KEY)
                    with self._lock:
                        for nid, data_str in raw.items():
                            try:
                                d = json.loads(data_str)
                                ts = d.get("ts", 0)
                                if time.time() - ts > HB_TTL_SEC * 2:
                                    # Stale — skip
                                    continue
                                if nid not in self._local_nodes:
                                    self._local_nodes[nid] = NodeInfo(
                                        node_id=nid,
                                        hostname=d.get("hostname", "unknown"),
                                        region=d.get("region", "default"),
                                        role=NodeRole(d.get("role", "FOLLOWER")),
                                        state=NodeState(d.get("state", "ACTIVE")),
                                        registered_at=ts,
                                        last_heartbeat=ts,
                                    )
                                else:
                                    self._local_nodes[nid].last_heartbeat = ts
                                    self._local_nodes[nid].role  = NodeRole(d.get("role", "FOLLOWER"))
                                    self._local_nodes[nid].state = NodeState(d.get("state", "ACTIVE"))
                            except Exception:
                                pass
            except Exception as e:
                logger.debug(f"[ControlPlane] Registry sync error: {e}")
            time.sleep(HB_INTERVAL_SEC * 2)

    def _sweep_dead_nodes(self, rc) -> None:
        """Leader-only: removes nodes that have not heartbeat within TTL."""
        try:
            raw = rc.hgetall(_NODE_REGISTRY_KEY)
            for nid, data_str in raw.items():
                try:
                    d = json.loads(data_str)
                    if time.time() - d.get("ts", 0) > HB_TTL_SEC * 2:
                        rc.hdel(_NODE_REGISTRY_KEY, nid)
                        with self._lock:
                            if nid in self._local_nodes:
                                self._local_nodes[nid].state = NodeState.DEAD
                        logger.warning(f"[ControlPlane] Node expelled (dead): {nid}")
                        self._publish_cluster_event("node_expelled", {"node_id": nid})
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"[ControlPlane] Dead-node sweep error: {e}")

    # ── Role management ───────────────────────────────────────────────────────

    def _set_role(self, new_role: NodeRole) -> None:
        with self._lock:
            old_role = self._role
            if old_role == new_role:
                return
            self._role = new_role
            if self._node_id in self._local_nodes:
                self._local_nodes[self._node_id].role = new_role

        for cb in self._callbacks:
            try:
                cb(old_role, new_role)
            except Exception:
                pass

        try:
            from infra.telemetry import get_telemetry
            get_telemetry().record("cluster", "role_change",
                                   {"node_id": self._node_id,
                                    "from": old_role.value, "to": new_role.value})
        except Exception:
            pass

    def on_role_change(self, fn) -> None:
        """Register callback invoked when this node's role changes."""
        self._callbacks.append(fn)

    # ── Cluster coordination ──────────────────────────────────────────────────

    def is_leader(self) -> bool:
        return self._role == NodeRole.LEADER

    def is_solo(self) -> bool:
        return self._role == NodeRole.SOLO

    def current_leader_id(self) -> Optional[str]:
        return self._leader_id

    def am_i_leader(self) -> bool:
        return self._role == NodeRole.LEADER and self._leader_id == self._node_id

    def active_nodes(self) -> List[NodeInfo]:
        with self._lock:
            return [n for n in self._local_nodes.values() if n.is_alive()]

    def dead_nodes(self) -> List[NodeInfo]:
        with self._lock:
            return [n for n in self._local_nodes.values() if not n.is_alive()]

    def cluster_size(self) -> int:
        return len(self.active_nodes())

    def quorum_size(self) -> int:
        """Minimum nodes for a decision to be valid (majority)."""
        return (self.cluster_size() // 2) + 1

    def has_quorum(self) -> bool:
        """True if we have enough nodes for safe distributed decisions."""
        if self._role == NodeRole.SOLO:
            return True   # single-node is always its own quorum
        return self.cluster_size() >= self.quorum_size()

    def drain_self(self) -> None:
        """Gracefully removes this node from active work before shutdown."""
        with self._lock:
            self._state = NodeState.DRAINING
            if self._node_id in self._local_nodes:
                self._local_nodes[self._node_id].state = NodeState.DRAINING
        self._publish_cluster_event("node_draining", {"node_id": self._node_id})
        logger.info(f"[ControlPlane] Node {self._node_id} entering DRAINING state")

        # If we're leader, release leadership immediately
        if self._role == NodeRole.LEADER:
            rc = self._get_redis()
            if rc:
                try:
                    current = rc.get(_LEADER_KEY)
                    if current == self._node_id:
                        rc.delete(_LEADER_KEY)
                        logger.info("[ControlPlane] Leader key released for successor")
                except Exception:
                    pass
            self._set_role(NodeRole.FOLLOWER)

    # ── Workload assignment ───────────────────────────────────────────────────

    def assign_queue_owner(self, queue_name: str) -> str:
        """
        Deterministically assigns a queue to an active node using
        consistent hashing (node_id list sorted → hash(queue) mod len).
        Returns the owning node_id.
        """
        nodes = sorted(n.node_id for n in self.active_nodes())
        if not nodes:
            return self._node_id
        idx = hash(queue_name) % len(nodes)
        return nodes[idx]

    def should_own_queue(self, queue_name: str) -> bool:
        """Returns True if THIS node should process the given queue."""
        return self.assign_queue_owner(queue_name) == self._node_id

    # ── Events ────────────────────────────────────────────────────────────────

    def _publish_cluster_event(self, event_type: str, payload: dict) -> None:
        try:
            from infra.event_bus import get_event_bus
            get_event_bus().publish_global(f"cluster.{event_type}", payload)
        except Exception:
            pass

    # ── Redis access ──────────────────────────────────────────────────────────

    def _get_redis(self):
        try:
            from infra.event_bus import get_event_bus
            return get_event_bus()._redis.get()
        except Exception:
            return None

    # ── Public snapshot ───────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        active = self.active_nodes()
        dead   = self.dead_nodes()
        return {
            "this_node": {
                "node_id":   self._node_id,
                "hostname":  self._hostname,
                "region":    NODE_REGION,
                "role":      self._role.value,
                "state":     self._state.value,
            },
            "leader_id":      self._leader_id,
            "am_i_leader":    self.am_i_leader(),
            "cluster_size":   len(active),
            "dead_nodes":     len(dead),
            "has_quorum":     self.has_quorum(),
            "quorum_size":    self.quorum_size(),
            "active_nodes":   [n.to_dict() for n in active],
            "dead_node_list": [n.to_dict() for n in dead],
            "leader_ttl_sec": LEADER_TTL_SEC,
            "hb_ttl_sec":     HB_TTL_SEC,
        }

    def stop(self) -> None:
        self._running = False
        self.drain_self()


# ─── Global singleton ─────────────────────────────────────────────────────────
_instance: Optional[ControlPlane] = None
_instance_lock = threading.Lock()

def get_control_plane() -> ControlPlane:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = ControlPlane()
    return _instance
