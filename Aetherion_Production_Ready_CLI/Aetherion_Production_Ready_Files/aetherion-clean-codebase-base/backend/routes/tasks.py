"""
routes/tasks.py — Durable Task Execution, Status, HITL & Lifecycle API
Supports:
  - POST /api/tasks (Create task)
  - GET /api/tasks (List tasks)
  - GET /api/task/<tid> (Task status)
  - POST /api/task/<tid>/stop (Cancel/stop task)
  - POST /api/task/<tid>/retry (Retry task)
  - POST /api/task/<tid>/approve (Approve/reject HITL gate)
  - GET /api/task/<tid>/logs and GET /api/logs/<tid> (Task logs)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict
import requests
from flask import Blueprint, jsonify, request, g, Response, stream_with_context
from auth_system import token_required
from execution import durable_state

logger = logging.getLogger("aetherion.tasks")
tasks_bp = Blueprint("tasks", __name__)


def _get_tenant() -> str:
    """Derive tenant or user identifier from current auth context."""
    user_id = getattr(g, "user_id", None)
    if user_id:
        return str(user_id)
    return "default_tenant"


@tasks_bp.route("/api/tasks", methods=["POST"])
@token_required
def create_task():
    """Create an authoritative durable coding task."""
    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt") or data.get("task") or data.get("objective")
    if not prompt:
        return jsonify({"ok": False, "error": "Task prompt is required"}), 400

    workspace_id = data.get("workspace_id") or data.get("session_id") or "default"
    model = data.get("model") or "default"
    idempotency_key = request.headers.get("Idempotency-Key") or data.get("idempotency_key")
    tenant_id = _get_tenant()

    options = dict(data.get("options") or {})
    repo_id = data.get("repo_id") or options.get("repo_id")
    repo_root = data.get("repo_root") or options.get("repo_root")
    base_ref = data.get("base_ref") or options.get("base_ref") or "HEAD"
    execution_constraints = dict(data.get("execution_constraints") or options.get("execution_constraints") or {})
    verification_criteria = dict(data.get("verification_criteria") or options.get("verification_criteria") or {})
    execution_budget = dict(data.get("execution_budget") or options.get("execution_budget") or {})
    security_context = dict(data.get("security_context") or options.get("security_context") or {})

    # Check for allow_plain_workspace policy
    if "allow_plain_workspace" in data:
        execution_constraints["allow_plain_workspace"] = bool(data["allow_plain_workspace"])
    elif "allow_plain_workspace" in options:
        execution_constraints["allow_plain_workspace"] = bool(options["allow_plain_workspace"])

    payload = {
        "prompt": prompt,
        "objective": prompt,
        "workspace_id": workspace_id,
        "model": model,
        "repo_id": repo_id,
        "repo_root": repo_root,
        "base_ref": base_ref,
        "execution_constraints": execution_constraints,
        "verification_criteria": verification_criteria,
        "execution_budget": execution_budget,
        "security_context": security_context,
        "options": options,
    }

    try:
        record = durable_state.create(
            kind="agent_task",
            tenant_id=tenant_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        task_id = record["execution_id"]

        # If auto_run requested, launch task execution pipeline in background
        if data.get("auto_run") or data.get("run"):
            cancel_ev = register_cancellation(task_id)
            t = threading.Thread(
                target=run_task_pipeline,
                args=(task_id, tenant_id, cancel_ev),
                name=f"TaskExec-{task_id}",
                daemon=True,
            )
            t.start()

        return jsonify({
            "ok": True,
            "task_id": task_id,
            "id": task_id,
            "session_id": task_id,
            "status": record["status"].lower(),
            "workspace_id": workspace_id,
            "created_at": record["created_at"],
            "data": record,
            "execution_context": {
                "task_id": task_id,
                "tenant_id": tenant_id,
                "objective": prompt,
                "repo_root": repo_root,
                "base_ref": base_ref,
                "execution_constraints": execution_constraints,
                "verification_criteria": verification_criteria,
                "execution_budget": execution_budget,
            },
        }), 201
    except Exception as exc:
        logger.exception("Failed to create task: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@tasks_bp.route("/api/tasks", methods=["GET"])
@token_required
def list_tasks():
    """List tasks for the authenticated user/tenant."""
    tenant_id = _get_tenant()
    limit = min(int(request.args.get("limit", 50)), 100)
    records = durable_state.list_for_tenant(tenant_id, limit=limit)
    return jsonify({"ok": True, "tasks": records, "data": records})


@tasks_bp.route("/api/task/<tid>", methods=["GET"])
@tasks_bp.route("/api/tasks/<tid>", methods=["GET"])
@token_required
def get_task(tid: str):
    """Get status and result of a specific task."""
    tenant_id = _get_tenant()
    record = durable_state.get(tid, tenant_id)
    if not record:
        return jsonify({"ok": False, "error": "Task not found"}), 404

    payload = json.loads(record.get("payload_json") or "{}")
    result = json.loads(record.get("result_json") or "{}") if record.get("result_json") else None

    return jsonify({
        "ok": True,
        "task_id": record["execution_id"],
        "id": record["execution_id"],
        "session_id": record["execution_id"],
        "status": record["status"].lower(),
        "execution_state": record["status"],
        "status_version": record["status_version"],
        "failure_class": record.get("failure_class"),
        "workspace_id": payload.get("workspace_id", "default"),
        "result": result,
        "data": record,
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    })


ACTIVE_TASK_CANCELLATIONS: Dict[str, threading.Event] = {}
_CANCELLATION_LOCK = threading.Lock()


def register_cancellation(task_id: str) -> threading.Event:
    ev = threading.Event()
    with _CANCELLATION_LOCK:
        ACTIVE_TASK_CANCELLATIONS[task_id] = ev
    return ev


def trigger_cancellation(task_id: str) -> bool:
    with _CANCELLATION_LOCK:
        ev = ACTIVE_TASK_CANCELLATIONS.get(task_id)
        if ev:
            ev.set()
            return True
    return False


def unregister_cancellation(task_id: str) -> None:
    with _CANCELLATION_LOCK:
        ACTIVE_TASK_CANCELLATIONS.pop(task_id, None)


@tasks_bp.route("/api/task/<tid>/stop", methods=["POST"])
@tasks_bp.route("/api/tasks/<tid>/stop", methods=["POST"])
@token_required
def stop_task(tid: str):
    """Cancel / stop an active task with cancel-wins semantics."""
    tenant_id = _get_tenant()
    try:
        updated = durable_state.stop(tid, tenant_id)
        trigger_cancellation(tid)
        try:
            from execution.hitl import global_hitl_tracker
            global_hitl_tracker.cancel_approval(tid, feedback="Task cancelled via stop API")
        except Exception as hitl_exc:
            logger.debug("HITL tracker cancel error for %s: %s", tid, hitl_exc)
        return jsonify({"ok": True, "task_id": tid, "status": "stopped", "data": updated})
    except (PermissionError, durable_state.AuthorizationError):
        return jsonify({"ok": False, "error": "Forbidden: Task does not belong to your tenant"}), 403
    except durable_state.TransitionError as te:
        return jsonify({"ok": False, "error": str(te)}), 409
    except durable_state.ExecutionStateError as ee:
        if "unknown execution" in str(ee).lower():
            return jsonify({"ok": False, "error": "Task not found"}), 404
        return jsonify({"ok": False, "error": str(ee)}), 500
    except Exception as exc:
        return jsonify({"ok": False, "error": "Internal server error"}), 500


@tasks_bp.route("/api/task/<tid>/retry", methods=["POST"])
@tasks_bp.route("/api/tasks/<tid>/retry", methods=["POST"])
@token_required
def retry_task(tid: str):
    """Retry a failed or stopped task."""
    tenant_id = _get_tenant()
    record = durable_state.get(tid, tenant_id)
    if not record:
        return jsonify({"ok": False, "error": "Task not found"}), 404

    payload = json.loads(record.get("payload_json") or "{}")
    new_record = durable_state.create(
        kind="agent_task",
        tenant_id=tenant_id,
        payload=payload,
    )
    return jsonify({
        "ok": True,
        "previous_task_id": tid,
        "task_id": new_record["execution_id"],
        "status": new_record["status"],
    })


@tasks_bp.route("/api/task/<tid>/approve", methods=["POST"])
@tasks_bp.route("/api/tasks/<tid>/approve", methods=["POST"])
@tasks_bp.route("/api/z29/governance/<decision>/<tid>", methods=["POST"])
@token_required
def approve_task(tid: str, decision: Optional[str] = None):
    """Approve or reject a Human-in-the-Loop (HITL) gate for a task."""
    tenant_id = _get_tenant()
    user_id = getattr(g, "user_id", None) or "human_operator"
    body = request.get_json(silent=True) or {}
    if not decision:
        decision = (body.get("decision") or "approve").lower()  # approve | reject

    record = durable_state.get(tid, tenant_id)
    if not record:
        return jsonify({"ok": False, "error": "Task not found"}), 404

    status = (record.get("status") or "").upper()
    if status in durable_state.TERMINAL_STATUSES:
        return jsonify({"ok": False, "error": f"Cannot approve task in terminal state: '{status}'"}), 409

    comment = body.get("comment") or body.get("comments") or ""
    is_approved = decision in ("approve", "accept")
    gate_resolved = False
    target_status = None

    try:
        if is_approved:
            if status == durable_state.STATUS_REVIEW_PENDING:
                target_status = durable_state.STATUS_ACCEPTED
                durable_state.accept(
                    tid,
                    tenant_id,
                    comment=comment,
                    approver=str(user_id),
                )
                gate_resolved = True
            elif status in (durable_state.STATUS_WAITING_FOR_APPROVAL, durable_state.STATUS_AWAITING_APPROVAL):
                target_status = durable_state.STATUS_EXECUTING
                durable_state.transition(tid, tenant_id, target_status)
                gate_resolved = True
            elif status in (durable_state.STATUS_PENDING, durable_state.STATUS_QUEUED):
                target_status = durable_state.STATUS_RUNNING
                durable_state.transition(tid, tenant_id, target_status)
                gate_resolved = True
            else:
                target_status = status
                gate_resolved = True
        else:
            # Rejection
            if status in (durable_state.STATUS_REVIEW_PENDING, durable_state.STATUS_SUCCEEDED):
                target_status = durable_state.STATUS_REJECTED
                durable_state.reject(
                    tid,
                    tenant_id,
                    comment=comment,
                    reviewer=str(user_id),
                )
                gate_resolved = True
            elif status in (durable_state.STATUS_WAITING_FOR_APPROVAL, durable_state.STATUS_AWAITING_APPROVAL):
                target_status = durable_state.STATUS_CANCELLED
                durable_state.transition(tid, tenant_id, target_status)
                gate_resolved = True
            else:
                target_status = durable_state.STATUS_CANCELLED
                durable_state.transition(tid, tenant_id, target_status)
                gate_resolved = True
    except (PermissionError, durable_state.AuthorizationError):
        return jsonify({"ok": False, "error": "Forbidden: Task does not belong to your tenant"}), 403
    except durable_state.TransitionError as te:
        logger.warning("Durable transition error on task %s decision: %s", tid, te)
        return jsonify({"ok": False, "error": str(te)}), 409
    except Exception as exc:
        logger.exception("Failed to transition durable state for task %s on decision: %s", tid, exc)
        return jsonify({"ok": False, "error": "Internal server error"}), 500

    # Unblock any thread waiting on the in-memory or SQLite gate tracker
    try:
        from execution.hitl import resolve_gate
        resolve_gate(tid, is_approved, comments=comment)
    except Exception as e:
        logger.debug("Failed to resolve in-process HITL gate for task %s: %s", tid, e)

    # Return authoritative durable response
    return jsonify({
        "ok": True,
        "task_id": tid,
        "decision": decision,
        "status": target_status,
        "gate_resolved": gate_resolved,
    })



@tasks_bp.route("/api/task/<tid>/logs", methods=["GET"])
@tasks_bp.route("/api/tasks/<tid>/logs", methods=["GET"])
@tasks_bp.route("/api/logs/<tid>", methods=["GET"])
@token_required
def get_task_logs(tid: str):
    """Retrieve execution logs for a task."""
    tenant_id = _get_tenant()
    record = durable_state.get(tid, tenant_id)
    if not record:
        return jsonify({"ok": False, "error": "Task not found"}), 404

    log_lines = [
        f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(record['created_at']))}] Task {tid} initialized.",
        f"Status: {record['status']}",
    ]
    if record.get("failure_class"):
        log_lines.append(f"Failure class: {record['failure_class']}")

    return jsonify({
        "ok": True,
        "task_id": tid,
        "logs": "\n".join(log_lines),
        "lines": log_lines,
    })


def run_task_pipeline(
    task_id: str,
    tenant_id: str,
    cancel_event: Optional[threading.Event] = None,
    provider: Optional[Any] = None,
) -> Dict[str, Any]:
    """Authoritative task execution pipeline connecting TaskExecutionContext and RealAgentRuntime."""
    record = durable_state.get(task_id, tenant_id)
    if not record:
        raise ValueError(f"Task '{task_id}' not found for tenant '{tenant_id}'")

    payload = json.loads(record.get("payload_json") or "{}")
    prompt = payload.get("prompt") or payload.get("objective") or "autonomous_task"
    workspace_id = payload.get("workspace_id") or task_id
    model = payload.get("model") or "default"

    ws_base = os.path.join(os.getcwd(), "workspace")
    os.makedirs(ws_base, exist_ok=True)
    ws_dir = os.path.join(ws_base, workspace_id)
    os.makedirs(ws_dir, exist_ok=True)

    from execution.agent_runtime import (
        RealAgentRuntime,
        TaskExecutionContext,
        WorktreeRequiredError,
        attach_execution_worktree,
        get_provider,
    )

    # ------------------------------------------------------------------ #
    # Phase 15: Task Classification → Execution Policy → RunBudget        #
    # ------------------------------------------------------------------ #
    orch_evidence: Dict[str, Any] = {}
    try:
        from execution.orchestration import (
            TaskClassifier,
            derive_execution_policy,
            ContextCompiler,
            ModelRole,
            UsageAccounting,
            RepositoryIntelligence,
        )

        # 1. Classify the task (deterministic, no model call)
        classifier = TaskClassifier()
        classification = classifier.classify(
            prompt,
            hints=dict(payload.get("classification_hints") or {}),
        )
        orch_evidence["classification"] = classification.as_dict()
        logger.info(
            "Task %s classified: complexity=%s, risk=%s, severity=%d",
            task_id,
            classification.complexity.value,
            classification.risk.value,
            classification.overall_severity,
        )

        # 2. Derive execution policy (profile selection + RunBudget)
        execution_budget_payload = dict(payload.get("execution_budget") or {})
        policy = derive_execution_policy(
            classification,
            execution_budget_override=execution_budget_payload or None,
        )
        orch_evidence["policy"] = {
            "planner_profile": policy.planner_profile.name,
            "executor_profile": policy.executor_profile.name,
            "verifier_profile": policy.verifier_profile.name,
            "repair_profile": policy.repair_profile.name,
            "require_hitl": policy.require_hitl,
            "max_repair_attempts": policy.max_repair_attempts,
            "rationale": policy.rationale,
        }
        orch_evidence["run_budget_limits"] = policy.run_budget.as_dict()["limits"]

        # 3. Merge derived budget back into execution_budget so RealAgentRuntime
        #    reads it from the single authoritative source: context.execution_budget
        derived_budget = {
            "max_repair_attempts": policy.max_repair_attempts,
            "max_model_calls": policy.run_budget.max_model_calls,
            "max_tool_calls": policy.run_budget.max_tool_calls,
            "max_execution_time_sec": policy.run_budget.max_execution_time_sec,
            "max_tokens": policy.run_budget.max_tokens,
            "max_escalation_attempts": policy.run_budget.max_escalation_attempts,
        }
        # Caller-supplied budget keys take precedence over derived
        merged_budget = {**derived_budget, **execution_budget_payload}
        payload["execution_budget"] = merged_budget

        # Phase 17: Authoritative Repository Intelligence & Adaptive Context Compilation
        repo_intel = RepositoryIntelligence(repo_root=payload.get("repo_root") or ws_dir)
        relevance_items = repo_intel.get_task_context(
            prompt,
            user_files=payload.get("files") or payload.get("user_files"),
            recent_evidence=dict(payload.get("recent_evidence") or {}),
        )
        orch_evidence["repo_intelligence"] = repo_intel.as_dict()
        orch_evidence["task_relevance"] = [item.as_dict() for item in relevance_items]

        # Phase 18: Governed Skill Discovery & Verified Engineering Memory Retrieval
        from execution.orchestration import (
            SkillRegistry,
            SkillDiscoveryEngine,
            EngineeringMemoryKernel,
        )
        skill_registry = SkillRegistry()
        skill_engine = SkillDiscoveryEngine(skill_registry)
        skill_candidates = skill_engine.discover(
            task_objective=prompt,
            repo_intel=repo_intel,
            classification=classification,
        )
        orch_evidence["skills_discovered"] = [c.as_dict() for c in skill_candidates]

        memory_kernel = EngineeringMemoryKernel()
        eng_memories, memory_conflicts = memory_kernel.retrieve_relevant(
            tenant_id=tenant_id,
            query=prompt,
            repo_id=payload.get("repo_id") or payload.get("repo_root") or ws_dir,
            task_type=classification.task_type.value if hasattr(classification, "task_type") else None,
        )
        orch_evidence["engineering_memories"] = [m.as_dict() for m in eng_memories]
        orch_evidence["memory_conflicts"] = [c.as_dict() for c in memory_conflicts]

        # 4. Compile role-specific context for the EXECUTOR role
        compiler = ContextCompiler()
        compiled_ctx = compiler.compile(
            role=ModelRole.EXECUTOR,
            objective=prompt,
            context_budget_tokens=policy.executor_profile.context_budget,
            worktree_metadata={
                "repo_root": payload.get("repo_root"),
                "base_ref": payload.get("base_ref", "HEAD"),
                "workspace_id": workspace_id,
            },
            rationale=policy.rationale,
            repo_intelligence=repo_intel,
            relevance_items=relevance_items,
            verification_criteria=dict(payload.get("verification_criteria") or {}),
            selected_skills=skill_candidates,
            engineering_memories=eng_memories,
            memory_conflicts=memory_conflicts,
        )
        orch_evidence["compiled_context_summary"] = compiled_ctx.as_dict()

        # 5. Initialise usage accounting (stored in evidence; expanded post-run)
        usage = UsageAccounting(
            task_id=task_id,
            tenant_id=tenant_id,
            initial_profile_name=policy.executor_profile.name,
            initial_profile_selection_rationale=policy.rationale,
        )
        orch_evidence["usage_accounting_started"] = True

        # Phase 19: Adaptive Planning & Execution Graph Generation
        try:
            from execution.orchestration import AdaptivePlanner
            planner = AdaptivePlanner()
            exec_graph = planner.plan_task(
                task_id=task_id,
                tenant_id=tenant_id,
                objective=prompt,
                classification=classification,
                repo_intelligence=repo_intel,
                skills=skill_candidates,
                engineering_memories=eng_memories,
                memory_conflicts=memory_conflicts,
                execution_budget=payload.get("execution_budget"),
                verification_criteria=payload.get("verification_criteria"),
            )
            orch_evidence["execution_plan"] = exec_graph.plan.as_dict()
            orch_evidence["execution_graph"] = exec_graph.as_dict()

            # Phase 20: Durable Execution Supervisor & Crash-Resilient Workflow Kernel
            try:
                from execution.orchestration import DurableExecutionSupervisor
                supervisor = DurableExecutionSupervisor()
                supervisor.register_task(
                    task_id=task_id,
                    tenant_id=tenant_id,
                    graph=exec_graph,
                    status="RUNNING",
                )
                orch_evidence["durable_supervisor_registered"] = True

                # Phase 21: Execution Fabric & Worker Control Plane
                try:
                    from execution.fabric import ExecutionFabricService
                    fabric_service = ExecutionFabricService(supervisor=supervisor)
                    local_worker_id = f"worker_{os.getpid()}_{uuid.uuid4().hex[:6]}"
                    fabric_service.register_worker(
                        worker_id=local_worker_id,
                        max_concurrency=4,
                        supported_capabilities=["tool_execution", "sandbox", "model_access", "read_file", "edit_file", "bash"],
                    )
                    enqueued_items = fabric_service.enqueue_graph_steps(
                        task_id=task_id,
                        tenant_id=tenant_id,
                        graph=exec_graph,
                    )
                    orch_evidence["fabric_service_registered"] = True
                    orch_evidence["fabric_enqueued_steps"] = len(enqueued_items)
                except Exception as _fab_reg_exc:
                    logger.debug("Fabric service registration note: %s", _fab_reg_exc)
            except Exception as _sup_reg_exc:
                logger.debug("Supervisor registration note: %s", _sup_reg_exc)
        except Exception as _plan_exc:
            logger.debug("Plan generation note: %s", _plan_exc)


    except Exception as orch_exc:
        # Orchestration is additive — if it fails, pipeline continues unimpeded
        logger.warning(
            "Phase 15 orchestration layer error for task %s (continuing): %s",
            task_id, orch_exc,
        )
        orch_evidence["orchestration_error"] = str(orch_exc)

    exec_constraints = dict(payload.get("execution_constraints") or {})
    context = TaskExecutionContext(
        task_id=task_id,
        objective=prompt,
        workspace_dir=ws_dir,
        tenant_id=tenant_id,
        session_id=workspace_id,
        repo_id=payload.get("repo_id"),
        repo_root=payload.get("repo_root"),
        base_ref=payload.get("base_ref", "HEAD"),
        execution_constraints=exec_constraints,
        verification_criteria=dict(payload.get("verification_criteria") or {}),
        execution_budget=dict(payload.get("execution_budget") or {}),
        security_context=dict(payload.get("security_context") or {}),
    )
    if 'supervisor' in locals() and supervisor is not None:
        context.supervisor = supervisor
    if 'fabric_service' in locals() and fabric_service is not None:
        context.fabric = fabric_service

    # Attach worktree according to safety policy (isolated worktree required by default)
    try:
        context = attach_execution_worktree(context, repo_root=context.repo_root)
        durable_state.update_payload(
            task_id,
            tenant_id,
            lambda p: {
                **p,
                "worktree_id": context.worktree_id,
                "branch": context.branch,
                "base_ref": context.base_ref,
                "repo_root": context.repo_root,
                "workspace_dir": str(context.workspace_dir),
            },
        )
    except WorktreeRequiredError as wte:
        logger.error("Worktree required policy failed for task %s: %s", task_id, wte)
        fail_res = {
            "error": str(wte),
            "failure_class": durable_state.FAILURE_CLASS_QUEUE,
            "status": durable_state.STATUS_FAILED,
            "orchestration": orch_evidence,
        }
        durable_state.fail(
            task_id,
            tenant_id,
            failure_class=durable_state.FAILURE_CLASS_QUEUE,
            result=fail_res,
        )
        return fail_res

    # Transition to RUNNING
    try:
        durable_state.transition(task_id, tenant_id, durable_state.STATUS_RUNNING)
    except Exception as trans_exc:
        logger.debug("Transition to RUNNING note for task %s: %s", task_id, trans_exc)

    prov = provider or get_provider(model)
    runtime = RealAgentRuntime(context=context, provider=prov)

    # Record orchestration evidence as a replay event before execution begins
    if orch_evidence:
        if "compiled_context_summary" in orch_evidence:
            runtime._record_replay_event("context.compiled", orch_evidence["compiled_context_summary"])
        if "skills_discovered" in orch_evidence:
            runtime._record_replay_event("skill.discovered", {"skills": orch_evidence["skills_discovered"]})
        if "engineering_memories" in orch_evidence:
            runtime._record_replay_event("memory.retrieved", {
                "memories": orch_evidence["engineering_memories"],
                "conflicts": orch_evidence.get("memory_conflicts", []),
            })
        if "execution_plan" in orch_evidence:
            p_dict = orch_evidence["execution_plan"]
            runtime._record_replay_event("plan.created", {
                "plan_id": p_dict.get("plan_id"),
                "step_count": p_dict.get("step_count", 0),
                "steps": list(p_dict.get("steps", {}).keys()),
            })
            runtime._record_replay_event("plan.validated", {
                "plan_id": p_dict.get("plan_id"),
                "topological_order": orch_evidence.get("execution_graph", {}).get("ready_steps", []),
            })
        runtime._record_replay_event("orchestration.policy_selected", orch_evidence)


    ev_cancel = cancel_event or register_cancellation(task_id)
    last_event = None
    try:
        for ev in runtime.run_stream(prompt, cancel_event=ev_cancel):
            last_event = ev
    finally:
        unregister_cancellation(task_id)

    # Phase 16: populate final live usage accounting into orchestration evidence
    if hasattr(runtime, "budget_enforcer") and runtime.budget_enforcer is not None:
        orch_evidence["usage_final"] = runtime.budget_enforcer.final_evidence()

    latest_rec = durable_state.get(task_id, tenant_id)
    res_data = json.loads(latest_rec.get("result_json") or "{}") if latest_rec and latest_rec.get("result_json") else {}

    # Phase 18: Governed Learning Loop — promote verified task outcome to Engineering Memory
    if latest_rec and latest_rec.get("status") in (durable_state.STATUS_SUCCEEDED, durable_state.STATUS_REVIEW_PENDING):
        try:
            from execution.orchestration import EngineeringMemoryKernel
            mem_kernel = EngineeringMemoryKernel()
            promoted_mem = mem_kernel.promote_task_outcome(
                task_id=task_id,
                tenant_id=tenant_id,
                task_type=classification.task_type.value if hasattr(classification, "task_type") else "general",
                task_objective=prompt,
                summary=res_data.get("summary") or "Task completed and verified",
                diff=res_data.get("diff") or "",
                files_changed=res_data.get("files") or [],
                verification_summary={
                    "status": res_data.get("verification", "VERIFIED"),
                    "details": res_data.get("verification_details", ""),
                    "score": 1.0,
                },
                repo_id=payload.get("repo_id") or payload.get("repo_root"),
                skills_used=[c.skill.skill_id for c in skill_candidates] if 'skill_candidates' in locals() else [],
            )
            if promoted_mem:
                runtime._record_replay_event("memory.persisted", {"record_id": promoted_mem.record_id, "scope": promoted_mem.scope.value})
                orch_evidence["memory_persisted"] = promoted_mem.as_dict()
        except Exception as _mem_p_exc:
            logger.debug("Pipeline memory promote note: %s", _mem_p_exc)

    # Phase 19: Record plan terminal status replay event
    if "execution_plan" in orch_evidence:
        p_dict = orch_evidence["execution_plan"]
        p_id = p_dict.get("plan_id")
        p_steps = p_dict.get("steps", {})
        if latest_rec and latest_rec.get("status") in (durable_state.STATUS_SUCCEEDED, durable_state.STATUS_REVIEW_PENDING):
            runtime._record_replay_event("plan.completed", {
                "plan_id": p_id,
                "steps_completed": len(p_steps),
                "terminal_status": latest_rec.get("status"),
            })
        elif latest_rec and latest_rec.get("status") == durable_state.STATUS_FAILED:
            runtime._record_replay_event("plan.failed", {
                "plan_id": p_id,
                "terminal_status": "FAILED",
            })


    return {
        "ok": True,
        "task_id": task_id,
        "status": latest_rec["status"] if latest_rec else "unknown",
        "last_event": last_event,
        "result": res_data,
        "replay_events": list(runtime.replay_events),
        "orchestration": orch_evidence,
    }


@tasks_bp.route("/api/task/<tid>/execute", methods=["POST"])
@tasks_bp.route("/api/tasks/<tid>/execute", methods=["POST"])
@tasks_bp.route("/api/task/<tid>/run", methods=["POST"])
@tasks_bp.route("/api/tasks/<tid>/run", methods=["POST"])
@token_required
def execute_task(tid: str):
    """Execute a persisted task through the authoritative TaskExecutionContext and RealAgentRuntime pipeline."""
    tenant_id = _get_tenant()
    data = request.get_json(silent=True) or {}
    sync = data.get("sync", True)

    record = durable_state.get(tid, tenant_id)
    if not record:
        return jsonify({"ok": False, "error": "Task not found"}), 404

    curr_status = (record.get("status") or "").upper()
    if curr_status in durable_state.TERMINAL_STATUSES:
        return jsonify({"ok": False, "error": f"Task already in terminal status '{curr_status}'"}), 409

    cancel_ev = register_cancellation(tid)

    if sync:
        try:
            res = run_task_pipeline(tid, tenant_id, cancel_event=cancel_ev)
            return jsonify(res)
        except Exception as exc:
            logger.exception("Failed to execute task %s: %s", tid, exc)
            return jsonify({"ok": False, "error": str(exc)}), 500
    else:
        t = threading.Thread(
            target=run_task_pipeline,
            args=(tid, tenant_id, cancel_ev),
            name=f"TaskExec-{tid}",
            daemon=True,
        )
        t.start()
        return jsonify({
            "ok": True,
            "task_id": tid,
            "status": "running",
            "message": "Task execution launched in background",
        }), 202


@tasks_bp.route("/api/task/<tid>/diff", methods=["GET"])
@tasks_bp.route("/api/tasks/<tid>/diff", methods=["GET"])
@token_required
def get_task_diff(tid: str):
    """Retrieve verified diff for a task."""
    tenant_id = _get_tenant()
    record = durable_state.get(tid, tenant_id)
    if not record:
        return jsonify({"ok": False, "error": "Task not found"}), 404

    result = json.loads(record.get("result_json") or "{}") if record.get("result_json") else {}
    payload = json.loads(record.get("payload_json") or "{}")

    diff_text = result.get("diff", "")
    if not diff_text and payload.get("worktree_id"):
        try:
            from execution.worktree import worktree_diff
            diff_text = worktree_diff(payload["worktree_id"])
        except Exception:
            diff_text = ""

    return jsonify({
        "ok": True,
        "task_id": tid,
        "diff": diff_text,
        "lines": diff_text.splitlines(),
        "files_changed": len([ln for ln in diff_text.splitlines() if ln.startswith("+++ b/")]),
    })


@tasks_bp.route("/api/task/<tid>/review", methods=["GET", "POST"])
@tasks_bp.route("/api/tasks/<tid>/review", methods=["GET", "POST"])
@token_required
def get_task_review(tid: str):
    """Retrieve full review package (diff, verification evidence, replay, repair history) for a task."""
    tenant_id = _get_tenant()
    record = durable_state.get(tid, tenant_id)
    if not record:
        return jsonify({"ok": False, "error": "Task not found"}), 404

    result = json.loads(record.get("result_json") or "{}") if record.get("result_json") else {}
    payload = json.loads(record.get("payload_json") or "{}")

    diff_text = result.get("diff", "")
    if not diff_text and payload.get("worktree_id"):
        try:
            from execution.worktree import worktree_diff
            diff_text = worktree_diff(payload["worktree_id"])
        except Exception:
            pass

    return jsonify({
        "ok": True,
        "task_id": tid,
        "status": record["status"],
        "review_status": result.get("review_status") or record["status"],
        "verification": result.get("verification"),
        "verification_details": result.get("verification_details"),
        "summary": result.get("summary", ""),
        "files": result.get("files", []),
        "diff": diff_text,
        "repair_history": result.get("repair_history", []),
        "hitl_decisions": result.get("hitl_decisions", []),
        "execution_context": result.get("execution_context", {}),
    })


@tasks_bp.route("/api/task/<tid>/accept", methods=["POST"])
@tasks_bp.route("/api/tasks/<tid>/accept", methods=["POST"])
@tasks_bp.route("/api/task/<tid>/promote", methods=["POST"])
@tasks_bp.route("/api/tasks/<tid>/promote", methods=["POST"])
@token_required
def accept_task(tid: str):
    """Explicit human acceptance and promotion boundary for verified task changes."""
    tenant_id = _get_tenant()
    body = request.get_json(silent=True) or {}
    comment = body.get("comment", "")
    target_ref = body.get("target_ref", "HEAD")
    user_id = getattr(g, "user_id", None) or "human_operator"

    record = durable_state.get(tid, tenant_id)
    if not record:
        return jsonify({"ok": False, "error": "Task not found"}), 404

    curr_status = (record.get("status") or "").upper()
    if curr_status not in (durable_state.STATUS_SUCCEEDED, durable_state.STATUS_REVIEW_PENDING):
        return jsonify({
            "ok": False,
            "error": f"Cannot accept task in status '{curr_status}'. Task must be SUCCEEDED or REVIEW_PENDING."
        }), 409

    payload = json.loads(record.get("payload_json") or "{}")
    result = json.loads(record.get("result_json") or "{}") if record.get("result_json") else {}

    promotion_metadata = {
        "promoted": True,
        "branch": payload.get("branch") or result.get("execution_context", {}).get("branch"),
        "target_ref": target_ref,
        "approver": str(user_id),
        "timestamp": time.time(),
        "status": durable_state.STATUS_ACCEPTED,
    }
    result["review_decision"] = {
        "decision": "accept",
        "approver": str(user_id),
        "comment": comment,
        "timestamp": time.time(),
    }
    result["promotion_metadata"] = promotion_metadata
    result["review_status"] = "ACCEPTED"

    try:
        updated = durable_state.accept(
            tid,
            tenant_id,
            comment=comment,
            approver=str(user_id),
            result=result,
        )
        return jsonify({
            "ok": True,
            "task_id": tid,
            "status": durable_state.STATUS_ACCEPTED,
            "promotion_metadata": promotion_metadata,
        })
    except durable_state.TransitionError as te:
        return jsonify({"ok": False, "error": str(te)}), 409
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@tasks_bp.route("/api/task/<tid>/reject", methods=["POST"])
@tasks_bp.route("/api/tasks/<tid>/reject", methods=["POST"])
@token_required
def reject_task(tid: str):
    """Explicit human rejection of task changes; prunes isolated worktree and branch."""
    tenant_id = _get_tenant()
    body = request.get_json(silent=True) or {}
    reason = body.get("reason") or body.get("comment", "")
    user_id = getattr(g, "user_id", None) or "human_operator"

    record = durable_state.get(tid, tenant_id)
    if not record:
        return jsonify({"ok": False, "error": "Task not found"}), 404

    curr_status = (record.get("status") or "").upper()
    if curr_status not in (durable_state.STATUS_SUCCEEDED, durable_state.STATUS_REVIEW_PENDING):
        return jsonify({
            "ok": False,
            "error": f"Cannot reject task in status '{curr_status}'. Task must be SUCCEEDED or REVIEW_PENDING."
        }), 409

    payload = json.loads(record.get("payload_json") or "{}")
    result = json.loads(record.get("result_json") or "{}") if record.get("result_json") else {}

    cleanup_info = {}
    wt_id = payload.get("worktree_id") or result.get("execution_context", {}).get("worktree_id")
    branch = payload.get("branch") or result.get("execution_context", {}).get("branch")
    repo_root = payload.get("repo_root") or result.get("execution_context", {}).get("repo_root")
    ws_dir = payload.get("workspace_dir") or result.get("execution_context", {}).get("workspace_dir")

    if wt_id or branch:
        try:
            from execution.agent_runtime.context import TaskExecutionContext, cleanup_execution_worktree
            ctx = TaskExecutionContext(
                task_id=tid,
                objective="rejection_cleanup",
                workspace_dir=ws_dir or Path(repo_root or "."),
                tenant_id=tenant_id,
                repo_root=repo_root,
                worktree_id=wt_id,
                branch=branch,
            )
            cleanup_info = cleanup_execution_worktree(ctx, delete_branch=True)
        except Exception as disc_exc:
            logger.warning("Worktree cleanup failed during rejection: %s", disc_exc)
            cleanup_info = {"ok": False, "error": str(disc_exc)}

    result["review_decision"] = {
        "decision": "reject",
        "reviewer": str(user_id),
        "reason": reason,
        "timestamp": time.time(),
        "cleanup": cleanup_info,
    }
    result["review_status"] = "REJECTED"

    try:
        updated = durable_state.reject(
            tid,
            tenant_id,
            comment=reason,
            reviewer=str(user_id),
            result=result,
        )
        return jsonify({
            "ok": True,
            "task_id": tid,
            "status": durable_state.STATUS_REJECTED,
            "cleanup": cleanup_info,
        })
    except durable_state.TransitionError as te:
        return jsonify({"ok": False, "error": str(te)}), 409
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@tasks_bp.route("/api/v1/agent/invoke", methods=["POST"])
@tasks_bp.route("/api/agent/stream", methods=["POST"])
@token_required
def agent_invoke():
    """Authoritative autonomous agent execution streaming with isolated worktree and TaskExecutionContext."""
    data = request.get_json(silent=True) or {}
    task = (data.get("task") or data.get("prompt") or "").strip()
    if not task:
        return jsonify({"ok": False, "error": "Task is required"}), 400

    model = data.get("model") or os.getenv("DEFAULT_MODEL", "claude-3-5-sonnet")
    session_id = data.get("session_id") or f"cli-{uuid.uuid4().hex[:12]}"
    tenant_id = _get_tenant()

    # Workspace directory resolution
    ws_base = os.path.join(os.getcwd(), "workspace")
    os.makedirs(ws_base, exist_ok=True)
    ws_dir = os.path.join(ws_base, session_id)
    os.makedirs(ws_dir, exist_ok=True)

    exec_constraints = dict(data.get("execution_constraints") or {})
    if "allow_plain_workspace" not in exec_constraints and data.get("allow_plain_workspace") is not None:
        exec_constraints["allow_plain_workspace"] = bool(data["allow_plain_workspace"])
    require_review = data.get("require_review", True)
    exec_constraints.setdefault("require_review", require_review)
    exec_constraints.setdefault("max_iterations", 50)
    exec_constraints.setdefault("max_duration_sec", 600.0)

    # Record task in durable state
    payload = {
        "prompt": task,
        "objective": task,
        "model": model,
        "workspace_id": session_id,
        "tenant_id": tenant_id,
        "repo_id": data.get("repo_id"),
        "repo_root": data.get("repo_root"),
        "base_ref": data.get("base_ref", "HEAD"),
        "execution_constraints": exec_constraints,
        "verification_criteria": dict(data.get("verification_criteria") or {}),
        "execution_budget": dict(data.get("execution_budget") or {}),
        "security_context": dict(data.get("security_context") or {}),
    }
    try:
        record = durable_state.create(
            kind="agent_task",
            tenant_id=tenant_id,
            payload=payload,
        )
        task_id = record["execution_id"]
    except Exception:
        task_id = f"task_{uuid.uuid4().hex[:12]}"

    cancel_event = register_cancellation(task_id)

    def generate_events():
        event_counter = 0
        try:
            from execution.agent_runtime import (
                RealAgentRuntime,
                TaskExecutionContext,
                WorktreeRequiredError,
                attach_execution_worktree,
                get_provider,
            )

            context = TaskExecutionContext(
                task_id=task_id,
                objective=task,
                workspace_dir=ws_dir,
                tenant_id=tenant_id,
                session_id=session_id,
                repo_id=data.get("repo_id"),
                repo_root=data.get("repo_root"),
                base_ref=data.get("base_ref", "HEAD"),
                execution_constraints=exec_constraints,
                verification_criteria=dict(data.get("verification_criteria") or {}),
                execution_budget=dict(data.get("execution_budget") or {}),
                security_context=dict(data.get("security_context") or {}),
            )

            # Enforce worktree required safety contract
            try:
                context = attach_execution_worktree(context, repo_root=context.repo_root)
            except WorktreeRequiredError as wte:
                logger.error("Worktree required policy failed for task %s: %s", task_id, wte)
                durable_state.fail(
                    task_id,
                    tenant_id,
                    failure_class=durable_state.FAILURE_CLASS_QUEUE,
                    result={"error": str(wte)},
                )
                event_counter += 1
                yield f"id: {event_counter}\nevent: message\ndata: {json.dumps({'type': 'error', 'error': str(wte)})}\n\n"
                event_counter += 1
                yield f"id: {event_counter}\nevent: message\ndata: {json.dumps({'type': 'done', 'status': 'failed', 'task_id': task_id, 'error': str(wte)})}\n\n"
                return

            runtime = RealAgentRuntime(
                context=context,
                provider=get_provider(model),
            )

            for event_dict in runtime.run_stream(task, cancel_event=cancel_event):
                event_counter += 1
                raw = json.dumps(event_dict)
                yield f"id: {event_counter}\nevent: message\ndata: {raw}\n\n"
        except Exception as exc:
            logger.exception("Agent runtime stream failure: %s", exc)
            event_counter += 1
            err_payload = json.dumps({"type": "error", "error": str(exc)})
            yield f"id: {event_counter}\nevent: message\ndata: {err_payload}\n\n"
            event_counter += 1
            done_payload = json.dumps({"type": "done", "status": "failed", "task_id": task_id, "error": str(exc)})
            yield f"id: {event_counter}\nevent: message\ndata: {done_payload}\n\n"
        finally:
            unregister_cancellation(task_id)

    response = Response(
        stream_with_context(generate_events()),
        mimetype="text/event-stream",
    )
    response.headers["Content-Type"] = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response
