"""
backend/execution/jobs/manager.py — Background Job Lifecycle Orchestrator.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .models import BackgroundJob, JobAttempt, JobStatus
from .runner import JobRunner
from .store import JobStore

logger = logging.getLogger("aetherion.jobs.manager")


class JobManager:
    """Orchestrates job creation, execution, retry, pause/resume, cancellation, and startup recovery."""

    def __init__(self, store: Optional[JobStore] = None, runner: Optional[JobRunner] = None):
        self.store = store or JobStore()
        self.runner = runner or JobRunner(self.store)
        # Perform startup recovery check for stale running jobs
        self.recover_stale_jobs()

    def create_job(
        self,
        owner_id: str,
        name: str,
        task: str,
        workspace_id: str = "default_workspace",
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> BackgroundJob:
        """Create a new queued background job."""
        if not isinstance(owner_id, str):
            raise KeyError("A valid non-empty string owner_id is required.")
        normalized_owner_id = owner_id.strip()
        if not normalized_owner_id:
            raise KeyError("A valid non-empty string owner_id is required.")
        now = time.time()
        job = BackgroundJob(
            id=f"job_{uuid.uuid4().hex[:12]}",
            owner_id=normalized_owner_id,
            name=name.strip(),
            task=task.strip(),
            workspace_id=workspace_id,
            session_id=session_id,
            status=JobStatus.QUEUED.value,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        return self.store.create_job(job)

    def get_job(self, job_id: str, owner_id: str) -> Optional[BackgroundJob]:
        """Fetch job by ID for tenant."""
        return self.store.get_job(job_id, owner_id)

    def list_jobs(
        self,
        owner_id: str,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[BackgroundJob]:
        """List background jobs owned by this tenant."""
        return self.store.list_jobs(owner_id=owner_id, status=status, limit=limit)

    def start_job(
        self,
        job_id: str,
        owner_id: str,
        workspace_dir: Optional[str | Path] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> BackgroundJob:
        """Begin background execution of a queued or retried job."""
        job = self.store.get_job(job_id, owner_id)
        if not job:
            raise KeyError(f"Job '{job_id}' not found.")

        ws = workspace_dir or Path(job.workspace_id)
        return self.runner.start_job(job_id, owner_id, ws, on_event=on_event)

    def pause_job(self, job_id: str, owner_id: str) -> bool:
        """Request pause of an actively executing job."""
        return self.runner.pause_job(job_id, owner_id)

    def resume_job(
        self,
        job_id: str,
        owner_id: str,
        workspace_dir: Optional[str | Path] = None,
    ) -> BackgroundJob:
        """Resume execution of a paused job."""
        job = self.store.get_job(job_id, owner_id)
        if not job:
            raise KeyError(f"Job '{job_id}' not found.")
        ws = workspace_dir or Path(job.workspace_id)
        return self.runner.resume_job(job_id, owner_id, ws)

    def cancel_job(self, job_id: str, owner_id: str) -> bool:
        """Cancel an in-progress or queued job."""
        return self.runner.cancel_job(job_id, owner_id)

    def retry_job(
        self,
        job_id: str,
        owner_id: str,
        workspace_dir: Optional[str | Path] = None,
    ) -> BackgroundJob:
        """Retry a failed or cancelled job while preserving historical attempts."""
        job = self.store.get_job(job_id, owner_id)
        if not job:
            raise KeyError(f"Job '{job_id}' not found.")
        if job.status not in (JobStatus.FAILED.value, JobStatus.CANCELLED.value):
            raise ValueError(
                f"Cannot retry job '{job_id}' in status '{job.status}'. Only FAILED or CANCELLED jobs may be retried."
            )

        # Atomic database transition from FAILED or CANCELLED to QUEUED
        ok = self.store.retry_transition(job_id, owner_id)
        if not ok:
            current = self.store.get_job(job_id, owner_id)
            curr_status = current.status if current else "UNKNOWN"
            raise RuntimeError(f"Atomic retry failed for job '{job_id}'; current status is '{curr_status}'.")

        # Re-read canonical database state to ensure 100% in-memory / DB synchronization
        fresh_job = self.store.get_job(job_id, owner_id)
        if not fresh_job:
            raise KeyError(f"Job '{job_id}' not found after retry transition.")

        # Launch new attempt if workspace_dir provided
        if workspace_dir:
            return self.start_job(job_id, owner_id, workspace_dir)
        return fresh_job

    def delete_job(self, job_id: str, owner_id: str) -> bool:
        """Delete a job. Rejects deletion if job is actively RUNNING."""
        job = self.store.get_job(job_id, owner_id)
        if not job:
            raise KeyError(f"Job '{job_id}' not found.")
        if job.status == JobStatus.RUNNING.value:
            raise ValueError(f"Cannot delete job '{job_id}' while it is actively RUNNING. Cancel it first.")
        return self.store.delete_job(job_id, owner_id)

    def get_attempts(self, job_id: str, owner_id: str) -> List[JobAttempt]:
        """Fetch audit log of previous execution attempts."""
        job = self.store.get_job(job_id, owner_id)
        if not job:
            raise KeyError(f"Job '{job_id}' not found.")
        return self.store.get_attempts(job_id, owner_id)

    def recover_stale_jobs(self) -> int:
        """Detect and recover any jobs left in RUNNING state across a crash/restart."""
        stale = self.store.find_stale_running_jobs()
        if stale:
            logger.warning("Found %d stale RUNNING jobs from prior server run. Recovering...", len(stale))
            return self.store.recover_stale_jobs()
        return 0
