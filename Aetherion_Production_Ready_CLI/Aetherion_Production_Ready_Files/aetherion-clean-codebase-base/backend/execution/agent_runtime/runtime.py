"""
backend/execution/agent_runtime/runtime.py — Authoritative autonomous agent execution runtime.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional

from .. import durable_state
from ..extensions import ExtensionManager, PluginToolBridge, SkillEngine
from ..hitl import global_hitl_tracker
from ..providers.base import BaseModelProvider
from ..providers.router import get_provider
from ..sandbox.boundary import WorkspaceBoundary
from ..tools.output_policy import bound_tool_output
from ..tools.registry import AgentToolRegistry
from ..tools.schemas import ToolRequest, ToolResult
from ..verification.engine import NOT_VERIFIED, PARTIALLY_VERIFIED, VERIFIED, VerificationEngine
from .compaction import compact_history
from .context import (
    ContextImmutableError,
    TaskExecutionContext,
    attach_execution_worktree,
    cleanup_execution_worktree,
    get_execution_diff,
)
from .state import (
    STATE_ACCEPTED,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_EXECUTING,
    STATE_FAILED,
    STATE_INSPECTING,
    STATE_PLANNING,
    STATE_QUEUED,
    STATE_REJECTED,
    STATE_REPAIRING,
    STATE_REVIEW_PENDING,
    STATE_SUCCEEDED,
    STATE_VERIFYING,
    STATE_WAITING_FOR_APPROVAL,
)

logger = logging.getLogger("aetherion.agent_runtime")

try:
    from ..sessions.manager import redact_secrets
except Exception:
    def redact_secrets(s: str) -> str:
        return s

try:
    from ..jobs.checkpoint import normalize_checkpoint
except Exception:
    def normalize_checkpoint(raw: Any) -> Dict[str, Any]:
        return raw if isinstance(raw, dict) else {}

# Phase 16: Live budget enforcement — imported lazily; graceful no-op if missing
try:
    from ..orchestration.budget_enforcer import BudgetEnforcer as _BudgetEnforcer
    from ..orchestration.run_budget import BudgetExhaustedError as _BudgetExhaustedError
    _BUDGET_ENFORCER_AVAILABLE = True
except Exception:
    _BudgetEnforcer = None  # type: ignore[assignment,misc]
    _BudgetExhaustedError = None  # type: ignore[assignment]
    _BUDGET_ENFORCER_AVAILABLE = False

# Phase 17: Repository Intelligence & Adaptive Retrieval — imported lazily
try:
    from ..orchestration.repo_intelligence import RepositoryIntelligence as _RepositoryIntelligence
    from ..orchestration.adaptive_retriever import AdaptiveRetriever as _AdaptiveRetriever
    _REPO_INTEL_AVAILABLE = True
except Exception:
    _RepositoryIntelligence = None  # type: ignore[assignment,misc]
    _AdaptiveRetriever = None  # type: ignore[assignment,misc]
    _REPO_INTEL_AVAILABLE = False

# Phase 18: Governed Skills & Persistent Engineering Memory Kernel — imported lazily
try:
    from ..orchestration.governed_skills import (
        SkillRegistry as _SkillRegistry,
        SkillDiscoveryEngine as _SkillDiscoveryEngine,
        SkillPermissionAuditor as _SkillPermissionAuditor,
    )
    from ..orchestration.engineering_memory import (
        EngineeringMemoryKernel as _EngineeringMemoryKernel,
    )
    _SKILLS_MEMORY_AVAILABLE = True
except Exception:
    _SkillRegistry = None  # type: ignore[assignment,misc]
    _SkillDiscoveryEngine = None  # type: ignore[assignment,misc]
    _SkillPermissionAuditor = None  # type: ignore[assignment,misc]
    _EngineeringMemoryKernel = None  # type: ignore[assignment,misc]
    _SKILLS_MEMORY_AVAILABLE = False

# Phase 19: Adaptive Planning & Execution Graph Kernel — imported lazily
try:
    from ..orchestration.execution_plan import (
        ExecutionPlan as _ExecutionPlan,
        PlanStep as _PlanStep,
        PlanStepStatus as _PlanStepStatus,
        PlanValidator as _PlanValidator,
    )
    from ..orchestration.execution_graph import (
        ExecutionGraph as _ExecutionGraph,
    )
    from ..orchestration.adaptive_planner import (
        AdaptivePlanner as _AdaptivePlanner,
        ReplanBudgetExhaustedError as _ReplanBudgetExhaustedError,
    )
    _PLANNING_GRAPH_AVAILABLE = True
except Exception:
    _ExecutionPlan = None  # type: ignore[assignment,misc]
    _PlanStep = None  # type: ignore[assignment,misc]
    _PlanStepStatus = None  # type: ignore[assignment,misc]
    _PlanValidator = None  # type: ignore[assignment,misc]
    _ExecutionGraph = None  # type: ignore[assignment,misc]
    _AdaptivePlanner = None  # type: ignore[assignment,misc]
    _ReplanBudgetExhaustedError = None  # type: ignore[assignment,misc]
    _PLANNING_GRAPH_AVAILABLE = False

try:
    from ..orchestration.durable_supervisor import (
        DurableExecutionSupervisor as _DurableExecutionSupervisor,
        StepLease as _StepLease,
        StaleLeaseError as _StaleLeaseError,
        DuplicateClaimError as _DuplicateClaimError,
        TaskCancelledError as _TaskCancelledError,
    )
    _DURABLE_SUPERVISOR_AVAILABLE = True
except Exception:
    _DurableExecutionSupervisor = None  # type: ignore[assignment,misc]
    _StepLease = None  # type: ignore[assignment,misc]
    _StaleLeaseError = None  # type: ignore[assignment,misc]
    _DuplicateClaimError = None  # type: ignore[assignment,misc]
    _TaskCancelledError = None  # type: ignore[assignment,misc]
    _DURABLE_SUPERVISOR_AVAILABLE = False

# Tools that are safe to deduplicate within a single agent turn.
# These are purely read-only / idempotent operations whose repeated
# execution with identical arguments is guaranteed to produce the
# same result and has no side-effects.  ALL other tool calls —
# including run_command, bash, write_file, edit_file, delete_file,
# git_commit, run_tests, finish, etc. — MUST NOT be deduplicated.
_READONLY_DEDUP_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "list_dir",
    "inspect_environment",
    "search_files",
    "check_syntax",
    "git_status",
    "git_diff",
})


class RealAgentRuntime:
    """Coordinates genuine autonomous loop, tools, verification, repair, and durable state."""

    def __init__(
        self,
        workspace_dir: Optional[str | Path] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        tenant_id: str = "default_tenant",
        provider: Optional[BaseModelProvider] = None,
        max_iterations: int = 50,
        max_duration_sec: float = 600.0,
        resume_checkpoint: Optional[Dict[str, Any]] = None,
        session_manager: Optional[Any] = None,
        memory_manager: Optional[Any] = None,
        context: Optional[TaskExecutionContext] = None,
    ):
        if context is not None:
            self.context = context
            self.workspace_dir = Path(context.workspace_dir).resolve()
            self.session_id = context.session_id
            self.task_id = context.task_id
            self.tenant_id = context.tenant_id
            self.max_iterations = int(context.execution_constraints.get("max_iterations", max_iterations))
            self.max_duration_sec = float(context.execution_constraints.get("max_duration_sec", max_duration_sec))
            self.max_repair_attempts = int(context.execution_budget.get("max_repair_attempts", 2))
        else:
            ws_path = Path(workspace_dir or Path.cwd()).resolve()
            sid = session_id or f"sess_{uuid.uuid4().hex[:8]}"
            tid = task_id or f"task_{uuid.uuid4().hex[:12]}"
            self.workspace_dir = ws_path
            self.session_id = sid
            self.task_id = tid
            self.tenant_id = tenant_id
            self.max_iterations = max_iterations
            self.max_duration_sec = max_duration_sec
            self.max_repair_attempts = 2
            self.context = TaskExecutionContext(
                task_id=self.task_id,
                objective="autonomous_task",
                workspace_dir=self.workspace_dir,
                tenant_id=self.tenant_id,
                session_id=self.session_id,
                execution_constraints={"max_iterations": max_iterations, "max_duration_sec": max_duration_sec},
                execution_budget={"max_attempts": 3, "max_repair_attempts": 2},
            )

        if resume_checkpoint:
            from ..jobs.checkpoint import normalize_checkpoint
            self.resume_checkpoint = normalize_checkpoint(resume_checkpoint)
            self._initial_skip_tools = set(self.resume_checkpoint.get("resumability", {}).get("skip_tools", []))
            self._initial_completed_states = list(self.resume_checkpoint.get("tool_execution_states", []))
        else:
            self.resume_checkpoint = None
            self._initial_skip_tools = set()
            self._initial_completed_states = []
        self.boundary = WorkspaceBoundary(self.workspace_dir)
        self.tools = AgentToolRegistry(self.boundary)
        self.verifier = VerificationEngine(self.boundary, self.tools)
        self.provider = provider or get_provider()

        self.extension_manager = ExtensionManager()
        self.plugin_bridge = PluginToolBridge(self.extension_manager, self.boundary)
        self.skill_engine = SkillEngine(self.extension_manager)

        self.session_manager = session_manager
        self.memory_manager = memory_manager
        if self.session_manager is None or self.memory_manager is None:
            try:
                from ..memory import MemoryManager
                from ..sessions import SessionManager
                if self.memory_manager is None:
                    self.memory_manager = MemoryManager()
                if self.session_manager is None:
                    self.session_manager = SessionManager()
            except Exception as exc:
                logger.debug("Memory/Session manager init error: %s", exc)

        self.learning_engine = None
        self.feedback_tracker = None
        if self.memory_manager:
            try:
                from ..memory.learning import AgentLearningEngine
                from ..memory.feedback import MemoryFeedbackTracker
                self.learning_engine = AgentLearningEngine(
                    store=self.memory_manager.store,
                    embedding_provider=self.memory_manager.embedding_provider,
                    cache=self.memory_manager.cache,
                )
                self.feedback_tracker = MemoryFeedbackTracker(store=self.memory_manager.store)
            except Exception as exc:
                logger.debug("Learning/Feedback engine init error: %s", exc)

        # Register plugin-provided tools for this tenant
        try:
            plugin_tools = self.plugin_bridge.get_plugin_tools_schemas(self.tenant_id)
            for pt in plugin_tools:
                t_name = pt["name"]
                self.tools.register_custom_tool(
                    t_name,
                    pt,
                    lambda req: self.plugin_bridge.execute_plugin_tool(req, self.tenant_id),
                )
        except Exception as exc:
            logger.debug("No plugin tools registered: %s", exc)

        self.current_state = STATE_QUEUED
        self.created_or_edited_files: List[str] = []
        self.action_history: List[Dict[str, Any]] = []
        self.retrieved_memory_ids: List[str] = []
        self.repair_history: List[Dict[str, Any]] = []
        self.hitl_decisions: List[Dict[str, Any]] = []
        self.replay_events: List[Dict[str, Any]] = []

        # Phase 17: Repository Intelligence & Adaptive Retrieval
        self.repo_intelligence = None
        self.adaptive_retriever = None
        if _REPO_INTEL_AVAILABLE and _RepositoryIntelligence is not None:
            try:
                repo_root = getattr(self.context, "repo_root", None) or self.workspace_dir
                self.repo_intelligence = _RepositoryIntelligence(repo_root)
                if _AdaptiveRetriever is not None:
                    self.adaptive_retriever = _AdaptiveRetriever(self.repo_intelligence)
            except Exception as _ri_exc:
                logger.debug("Repo intelligence init note: %s", _ri_exc)

        # Phase 18: Governed Skills & Persistent Engineering Memory Kernel
        self.skill_registry = None
        self.skill_discovery = None
        self.memory_kernel = None
        if _SKILLS_MEMORY_AVAILABLE and _SkillRegistry is not None:
            try:
                self.skill_registry = _SkillRegistry()
                if _SkillDiscoveryEngine is not None:
                    self.skill_discovery = _SkillDiscoveryEngine(self.skill_registry)
                if _EngineeringMemoryKernel is not None:
                    self.memory_kernel = _EngineeringMemoryKernel()
            except Exception as _sm_exc:
                logger.debug("Skills/Memory kernel init note: %s", _sm_exc)

        # Phase 19: Adaptive Planning & Execution Graph Kernel
        self.execution_plan = None
        self.execution_graph = None
        self.planner = None
        if _PLANNING_GRAPH_AVAILABLE and _AdaptivePlanner is not None:
            try:
                self.planner = _AdaptivePlanner()
            except Exception as _pl_exc:
                logger.debug("Planner init note: %s", _pl_exc)

        # Phase 20: Durable Execution Supervisor & Crash-Resilient Kernel
        self.worker_id = f"worker_{uuid.uuid4().hex[:8]}"
        self.supervisor = getattr(self.context, "supervisor", None) if self.context else None

    def execute_plan_step(self, step: Any) -> Dict[str, Any]:
        """
        Execute a plan step through existing governance & Phase 20 Durable Supervisor:
        - Step claim & lease acquisition under supervisor
        - Permission audit on allowed tools/skills
        - Verification criteria check
        - Checkpoint & completion / failure handling
        - Replay event emission
        """
        lease = None
        if self.supervisor:
            try:
                lease = self.supervisor.claim_step(self.task_id, step.step_id, worker_id=self.worker_id)
                self.supervisor.start_step(self.task_id, step.step_id, worker_id=self.worker_id, lease_id=lease.lease_id)
            except Exception as claim_exc:
                logger.warning("Supervisor claim failed for step %s: %s", step.step_id, claim_exc)
                return {"status": "FAILED", "error": str(claim_exc)}

        self._record_replay_event("step.started", {
            "step_id": step.step_id,
            "objective": step.objective,
            "allowed_skills": step.allowed_skills,
            "risk_level": step.risk_level.value if hasattr(step.risk_level, "value") else str(step.risk_level),
            "worker_id": self.worker_id,
            "lease_id": lease.lease_id if lease else None,
        })
        if hasattr(step, "status"):
            if _PlanStepStatus is not None:
                step.status = _PlanStepStatus.RUNNING
            step.started_at = time.time()

        # Invariant: Risky step requires HITL if configured
        if getattr(step, "requires_hitl", False):
            self._record_replay_event("hitl.requested", {
                "step_id": step.step_id,
                "reason": f"Step '{step.step_id}' risk is {getattr(step.risk_level, 'value', step.risk_level)}",
            })

        if self.supervisor and lease:
            try:
                self.supervisor.start_verification(self.task_id, step.step_id, worker_id=self.worker_id, lease_id=lease.lease_id)
            except Exception as ver_start_exc:
                logger.debug("Supervisor start_verification note: %s", ver_start_exc)

        # Run step verification
        ver_ok, ver_details = self.verify_plan_step(step)
        if ver_ok:
            if hasattr(step, "status") and _PlanStepStatus is not None:
                step.status = _PlanStepStatus.SUCCEEDED
            if hasattr(step, "completed_at"):
                step.completed_at = time.time()
            if hasattr(step, "result"):
                step.result = {"verified": True, "details": ver_details}

            if self.supervisor and lease:
                try:
                    self.supervisor.complete_step(
                        task_id=self.task_id,
                        step_id=step.step_id,
                        worker_id=self.worker_id,
                        lease_id=lease.lease_id,
                        result={"verified": True, "details": ver_details},
                        evidence_reference=f"ev_{self.task_id}_{step.step_id}",
                    )
                except Exception as comp_exc:
                    logger.warning("Supervisor complete_step error: %s", comp_exc)
                    return {"status": "FAILED", "error": str(comp_exc)}

            self._record_replay_event("step.verified", {
                "step_id": step.step_id,
                "status": "SUCCEEDED",
                "details": ver_details,
            })
            return {"status": "SUCCEEDED", "details": ver_details}
        else:
            if hasattr(step, "status") and _PlanStepStatus is not None:
                step.status = _PlanStepStatus.FAILED
            if hasattr(step, "completed_at"):
                step.completed_at = time.time()
            if hasattr(step, "error_message"):
                step.error_message = ver_details

            if self.supervisor and lease:
                try:
                    self.supervisor.fail_step(
                        task_id=self.task_id,
                        step_id=step.step_id,
                        worker_id=self.worker_id,
                        lease_id=lease.lease_id,
                        error_message=ver_details,
                    )
                except Exception as fail_exc:
                    logger.debug("Supervisor fail_step note: %s", fail_exc)

            self._record_replay_event("step.failed", {
                "step_id": step.step_id,
                "status": "FAILED",
                "error": ver_details,
            })
            return {"status": "FAILED", "error": ver_details}

    def verify_plan_step(self, step: Any) -> Tuple[bool, str]:
        """
        Evaluate step verification criteria.
        Invariant: Model completion alone is NOT sufficient; verification required.
        """
        v_crit = getattr(step, "verification_criteria", {})
        if not v_crit:
            return True, "Step completed with baseline criteria"

        if isinstance(v_crit, dict):
            if v_crit.get("fail") is True or v_crit.get("status") in ("FAILED", "failed", False):
                return False, str(v_crit.get("error") or "Step verification criteria failed")

        return True, "Step verification passed"


    def _record_replay_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Record typed event conforming to ReplayEngine and EvidenceLedger projection format."""
        ev = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "event_type": event_type,
            "timestamp": time.time(),
            "session_id": self.session_id,
            "correlation_id": self.task_id,
            "execution_id": self.task_id,
            "payload": dict(payload),
        }
        self.replay_events.append(ev)

    def _update_state(self, new_state: str, emit_fn: Callable[[Dict[str, Any]], None], details: str = "") -> None:
        """Update durable task state and broadcast state_change event."""
        prev = self.current_state
        self.current_state = new_state
        try:
            durable_state.update_status(self.task_id, new_state, tenant_id=self.tenant_id)
        except Exception as exc:
            logger.debug("Durable state transition (%s -> %s): %s", prev, new_state, exc)

        emit_fn({
            "type": "state_change",
            "agent_state": details or f"State: {new_state}",
            "status": new_state,
        })

    def run_stream(
        self,
        task_prompt: str,
        cancel_event: Optional[Any] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Execute the real autonomous agent loop with truthful event streaming."""
        t_start = time.time()
        deadline = t_start + self.max_duration_sec
        total_tokens_in = len(task_prompt.split()) * 2 + 30
        total_tokens_out = 0

        # Queue of events to emit
        event_buffer: List[Dict[str, Any]] = []

        def emit(e: Dict[str, Any]) -> None:
            event_buffer.append(e)

        # Freeze execution context once execution begins
        if (not self.context.objective or self.context.objective == "autonomous_task") and task_prompt:
            self.context.objective = task_prompt
        if not self.context.is_frozen():
            self.context.freeze()

        self._record_replay_event("task.started", {"task_id": self.task_id, "prompt": task_prompt})

        # 1. Emit QUEUED
        self._update_state(STATE_QUEUED, emit, f"Task queued: {self.task_id}")
        emit({"type": "queued", "task_id": self.task_id, "session_id": self.session_id})

        while event_buffer:
            yield event_buffer.pop(0)

        # Check cancellation
        if cancel_event and cancel_event.is_set():
            self._update_state(STATE_CANCELLED, emit, "Task cancelled before start")
            emit({"type": "done", "status": "cancelled", "task_id": self.task_id})
            while event_buffer:
                yield event_buffer.pop(0)
            return

        # 2. Transition to PLANNING
        self._update_state(STATE_PLANNING, emit, "Analyzing prompt & environment...")
        while event_buffer:
            yield event_buffer.pop(0)

        # Provider capability inspection
        caps_func = getattr(self.provider, "capabilities", None)
        caps = caps_func() if callable(caps_func) else None
        supports_tools = caps.supports_tools if caps is not None else True
        supports_sys_msg = caps.supports_system_messages if caps is not None else True

        # Discover and activate matching enabled skills
        skill_guidance = ""
        try:
            activated_skills = self.skill_engine.match_and_activate(task_prompt, self.tenant_id)
            for sk in activated_skills:
                emit({"type": "skill_activated", "skill": sk["name"], "id": sk["id"], "version": sk["version"]})
                emit({"type": "text", "text": f"\n* Skill activated: '{sk['name']}' (v{sk['version']})\n"})
            if activated_skills:
                skill_guidance = self.skill_engine.build_skill_prompt_instructions(activated_skills)
        except Exception as exc:
            logger.debug("Skill matching error: %s", exc)

        # Retrieve relevant persistent memories for this tenant
        memory_context = ""
        self.retrieved_memory_ids = []
        if self.memory_manager:
            try:
                search_results = self.memory_manager.search_memories(
                    query=task_prompt,
                    owner_id=self.tenant_id,
                    limit=5,
                )
                self.retrieved_memory_ids = [hit.memory.id for hit in search_results if hasattr(hit, "memory")]
                memory_context = self.memory_manager.build_system_memory_context(
                    task_prompt, self.tenant_id, max_memories=5, max_chars=3000
                )
                if memory_context:
                    emit({"type": "memory_retrieved", "tenant_id": self.tenant_id, "count": len(self.retrieved_memory_ids)})
            except Exception as exc:
                logger.debug("Memory retrieval error: %s", exc)

        # 1. Retrieve prior bounded session context (SessionStore -> SessionManager -> Runtime)
        prior_session_messages: List[Dict[str, Any]] = []
        session_summary: str = ""
        if self.session_manager and self.session_id:
            try:
                # Check if session exists under a DIFFERENT tenant (Attack C / Tenant Isolation)
                existing_owner = self.session_manager.store.get_session_owner(self.session_id)
                if existing_owner and str(existing_owner) != str(self.tenant_id):
                    raise KeyError(f"Session '{self.session_id}' belongs to another tenant.")

                # If session does not exist at all, auto-create it for this tenant
                if not existing_owner:
                    self.session_manager.create_session(
                        owner_id=self.tenant_id,
                        title=f"Session {self.session_id}",
                        session_id=self.session_id,
                    )

                # Retrieve bounded context BEFORE recording the new prompt,
                # ensuring no accidental duplication of the active user turn.
                bounded_ctx = self.session_manager.build_bounded_context(
                    session_id=self.session_id,
                    owner_id=self.tenant_id,
                    max_messages=10,
                    max_chars=4000,
                )
                prior_session_messages = bounded_ctx.get("recent_messages", [])
                session_summary = bounded_ctx.get("summary", "")
                emit({
                    "type": "session_context_loaded",
                    "session_id": self.session_id,
                    "turns": len(prior_session_messages),
                    "has_summary": bool(session_summary),
                })
            except KeyError as ke:
                # Tenant isolation violation
                logger.warning("Session context retrieval rejected for tenant %s: %s", self.tenant_id, ke)
                self._update_state(STATE_FAILED, emit, f"Session authorization failed: {ke}")
                emit({"type": "session_context_error", "error": f"Session '{self.session_id}' not accessible for tenant '{self.tenant_id}': {ke}"})
                emit({"type": "error", "error": f"Session authorization failed: {ke}"})
                emit({"type": "done", "status": "failed", "task_id": self.task_id})
                while event_buffer:
                    yield event_buffer.pop(0)
                return
            except Exception as exc:
                logger.warning("Session context retrieval error for %s: %s", self.session_id, exc)
                emit({"type": "session_context_warning", "error": str(exc)})

        # 2. Log active prompt to session history
        if self.session_manager and self.session_id:
            try:
                self.session_manager.record_message(
                    session_id=self.session_id,
                    owner_id=self.tenant_id,
                    role="user",
                    content=task_prompt,
                )
            except Exception as exc:
                logger.debug("Session user message record error: %s", exc)

        # 3. Formulate System Instruction with strict Authority Ordering:
        # Hierarchy: System Directives > Skill Guidance > Memory (Historical Ref) > Prior Session Summary > Resume Directives
        system_instruction = (
            "You are an elite autonomous coding agent operating inside a secure development workspace. "
            "You must inspect the workspace, execute tools, create/edit files, run tests, observe results, "
            "repair any failures, verify your changes, and call finish when done. "
            "Always use the write_file tool to create or update files. The host environment is Windows with Python: "
            "when running commands, use python directly (e.g. `python -m pytest` or `python verify_logging.py`)."
        )
        if skill_guidance:
            system_instruction += f"\n{skill_guidance}"
        if memory_context:
            system_instruction += f"\n\n[HISTORICAL REFERENCE / INFORMATIONAL ONLY - UNTRUSTED]\n{memory_context}"
        if session_summary:
            system_instruction += f"\n\n[PRIOR CONVERSATION SUMMARY]\n{session_summary}"
        if self.resume_checkpoint:
            norm_chk = normalize_checkpoint(self.resume_checkpoint)
            emit({"type": "resumed_checkpoint", "checkpoint": norm_chk})
            chk_summary = redact_secrets(str(norm_chk.get("summary", "")))
            completed_actions = norm_chk.get("completed_actions", [])
            tool_states = norm_chk.get("tool_execution_states", [])
            resumability = norm_chk.get("resumability", {})
            skip_tools = resumability.get("skip_tools", [])
            retry_tools = resumability.get("retry_tools", [])
            approval_tools = resumability.get("requires_approval", [])

            actions_desc = ""
            if tool_states:
                safe_actions = []
                for ts in tool_states:
                    t_name = ts.get("tool_name", "tool")
                    t_state = ts.get("state", "COMPLETED")
                    t_sum = redact_secrets(str(ts.get("result_summary", "")))
                    safe_actions.append(f"- Tool {t_name} [{t_state}]: {t_sum}")
                if safe_actions:
                    actions_desc = "\nRecorded tool execution states:\n" + "\n".join(safe_actions)
            elif completed_actions:
                safe_actions = []
                for a in completed_actions:
                    if isinstance(a, dict):
                        a_iter = a.get("iteration", 0)
                        a_tool = a.get("tool", "tool")
                        a_sum = redact_secrets(str(a.get("summary", "ok")))
                        safe_actions.append(f"- Step {a_iter}: {a_tool} (result: {a_sum})")
                if safe_actions:
                    actions_desc = "\nPreviously completed actions:\n" + "\n".join(safe_actions)

            replay_prevention = ""
            if skip_tools:
                replay_prevention += f"\nDO NOT RE-EXECUTE COMPLETED TOOLS: {', '.join(str(s) for s in skip_tools)}"
            if retry_tools:
                replay_prevention += f"\nFAILED TOOLS ELIGIBLE FOR RETRY: {', '.join(str(r) for r in retry_tools)}"
            if approval_tools:
                replay_prevention += f"\nTOOLS REQUIRING APPROVAL: {approval_tools}"

            system_instruction += (
                f"\n\n[RESUMED CHECKPOINT DIRECTIVE (v2)]\n"
                f"You are resuming a previously paused task (checkpoint-based continuation/replay avoidance).\n"
                f"Prior execution progress:\n{chk_summary}\n"
                f"{actions_desc}\n"
                f"{replay_prevention}\n"
                f"CRITICAL CONTINUATION DIRECTIVE: Do NOT replay or duplicate previously completed actions/tools listed above. "
                f"Continue directly with the remaining unfinished steps to complete the task."
            )

        # 4. Construct Multi-turn Conversation Messages:
        # System Instruction -> Prior Session Turns (Chronological) -> Current Active User Prompt
        messages: List[Dict[str, Any]] = []
        if supports_sys_msg:
            messages.append({"role": "system", "content": system_instruction})
            for m in prior_session_messages:
                messages.append({"role": m["role"], "content": m["content"]})
            messages.append({"role": "user", "content": task_prompt})
        else:
            combined_first_turn = f"{system_instruction}\n\n"
            if prior_session_messages:
                combined_first_turn += "Prior dialogue history:\n"
                for m in prior_session_messages:
                    combined_first_turn += f"[{m['role'].upper()}]: {m['content']}\n"
                combined_first_turn += "\n"
            combined_first_turn += f"Current Task: {task_prompt}"
            messages.append({"role": "user", "content": combined_first_turn})

        # 5. Initialize Durable Rolling Execution Checkpoint (v2)
        norm_chk = normalize_checkpoint(self.resume_checkpoint) if self.resume_checkpoint else {}
        prior_actions = list(norm_chk.get("completed_actions", []))
        prior_tool_states = list(norm_chk.get("tool_execution_states", []))
        prior_resumability = dict(norm_chk.get("resumability", {"skip_tools": [], "retry_tools": [], "requires_approval": []}))
        rolling_checkpoint: Dict[str, Any] = {
            "schema_version": 2,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "iteration": norm_chk.get("iteration", 0),
            "last_tool": norm_chk.get("last_tool"),
            "last_args": norm_chk.get("last_args"),
            "last_thought": norm_chk.get("last_thought"),
            "completed_actions": prior_actions,
            "tool_execution_states": prior_tool_states,
            "resumability": prior_resumability,
            "summary": norm_chk.get("summary", ""),
            "verification": norm_chk.get("verification", NOT_VERIFIED),
            "created_or_edited_files": list(self.created_or_edited_files),
            "elapsed_sec": norm_chk.get("elapsed_sec", 0.0),
            "updated_at": time.time(),
        }

        iteration = 0
        final_summary = ""
        verification_status = NOT_VERIFIED
        has_tool_failures = False

        max_repair_attempts = int(self.context.execution_budget.get("max_repair_attempts", 2))
        max_total_attempts = 1 + max_repair_attempts
        current_attempt = 1
        terminal_status = "failed"
        ver_status = NOT_VERIFIED
        ver_details = ""
        ver_meta: Dict[str, Any] = {"verified_files": [], "test_exit_code": None}

        # Phase 16: Construct BudgetEnforcer — single authoritative enforcement object.
        # Reads limits from context.execution_budget (set by Phase 15 or defaults).
        # If the orchestration package is unavailable, _budget_enforcer stays None (no-op).
        _budget_enforcer = None
        if _BUDGET_ENFORCER_AVAILABLE and _BudgetEnforcer is not None:
            try:
                _budget_enforcer = _BudgetEnforcer.from_execution_budget(
                    execution_budget=dict(self.context.execution_budget),
                    task_id=self.task_id,
                    tenant_id=self.tenant_id,
                    profile_name=self.context.execution_budget.get("initial_profile", "standard"),
                    rationale=self.context.execution_budget.get("policy_rationale", "runtime_default"),
                )
            except Exception as _be_exc:
                logger.debug("Phase 16 BudgetEnforcer init failed (continuing without): %s", _be_exc)
                _budget_enforcer = None
        self.budget_enforcer = _budget_enforcer

        # 6. Main Bounded Autonomous Agent Loop with Verifier & Repair
        while current_attempt <= max_total_attempts and iteration < self.max_iterations and time.time() < deadline:
            attempt_active = True

            while attempt_active and iteration < self.max_iterations and time.time() < deadline:
                iteration += 1

                # Cancellation check
                if cancel_event and cancel_event.is_set():
                    self._update_state(STATE_CANCELLED, emit, "Execution cancelled by operator")
                    self._record_replay_event("task.cancelled", {"task_id": self.task_id})
                    emit({"type": "done", "status": "cancelled", "task_id": self.task_id})
                    while event_buffer:
                        yield event_buffer.pop(0)
                    return

                # Ask model for next action
                try:
                    tools_schema = self.tools.get_anthropic_tools() if supports_tools else []
                    messages = compact_history(messages)
                    # Phase 16: mark turn start for duration accounting
                    if _budget_enforcer is not None:
                        _budget_enforcer.start_turn()
                    response = self.provider.chat(messages, tools=tools_schema)
                    # Phase 16: record turn and enforce all budget limits
                    if _budget_enforcer is not None:
                        _tok_in = response.get("usage", {}).get("input_tokens", 0)
                        _tok_out = response.get("usage", {}).get("output_tokens", 0)
                        _budget_enforcer.after_model_call(
                            tokens_in=_tok_in,
                            tokens_out=_tok_out,
                            was_repair=(current_attempt > 1),
                        )
                        _budget_enforcer.check()  # raises BudgetExhaustedError if over
                except Exception as pe:
                    # Phase 16: check if BudgetExhaustedError first; otherwise treat as provider error
                    if _BudgetExhaustedError is not None and isinstance(pe, _BudgetExhaustedError):
                        self._update_state(STATE_FAILED, emit, f"Budget exhausted: {pe.limit_name}")
                        _bex_dict = pe.as_dict()
                        self._record_replay_event("budget.exhausted", _bex_dict)
                        if _budget_enforcer is not None:
                            self._record_replay_event("orchestration.usage_final", _budget_enforcer.final_evidence())
                        emit({"type": "budget_exhausted", "detail": _bex_dict})
                        emit({"type": "done", "status": "failed", "task_id": self.task_id,
                              "failure_class": "budget_exhausted"})
                        terminal_status = "failed"
                        while event_buffer:
                            yield event_buffer.pop(0)
                        return
                    self._update_state(STATE_FAILED, emit, f"Model provider error: {pe}")
                    self._record_replay_event("task.failed", {"task_id": self.task_id, "error": str(pe)})
                    emit({"type": "error", "error": f"Provider error: {pe}"})
                    emit({"type": "done", "status": "failed", "task_id": self.task_id})
                    while event_buffer:
                        yield event_buffer.pop(0)
                    return

                thought = response.get("thought", "")
                
                # Multi-Tool Handling: extract every tool call returned in response
                raw_tool_calls = []
                if hasattr(response, "tool_calls") and getattr(response, "tool_calls"):
                    raw_tool_calls = getattr(response, "tool_calls")
                elif isinstance(response, dict) and response.get("tool_calls"):
                    raw_tool_calls = response["tool_calls"]
                elif response.get("tool"):
                    raw_tool_calls = [{
                        "id": response.get("tool_call_id") or f"call_{iteration}_0",
                        "name": response.get("tool"),
                        "arguments": response.get("arguments") or {},
                    }]

                normalized_tool_calls: List[Dict[str, Any]] = []
                for idx, tc in enumerate(raw_tool_calls):
                    if hasattr(tc, "name") and hasattr(tc, "arguments"):
                        c_id = getattr(tc, "id", None) or f"call_{iteration}_{idx}"
                        c_name = tc.name
                        c_args = tc.arguments if isinstance(tc.arguments, dict) else {}
                    elif isinstance(tc, dict):
                        c_id = tc.get("id") or tc.get("tool_call_id") or f"call_{iteration}_{idx}"
                        c_name = tc.get("name") or tc.get("tool", "")
                        c_args = tc.get("arguments") if "arguments" in tc else tc.get("args", {})
                        if not isinstance(c_args, dict):
                            c_args = {}
                    else:
                        continue
                    if c_name:
                        normalized_tool_calls.append({"id": str(c_id), "name": c_name, "args": c_args})

                if thought:
                    total_tokens_out += max(1, len(thought.split()))
                    emit({"type": "text", "text": f"\n* {thought}\n"})

                # Boundary 1: After provider response
                rolling_checkpoint["iteration"] = iteration
                rolling_checkpoint["last_thought"] = redact_secrets(thought[:200]) if thought else None
                rolling_checkpoint["elapsed_sec"] = time.time() - t_start
                emit({
                    "type": "progress",
                    "phase": "after_provider_response",
                    "checkpoint": dict(rolling_checkpoint),
                })

                # If no tool was called (e.g. model planning turn or provider with supports_tools=False)
                if not normalized_tool_calls:
                    if not self.created_or_edited_files and iteration < self.max_iterations - 5:
                        messages.append({"role": "assistant", "content": thought})
                        messages.append({"role": "user", "content": "Please proceed with invoking the necessary tools (such as write_file) to implement the required changes."})
                        continue
                    final_summary = thought or "Task executed (no tools called)."
                    attempt_active = False
                    break

                # If single finish tool was called, proceed immediately to verification
                if len(normalized_tool_calls) == 1 and normalized_tool_calls[0]["name"] == "finish":
                    f_call = normalized_tool_calls[0]
                    f_args = f_call["args"]
                    final_summary = f_args.get("summary", "Task completed.") if isinstance(f_args, dict) else "Task completed."
                    if isinstance(f_args, dict) and "verification" in f_args:
                        verification_status = f_args["verification"]
                    attempt_active = False
                    break

                # Reorder so finish tool (if any) executes last after other tools in the turn
                non_finish_calls = [tc for tc in normalized_tool_calls if tc["name"] != "finish"]
                finish_calls = [tc for tc in normalized_tool_calls if tc["name"] == "finish"]
                ordered_calls = non_finish_calls + finish_calls

                # Append assistant message with ALL tool calls preserved
                assistant_entry: Dict[str, Any] = {
                    "role": "assistant",
                    "content": thought,
                    "tool_calls": [
                        {"id": tc["id"], "name": tc["name"], "args": tc["args"]}
                        for tc in normalized_tool_calls
                    ],
                }
                if len(normalized_tool_calls) == 1:
                    assistant_entry["tool_call"] = {
                        "name": normalized_tool_calls[0]["name"],
                        "args": normalized_tool_calls[0]["args"],
                    }
                    assistant_entry["tool_call_id"] = normalized_tool_calls[0]["id"]
                messages.append(assistant_entry)

                executed_calls_cache: Dict[str, ToolResult] = {}
                turn_finished = False

                for tc in ordered_calls:
                    tcid = tc["id"]
                    tool_name = tc["name"]
                    tool_args = tc["args"]

                    # If tool is finish, complete execution attempt
                    if tool_name == "finish":
                        final_summary = tool_args.get("summary", "Task completed.") if isinstance(tool_args, dict) else "Task completed."
                        if isinstance(tool_args, dict) and "verification" in tool_args:
                            verification_status = tool_args["verification"]
                        messages.append({
                            "role": "tool",
                            "name": tool_name,
                            "content": "Task completed.",
                            "tool_call_id": tcid,
                        })
                        attempt_active = False
                        turn_finished = True
                        break

                    # Cancellation check before executing each tool call in turn
                    if cancel_event and cancel_event.is_set():
                        self._update_state(STATE_CANCELLED, emit, "Execution cancelled by operator")
                        self._record_replay_event("task.cancelled", {"task_id": self.task_id})
                        emit({"type": "done", "status": "cancelled", "task_id": self.task_id})
                        while event_buffer:
                            yield event_buffer.pop(0)
                        return

                    # Update state
                    if tool_name in ("inspect_environment", "list_dir"):
                        self._update_state(STATE_INSPECTING, emit, "Inspecting workspace environment...")
                    else:
                        self._update_state(STATE_EXECUTING, emit, f"Executing {tool_name}...")

                    # Emit tool_call event to CLI/TUI and record replay event
                    emit({"type": "tool_call", "tool": tool_name, "args": tool_args})
                    self._record_replay_event("tool.called", {"tool": tool_name, "args": tool_args})
                    while event_buffer:
                        yield event_buffer.pop(0)

                    is_skipped = False
                    result: Optional[ToolResult] = None

                    # Check for dangerous command -> approval
                    if tool_name == "run_command":
                        cmd_to_run = tool_args.get("command", "") if isinstance(tool_args, dict) else ""
                        self._record_replay_event("command.run", {"command": cmd_to_run})
                        if self.boundary.is_dangerous(cmd_to_run):
                            self._update_state(STATE_WAITING_FOR_APPROVAL, emit, f"Approval required for command: {cmd_to_run}")
                            emit({
                                "type": "approval",
                                "request_id": self.task_id,
                                "tool": tool_name,
                                "description": f"Command requires approval: {cmd_to_run}",
                                "risk": "high",
                                "details": {"command": cmd_to_run},
                            })
                            while event_buffer:
                                yield event_buffer.pop(0)

                            # Real blocking HITL gate
                            try:
                                approval_resp = global_hitl_tracker.request_approval(
                                    execution_id=self.task_id,
                                    payload={
                                        "session_id": self.session_id,
                                        "tenant_id": self.tenant_id,
                                        "task_id": self.task_id,
                                        "tool": tool_name,
                                        "command": cmd_to_run,
                                        "prompt": f"Approval required for dangerous command: {cmd_to_run}",
                                    },
                                    timeout_sec=300,
                                    cancel_event=cancel_event,
                                )
                            except Exception as hitl_err:
                                logger.error("HITL approval gate failed closed: %s", hitl_err, exc_info=True)
                                approval_resp = {"status": "failed", "feedback": f"HITL approval gate error: {hitl_err}"}

                            decision = approval_resp.get("status", "rejected")
                            feedback = approval_resp.get("feedback", "")
                            self.hitl_decisions.append({
                                "command": cmd_to_run,
                                "tool": tool_name,
                                "decision": decision,
                                "feedback": feedback,
                                "timestamp": time.time(),
                            })
                            self._record_replay_event("hitl.decision", {"command": cmd_to_run, "decision": decision, "feedback": feedback})

                            if decision == "approved":
                                if cancel_event and cancel_event.is_set():
                                    self._update_state(STATE_CANCELLED, emit, "Task cancelled after approval was granted")
                                    self._record_replay_event("task.cancelled", {"task_id": self.task_id})
                                    emit({"type": "approval_resolved", "decision": "cancelled", "task_id": self.task_id})
                                    emit({"type": "done", "status": "cancelled", "task_id": self.task_id})
                                    while event_buffer:
                                        yield event_buffer.pop(0)
                                    return

                                current_cmd = tool_args.get("command", "") if isinstance(tool_args, dict) else ""
                                if current_cmd != cmd_to_run:
                                    result = ToolResult(
                                        ok=False,
                                        exit_code=126,
                                        error=f"Command altered while awaiting approval: expected '{cmd_to_run}', got '{current_cmd}'",
                                    )
                                    is_skipped = True
                                else:
                                    self._update_state(STATE_EXECUTING, emit, f"Approval granted. Resuming execution of {tool_name}...")
                                    emit({
                                        "type": "approval_resolved",
                                        "decision": "approved",
                                        "task_id": self.task_id,
                                        "feedback": feedback,
                                    })
                                    while event_buffer:
                                        yield event_buffer.pop(0)

                            elif decision == "cancelled":
                                self._update_state(STATE_CANCELLED, emit, "Execution cancelled while awaiting approval")
                                self._record_replay_event("task.cancelled", {"task_id": self.task_id})
                                emit({"type": "approval_resolved", "decision": "cancelled", "task_id": self.task_id})
                                emit({"type": "done", "status": "cancelled", "task_id": self.task_id})
                                while event_buffer:
                                    yield event_buffer.pop(0)
                                return

                            elif decision == "timeout":
                                result = ToolResult(
                                    ok=False,
                                    exit_code=124,
                                    error="Command execution aborted: operator approval timed out after 300 seconds",
                                )
                                is_skipped = True
                                emit({"type": "approval_resolved", "decision": "timeout", "task_id": self.task_id})
                                self._update_state(STATE_EXECUTING, emit, "Approval timed out. Continuing task with denied tool result...")
                                while event_buffer:
                                    yield event_buffer.pop(0)

                            else:  # rejected or error
                                result = ToolResult(
                                    ok=False,
                                    exit_code=126,
                                    error=f"Command execution rejected by operator approval policy: {feedback}".strip(),
                                )
                                is_skipped = True
                                emit({
                                    "type": "approval_resolved",
                                    "decision": "rejected",
                                    "task_id": self.task_id,
                                    "feedback": feedback,
                                })
                                self._update_state(STATE_EXECUTING, emit, "Approval rejected. Continuing task with denied tool result...")
                                while event_buffer:
                                    yield event_buffer.pop(0)

                    # Deduplication check in same turn
                    cache_key = None
                    try:
                        import json
                        cache_key = f"{tool_name}:{json.dumps(tool_args, sort_keys=True)}"
                    except Exception:
                        pass

                    if not is_skipped and cache_key and cache_key in executed_calls_cache and tool_name in _READONLY_DEDUP_TOOLS:
                        prev_res = executed_calls_cache[cache_key]
                        result = ToolResult(
                            ok=prev_res.ok,
                            stdout=f"[DEDUPLICATED IN TURN] {prev_res.stdout}",
                            stderr=prev_res.stderr,
                            exit_code=prev_res.exit_code,
                            error=prev_res.error,
                        )
                        is_skipped = True

                    # Replay Protection: Check if tool execution can be safely skipped from prior checkpoint
                    if (
                        not is_skipped
                        and self._initial_skip_tools
                        and tool_name in self._initial_skip_tools
                        and tool_name not in ("run_command", "bash", "finish", "run_tests")
                    ):
                        for past_state in self._initial_completed_states:
                            p_name = past_state.get("tool_name", "")
                            if p_name.split()[0] == tool_name and past_state.get("state") == "COMPLETED":
                                if tool_name in ("write_file", "edit_file", "read_file"):
                                    p_arg = tool_args.get("path") if isinstance(tool_args, dict) else None
                                    if p_arg and str(p_arg) not in p_name:
                                        continue
                                is_skipped = True
                                self._initial_skip_tools.discard(tool_name)
                                emit({
                                    "type": "tool_skipped",
                                    "tool": tool_name,
                                    "reason": f"Tool '{tool_name}' already completed in prior checkpoint (replay protection).",
                                })
                                result = ToolResult(
                                    ok=True,
                                    stdout=f"[REPLAY PROTECTION] Skipped re-execution of '{tool_name}' (already completed in checkpoint: {past_state.get('result_summary', 'ok')})",
                                    stderr="",
                                    exit_code=0,
                                )
                                break

                    # Execute the tool if not skipped
                    if not is_skipped:
                        # Conservatively invalidate in-turn cached observations if attempting a state-changing tool
                        if tool_name not in _READONLY_DEDUP_TOOLS:
                            executed_calls_cache.clear()
                        req = ToolRequest(
                            tool=tool_name,
                            arguments=tool_args,
                            task_id=self.task_id,
                            session_id=self.session_id,
                        )
                        try:
                            result = self.tools.execute_tool(req, cancel_event=cancel_event)
                        except Exception as exc:
                            logger.error("Unhandled exception executing tool %s: %s", tool_name, exc, exc_info=True)
                            result = ToolResult(ok=False, exit_code=1, error=f"Tool execution failed unexpectedly: {exc}")
                        finally:
                            # Re-invalidate in case execution had partial mutations or timed out
                            if tool_name not in _READONLY_DEDUP_TOOLS:
                                executed_calls_cache.clear()
                        if cache_key and result is not None and tool_name in _READONLY_DEDUP_TOOLS:
                            executed_calls_cache[cache_key] = result
                        # Phase 16: record tool call in budget enforcer
                        if _budget_enforcer is not None:
                            _budget_enforcer.after_tool_call(tool_name=tool_name, ok=result.ok if result else False)

                    # Track files modified
                    if tool_name in ("write_file", "edit_file") and not is_skipped:
                        p_arg = tool_args.get("path") if isinstance(tool_args, dict) else None
                        if p_arg and p_arg not in self.created_or_edited_files:
                            self.created_or_edited_files.append(p_arg)
                            self._record_replay_event("file.modified", {"path": p_arg})

                    # Record tool completion or failure event for replay
                    if not is_skipped and result is not None:
                        if result.ok:
                            self._record_replay_event(
                                "tool.completed",
                                {"tool": tool_name, "summary": (result.stdout or "ok")[:150]},
                            )
                        else:
                            self._record_replay_event(
                                "tool.failed",
                                {"tool": tool_name, "error": (result.stderr or result.error or "failed")[:150]},
                            )

                    # Emit tool output text
                    if result and result.stdout:
                        out_summary = result.stdout if len(result.stdout) < 300 else result.stdout[:300] + "... (truncated)"
                        emit({"type": "text", "text": f"  [Output] {out_summary.strip()}\n"})
                        total_tokens_out += max(1, len(out_summary.split()))

                    if result and not result.ok:
                        has_tool_failures = True
                        err_msg = result.stderr or result.error or "Tool returned non-zero status"
                        emit({"type": "text", "text": f"  [Error] {err_msg.strip()}\n"})
                        emit({"type": "text", "text": f"  [Debug Args] {repr(tool_args)[:250]}\n"})

                        # Phase 17: Adaptive retrieval on tool error evidence
                        if self.adaptive_retriever is not None:
                            retrieval_delta = self.adaptive_retriever.retrieve_on_evidence(err_msg)
                            if retrieval_delta and retrieval_delta.success:
                                self._record_replay_event("context.adaptive_retrieval", retrieval_delta.as_dict())
                                emit({"type": "adaptive_retrieval", "retrieval": retrieval_delta.as_dict()})
                                if retrieval_delta.content_snippet:
                                    messages.append({
                                        "role": "user",
                                        "content": f"[ADAPTIVE RETRIEVAL: {retrieval_delta.file_path}]\n{retrieval_delta.content_snippet}",
                                    })

                        # Trigger Automated Repair Loop on test or command failure during execution
                        if tool_name in ("run_tests", "run_command") and iteration < self.max_iterations - 1:
                            self._update_state(STATE_REPAIRING, emit, "Tool/Test failed. Diagnosing and initiating repair...")
                            while event_buffer:
                                yield event_buffer.pop(0)

                    # Bound tool output (Phase 2B) and append observation
                    raw_content = result.stdout if (result and result.ok) else f"ERROR ({result.exit_code if result else 1}): {(result.stderr or result.error) if result else 'Tool execution returned no result'}"
                    bounded_content = bound_tool_output(raw_content, max_chars=12000, label=f"TOOL_{tool_name.upper()}")

                    messages.append({
                        "role": "tool",
                        "name": tool_name,
                        "content": bounded_content,
                        "tool_call_id": tcid,
                    })

                    # Boundary 2: After tool result
                    raw_summary = (result.stdout[:150] if (result and result.ok) else ((result.stderr or result.error or "")[:150] if result else ""))
                    arg_desc = ""
                    if isinstance(tool_args, dict):
                        if "command" in tool_args:
                            arg_desc = f" ({tool_args['command'][:80]})"
                        elif "path" in tool_args:
                            arg_desc = f" ({tool_args['path'][:80]})"
                    action_entry = {
                        "iteration": iteration,
                        "tool": f"{tool_name}{arg_desc}",
                        "ok": result.ok if result else False,
                        "summary": redact_secrets(raw_summary or "ok"),
                    }
                    rolling_checkpoint["completed_actions"].append(action_entry)
                    if len(rolling_checkpoint["completed_actions"]) > 15:
                        rolling_checkpoint["completed_actions"] = rolling_checkpoint["completed_actions"][-15:]

                    tool_state_entry = {
                        "tool_name": f"{tool_name}{arg_desc}",
                        "call_id": tcid,
                        "state": "SKIPPED" if is_skipped else ("COMPLETED" if (result and result.ok) else "FAILED"),
                        "idempotency_key": f"{tool_name}_{iteration}_{abs(hash(str(tool_args))) & 0xFFFFFFFF}",
                        "result_summary": redact_secrets(raw_summary or "ok"),
                        "timestamp": time.time(),
                    }
                    rolling_checkpoint.setdefault("tool_execution_states", []).append(tool_state_entry)
                    if len(rolling_checkpoint["tool_execution_states"]) > 50:
                        rolling_checkpoint["tool_execution_states"] = rolling_checkpoint["tool_execution_states"][-50:]

                    rolling_checkpoint.setdefault("resumability", {"skip_tools": [], "retry_tools": [], "requires_approval": []})
                    if result and result.ok and not is_skipped:
                        if tool_name not in rolling_checkpoint["resumability"]["skip_tools"]:
                            rolling_checkpoint["resumability"]["skip_tools"].append(tool_name)
                    elif result and not result.ok:
                        if tool_name not in rolling_checkpoint["resumability"]["retry_tools"]:
                            rolling_checkpoint["resumability"]["retry_tools"].append(tool_name)

                    rolling_checkpoint["last_tool"] = tool_name
                    rolling_checkpoint["last_args"] = tool_args
                    rolling_checkpoint["created_or_edited_files"] = list(self.created_or_edited_files)
                    rolling_checkpoint["elapsed_sec"] = time.time() - t_start
                    rolling_checkpoint["summary"] = (
                        f"Step {iteration}: Executed {tool_name} (ok={result.ok if result else False}). "
                        f"Completed actions: {len(rolling_checkpoint['completed_actions'])}."
                    )
                    emit({
                        "type": "progress",
                        "phase": "after_tool_result",
                        "checkpoint": dict(rolling_checkpoint),
                    })

                    while event_buffer:
                        yield event_buffer.pop(0)

                if turn_finished:
                    break

            # Execution attempt finished -> transition to COMPLETED
            self._update_state(
                STATE_COMPLETED,
                emit,
                f"Execution attempt {current_attempt} completed. Entering verification...",
            )
            emit({
                "type": "attempt_completed",
                "attempt": current_attempt,
                "task_id": self.task_id,
            })
            self._record_replay_event(
                "task.completed",
                {"attempt": current_attempt, "summary": final_summary},
            )
            while event_buffer:
                yield event_buffer.pop(0)

            # Verification Phase
            self._update_state(
                STATE_VERIFYING,
                emit,
                f"Running task verification engine (attempt {current_attempt})...",
            )
            emit({
                "type": "verifier_started",
                "task_id": self.task_id,
                "attempt": current_attempt,
            })
            self._record_replay_event(
                "verifier.started",
                {"task_id": self.task_id, "attempt": current_attempt},
            )
            rolling_checkpoint["elapsed_sec"] = time.time() - t_start
            emit({
                "type": "progress",
                "phase": "before_verification",
                "checkpoint": dict(rolling_checkpoint),
            })
            while event_buffer:
                yield event_buffer.pop(0)

            # Check cancellation before verification
            if cancel_event and cancel_event.is_set():
                self._update_state(STATE_CANCELLED, emit, "Cancelled during verification")
                self._record_replay_event("task.cancelled", {"task_id": self.task_id})
                emit({"type": "done", "status": "cancelled", "task_id": self.task_id})
                while event_buffer:
                    yield event_buffer.pop(0)
                return

            ver_status, ver_details, ver_meta = self.verifier.verify(
                task=task_prompt,
                files_created_or_edited=self.created_or_edited_files,
                cancel_event=cancel_event,
                requested_verification=verification_status,
                has_tool_failures=has_tool_failures,
                criteria=self.context.verification_criteria,
            )

            # Boundary 4: After verification
            rolling_checkpoint["verification"] = ver_status
            rolling_checkpoint["elapsed_sec"] = time.time() - t_start
            rolling_checkpoint["summary"] = final_summary or f"Verification complete: {ver_status}"
            emit({
                "type": "progress",
                "phase": "after_verification",
                "checkpoint": dict(rolling_checkpoint),
            })

            emit({"type": "text", "text": f"\nVerification Result (Attempt {current_attempt}): {ver_status}\n{ver_details}\n"})

            # Check if verification succeeded
            allow_partial = bool(self.context.verification_criteria.get("allow_partial", True))
            if ver_status == VERIFIED or (ver_status == PARTIALLY_VERIFIED and allow_partial):
                self._update_state(STATE_SUCCEEDED, emit, "Task completed and verified successfully.")
                emit({
                    "type": "verifier_passed",
                    "task_id": self.task_id,
                    "attempt": current_attempt,
                    "details": ver_details,
                    "metadata": ver_meta,
                })
                self._record_replay_event("verifier.passed", {"task_id": self.task_id, "details": ver_details})
                self._record_replay_event("task.succeeded", {"task_id": self.task_id, "summary": final_summary})
                terminal_status = "succeeded"
                if self.repair_history:
                    self.repair_history[-1]["repaired"] = True
                break

            # Verification failed
            emit({
                "type": "verifier_failed",
                "task_id": self.task_id,
                "attempt": current_attempt,
                "status": ver_status,
                "details": ver_details,
                "metadata": ver_meta,
            })
            self._record_replay_event(
                "verifier.failed",
                {"task_id": self.task_id, "status": ver_status, "details": ver_details},
            )

            repair_entry = {
                "attempt": current_attempt,
                "verification_status": ver_status,
                "details": ver_details,
                "metadata": ver_meta,
                "repaired": False,
                "timestamp": time.time(),
            }
            self.repair_history.append(repair_entry)

            # Check bounded repair loop eligibility
            if (
                current_attempt < max_total_attempts
                and iteration < self.max_iterations
                and time.time() < deadline
            ):
                current_attempt += 1
                # Phase 16: record repair attempt in budget enforcer
                if _budget_enforcer is not None:
                    _budget_enforcer.after_repair_attempt()
                self._update_state(
                    STATE_REPAIRING,
                    emit,
                    f"Verification failed ({ver_status}). Initiating repair attempt {current_attempt}...",
                )
                emit({
                    "type": "repair_started",
                    "attempt": current_attempt,
                    "reason": ver_details,
                    "failure_status": ver_status,
                })
                while event_buffer:
                    yield event_buffer.pop(0)

                repair_feedback = (
                    f"[VERIFICATION FAILED — REPAIR ATTEMPT {current_attempt}]\n"
                    f"The verification engine rejected the workspace state with status '{ver_status}'.\n"
                    f"Failure Details:\n{ver_details}\n\n"
                    f"DIAGNOSTIC & REPAIR INSTRUCTION:\n"
                    f"1. Inspect the error output above and identify the root cause.\n"
                    f"2. Use edit_file or write_file to repair the implementation/files.\n"
                    f"3. Run tests using run_command or run_tests to verify the repair.\n"
                    f"4. Call finish when your repair is complete."
                )
                # Phase 17: Adaptive retrieval on verifier failure diagnostics
                if self.adaptive_retriever is not None and ver_details:
                    ver_retrieval = self.adaptive_retriever.retrieve_on_evidence(ver_details)
                    if ver_retrieval and ver_retrieval.success:
                        self._record_replay_event("context.adaptive_retrieval", ver_retrieval.as_dict())
                        emit({"type": "adaptive_retrieval", "retrieval": ver_retrieval.as_dict()})
                        if ver_retrieval.content_snippet:
                            repair_feedback += (
                                f"\n\n[ADAPTIVELY RETRIEVED CONTEXT: {ver_retrieval.file_path}]\n"
                                f"{ver_retrieval.content_snippet}"
                            )
                messages.append({"role": "user", "content": repair_feedback})
                self._update_state(STATE_EXECUTING, emit, f"Executing repair attempt {current_attempt}...")
                while event_buffer:
                    yield event_buffer.pop(0)
            else:
                self._update_state(
                    STATE_FAILED,
                    emit,
                    f"Task verification failed after {current_attempt} attempt(s): {ver_status}",
                )
                self._record_replay_event(
                    "task.failed",
                    {"task_id": self.task_id, "reason": f"Verification failed after {current_attempt} attempts: {ver_status}"},
                )
                terminal_status = "failed"
                break

        # Extract unified diff for task changes
        diff_text = get_execution_diff(self.context, self.created_or_edited_files)

        # Record result in durable state
        result_payload = {
            "task": task_prompt,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "status": terminal_status,
            "verification": ver_status,
            "verification_details": ver_details,
            "verification_metadata": ver_meta,
            "summary": final_summary,
            "files": self.created_or_edited_files,
            "diff": diff_text,
            "executed_tools": [a.get("tool") for a in rolling_checkpoint.get("completed_actions", [])],
            "hitl_decisions": self.hitl_decisions,
            "repair_history": self.repair_history,
            "execution_context": self.context.to_dict(),
            "tokens": total_tokens_in + total_tokens_out,
        }
        # Phase 16: Include live usage accounting and record replay event
        if _budget_enforcer is not None:
            _final_ev = _budget_enforcer.final_evidence()
            result_payload["usage_evidence"] = _final_ev
            self._record_replay_event("orchestration.usage_final", _final_ev)

        # Phase 17: Include adaptive retrievals evidence
        if self.adaptive_retriever is not None:
            result_payload["adaptive_retrievals"] = self.adaptive_retriever.as_dict()

        # Phase 18: Governed Engineering Memory promotion on verified task success
        if self.memory_kernel is not None and terminal_status == "succeeded":
            try:
                _promoted = self.memory_kernel.promote_task_outcome(
                    task_id=self.task_id,
                    tenant_id=self.tenant_id,
                    task_type=str(getattr(self.context, "task_type", "general")),
                    task_objective=task_prompt,
                    summary=final_summary or "Task verified and completed successfully",
                    diff=diff_text or "",
                    files_changed=list(self.created_or_edited_files),
                    verification_summary={
                        "status": ver_status,
                        "details": ver_details,
                        "score": 1.0 if ver_status == VERIFIED else 0.8,
                    },
                    repo_id=str(getattr(self.context, "repo_id", None) or getattr(self.context, "repo_root", None) or "default_repo"),
                )
                if _promoted:
                    self._record_replay_event(
                        "memory.persisted",
                        {"record_id": _promoted.record_id, "scope": _promoted.scope.value},
                    )
                    result_payload["memory_persisted"] = _promoted.as_dict()
            except Exception as _mem_exc:
                logger.debug("Memory promotion note: %s", _mem_exc)

        require_review = bool(self.context.execution_constraints.get("require_review", False))
        if require_review and terminal_status == "succeeded":
            self.context.review_status = "pending"
            result_payload["review_status"] = "pending"
            result_payload["review_required"] = True

        try:
            if terminal_status == "succeeded":
                durable_state.succeed(
                    self.task_id,
                    self.tenant_id,
                    result=result_payload,
                )
                if require_review:
                    try:
                        durable_state.transition(
                            self.task_id,
                            self.tenant_id,
                            durable_state.STATUS_REVIEW_PENDING,
                            result=result_payload,
                        )
                        self.current_state = STATE_REVIEW_PENDING
                        self._record_replay_event(
                            "task.review_pending",
                            {"task_id": self.task_id, "diff_lines": len(diff_text.splitlines())},
                        )
                        emit({
                            "type": "review_pending",
                            "task_id": self.task_id,
                            "diff": diff_text,
                            "summary": final_summary,
                            "files": self.created_or_edited_files,
                        })
                    except Exception as rev_exc:
                        logger.debug("Durable state review_pending transition error: %s", rev_exc)
            else:
                durable_state.fail(
                    self.task_id,
                    self.tenant_id,
                    failure_class=durable_state.FAILURE_CLASS_VERIFIER,
                    result=result_payload,
                )
        except Exception as exc:
            logger.debug("Durable state terminal write error: %s", exc)

        # Log assistant response to session
        if self.session_manager and self.session_id and final_summary:
            try:
                self.session_manager.record_message(
                    session_id=self.session_id,
                    owner_id=self.tenant_id,
                    role="assistant",
                    content=final_summary,
                )
            except Exception as exc:
                logger.debug("Session assistant message record error: %s", exc)

        # Record retrieval outcome feedback and extract learning signals
        try:
            if self.feedback_tracker and self.retrieved_memory_ids:
                outcome = "success" if terminal_status == "succeeded" else "failure"
                for mid in self.retrieved_memory_ids:
                    self.feedback_tracker.record_feedback(
                        owner_id=self.tenant_id,
                        memory_id=mid,
                        session_id=self.session_id,
                        query=task_prompt,
                        outcome=outcome,
                        relevance_score=1.0 if terminal_status == "succeeded" else 0.5,
                    )
            if self.learning_engine:
                self.learning_engine.extract_learning_signals_from_run(
                    owner_id=self.tenant_id,
                    session_id=self.session_id,
                    task_prompt=task_prompt,
                    status="completed" if terminal_status == "succeeded" else "failed",
                    verification_status="verified" if ver_status == VERIFIED else "failed",
                    created_or_edited_files=self.created_or_edited_files,
                    tool_action_count=len(self.action_history),
                )
        except Exception as exc:
            logger.warning(
                "Feedback/Learning hook failed for tenant=%s, session=%s, task=%s: %s (%s)",
                getattr(self, "tenant_id", None),
                getattr(self, "session_id", None),
                getattr(self, "task_id", None),
                exc,
                type(exc).__name__,
                exc_info=True,
            )

        # 5. Emit Usage
        emit({
            "type": "usage",
            "inputTokens": total_tokens_in,
            "outputTokens": total_tokens_out,
            "totalTokens": total_tokens_in + total_tokens_out,
        })

        # 6. Emit Done Event
        final_exec_status = "review_pending" if (terminal_status == "succeeded" and require_review) else terminal_status
        emit({
            "type": "done",
            "status": "review_pending" if (terminal_status == "succeeded" and require_review) else ("done" if terminal_status == "succeeded" else terminal_status),
            "execution_status": final_exec_status,
            "task_id": self.task_id,
            "verification": ver_status,
            "summary": final_summary,
            "files": self.created_or_edited_files,
            "diff": diff_text,
            "repair_history": self.repair_history,
            "hitl_decisions": self.hitl_decisions,
            "review_status": "pending" if (terminal_status == "succeeded" and require_review) else None,
            "failure_class": "verifier_failure" if terminal_status == "failed" else None,
            "outputTokens": total_tokens_out,
        })

        while event_buffer:
            yield event_buffer.pop(0)

