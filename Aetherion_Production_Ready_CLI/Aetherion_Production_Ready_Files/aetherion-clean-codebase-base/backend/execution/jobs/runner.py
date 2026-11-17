"""
backend/execution/jobs/runner.py — Executes Background Jobs via RealAgentRuntime with Thread Concurrency.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from ..agent_runtime.runtime import RealAgentRuntime
from .models import BackgroundJob, JobAttempt, JobStatus
from .store import JobStore

logger = logging.getLogger("aetherion.jobs.runner")


class JobRunner:
    """Manages asynchronous job execution through the authoritative RealAgentRuntime."""

    def __init__(self, store: Optional[JobStore] = None, provider: Optional[BaseModelProvider] = None):
        self.store = store or JobStore()
        self.provider = provider
        self._active_jobs: Dict[str, Tuple[threading.Thread, threading.Event, threading.Event]] = {}
        # Map: job_id -> (Thread, cancel_event, pause_event)
        self._lock = threading.Lock()

    def start_job(
        self,
        job_id: str,
        owner_id: str,
        workspace_dir: str | Path,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        resume_from_checkpoint: bool = False,
    ) -> BackgroundJob:
        """Launch job execution asynchronously in background thread."""
        with self._lock:
            job = self.store.get_job(job_id, owner_id)
            if not job:
                raise KeyError(f"Job '{job_id}' not found.")
            if not resume_from_checkpoint and job.status == JobStatus.RUNNING.value:
                raise ValueError(f"Job '{job_id}' is already running.")
            if job.status == JobStatus.COMPLETED.value:
                raise ValueError(f"Job '{job_id}' has already completed. Use retry to run a new attempt.")

            cancel_event = threading.Event()
            pause_event = threading.Event()

            # Atomic transition to RUNNING
            allowed_from = [JobStatus.QUEUED.value, JobStatus.PAUSED.value]
            if resume_from_checkpoint:
                allowed_from.append(JobStatus.RUNNING.value)

            self.store.atomic_transition(
                job_id=job_id,
                owner_id=owner_id,
                from_statuses=allowed_from,
                to_status=JobStatus.RUNNING.value,
            )
            job = self.store.get_job(job_id, owner_id)

            t = threading.Thread(
                target=self._run_job_thread,
                args=(job_id, owner_id, Path(workspace_dir).resolve(), cancel_event, pause_event, on_event, resume_from_checkpoint),
                name=f"JobRunner-{job_id}",
                daemon=True,
            )
            self._active_jobs[job_id] = (t, cancel_event, pause_event)
            t.start()
            return job

    def cancel_job(self, job_id: str, owner_id: str) -> bool:
        """Signal job cancellation and ensure status immediately transitions to CANCELLED atomically."""
        with self._lock:
            job = self.store.get_job(job_id, owner_id)
            if not job:
                raise KeyError(f"Job '{job_id}' not found.")

            if job.status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value):
                # Terminal jobs cannot be cancelled; treat as safe idempotent no-op without uncaught ValueError
                return False

            if job_id in self._active_jobs:
                _, cancel_event, _ = self._active_jobs[job_id]
                cancel_event.set()

            # Atomic database transition from non-terminal states
            ok = self.store.atomic_transition(
                job_id=job_id,
                owner_id=owner_id,
                from_statuses=[
                    JobStatus.QUEUED.value,
                    JobStatus.RUNNING.value,
                    JobStatus.PAUSE_REQUESTED.value,
                    JobStatus.PAUSED.value,
                ],
                to_status=JobStatus.CANCELLED.value,
                error="CANCELLED: Execution was cancelled by operator.",
                finished_at=time.time(),
            )
            return ok

    def pause_job(self, job_id: str, owner_id: str) -> bool:
        """Request pause for an active running job."""
        with self._lock:
            job = self.store.get_job(job_id, owner_id)
            if not job:
                raise KeyError(f"Job '{job_id}' not found.")
            if job.status != JobStatus.RUNNING.value:
                raise ValueError(f"Cannot pause job in status '{job.status}'.")

            if job_id in self._active_jobs:
                _, _, pause_event = self._active_jobs[job_id]
                pause_event.set()

            # Transition atomically to PAUSED
            ok = self.store.atomic_transition(
                job_id=job_id,
                owner_id=owner_id,
                from_statuses=[JobStatus.RUNNING.value, JobStatus.PAUSE_REQUESTED.value],
                to_status=JobStatus.PAUSED.value,
            )
            return ok

    def resume_job(self, job_id: str, owner_id: str, workspace_dir: str | Path) -> BackgroundJob:
        """Resume a paused job from its persisted checkpoint."""
        with self._lock:
            job = self.store.get_job(job_id, owner_id)
            if not job:
                raise KeyError(f"Job '{job_id}' not found.")
            if job.status != JobStatus.PAUSED.value:
                raise ValueError(f"Cannot resume job in status '{job.status}'.")

            # Clean previous active entry if dead
            self._active_jobs.pop(job_id, None)

        # Restart job execution with checkpoint continuation
        return self.start_job(job_id, owner_id, workspace_dir, resume_from_checkpoint=True)

    def _run_job_thread(
        self,
        job_id: str,
        owner_id: str,
        workspace_dir: Path,
        cancel_event: threading.Event,
        pause_event: threading.Event,
        on_event: Optional[Callable[[Dict[str, Any]], None]],
        resume_from_checkpoint: bool = False,
    ) -> None:
        """Worker thread executing the job through RealAgentRuntime with atomic state persistence."""
        job = self.store.get_job(job_id, owner_id)
        if not job:
            return

        t0 = time.time()
        attempt = JobAttempt(
            job_id=job_id,
            owner_id=owner_id,
            attempt_number=job.attempt_number,
            status=JobStatus.RUNNING.value,
            started_at=t0,
        )
        self.store.record_attempt(attempt)

        # Check if this is a system maintenance job
        is_maintenance = (
            (job.name and job.name.startswith("memory_maintenance:"))
            or (job.metadata and job.metadata.get("type") == "maintenance")
            or job.task in (
                "embedding_backfill",
                "stale_embedding_repair",
                "duplicate_detection",
                "index_rebuild",
                "contradiction_scan",
                "consolidation_scan",
                "cache_prune_expired",
                "memory_lifecycle_cleanup",
                "staleness_prune",
            )
        )

        if is_maintenance:
            try:
                from ..memory.maintenance import dispatch_maintenance_job
                from ..memory.store import MemoryStore
                maintenance_store = MemoryStore(self.store.db.db_path)

                if cancel_event.is_set():
                    with self._lock:
                        self.store.atomic_transition(
                            job_id=job_id,
                            owner_id=owner_id,
                            from_statuses=[JobStatus.RUNNING.value, JobStatus.QUEUED.value],
                            to_status=JobStatus.CANCELLED.value,
                            error="Execution was cancelled by operator.",
                            finished_at=time.time(),
                        )
                    return

                extra_kwargs = {}
                if job.metadata:
                    for k, v in job.metadata.items():
                        if k not in ("action", "type", "checkpoint"):
                            extra_kwargs[k] = v

                if "batch_size" in extra_kwargs and "limit" not in extra_kwargs:
                    try:
                        extra_kwargs["limit"] = int(extra_kwargs.pop("batch_size"))
                    except Exception:
                        pass
                if "similarity_threshold" in extra_kwargs:
                    try:
                        extra_kwargs["similarity_threshold"] = float(extra_kwargs["similarity_threshold"])
                    except Exception:
                        pass

                result = dispatch_maintenance_job(
                    job_name=job.task,
                    owner_id=owner_id,
                    store=maintenance_store,
                    **extra_kwargs,
                )

                finished_at = time.time()
                if cancel_event.is_set():
                    final_status = JobStatus.CANCELLED.value
                    final_error = "Execution was cancelled by operator."
                else:
                    final_status = JobStatus.COMPLETED.value
                    final_error = None

                with self._lock:
                    self.store.atomic_transition(
                        job_id=job_id,
                        owner_id=owner_id,
                        from_statuses=[JobStatus.RUNNING.value],
                        to_status=final_status,
                        result=result if isinstance(result, dict) else {"output": str(result)},
                        error=final_error,
                        finished_at=finished_at,
                    )

                attempt.finished_at = finished_at
                attempt.status = final_status
                attempt.error = final_error
                attempt.result = result if isinstance(result, dict) else {"output": str(result)}
                conn = self.store.db.get_connection()
                try:
                    import json
                    conn.execute(
                        """
                        UPDATE job_attempts SET
                            status = ?,
                            finished_at = ?,
                            error = ?,
                            result_json = ?
                        WHERE id = ?
                        """,
                        (attempt.status, attempt.finished_at, attempt.error, json.dumps(attempt.result), attempt.id),
                    )
                finally:
                    conn.close()

            except Exception as exc:
                logger.exception("Background maintenance job '%s' failed: %s", job_id, exc)
                finished_at = time.time()
                with self._lock:
                    self.store.atomic_transition(
                        job_id=job_id,
                        owner_id=owner_id,
                        from_statuses=[JobStatus.RUNNING.value, JobStatus.QUEUED.value],
                        to_status=JobStatus.FAILED.value,
                        error=str(exc),
                        finished_at=finished_at,
                    )
            finally:
                with self._lock:
                    self._active_jobs.pop(job_id, None)
            return

        try:
            # Check for existing checkpoint
            raw_checkpoint = job.metadata.get("checkpoint") if (resume_from_checkpoint and job.metadata) else None
            from .checkpoint import normalize_checkpoint
            checkpoint = normalize_checkpoint(raw_checkpoint) if raw_checkpoint is not None else None

            # Instantiate RealAgentRuntime with shared persistence engines
            from ..sessions import SessionManager, SessionStore
            from ..memory import MemoryManager, MemoryStore
            sess_mgr = SessionManager(SessionStore(self.store.db.db_path))
            mem_mgr = MemoryManager(MemoryStore(self.store.db.db_path))
            runtime = RealAgentRuntime(
                workspace_dir=workspace_dir,
                session_id=job.session_id or f"sess_{job_id}",
                task_id=f"task_{job_id}",
                tenant_id=owner_id,
                provider=self.provider,
                resume_checkpoint=checkpoint,
                session_manager=sess_mgr,
                memory_manager=mem_mgr,
            )

            latest_checkpoint: Dict[str, Any] = dict(checkpoint or {})
            last_summary = (checkpoint.get("summary", "") if checkpoint else "")
            verification_status = (checkpoint.get("verification", "NOT_VERIFIED") if checkpoint else "NOT_VERIFIED")
            final_status = JobStatus.COMPLETED.value
            final_error = None

            # Execute runtime generator
            for ev in runtime.run_stream(task_prompt=job.task, cancel_event=cancel_event):
                if on_event:
                    try:
                        on_event(ev)
                    except Exception:
                        pass

                ev_type = ev.get("type")
                if ev_type == "progress":
                    chk = ev.get("checkpoint")
                    if isinstance(chk, dict):
                        latest_checkpoint = dict(chk)
                        if chk.get("summary"):
                            last_summary = chk["summary"]
                        if chk.get("verification"):
                            verification_status = chk["verification"]
                elif ev_type == "summary":
                    last_summary = ev.get("summary", "")
                    verification_status = ev.get("verification", "NOT_VERIFIED")
                elif ev_type == "error":
                    final_error = ev.get("error")
                    final_status = JobStatus.FAILED.value
                elif ev_type == "done" and ev.get("status") == "failed":
                    final_status = JobStatus.FAILED.value

                # Handle cooperative pause
                if pause_event.is_set():
                    final_status = JobStatus.PAUSED.value
                    break

                # Handle cancellation
                if cancel_event.is_set():
                    final_status = JobStatus.CANCELLED.value
                    final_error = "Execution was cancelled by operator."
                    break

            finished_at = time.time()
            result_payload = {
                "summary": last_summary,
                "verification": verification_status,
                "duration_sec": finished_at - t0,
            }

            metadata_payload = dict(job.metadata or {})
            if final_status == JobStatus.PAUSED.value:
                # Merge durable rolling checkpoint
                durable_chk = dict(latest_checkpoint)
                durable_chk["summary"] = last_summary or durable_chk.get("summary") or "Execution paused by operator."
                durable_chk["verification"] = verification_status
                durable_chk["paused_at"] = finished_at
                durable_chk["elapsed_sec"] = finished_at - t0
                try:
                    from ..sessions.manager import redact_secrets
                    if isinstance(durable_chk.get("summary"), str):
                        durable_chk["summary"] = redact_secrets(durable_chk["summary"])
                except Exception:
                    pass
                metadata_payload["checkpoint"] = durable_chk

            # Atomic terminal transition:
            # If job is COMPLETED or PAUSED, it can ONLY transition from RUNNING or PAUSE_REQUESTED.
            # If operator cancelled in the meantime, atomic_transition returns False!
            from_statuses = [JobStatus.RUNNING.value, JobStatus.PAUSE_REQUESTED.value]
            if final_status == JobStatus.CANCELLED.value:
                from_statuses.extend([JobStatus.QUEUED.value, JobStatus.PAUSED.value])
            elif final_status == JobStatus.PAUSED.value:
                from_statuses.extend([JobStatus.PAUSED.value])

            with self._lock:
                transition_ok = self.store.atomic_transition(
                    job_id=job_id,
                    owner_id=owner_id,
                    from_statuses=from_statuses,
                    to_status=final_status,
                    error=final_error,
                    finished_at=finished_at,
                    result=result_payload,
                    metadata=metadata_payload,
                )

            if not transition_ok:
                # Cancel or another terminal transition occurred concurrently; read ground truth
                fresh_job = self.store.get_job(job_id, owner_id)
                if fresh_job:
                    final_status = fresh_job.status
                    final_error = fresh_job.error

            # Update attempt record
            attempt.finished_at = finished_at
            attempt.status = final_status
            attempt.error = final_error
            attempt.result = {
                "summary": last_summary,
                "verification": verification_status,
            }
            conn = self.store.db.get_connection()
            try:
                import json
                conn.execute(
                    """
                    UPDATE job_attempts SET
                        status = ?,
                        finished_at = ?,
                        error = ?,
                        result_json = ?
                    WHERE id = ?
                    """,
                    (attempt.status, attempt.finished_at, attempt.error, json.dumps(attempt.result), attempt.id),
                )
            finally:
                conn.close()

        except Exception as exc:
            logger.exception("Background job '%s' crashed with error: %s", job_id, exc)
            try:
                with self._lock:
                    self.store.atomic_transition(
                        job_id=job_id,
                        owner_id=owner_id,
                        from_statuses=[JobStatus.RUNNING.value, JobStatus.PAUSE_REQUESTED.value],
                        to_status=JobStatus.FAILED.value,
                        error=str(exc),
                        finished_at=time.time(),
                    )
            except Exception:
                pass
        finally:
            with self._lock:
                self._active_jobs.pop(job_id, None)
