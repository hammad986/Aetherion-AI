"""
backend/execution/jobs/store.py — Persistent Multi-Tenant Store for Background Jobs and Attempts.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..persistence.db import StateDatabase
from .models import BackgroundJob, JobAttempt, JobStatus

logger = logging.getLogger("aetherion.jobs.store")


def _require_valid_owner_id(owner_id: Any) -> str:
    """Validate that owner_id is strictly a non-empty string, preventing service-layer BOLA."""
    if not isinstance(owner_id, str):
        raise KeyError("A valid non-empty string owner_id is required.")
    normalized_owner_id = owner_id.strip()
    if not normalized_owner_id:
        raise KeyError("A valid non-empty string owner_id is required.")
    return normalized_owner_id


class JobStore:
    """Multi-tenant SQLite store for background jobs and their historical attempts."""

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db = StateDatabase(db_path)

    def create_job(self, job: BackgroundJob) -> BackgroundJob:
        """Insert a newly created job into storage."""
        job.validate()
        _require_valid_owner_id(job.owner_id)
        conn = self.db.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO background_jobs (
                    id, owner_id, name, status, task, workspace_id, session_id,
                    created_at, started_at, updated_at, finished_at, error,
                    metadata_json, result_json, attempt_number, previous_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.owner_id,
                    job.name,
                    job.status,
                    job.task,
                    job.workspace_id,
                    job.session_id,
                    job.created_at,
                    job.started_at,
                    job.updated_at,
                    job.finished_at,
                    job.error,
                    json.dumps(job.metadata),
                    json.dumps(job.result) if job.result is not None else None,
                    job.attempt_number,
                    job.previous_error,
                ),
            )
            return job
        finally:
            conn.close()

    def get_job(self, job_id: str, owner_id: str) -> Optional[BackgroundJob]:
        """Fetch job by ID with tenant scoping."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            cur = conn.execute(
                "SELECT * FROM background_jobs WHERE id = ? AND owner_id = ?",
                (job_id, owner_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_job(row)
        finally:
            conn.close()

    def update_job(self, job: BackgroundJob) -> BackgroundJob:
        """Update existing background job status and state with terminal-state protection."""
        job.validate()
        job.updated_at = time.time()
        conn = self.db.get_connection()
        try:
            # Enforce database-level invariant:
            # CANCELLED jobs cannot be overwritten by COMPLETED, RUNNING, or FAILED.
            where_clause = "WHERE id = ? AND owner_id = ?"
            if job.status != JobStatus.CANCELLED.value:
                where_clause += f" AND status != '{JobStatus.CANCELLED.value}'"

            conn.execute(
                f"""
                UPDATE background_jobs SET
                    name = ?,
                    status = ?,
                    task = ?,
                    workspace_id = ?,
                    session_id = ?,
                    started_at = ?,
                    updated_at = ?,
                    finished_at = ?,
                    error = ?,
                    metadata_json = ?,
                    result_json = ?,
                    attempt_number = ?,
                    previous_error = ?
                {where_clause}
                """,
                (
                    job.name,
                    job.status,
                    job.task,
                    job.workspace_id,
                    job.session_id,
                    job.started_at,
                    job.updated_at,
                    job.finished_at,
                    job.error,
                    json.dumps(job.metadata),
                    json.dumps(job.result) if job.result is not None else None,
                    job.attempt_number,
                    job.previous_error,
                    job.id,
                    job.owner_id,
                ),
            )
            return job
        finally:
            conn.close()

    def atomic_transition(
        self,
        job_id: str,
        owner_id: str,
        from_statuses: List[str],
        to_status: str,
        error: Optional[str] = None,
        finished_at: Optional[float] = None,
        result: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Atomically transition job status only if current status is in from_statuses."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            now = time.time()
            placeholders = ", ".join(["?"] * len(from_statuses))
            query = f"""
                UPDATE background_jobs SET
                    status = ?,
                    error = COALESCE(?, error),
                    finished_at = COALESCE(?, finished_at),
                    result_json = COALESCE(?, result_json),
                    metadata_json = COALESCE(?, metadata_json),
                    updated_at = ?
                WHERE id = ? AND owner_id = ? AND status IN ({placeholders})
            """
            params = [
                to_status,
                error,
                finished_at,
                json.dumps(result) if result is not None else None,
                json.dumps(metadata) if metadata is not None else None,
                now,
                job_id,
                owner_id,
                *from_statuses,
            ]
            cur = conn.execute(query, params)
            return cur.rowcount > 0
        finally:
            conn.close()

    def retry_transition(
        self,
        job_id: str,
        owner_id: str,
    ) -> bool:
        """Atomically transition a FAILED or CANCELLED job to QUEUED, bumping attempt_number."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            now = time.time()
            query = """
                UPDATE background_jobs SET
                    status = 'QUEUED',
                    attempt_number = attempt_number + 1,
                    previous_error = error,
                    error = NULL,
                    started_at = NULL,
                    finished_at = NULL,
                    result_json = NULL,
                    updated_at = ?
                WHERE id = ? AND owner_id = ? AND status IN ('FAILED', 'CANCELLED')
            """
            cur = conn.execute(query, (now, job_id, owner_id))
            return cur.rowcount > 0
        finally:
            conn.close()

    def list_jobs(
        self,
        owner_id: str,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[BackgroundJob]:
        """List jobs owned by a tenant, ordered by creation date."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            query = "SELECT * FROM background_jobs WHERE owner_id = ?"
            params: list[Any] = [owner_id]

            if status:
                query += " AND status = ?"
                params.append(status)

            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cur = conn.execute(query, params)
            return [self._row_to_job(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def delete_job(self, job_id: str, owner_id: str) -> bool:
        """Delete a background job (only permitted if not currently RUNNING)."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            cur = conn.execute(
                f"""
                DELETE FROM background_jobs
                WHERE id = ? AND owner_id = ? AND status != '{JobStatus.RUNNING.value}'
                """,
                (job_id, owner_id),
            )
            return cur.rowcount > 0
        finally:
            conn.close()

    # ── Attempts ─────────────────────────────────────────────────────────────

    def record_attempt(self, attempt: JobAttempt) -> JobAttempt:
        """Record an execution attempt for auditing and retry history."""
        _require_valid_owner_id(attempt.owner_id)
        conn = self.db.get_connection()
        try:
            cur = conn.execute(
                """
                INSERT INTO job_attempts (
                    job_id, owner_id, attempt_number, status, started_at,
                    finished_at, error, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.job_id,
                    attempt.owner_id,
                    attempt.attempt_number,
                    attempt.status,
                    attempt.started_at,
                    attempt.finished_at,
                    attempt.error,
                    json.dumps(attempt.result) if attempt.result is not None else None,
                ),
            )
            attempt.id = cur.lastrowid
            return attempt
        finally:
            conn.close()

    def get_attempts(self, job_id: str, owner_id: str) -> List[JobAttempt]:
        """Get history of execution attempts for a job."""
        owner_id = _require_valid_owner_id(owner_id)
        conn = self.db.get_connection()
        try:
            cur = conn.execute(
                "SELECT * FROM job_attempts WHERE job_id = ? AND owner_id = ? ORDER BY attempt_number ASC",
                (job_id, owner_id),
            )
            return [self._row_to_attempt(r) for r in cur.fetchall()]
        finally:
            conn.close()

    # ── Restart Recovery & Stale Detection ────────────────────────────────────

    def find_stale_running_jobs(self) -> List[BackgroundJob]:
        """Identify jobs that were left in RUNNING state across a server restart."""
        conn = self.db.get_connection()
        try:
            cur = conn.execute(
                f"""
                SELECT * FROM background_jobs
                WHERE status IN ('{JobStatus.RUNNING.value}', '{JobStatus.PAUSE_REQUESTED.value}')
                """
            )
            return [self._row_to_job(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def recover_stale_jobs(self) -> int:
        """Mark all stale running jobs as FAILED due to interruption."""
        conn = self.db.get_connection()
        try:
            now = time.time()
            cur = conn.execute(
                f"""
                UPDATE background_jobs
                SET status = '{JobStatus.FAILED.value}',
                    finished_at = ?,
                    error = 'INTERRUPTED: Job execution was interrupted by server restart or process crash.',
                    updated_at = ?
                WHERE status IN ('{JobStatus.RUNNING.value}', '{JobStatus.PAUSE_REQUESTED.value}')
                """,
                (now, now),
            )
            return cur.rowcount
        finally:
            conn.close()

    # ── Helper Mappings ──────────────────────────────────────────────────────

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> BackgroundJob:
        return BackgroundJob(
            id=row["id"],
            owner_id=row["owner_id"],
            name=row["name"],
            status=row["status"],
            task=row["task"] or "",
            workspace_id=row["workspace_id"] or "default_workspace",
            session_id=row["session_id"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            updated_at=row["updated_at"],
            finished_at=row["finished_at"],
            error=row["error"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            attempt_number=row["attempt_number"] or 1,
            previous_error=row["previous_error"],
        )

    @staticmethod
    def _row_to_attempt(row: sqlite3.Row) -> JobAttempt:
        return JobAttempt(
            id=row["id"],
            job_id=row["job_id"],
            owner_id=row["owner_id"],
            attempt_number=row["attempt_number"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
        )
