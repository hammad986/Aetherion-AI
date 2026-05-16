"""
infra/telemetry.py — Phase: Production Observability & Telemetry
================================================================
Enterprise-grade instrumentation layer for Aetherion AI.

Tracks:
  • Coordination latency        (time from delegation to completion)
  • Semantic validation timing  (per-check duration)
  • Retry storm events          (frequency, session, role)
  • Worker crash events
  • SSE throughput              (events/sec per session)
  • Browser pool utilization    (utilization%, wait times)
  • Token burn rates            (tokens/sec, cost/min)
  • Governance escalations      (count, type, resolution time)
  • HITL frequency              (triggers/hr, resolution rate)

Exports:
  • Prometheus-compatible /metrics endpoint (via text format)
  • Internal snapshot() for operational dashboards
  • Structured log emission per event category
  • In-memory ring buffers for recent event replay

Design: zero-dependency core (no prometheus_client required).
If prometheus_client is available, metrics are also pushed there.
"""

import collections
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional

logger = logging.getLogger("nexora.telemetry")

_RING_CAPACITY = int(os.getenv("TELEMETRY_RING_SIZE", "500"))   # events kept in ring buffer

# Try to load prometheus_client (optional)
try:
    import prometheus_client as _prom
    _PROM_OK = True
    _prom.REGISTRY  # ensure it's usable
except Exception:
    _prom = None
    _PROM_OK = False


# ─────────────────────────────────────────────────────────────────────────────
# Counter / Gauge / Histogram — lightweight in-process metrics
# ─────────────────────────────────────────────────────────────────────────────

class Counter:
    """Thread-safe incrementing counter."""
    def __init__(self, name: str, desc: str = ""):
        self.name = name
        self.desc = desc
        self._val = 0
        self._lock = threading.Lock()
        if _PROM_OK:
            try:
                self._prom_ctr = _prom.Counter(name.replace(".", "_"), desc)
            except Exception:
                self._prom_ctr = None
        else:
            self._prom_ctr = None

    def inc(self, amount: int = 1) -> None:
        with self._lock:
            self._val += amount
        if self._prom_ctr:
            try:
                self._prom_ctr.inc(amount)
            except Exception:
                pass

    def value(self) -> int:
        with self._lock:
            return self._val


class Gauge:
    """Thread-safe gauge (can go up or down)."""
    def __init__(self, name: str, desc: str = ""):
        self.name = name
        self.desc = desc
        self._val = 0.0
        self._lock = threading.Lock()
        if _PROM_OK:
            try:
                self._prom_g = _prom.Gauge(name.replace(".", "_"), desc)
            except Exception:
                self._prom_g = None
        else:
            self._prom_g = None

    def set(self, v: float) -> None:
        with self._lock:
            self._val = v
        if self._prom_g:
            try:
                self._prom_g.set(v)
            except Exception:
                pass

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self._val += amount
        if self._prom_g:
            try:
                self._prom_g.inc(amount)
            except Exception:
                pass

    def dec(self, amount: float = 1.0) -> None:
        with self._lock:
            self._val -= amount
        if self._prom_g:
            try:
                self._prom_g.dec(amount)
            except Exception:
                pass

    def value(self) -> float:
        with self._lock:
            return self._val


class Histogram:
    """Thread-safe histogram tracking latency distributions."""
    _BUCKETS = [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]

    def __init__(self, name: str, desc: str = ""):
        self.name = name
        self.desc = desc
        self._observations: Deque[float] = collections.deque(maxlen=_RING_CAPACITY)
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self._observations.append(value)

    def stats(self) -> dict:
        with self._lock:
            data = sorted(self._observations)
        if not data:
            return {"count": 0, "min": 0, "max": 0, "p50": 0, "p95": 0, "p99": 0, "mean": 0}
        n = len(data)
        return {
            "count": n,
            "min":   round(data[0], 4),
            "max":   round(data[-1], 4),
            "p50":   round(data[int(n * 0.50)], 4),
            "p95":   round(data[int(n * 0.95)], 4),
            "p99":   round(data[min(int(n * 0.99), n - 1)], 4),
            "mean":  round(sum(data) / n, 4),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Event ring buffer
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TelemetryEvent:
    category: str
    event_type: str
    session_id: str
    tenant_id: str
    payload: dict
    ts: float = field(default_factory=time.time)


# ─────────────────────────────────────────────────────────────────────────────
# TelemetryCollector — singleton
# ─────────────────────────────────────────────────────────────────────────────

class TelemetryCollector:
    """
    Central telemetry sink for the Aetherion runtime.
    All metrics are collected here; exposed via snapshot() and /metrics.
    """

    def __init__(self):
        # ── Counters ──────────────────────────────────────────────────────────
        self.c_tokens_total       = Counter("nexora.tokens.total",         "Total tokens consumed")
        self.c_sse_events         = Counter("nexora.sse.events",           "SSE events broadcast")
        self.c_retries            = Counter("nexora.retries.total",        "Total task retries")
        self.c_retry_storms       = Counter("nexora.retry_storms",         "Retry storm events")
        self.c_worker_crashes     = Counter("nexora.worker.crashes",       "Worker crash events")
        self.c_hitl_triggers      = Counter("nexora.hitl.triggers",        "HITL escalations")
        self.c_hitl_resolved      = Counter("nexora.hitl.resolved",        "HITL resolved")
        self.c_governance_audits  = Counter("nexora.governance.audits",    "Governance audit events")
        self.c_browser_sessions   = Counter("nexora.browser.sessions",     "Browser sessions opened")
        self.c_file_mutations     = Counter("nexora.file.mutations",       "File write operations")
        self.c_delegation_created = Counter("nexora.delegation.created",   "Delegations created")
        self.c_quota_exceeded     = Counter("nexora.quota.exceeded",       "Quota exceeded events")
        self.c_lock_timeouts      = Counter("nexora.lock.timeouts",        "Lock acquisition timeouts")

        # ── Gauges ────────────────────────────────────────────────────────────
        self.g_active_sessions    = Gauge("nexora.sessions.active",        "Active sessions")
        self.g_active_agents      = Gauge("nexora.agents.active",          "Active agent instances")
        self.g_browser_pool_used  = Gauge("nexora.browser.pool_used",      "Browser slots in use")
        self.g_terminal_pool_used = Gauge("nexora.terminal.pool_used",     "Terminal slots in use")
        self.g_token_burn_rate    = Gauge("nexora.tokens.burn_rate",       "Tokens/sec (1m avg)")
        self.g_sse_clients        = Gauge("nexora.sse.clients",            "Connected SSE clients")
        self.g_queue_depth        = Gauge("nexora.queue.depth",            "Tasks in queue")

        # ── Histograms ────────────────────────────────────────────────────────
        self.h_coordination_lat   = Histogram("nexora.coordination.latency",   "Delegation-to-complete latency (s)")
        self.h_semantic_val_lat   = Histogram("nexora.semantic_val.latency",   "Semantic validation duration (s)")
        self.h_hitl_resolution    = Histogram("nexora.hitl.resolution_time",   "HITL resolution time (s)")
        self.h_sse_event_lat      = Histogram("nexora.sse.event_latency",      "SSE event delivery latency (ms)")
        self.h_task_duration      = Histogram("nexora.task.duration",          "End-to-end task duration (s)")
        self.h_worker_heartbeat   = Histogram("nexora.worker.heartbeat_gap",   "Worker heartbeat gap (s)")

        # ── Ring buffers ──────────────────────────────────────────────────────
        self._events: Deque[TelemetryEvent] = collections.deque(maxlen=_RING_CAPACITY)
        self._lock = threading.Lock()

        # ── Token burn rate tracking ──────────────────────────────────────────
        self._token_timestamps: Deque[tuple] = collections.deque(maxlen=1000)  # (ts, count)
        self._burn_rate_thread = threading.Thread(
            target=self._update_burn_rate_loop, daemon=True, name="telemetry-burn-rate"
        )
        self._burn_rate_thread.start()

    # ── Structured event recording ────────────────────────────────────────────

    def record(self, category: str, event_type: str, payload: dict,
               session_id: str = "", tenant_id: str = "") -> None:
        evt = TelemetryEvent(
            category=category, event_type=event_type,
            session_id=session_id, tenant_id=tenant_id, payload=payload
        )
        with self._lock:
            self._events.append(evt)
        logger.debug(f"[Telemetry] {category}.{event_type} session={session_id}")

    # ── Specific recorders ────────────────────────────────────────────────────

    def record_tokens(self, count: int, session_id: str = "") -> None:
        self.c_tokens_total.inc(count)
        with self._lock:
            self._token_timestamps.append((time.time(), count))

    def record_retry_storm(self, session_id: str, role: str = "") -> None:
        self.c_retry_storms.inc()
        self.record("reliability", "retry_storm", {"role": role}, session_id=session_id)
        logger.warning(f"[Telemetry] 🔴 Retry storm: session={session_id} role={role}")

    def record_worker_crash(self, worker_id: str, error: str = "") -> None:
        self.c_worker_crashes.inc()
        self.record("reliability", "worker_crash",
                    {"worker_id": worker_id, "error": error[:200]})
        logger.error(f"[Telemetry] Worker crash: {worker_id}")

    def record_hitl(self, session_id: str, hitl_type: str, resolved: bool = False) -> None:
        if resolved:
            self.c_hitl_resolved.inc()
        else:
            self.c_hitl_triggers.inc()
        self.record("hitl", "resolved" if resolved else "triggered",
                    {"hitl_type": hitl_type}, session_id=session_id)

    def record_coordination_latency(self, latency_s: float) -> None:
        self.h_coordination_lat.observe(latency_s)

    def record_semantic_validation(self, latency_s: float) -> None:
        self.h_semantic_val_lat.observe(latency_s)

    def record_file_mutation(self, session_id: str, path: str) -> None:
        self.c_file_mutations.inc()
        self.record("workspace", "file_mutation", {"path": path[:100]}, session_id=session_id)

    def record_lock_timeout(self, session_id: str, path: str) -> None:
        self.c_lock_timeouts.inc()
        self.record("workspace", "lock_timeout", {"path": path[:100]}, session_id=session_id)
        logger.warning(f"[Telemetry] ⚠ Lock timeout: session={session_id} path={path[:80]}")

    def record_quota_exceeded(self, tenant_id: str, resource: str) -> None:
        self.c_quota_exceeded.inc()
        self.record("tenant", "quota_exceeded", {"resource": resource}, tenant_id=tenant_id)
        logger.warning(f"[Telemetry] ⚠ Quota exceeded: tenant={tenant_id} resource={resource}")

    # ── Burn rate calculation ──────────────────────────────────────────────────

    def _update_burn_rate_loop(self) -> None:
        while True:
            time.sleep(10)
            try:
                now = time.time()
                cutoff = now - 60.0
                with self._lock:
                    recent = [(ts, cnt) for ts, cnt in self._token_timestamps if ts >= cutoff]
                total = sum(cnt for _, cnt in recent)
                rate = total / 60.0
                self.g_token_burn_rate.set(round(rate, 2))
            except Exception:
                pass

    # ── Prometheus /metrics text export ──────────────────────────────────────

    def export_prometheus(self) -> str:
        """Exports all metrics in Prometheus text format."""
        lines = []
        for attr_name in dir(self):
            obj = getattr(self, attr_name, None)
            if isinstance(obj, Counter):
                lines.append(f"# HELP {obj.name} {obj.desc}")
                lines.append(f"# TYPE {obj.name} counter")
                lines.append(f"{obj.name.replace('.','_')} {obj.value()}")
            elif isinstance(obj, Gauge):
                lines.append(f"# HELP {obj.name} {obj.desc}")
                lines.append(f"# TYPE {obj.name} gauge")
                lines.append(f"{obj.name.replace('.','_')} {obj.value()}")
            elif isinstance(obj, Histogram):
                stats = obj.stats()
                lines.append(f"# HELP {obj.name} {obj.desc}")
                lines.append(f"# TYPE {obj.name} summary")
                for q, v in [("0.5", stats["p50"]), ("0.95", stats["p95"]), ("0.99", stats["p99"])]:
                    lines.append(f'{obj.name.replace(".","_")}{{quantile="{q}"}} {v}')
                lines.append(f"{obj.name.replace('.','_')}_count {stats['count']}")
        return "\n".join(lines) + "\n"

    # ── Operational snapshot ──────────────────────────────────────────────────

    def snapshot(self) -> dict:
        return {
            "counters": {
                "tokens_total":       self.c_tokens_total.value(),
                "sse_events":         self.c_sse_events.value(),
                "retries":            self.c_retries.value(),
                "retry_storms":       self.c_retry_storms.value(),
                "worker_crashes":     self.c_worker_crashes.value(),
                "hitl_triggers":      self.c_hitl_triggers.value(),
                "hitl_resolved":      self.c_hitl_resolved.value(),
                "governance_audits":  self.c_governance_audits.value(),
                "file_mutations":     self.c_file_mutations.value(),
                "quota_exceeded":     self.c_quota_exceeded.value(),
                "lock_timeouts":      self.c_lock_timeouts.value(),
            },
            "gauges": {
                "active_sessions":    self.g_active_sessions.value(),
                "active_agents":      self.g_active_agents.value(),
                "browser_pool_used":  self.g_browser_pool_used.value(),
                "terminal_pool_used": self.g_terminal_pool_used.value(),
                "token_burn_rate":    self.g_token_burn_rate.value(),
                "sse_clients":        self.g_sse_clients.value(),
                "queue_depth":        self.g_queue_depth.value(),
            },
            "latency": {
                "coordination_p95":   self.h_coordination_lat.stats()["p95"],
                "semantic_val_p95":   self.h_semantic_val_lat.stats()["p95"],
                "hitl_resolution_p95":self.h_hitl_resolution.stats()["p95"],
                "task_duration_p95":  self.h_task_duration.stats()["p95"],
            },
            "recent_events": self._recent_events(20),
        }

    def _recent_events(self, n: int) -> list:
        with self._lock:
            return [
                {
                    "category": e.category,
                    "event_type": e.event_type,
                    "session_id": e.session_id,
                    "ts": e.ts,
                }
                for e in list(self._events)[-n:]
            ]


# ─────────────────────────────────────────────────────────────────────────────
# Global singleton
# ─────────────────────────────────────────────────────────────────────────────

_telemetry: Optional[TelemetryCollector] = None
_telemetry_lock = threading.Lock()


def get_telemetry() -> TelemetryCollector:
    global _telemetry
    with _telemetry_lock:
        if _telemetry is None:
            _telemetry = TelemetryCollector()
    return _telemetry
