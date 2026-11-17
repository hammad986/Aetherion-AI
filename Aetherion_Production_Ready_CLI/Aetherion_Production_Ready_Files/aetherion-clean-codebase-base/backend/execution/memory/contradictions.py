"""
backend/execution/memory/contradictions.py — Contradiction Detection Engine for Phase 7.
Identifies factual mismatches, polarity negations, value conflicts, and temporal superseding.
Adheres strictly to non-destructive intelligence principles: user memories are never silently deleted.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import (
    ContradictionRecord,
    ContradictionState,
    MemoryEntry,
    MemoryStatus,
)
from .store import MemoryStore

logger = logging.getLogger("aetherion.memory.contradictions")

# Rule-based regexes for key-value extraction
KEY_VALUE_PATTERNS = [
    re.compile(r"(?:port|port number)\s*(?:is|to|:|=|equals)\s*(\d+)", re.IGNORECASE),
    re.compile(r"(?:database|db)\s*(?:is|to|:|=|engine|system)\s*([a-zA-Z0-9_\-]+)", re.IGNORECASE),
    re.compile(r"(?:python|node|ruby|go|java)\s*(?:version|ver)?\s*(?:is|to|:|=)?\s*(\d+(?:\.\d+)*)", re.IGNORECASE),
    re.compile(r"(?:environment|env)\s*(?:is|to|:|=|mode)\s*([a-zA-Z0-9_\-]+)", re.IGNORECASE),
    re.compile(r"(?:framework|ui framework|library)\s*(?:is|to|:|=|using)\s*([a-zA-Z0-9_\-]+)", re.IGNORECASE),
    re.compile(r"(?:host|hostname|server)\s*(?:is|to|:|=|at)\s*([a-zA-Z0-9_\-\.]+)", re.IGNORECASE),
]

NEGATION_WORDS = {
    "not", "no", "never", "disable", "disabled", "disabling",
    "don't", "dont", "cannot", "cant", "deprecated", "neither",
    "unsupported", "false", "off", "disallow", "disallowed", "stop", "prevent",
}

AFFIRMATION_WORDS = {
    "enable", "enabled", "enabling", "true", "on", "yes",
    "active", "supported", "allow", "allowed", "start", "permit", "mandatory",
}

SUPERSEDE_PATTERNS = [
    re.compile(r"\b(?:now|updated to|upgrade to|migrated to|superseded by|supersedes)\b", re.IGNORECASE),
    re.compile(r"\b(?:no longer|deprecated in favor of|replaced with|switched to)\b", re.IGNORECASE),
    re.compile(r"\b(?:new version|version \d+ replaces)\b", re.IGNORECASE),
]

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "and", "or", "in", "on", "at",
    "to", "for", "with", "by", "from", "of", "about", "as", "into", "like", "through",
    "after", "over", "between", "out", "against", "during", "without", "before", "under",
    "around", "among", "this", "that", "these", "those", "it", "its", "we", "our",
}


def _tokenize(text: str) -> List[str]:
    """Tokenize and normalize text into clean words."""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return [w for w in cleaned.split() if w]


def _content_words(text: str) -> Set[str]:
    """Extract content words excluding common stopwords."""
    tokens = _tokenize(text)
    return {w for w in tokens if w not in STOPWORDS and len(w) > 1}


def _jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a.intersection(set_b))
    union = len(set_a.union(set_b))
    return float(intersection) / float(union) if union > 0 else 0.0


def _extract_key_values(text: str) -> Dict[str, str]:
    """Extract known structured configuration key-values from text."""
    kvs = {}
    lower = text.lower()
    for pattern in KEY_VALUE_PATTERNS:
        match = pattern.search(lower)
        if match:
            # Pattern index identifies the category
            kvs[pattern.pattern] = match.group(1).strip()
    return kvs


class ContradictionDetector:
    """
    Intelligent multi-tenant contradiction detection and reconciliation engine.
    Detects polarity negations, value conflicts, and temporal superseding.
    """

    def __init__(self, store: Optional[MemoryStore] = None):
        self.store = store

    def compare_memories(
        self,
        mem_a: MemoryEntry,
        mem_b: MemoryEntry,
    ) -> Optional[ContradictionRecord]:
        """
        Compare two memories for factual or logical contradiction.
        Returns a ContradictionRecord if a conflict is detected, else None.
        """
        if mem_a.id == mem_b.id:
            return None
        if mem_a.owner_id != mem_b.owner_id:
            return None  # Strict tenant isolation

        # Combine title and content for comprehensive analysis
        full_a = f"{mem_a.title} {mem_a.content}"
        full_b = f"{mem_b.title} {mem_b.content}"

        words_a = _content_words(full_a)
        words_b = _content_words(full_b)
        overlap = _jaccard_similarity(words_a, words_b)

        # 1. Structured Value Conflict Detection (e.g. port 8080 vs port 3000)
        kvs_a = _extract_key_values(full_a)
        kvs_b = _extract_key_values(full_b)

        common_keys = set(kvs_a.keys()).intersection(set(kvs_b.keys()))
        for key in common_keys:
            val_a = kvs_a[key]
            val_b = kvs_b[key]
            if val_a != val_b:
                desc = f"Conflicting values detected for attribute pattern '{key}': '{val_a}' vs '{val_b}'."
                return ContradictionRecord(
                    id=f"contra_{uuid.uuid4().hex[:12]}",
                    owner_id=mem_a.owner_id,
                    memory_id_a=mem_a.id,
                    memory_id_b=mem_b.id,
                    contradiction_type="value_conflict",
                    description=desc,
                    state=ContradictionState.CONFLICTING.value,
                    confidence=0.92,
                    created_at=time.time(),
                    updated_at=time.time(),
                )

        # 2. Polarity / Negation Conflict Detection
        # If topics overlap (>= 0.20 or shared words in title >= 0.30) and one asserts what the other negates
        title_words_a = _content_words(mem_a.title)
        title_words_b = _content_words(mem_b.title)
        title_overlap = _jaccard_similarity(title_words_a, title_words_b)

        if overlap >= 0.20 or title_overlap >= 0.30:
            tokens_a = set(_tokenize(full_a))
            tokens_b = set(_tokenize(full_b))

            has_neg_a = bool(tokens_a.intersection(NEGATION_WORDS))
            has_neg_b = bool(tokens_b.intersection(NEGATION_WORDS))
            has_aff_a = bool(tokens_a.intersection(AFFIRMATION_WORDS))
            has_aff_b = bool(tokens_b.intersection(AFFIRMATION_WORDS))

            if (has_neg_a and (has_aff_b or not has_neg_b)) or (has_neg_b and (has_aff_a or not has_neg_a)):
                if (has_neg_a and not has_neg_b) or (has_neg_b and not has_neg_a):
                    desc = (
                        f"Polarity negation detected between '{mem_a.title}' and '{mem_b.title}' "
                        f"with shared context (overlap: {overlap:.2f})."
                    )
                    return ContradictionRecord(
                        id=f"contra_{uuid.uuid4().hex[:12]}",
                        owner_id=mem_a.owner_id,
                        memory_id_a=mem_a.id,
                        memory_id_b=mem_b.id,
                        contradiction_type="negation",
                        description=desc,
                        state=ContradictionState.CONFLICTING.value,
                        confidence=0.88,
                        created_at=time.time(),
                        updated_at=time.time(),
                    )

        # 3. Temporal Supersede Detection
        # High overlap (>= 0.40) where one memory explicitly signals updates / deprecation
        if overlap >= 0.40:
            supersede_in_a = any(p.search(full_a) for p in SUPERSEDE_PATTERNS)
            supersede_in_b = any(p.search(full_b) for p in SUPERSEDE_PATTERNS)

            if supersede_in_b and mem_b.updated_at >= mem_a.updated_at:
                desc = f"Memory '{mem_b.title}' appears to temporally supersede '{mem_a.title}'."
                return ContradictionRecord(
                    id=f"contra_{uuid.uuid4().hex[:12]}",
                    owner_id=mem_a.owner_id,
                    memory_id_a=mem_a.id,
                    memory_id_b=mem_b.id,
                    contradiction_type="temporal_supersede",
                    description=desc,
                    state=ContradictionState.SUPERSEDED.value,
                    confidence=0.85,
                    created_at=time.time(),
                    updated_at=time.time(),
                )
            elif supersede_in_a and mem_a.updated_at >= mem_b.updated_at:
                desc = f"Memory '{mem_a.title}' appears to temporally supersede '{mem_b.title}'."
                return ContradictionRecord(
                    id=f"contra_{uuid.uuid4().hex[:12]}",
                    owner_id=mem_a.owner_id,
                    memory_id_a=mem_b.id,
                    memory_id_b=mem_a.id,
                    contradiction_type="temporal_supersede",
                    description=desc,
                    state=ContradictionState.SUPERSEDED.value,
                    confidence=0.85,
                    created_at=time.time(),
                    updated_at=time.time(),
                )

        return None

    def detect_contradictions_for_memory(
        self,
        memory: MemoryEntry,
        candidates: Optional[List[MemoryEntry]] = None,
        persist: bool = False,
    ) -> List[ContradictionRecord]:
        """
        Detect contradictions between a specific memory and candidates.
        """
        if candidates is None:
            if not self.store:
                return []
            candidates = self.store.list_memories(
                owner_id=memory.owner_id,
                status=MemoryStatus.ACTIVE.value,
            )

        results: List[ContradictionRecord] = []
        seen_pairs: Set[Tuple[str, str]] = set()

        for cand in candidates:
            if cand.id == memory.id:
                continue
            pair_key = tuple(sorted([memory.id, cand.id]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            record = self.compare_memories(memory, cand)
            if record:
                results.append(record)
                if persist and self.store:
                    try:
                        self.store.create_contradiction(record)
                    except Exception as ex:
                        logger.error("Failed persisting contradiction record: %s", ex)

        return results

    def scan_tenant_contradictions(
        self,
        owner_id: str,
        limit: int = 100,
        persist: bool = False,
    ) -> List[ContradictionRecord]:
        """
        Scan all active memories of a tenant for pairwise contradictions.
        """
        if not self.store:
            return []

        memories = self.store.list_memories(
            owner_id=owner_id,
            status=MemoryStatus.ACTIVE.value,
            limit=limit,
        )

        detected: List[ContradictionRecord] = []
        seen_pairs: Set[Tuple[str, str]] = set()

        for i in range(len(memories)):
            for j in range(i + 1, len(memories)):
                mem_a = memories[i]
                mem_b = memories[j]
                pair_key = tuple(sorted([mem_a.id, mem_b.id]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                record = self.compare_memories(mem_a, mem_b)
                if record:
                    detected.append(record)
                    if persist:
                        try:
                            self.store.create_contradiction(record)
                        except Exception as ex:
                            logger.error("Failed persisting contradiction record: %s", ex)

        return detected

    def resolve_contradiction(
        self,
        contradiction_id: str,
        owner_id: str,
        resolution_strategy: str,
        winner_memory_id: Optional[str] = None,
    ) -> bool:
        """
        Resolve a detected contradiction with non-destructive state adjustments.
        Valid strategies: 'keep_a', 'keep_b', 'supersede', 'dismiss', 'resolve'.
        NEVER deletes underlying memories.
        """
        if not self.store:
            return False

        records = self.store.list_contradictions(owner_id=owner_id)
        record = next((r for r in records if r.id == contradiction_id), None)
        if not record:
            return False

        new_state = ContradictionState.RESOLVED.value

        if resolution_strategy == "dismiss":
            new_state = ContradictionState.DISMISSED.value
        elif resolution_strategy == "supersede":
            new_state = ContradictionState.SUPERSEDED.value
            # Mark the older memory as STALE (non-destructive)
            mem_a = self.store.get_memory(record.memory_id_a, owner_id)
            mem_b = self.store.get_memory(record.memory_id_b, owner_id)
            if mem_a and mem_b:
                older = mem_a if mem_a.created_at <= mem_b.created_at else mem_b
                self.store.update_memory(
                    memory_id=older.id,
                    owner_id=owner_id,
                    status=MemoryStatus.STALE.value,
                )
        elif resolution_strategy in ("keep_a", "keep_b"):
            new_state = ContradictionState.RESOLVED.value
            loser_id = record.memory_id_b if resolution_strategy == "keep_a" else record.memory_id_a
            # Mark loser as STALE (non-destructive)
            self.store.update_memory(
                memory_id=loser_id,
                owner_id=owner_id,
                status=MemoryStatus.STALE.value,
            )
        elif resolution_strategy == "resolve":
            new_state = ContradictionState.RESOLVED.value
            if winner_memory_id:
                loser_id = record.memory_id_b if winner_memory_id == record.memory_id_a else record.memory_id_a
                self.store.update_memory(
                    memory_id=loser_id,
                    owner_id=owner_id,
                    status=MemoryStatus.STALE.value,
                )

        return self.store.update_contradiction_state(
            contradiction_id=contradiction_id,
            owner_id=owner_id,
            new_state=new_state,
        )
