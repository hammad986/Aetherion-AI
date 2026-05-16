"""
execution_planner.py — Aetherion Long-Horizon Execution Intelligence v1
════════════════════════════════════════════════════════════════════════
Provides:
  • ExecutionDAG       — live dependency graph (tasks, states, retry budgets)
  • MilestoneTracker   — milestone evidence & progress measurement
  • AdaptiveReplanner  — failure-driven replanning without infinite loops
  • StrategyMemory     — records strategy success/failure for future runs
  • PlannerAudit       — exposes current chaos vectors & execution drift
"""

import time
import json
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Node States
# ─────────────────────────────────────────────────────────────────────────────

class NodeState(str, Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    DONE       = "done"
    FAILED     = "failed"
    BLOCKED    = "blocked"    # dependency not satisfied
    SKIPPED    = "skipped"    # skipped after replan
    HITL_WAIT  = "hitl_wait"  # awaiting human input


# ─────────────────────────────────────────────────────────────────────────────
# Execution DAG Node
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DAGNode:
    step_index: int
    step_text: str
    stage: str = "execution"
    state: NodeState = NodeState.PENDING
    depends_on: list[int] = field(default_factory=list)   # step indices
    retry_count: int = 0
    retry_budget: int = 3
    error_category: str = ""
    last_error: str = ""
    semantic_confidence: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    is_critical_path: bool = False
    rollback_possible: bool = True

    def is_ready(self, completed_indices: set[int]) -> bool:
        """True if all dependencies are satisfied."""
        return all(d in completed_indices for d in self.depends_on)

    def retry_budget_exhausted(self) -> bool:
        return self.retry_count >= self.retry_budget

    def to_dict(self) -> dict:
        return {
            "step_index": self.step_index,
            "step_text": self.step_text[:80],
            "stage": self.stage,
            "state": self.state.value,
            "retry_count": self.retry_count,
            "retry_budget": self.retry_budget,
            "error_category": self.error_category,
            "semantic_confidence": self.semantic_confidence,
            "is_critical_path": self.is_critical_path,
            "elapsed_s": round(self.finished_at - self.started_at, 1) if self.finished_at else None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Execution DAG
# ─────────────────────────────────────────────────────────────────────────────

class ExecutionDAG:
    """
    Live directed acyclic graph of all execution steps.
    Tracks states, dependencies, retries, and failure propagation.
    Serializable for checkpoint/replay.
    """

    def __init__(self):
        self.nodes: dict[int, DAGNode] = {}
        self.created_at = time.time()
        self._replan_count = 0

    # ── Build ─────────────────────────────────────────────────────────────────

    def build_from_plan(self, plan_steps: list[str], plan_stages: list[dict]) -> None:
        """Construct nodes from the existing flat plan + stage structure."""
        stage_map = {}
        for stage_obj in plan_stages:
            sname = stage_obj.get("stage", "execution")
            for step in stage_obj.get("steps", []):
                stage_map[step] = sname

        # Infer simple sequential dependencies: each step depends on the previous
        # within its stage. Cross-stage: first step of stage N depends on last of stage N-1.
        stage_last: dict[str, int] = {}
        prev_idx: Optional[int] = None

        for idx, step in enumerate(plan_steps):
            stage = stage_map.get(step, "execution")
            deps = []
            if prev_idx is not None:
                deps = [prev_idx]
            node = DAGNode(
                step_index=idx,
                step_text=step,
                stage=stage,
                depends_on=deps,
                is_critical_path=True,  # all steps on critical path for sequential plans
            )
            self.nodes[idx] = node
            stage_last[stage] = idx
            prev_idx = idx

    # ── State transitions ─────────────────────────────────────────────────────

    def start(self, step_index: int) -> None:
        if n := self.nodes.get(step_index):
            n.state = NodeState.RUNNING
            n.started_at = time.time()

    def complete(self, step_index: int, semantic_confidence: float = 1.0) -> None:
        if n := self.nodes.get(step_index):
            n.state = NodeState.DONE
            n.finished_at = time.time()
            n.semantic_confidence = semantic_confidence

    def fail(self, step_index: int, error: str, error_category: str = "") -> None:
        if n := self.nodes.get(step_index):
            n.state = NodeState.FAILED
            n.finished_at = time.time()
            n.last_error = error[:200]
            n.error_category = error_category
            n.retry_count += 1

    def block(self, step_index: int) -> None:
        if n := self.nodes.get(step_index):
            n.state = NodeState.BLOCKED

    def retry(self, step_index: int) -> bool:
        """Returns True if retry is allowed."""
        n = self.nodes.get(step_index)
        if not n:
            return False
        if n.retry_budget_exhausted():
            return False
        n.state = NodeState.PENDING
        return True

    # ── Queries ───────────────────────────────────────────────────────────────

    def completed_indices(self) -> set[int]:
        return {i for i, n in self.nodes.items() if n.state == NodeState.DONE}

    def failed_nodes(self) -> list[DAGNode]:
        return [n for n in self.nodes.values() if n.state == NodeState.FAILED]

    def blocked_nodes(self) -> list[DAGNode]:
        done = self.completed_indices()
        return [n for n in self.nodes.values()
                if n.state == NodeState.PENDING and not n.is_ready(done)]

    def completion_ratio(self) -> float:
        if not self.nodes:
            return 0.0
        return len(self.completed_indices()) / len(self.nodes)

    def avg_semantic_confidence(self) -> float:
        done = [n for n in self.nodes.values() if n.state == NodeState.DONE]
        if not done:
            return 0.0
        return round(sum(n.semantic_confidence for n in done) / len(done), 2)

    def serialize(self) -> dict:
        return {
            "nodes": {str(i): n.to_dict() for i, n in self.nodes.items()},
            "replan_count": self._replan_count,
            "completion_ratio": self.completion_ratio(),
            "avg_semantic_confidence": self.avg_semantic_confidence(),
        }

    def to_sse_payload(self) -> dict:
        return {
            "dag": [n.to_dict() for n in self.nodes.values()],
            "completion_ratio": self.completion_ratio(),
            "avg_confidence": self.avg_semantic_confidence(),
            "failed_count": len(self.failed_nodes()),
            "replan_count": self._replan_count,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Milestone Tracker
# ─────────────────────────────────────────────────────────────────────────────

# Canonical milestones for common project types (heuristic mapping)
MILESTONE_SIGNATURES = {
    "dependencies_ready": [
        "install", "pip install", "requirements", "package", "npm install",
    ],
    "schema_ready": [
        "database", "migration", "init_db", "schema", "model", "table",
    ],
    "backend_ready": [
        "server_start", "flask", "fastapi", "server", "api", "endpoint", "route",
    ],
    "frontend_ready": [
        "html", "template", "css", "javascript", "react", "render", "ui",
    ],
    "verified": [
        "test", "verify", "validate", "browser_navigate", "assertion", "check",
    ],
}


@dataclass
class Milestone:
    name: str
    description: str
    achieved: bool = False
    achieved_at: float = 0.0
    evidence: str = ""
    contributing_steps: list[int] = field(default_factory=list)


class MilestoneTracker:
    """
    Tracks progress across high-level milestones.
    Milestones are inferred from step text — no LLM call needed.
    """

    def __init__(self):
        self.milestones: dict[str, Milestone] = {
            name: Milestone(name=name, description=name.replace("_", " ").title())
            for name in MILESTONE_SIGNATURES
        }
        self._custom: dict[str, Milestone] = {}

    def evaluate_step(self, step_index: int, step_text: str, tool_output: str = "") -> list[str]:
        """Returns names of milestones newly achieved by this step."""
        newly_achieved = []
        combined = (step_text + " " + tool_output).lower()

        for ms_name, signatures in MILESTONE_SIGNATURES.items():
            ms = self.milestones[ms_name]
            if ms.achieved:
                continue
            if any(sig in combined for sig in signatures):
                ms.achieved = True
                ms.achieved_at = time.time()
                ms.evidence = tool_output[:120]
                ms.contributing_steps.append(step_index)
                newly_achieved.append(ms_name)
                logger.info(f"[Milestone] {ms_name} achieved at step {step_index}")

        return newly_achieved

    def progress_ratio(self) -> float:
        achieved = sum(1 for m in self.milestones.values() if m.achieved)
        return round(achieved / len(self.milestones), 2)

    def blocked_milestones(self) -> list[str]:
        return [name for name, m in self.milestones.items() if not m.achieved]

    def to_sse_payload(self) -> dict:
        return {
            "milestones": {
                name: {
                    "achieved": m.achieved,
                    "evidence": m.evidence[:80],
                }
                for name, m in self.milestones.items()
            },
            "progress_ratio": self.progress_ratio(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Adaptive Replanner
# ─────────────────────────────────────────────────────────────────────────────

# How many consecutive semantic/runtime failures on a step before replanning
REPLAN_THRESHOLD = 3
# How many total replans before escalating to HITL
MAX_REPLANS      = 4


class ReplanDecision(str, Enum):
    RETRY_SAME    = "retry_same"       # try the same step again
    RETRY_ALT     = "retry_alt"        # inject an alternative approach hint
    SKIP_STEP     = "skip_step"        # skip non-critical step and continue
    ROLLBACK      = "rollback"         # revert to previous checkpoint
    REPLAN        = "replan"           # regenerate remaining plan
    ESCALATE_HITL = "escalate_hitl"   # require human input before proceeding
    ABANDON       = "abandon"          # task beyond autonomous confidence


@dataclass
class ReplanResult:
    decision: ReplanDecision
    reason: str
    injected_steps: list[str] = field(default_factory=list)
    rollback_to_step: int = -1
    hitl_prompt: str = ""


class AdaptiveReplanner:
    """
    Decides what to do when execution gets stuck — without infinite loops.
    All decisions are based on hard evidence: retry count, error category,
    semantic failure type, replan history.
    """

    def __init__(self, dag: ExecutionDAG, emit_fn=None, session_id: str = ""):
        self.dag        = dag
        self._emit      = emit_fn or (lambda *a, **k: None)
        self._sid       = session_id
        self._replan_history: list[dict] = []

    def evaluate(
        self,
        step_index: int,
        error_count: int,
        error_category: str,
        semantic_failed: bool,
        is_critical: bool,
        per_step_retry: int,
    ) -> ReplanResult:
        """
        Called when a step fails. Returns what to do next.
        Respects retry budgets and replan limits.
        """
        node = self.dag.nodes.get(step_index)
        total_replans = self.dag._replan_count

        # --- Decision tree (deterministic, no LLM) ---

        # 1. Retry budget check
        if node and node.retry_budget_exhausted():
            if total_replans >= MAX_REPLANS:
                return ReplanResult(
                    decision=ReplanDecision.ESCALATE_HITL,
                    reason=f"Step {step_index} retry budget exhausted, max replans ({MAX_REPLANS}) reached",
                    hitl_prompt=(
                        f"Step '{(node.step_text if node else '')[:80]}' has failed "
                        f"{node.retry_count if node else 0} times with no recovery. "
                        "How should I proceed?"
                    ),
                )
            if not is_critical:
                return ReplanResult(
                    decision=ReplanDecision.SKIP_STEP,
                    reason=f"Non-critical step {step_index} budget exhausted — skipping",
                )

        # 2. Semantic failure on a verified step → rollback and retry alternative
        if semantic_failed and error_count >= 2:
            if step_index > 0:
                return ReplanResult(
                    decision=ReplanDecision.RETRY_ALT,
                    reason=f"Semantic validation failed {error_count}x — switching approach",
                    injected_steps=[
                        f"Re-read the file written in the previous step and verify its content is correct",
                        f"Fix the semantic issue identified: {error_category}",
                    ],
                )

        # 3. After PER_STEP_RETRY errors on a critical step → rollback
        if error_count >= per_step_retry and is_critical and step_index > 0:
            return ReplanResult(
                decision=ReplanDecision.ROLLBACK,
                reason=f"Critical step {step_index} failed {error_count}x — rolling back",
                rollback_to_step=max(0, step_index - 1),
            )

        # 4. Import/dependency error → inject install step
        if error_category in ("import", "dependency") and error_count == 1:
            dep_guess = self._guess_dependency(node.last_error if node else "")
            extra = [f"Install missing dependency: pip install {dep_guess}"] if dep_guess else []
            return ReplanResult(
                decision=ReplanDecision.RETRY_ALT,
                reason=f"Dependency missing — injecting install step",
                injected_steps=extra,
            )

        # 5. Port conflict → inject server_stop
        if error_category == "port_conflict":
            return ReplanResult(
                decision=ReplanDecision.RETRY_ALT,
                reason="Port conflict — injecting server_stop before retry",
                injected_steps=["Stop any running server on the conflicting port using server_stop"],
            )

        # 6. Exceeding double retry budget → replan remaining
        if error_count >= per_step_retry * 2:
            if total_replans < MAX_REPLANS:
                self.dag._replan_count += 1
                return ReplanResult(
                    decision=ReplanDecision.REPLAN,
                    reason=f"Step {step_index} blocked — regenerating remaining plan (replan #{total_replans+1})",
                )
            return ReplanResult(
                decision=ReplanDecision.ESCALATE_HITL,
                reason=f"Max replans ({MAX_REPLANS}) reached with persistent failures",
                hitl_prompt="Execution is stuck. Please review and provide guidance.",
            )

        # Default: retry same approach
        return ReplanResult(
            decision=ReplanDecision.RETRY_SAME,
            reason=f"Retrying step {step_index} (attempt {error_count+1})",
        )

    def _guess_dependency(self, error_text: str) -> str:
        """Extract likely package name from ModuleNotFoundError."""
        import re
        m = re.search(r"No module named '([^']+)'", error_text)
        if m:
            pkg = m.group(1).split(".")[0]
            # Common remappings: import name → pip name
            REMAPS = {
                "cv2": "opencv-python",
                "PIL": "Pillow",
                "sklearn": "scikit-learn",
                "bs4": "beautifulsoup4",
                "dotenv": "python-dotenv",
                "yaml": "pyyaml",
            }
            return REMAPS.get(pkg, pkg)
        return ""

    def record_replan(self, step_index: int, decision: ReplanDecision, reason: str) -> None:
        self._replan_history.append({
            "step_index": step_index,
            "decision": decision.value,
            "reason": reason,
            "ts": time.time(),
        })
        try:
            self._emit("agent.trust_signal", {
                "type": "replan_event",
                "verified": False,
                "confidence": 0.40,
                "message": f"Replan: {decision.value} — {reason[:80]}",
                "step": step_index,
                "action": "adaptive_replan",
                "session_id": self._sid,
            })
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Strategy Memory
# ─────────────────────────────────────────────────────────────────────────────

STRATEGY_MEMORY_FILE = "strategy_memory.json"

# Detect task type from keywords → map to known-good strategies
TASK_TYPE_PATTERNS = {
    "flask_app":  ["flask", "jinja", "render_template", "flask app"],
    "fastapi":    ["fastapi", "uvicorn", "pydantic"],
    "ml_pipeline":["sklearn", "torch", "tensorflow", "pandas", "train", "model"],
    "opencv":     ["cv2", "opencv", "camera", "image processing"],
    "scraper":    ["selenium", "playwright", "scrape", "beautifulsoup"],
    "cli_tool":   ["argparse", "click", "command line", "cli"],
    "react_app":  ["react", "nextjs", "vite", "jsx", "tsx"],
    "database":   ["sqlalchemy", "sqlite", "postgres", "mysql", "migration"],
}


class StrategyMemory:
    """
    Records what worked and what failed per task type.
    Cautionary signals are emitted for historically failing patterns.
    """

    def __init__(self, workspace_dir: str = ".", emit_fn=None, session_id: str = ""):
        from pathlib import Path
        self._path     = Path(workspace_dir) / ".aetherion" / STRATEGY_MEMORY_FILE
        self._emit     = emit_fn or (lambda *a, **k: None)
        self._sid      = session_id
        self._data     = self._load()

    def _load(self) -> dict:
        try:
            if self._path.exists():
                return json.loads(self._path.read_text())
        except Exception:
            pass
        return {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, indent=2))
        except Exception as e:
            logger.warning(f"[StrategyMemory] Save failed: {e}")

    def detect_task_type(self, task: str) -> str:
        task_lower = task.lower()
        for t_type, patterns in TASK_TYPE_PATTERNS.items():
            if any(p in task_lower for p in patterns):
                return t_type
        return "general"

    def record_outcome(self, task: str, plan_steps: list[str], success: bool,
                       failure_categories: list[str] = None) -> None:
        t_type = self.detect_task_type(task)
        if t_type not in self._data:
            self._data[t_type] = {"runs": 0, "successes": 0, "failure_categories": {}}
        rec = self._data[t_type]
        rec["runs"]      += 1
        rec["successes"] += 1 if success else 0
        for cat in (failure_categories or []):
            rec["failure_categories"][cat] = rec["failure_categories"].get(cat, 0) + 1
        rec["last_plan_size"] = len(plan_steps)
        rec["last_updated"]   = time.time()
        self._save()
        logger.info(f"[StrategyMemory] Recorded {t_type}: success={success}")

    def get_caution_signals(self, task: str) -> list[dict]:
        """Return cautionary signals for the current task type based on history."""
        t_type = self.detect_task_type(task)
        rec = self._data.get(t_type)
        if not rec or rec["runs"] < 2:
            return []

        success_rate = rec["successes"] / rec["runs"]
        signals = []

        if success_rate < 0.50:
            signals.append({
                "type": "strategy_caution",
                "message": (
                    f"Task type '{t_type}' has historically low success rate "
                    f"({int(success_rate*100)}% over {rec['runs']} runs). "
                    "Consider breaking the task into smaller sub-goals."
                ),
                "confidence": success_rate,
                "verified": False,
            })

        top_failures = sorted(
            rec.get("failure_categories", {}).items(),
            key=lambda x: x[1], reverse=True
        )[:2]
        for cat, count in top_failures:
            if count >= 2:
                signals.append({
                    "type": "known_failure_pattern",
                    "message": f"Historically recurring failure: '{cat}' ({count}x in past runs)",
                    "confidence": 0.40,
                    "verified": False,
                })

        return signals

    def emit_caution_signals(self, task: str, step: int = 0) -> None:
        for sig in self.get_caution_signals(task):
            try:
                self._emit("agent.trust_signal", {**sig, "step": step, "action": "strategy_memory", "session_id": self._sid})
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Planner Audit — execution chaos detector
# ─────────────────────────────────────────────────────────────────────────────

class PlannerAudit:
    """
    Passive monitor that detects execution chaos patterns in real time.
    Called after each loop iteration — no LLM, pure counters.
    """

    DRIFT_THRESHOLD    = 5    # same step repeated 5+ times without progress
    LOOP_THRESHOLD     = 3    # same (action, args) in <10 loops = chaos loop
    STAGNATION_LOOPS   = 8    # no step completion in 8+ loops = stagnation

    def __init__(self):
        self._step_visit_counts: dict[int, int] = {}
        self._action_fingerprints: list[str]    = []
        self._last_completed_step: int          = -1
        self._loops_since_advance: int          = 0

    def record_loop(self, step_index: int, action: str, fingerprint: str,
                    step_advanced: bool) -> list[str]:
        """Returns list of detected chaos signals."""
        signals = []

        # Track step visits
        self._step_visit_counts[step_index] = self._step_visit_counts.get(step_index, 0) + 1
        if self._step_visit_counts[step_index] >= self.DRIFT_THRESHOLD:
            signals.append(
                f"DRIFT: Step {step_index} has been attempted "
                f"{self._step_visit_counts[step_index]}x — possible infinite loop"
            )

        # Track fingerprint repetition
        self._action_fingerprints.append(fingerprint)
        if len(self._action_fingerprints) > 10:
            self._action_fingerprints.pop(0)
        recent = self._action_fingerprints[-6:]
        if len(set(recent)) == 1 and len(recent) >= 3:
            signals.append(f"LOOP: Action '{action}' repeated {len(recent)}x with identical args — chaos loop")

        # Track stagnation
        if step_advanced:
            self._loops_since_advance = 0
        else:
            self._loops_since_advance += 1
            if self._loops_since_advance >= self.STAGNATION_LOOPS:
                signals.append(
                    f"STAGNATION: No step completed in {self._loops_since_advance} loops — execution stuck"
                )

        return signals

    def reset_for_step(self, step_index: int) -> None:
        self._step_visit_counts[step_index] = 0
        self._loops_since_advance = 0
