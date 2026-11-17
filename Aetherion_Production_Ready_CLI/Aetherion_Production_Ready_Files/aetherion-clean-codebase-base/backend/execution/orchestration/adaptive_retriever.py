"""
backend/execution/orchestration/adaptive_retriever.py — Adaptive Execution-Time Retrieval.

Phase 17: Adaptive Context Retrieval.

Allows context selection to react dynamically to execution evidence:
  - Detects missing imports, missing symbols, and runtime errors from tool results
  - Looks up candidate definitions in RepositoryIntelligence
  - Enforces a strict retrieval ceiling (e.g. max 3 retrievals) to prevent unbounded loops
  - Deduplicates to ensure the same file/symbol is never retrieved repeatedly
  - Generates structured retrieval evidence for replay events
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .repo_intelligence import RepositoryIntelligence

logger = logging.getLogger("aetherion.orchestration.adaptive_retriever")


@dataclass
class AdaptiveRetrievalResult:
    """Result of an adaptive retrieval action."""
    trigger: str
    target_identifier: str
    file_path: Optional[str] = None
    symbol_name: Optional[str] = None
    content_snippet: Optional[str] = None
    retrieval_index: int = 0
    success: bool = False
    details: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "trigger": self.trigger,
            "target_identifier": self.target_identifier,
            "file_path": self.file_path,
            "symbol_name": self.symbol_name,
            "has_snippet": bool(self.content_snippet),
            "retrieval_index": self.retrieval_index,
            "success": self.success,
            "details": self.details,
        }


class AdaptiveRetriever:
    """
    Coordinates dynamic on-demand retrieval when execution reveals missing dependencies.

    Enforces strict ceilings and deterministic termination.
    """

    DEFAULT_MAX_RETRIEVALS = 3
    MAX_SNIPPET_CHARS = 1500

    # Common error patterns indicating missing code/symbol/import
    PATTERNS = [
        (r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)['\"]", "missing_module"),
        (r"ImportError:\s+cannot import name ['\"]([^'\"]+)['\"]", "missing_import"),
        (r"AttributeError:\s+.*has no attribute ['\"]([^'\"]+)['\"]", "missing_attribute"),
        (r"NameError:\s+name ['\"]([^'\"]+)['\"]\s+is not defined", "missing_name"),
        (r"FileNotFoundError:\s+.*['\"]([^'\"]+\.py)['\"]", "missing_file"),
    ]

    def __init__(
        self,
        repo_intelligence: RepositoryIntelligence,
        max_retrievals: int = DEFAULT_MAX_RETRIEVALS,
    ) -> None:
        self.repo_intelligence = repo_intelligence
        self.max_retrievals = max_retrievals
        self.retrieval_history: List[AdaptiveRetrievalResult] = []
        self.retrieved_paths: Set[str] = set()
        self.retrieved_identifiers: Set[str] = set()

    @property
    def remaining_retrievals(self) -> int:
        return max(0, self.max_retrievals - len(self.retrieval_history))

    @property
    def is_ceiling_reached(self) -> bool:
        return len(self.retrieval_history) >= self.max_retrievals

    def inspect_output_for_trigger(self, text: str) -> Optional[Tuple[str, str]]:
        """
        Check if text contains an error indicating missing dependencies.
        Returns (trigger_kind, identifier) or None.
        """
        if not text:
            return None
        for pattern, kind in self.PATTERNS:
            match = re.search(pattern, text)
            if match:
                identifier = match.group(1).split(".")[-1]
                return (kind, identifier)
        return None

    def retrieve_on_evidence(
        self,
        error_or_output: str,
        current_budget_chars: int = 10000,
    ) -> Optional[AdaptiveRetrievalResult]:
        """
        Attempt adaptive retrieval based on error or tool output.

        Returns AdaptiveRetrievalResult if a new definition was retrieved, or None.
        """
        if self.is_ceiling_reached:
            logger.info("AdaptiveRetriever ceiling reached (%d/%d)", len(self.retrieval_history), self.max_retrievals)
            return None

        trigger_info = self.inspect_output_for_trigger(error_or_output)
        if not trigger_info:
            return None

        kind, ident = trigger_info
        if ident in self.retrieved_identifiers:
            logger.info("Identifier '%s' already retrieved in prior step; deduplicating", ident)
            return None

        index = len(self.retrieval_history) + 1

        # Look up identifier in RepositoryIntelligence
        # 1. Check for symbol match
        syms = self.repo_intelligence.find_symbol(ident)
        if syms:
            s = syms[0]
            snippet = self.repo_intelligence.read_file_safe(
                s.file_path, max_chars=min(self.MAX_SNIPPET_CHARS, current_budget_chars)
            )
            self.retrieved_paths.add(s.file_path)
            self.retrieved_identifiers.add(ident)
            res = AdaptiveRetrievalResult(
                trigger=kind,
                target_identifier=ident,
                file_path=s.file_path,
                symbol_name=s.qualified_name,
                content_snippet=snippet,
                retrieval_index=index,
                success=True,
                details=f"Retrieved {s.kind.value} '{s.qualified_name}' from {s.file_path}",
            )
            self.retrieval_history.append(res)
            return res

        # 2. Check for filename match
        rel_items = self.repo_intelligence.get_task_context(ident, max_candidates=5)
        for item in rel_items:
            snippet = self.repo_intelligence.read_file_safe(
                item.path, max_chars=min(self.MAX_SNIPPET_CHARS, current_budget_chars)
            )
            self.retrieved_paths.add(item.path)
            self.retrieved_identifiers.add(ident)
            res = AdaptiveRetrievalResult(
                trigger=kind,
                target_identifier=ident,
                file_path=item.path,
                symbol_name=None,
                content_snippet=snippet,
                retrieval_index=index,
                success=True,
                details=f"Retrieved file '{item.path}' matching identifier '{ident}'",
            )
            self.retrieval_history.append(res)
            return res

        # Not found
        res = AdaptiveRetrievalResult(
            trigger=kind,
            target_identifier=ident,
            retrieval_index=index,
            success=False,
            details=f"Identifier '{ident}' not found in repository",
        )
        self.retrieval_history.append(res)
        return res

    def as_dict(self) -> Dict[str, Any]:
        return {
            "max_retrievals": self.max_retrievals,
            "retrieval_count": len(self.retrieval_history),
            "remaining_retrievals": self.remaining_retrievals,
            "retrieved_paths": sorted(list(self.retrieved_paths)),
            "history": [r.as_dict() for r in self.retrieval_history],
        }
