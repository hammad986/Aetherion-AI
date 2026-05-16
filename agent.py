"""
agent.py - Autonomous Coding Agent v2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Loop: Task → Plan → Execute → Observe → Fix → Loop → Done

Features:
  • Project context injection
  • Long-term learnings injection
  • Error self-fix with retry counter
  • Similar task recall
  • Auto learning capture
  • Real execution logging
"""

import json, re, logging
from collections import defaultdict
from config import Config
from memory import Memory
from router import LLMRouter
from tools  import Tools

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(), logging.FileHandler("agent.log")]
)
logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an expert AI coding agent that autonomously builds software.

{tools_schema}

{project_context}

{learnings}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESPONSE FORMAT — STRICT JSON ONLY:
{{
  "thought":  "What I'm planning or why",
  "action":   "tool_name or null",
  "args":     {{}},
  "output":   "Message to user if no action",
  "done":     false
}}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

AGENT RULES:
1.  Respond ONLY with valid JSON — no markdown, no preamble.
2.  One action per step. Think before acting.
3.  Always read a file before editing it.
4.  After writing code, RUN it to verify it works.
5.  If code fails, FIX it — never give up before {max_retries} attempts.
6.  Set "done": true only when ALL of the following are true:
    (a) The tool action returned success, AND
    (b) The outcome matches the step goal (e.g. file runs, server responds, test passes), AND
    (c) No new errors were introduced.
    Do NOT set "done": true immediately after write_file — first run or test the result.
    Exception: pure setup steps (pip install, git init) may set done=true on tool success.
7.  For web projects: use server_start to launch the server, then server_test or
    browser_navigate to confirm it responds — THEN set done=true.
8.  Write production-quality code. No TODOs, no stubs, no placeholder comments.
9.  If a prior action failed, do NOT repeat the exact same action+args. Change approach.
10. File paths: pass only the filename or relative path inside workspace (e.g. "app.py").
    Never prefix with "workspace/" — the tool resolves it automatically.
11. To install packages: run_shell("pip install <pkg1> <pkg2>").
    Chains like "pip install X && pip install Y" are also supported.
12. When an error is given, read its category and apply the stated strategy exactly.
13. NEVER write the same file a second time in the same step. Once write_file succeeds,
    run or test the file — do not rewrite it without reading it first.
14. NEVER repeat an action that already succeeded in the current step.
    The system tracks which actions have already been completed; duplicates are skipped.
15. Each plan step executes exactly ONCE unless it FAILED. Move forward, not backward.
16. If you are uncertain about the target file, intended behavior, or correct approach:
    set action=null and use "output" to ask ONE focused clarification question.
    Do NOT guess when uncertain — ask.
"""


PLANNER_PROMPT = """You are a software execution planner.

Decompose the user task into {max_steps} or fewer ATOMIC, ordered steps.
Each step is ONE concrete action: write a file, install a package, run a script,
start a server, or test an endpoint.  Never combine multiple actions in one step.

Ordering rules:
- Install dependencies BEFORE running any code.
- Initialize databases BEFORE starting the server.
- Start the server BEFORE running browser tests.
- The LAST step must validate or test the result.

Return STRICT JSON only — no prose, no markdown:
{{
    "steps": [
        "Write requirements.txt listing flask, flask-sqlalchemy, werkzeug",
        "Install dependencies with pip",
        "Write app.py with routes /login /register /dashboard /logout",
        "Write HTML templates: base, login, register, dashboard",
        "Write and run init_db.py to create the SQLite database",
        "Start Flask server on port 5000 using server_start",
        "Test registration and login via browser automation"
    ]
}}

Rules:
- Be specific: name exact files, ports, packages.
- Each step must be independently testable.
- Maximum {max_steps} steps.
"""


class Agent:
    def __init__(
        self,
        config: Config = None,
        memory: Memory = None,
        force_model: str | None = None,
        emit_fn=None,          # ── REALTIME WIRING (Op-1): optional SSE callback
        session_id: str = "",  # ── used to tag emitted events with session context
    ):
        self.config     = config  or Config()
        self.memory     = memory  or Memory()
        self.router     = LLMRouter(config=self.config, force_model=force_model)
        self.tools      = Tools(config=self.config)
        # If emit_fn is not supplied the agent behaves exactly as before (no-op).
        self._emit_fn   = emit_fn if callable(emit_fn) else (lambda kind, payload: None)
        self._session_id = session_id
        # Wire memory explainability — memory emits agent.memory_retrieved
        # for every retrieval when realtime mode is on.
        self.memory._emit_fn = self._emit_record


    # ──────────────────────────────────────────────────────────────────────────
    # Phase 8 — structured records for the outer orchestrator
    # ──────────────────────────────────────────────────────────────────────────
    # Each record is a single-line JSON payload prefixed with [AGENT_RECORD]
    # so the orchestrator (orchestrator.py) can THINK→PLAN→VERIFY→REFLECT
    # around the agent without modifying tools.py / router.py / main.py.
    # CLI users see the extra lines but they're harmless.
    def _emit_record(self, kind: str, payload: dict) -> None:
        """Emit a structured record to stdout (CLI) AND to the realtime SSE bus."""
        try:
            line = json.dumps({"kind": kind, **payload}, default=str)
        except Exception:
            line = json.dumps({"kind": kind, "error": "unserialisable payload"})
        # Preserve original CLI output — always printed.
        print(f"[AGENT_RECORD] {line}", flush=True)
        # ── REALTIME WIRING (Op-1): forward to SSE bus when enabled ──────────
        import os as _os
        if _os.getenv("AETHERION_REALTIME_V1", "").lower() == "true":
            try:
                self._emit_fn(f"agent.{kind}", {**payload, "session_id": self._session_id})
            except Exception:
                pass  # Never let SSE errors interrupt the agent loop

    # ──────────────────────────────────────────────────────────────────────────
    # PUBLIC: run a task
    # ──────────────────────────────────────────────────────────────────────────
    def run(self, task: str) -> str:
        logger.info(f"\n{'='*55}\nTASK: {task}\n{'='*55}")
        print(f"\n{'━'*55}")
        print(f"🎯 TASK: {task}")
        print(f"{'━'*55}")
        # ── REALTIME WIRING (Op-1): emit task.start event ─────────────────────
        import os as _os
        _realtime_on = _os.getenv("AETHERION_REALTIME_V1", "").lower() == "true"
        if _realtime_on:
            try:
                self._emit_fn("agent.task_start", {"task": task[:200], "session_id": self._session_id})
            except Exception:
                pass

        # ── Trust Engine: pre-task clarification check ───────────────────
        try:
            from trust_engine import ClarificationEngine, AssumptionExposer
            _clarifier   = ClarificationEngine(workspace_dir=str(self.tools.workspace))
            _assumptioner = AssumptionExposer(
                emit_fn=self._emit_fn if _realtime_on else None,
                session_id=self._session_id,
            )
            _workspace_files = (
                self.tools.list_files().get("output", "").splitlines()
            )
            _clarity = _clarifier.score_task(task, _workspace_files)
            if _clarity["should_pause"] and _clarity["question"]:
                logger.info(f"[Trust] Ambiguous task (confidence={_clarity['confidence']:.0%}). Emitting HITL.")
                print(f"  [TRUST] Task ambiguity detected ({int(_clarity['confidence']*100)}%). "
                      f"Question: {_clarity['question']}")
                if _realtime_on:
                    try:
                        from execution.hitl import global_hitl_tracker
                        hitl_payload = {
                            "prompt": _clarity["question"],
                            "last_error": "",
                            "hitl_type": "clarification",
                            "context": {
                                "confidence": _clarity["confidence"],
                                "ambiguities": _clarity["ambiguities"],
                                "task_preview": task[:100],
                            },
                        }
                        hint = global_hitl_tracker.request_input(
                            self._session_id, hitl_payload, timeout=300
                        )
                        if hint and hint.strip():
                            task = task + f"\n[User clarification: {hint.strip()}]"
                            print(f"  [TRUST] Clarification received, proceeding.")
                    except Exception as _he:
                        logger.warning(f"[Trust] HITL clarification failed: {_he}. Proceeding with assumption.")
        except ImportError:
            _clarifier    = None
            _assumptioner = None
            _workspace_files = []

        # ── Semantic Validation Engine ────────────────────────────────────
        try:
            from semantic_validator import SemanticValidator, SelfTestGenerator, ConfidenceModelV2
            _sem_validator   = SemanticValidator(
                tools=self.tools,
                workspace_dir=str(self.tools.workspace),
                emit_fn=self._emit_fn if _realtime_on else None,
                session_id=self._session_id,
            )
            _self_tester     = SelfTestGenerator(
                tools=self.tools,
                workspace_dir=str(self.tools.workspace),
            )
            _conf_model      = ConfidenceModelV2()
        except ImportError:
            _sem_validator = None
            _self_tester   = None
            _conf_model    = None

        # ── Execution Planning Intelligence ───────────────────────────────
        try:
            from execution_planner import (
                ExecutionDAG, MilestoneTracker, AdaptiveReplanner,
                StrategyMemory, PlannerAudit, ReplanDecision,
            )
            _exec_dag    = ExecutionDAG()
            _milestones  = MilestoneTracker()
            _audit       = PlannerAudit()
            _strat_mem   = StrategyMemory(
                workspace_dir=str(self.tools.workspace),
                emit_fn=self._emit_fn if _realtime_on else None,
                session_id=self._session_id,
            )
        except ImportError:
            _exec_dag = _milestones = _audit = _strat_mem = None
            ReplanDecision = None

        # ── Z27A: Initialize Z26 cognitive runtime modules ────────────────────
        try:
            from runtime.context_compression import get_session_context as _z26_get_ctx
            from runtime.confidence_engine import (
                score_step as _z26_score_step,
                get_tracker as _z26_get_tracker,
            )
            from runtime.explainability import (
                explain_model_selection  as _z26_explain_model,
                explain_retry            as _z26_explain_retry,
                explain_escalation       as _z26_explain_escalation,
                explain_replanning       as _z26_explain_replan,
                explain_provider_switch  as _z26_explain_provider,
                explain_context_compression as _z26_explain_compress,
            )
            _z26_ctx          = _z26_get_ctx(self._session_id)
            _z26_conf_tracker = _z26_get_tracker(self._session_id)
            _z26_enabled      = True
        except Exception as _z26_init_err:
            logger.debug("[Z27] Runtime modules not available: %s", _z26_init_err)
            _z26_ctx = _z26_conf_tracker = _z26_enabled = None
            _z26_score_step = _z26_explain_model = _z26_explain_retry = None
            _z26_explain_escalation = _z26_explain_replan = _z26_explain_provider = None
            _z26_explain_compress = None

        # ── Z29: Initialize Operator Control + Mission Governance runtime ──────
        _z29_mc     = None  # mission_control module
        _z29_ov     = None  # override_engine module
        _z29_rec    = None  # mission_recovery module
        _emit_z29   = self._emit_fn if _realtime_on else None
        try:
            from runtime import mission_control  as _z29_mc
            from runtime import override_engine  as _z29_ov
            from runtime import mission_recovery as _z29_rec
            _z29_mc.register_mission(self._session_id)
            logger.debug("[Z29] Mission control, override engine, and recovery monitor active.")
        except Exception as _z29_init_err:
            logger.debug("[Z29] Runtime governance modules not available: %s", _z29_init_err)
            _z29_mc = _z29_ov = _z29_rec = None

        # ── Recall similar tasks ─────────────────────────
        similar = self.memory.find_similar_task(task)
        if similar:
            print(f"  📚 Found {len(similar)} similar past task(s)")

        self.memory.add_message("user", task)

        system = SYSTEM_PROMPT.format(
            tools_schema    = Tools.schema(),
            project_context = self.memory.project_context_prompt(),
            learnings       = self.memory.learnings_prompt(task),
            max_retries     = self.config.MAX_RETRIES,
        )

        checkpoint = self.memory.load_checkpoint(task)
        if checkpoint:
            plan_steps    = checkpoint.get("plan_steps") or self._plan_task(task)
            plan_stages   = checkpoint.get("plan_stages", [])
            step_stage_map= checkpoint.get("step_stage_map", {})
            completed_steps = set(checkpoint.get("completed_steps", []))
            current_step  = min(int(checkpoint.get("current_step", 0)), max(len(plan_steps) - 1, 0))
            loop_count    = int(checkpoint.get("loop_count", 0))
            error_count   = int(checkpoint.get("error_count", 0))
            last_output   = checkpoint.get("last_output", "")
            last_error    = checkpoint.get("last_error", "")
            state_stack   = checkpoint.get("state_stack", [])
            blocked_attempts = defaultdict(set)
            for k, v in checkpoint.get("blocked_attempts", {}).items():
                blocked_attempts[int(k)] = set(v)
            print(f"  Resuming: step {current_step + 1}/{max(len(plan_steps), 1)}, {len(completed_steps)} steps already done")
        else:
            plan_stages, plan_steps, step_stage_map = self._plan_staged_task(task)
            current_step  = 0
            loop_count    = 0
            error_count   = 0
            last_output   = ""
            last_error    = ""
            state_stack   = []
            blocked_attempts  = defaultdict(set)
            completed_steps   = set()   # step strings confirmed done
        # Per-step success tracker -- prevents re-executing already-succeeded actions
        step_completed_actions: dict = defaultdict(set)  # {step_idx: {fingerprint, ...}}
        # Execution counters (always start fresh -- not persisted in checkpoint)
        step_loop_count = 0   # loops within the current step (resets on step advance)
        total_failures  = 0   # cumulative errors across all steps this session

        # ── Build execution DAG from plan ────────────────────────────
        if _exec_dag and plan_steps:
            try:
                _exec_dag.build_from_plan(plan_steps, plan_stages)
                # Emit initial DAG state
                if _realtime_on:
                    self._emit_fn("agent.dag_update", {
                        **_exec_dag.to_sse_payload(),
                        "session_id": self._session_id,
                    })
            except Exception as _de:
                logger.warning(f"[ExecutionDAG] Build failed: {_de}")

        # Emit strategy caution signals BEFORE execution starts
        if _strat_mem:
            try:
                _strat_mem.emit_caution_signals(task, step=0)
            except Exception:
                pass

        if not plan_steps:
            plan_steps = [task]

        # -- Stage-aware plan display --
        if plan_stages:
            print("\n  Plan (staged):")
            for si, stage_obj in enumerate(plan_stages, 1):
                sname = stage_obj.get("stage", f"stage{si}").upper()
                print(f"   Stage {si}/{len(plan_stages)}: {sname}")
                for step_str in stage_obj.get("steps", []):
                    idx = next((i for i, s in enumerate(plan_steps) if s == step_str), -1)
                    mark = "OK" if step_str in completed_steps else (">>" if idx == current_step else " .")
                    num  = idx + 1 if idx >= 0 else "?"
                    print(f"     [{mark}] {num}. {step_str}")
        else:
            print("\n  Plan:")
            for i, step in enumerate(plan_steps, 1):
                mark = "OK" if step in completed_steps else (">>" if i - 1 == current_step else " .")
                print(f"   [{mark}] {i}. {step}")

        def persist_checkpoint():
            self.memory.save_checkpoint({
                "task":             task,
                "plan_steps":       plan_steps,
                "plan_stages":      plan_stages,
                "step_stage_map":   step_stage_map,
                "completed_steps":  sorted(completed_steps),
                "current_step":     current_step,
                "loop_count":       loop_count,
                "error_count":      error_count,
                "last_output":      last_output,
                "last_error":       last_error,
                "state_stack":      state_stack[-20:],
                "blocked_attempts": {str(k): sorted(v) for k, v in blocked_attempts.items()},
            })

        try:
            while (
                loop_count    < self.config.MAX_AGENT_LOOPS
                and current_step < len(plan_steps)
                and total_failures < self.config.MAX_TOTAL_ATTEMPTS
            ):
                # -- Early exit: all steps already completed --
                if len(completed_steps) >= len(plan_steps) and plan_steps:
                    logger.info("[Agent] All steps completed -- early exit")
                    print("  All steps completed, exiting loop")
                    break

                loop_count     += 1
                step_loop_count += 1

                # ── Z29: check operator control signal between steps ──────────
                if _z29_mc:
                    try:
                        _z29_mc.on_loop_tick(self._session_id) if _z29_rec else None
                        _z29_sig = _z29_mc.check_signal(self._session_id)
                        if _z29_sig == _z29_mc.MissionSignal.CANCEL:
                            logger.info("[Z29] CANCEL signal received — terminating execution loop")
                            break
                        if _z29_sig == _z29_mc.MissionSignal.PAUSE:
                            logger.info("[Z29] PAUSE signal — waiting for resume…")
                            _resumed = _z29_mc.wait_if_paused(self._session_id)
                            if not _resumed:
                                logger.info("[Z29] Timeout/cancel while paused — terminating")
                                break
                        # Drain injected instructions and append to context
                        _injected = _z29_mc.drain_inject_queue(self._session_id)
                        if _injected and self.memory:
                            for _inj in _injected:
                                self.memory.add_message("user", f"[Operator Instruction] {_inj}")
                        # Replan signal — trigger replan on next iteration
                        if _z29_sig == _z29_mc.MissionSignal.REPLAN:
                            logger.info("[Z29] REPLAN signal — resetting step to trigger replanning")
                            last_error = "Operator requested replanning"
                    except Exception as _z29_sig_err:
                        logger.debug("[Z29] Signal check error: %s", _z29_sig_err)

                active_step = plan_steps[current_step]

                # -- Step-skip guard: jump over already-completed steps immediately --
                if active_step in completed_steps:
                    print(f"  [skip] Step {current_step+1} already done: {active_step[:60]}")
                    if current_step < len(plan_steps) - 1:
                        current_step    += 1
                        error_count      = 0
                        step_loop_count  = 0
                        self.router._gemini_use_pro = False
                    else:
                        break
                    persist_checkpoint()
                    continue

                logger.info(
                    f"[Agent] Loop {loop_count}/{self.config.MAX_AGENT_LOOPS} | "
                    f"Step {current_step + 1}/{len(plan_steps)} | Stage: {step_stage_map.get(active_step, 'n/a')} | "
                    f"Failures {total_failures}/{self.config.MAX_TOTAL_ATTEMPTS}"
                )

                # ── DAG: mark step as running; audit for chaos ──
                _step_advanced_this_loop = False
                if _exec_dag:
                    try:
                        _exec_dag.start(current_step)
                    except Exception:
                        pass
                if _audit:
                    try:
                        _chaos = _audit.record_loop(
                            step_index=current_step,
                            action="(pending)",
                            fingerprint=f"{current_step}:{step_loop_count}",
                            step_advanced=False,
                        )
                        for _cs in _chaos:
                            print(f"  [PLANNER AUDIT] {_cs}")
                            if _realtime_on:
                                self._emit_fn("agent.trust_signal", {
                                    "type": "contradiction",
                                    "verified": False,
                                    "confidence": 0.20,
                                    "message": _cs,
                                    "step": current_step,
                                    "action": "audit",
                                    "session_id": self._session_id,
                                })
                    except Exception:
                        pass

                # -- Pattern memory injection: prepend top-1 relevant learning --
                top_pattern = self.memory.learnings_prompt(active_step)
                pattern_hint = f"\n[Memory] Relevant pattern: {top_pattern[:200]}" if top_pattern and top_pattern.strip() != "No learnings yet." else ""

                # Context minimization: last 6 messages only (~3x fewer tokens per call)
                messages = self.memory.get_messages(last_n=6)
                messages.append({
                    "role": "user",
                    "content": self._step_instruction(
                        current_step, plan_steps,
                        blocked_attempts[current_step],
                        last_error=last_error,
                        stage_name=step_stage_map.get(active_step, ""),
                        completed_count=len(completed_steps),
                    ) + pattern_hint,
                })

                try:
                    raw = self.router.chat(messages, system=system)
                except RuntimeError as e:
                    msg = f"LLM unavailable: {e}"
                    logger.error(msg)
                    persist_checkpoint()
                    self.memory.log_task(task, "error", msg)
                    return msg

                # ── REALTIME: emit budget_update after every LLM call ─────────
                import os as _os_budget
                if _os_budget.getenv("AETHERION_REALTIME_V1", "").lower() == "true":
                    try:
                        _total_tokens = sum(
                            getattr(h, "total_tokens", 0)
                            for h in self.router.health.values()
                        )
                        self._emit_record("agent.budget_update", {
                            "tokens":   _total_tokens,
                            "token_max": getattr(self.config, "MAX_TOKENS", 100000),
                            "steps":    len(completed_steps),
                            "step_max": self.config.MAX_AGENT_LOOPS,
                            "model":    self.router.last_used_api or "unknown",
                        })
                    except Exception:
                        pass

                # ── Z27A: Model selection explanation + context feed ──────────
                if _z26_enabled:
                    try:
                        _used_model = self.router.last_used_api or "unknown"
                        _z26_rec_model = _z26_explain_model(
                            self._session_id,
                            f"step-{current_step}",
                            model=_used_model,
                            reason="selected by router based on plan mode and provider health",
                            factors=[
                                f"step={current_step + 1}/{len(plan_steps)}",
                                f"provider={_used_model}",
                                f"errors_so_far={error_count}",
                            ],
                            confidence=0.85,
                        )
                        if _realtime_on and _z26_rec_model:
                            self._emit_fn("agent.explain", _z26_rec_model.to_dict())
                        _z26_ctx.add_message("user", active_step[:600])
                        _z26_ctx.add_message("assistant", (raw or "")[:600])
                        _ctx_usage = _z26_ctx.token_usage()
                        if _realtime_on:
                            self._emit_fn("agent.context_state", {
                                "token_pct":   _ctx_usage["budget_pct"],
                                "total_tokens": _ctx_usage["total"],
                                "episodes":    len(_z26_ctx._episodes),
                                "session_id":  self._session_id,
                            })
                    except Exception:
                        pass

                parsed = self._parse(raw)
                if not parsed:
                    logger.warning(f"[Agent] Bad JSON:\n{raw[:200]}")
                    self.memory.add_message("assistant", raw)
                    self.memory.add_message(
                        "user",
                        "Your response is not valid JSON. Reply ONLY with a JSON object.",
                    )
                    persist_checkpoint()
                    continue

                thought = parsed.get("thought", "")
                action = parsed.get("action")
                args = parsed.get("args", {})
                output = parsed.get("output", "")
                done = parsed.get("done", False)

                # ── Trust Engine: Contradiction Detection ─────────────────────
                # Catch: done=True but last action failed (LLM hallucinating success)
                _realtime = _os.getenv("AETHERION_REALTIME_V1", "").lower() == "true"
                if done and action and result if 'result' in dir() else False:
                    pass  # evaluated below after result is available

                if thought:
                    print(f"\n  💭 {thought}")
                    # ── REALTIME WIRING (Op-1): stream thought token ──────────
                    import os as _os
                    if _os.getenv("AETHERION_REALTIME_V1", "").lower() == "true":
                        try:
                            self._emit_fn("agent.think", {
                                "thought": thought,
                                "step_index": current_step,
                                "step_text": active_step,
                                "session_id": self._session_id,
                            })
                        except Exception:
                            pass

                if action:
                    fingerprint = self._fingerprint_action(action, args)

                    # ── Guard 1: skip actions that already FAILED this step ──────────
                    if fingerprint in blocked_attempts[current_step]:
                        observation = (
                            f"Action '{action}' with identical args already failed for step '{active_step}'. "
                            "Choose a different tool or different arguments."
                        )
                        self.memory.add_message("assistant", json.dumps(parsed))
                        self.memory.add_message("user", f"Observation: {observation}")
                        persist_checkpoint()
                        continue

                    # ── Guard 2: skip actions that already SUCCEEDED this step ───────
                    if fingerprint in step_completed_actions[current_step]:
                        print(f"  ⏭ {action} already completed for this step — auto-advancing")
                        observation = (
                            f"[SYSTEM] Action '{action}' already succeeded in the current step '{active_step}'. "
                            f"Do NOT repeat it. Set \"done\": true now to advance to the next step."
                        )
                        self.memory.add_message("assistant", json.dumps(parsed))
                        self.memory.add_message("user", f"Observation: {observation}")
                        # Force-advance immediately — the LLM is stuck in a re-write loop
                        if current_step < len(plan_steps) - 1:
                            current_step    += 1
                            error_count      = 0
                            step_loop_count  = 0
                            self.router._gemini_use_pro = False
                            transition = (
                                f"Step auto-advanced. Now on step {current_step + 1}/{len(plan_steps)}: "
                                f"{plan_steps[current_step]}"
                            )
                            self.memory.add_message("user", transition)
                        persist_checkpoint()
                        continue

                    args_preview = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
                    print(f"  🔧 {action}({args_preview})")

                    # ── Trust Engine: pre-action assumption exposure ───────
                    if _assumptioner:
                        _assumptions = _assumptioner.expose_before_action(
                            action=action,
                            args=args,
                            step_text=active_step,
                            step_index=current_step,
                            workspace_files=_workspace_files,
                        )
                        if _assumptions:
                            banner = _assumptioner.format_assumption_banner(_assumptions, action)
                            if banner:
                                print(f"  {banner}")
                    # ── REALTIME WIRING (Op-1): emit agent.action event ───────
                    import os as _os
                    if _os.getenv("AETHERION_REALTIME_V1", "").lower() == "true":
                        try:
                            self._emit_fn("agent.action", {
                                "tool": action,
                                "args": {k: str(v)[:120] for k, v in (args or {}).items()},
                                "step_index": current_step,
                                "step_text": active_step,
                                "session_id": self._session_id,
                            })
                        except Exception:
                            pass

                    snapshot = self._snapshot_before_action(
                        action=action,
                        args=args,
                        current_step=current_step,
                        last_output=last_output,
                        error_count=error_count,
                    )
                    state_stack.append(snapshot)

                    result = self.tools.execute(action, **args)
                    success = result["success"]
                    out = result["output"]
                    err = result["error"]

                    if success:
                        print(f"  ✓ {out[:250]}")
                        observation = f"TOOL '{action}' SUCCEEDED for step '{active_step}':\n{out}"
                        error_count = 0
                        last_output = out

                        # ── Trust Engine: contradiction guard ─────────────────
                        # If LLM set done=True on a write_file without a verify
                        # step following, inject a verification nudge.
                        _is_code_write = action in ("write_file", "diff_edit",
                            "search_replace", "ast_replace")
                        _written_path = (args or {}).get("path", "")
                        _needs_verify = (
                            _is_code_write
                            and done
                            and _written_path.endswith((".py", ".js", ".ts", ".sh"))
                            and current_step < len(plan_steps) - 1  # not final step
                        )
                        if _needs_verify:
                            done = False  # override premature done
                            observation += (
                                "\n\n[TRUST ENGINE] File written but not yet verified. "
                                f"You must run or test '{_written_path}' before setting done=true. "
                                "Use run_python or run_shell to confirm it executes without errors."
                            )
                            print(f"  [TRUST] Overrode done=true — verification required for {_written_path}")

                        # ── REALTIME WIRING: emit tool success + trust signal ──
                        import os as _os
                        if _os.getenv("AETHERION_REALTIME_V1", "").lower() == "true":
                            try:
                                _file_mutating = action in (
                                    "write_file", "diff_edit", "search_replace",
                                    "delete_file", "ast_replace",
                                )
                                _evt = "file.modified" if _file_mutating else "agent.tool_success"
                                self._emit_fn(_evt, {
                                    "tool": action,
                                    "step_index": current_step,
                                    "output": str(out)[:300],
                                    "path": (args or {}).get("path", ""),
                                    "session_id": self._session_id,
                                })
                                # Emit assumption/trust signal for file mutations
                                if _file_mutating and _written_path:
                                    self._emit_fn("agent.trust_signal", {
                                        "type": "verification" if _needs_verify else "action_success",
                                        "verified": not _needs_verify,
                                        "confidence": 0.6 if _needs_verify else 0.85,
                                        "message": (
                                            f"Written {_written_path} — awaiting run verification"
                                            if _needs_verify
                                            else f"Written {_written_path}"
                                        ),
                                        "step": current_step,
                                        "action": action,
                                        "session_id": self._session_id,
                                    })
                            except Exception:
                                pass

                        # ── Semantic Validation: post-success behavioral check ──
                        _sem_result = None
                        _SEMANTICALLY_VALIDATED = {
                            "write_file", "run_python", "run_shell",
                            "server_start", "server_test",
                            "browser_navigate", "browser_click", "browser_fill",
                        }
                        if _sem_validator and action in _SEMANTICALLY_VALIDATED:
                            try:
                                _sem_result = _sem_validator.validate_after(
                                    action=action,
                                    args=args or {},
                                    tool_output=out,
                                    step_text=active_step,
                                    step_index=current_step,
                                )
                                if _sem_result:
                                    # Feed into confidence model
                                    if _conf_model:
                                        _conf_model.record(
                                            "semantic_validation",
                                            passed=_sem_result.passed(),
                                            score=_sem_result.semantic_confidence,
                                        )
                                    # If semantic check fails hard, force re-try
                                    if not _sem_result.passed() and not _sem_result.retryable is False:
                                        done = False
                                        _fail_names = [
                                            c.name for c in _sem_result.checks
                                            if not c.passed and not c.optional
                                        ]
                                        observation += (
                                            f"\n\n[SEMANTIC VALIDATOR] Tool succeeded but behavioral "
                                            f"checks failed: {', '.join(_fail_names[:3])}.\n"
                                            f"Category: {_sem_result.failure_category or 'behavioral'}.\n"
                                            f"Evidence: {_sem_result.summary}\n"
                                            "Fix the root cause before setting done=true."
                                        )
                                        print(f"  [SEMANTIC] FAIL — {_sem_result.summary[:120]}")
                            except Exception as _sve:
                                logger.warning(f"[SemanticValidator] Error: {_sve}")

                        # ── Self-test for Python files ──────────────────────
                        if (_self_tester and action == "write_file"
                                and (args or {}).get("path", "").endswith(".py")
                                and "[syntax:OK]" in out):
                            try:
                                _st = _self_tester.generate_and_run(
                                    path=(args or {}).get("path", ""),
                                    content=(args or {}).get("content", ""),
                                )
                                if _conf_model:
                                    _conf_model.record("self_test", passed=_st["passed"])
                                if not _st["passed"]:
                                    done = False
                                    observation += (
                                        f"\n\n[SELF-TEST] Smoke test FAILED for {(args or {}).get('path')}.\n"
                                        f"Evidence: {_st['evidence'][:200]}\n"
                                        "The file has a runtime import or logic error. Fix it."
                                    )
                                    print(f"  [SELF-TEST] FAIL: {_st['evidence'][:100]}")
                                else:
                                    print(f"  [SELF-TEST] PASS: {_st['evidence'][:80]}")
                            except Exception as _ste:
                                logger.warning(f"[SelfTest] Error: {_ste}")

                        # Record confidence from execution success
                        if _conf_model:
                            _conf_model.record("execution_success", passed=True, score=1.0)
                            # Syntax check signal from tool output
                            if "[syntax:OK]" in out:
                                _conf_model.record("syntax_check", passed=True, score=1.0)
                            elif "[syntax:ERROR" in out:
                                _conf_model.record("syntax_check", passed=False, score=0.0)

                        # Record action + step as completed
                        step_completed_actions[current_step].add(fingerprint)
                        completed_steps.add(active_step)

                        # \u2500\u2500 DAG: mark complete + emit milestone \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n                        _step_advanced_this_loop = True\n                        _sem_conf = _sem_result.semantic_confidence if _sem_result else 0.85\n                        if _exec_dag:\n                            try:\n                                _exec_dag.complete(current_step, semantic_confidence=_sem_conf)\n                                if _realtime_on:\n                                    self._emit_fn(\"agent.dag_update\", {\n                                        **_exec_dag.to_sse_payload(),\n                                        \"session_id\": self._session_id,\n                                    })\n                            except Exception:\n                                pass\n                        if _milestones:\n                            try:\n                                _new_ms = _milestones.evaluate_step(current_step, active_step, out)\n                                for _ms_name in _new_ms:\n                                    print(f\"  \u2713 Milestone: {_ms_name}\")\n                                    if _realtime_on:\n                                        self._emit_fn(\"agent.trust_signal\", {\n                                            \"type\": \"action_success\",\n                                            \"verified\": True,\n                                            \"confidence\": _milestones.progress_ratio(),\n                                            \"message\": f\"Milestone: {_ms_name.replace('_', ' ').title()} achieved\",\n                                            \"step\": current_step,\n                                            \"action\": \"milestone\",\n                                            \"session_id\": self._session_id,\n                                        })\n                                if _realtime_on and _new_ms:\n                                    self._emit_fn(\"agent.milestone_update\", {\n                                        **_milestones.to_sse_payload(),\n                                        \"session_id\": self._session_id,\n                                    })\n                            except Exception:\n                                pass\n

                        # Log stage completion when last step of a stage finishes
                        if plan_stages and step_stage_map.get(active_step):
                            sn = step_stage_map[active_step]
                            so = next((s for s in plan_stages if s["stage"] == sn), None)
                            if so and all(s in completed_steps for s in so.get("steps", [])):
                                print(f"  [Stage DONE] {sn.upper()} complete")
                                logger.info(f"[Agent] Stage '{sn}' completed")

                        if last_error and action in ("run_python", "run_shell", "write_file", "diff_edit"):
                            self.memory.add_learning(
                                "error_fix",
                                f"Fixed '{last_error[:120]}' via {action} ({active_step[:80]})",
                            )
                            last_error = ""

                        if len(state_stack) > 40:
                            state_stack = state_stack[-40:]

                        # If LLM forgot to set done=True on a non-code step, nudge it
                        if not done and not _needs_verify:
                            observation += (
                                f"\n\n[SYSTEM] The action SUCCEEDED. "
                                f"Step goal: '{active_step}'.\n"
                                "If this step is now complete AND you have verified the result, "
                                "set \"done\": true in your next response."
                            )
                    else:
                        print(f"  ❌ {err[:250]}")
                        blocked_attempts[current_step].add(fingerprint)
                        category = self._categorize_error(err)
                        strategy = self._strategy_for_error(category)
                        rollback_msg = self._rollback_from_snapshot(state_stack.pop() if state_stack else None)

                        observation = (
                            f"TOOL '{action}' FAILED on step '{active_step}':\n{err}\n"
                            f"Error category: {category}. Strategy: {strategy}.\n"
                            f"Rollback: {rollback_msg}.\n"
                            f"Fix attempt {error_count + 1}/{self.config.PER_STEP_RETRY}. "
                            "Do not repeat the same action+args."
                        )
                        error_count   += 1
                        last_error     = err
                        total_failures += 1

                        # ── Z27A: Confidence scoring + retry explanation ───────
                        if _z26_enabled:
                            try:
                                _z26_report = _z26_score_step(
                                    self._session_id,
                                    f"step-{current_step}-err{error_count}",
                                    output_text=err,
                                    retry_count=error_count,
                                    tool_failures=1,
                                    evidence_count=max(1, len(completed_steps)),
                                )
                                _z26_conf_tracker.record(_z26_report)
                                _z26_rec_retry = _z26_explain_retry(
                                    self._session_id,
                                    f"step-{current_step}",
                                    attempt=error_count,
                                    failure_reason=f"{category}: {err[:80]}",
                                    factors=[
                                        f"tool={action}",
                                        f"category={category}",
                                        f"total_failures={total_failures}",
                                    ],
                                )
                                if _realtime_on and _z26_rec_retry:
                                    self._emit_fn("agent.explain", _z26_rec_retry.to_dict())
                                if _z26_report.requires_hitl and _realtime_on:
                                    self._emit_fn("agent.confidence_warning", {
                                        "score":      _z26_report.final_score,
                                        "level":      _z26_report.level,
                                        "alert":      _z26_report.operator_alert,
                                        "session_id": self._session_id,
                                    })
                            except Exception:
                                pass

                        # ── DAG: fail node + adaptive replan ───────────────────
                        if _exec_dag:
                            try:
                                _exec_dag.fail(current_step, err, error_category=category)
                                from execution_planner import AdaptiveReplanner, ReplanDecision
                                _replanner = AdaptiveReplanner(
                                    dag=_exec_dag,
                                    emit_fn=self._emit_fn if _realtime_on else None,
                                    session_id=self._session_id,
                                )
                                _sem_failed = (
                                    _sem_result is not None and not _sem_result.passed()
                                ) if '_sem_result' in dir() else False
                                _rp = _replanner.evaluate(
                                    step_index=current_step,
                                    error_count=error_count,
                                    error_category=category,
                                    semantic_failed=_sem_failed,
                                    is_critical=_exec_dag.nodes.get(current_step, None) and
                                                _exec_dag.nodes[current_step].is_critical_path,
                                    per_step_retry=self.config.PER_STEP_RETRY,
                                )
                                if _rp.injected_steps:
                                    observation += (
                                        f"\n\n[ADAPTIVE REPLAN] Injecting recovery steps: "
                                        + " → ".join(_rp.injected_steps[:2])
                                    )
                                if _rp.decision == ReplanDecision.ESCALATE_HITL and _rp.hitl_prompt:
                                    observation += f"\n\n[REPLAN] Escalating to HITL: {_rp.hitl_prompt}"
                                _replanner.record_replan(current_step, _rp.decision, _rp.reason)
                                # ── Z27A: Replanning explanation ─────────────
                                if _z26_enabled:
                                    try:
                                        _z26_rec_replan = _z26_explain_replan(
                                            self._session_id,
                                            f"step-{current_step}",
                                            original_plan_summary=active_step[:80],
                                            reason=(_rp.reason or "adaptive replanner triggered"),
                                            factors=[
                                                f"decision={_rp.decision}",
                                                f"category={category}",
                                                f"errors={error_count}",
                                            ],
                                        )
                                        if _realtime_on and _z26_rec_replan:
                                            self._emit_fn("agent.explain", _z26_rec_replan.to_dict())
                                    except Exception:
                                        pass
                            except Exception as _rpe:
                                logger.warning(f"[AdaptiveReplanner] Error: {_rpe}")

                        # Escalate to Gemini-pro after GEMINI_PRO_AFTER_FAILURES per-step failures
                        if error_count >= self.config.GEMINI_PRO_AFTER_FAILURES:
                            if not self.router._gemini_use_pro:
                                logger.info(
                                    f"[Agent] Escalating Gemini flash->pro after "
                                    f"{error_count} failures on step {current_step + 1}"
                                )
                            self.router._gemini_use_pro = True
                            # ── Z27A: Provider switch explanation ─────────────
                            if _z26_enabled:
                                try:
                                    _z26_rec_prov = _z26_explain_provider(
                                        self._session_id,
                                        f"step-{current_step}",
                                        from_provider="gemini-flash",
                                        to_provider="gemini-pro",
                                        reason=f"quality escalation after {error_count} failures",
                                        factors=[
                                            f"error_count={error_count}",
                                            f"threshold={self.config.GEMINI_PRO_AFTER_FAILURES}",
                                        ],
                                    )
                                    if _realtime_on and _z26_rec_prov:
                                        self._emit_fn("agent.explain", _z26_rec_prov.to_dict())
                                except Exception:
                                    pass

                        # User intervention after cumulative threshold OR explicit replanner escalation
                        _escalate_hitl = False
                        _hitl_reason = f"Last: [{category.upper()}] {err[:100]}"
                        
                        if total_failures == self.config.USER_INTERVENTION_THRESHOLD:
                            _escalate_hitl = True
                        if '_rp' in locals() and _rp.decision == ReplanDecision.ESCALATE_HITL:
                            _escalate_hitl = True
                            _hitl_reason = _rp.hitl_prompt or "Replanner requested HITL escalation."

                        if _escalate_hitl:
                            print(f"\n  ⚠️  HITL Escalation. {_hitl_reason}")
                            try:
                                from governance_layer import get_governance_layer
                                get_governance_layer().log_audit(
                                    event_type="hitl_escalation",
                                    description=f"Task: {self.task[:100]}... Step: {active_step[:100]}... Reason: {_hitl_reason}",
                                    result="Awaiting operator approval"
                                )
                            except Exception as _gov_err:
                                logger.warning(f"[Agent] Governance log error: {_gov_err}")

                            # ── Z27A: Escalation explanation ─────────────────
                            if _z26_enabled:
                                try:
                                    _z26_conf_avg = (_z26_conf_tracker.rolling_average()
                                                     if _z26_conf_tracker else 0.0)
                                    _z26_rec_esc = _z26_explain_escalation(
                                        self._session_id,
                                        f"step-{current_step}",
                                        trigger=_hitl_reason[:120],
                                        factors=[
                                            f"total_failures={total_failures}",
                                            f"threshold={self.config.USER_INTERVENTION_THRESHOLD}",
                                            f"category={category}",
                                        ],
                                        confidence=_z26_conf_avg,
                                    )
                                    if _realtime_on and _z26_rec_esc:
                                        self._emit_fn("agent.explain", _z26_rec_esc.to_dict())
                                except Exception:
                                    pass

                            if _os.getenv("AETHERION_REALTIME_V1", "").lower() == "true":
                                # ── Web-safe HITL: non-blocking threading.Event wait ──────────
                                # The daemon worker thread blocks here (NOT a gunicorn request
                                # thread). Timeout auto-continues; rejection raises InterruptedError.
                                try:
                                    from execution.hitl import global_hitl_tracker
                                    _hitl_payload = {
                                        "prompt": _hitl_reason,
                                        "last_error": err[:200],
                                        "error_category": category,
                                        "total_failures": total_failures,
                                        "step": active_step[:100],
                                        "session_id": self._session_id,
                                    }
                                    self._emit_fn("hitl.required", _hitl_payload)
                                    _hitl_timeout = int(_os.getenv("HITL_TIMEOUT_SECONDS", "60"))
                                    _hitl_resp = global_hitl_tracker.request_approval(
                                        execution_id=getattr(self, "_execution_id", "unknown"),
                                        payload=_hitl_payload,
                                        timeout_sec=_hitl_timeout,
                                    )
                                    hint = (_hitl_resp.get("feedback") or "").strip()
                                    if hint:
                                        self.memory.add_message("user", f"[USER HINT]: {hint}")
                                        observation += f"\n[USER HINT from operator]: {hint}"
                                        logger.info(f"[Agent] HITL hint received: {hint[:100]}")
                                    elif _hitl_resp.get("status") == "timeout":
                                        logger.info("[Agent] HITL timeout — auto-continuing.")
                                    elif _hitl_resp.get("status") == "rejected":
                                        logger.info("[Agent] HITL rejected — terminating task.")
                                        raise InterruptedError("Task rejected by operator via HITL.")
                                except InterruptedError:
                                    raise   # propagate rejection cleanly
                                except Exception as _hitl_err:
                                    logger.warning(f"[Agent] HITL system error: {_hitl_err} — auto-continuing.")
                            else:
                                # ── Legacy CLI mode (original behaviour) ──────────────────────
                                print(
                                    "  💡  Enter a hint/correction (or press Enter to auto-continue): ",
                                    end="", flush=True,
                                )
                                try:
                                    hint = input().strip()
                                    if hint:
                                        self.memory.add_message("user", f"[USER HINT]: {hint}")
                                        observation += f"\n[USER HINT from operator]: {hint}"
                                        logger.info(f"[Agent] User hint received: {hint[:100]}")
                                except (EOFError, KeyboardInterrupt):
                                    pass

                        if error_count >= self.config.PER_STEP_RETRY and current_step > 0:
                            rollback_logs = []
                            # Revert all actions from the current step
                            while state_stack and state_stack[-1].get("step") == current_step:
                                r_msg = self._rollback_from_snapshot(state_stack.pop())
                                if r_msg: rollback_logs.append(r_msg)
                            
                            current_step -= 1
                            error_count   = 0
                            
                            # Revert all actions from the previous step we are returning to, so we can retry it freshly
                            while state_stack and state_stack[-1].get("step") == current_step:
                                r_msg = self._rollback_from_snapshot(state_stack.pop())
                                if r_msg: rollback_logs.append(r_msg)

                            rollback_summary = " ".join(rollback_logs)
                            observation  += f"\nBacktracked to step {current_step + 1} to try a safer alternative path. Restored state: {rollback_summary}"

                        elif error_count >= self.config.PER_STEP_RETRY * 2:
                            msg = f"Too many failures ({error_count}). Last error: {err}"
                            logger.error(msg)
                            # Phase 8 — emit final-failure step_result + reflection
                            # so the orchestrator can decide whether to adapt &
                            # retry the whole sub-task with a new strategy.
                            self._emit_record("step_result", {
                                "step_index": current_step,
                                "step_text":  active_step,
                                "success":    False,
                                "tool":       action,
                                "error_reason": err[:300],
                                "error_category": category,
                                "attempts":   step_loop_count,
                                "summary":    msg[:200],
                            })
                            self._emit_record("reflection", {
                                "step_index":      current_step,
                                "worked":          False,
                                "failed_attempts": step_loop_count,
                                "error_category":  category,
                                "recommendation":  strategy[:200],
                            })
                            try:
                                self.memory.add_learning(
                                    "step_reflection",
                                    f"FAILED step '{active_step[:80]}' "
                                    f"after {step_loop_count} tries — "
                                    f"category={category}; try: {strategy[:120]}",
                                )
                            except Exception:
                                pass
                            persist_checkpoint()
                            self.memory.log_task(task, "failed", msg, self.router.last_used_api or "")
                            return msg

                    self.memory.add_message("assistant", json.dumps(parsed))
                    self.memory.add_message("user", f"Observation: {observation}")

                    if success and done:
                        # Phase 8 — emit success step_result + reflection BEFORE
                        # the counters are reset, so the orchestrator sees the
                        # accurate per-step attempt count.
                        attempts_used = step_loop_count
                        self._emit_record("step_result", {
                            "step_index": current_step,
                            "step_text":  active_step,
                            "success":    True,
                            "tool":       action,
                            "error_reason": None,
                            "attempts":   attempts_used,
                            "summary":    str(out)[:200],
                        })
                        self._emit_record("reflection", {
                            "step_index":      current_step,
                            "worked":          True,
                            "failed_attempts": max(0, attempts_used - 1),
                            "tool":            action,
                            "recommendation":  "keep this approach for similar steps",
                        })
                        if attempts_used > 1:
                            try:
                                self.memory.add_learning(
                                    "step_reflection",
                                    f"OK step '{active_step[:80]}' succeeded "
                                    f"with {action} after {attempts_used} tries",
                                )
                            except Exception:
                                pass
                        if current_step < len(plan_steps) - 1:
                            current_step    += 1
                            error_count      = 0
                            step_loop_count  = 0              # reset for new step
                            self.router._gemini_use_pro = False  # back to flash on success
                            transition = (
                                f"Step completed. Move to step {current_step + 1}/{len(plan_steps)}: "
                                f"{plan_steps[current_step]}"
                            )
                            self.memory.add_message("user", transition)
                        else:
                            persist_checkpoint()
                            break

                    persist_checkpoint()
                    continue

                if output:
                    last_output = output
                    print(f"\n  💬 {output[:400]}")
                    self.memory.add_message("assistant", output)

                if done:
                    if current_step < len(plan_steps) - 1:
                        current_step    += 1
                        error_count      = 0
                        step_loop_count  = 0              # reset for new step
                        self.router._gemini_use_pro = False  # back to flash on success
                        transition = f"Step completed. Move to step {current_step + 1}/{len(plan_steps)}: {plan_steps[current_step]}"
                        self.memory.add_message("assistant", json.dumps(parsed))
                        self.memory.add_message("user", transition)
                        persist_checkpoint()
                        continue
                    break

                persist_checkpoint()

        except Exception as e:
            logger.exception("Agent loop crashed; checkpoint saved")
            persist_checkpoint()
            msg = f"Agent crashed. Checkpoint saved for resume. Error: {e}"
            self.memory.log_task(task, "crashed", msg, self.router.last_used_api or "")
            return msg

        status = "done" if loop_count < self.config.MAX_AGENT_LOOPS else "timeout"
        self.memory.log_task(
            task, status, last_output,
            self.router.last_used_api or "",
            tokens=sum(h.total_tokens for h in self.router.health.values())
        )
        if status in ("done", "timeout"):   # both paths must clear the stale checkpoint
            self.memory.clear_checkpoint()

        # ── Z27A: Emit final runtime telemetry + drop Z26 session state ───────
        if _z26_enabled and _z26_ctx:
            try:
                from runtime.context_compression import drop_session_context as _z26_drop_ctx
                from runtime.confidence_engine import drop_tracker as _z26_drop_tracker
                _z26_final_stats = {
                    "context":    _z26_ctx.stats(),
                    "confidence": (_z26_conf_tracker.summary()
                                   if _z26_conf_tracker else {}),
                    "session_id": self._session_id,
                    "status":     status,
                }
                if _realtime_on:
                    self._emit_fn("agent.runtime_telemetry", _z26_final_stats)
                logger.info(
                    "[Z27] Runtime telemetry | ctx_tokens=%d episodes=%d conf_avg=%.2f",
                    _z26_ctx.token_usage()["total"],
                    len(_z26_ctx._episodes),
                    (_z26_conf_tracker.rolling_average() if _z26_conf_tracker else 1.0),
                )
                _z26_drop_ctx(self._session_id)
                _z26_drop_tracker(self._session_id)
            except Exception as _z26_end_err:
                logger.debug("[Z27] Cleanup error: %s", _z26_end_err)

        # ── Strategy Memory: record outcome for future runs ────────────────
        if _strat_mem:
            try:
                _failure_cats = []
                if _sem_scorecard:
                    _failure_cats = list(_sem_scorecard.get("failure_categories", []))
                _strat_mem.record_outcome(
                    task=task,
                    plan_steps=plan_steps,
                    success=(status == "done" and len(completed_steps) == len(plan_steps)),
                    failure_categories=_failure_cats,
                )
            except Exception:
                pass

        # ── Trust Engine: honest completion report ────────────────────────────
        # Merge semantic confidence into final score
        _sem_scorecard = {}
        if _sem_validator:
            try:
                _sem_scorecard = _sem_validator.session_scorecard()
            except Exception:
                _sem_scorecard = {}

        completion_report = self._build_completion_report(
            task=task,
            status=status,
            completed_steps=completed_steps,
            plan_steps=plan_steps,
            loop_count=loop_count,
            total_failures=total_failures,
            last_output=last_output,
            semantic_scorecard=_sem_scorecard,
        )

        print(f"\n{'━'*55}")
        print(f"Status: {status.upper()} | API: {self.router.last_used_api} | Loops: {loop_count}")
        print(f"Completion: {completion_report[:120]}")
        print(f"{'━'*55}\n")

        import os as _os
        if _os.getenv("AETHERION_REALTIME_V1", "").lower() == "true":
            try:
                confidence = self._completion_confidence(
                    completed_steps, plan_steps, total_failures, status
                )
                self._emit_fn("agent.task_complete", {
                    "status": status,
                    "loops": loop_count,
                    "api": self.router.last_used_api or "",
                    "output": completion_report[:400],
                    "confidence": confidence,
                    "completed_steps": len(completed_steps),
                    "total_steps": len(plan_steps),
                    "session_id": self._session_id,
                })
                self._emit_fn("agent.trust_signal", {
                    "type": "completion",
                    "verified": confidence >= 0.70,
                    "confidence": confidence,
                    "message": completion_report[:200],
                    "step": len(plan_steps),
                    "action": "task_complete",
                    "session_id": self._session_id,
                })
            except Exception:
                pass
        return completion_report

    # ──────────────────────────────────────────────────────────────────────────
    # Simple chat (no tools)
    # ──────────────────────────────────────────────────────────────────────────
    def chat(self, message: str) -> str:
        self.memory.add_message("user", message)
        resp = self.router.chat(self.memory.get_messages())
        self.memory.add_message("assistant", resp)
        return resp

    # ──────────────────────────────────────────────────────────────────────────
    # Trust Engine — Completion Report & Confidence Scoring
    # ──────────────────────────────────────────────────────────────────────────
    def _completion_confidence(
        self,
        completed_steps: set,
        plan_steps: list,
        total_failures: int,
        status: str,
        semantic_scorecard: dict = None,
    ) -> float:
        """
        Returns 0.0–1.0 computed from HARD evidence only.
        Breakdown:
          40% semantic validation scores (behavioral checks)
          30% step completion ratio
          15% failure penalty
          15% status + plan depth
        """
        if not plan_steps:
            return 0.5

        step_ratio      = len(completed_steps) / len(plan_steps)
        failure_penalty = min(0.35, total_failures * 0.07)
        status_adj      = {"done": 0.0, "timeout": -0.20, "crashed": -0.35, "failed": -0.25}.get(status, -0.08)
        plan_bonus      = 0.05 if len(plan_steps) >= 3 else 0.0

        # Semantic validation contribution (strongest signal)
        sem_score = 0.0
        sem_weight = 0.0
        if semantic_scorecard and semantic_scorecard.get("validation_count", 0) > 0:
            sem_score  = semantic_scorecard.get("semantic_confidence", 0.5)
            sem_weight = 0.40  # Semantic checks carry 40% of final confidence

        exec_weight = 1.0 - sem_weight
        exec_score  = step_ratio - failure_penalty + status_adj + plan_bonus
        exec_score  = max(0.0, min(1.0, exec_score))

        confidence = sem_score * sem_weight + exec_score * exec_weight
        return round(max(0.0, min(1.0, confidence)), 2)

    def _build_completion_report(
        self,
        task: str,
        status: str,
        completed_steps: set,
        plan_steps: list,
        loop_count: int,
        total_failures: int,
        last_output: str,
        semantic_scorecard: dict = None,
    ) -> str:
        """
        Evidence-backed completion report. Never claims success without evidence.
        """
        n_done  = len(completed_steps)
        n_total = len(plan_steps)
        sem     = semantic_scorecard or {}
        confidence = self._completion_confidence(
            completed_steps, plan_steps, total_failures, status,
            semantic_scorecard=sem,
        )

        # Build semantic evidence line
        sem_line = ""
        if sem.get("validation_count", 0) > 0:
            sem_line = (
                f" | Semantic: {sem.get('checks_passed', 0)}/{sem.get('checks_run', 0)} checks, "
                f"{int(sem.get('semantic_confidence', 0) * 100)}% behavioral confidence"
            )
            if sem.get("failure_categories"):
                sem_line += f" | Failures: {', '.join(sem['failure_categories'])}"

        if status == "done" and n_done == n_total and n_total > 0 and confidence >= 0.70:
            verb = "successfully" if total_failures == 0 else f"with {total_failures} error(s) self-corrected"
            return (
                f"Task completed {verb}. "
                f"{n_done}/{n_total} steps verified. "
                f"Confidence: {int(confidence*100)}%{sem_line}. "
                f"({last_output[:180] if last_output else 'No output captured.'})"
            )

        if status == "done" and n_done == n_total and confidence < 0.70:
            return (
                f"Task steps executed but confidence is below threshold ({int(confidence*100)}%). "
                f"{n_done}/{n_total} steps ran with {total_failures} failure(s){sem_line}. "
                "Manual review recommended before relying on this output. "
                f"Last output: {last_output[:180] if last_output else 'None.'}"
            )

        if status == "done" and n_done < n_total:
            remaining = [s for s in plan_steps if s not in completed_steps]
            return (
                f"Partial completion: {n_done}/{n_total} steps completed. "
                f"Incomplete: {'; '.join(r[:60] for r in remaining[:3])}. "
                f"Confidence: {int(confidence*100)}%{sem_line}. "
                f"Last output: {last_output[:130] if last_output else 'None.'}"
            )

        if status == "timeout":
            return (
                f"Task timed out after {loop_count} iterations. "
                f"{n_done}/{n_total} steps completed{sem_line}. "
                "The task may be partially complete. Review workspace files manually. "
                f"Last output: {last_output[:130] if last_output else 'None.'}"
            )

        return (
            f"Task ended with status '{status}'. "
            f"{n_done}/{n_total} steps completed. "
            f"{total_failures} failure(s). "
            f"Confidence: {int(confidence*100)}%{sem_line}. "
            f"Last output: {last_output[:130] if last_output else 'No output captured.'}"
        )


    # ──────────────────────────────────────────────────────────────────────────
    # JSON parser
    # ──────────────────────────────────────────────────────────────────────────
    def _parse(self, text: str) -> dict | None:
        text = re.sub(r"^```(?:json)?\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try: return json.loads(m.group())
                except Exception: pass
        return None

    def _plan_task(self, task: str) -> list[str]:
        # Phase 1: phi3:mini local planner (zero-cost, fast, no API call)
        steps = self.router.chat_planner(task)
        if steps:
            logger.info(f"[Agent] Planner: phi3 local ({len(steps)} steps)")
            return steps

        # Phase 2: fall back to main LLM (Gemini-flash or whatever is available)
        planner_system = PLANNER_PROMPT.format(max_steps=self.config.PLANNER_MAX_STEPS)
        try:
            raw = self.router.chat(
                [{"role": "user", "content": task}],
                system=planner_system,
                max_tokens=800,
            )
            parsed = self._parse(raw) or {}
            steps = parsed.get("steps", []) if isinstance(parsed, dict) else []
            steps = [s.strip() for s in steps if isinstance(s, str) and s.strip()]
            if steps:
                logger.info(f"[Agent] Planner: remote LLM ({len(steps)} steps)")
                return steps[: self.config.PLANNER_MAX_STEPS]
        except Exception as e:
            logger.warning(f"Planner failed, using fallback plan: {e}")
        return [task]

    def _plan_staged_task(self, task: str) -> tuple:
        """Phase 2 staged planner.  Returns (plan_stages, plan_steps, step_stage_map).

        Tries chat_staged_planner (phi3 -> /api/generate fallback) first.
        Falls back to flat _plan_task() wrapped in a single 'execution' stage.
        """
        if self.config.PLANNER_STAGED:
            staged = self.router.chat_staged_planner(task)
            if staged:
                plan_steps: list = []
                step_stage_map: dict = {}
                for stage_obj in staged:
                    sname = stage_obj.get("stage", "execution")
                    for s in stage_obj.get("steps", []):
                        if s and s not in plan_steps:
                            plan_steps.append(s)
                            step_stage_map[s] = sname
                if plan_steps:
                    logger.info(
                        f"[Agent] StagedPlanner: {len(staged)} stages, {len(plan_steps)} steps"
                    )
                    return staged, plan_steps, step_stage_map

        # Fallback: flat list wrapped in single execution stage
        flat = self._plan_task(task)
        return [{"stage": "execution", "steps": flat}], flat, {s: "execution" for s in flat}

    def _needs_llm(self, step_loop_count: int, last_error: str, prev_done: bool) -> bool:
        """Execution-first gate: return True only when an LLM call is warranted.

        True  -- first attempt on new step / error needs diagnosis / step not done yet.
        False -- previous action succeeded with done=True (step just advanced;
                  next iteration resets step_loop_count=0 and will call LLM for new step).

        In practice this always returns True in the current loop structure because:
        - step_loop_count is reset to 0 on every step advance
        - last_error is non-empty when any action fails
        The method is here as documented policy and for future deterministic-step shortcuts.
        """
        if step_loop_count <= 1:
            return True   # First attempt -- always needs generation
        if last_error:
            return True   # Error occurred -- LLM must diagnose and fix
        if not prev_done:
            return True   # Step not yet complete -- need next action
        return False      # Advancing to next step; next loop will call LLM with step_loop_count=0

    def _step_instruction(self, current_step: int, plan_steps: list[str],
                          blocked_attempts: set[str], last_error: str = "",
                          stage_name: str = "", completed_count: int = 0) -> str:
        blocked = "\n".join(f"- {x[:160]}" for x in list(blocked_attempts)[-5:]) or "- none"
        error_hint = ""
        variation_hint = ""
        if last_error:
            cat = self._categorize_error(last_error)
            strategy = self._strategy_for_error(cat)
            error_hint = (
                f"\nLast error category : [{cat.upper()}]"
                f"\nRecommended strategy: {strategy}"
            )
            # Phase 8 — retry-with-variation. Suggest concrete alternative
            # tools/approaches instead of just "don't repeat the same args".
            alternatives = self._alternatives_for_category(cat)
            if alternatives:
                variation_hint = (
                    "\nTRY A DIFFERENT APPROACH this attempt:\n  "
                    + "\n  ".join(f"• {a}" for a in alternatives)
                )
        stage_hint = f"\nCurrent stage: {stage_name.upper()}" if stage_name else ""
        return (
            f"Current plan step {current_step + 1}/{len(plan_steps)}: "
            f"{plan_steps[current_step]}\n"
            f"Steps completed so far: {completed_count}/{len(plan_steps)}"
            f"{stage_hint}"
            f"{error_hint}"
            f"{variation_hint}\n"
            "Failed action fingerprints for this step (do not repeat):\n"
            f"{blocked}"
        )

    def _alternatives_for_category(self, category: str) -> list[str]:
        """Phase 8 — concrete alternative actions to suggest on retry."""
        alts = {
            "file_not_found": [
                "Call list_files() and use the EXACT path it shows",
                "Try a relative path (just the filename) without any prefix",
                "Re-create the file with write_file before referencing it",
            ],
            "syntax": [
                "read_file the failing file, then diff_edit only the bad line",
                "Rewrite the whole file fresh with write_file (small files)",
                "Run the file with run_python to see the precise error line",
            ],
            "import": [
                "run_shell('pip install <pkg>') then retry",
                "Switch to a stdlib equivalent if the package is optional",
            ],
            "port_conflict": [
                "Call server_stop first to free the port",
                "Pick a different port (5001, 8080, 8000) for server_start",
            ],
            "network": [
                "Wait a moment and retry once — do not hammer the API",
                "Use a different model/provider via the router",
            ],
            "runtime": [
                "read_file the failing source, locate the line in the traceback",
                "Add a tiny test script that isolates the failing call",
                "diff_edit the precise broken line instead of rewriting",
            ],
            "loop_detected": [
                "Use a completely different tool than the one that just failed",
                "Change the args (different path, different content, different cmd)",
                "Simplify the step — try a smaller intermediate goal first",
            ],
            "permission": [
                "Drop any system path; only use workspace-relative filenames",
                "Stop the server (server_stop) if you're rewriting its files",
            ],
            "unknown": [
                "Gather context: list_files, then read_file the related files",
                "Pick a different tool entirely and approach the goal sideways",
            ],
        }
        return alts.get(category, alts["unknown"])

    def _fingerprint_action(self, action: str, args: dict) -> str:
        try:
            args_s = json.dumps(args or {}, sort_keys=True, default=str)
        except Exception:
            args_s = str(args)
        return f"{action}|{args_s}"

    def _snapshot_before_action(self, action: str, args: dict, current_step: int,
                                last_output: str, error_count: int) -> dict:
        snapshot = {
            "action": action,
            "args": args,
            "step": current_step,
            "last_output": last_output,
            "error_count": error_count,
        }
        if action in {"write_file", "delete_file", "search_replace", "diff_edit"} and isinstance(args, dict):
            path = args.get("path")
            if path:
                prior = self.tools.read_file(path)
                snapshot["path"] = path
                snapshot["file_existed"] = prior["success"]
                snapshot["file_content"] = prior["output"] if prior["success"] else ""
        return snapshot

    def _rollback_from_snapshot(self, snapshot: dict | None) -> str:
        if not snapshot or "path" not in snapshot:
            return ""

        path = snapshot["path"]
        if snapshot.get("file_existed"):
            res = self.tools.write_file(path=path, content=snapshot.get("file_content", ""))
            return f"Restored previous file content for {path}" if res["success"] else f"Rollback failed for {path}: {res['error']}"

        res = self.tools.delete_file(path=path)
        if res["success"]:
            return f"Removed newly-created file {path}"
        return ""

    def _categorize_error(self, err: str) -> str:
        msg = (err or "").lower()
        # Check most-specific patterns first
        if any(x in msg for x in ("no such file", "not found", "does not exist",
                                   "cannot find", "filenotfounderror", "resolved →")):
            return "file_not_found"
        if any(x in msg for x in ("syntaxerror", "invalid syntax",
                                   "indentationerror", "unexpected indent",
                                   "unexpected eof")):
            return "syntax"
        if any(x in msg for x in ("modulenotfounderror", "no module named",
                                   "importerror", "cannot import")):
            return "import"
        if any(x in msg for x in ("address already in use", "port",
                                   "already in use", "bind:", "eaddrinuse")):
            return "port_conflict"
        if any(x in msg for x in ("permission", "access denied",
                                   "readonly", "operation not permitted")):
            return "permission"
        if any(x in msg for x in ("timeout", "timed out", "429",
                                   "rate limit", "connection", "dns", "network",
                                   "did not bind", "did not become ready")):
            return "network"
        if any(x in msg for x in ("traceback", "runtimeerror", "typeerror",
                                   "valueerror", "assertionerror", "attributeerror",
                                   "keyerror", "indexerror", "nameerror")):
            return "runtime"
        if any(x in msg for x in ("blocked", "do not repeat", "same action",
                                   "already failed")):
            return "loop_detected"
        return "unknown"

    def _strategy_for_error(self, category: str) -> str:
        strategies = {
            "file_not_found": (
                "Call list_files() first to see what actually exists. "
                "Then use the exact filename shown — never guess paths."
            ),
            "syntax": (
                "Call read_file on the failing file, locate the exact bad line, "
                "fix it with diff_edit, then run again to confirm."
            ),
            "import": (
                "Run run_shell('pip install <missing_package>') first, "
                "then retry the code."
            ),
            "port_conflict": (
                "Call server_stop to free the port, or switch to a different port "
                "(5001, 8080, 8000).  Then retry server_start."
            ),
            "permission": (
                "Use only workspace-relative filenames. "
                "Avoid system directories or privileged commands."
            ),
            "network": (
                "The API or server may be unavailable. "
                "Wait briefly, then retry.  Do not hammer with identical requests."
            ),
            "runtime": (
                "Read the full traceback. Use read_file to inspect the failing code. "
                "Fix the root cause with diff_edit, then rerun."
            ),
            "loop_detected": (
                "You are repeating a failed action. "
                "Choose a completely different tool or different arguments."
            ),
            "unknown": (
                "Gather more context via list_files or read_file, "
                "then choose a different fix path."
            ),
        }
        return strategies.get(category, strategies["unknown"])