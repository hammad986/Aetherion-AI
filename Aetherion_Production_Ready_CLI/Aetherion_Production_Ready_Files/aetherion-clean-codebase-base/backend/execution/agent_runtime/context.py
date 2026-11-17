"""
backend/execution/agent_runtime/context.py — Authoritative Task Execution Context and Workspace Attachment.

Provides first-class, immutable task execution context and repository/worktree
attachment primitives for verified coding agents.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from ..runner_contract import RunnerDeniedError, check_mount_safety

logger = logging.getLogger("aetherion.execution.context")

# Immutable fields once frozen
_FROZEN_ATTRIBUTES = frozenset({
    "task_id",
    "tenant_id",
    "session_id",
    "repo_id",
    "repo_root",
    "workspace_dir",
    "worktree_id",
    "branch",
    "base_ref",
    "objective",
    "execution_constraints",
    "verification_criteria",
    "execution_budget",
    "security_context",
})


class ContextImmutableError(RuntimeError):
    """Raised when an attempt is made to mutate a frozen TaskExecutionContext."""


class WorktreeRequiredError(RuntimeError):
    """Raised when an isolated worktree is required by safety policy but cannot be created."""


@dataclass
class TaskExecutionContext:
    """Authoritative, first-class context for coding agent execution."""

    task_id: str
    objective: str = "autonomous_task"
    workspace_dir: Union[Path, str] = field(default_factory=Path.cwd)
    tenant_id: str = "default_tenant"
    session_id: str = ""
    repo_id: Optional[str] = None
    repo_root: Optional[str] = None
    worktree_id: Optional[str] = None
    branch: Optional[str] = None
    base_ref: str = "HEAD"
    execution_constraints: Dict[str, Any] = field(default_factory=dict)
    verification_criteria: Dict[str, Any] = field(default_factory=dict)
    execution_budget: Dict[str, Any] = field(default_factory=dict)
    security_context: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    review_status: Optional[str] = None
    review_decision: Optional[Dict[str, Any]] = None
    promotion_metadata: Optional[Dict[str, Any]] = None
    _frozen: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.task_id or not str(self.task_id).strip():
            raise ValueError("task_id must be a non-empty string")
        if not self.tenant_id or not str(self.tenant_id).strip():
            raise ValueError("tenant_id must be a non-empty string")
        if not self.objective or not str(self.objective).strip():
            self.objective = "autonomous_task"

        # Normalize and validate workspace directory
        self.workspace_dir = Path(self.workspace_dir).resolve()
        check_mount_safety([str(self.workspace_dir)])

        if self.repo_root:
            self.repo_root = str(Path(self.repo_root).resolve())
            check_mount_safety([self.repo_root])

        if not self.session_id:
            self.session_id = f"sess_{self.task_id}"

        # Default execution constraints
        self.execution_constraints.setdefault("max_iterations", 15)
        self.execution_constraints.setdefault("max_duration_sec", 300.0)
        self.execution_constraints.setdefault("timeout_sec", 120.0)

        # Default execution budget
        self.execution_budget.setdefault("max_attempts", 3)
        self.execution_budget.setdefault("max_repair_attempts", 2)

    def freeze(self) -> None:
        """Lock the context, making critical execution parameters immutable."""
        object.__setattr__(self, "_frozen", True)

    def is_frozen(self) -> bool:
        return getattr(self, "_frozen", False)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False) and name in _FROZEN_ATTRIBUTES:
            raise ContextImmutableError(
                f"Cannot mutate attribute '{name}': TaskExecutionContext is frozen during execution."
            )
        object.__setattr__(self, name, value)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize context to a JSON-safe dictionary."""
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "repo_id": self.repo_id,
            "repo_root": self.repo_root,
            "workspace_dir": str(self.workspace_dir),
            "worktree_id": self.worktree_id,
            "branch": self.branch,
            "base_ref": self.base_ref,
            "objective": self.objective,
            "execution_constraints": dict(self.execution_constraints),
            "verification_criteria": dict(self.verification_criteria),
            "execution_budget": dict(self.execution_budget),
            "security_context": dict(self.security_context),
            "created_at": self.created_at,
            "review_status": self.review_status,
            "review_decision": dict(self.review_decision) if self.review_decision else None,
            "promotion_metadata": dict(self.promotion_metadata) if self.promotion_metadata else None,
            "is_frozen": self._frozen,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskExecutionContext:
        """Construct TaskExecutionContext from a dictionary."""
        ctx = cls(
            task_id=data["task_id"],
            objective=data["objective"],
            workspace_dir=Path(data["workspace_dir"]),
            tenant_id=data.get("tenant_id", "default_tenant"),
            session_id=data.get("session_id", ""),
            repo_id=data.get("repo_id"),
            repo_root=data.get("repo_root"),
            worktree_id=data.get("worktree_id"),
            branch=data.get("branch"),
            base_ref=data.get("base_ref", "HEAD"),
            execution_constraints=dict(data.get("execution_constraints", {})),
            verification_criteria=dict(data.get("verification_criteria", {})),
            execution_budget=dict(data.get("execution_budget", {})),
            security_context=dict(data.get("security_context", {})),
            created_at=data.get("created_at", time.time()),
            review_status=data.get("review_status"),
            review_decision=data.get("review_decision"),
            promotion_metadata=data.get("promotion_metadata"),
        )
        if data.get("is_frozen"):
            ctx.freeze()
        return ctx


def attach_execution_worktree(
    context: TaskExecutionContext,
    repo_root: Optional[Union[str, Path]] = None,
) -> TaskExecutionContext:
    """
    Attach an isolated git worktree to the task context.

    Ensures:
      - Mount safety checks against unauthorized system roots.
      - Isolated branch creation (aeth/task-worktree_id).
      - Confinement inside tenant workspace.
      - Authoritative task execution directory updated to worktree path.
    """
    if context.is_frozen():
        raise ContextImmutableError("Cannot attach worktree to a frozen TaskExecutionContext.")

    target_root = str(Path(repo_root or context.repo_root or os.getcwd()).resolve())
    check_mount_safety([target_root])

    allow_plain_workspace = bool(context.execution_constraints.get("allow_plain_workspace", False))
    error_reasons: List[str] = []

    try:
        from ..worktree import create_worktree
        wt = create_worktree(
            session_id=context.session_id,
            execution_id=context.task_id,
            task_id=context.task_id,
            tenant_id=context.tenant_id,
            base_ref=context.base_ref,
            repo_root=target_root,
        )
        context.repo_root = wt.repo_root
        context.worktree_id = wt.worktree_id
        context.branch = wt.branch
        context.workspace_dir = Path(wt.path)
        logger.info(
            "Attached isolated worktree %s (branch %s) at %s for task %s",
            wt.worktree_id,
            wt.branch,
            wt.path,
            context.task_id,
        )
        return context
    except Exception as exc:
        error_reasons.append(f"server worktree error: {exc}")
        logger.warning(
            "Could not attach git worktree via server worktree module for task %s (repo %s): %s; checking direct git",
            context.task_id,
            target_root,
            exc,
        )

    # Direct git worktree fallback for standalone/test environments
    git_dir = Path(target_root) / ".git"
    if git_dir.exists():
        import subprocess
        import uuid
        wt_id = uuid.uuid4().hex[:8]
        branch = f"aeth/{context.task_id}-{wt_id}"
        wt_dir = Path(target_root).parent / "worktrees" / f"wt_{context.task_id}_{wt_id}"
        wt_dir.parent.mkdir(parents=True, exist_ok=True)
        check_mount_safety([str(wt_dir)])

        try:
            subprocess.run(
                ["git", "worktree", "add", "-b", branch, str(wt_dir), context.base_ref],
                cwd=target_root,
                check=True,
                capture_output=True,
                text=True,
            )
            context.repo_root = target_root
            context.worktree_id = wt_id
            context.branch = branch
            context.workspace_dir = wt_dir
            logger.info("Directly attached isolated worktree %s (branch %s) at %s", wt_id, branch, wt_dir)
            return context
        except Exception as direct_exc:
            error_reasons.append(f"direct git worktree error: {direct_exc}")
            logger.warning("Direct git worktree creation failed: %s", direct_exc)
    else:
        error_reasons.append(f"Target '{target_root}' is not a git repository (.git missing)")

    # Autonomous coding execution requires an isolated worktree by default safety contract
    if not allow_plain_workspace:
        err_msg = (
            f"Autonomous coding execution requires an isolated worktree by default safety policy. "
            f"Worktree creation failed ({'; '.join(error_reasons)}). "
            f"Plain workspace fallback is prohibited unless explicitly authorized via allow_plain_workspace=True."
        )
        logger.error(err_msg)
        raise WorktreeRequiredError(err_msg)

    # Authorized plain workspace fallback
    check_mount_safety([str(context.workspace_dir)])
    logger.info("Authorized plain workspace fallback at %s for task %s", context.workspace_dir, context.task_id)
    return context


def cleanup_execution_worktree(
    context: TaskExecutionContext,
    delete_branch: bool = True,
) -> Dict[str, Any]:
    """
    Explicit cleanup of the task's isolated worktree.

    Reaps child processes, removes checkout directory, and deletes isolated branch,
    preventing failed tasks from corrupting repository state.
    """
    if not context.worktree_id:
        return {"ok": True, "cleaned": False, "reason": "no_worktree"}

    try:
        from ..worktree import discard_worktree
        res = discard_worktree(context.worktree_id, delete_branch=delete_branch)
        if res.get("ok"):
            logger.info("Cleaned up worktree %s for task %s: %s", context.worktree_id, context.task_id, res)
            return res
    except Exception as exc:
        logger.debug("Server worktree discard failed: %s; trying direct git cleanup", exc)

    # Direct git cleanup fallback
    cleaned = False
    error_msg = ""
    if context.repo_root and Path(context.repo_root).exists():
        import subprocess
        try:
            if context.workspace_dir and Path(context.workspace_dir).exists():
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(context.workspace_dir)],
                    cwd=context.repo_root,
                    capture_output=True,
                    text=True,
                )
            if delete_branch and context.branch:
                subprocess.run(
                    ["git", "branch", "-D", context.branch],
                    cwd=context.repo_root,
                    capture_output=True,
                    text=True,
                )
            cleaned = True
        except Exception as exc:
            error_msg = str(exc)

    return {
        "ok": cleaned or not error_msg,
        "cleaned": cleaned,
        "worktree_id": context.worktree_id,
        "error": error_msg,
    }


def promote_execution(
    context: TaskExecutionContext,
    target_ref: Optional[str] = None,
    approver: str = "human_operator",
    comment: str = "",
) -> Dict[str, Any]:
    """
    Promote verified task changes after explicit human approval.

    Preserves the isolated branch for inspection/merge and records promotion metadata.
    """
    ref = target_ref or context.base_ref or "HEAD"
    promotion_record = {
        "ok": True,
        "task_id": context.task_id,
        "worktree_id": context.worktree_id,
        "branch": context.branch,
        "target_ref": ref,
        "approver": approver,
        "comment": comment,
        "promoted_at": time.time(),
        "status": "ACCEPTED",
    }
    context.review_status = "ACCEPTED"
    context.review_decision = {
        "decision": "accept",
        "approver": approver,
        "comment": comment,
        "timestamp": time.time(),
    }
    context.promotion_metadata = promotion_record
    logger.info("Promoted execution for task %s (branch %s -> %s)", context.task_id, context.branch, ref)
    return promotion_record


def reject_execution(
    context: TaskExecutionContext,
    reviewer: str = "human_operator",
    reason: str = "",
) -> Dict[str, Any]:
    """
    Reject task changes after human review.

    Fully cleans up the isolated worktree directory and prunes the task branch.
    """
    cleanup_res = cleanup_execution_worktree(context, delete_branch=True)
    rejection_record = {
        "ok": True,
        "task_id": context.task_id,
        "worktree_id": context.worktree_id,
        "branch": context.branch,
        "reviewer": reviewer,
        "reason": reason,
        "rejected_at": time.time(),
        "cleanup": cleanup_res,
        "status": "REJECTED",
    }
    context.review_status = "REJECTED"
    context.review_decision = {
        "decision": "reject",
        "reviewer": reviewer,
        "reason": reason,
        "timestamp": time.time(),
    }
    context.promotion_metadata = rejection_record
    logger.info("Rejected execution for task %s; worktree and branch cleaned up", context.task_id)
    return rejection_record


def get_execution_diff(
    context: TaskExecutionContext,
    modified_files: Optional[Sequence[str]] = None,
) -> str:
    """
    Extract unified diff for task changes.

    If worktree attached, extracts git unified diff against base ref.
    Guarantees index isolation: never runs `git add -N` on the user's source checkout.
    If plain workspace, generates unified diff for modified files if baseline available.
    """
    if context.worktree_id and context.repo_root and Path(context.workspace_dir).exists():
        ws_path = Path(context.workspace_dir).resolve()
        repo_path = Path(context.repo_root).resolve()
        # Verify that workspace_dir is an isolated worktree and NOT the main repo root
        if ws_path != repo_path:
            import subprocess
            try:
                # Mark untracked files with intent-to-add in the worktree's private index
                subprocess.run(
                    ["git", "add", "-N", "."],
                    cwd=str(ws_path),
                    capture_output=True,
                    text=True,
                )
                res = subprocess.run(
                    ["git", "diff", context.base_ref],
                    cwd=str(ws_path),
                    capture_output=True,
                    text=True,
                )
                if res.stdout and res.stdout.strip():
                    return res.stdout
            except Exception as e:
                logger.debug("Worktree git diff failed: %s", e)

        try:
            from ..worktree import worktree_diff
            diff_text = worktree_diff(context.worktree_id)
            if diff_text and diff_text.strip():
                return diff_text
        except Exception as exc:
            logger.debug("Failed to extract server worktree diff for %s: %s", context.worktree_id, exc)

    # Fallback for plain workspace
    import difflib
    diff_lines: List[str] = []
    ws = Path(context.workspace_dir)
    files = list(modified_files or [])
    for f in files:
        fp = ws / f
        if fp.is_file():
            try:
                content = fp.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                file_diff = difflib.unified_diff(
                    [],
                    content,
                    fromfile=f"a/{f}",
                    tofile=f"b/{f}",
                )
                diff_lines.extend(file_diff)
            except Exception as e:
                logger.debug("Failed to diff %s: %s", f, e)

    return "".join(diff_lines)
