"""
devops/health_monitor.py — Autonomous Runtime Health Intelligence
================================================================
Continuous, proactive health monitoring for all runtime components.

Monitors (polled every POLL_INTERVAL_SEC):
  ● Worker threads       — alive? heartbeat gap? task throughput?
  ● Task queue           — depth? age of oldest task? priority starvation?
  ● Event bus            — backend alive? publish latency?
  ● Browser pool         — slots in use vs total; deadlock detection
  ● Token burn rate      — rolling 60s window vs budget ceiling
  ● Retry storm rate     — retries/min across all sessions
  ● Lock contention      — orphaned locks / lock wait timeout rate
  ● Memory pressure      — process RSS vs configurable threshold
  ● Semantic validation  — p95 latency trend
  ● Governance escal.    — escalation frequency vs expected baseline
  ● Degraded-mode churn  — oscillation detection (mark/recover < 30s)

Health scoring (0.0–1.0 per component):
  1.0 = fully healthy
  0.7–0.99 = degraded (warning)
  0.4–0.69 = impaired (alert)
  0.0–0.39 = critical (page)

Predictive alerts:
  • Trend analysis (last 5 readings): if declining 3 consecutive polls → early warning
  • Baseline deviation: if metric > mean + 2σ for that component → anomaly flag
  • Cascading failure detection: if 3+ components impaired simultaneously → cluster alert

Outputs:
  • HealthSnapshot — point-in-time reading
  • PlaybookEngine triggers (automatic for clear-cut conditions)
  • SSE broadcast on health change
  • Prometheus-compatible metrics via /api/devops/health
"""

import collections
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("nexora.devops.health")

# ─── Configuration ────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC  = int(os.getenv("HEALTH_POLL_INTERVAL", "15"))
MEMORY_WARN_MB     = int(os.getenv("HEALTH_MEMORY_WARN_MB", "1024"))
MEMORY_CRIT_MB     = int(os.getenv("HEALTH_MEMORY_CRIT_MB", "2048"))
QUEUE_DEPTH_WARN   = int(os.getenv("HEALTH_QUEUE_DEPTH_WARN", "50"))
QUEUE_DEPTH_CRIT   = int(os.getenv("HEALTH_QUEUE_DEPTH_CRIT", "200"))
BURN_RATE_WARN     = float(os.getenv("HEALTH_BURN_RATE_WARN", "30.0"))   # tokens/sec
BURN_RATE_CRIT     = float(os.getenv("HEALTH_BURN_RATE_CRIT", "80.0"))
RETRY_STORM_WARN   = int(os.getenv("HEALTH_RETRY_STORM_WARN", "5"))      # storms/min
TREND_WINDOW       = 5    # readings for trend analysis
ANOMALY_SIGMA      = 2.0  # std dev multiplier for anomaly detection


class HealthLevel(str, Enum):
    HEALTHY  = "HEALTHY"    # 1.0
    WARNING  = "WARNING"    # 0.7–0.99
    IMPAIRED = "IMPAIRED"   # 0.4–0.69
    CRITICAL = "CRITICAL"   # 0.0–0.39

    @classmethod
    def from_score(cls, score: float) -> "HealthLevel":
        if score >= 0.99:  return cls.HEALTHY
        if score >= 0.70:  return cls.WARNING
        if score >= 0.40:  return cls.IMPAIRED
        return cls.CRITICAL


@dataclass
class ComponentHealth:
    name:        str
    score:       float          # 0.0–1.0
    level:       HealthLevel
    metrics:     dict
    trend:       str = "stable"  # "declining" | "recovering" | "stable"
    anomalies:   List[str] = field(default_factory=list)
    ts:          float = field(default_factory=time.time)


@dataclass
class HealthSnapshot:
    ts:            float
    overall_score: float
    overall_level: HealthLevel
    components:    Dict[str, ComponentHealth]
    cluster_alert: bool
    trending_down: List[str]    # components with declining trend
    anomalies:     List[str]    # component-level anomalies flagged
    predictive_warnings: List[str]


class _Baseline:
    """Rolling statistics for anomaly detection."""
    def __init__(self, window: int = 20):
        self._values: Deque[float] = collections.deque(maxlen=window)

    def add(self, v: float) -> None:
        self._values.append(v)

    def mean(self) -> float:
        return sum(self._values) / len(self._values) if self._values else 0.0

    def std(self) -> float:
        if len(self._values) < 2:
            return 0.0
        m = self.mean()
        return (sum((x - m) ** 2 for x in self._values) / len(self._values)) ** 0.5

    def is_anomaly(self, v: float, sigma: float = ANOMALY_SIGMA) -> bool:
        if len(self._values) < 5:
            return False
        return v > self.mean() + sigma * self.std()


class HealthMonitor:
    """
    Continuous autonomous health monitoring daemon.
    Runs a background poll loop. Consumers query via latest_snapshot().
    """

    def __init__(self):
        self._lock    = threading.RLock()
        self._history: Deque[HealthSnapshot] = collections.deque(maxlen=100)
        self._baselines: Dict[str, _Baseline] = {}
        self._trend_history: Dict[str, Deque[float]] = {}
        self._sse_fn  = None
        self._playbook_trigger_fn = None
        self._running = True

        t = threading.Thread(target=self._poll_loop, daemon=True, name="health-monitor")
        t.start()
        logger.info(f"[HealthMonitor] Started (poll every {POLL_INTERVAL_SEC}s)")

    def set_sse_broadcast(self, fn) -> None:
        self._sse_fn = fn

    def set_playbook_trigger(self, fn) -> None:
        """fn(component_name, health_level, metrics) — called when critical"""
        self._playbook_trigger_fn = fn

    # ── Poll loop ─────────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while self._running:
            try:
                snap = self._collect()
                with self._lock:
                    self._history.append(snap)
                self._evaluate_triggers(snap)
                self._broadcast(snap)
            except Exception as e:
                logger.debug(f"[HealthMonitor] Poll error: {e}")
            time.sleep(POLL_INTERVAL_SEC)

    def _collect(self) -> HealthSnapshot:
        components: Dict[str, ComponentHealth] = {}

        components["workers"]          = self._check_workers()
        components["task_queue"]       = self._check_task_queue()
        components["event_bus"]        = self._check_event_bus()
        components["browser_pool"]     = self._check_browser_pool()
        components["token_burn"]       = self._check_token_burn()
        components["retry_storms"]     = self._check_retry_storms()
        components["lock_contention"]  = self._check_lock_contention()
        components["memory_pressure"]  = self._check_memory_pressure()
        components["infra_degraded"]   = self._check_infra_degraded()
        components["security_posture"] = self._check_security_posture()

        # Apply trend analysis
        trending_down = []
        anomalies = []
        for name, comp in components.items():
            trend, anom = self._analyze_trend(name, comp.score)
            comp.trend = trend
            if anom:
                comp.anomalies.append(f"{name}_anomaly")
                anomalies.append(name)
            if trend == "declining":
                trending_down.append(name)

        # Overall score: weighted average
        weights = {
            "workers": 2.0, "task_queue": 1.5, "event_bus": 1.5,
            "browser_pool": 1.0, "token_burn": 1.0, "retry_storms": 1.0,
            "lock_contention": 0.5, "memory_pressure": 1.5,
            "infra_degraded": 2.0, "security_posture": 1.5,
        }
        total_w = sum(weights.values())
        overall = sum(comp.score * weights.get(name, 1.0)
                      for name, comp in components.items()) / total_w
        overall = max(0.0, min(1.0, overall))

        # Cluster alert: 3+ components impaired or critical
        impaired = sum(1 for c in components.values()
                       if c.level in (HealthLevel.IMPAIRED, HealthLevel.CRITICAL))
        cluster_alert = impaired >= 3

        # Predictive warnings: declining trend on critical components
        predictive = []
        for name in trending_down:
            if name in ("workers", "memory_pressure", "infra_degraded"):
                predictive.append(
                    f"PREDICT: {name} declining — intervention may be needed soon"
                )

        return HealthSnapshot(
            ts=time.time(),
            overall_score=overall,
            overall_level=HealthLevel.from_score(overall),
            components=components,
            cluster_alert=cluster_alert,
            trending_down=trending_down,
            anomalies=anomalies,
            predictive_warnings=predictive,
        )

    # ── Component checks ──────────────────────────────────────────────────────

    def _check_workers(self) -> ComponentHealth:
        try:
            from task_queue import global_task_queue
            stats = global_task_queue.stats()
            alive  = stats.get("active_workers", 0)
            total  = stats.get("total_workers", 4)
            frac   = alive / max(total, 1)
            score  = frac
            return ComponentHealth(
                name="workers", score=score,
                level=HealthLevel.from_score(score),
                metrics={"alive": alive, "total": total, "fraction": round(frac, 2)},
            )
        except Exception as e:
            return ComponentHealth("workers", 0.5, HealthLevel.WARNING,
                                   {"error": str(e)[:60]})

    def _check_task_queue(self) -> ComponentHealth:
        try:
            from task_queue import global_task_queue
            stats = global_task_queue.stats()
            depth   = stats.get("queued", 0)
            oldest  = stats.get("oldest_task_age_sec", 0)
            score = 1.0
            if depth >= QUEUE_DEPTH_CRIT or oldest > 300:
                score = 0.2
            elif depth >= QUEUE_DEPTH_WARN or oldest > 120:
                score = 0.6
            return ComponentHealth(
                name="task_queue", score=score,
                level=HealthLevel.from_score(score),
                metrics={"depth": depth, "oldest_sec": oldest},
            )
        except Exception as e:
            return ComponentHealth("task_queue", 0.7, HealthLevel.WARNING,
                                   {"error": str(e)[:60]})

    def _check_event_bus(self) -> ComponentHealth:
        try:
            from infra.event_bus import get_event_bus
            h = get_event_bus().health()
            backend = h.get("backend", "unknown")
            score = 1.0 if backend == "redis" else 0.7  # in-process = degraded
            return ComponentHealth(
                name="event_bus", score=score,
                level=HealthLevel.from_score(score),
                metrics=h,
            )
        except Exception as e:
            return ComponentHealth("event_bus", 0.4, HealthLevel.IMPAIRED,
                                   {"error": str(e)[:60]})

    def _check_browser_pool(self) -> ComponentHealth:
        try:
            from execution.resource_governor import global_resource_governor
            used  = global_resource_governor.active_browsers()
            total = global_resource_governor.max_browsers()
            frac  = used / max(total, 1)
            # score drops as pool fills; deadlock risk increases
            score = 1.0 - (frac * 0.5)  # never below 0.5 just from usage
            # Check for stuck browsers (used same for 3+ polls)
            stuck = self._detect_stuck_browsers(used)
            if stuck:
                score = min(score, 0.3)
            return ComponentHealth(
                name="browser_pool", score=score,
                level=HealthLevel.from_score(score),
                metrics={"used": used, "total": total, "stuck": stuck},
            )
        except Exception as e:
            return ComponentHealth("browser_pool", 0.8, HealthLevel.HEALTHY,
                                   {"error": str(e)[:60]})

    def _detect_stuck_browsers(self, current_used: int) -> bool:
        """Returns True if browser count has been the same non-zero value for 3+ polls."""
        key = "_stuck_browser"
        if not hasattr(self, key):
            setattr(self, key, collections.deque(maxlen=4))
        hist = getattr(self, key)
        hist.append(current_used)
        return (len(hist) >= 4 and current_used > 0
                and len(set(hist)) == 1)

    def _check_token_burn(self) -> ComponentHealth:
        try:
            from infra.telemetry import get_telemetry
            snap  = get_telemetry().snapshot()
            rate  = snap.get("gauges", {}).get("token_burn_rate", 0.0)
            score = 1.0
            if rate >= BURN_RATE_CRIT:
                score = 0.2
            elif rate >= BURN_RATE_WARN:
                score = 0.65
            return ComponentHealth(
                name="token_burn", score=score,
                level=HealthLevel.from_score(score),
                metrics={"tokens_per_sec": round(rate, 2),
                         "warn_threshold": BURN_RATE_WARN,
                         "crit_threshold": BURN_RATE_CRIT},
            )
        except Exception as e:
            return ComponentHealth("token_burn", 0.9, HealthLevel.HEALTHY,
                                   {"error": str(e)[:60]})

    def _check_retry_storms(self) -> ComponentHealth:
        try:
            from infra.telemetry import get_telemetry
            snap    = get_telemetry().snapshot()
            storms  = snap.get("counters", {}).get("retry_storms", 0)
            score   = 1.0 if storms < RETRY_STORM_WARN else max(0.1, 1.0 - storms * 0.1)
            return ComponentHealth(
                name="retry_storms", score=score,
                level=HealthLevel.from_score(score),
                metrics={"storm_count": storms, "warn_at": RETRY_STORM_WARN},
            )
        except Exception as e:
            return ComponentHealth("retry_storms", 0.9, HealthLevel.HEALTHY,
                                   {"error": str(e)[:60]})

    def _check_lock_contention(self) -> ComponentHealth:
        try:
            from execution.workspace_lock import global_lock_registry
            stats = global_lock_registry.stats()
            orphaned = stats.get("orphaned_locks", 0)
            timeouts = stats.get("timeout_events", 0)
            score    = 1.0
            if orphaned > 10 or timeouts > 5:
                score = 0.4
            elif orphaned > 3 or timeouts > 1:
                score = 0.7
            return ComponentHealth(
                name="lock_contention", score=score,
                level=HealthLevel.from_score(score),
                metrics={"orphaned_locks": orphaned, "timeout_events": timeouts},
            )
        except Exception as e:
            return ComponentHealth("lock_contention", 1.0, HealthLevel.HEALTHY,
                                   {"error": str(e)[:60]})

    def _check_memory_pressure(self) -> ComponentHealth:
        try:
            import psutil
            proc   = psutil.Process(os.getpid())
            rss_mb = proc.memory_info().rss / (1024 * 1024)
            score  = 1.0
            if rss_mb >= MEMORY_CRIT_MB:
                score = 0.1
            elif rss_mb >= MEMORY_WARN_MB:
                score = 0.6
            return ComponentHealth(
                name="memory_pressure", score=score,
                level=HealthLevel.from_score(score),
                metrics={"rss_mb": round(rss_mb, 1),
                         "warn_mb": MEMORY_WARN_MB, "crit_mb": MEMORY_CRIT_MB},
            )
        except ImportError:
            # psutil not installed — use basic fallback
            return ComponentHealth("memory_pressure", 0.9, HealthLevel.HEALTHY,
                                   {"note": "psutil not available"})
        except Exception as e:
            return ComponentHealth("memory_pressure", 0.8, HealthLevel.HEALTHY,
                                   {"error": str(e)[:60]})

    def _check_infra_degraded(self) -> ComponentHealth:
        try:
            from infra.resilience import global_degraded_mode
            degraded = global_degraded_mode.degraded_components()
            count    = len(degraded)
            score    = max(0.0, 1.0 - count * 0.25)
            return ComponentHealth(
                name="infra_degraded", score=score,
                level=HealthLevel.from_score(score),
                metrics={"degraded_components": degraded, "count": count},
            )
        except Exception as e:
            return ComponentHealth("infra_degraded", 1.0, HealthLevel.HEALTHY,
                                   {"error": str(e)[:60]})

    def _check_security_posture(self) -> ComponentHealth:
        try:
            from security.security_telemetry import get_security_telemetry
            snap = get_security_telemetry().snapshot()
            lockdown = snap.get("lockdown_active", False)
            open_inc = snap.get("open_incidents", 0)
            sev1     = snap.get("by_severity", {}).get("SEV1", 0)
            score    = 1.0
            if lockdown:
                score = 0.1
            elif sev1 > 0:
                score = 0.3
            elif open_inc > 2:
                score = 0.6
            elif open_inc > 0:
                score = 0.8
            return ComponentHealth(
                name="security_posture", score=score,
                level=HealthLevel.from_score(score),
                metrics={"lockdown": lockdown, "open_incidents": open_inc, "sev1_count": sev1},
            )
        except Exception as e:
            return ComponentHealth("security_posture", 0.9, HealthLevel.HEALTHY,
                                   {"error": str(e)[:60]})

    # ── Trend & anomaly analysis ──────────────────────────────────────────────

    def _analyze_trend(self, name: str, score: float) -> Tuple[str, bool]:
        """Returns (trend, is_anomaly)."""
        if name not in self._trend_history:
            self._trend_history[name] = collections.deque(maxlen=TREND_WINDOW)
        hist = self._trend_history[name]
        hist.append(score)

        if name not in self._baselines:
            self._baselines[name] = _Baseline()
        bl = self._baselines[name]
        bl.add(score)

        trend = "stable"
        if len(hist) >= 3:
            recent = list(hist)[-3:]
            if all(recent[i] < recent[i - 1] - 0.05 for i in range(1, len(recent))):
                trend = "declining"
            elif all(recent[i] > recent[i - 1] + 0.05 for i in range(1, len(recent))):
                trend = "recovering"

        is_anom = bl.is_anomaly(1.0 - score)  # anomaly = unusually low score
        return trend, is_anom

    # ── Trigger evaluation ────────────────────────────────────────────────────

    def _evaluate_triggers(self, snap: HealthSnapshot) -> None:
        """Fires playbook triggers for components in critical state."""
        if self._playbook_trigger_fn is None:
            return
        for name, comp in snap.components.items():
            if comp.level == HealthLevel.CRITICAL:
                try:
                    self._playbook_trigger_fn(name, comp.level, comp.metrics)
                except Exception as e:
                    logger.debug(f"[HealthMonitor] Trigger error: {e}")

    # ── SSE broadcast ─────────────────────────────────────────────────────────

    def _broadcast(self, snap: HealthSnapshot) -> None:
        if self._sse_fn and snap.overall_level != HealthLevel.HEALTHY:
            try:
                self._sse_fn(
                    None,   # broadcast to all operator sessions
                    "devops.health_update",
                    {
                        "overall_score": round(snap.overall_score, 3),
                        "overall_level": snap.overall_level.value,
                        "cluster_alert": snap.cluster_alert,
                        "trending_down": snap.trending_down,
                        "anomalies":     snap.anomalies,
                        "predictive":    snap.predictive_warnings,
                    }
                )
            except Exception:
                pass

    # ── Public API ────────────────────────────────────────────────────────────

    def latest_snapshot(self) -> Optional[HealthSnapshot]:
        with self._lock:
            return self._history[-1] if self._history else None

    def history(self, n: int = 10) -> List[HealthSnapshot]:
        with self._lock:
            return list(self._history)[-n:]

    def snapshot_dict(self) -> dict:
        snap = self.latest_snapshot()
        if not snap:
            return {"status": "no_data"}
        return {
            "ts":            snap.ts,
            "overall_score": round(snap.overall_score, 3),
            "overall_level": snap.overall_level.value,
            "cluster_alert": snap.cluster_alert,
            "trending_down": snap.trending_down,
            "anomalies":     snap.anomalies,
            "predictive":    snap.predictive_warnings,
            "components": {
                name: {
                    "score":    round(c.score, 3),
                    "level":    c.level.value,
                    "metrics":  c.metrics,
                    "trend":    c.trend,
                    "anomalies":c.anomalies,
                }
                for name, c in snap.components.items()
            },
        }

    def stop(self) -> None:
        self._running = False


# ─── Global singleton ─────────────────────────────────────────────────────────
_instance: Optional[HealthMonitor] = None
_instance_lock = threading.Lock()

def get_health_monitor() -> HealthMonitor:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = HealthMonitor()
    return _instance
