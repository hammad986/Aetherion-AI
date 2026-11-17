"""
tests/integration/test_retrieval_quality.py — Ground-Truth Retrieval Quality Evaluation Test.
Executes the 25-fixture ground-truth suite and validates Recall@5 >= 0.85, MRR >= 0.80,
and 100% prompt-injection context framing.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from backend.execution.memory import MemoryRetriever, MemoryStore, run_retrieval_benchmark


def test_retrieval_quality_ground_truth_benchmark():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "eval_retrieval.db"
        store = MemoryStore(db_path=db_path)
        retriever = MemoryRetriever(store=store)

        report = run_retrieval_benchmark(
            retriever=retriever,
            store=store,
            eval_owner_id="quality_eval_tenant",
        )

        assert report["total_test_cases"] == 25
        assert report["recall_at_5"] >= 0.85, f"Recall@5 ({report['recall_at_5']}) below target 0.85"
        assert report["mrr"] >= 0.80, f"MRR ({report['mrr']}) below target 0.80"
        assert report["prompt_injection_safety_verified"] is True
        assert report["all_thresholds_met"] is True
