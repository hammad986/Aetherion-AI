"""
backend/execution/fabric/metrics.py — Phase 23 Execution Fabric Metrics Collector.

Thread-safe, zero-external-dependency metrics engine providing:
- Task lifecycle metrics (submitted, claimed, running, completed, failed, cancelled, retried, recovered)
- Queue latency and depth metrics
- Worker fleet capacity, utilization, and heartbeat health
- Agent tool execution, LLM call, and step duration metrics
- Dual exposition: Structured JSON and Prometheus text exposition format
"""

from __future__ import annotations

from collections import defaultdict
import threading
import time
from typing import Any, Dict, List, Optional


class FabricMetricsCollector:
    """
    Authoritative metrics engine for the Execution Fabric and Worker Runtime.
    Thread-safe counter, gauge, and summary aggregations.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """Reset all metrics back to baseline (primarily used in tests)."""
        with self._lock:
            # Task metrics
            self._tasks_submitted_total = 0
            self._tasks_claimed_total = 0
            self._tasks_completed_total = 0
            self._tasks_failed_total = 0
            self._tasks_cancelled_total = 0
            self._tasks_retried_total = 0
            self._tasks_recovered_total = 0
            self._tasks_running = 0

            # Sub-breakdowns
            self._tasks_by_tenant: Dict[str, int] = defaultdict(int)
            self._tasks_by_priority: Dict[int, int] = defaultdict(int)
            self._failures_by_class: Dict[str, int] = defaultdict(int)
            self._recoveries_by_reason: Dict[str, int] = defaultdict(int)

            # Queue latency tracking (in seconds)
            self._queue_latency_sum = 0.0
            self._queue_latency_count = 0
            self._queue_depth_by_prio: Dict[int, int] = defaultdict(int)

            # Worker metrics
            self._workers_registered = 0
            self._workers_by_status: Dict[str, int] = defaultdict(int)
            self._worker_capacity = 0
            self._worker_utilized = 0
            self._worker_heartbeats: Dict[str, float] = {}

            # Agent / Tool / LLM metrics
            self._tool_executions: Dict[str, int] = defaultdict(int)
            self._llm_calls: Dict[str, int] = defaultdict(int)
            self._llm_tokens: Dict[str, int] = defaultdict(int)
            self._step_duration_sum = 0.0
            self._step_duration_count = 0
            self._hitl_wait_sum = 0.0
            self._hitl_wait_count = 0

    # ── Task Tracking ────────────────────────────────────────────────────────

    def record_task_submitted(self, task_id: str, tenant_id: str = "default", priority: int = 50) -> None:
        """Record task submission to queue."""
        with self._lock:
            self._tasks_submitted_total += 1
            self._tasks_by_tenant[tenant_id] += 1
            self._tasks_by_priority[priority] += 1
            self._queue_depth_by_prio[priority] += 1

    def record_task_claimed(
        self,
        task_id: str,
        worker_id: str = "unknown",
        queue_latency_sec: float = 0.0,
        priority: int = 50,
    ) -> None:
        """Record task claim by a worker and record queue wait time."""
        with self._lock:
            self._tasks_claimed_total += 1
            self._tasks_running += 1
            if queue_latency_sec > 0:
                self._queue_latency_sum += queue_latency_sec
                self._queue_latency_count += 1
            if self._queue_depth_by_prio[priority] > 0:
                self._queue_depth_by_prio[priority] -= 1

    def record_task_completed(self, task_id: str, tenant_id: str = "default", duration_sec: float = 0.0) -> None:
        """Record successful task completion."""
        with self._lock:
            self._tasks_completed_total += 1
            self._tasks_running = max(0, self._tasks_running - 1)

    def record_task_failed(
        self,
        task_id: str,
        tenant_id: str = "default",
        error_class: str = "GenericError",
        duration_sec: float = 0.0,
    ) -> None:
        """Record task execution failure."""
        with self._lock:
            self._tasks_failed_total += 1
            self._failures_by_class[error_class] += 1
            self._tasks_running = max(0, self._tasks_running - 1)

    def record_task_cancelled(self, task_id: str) -> None:
        """Record task cancellation."""
        with self._lock:
            self._tasks_cancelled_total += 1
            self._tasks_running = max(0, self._tasks_running - 1)

    def record_task_retried(self, task_id: str) -> None:
        """Record task scheduled for retry."""
        with self._lock:
            self._tasks_retried_total += 1

    def record_task_recovered(self, task_id: str, reason: str = "orphan_detected") -> None:
        """Record orphaned or crashed task recovery."""
        with self._lock:
            self._tasks_recovered_total += 1
            self._recoveries_by_reason[reason] += 1
            self._tasks_running = max(0, self._tasks_running - 1)

    # ── Worker Tracking ──────────────────────────────────────────────────────

    def record_worker_heartbeat(
        self,
        worker_id: str,
        status: str = "READY",
        active_concurrency: int = 0,
        max_concurrency: int = 4,
    ) -> None:
        """Update worker fleet health metrics."""
        with self._lock:
            self._worker_heartbeats[worker_id] = time.time()
            self._workers_by_status[status] = self._workers_by_status.get(status, 0)

    def sync_worker_fleet(self, workers: List[Any]) -> None:
        """Synchronize metrics with current store worker snapshot."""
        with self._lock:
            self._workers_registered = len(workers)
            self._workers_by_status.clear()
            cap = 0
            util = 0
            now = time.time()

            for w in workers:
                st = getattr(w.status, "value", str(w.status))
                self._workers_by_status[st] = self._workers_by_status.get(st, 0) + 1
                cap += getattr(w, "max_concurrency", 0)
                util += getattr(w, "active_concurrency", 0)
                hb = getattr(w, "last_heartbeat", 0.0)
                if hb > 0:
                    self._worker_heartbeats[w.worker_id] = hb

            self._worker_capacity = cap
            self._worker_utilized = util

    def record_worker_stopped(self, worker_id: str) -> None:
        """Record worker deregistration or shutdown."""
        with self._lock:
            self._worker_heartbeats.pop(worker_id, None)

    # ── Agent & LLM Tracking ─────────────────────────────────────────────────

    def record_agent_step(self, tool_name: str, duration_sec: float = 0.0, success: bool = True) -> None:
        """Record agent tool execution step."""
        with self._lock:
            status_tag = "success" if success else "failure"
            key = f"{tool_name}:{status_tag}"
            self._tool_executions[key] += 1
            if duration_sec > 0:
                self._step_duration_sum += duration_sec
                self._step_duration_count += 1

    def record_llm_call(self, model: str, tokens: int = 0, duration_sec: float = 0.0) -> None:
        """Record model invocation metrics."""
        with self._lock:
            self._llm_calls[model] += 1
            if tokens > 0:
                self._llm_tokens[model] += tokens

    def record_hitl_wait(self, duration_sec: float) -> None:
        """Record human-in-the-loop wait duration."""
        with self._lock:
            if duration_sec > 0:
                self._hitl_wait_sum += duration_sec
                self._hitl_wait_count += 1

    # ── Export Formats ───────────────────────────────────────────────────────

    def get_metrics(self) -> Dict[str, Any]:
        """Export all metrics as a structured JSON-serializable dictionary."""
        with self._lock:
            avg_queue_latency = (
                (self._queue_latency_sum / self._queue_latency_count)
                if self._queue_latency_count > 0
                else 0.0
            )
            avg_step_duration = (
                (self._step_duration_sum / self._step_duration_count)
                if self._step_duration_count > 0
                else 0.0
            )
            avg_hitl_wait = (
                (self._hitl_wait_sum / self._hitl_wait_count)
                if self._hitl_wait_count > 0
                else 0.0
            )

            total_queue_depth = sum(self._queue_depth_by_prio.values())

            return {
                "tasks": {
                    "submitted_total": self._tasks_submitted_total,
                    "claimed_total": self._tasks_claimed_total,
                    "running": self._tasks_running,
                    "completed_total": self._tasks_completed_total,
                    "failed_total": self._tasks_failed_total,
                    "cancelled_total": self._tasks_cancelled_total,
                    "retried_total": self._tasks_retried_total,
                    "recovered_total": self._tasks_recovered_total,
                    "by_tenant": dict(self._tasks_by_tenant),
                    "failures_by_class": dict(self._failures_by_class),
                    "recoveries_by_reason": dict(self._recoveries_by_reason),
                },
                "queue": {
                    "depth_total": total_queue_depth,
                    "depth_by_priority": {str(k): v for k, v in self._queue_depth_by_prio.items()},
                    "latency": {
                        "count": self._queue_latency_count,
                        "sum_seconds": round(self._queue_latency_sum, 4),
                        "avg_seconds": round(avg_queue_latency, 4),
                    },
                },
                "workers": {
                    "registered_total": self._workers_registered,
                    "by_status": dict(self._workers_by_status),
                    "capacity_total": self._worker_capacity,
                    "utilized_total": self._worker_utilized,
                    "active_heartbeats_count": len(self._worker_heartbeats),
                },
                "agent": {
                    "tool_executions": dict(self._tool_executions),
                    "llm_calls": dict(self._llm_calls),
                    "llm_tokens": dict(self._llm_tokens),
                    "step_duration": {
                        "count": self._step_duration_count,
                        "sum_seconds": round(self._step_duration_sum, 4),
                        "avg_seconds": round(avg_step_duration, 4),
                    },
                    "hitl_wait": {
                        "count": self._hitl_wait_count,
                        "sum_seconds": round(self._hitl_wait_sum, 4),
                        "avg_seconds": round(avg_hitl_wait, 4),
                    },
                },
                "timestamp": time.time(),
            }

    def to_prometheus_format(self) -> str:
        """Export metrics formatted according to Prometheus exposition standards."""
        data = self.get_metrics()
        lines: List[str] = []

        def add_counter(name: str, val: float, help_text: str, labels: Optional[Dict[str, str]] = None) -> None:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            lbl_str = ""
            if labels:
                lbl_str = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}"
            lines.append(f"{name}{lbl_str} {val}")

        def add_gauge(name: str, val: float, help_text: str, labels: Optional[Dict[str, str]] = None) -> None:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            lbl_str = ""
            if labels:
                lbl_str = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}"
            lines.append(f"{name}{lbl_str} {val}")

        # Tasks
        add_counter("fabric_tasks_submitted_total", data["tasks"]["submitted_total"], "Total tasks submitted to fabric")
        add_counter("fabric_tasks_claimed_total", data["tasks"]["claimed_total"], "Total tasks claimed by workers")
        add_gauge("fabric_tasks_running", data["tasks"]["running"], "Current number of in-flight tasks")
        add_counter("fabric_tasks_completed_total", data["tasks"]["completed_total"], "Total successfully completed tasks")
        add_counter("fabric_tasks_failed_total", data["tasks"]["failed_total"], "Total failed tasks")
        add_counter("fabric_tasks_cancelled_total", data["tasks"]["cancelled_total"], "Total cancelled tasks")
        add_counter("fabric_tasks_retried_total", data["tasks"]["retried_total"], "Total task retry attempts")
        add_counter("fabric_tasks_recovered_total", data["tasks"]["recovered_total"], "Total orphaned tasks recovered")

        # Queue
        add_gauge("fabric_queue_depth", data["queue"]["depth_total"], "Current total queue depth")
        add_counter("fabric_queue_latency_seconds_sum", data["queue"]["latency"]["sum_seconds"], "Sum of task queue latencies")
        add_counter("fabric_queue_latency_seconds_count", data["queue"]["latency"]["count"], "Count of measured task queue latencies")

        # Workers
        add_gauge("fabric_workers_registered", data["workers"]["registered_total"], "Total registered execution workers")
        add_gauge("fabric_worker_capacity", data["workers"]["capacity_total"], "Total worker concurrency capacity")
        add_gauge("fabric_worker_utilized", data["workers"]["utilized_total"], "Current concurrency slots utilized")

        for status, count in data["workers"]["by_status"].items():
            add_gauge("fabric_workers_status_count", count, "Workers count by status", {"status": status})

        # Agent
        for tool_tag, count in data["agent"]["tool_executions"].items():
            parts = tool_tag.split(":", 1)
            tool_name = parts[0]
            st = parts[1] if len(parts) > 1 else "unknown"
            add_counter("fabric_agent_tool_executions_total", count, "Total tool executions", {"tool": tool_name, "status": st})

        for model, count in data["agent"]["llm_calls"].items():
            add_counter("fabric_agent_llm_calls_total", count, "Total LLM calls", {"model": model})

        for model, tokens in data["agent"]["llm_tokens"].items():
            add_counter("fabric_agent_llm_tokens_total", tokens, "Total LLM tokens", {"model": model})

        return "\n".join(lines) + "\n"


# ── Global Collector Singleton ───────────────────────────────────────────────

_GLOBAL_COLLECTOR: Optional[FabricMetricsCollector] = None
_COLLECTOR_LOCK = threading.Lock()


def get_metrics_collector() -> FabricMetricsCollector:
    """Obtain or instantiate the global metrics collector singleton."""
    global _GLOBAL_COLLECTOR
    if _GLOBAL_COLLECTOR is None:
        with _COLLECTOR_LOCK:
            if _GLOBAL_COLLECTOR is None:
                _GLOBAL_COLLECTOR = FabricMetricsCollector()
    return _GLOBAL_COLLECTOR
