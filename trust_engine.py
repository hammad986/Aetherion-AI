"""
trust_engine.py — Aetherion Trust Engine v1
════════════════════════════════════════════
Provides:
  • ClarificationEngine  — detects ambiguity, generates focused questions
  • AssumptionExposer    — surfaces agent assumptions before destructive actions
  • MemoryTrustScorer    — adds confidence/staleness metadata to memory retrieval

Designed to be injected into Agent.run() with zero architectural changes.
All methods are pure functions or thin wrappers — no network calls, no LLM calls.
"""

import re
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Clarification Engine
# ─────────────────────────────────────────────────────────────────────────────

AMBIGUOUS_PATTERNS = [
    # File reference ambiguity
    (r'\bthe file\b(?! named| at| called)', "Which specific file should I work with?"),
    (r'\bthe config\b(?! file\b)', "Which configuration file do you mean?"),
    (r'\bthe database\b', "Which database or table are you referring to?"),
    (r'\bthe server\b(?! at| on port)', "Which server or service should I target?"),
    (r'\bthe script\b', "Which script file should I run?"),
    # Vague action verbs
    (r'\b(fix|improve|update|optimize|clean up)\b(?! the syntax| the import)', None),
    (r'\bmake it work\b', "What specific behavior should 'working' produce?"),
    (r'\blast time\b|\bprevious\b|\bbefore\b', "I don't have memory of a previous session. Could you re-specify the target?"),
    # Ambiguous scope
    (r'\beverything\b|\ball of it\b|\bthe whole\b', "Could you specify which parts to include?"),
    (r'\bdeploy\b(?! to| on)', "Where should I deploy — local, staging, or production?"),
]

# Signals that imply the agent can answer the question itself (don't ask)
SELF_ANSWERABLE = [
    r'which files exist',
    r'what is installed',
    r'what port',
    r'what packages',
]

# Actions that are inherently dangerous enough to require assumption exposure
DESTRUCTIVE_ACTIONS = {
    "write_file", "delete_file", "diff_edit", "search_replace",
    "ast_replace", "run_shell", "git_push",
}

# Pure-setup actions that can be trusted without verification
SAFE_SETUP_ACTIONS = {
    "git_init", "list_files", "read_file", "run_shell",  # pip install only
}


class ClarificationEngine:
    """
    Detects ambiguity in task prompts and generates ONE focused clarification question.
    Rule: never ask about something the system can answer by calling a tool.
    """

    CONFIDENCE_FULL    = 1.00
    CONFIDENCE_HIGH    = 0.85
    CONFIDENCE_MEDIUM  = 0.65
    CONFIDENCE_LOW     = 0.40
    CONFIDENCE_MINIMAL = 0.20

    def __init__(self, workspace_dir: str = ""):
        self.workspace_dir = workspace_dir

    def score_task(self, task: str, workspace_files: list[str] = None) -> dict:
        """
        Returns: {
            'confidence': float,
            'ambiguities': list[str],
            'question': str | None,
            'should_pause': bool,
        }
        """
        task_lower = task.lower().strip()
        ambiguities = []
        question    = None
        workspace_files = workspace_files or []

        for pattern, q_template in AMBIGUOUS_PATTERNS:
            if re.search(pattern, task_lower):
                if q_template:
                    ambiguities.append(q_template)
                else:
                    # Generic vague-verb ambiguity — generate contextual question
                    verb_match = re.search(pattern, task_lower)
                    verb = verb_match.group(0) if verb_match else "that"
                    ambiguities.append(f"What specific outcome defines '{verb}' as complete?")

        # Check for file references that don't match workspace
        mentioned_files = re.findall(r'\b[\w/-]+\.(py|js|ts|json|yaml|yml|sh|html|css)\b', task_lower)
        for mf in mentioned_files:
            if workspace_files and not any(mf in wf for wf in workspace_files):
                ambiguities.append(
                    f"File '{mf}' was not found in the workspace. Should I create it or did you mean a different file?"
                )

        # Score
        if not ambiguities:
            confidence = self.CONFIDENCE_HIGH
        elif len(ambiguities) == 1:
            confidence = self.CONFIDENCE_MEDIUM
        elif len(ambiguities) == 2:
            confidence = self.CONFIDENCE_LOW
        else:
            confidence = self.CONFIDENCE_MINIMAL

        # Pick the single most important question
        if ambiguities:
            question = ambiguities[0]  # Always ONE question, most specific first

        should_pause = confidence < self.CONFIDENCE_MEDIUM

        return {
            "confidence": confidence,
            "ambiguities": ambiguities,
            "question": question,
            "should_pause": should_pause,
        }

    def format_clarification_request(self, analysis: dict, task: str) -> str:
        """
        Formats the clarification message to inject into the agent's output
        or emit as a HITL event.
        """
        q = analysis.get("question")
        if not q:
            return ""
        confidence_pct = int(analysis["confidence"] * 100)
        return (
            f"[CLARIFICATION NEEDED — confidence {confidence_pct}%]\n"
            f"Before proceeding with: '{task[:80]}...'\n"
            f"Question: {q}\n"
            f"Please respond with a specific answer. I will wait."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Assumption Exposer
# ─────────────────────────────────────────────────────────────────────────────

class AssumptionExposer:
    """
    Generates structured assumption lists before destructive or ambiguous actions.
    Emits agent.trust_signal events via the provided emit_fn.
    """

    def __init__(self, emit_fn=None, session_id: str = ""):
        self._emit = emit_fn or (lambda *a, **k: None)
        self._sid  = session_id

    def expose_before_action(
        self,
        action: str,
        args: dict,
        step_text: str,
        step_index: int,
        workspace_files: list[str] = None,
    ) -> list[dict]:
        """
        Returns a list of assumption dicts and emits trust signals.
        Called BEFORE executing a destructive action.
        """
        if action not in DESTRUCTIVE_ACTIONS:
            return []

        assumptions = []
        path = (args or {}).get("path", "")
        workspace_files = workspace_files or []

        # File target assumption
        if path:
            file_exists = any(path in wf or wf.endswith(path) for wf in workspace_files)
            assumptions.append({
                "type": "target_file",
                "value": path,
                "confidence": 0.90 if file_exists else 0.50,
                "verified": file_exists,
                "note": "exists in workspace" if file_exists else "not found in workspace — will create",
            })

        # Shell command assumptions
        if action == "run_shell":
            cmd = (args or {}).get("command", "")
            if "pip install" in cmd:
                assumptions.append({
                    "type": "package_install",
                    "value": cmd,
                    "confidence": 0.80,
                    "verified": False,
                    "note": "will modify system package state",
                })
            elif any(x in cmd for x in ["rm ", "del ", "rmdir"]):
                assumptions.append({
                    "type": "destructive_command",
                    "value": cmd,
                    "confidence": 0.95,
                    "verified": False,
                    "note": "WARNING: deletion command — irreversible",
                })

        if not assumptions:
            return []

        # Emit trust signal for each assumption
        for assumption in assumptions:
            try:
                self._emit("agent.trust_signal", {
                    "type": "assumption",
                    "verified": assumption["verified"],
                    "confidence": assumption["confidence"],
                    "message": f"Assuming {assumption['type']}: {assumption['value']} ({assumption['note']})",
                    "step": step_index,
                    "action": action,
                    "session_id": self._sid,
                })
            except Exception:
                pass

        logger.info(f"[Trust] Exposed {len(assumptions)} assumption(s) for {action}({path})")
        return assumptions

    def format_assumption_banner(self, assumptions: list[dict], action: str) -> str:
        if not assumptions:
            return ""
        lines = [f"[ASSUMPTIONS before {action}]"]
        for a in assumptions:
            verified_marker = "✓" if a["verified"] else "?"
            lines.append(
                f"  {verified_marker} {a['type']}: {a['value']} "
                f"(confidence: {int(a['confidence']*100)}%) — {a['note']}"
            )
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Memory Trust Scorer
# ─────────────────────────────────────────────────────────────────────────────

MEMORY_STALENESS_DAYS = {
    "learning":       30,    # learnings stale after 30 days
    "task_history":   90,    # task history stale after 90 days
    "skill":          60,    # skills stale after 60 days
    "execution":      14,    # execution records stale after 14 days
    "snippet":        180,   # snippets stale after 180 days
}


def score_memory_item(
    item: dict,
    memory_type: str,
    current_stack: str = "",
    now: float = None,
) -> dict:
    """
    Augments a raw memory item with trust metadata:
      - confidence: 0.0 – 1.0
      - staleness_warning: bool
      - cautionary: bool  (low-success items should be flagged, not silently used)
      - source: str
    """
    now = now or time.time()
    result = dict(item)
    result["source"]     = memory_type
    result["trust_scored_at"] = now

    # Staleness check
    created_at = item.get("created_at") or item.get("ts") or 0
    age_days   = (now - created_at) / 86400 if created_at else 9999
    stale_threshold = MEMORY_STALENESS_DAYS.get(memory_type, 30)
    result["staleness_warning"] = age_days > stale_threshold
    result["age_days"] = round(age_days, 1)

    # Confidence from success rate
    success_rate = item.get("success_rate")
    use_count    = item.get("use_count", 0)

    if success_rate is not None:
        base_confidence = float(success_rate)
    elif use_count >= 3:
        # Assume decent confidence if item has been used multiple times
        base_confidence = 0.70
    elif use_count >= 1:
        base_confidence = 0.55
    else:
        base_confidence = 0.40  # Never used — low confidence

    # Staleness decay
    if result["staleness_warning"]:
        staleness_factor = max(0.3, 1.0 - (age_days - stale_threshold) / stale_threshold)
        base_confidence *= staleness_factor

    result["confidence"] = round(base_confidence, 2)

    # Stack tag mismatch — cross-project contamination guard
    item_stack = item.get("stack_tags", "")
    if current_stack and item_stack and current_stack not in item_stack:
        result["confidence"] = round(result["confidence"] * 0.6, 2)
        result["stack_mismatch_warning"] = True
    else:
        result["stack_mismatch_warning"] = False

    # Cautionary flag: low success rate items must be labelled, not silently applied
    result["cautionary"] = (
        result["confidence"] < 0.50
        or result["staleness_warning"]
        or result.get("stack_mismatch_warning", False)
    )

    return result


def filter_cautionary_memories(items: list[dict], emit_fn=None, session_id: str = "") -> list[dict]:
    """
    Separates high-confidence from cautionary memories.
    Emits trust signals for cautionary items so the UI can surface them.
    Returns only high-confidence items for direct agent injection.
    """
    safe       = []
    cautionary = []
    for item in items:
        if item.get("cautionary"):
            cautionary.append(item)
            if emit_fn:
                try:
                    emit_fn("agent.trust_signal", {
                        "type": "memory_caution",
                        "verified": False,
                        "confidence": item.get("confidence", 0),
                        "message": (
                            f"Memory item flagged cautionary: "
                            f"confidence={item.get('confidence', 0):.0%}, "
                            f"stale={item.get('staleness_warning')}, "
                            f"stack_mismatch={item.get('stack_mismatch_warning')}"
                        ),
                        "step": -1,
                        "action": "memory_retrieval",
                        "session_id": session_id,
                    })
                except Exception:
                    pass
        else:
            safe.append(item)

    if cautionary:
        logger.warning(
            f"[MemoryTrust] {len(cautionary)} cautionary item(s) excluded from agent context. "
            f"{len(safe)} safe item(s) injected."
        )
    return safe
