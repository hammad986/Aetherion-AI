"""
backend/execution/memory/reranker.py — Modular Hybrid Retrieval Reranker for Phase 6.
Combines dense semantic similarity, sparse lexical matching, trust tiers, recency decay,
and diversity/near-duplicate penalties with strict deterministic tie-breaking.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Dict, List, Optional, Tuple

from .embeddings import cosine_similarity
from .models import MemoryEntry, MemoryTrust

logger = logging.getLogger("aetherion.memory.reranker")

DEFAULT_TRUST_MULTIPLIERS = {
    MemoryTrust.USER_CONTROLLED.value: 1.0,
    MemoryTrust.AGENT_GENERATED.value: 0.85,
    MemoryTrust.IMPORTED.value: 0.70,
}


@dataclasses.dataclass
class CandidateItem:
    """Internal candidate representation before reranking."""
    entry: MemoryEntry
    semantic_score: float = 0.0
    lexical_score: float = 0.0
    recency_score: float = 1.0
    trust_multiplier: float = 1.0
    vector: Optional[List[float]] = None


class RetrievalResult(tuple):
    """
    Structured retrieval result item for Phase 6.
    Acts as a 2-tuple (entry, final_score) for complete backwards compatibility,
    while also exposing named attributes and dict conversion.
    """

    def __new__(
        cls,
        entry: MemoryEntry,
        final_score: float,
        semantic_score: float = 0.0,
        lexical_score: float = 0.0,
        trust_multiplier: float = 1.0,
        recency_score: float = 1.0,
        rank: int = 1,
    ):
        instance = super().__new__(cls, (entry, final_score))
        instance._entry = entry
        instance._final_score = final_score
        instance._semantic_score = semantic_score
        instance._lexical_score = lexical_score
        instance._trust_multiplier = trust_multiplier
        instance._recency_score = recency_score
        instance._rank = rank
        return instance

    @property
    def entry(self) -> MemoryEntry:
        return self._entry

    @property
    def memory(self) -> MemoryEntry:
        return self._entry

    @property
    def final_score(self) -> float:
        return self._final_score

    @property
    def score(self) -> float:
        return self._final_score

    @property
    def semantic_score(self) -> float:
        return self._semantic_score

    @property
    def lexical_score(self) -> float:
        return self._lexical_score

    @property
    def trust_multiplier(self) -> float:
        return self._trust_multiplier

    @property
    def recency_score(self) -> float:
        return self._recency_score

    @property
    def rank(self) -> int:
        return self._rank

    def __getitem__(self, item: Any) -> Any:
        if isinstance(item, str):
            if item in ("score", "final_score"):
                return self._final_score
            elif item in ("entry", "memory"):
                return self._entry
            elif item == "semantic_score":
                return self._semantic_score
            elif item == "lexical_score":
                return self._lexical_score
            elif item == "trust_multiplier":
                return self._trust_multiplier
            elif item == "recency_score":
                return self._recency_score
            elif item == "rank":
                return self._rank
            elif hasattr(self._entry, item):
                return getattr(self._entry, item)
            raise KeyError(item)
        return super().__getitem__(item)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def to_dict(self) -> Dict[str, Any]:
        d = self._entry.to_dict()
        d.update({
            "final_score": round(self._final_score, 4),
            "score": round(self._final_score, 4),
            "semantic_score": round(self._semantic_score, 4),
            "lexical_score": round(self._lexical_score, 4),
            "trust_multiplier": round(self._trust_multiplier, 4),
            "recency_score": round(self._recency_score, 4),
            "rank": self._rank,
        })
        return d


class RetrievalReranker:
    """
    Reranks candidate memories using multi-signal hybrid scoring, diversity deduplication,
    and deterministic tie-breaking.
    """

    def __init__(
        self,
        diversity_threshold: float = 0.90,
        duplicate_penalty: float = 0.80,
        trust_multipliers: Optional[Dict[str, float]] = None,
    ):
        self.diversity_threshold = diversity_threshold
        self.duplicate_penalty = duplicate_penalty
        self.trust_multipliers = trust_multipliers or DEFAULT_TRUST_MULTIPLIERS

    def rerank(
        self,
        candidates: List[CandidateItem],
        alpha: float = 0.5,
        min_score: float = 0.0,
        top_k: int = 10,
    ) -> List[RetrievalResult]:
        """
        Rerank candidates:
        1. Compute composite relevance score.
        2. Sort by raw score.
        3. Apply diversity penalty to near-duplicate items.
        4. Break ties deterministically.
        5. Return top_k results.
        """
        if not candidates:
            return []

        clamped_alpha = max(0.0, min(1.0, float(alpha)))
        scored_items: List[Tuple[CandidateItem, float]] = []

        for cand in candidates:
            # Composite formula
            # Relevance blends semantic and lexical scores
            blend = (clamped_alpha * cand.semantic_score) + ((1.0 - clamped_alpha) * cand.lexical_score)

            # Boost importance slightly (0.0 to 1.0 adds up to +10%)
            imp_factor = 1.0 + (0.10 * cand.entry.importance)

            # Trust multiplier
            t_mult = self.trust_multipliers.get(cand.entry.trust_level, 0.80)

            # Base final score
            raw_score = blend * t_mult * cand.recency_score * imp_factor
            score = max(0.0, min(1.0, raw_score))

            if score >= min_score:
                scored_items.append((cand, score))

        # Initial sort: score desc, importance desc, updated_at desc, id asc
        scored_items.sort(
            key=lambda x: (-x[1], -x[0].entry.importance, -x[0].entry.updated_at, x[0].entry.id),
        )


        # Diversity & Near-Duplicate Penalty step
        reranked: List[Tuple[CandidateItem, float]] = []
        selected_vectors: List[List[float]] = []

        for cand, score in scored_items:
            current_score = score

            # Check similarity against already selected items
            if cand.vector and selected_vectors:
                for past_v in selected_vectors:
                    sim = cosine_similarity(cand.vector, past_v)
                    if sim >= self.diversity_threshold:
                        # Near-duplicate detected; damp the score
                        current_score *= self.duplicate_penalty
                        break

            reranked.append((cand, current_score))
            if cand.vector:
                selected_vectors.append(cand.vector)

        # Final deterministic tie-breaking sort:
        # 1. -final_score
        # 2. -importance
        # 3. -updated_at
        # 4. id (lexicographical)
        reranked.sort(
            key=lambda x: (-x[1], -x[0].entry.importance, -x[0].entry.updated_at, x[0].entry.id),
        )

        # Build final RetrievalResult objects
        results: List[RetrievalResult] = []
        for rank_idx, (cand, final_sc) in enumerate(reranked[:top_k], 1):
            results.append(
                RetrievalResult(
                    entry=cand.entry,
                    final_score=final_sc,
                    semantic_score=cand.semantic_score,
                    lexical_score=cand.lexical_score,
                    trust_multiplier=self.trust_multipliers.get(cand.entry.trust_level, 0.80),
                    recency_score=cand.recency_score,
                    rank=rank_idx,
                )
            )

        return results
