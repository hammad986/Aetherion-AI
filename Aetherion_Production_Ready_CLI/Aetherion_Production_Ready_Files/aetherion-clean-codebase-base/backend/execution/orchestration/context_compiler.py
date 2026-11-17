"""
backend/execution/orchestration/context_compiler.py — Bounded, Adaptive Context Compiler.

Phase 17: Adaptive Context Compiler.

Produces a role-aware, bounded CompiledContext from:
  - Task objective
  - Repository intelligence (RepositoryMap + TaskRelevanceEngine)
  - Worktree metadata (repo root, branch, worktree id)
  - Selected files and symbols with verifiable relevance reasons
  - Tool results (bounded)
  - Verification failures and verification criteria
  - Repair history (bounded)
  - Diff text and affected areas
  - Orchestration rationale

Enforces role-specific compilation semantics:
  - PLANNER: high-level architecture, symbols, constraints, candidate files, related tests
  - EXECUTOR: exact files/symbols to modify, current file contents, interfaces, tool state
  - VERIFIER: verification criteria, changed files, test/build commands, expected output
  - REPAIR: verifier failure diagnostics, changed files, error source context, remaining budget

Strictly bounds total context:
  - Ranks candidate context
  - Prunes low-value items when budget is tight
  - Compresses whitespace and long docstrings
  - Retains critical evidence (errors, targeted files)
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

from .model_profile import ModelRole
from .repo_intelligence import RelevanceItem, RelevanceReason, RepositoryIntelligence

logger = logging.getLogger("aetherion.orchestration.context_compiler")

# Maximum characters for any single section (hard ceiling)
_MAX_FILE_SNIPPET_CHARS = 2_000
_MAX_TOOL_RESULT_CHARS = 1_000
_MAX_REPAIR_ENTRY_CHARS = 800
_MAX_MEMORY_CHARS = 1_500


@dataclass
class CompiledContext:
    """
    Bounded, role-aware context ready for injection into a model call.

    All sections are pre-formatted strings. Characters-per-section are bounded
    so total context never silently grows beyond profile.context_budget.
    """
    role: ModelRole
    objective: str
    worktree_metadata: Dict[str, Any] = field(default_factory=dict)
    file_snippets: List[Dict[str, str]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    verification_failures: List[str] = field(default_factory=list)
    repair_history: List[Dict[str, Any]] = field(default_factory=list)
    memory_context: str = ""
    rationale: str = ""
    estimated_chars: int = 0

    # Phase 17: Repository Intelligence additions
    selected_files: List[str] = field(default_factory=list)
    selected_symbols: List[str] = field(default_factory=list)
    selection_reasons: List[Dict[str, Any]] = field(default_factory=list)
    related_tests: List[str] = field(default_factory=list)
    repo_summary: Dict[str, Any] = field(default_factory=dict)
    affected_areas: Dict[str, Any] = field(default_factory=dict)
    verification_criteria: Dict[str, Any] = field(default_factory=dict)
    pruned_items_count: int = 0
    compression_applied: bool = False

    # Phase 18: Governed Skills and Verified Engineering Memory
    selected_skills: List[Dict[str, Any]] = field(default_factory=list)
    engineering_memories: List[Dict[str, Any]] = field(default_factory=list)
    memory_conflicts: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def tokens_used(self) -> int:
        """Estimate tokens used based on actual rendered prompt length."""
        prompt_text = self.to_prompt_text()
        return max(1, (len(prompt_text) + 3) // 4)

    def to_prompt_text(self) -> str:
        """Render compiled context into a single role-adapted prompt string."""
        parts: List[str] = []
        parts.append(f"[ROLE: {self.role.value.lower()}] [{self.role.value}]")
        parts.append(f"[OBJECTIVE]\n{self.objective}")

        if self.worktree_metadata:
            meta_str = "\n".join(
                f"  {k}: {v}" for k, v in self.worktree_metadata.items()
            )
            parts.append(f"[WORKTREE]\n{meta_str}")

        if self.repo_summary:
            summary_str = f"Files: {self.repo_summary.get('file_count', 0)}, Symbols: {self.repo_summary.get('symbol_count', 0)}"
            parts.append(f"[REPOSITORY SUMMARY]\n{summary_str}")

        if self.selection_reasons:
            reasons_str = "\n".join(
                f"  - {r.get('name')} ({r.get('path')}): [{r.get('reason')}] {r.get('details')}"
                for r in self.selection_reasons[:8]
            )
            parts.append(f"[RELEVANT REPOSITORY ITEMS]\n{reasons_str}")

        if self.selected_skills:
            skills_str = "\n".join(
                f"  - {s.get('name') or (s.get('skill') or {}).get('name') or s.get('skill_id')} "
                f"[{s.get('skill_id') or (s.get('skill') or {}).get('skill_id') or ''}] "
                f"v{s.get('version') or (s.get('skill') or {}).get('version', '1.0.0')} "
                f"[{s.get('capability') or (s.get('skill') or {}).get('capability', 'general')}]: "
                f"[{s.get('reason', 'matched')}] {s.get('details', '')}"
                + (f"\n    Allowed Tools: {', '.join(s.get('allowed_tools') or (s.get('skill') or {}).get('allowed_tools', []))}"
                   if (s.get('allowed_tools') or (s.get('skill') or {}).get('allowed_tools')) else "")
                for s in self.selected_skills[:3]
            )
            parts.append(f"[GOVERNED SKILLS]\n{skills_str}")

        if self.engineering_memories:
            memories_list = []
            for em in self.engineering_memories[:4]:
                m_id = em.get("record_id") or em.get("memory_id", "mem")
                t_type = em.get("task_type", "task")
                prob = em.get("problem_summary", "")
                sol = em.get("solution_summary", "")
                conf = em.get("confidence", 1.0)
                memories_list.append(
                    f"  - [{m_id}] ({t_type}, conf={conf:.2f}):\n"
                    f"      Problem: {prob}\n"
                    f"      Proven Solution: {sol}"
                )
            mem_text = "\n".join(memories_list)
            parts.append(
                f"[VERIFIED ENGINEERING MEMORY]\n{mem_text}\n"
                f"  [NOTE: Stored engineering memory is empirical evidence, NOT execution authority. Current task success must be independently established by the verifier.]"
            )

        if self.memory_conflicts:
            conflicts_list = []
            for mc in self.memory_conflicts[:3]:
                pkey = mc.get("problem_key") or ", ".join(mc.get("divergent_symbols") or []) or mc.get("conflict_id", "conflict")
                desc = mc.get("conflict_description") or mc.get("conflict_reason", "")
                guidance = mc.get("resolution_guidance") or mc.get("recommended_resolution", "")
                conflicts_list.append(
                    f"  - Conflict on {pkey}: {desc}\n"
                    f"    Resolution Guidance: {guidance}"
                )
            parts.append(f"[MEMORY CONFLICTS — REQUIRES PLANNER RESOLUTION]\n" + "\n".join(conflicts_list))

        if self.memory_context:
            parts.append(f"[MEMORY — INFORMATIONAL ONLY]\n{self.memory_context}")

        if self.file_snippets:
            snippets = []
            for fs in self.file_snippets:
                path = fs.get("path", "unknown")
                content = fs.get("content", "")
                snippets.append(f"--- {path} ---\n{content}")
            parts.append("[RELEVANT FILES]\n" + "\n\n".join(snippets))

        if self.tool_results:
            results = []
            for tr in self.tool_results:
                tool = tr.get("tool", "tool")
                out = tr.get("output", "")
                results.append(f"Tool: {tool}\nOutput: {out}")
            parts.append("[TOOL RESULTS]\n" + "\n---\n".join(results))

        if self.verification_failures:
            parts.append(
                "[VERIFICATION FAILURES]\n" + "\n".join(
                    f"  - {vf}" for vf in self.verification_failures
                )
            )

        if self.verification_criteria:
            crit_str = "\n".join(f"  {k}: {v}" for k, v in self.verification_criteria.items())
            parts.append(f"[VERIFICATION CRITERIA]\n{crit_str}")

        if self.repair_history:
            repairs = []
            for rh in self.repair_history:
                attempt = rh.get("attempt", "?")
                status = rh.get("status", "?")
                reason = rh.get("reason", "")
                repairs.append(f"  Attempt {attempt}: {status} — {reason}")
            parts.append("[REPAIR HISTORY]\n" + "\n".join(repairs))

        if self.related_tests:
            tests_str = ", ".join(self.related_tests[:5])
            parts.append(f"[RELATED TESTS]\n{tests_str}")

        if self.rationale:
            parts.append(f"[ORCHESTRATION RATIONALE]\n{self.rationale}")

        return "\n\n".join(parts)

    def as_dict(self) -> Dict[str, Any]:
        """Safe observability dictionary containing zero secrets or credentials."""
        return {
            "role": self.role.value,
            "objective": self.objective,
            "worktree_metadata": self.worktree_metadata,
            "file_snippet_count": len(self.file_snippets),
            "tool_result_count": len(self.tool_results),
            "verification_failure_count": len(self.verification_failures),
            "repair_history_count": len(self.repair_history),
            "has_memory": bool(self.memory_context),
            "estimated_chars": self.estimated_chars,
            "rationale": self.rationale,
            # Phase 17 additions
            "selected_files": self.selected_files,
            "selected_symbols": self.selected_symbols,
            "selection_reasons": self.selection_reasons,
            "related_tests": self.related_tests,
            "repo_summary": self.repo_summary,
            "pruned_items_count": self.pruned_items_count,
            "compression_applied": self.compression_applied,
            # Phase 18 additions
            "selected_skills": [s.get("name") or s.get("skill_id") for s in self.selected_skills],
            "engineering_memory_count": len(self.engineering_memories),
            "memory_conflict_count": len(self.memory_conflicts),
        }


class ContextCompiler:
    """
    Bounded, deterministic, role-aware context compiler.

    All sections are trimmed to hard per-section limits. Total context is
    bounded to profile.context_budget (measured in characters; 1 token ≈ 4 chars).
    """

    CHARS_PER_TOKEN = 4  # conservative estimate

    def compile(
        self,
        role: ModelRole,
        objective: str,
        context_budget_tokens: int = 4000,
        worktree_metadata: Optional[Dict[str, Any]] = None,
        file_snippets: Optional[List[Dict[str, str]]] = None,
        tool_results: Optional[List[Dict[str, Any]]] = None,
        verification_failures: Optional[List[str]] = None,
        repair_history: Optional[List[Dict[str, Any]]] = None,
        memory_context: Optional[str] = None,
        rationale: str = "",
        # Phase 17 additions
        repo_intelligence: Optional[RepositoryIntelligence] = None,
        relevance_items: Optional[List[RelevanceItem]] = None,
        verification_criteria: Optional[Dict[str, Any]] = None,
        user_files: Optional[List[str]] = None,
        recent_evidence: Optional[Dict[str, Any]] = None,
        # Phase 18 additions
        selected_skills: Optional[List[Any]] = None,
        engineering_memories: Optional[List[Any]] = None,
        memory_conflicts: Optional[List[Any]] = None,
    ) -> CompiledContext:
        """
        Compile a bounded, role-adapted CompiledContext.

        Respects context budget tokens and prunes lower-value items if budget is tight.
        """
        budget_chars = context_budget_tokens * self.CHARS_PER_TOKEN
        pruned_count = 0
        compression_applied = False

        # --- Objective (always preserved) ---
        obj = str(objective or "").strip()[:budget_chars]

        # --- Worktree metadata ---
        wt_meta = dict(worktree_metadata or {})
        # Scrub sensitive keys
        for sensitive in ("token", "secret", "password", "credential", "key", "auth"):
            wt_meta = {k: v for k, v in wt_meta.items() if sensitive.lower() not in k.lower()}

        # --- Repository Intelligence & Relevance Selection ---
        selected_files: List[str] = []
        selected_symbols: List[str] = []
        selection_reasons: List[Dict[str, Any]] = []
        related_tests: List[str] = []
        repo_summary: Dict[str, Any] = {}
        affected_areas: Dict[str, Any] = {}

        candidate_snippets: List[Dict[str, str]] = list(file_snippets or [])

        if repo_intelligence is not None:
            repo_summary = repo_intelligence.as_dict()

            # Analyze relevance if not pre-computed
            if relevance_items is None:
                relevance_items = repo_intelligence.get_task_context(
                    objective=objective,
                    user_files=user_files,
                    recent_evidence=recent_evidence,
                    max_candidates=10,
                )

        if relevance_items:
            for item in relevance_items:
                selection_reasons.append(item.as_dict())
                if item.item_type == "test":
                    if item.path not in related_tests:
                        related_tests.append(item.path)
                elif item.item_type == "symbol":
                    if item.name not in selected_symbols:
                        selected_symbols.append(item.name)
                    if item.path not in selected_files:
                        selected_files.append(item.path)
                else:
                    if item.path not in selected_files:
                        selected_files.append(item.path)

            # If no manual snippets provided, fetch safe snippets from top relevant files
            if not candidate_snippets and repo_intelligence is not None:
                # Role-specific snippet limit
                snippet_limit = 2 if role in (ModelRole.VERIFIER, ModelRole.REPAIR) else (
                    4 if role == ModelRole.PLANNER else 6
                )
                for fpath in selected_files[:snippet_limit]:
                    content = repo_intelligence.read_file_safe(fpath, max_chars=_MAX_FILE_SNIPPET_CHARS)
                    if content:
                        candidate_snippets.append({"path": fpath, "content": content})

            # Compute affected areas if relevant files were found
            if repo_intelligence is not None and selected_files:
                affected_areas = repo_intelligence.get_affected_areas(selected_files[:3])
                for t in affected_areas.get("related_tests", []):
                    if t not in related_tests:
                        related_tests.append(t)

        # --- File snippets (bounded and pruned to fit budget) ---
        compiled_snippets, s_pruned, s_compressed = self._bound_file_snippets(
            candidate_snippets, budget_chars, role
        )
        pruned_count += s_pruned
        if s_compressed:
            compression_applied = True

        # --- Tool results (bounded) ---
        compiled_tool_results, t_pruned = self._bound_tool_results(tool_results or [], role)
        pruned_count += t_pruned

        # --- Verification failures ---
        vf_list = [str(vf)[:_MAX_TOOL_RESULT_CHARS] for vf in (verification_failures or [])]

        # --- Verification Criteria ---
        v_crit = dict(verification_criteria or {})
        for sensitive in ("token", "secret", "key"):
            v_crit = {k: v for k, v in v_crit.items() if sensitive not in k.lower()}

        # --- Repair history (bounded) ---
        repair = [
            {
                "attempt": rh.get("attempt", "?"),
                "status": str(rh.get("status", ""))[:200],
                "reason": str(rh.get("reason", ""))[:_MAX_REPAIR_ENTRY_CHARS],
            }
            for rh in (repair_history or [])
        ]

        # --- Memory (bounded) ---
        mem = str(memory_context or "")[:_MAX_MEMORY_CHARS]

        # --- Rationale ---
        rat = str(rationale or "")[:1_000]

        # --- Phase 18: Governed Skills ---
        compiled_skills: List[Dict[str, Any]] = []
        for sk in (selected_skills or []):
            if hasattr(sk, "as_dict"):
                d = sk.as_dict()
            elif hasattr(sk, "to_dict"):
                d = sk.to_dict()
            elif isinstance(sk, dict):
                d = dict(sk)
            else:
                d = {}
            if "skill" in d and isinstance(d["skill"], dict):
                s_info = d["skill"]
                d.setdefault("name", s_info.get("name"))
                d.setdefault("skill_id", s_info.get("skill_id"))
                d.setdefault("version", s_info.get("version"))
                d.setdefault("allowed_tools", s_info.get("allowed_tools", []))
                d.setdefault("capability", s_info.get("capability", "general"))
            compiled_skills.append(d)

        # --- Phase 18: Verified Engineering Memories ---
        raw_memories: List[Dict[str, Any]] = []
        for em in (engineering_memories or []):
            if hasattr(em, "as_dict"):
                raw_memories.append(dict(em.as_dict()))
            elif hasattr(em, "to_dict"):
                raw_memories.append(dict(em.to_dict()))
            elif isinstance(em, dict):
                raw_memories.append(dict(em))

        # Memory budget is dynamically allocated from available budget (up to 40% of budget_chars or _MAX_MEMORY_CHARS)
        mem_budget = min(_MAX_MEMORY_CHARS, max(300, int(budget_chars * 0.4)))
        compiled_memories: List[Dict[str, Any]] = []
        mem_chars = 0
        for m in raw_memories:
            prob = str(m.get("problem_summary", ""))
            sol = str(m.get("solution_summary", ""))
            entry_cost = len(prob) + len(sol) + 80
            if mem_chars + entry_cost > mem_budget:
                if not compiled_memories:
                    # Keep at least first entry, truncated to available budget
                    avail = max(60, mem_budget - 80)
                    m["problem_summary"] = prob[:avail // 2]
                    m["solution_summary"] = sol[:avail // 2]
                    compiled_memories.append(m)
                    mem_chars += len(m["problem_summary"]) + len(m["solution_summary"]) + 80
                else:
                    pruned_count += 1
                break
            else:
                compiled_memories.append(m)
                mem_chars += entry_cost

        # --- Phase 18: Memory Conflicts ---
        compiled_conflicts: List[Dict[str, Any]] = []
        for mc in (memory_conflicts or []):
            if hasattr(mc, "as_dict"):
                compiled_conflicts.append(mc.as_dict())
            elif hasattr(mc, "to_dict"):
                compiled_conflicts.append(mc.to_dict())
            elif isinstance(mc, dict):
                compiled_conflicts.append(mc)

        # Estimate total chars
        estimated = (
            len(obj)
            + sum(len(s.get("content", "")) for s in compiled_snippets)
            + sum(len(str(t.get("output", ""))) for t in compiled_tool_results)
            + sum(len(vf) for vf in vf_list)
            + len(mem)
            + len(rat)
            + len(str(selection_reasons))
            + len(str(compiled_skills))
            + len(str(compiled_memories))
            + len(str(compiled_conflicts))
        )

        # Budget compression fallback: if estimated exceeds budget_chars, compress snippets
        if estimated > budget_chars:
            compression_applied = True
            for s in compiled_snippets:
                raw = s.get("content", "")
                # Compress multiple whitespace/newlines
                compressed = re.sub(r"\n\s*\n+", "\n\n", raw).strip()
                s["content"] = compressed[:len(compressed) // 2]
                pruned_count += 1
            estimated = (
                len(obj)
                + sum(len(s.get("content", "")) for s in compiled_snippets)
                + sum(len(str(t.get("output", ""))) for t in compiled_tool_results)
                + sum(len(vf) for vf in vf_list)
                + len(mem)
                + len(rat)
                + len(str(compiled_skills))
                + len(str(compiled_memories))
            )

        logger.debug(
            "ContextCompiler[role=%s]: budget=%d chars, estimated=%d chars, "
            "snippets=%d, tool_results=%d, ver_failures=%d, repairs=%d, skills=%d, memories=%d, pruned=%d",
            role.value,
            budget_chars,
            estimated,
            len(compiled_snippets),
            len(compiled_tool_results),
            len(vf_list),
            len(repair),
            len(compiled_skills),
            len(compiled_memories),
            pruned_count,
        )

        return CompiledContext(
            role=role,
            objective=obj,
            worktree_metadata=wt_meta,
            file_snippets=compiled_snippets,
            tool_results=compiled_tool_results,
            verification_failures=vf_list,
            repair_history=repair,
            memory_context=mem,
            rationale=rat,
            estimated_chars=estimated,
            selected_files=selected_files,
            selected_symbols=selected_symbols,
            selection_reasons=selection_reasons,
            related_tests=related_tests,
            repo_summary=repo_summary,
            affected_areas=affected_areas,
            verification_criteria=v_crit,
            pruned_items_count=pruned_count,
            compression_applied=compression_applied,
            selected_skills=compiled_skills,
            engineering_memories=compiled_memories,
            memory_conflicts=compiled_conflicts,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _bound_file_snippets(
        snippets: List[Dict[str, str]],
        budget_chars: int,
        role: ModelRole,
    ) -> Tuple[List[Dict[str, str]], int, bool]:
        """
        Return file snippets bounded by per-snippet and total budget limits.

        For VERIFIER / REPAIR roles, reduce snippet count to avoid over-contextualising.
        Returns (bounded_snippets, pruned_count, compression_applied).
        """
        if role in (ModelRole.VERIFIER, ModelRole.REPAIR):
            max_snippets = 3
        elif role == ModelRole.PLANNER:
            max_snippets = 5
        else:
            max_snippets = 8

        result = []
        chars_used = 0
        pruned_count = 0
        compression_applied = False

        for i, s in enumerate(snippets):
            if i >= max_snippets:
                pruned_count += 1
                continue
            path = str(s.get("path", ""))[:200]
            raw_content = str(s.get("content", ""))
            content = raw_content[:_MAX_FILE_SNIPPET_CHARS]

            avail_chars = max(0, (budget_chars // 2) - chars_used)

            # Check for whitespace compression opportunities on oversized snippets
            compressed = re.sub(r"\n\s*\n+", "\n\n", content).strip()
            if len(compressed) < len(content):
                if len(content) > avail_chars or len(raw_content) > budget_chars // 2:
                    content = compressed
                    compression_applied = True

            if chars_used + len(content) > budget_chars // 2:
                # If this is the only snippet or first snippet, compress/truncate to fit available
                if i == 0 and avail_chars > 50:
                    compression_applied = True
                    content = content[:avail_chars]
                    result.append({"path": path, "content": content})
                    chars_used += len(content)
                else:
                    pruned_count += len(snippets) - i
                    break
            else:
                result.append({"path": path, "content": content})
                chars_used += len(content)

        return result, pruned_count, compression_applied

    @staticmethod
    def _bound_tool_results(
        tool_results: List[Dict[str, Any]],
        role: ModelRole,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Return bounded tool results (last N, each capped) and pruned count."""
        if role == ModelRole.PLANNER:
            keep = 2
        elif role in (ModelRole.VERIFIER, ModelRole.REPAIR):
            keep = 5
        else:
            keep = 3

        pruned_count = max(0, len(tool_results) - keep)
        result = []
        for tr in tool_results[-keep:]:
            tool_name = str(tr.get("tool", ""))[:100]
            output = str(tr.get("output", ""))[:_MAX_TOOL_RESULT_CHARS]
            result.append({"tool": tool_name, "output": output})

        return result, pruned_count
