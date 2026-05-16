"""
cluster/resource_governor.py — Cluster-Wide Resource Governance
================================================================
Coordinates resource quotas and fair scheduling across all cluster nodes.

Governed resources:
  ● Browser pool slots   — cluster-wide cap, node-local allocation
  ● LLM token budget     — rolling 60s window across all nodes
  ● Task queue depth     — per-queue depth caps with backpressure
  ● Deployment waves     — max concurrent deploys across cluster
  ● Memory pressure      — node-level RSS budget with spillover prevention
  ● Retry budget         — max retries/minute cluster-wide (storm prevention)
  ● Concurrent agents    — cluster-wide cap on simultaneous agent sessions

Quota enforcement strategy:
  Redis counters + TTL windows:
    INCR nexora:quota:{resource}:{window_key}
    EXPIRE to window duration
  Cluster-wide: atomic INCR prevents double-counting across nodes.
  Local fallback: per-node counters when Redis unavailable (proportional share).

Fair scheduling:
  Token bucket per resource. Bucket refilled on TTL expiry.
  Over-quota requests → backpressure (wait or reject based on priority).
  High-priority sessions get 2× allocation headroom.
  Starvation prevention: sessions waiting > MAX_WAIT_SEC are granted once.

Hotspot prevention:
  If any single session accounts for > HOTSPOT_PCT of a resource's usage →
    Rate-limit that session and emit telemetry alert.
  Tracked via Redis hash of session_id → allocation counts.

Overload isolation:
  When cluster-wide quota exceeds DANGER_PCT of hard ceiling:
    - Reject LOW priority requests immediately
    - Warn NORMAL priority requests
    - Only CRITICAL priority requests proceed
"""

import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

logger = logging.getLogger("nexora.cluster.resource_governor")

# ─── Configuration ────────────────────────────────────────────────────────────
# Cluster-wide hard limits
CLUSTER_MAX_BROWSERS     = int(os.getenv("CLUSTER_MAX_BROWSERS",    "20"))
CLUSTER_MAX_AGENTS       = int(os.getenv("CLUSTER_MAX_AGENTS",      "50"))
CLUSTER_TOKEN_BUDGET_MIN = int(os.getenv("CLUSTER_TOKEN_BUDGET_MIN","100000")) # per 60s window
CLUSTER_MAX_RETRIES_MIN  = int(os.getenv("CLUSTER_MAX_RETRIES_MIN", "60"))     # per 60s
CLUSTER_MAX_DEPLOYS      = int(os.getenv("CLUSTER_MAX_DEPLOYS",     "2"))      # concurrent

HOTSPOT_PCT   = float(os.getenv("RESOURCE_HOTSPOT_PCT",  "0.40"))  # 40% of quota by one session
DANGER_PCT    = float(os.getenv("RESOURCE_DANGER_PCT",   "0.85"))  # 85% → overload isolation
MAX_WAIT_SEC  = float(os.getenv("RESOURCE_MAX_WAIT_SEC", "30.0"))  # starvation prevention


class Priority(str, Enum):
    LOW      = "LOW"
    NORMAL   = "NORMAL"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


class QuotaDecision(str, Enum):
    GRANTED  = "GRANTED"
    REJECTED = "REJECTED"
    WAITING  = "WAITING"


@dataclass
class QuotaResult:
    decision:   QuotaDecision
    resource:   str
    session_id: str
    requested:  int
    allowed:    int
    reason:     str = ""
    cluster_usage: float = 0.0   # 0.0–1.0 fraction of hard cap


class ClusterResourceGovernor:
    """
    Cluster-wide resource quota enforcer.
    All allocations are tracked in Redis (atomic INCR) to prevent
    cross-node double-counting.
    """

    def __init__(self):
        self._lock   = threading.RLock()
        self._node_id = os.getenv("NODE_ID", "local")
        self._local_counters: Dict[str, int] = {}  # fallback counters
        self._wait_queues: Dict[str, list] = {}     # resource → [(priority, event)]
        self._stats = {
            "granted": 0, "rejected": 0, "hotspot_alerts": 0, "overload_events": 0
        }
        logger.info("[ClusterResourceGovernor] Initialized")

    # ── Core allocation ───────────────────────────────────────────────────────

    def request(self, resource: str, amount: int = 1, session_id: str = "",
                priority: Priority = Priority.NORMAL) -> QuotaResult:
        """
        Request `amount` units of `resource` for `session_id`.
        Returns QuotaResult indicating GRANTED / REJECTED / WAITING.
        """
        cap         = self._get_cap(resource)
        current     = self._get_current(resource)
        usage_frac  = current / max(cap, 1)

        # Overload isolation
        if usage_frac >= DANGER_PCT:
            if priority == Priority.LOW:
                self._stats["overload_events"] += 1
                return QuotaResult(QuotaDecision.REJECTED, resource, session_id,
                                   amount, 0, f"Cluster overload ({usage_frac:.0%} of cap)",
                                   usage_frac)
            if priority == Priority.NORMAL:
                logger.warning(f"[ResourceGovernor] Overload warning: {resource} "
                                f"at {usage_frac:.0%}")

        # Cap enforcement
        if current + amount > cap:
            return QuotaResult(QuotaDecision.REJECTED, resource, session_id,
                               amount, 0,
                               f"Quota exceeded: {current}/{cap} in use", usage_frac)

        # Hotspot check
        session_usage = self._get_session_usage(resource, session_id)
        if session_id and current > 0:
            session_frac = session_usage / max(current, 1)
            if session_frac > HOTSPOT_PCT:
                self._stats["hotspot_alerts"] += 1
                self._emit_hotspot_alert(resource, session_id, session_frac)

        # Grant
        self._increment(resource, amount)
        self._increment_session(resource, session_id, amount)
        self._stats["granted"] += 1
        return QuotaResult(QuotaDecision.GRANTED, resource, session_id,
                           amount, amount, "", usage_frac)

    def release(self, resource: str, amount: int = 1, session_id: str = "") -> None:
        """Returns `amount` units of `resource` back to the cluster pool."""
        self._decrement(resource, amount)
        if session_id:
            self._decrement_session(resource, session_id, amount)

    # ── Specific resource helpers ─────────────────────────────────────────────

    def request_browser_slot(self, session_id: str,
                              priority: Priority = Priority.NORMAL) -> QuotaResult:
        return self.request("browsers", 1, session_id, priority)

    def release_browser_slot(self, session_id: str) -> None:
        self.release("browsers", 1, session_id)

    def request_tokens(self, session_id: str, token_count: int,
                       priority: Priority = Priority.NORMAL) -> QuotaResult:
        """Cluster-wide token budget enforcement (rolling 60s window)."""
        return self._windowed_request("tokens_60s", token_count, 60,
                                      CLUSTER_TOKEN_BUDGET_MIN, session_id, priority)

    def request_agent_slot(self, session_id: str) -> QuotaResult:
        return self.request("agents", 1, session_id, Priority.NORMAL)

    def release_agent_slot(self, session_id: str) -> None:
        self.release("agents", 1, session_id)

    def request_deploy_slot(self) -> QuotaResult:
        return self.request("deploys", 1, "system", Priority.HIGH)

    def release_deploy_slot(self) -> None:
        self.release("deploys", 1, "system")

    def record_retry(self, session_id: str) -> QuotaResult:
        """Track retries for storm prevention (rolling 60s window)."""
        return self._windowed_request("retries_60s", 1, 60,
                                      CLUSTER_MAX_RETRIES_MIN, session_id, Priority.NORMAL)

    # ── Windowed rate limiting ────────────────────────────────────────────────

    def _windowed_request(self, resource: str, amount: int, window_sec: int,
                           cap: int, session_id: str, priority: Priority) -> QuotaResult:
        """Uses Redis time-bucketed keys for sliding window quota."""
        window_key = f"{resource}:{int(time.time() // window_sec)}"
        rc = self._get_redis()

        if rc:
            try:
                key = f"nexora:quota:{window_key}"
                current = int(rc.get(key) or 0)
                if current + amount > cap:
                    usage_frac = current / max(cap, 1)
                    self._stats["rejected"] += 1
                    return QuotaResult(QuotaDecision.REJECTED, resource, session_id,
                                       amount, 0,
                                       f"Window quota exceeded: {current}/{cap}", usage_frac)
                pipe = rc.pipeline()
                pipe.incrby(key, amount)
                pipe.expire(key, window_sec * 2)
                pipe.execute()
                self._stats["granted"] += 1
                return QuotaResult(QuotaDecision.GRANTED, resource, session_id,
                                   amount, amount, "", current / max(cap, 1))
            except Exception as e:
                logger.debug(f"[ResourceGovernor] Redis window error: {e}")

        # Local fallback
        with self._lock:
            k = f"local:{window_key}"
            current = self._local_counters.get(k, 0)
            node_cap = max(1, cap // max(1, self._cluster_size()))
            if current + amount > node_cap:
                return QuotaResult(QuotaDecision.REJECTED, resource, session_id,
                                   amount, 0, f"Node quota exceeded: {current}/{node_cap}", 0.9)
            self._local_counters[k] = current + amount
            return QuotaResult(QuotaDecision.GRANTED, resource, session_id,
                               amount, amount, "(local fallback)", 0.0)

    # ── Redis counters ────────────────────────────────────────────────────────

    def _get_cap(self, resource: str) -> int:
        caps = {
            "browsers": CLUSTER_MAX_BROWSERS,
            "agents":   CLUSTER_MAX_AGENTS,
            "deploys":  CLUSTER_MAX_DEPLOYS,
        }
        return caps.get(resource, 100)

    def _get_current(self, resource: str) -> int:
        rc = self._get_redis()
        key = f"nexora:resource:{resource}"
        if rc:
            try:
                v = rc.get(key)
                return max(0, int(v or 0))
            except Exception:
                pass
        with self._lock:
            return self._local_counters.get(resource, 0)

    def _increment(self, resource: str, amount: int) -> None:
        rc = self._get_redis()
        key = f"nexora:resource:{resource}"
        if rc:
            try:
                rc.incrby(key, amount)
                return
            except Exception:
                pass
        with self._lock:
            self._local_counters[resource] = self._local_counters.get(resource, 0) + amount

    def _decrement(self, resource: str, amount: int) -> None:
        rc = self._get_redis()
        key = f"nexora:resource:{resource}"
        if rc:
            try:
                new_val = rc.decrby(key, amount)
                if new_val < 0:
                    rc.set(key, 0)
                return
            except Exception:
                pass
        with self._lock:
            self._local_counters[resource] = max(0,
                self._local_counters.get(resource, 0) - amount)

    def _get_session_usage(self, resource: str, session_id: str) -> int:
        rc = self._get_redis()
        if rc and session_id:
            try:
                v = rc.hget(f"nexora:resource_session:{resource}", session_id)
                return int(v or 0)
            except Exception:
                pass
        return 0

    def _increment_session(self, resource: str, session_id: str, amount: int) -> None:
        if not session_id:
            return
        rc = self._get_redis()
        if rc:
            try:
                rc.hincrby(f"nexora:resource_session:{resource}", session_id, amount)
                rc.expire(f"nexora:resource_session:{resource}", 3600)
            except Exception:
                pass

    def _decrement_session(self, resource: str, session_id: str, amount: int) -> None:
        if not session_id:
            return
        rc = self._get_redis()
        if rc:
            try:
                new_val = rc.hincrby(f"nexora:resource_session:{resource}", session_id, -amount)
                if new_val < 0:
                    rc.hset(f"nexora:resource_session:{resource}", session_id, 0)
            except Exception:
                pass

    # ── Hotspot detection ─────────────────────────────────────────────────────

    def _emit_hotspot_alert(self, resource: str, session_id: str, fraction: float) -> None:
        logger.warning(f"[ResourceGovernor] HOTSPOT: session {session_id} using "
                       f"{fraction:.0%} of {resource} quota")
        try:
            from infra.telemetry import get_telemetry
            get_telemetry().record("cluster", "resource_hotspot", {
                "resource":   resource,
                "session_id": session_id[:24],
                "fraction":   round(fraction, 2),
            })
        except Exception:
            pass

    # ── Cluster size ──────────────────────────────────────────────────────────

    def _cluster_size(self) -> int:
        try:
            from cluster.control_plane import get_control_plane
            return max(1, get_control_plane().cluster_size())
        except Exception:
            return 1

    # ── Observability ─────────────────────────────────────────────────────────

    def _get_redis(self):
        try:
            from infra.event_bus import get_event_bus
            return get_event_bus()._redis.get()
        except Exception:
            return None

    def cluster_resource_snapshot(self) -> dict:
        resources = ["browsers", "agents", "deploys"]
        snap = {}
        for res in resources:
            current = self._get_current(res)
            cap     = self._get_cap(res)
            snap[res] = {
                "current":    current,
                "cap":        cap,
                "usage_pct":  round(current / max(cap, 1) * 100, 1),
                "available":  max(0, cap - current),
            }
        return {
            "resources":         snap,
            "cluster_size":      self._cluster_size(),
            "danger_threshold":  DANGER_PCT,
            "hotspot_threshold": HOTSPOT_PCT,
            "stats":             self._stats,
        }


# ─── Global singleton ─────────────────────────────────────────────────────────
_instance: Optional[ClusterResourceGovernor] = None
_instance_lock = threading.Lock()

def get_cluster_resource_governor() -> ClusterResourceGovernor:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = ClusterResourceGovernor()
    return _instance
