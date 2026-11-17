"""
backend/execution/memory/feedback.py — Memory Outcome & Relevance Feedback Tracker for Phase 7.
Records execution outcome signals for retrieved memories and updates confidence dynamically.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from .models import MemoryFeedbackRecord
from .store import MemoryStore

logger = logging.getLogger("aetherion.memory.feedback")


class MemoryFeedbackTracker:
    """
    Records outcome feedback and dynamically adjusts memory confidence scores.
    """

    def __init__(self, store: Optional[MemoryStore] = None):
        self.store = store

    def record_feedback(
        self,
        owner_id: str,
        memory_id: str,
        session_id: Optional[str] = None,
        query: str = "",
        outcome: str = "success",
        relevance_score: float = 1.0,
    ) -> MemoryFeedbackRecord:
        """
        Record outcome feedback and adjust memory confidence score.
        Outcomes:
        - 'success': memory contributed to verified or successful task (boost confidence)
        - 'failure': task failed or memory caused incorrect plan (decay confidence)
        - 'partial': task succeeded partially (neutral / slight boost)
        - 'irrelevant': retrieved memory was not useful or ignored (slight decay)
        """
        rec = MemoryFeedbackRecord(
            id=f"fb_{uuid.uuid4().hex[:12]}",
            owner_id=owner_id,
            memory_id=memory_id,
            session_id=session_id,
            query=query,
            outcome=outcome,
            relevance_score=max(0.0, min(1.0, relevance_score)),
            created_at=time.time(),
        )

        if not self.store:
            return rec

        # Persist feedback record
        self.store.create_feedback(rec)

        # Dynamic confidence adjustment
        try:
            mem = self.store.get_memory(memory_id, owner_id)
            if mem:
                current_conf = mem.confidence
                delta = 0.0

                if outcome == "success":
                    delta = 0.05 * rec.relevance_score
                elif outcome == "failure":
                    delta = -0.10
                elif outcome == "irrelevant":
                    delta = -0.05
                elif outcome == "partial":
                    delta = 0.02 * rec.relevance_score

                new_conf = max(0.10, min(1.0, current_conf + delta))
                if abs(new_conf - current_conf) > 0.001:
                    self.store.update_memory(
                        memory_id=memory_id,
                        owner_id=owner_id,
                        confidence=round(new_conf, 4),
                        last_validated_at=time.time() if outcome == "success" else mem.last_validated_at,
                    )
        except Exception as ex:
            logger.debug("Error adjusting confidence on feedback: %s", ex)

        return rec

    def get_memory_feedback_summary(self, owner_id: str, memory_id: str) -> Dict[str, Any]:
        """Aggregate feedback statistics for a specific memory."""
        if not self.store:
            return {"total": 0, "success_count": 0, "failure_count": 0, "avg_relevance": 0.0}

        records = self.store.list_feedback(owner_id=owner_id, memory_id=memory_id)
        if not records:
            return {"total": 0, "success_count": 0, "failure_count": 0, "avg_relevance": 0.0}

        successes = sum(1 for r in records if r.outcome == "success")
        failures = sum(1 for r in records if r.outcome == "failure")
        avg_rel = sum(r.relevance_score for r in records) / len(records)

        return {
            "total": len(records),
            "success_count": successes,
            "failure_count": failures,
            "avg_relevance": round(avg_rel, 4),
        }
