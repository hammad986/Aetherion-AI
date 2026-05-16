"""
execution/cognitive_integrity.py — Phase Z39A: Cognitive Integrity Engine
==========================================================================
Protects persistent runtime cognition from corruption, replay drift,
and lineage instability.

Subsystems:
  • IntegritySeverity   — severity classification (LOW / DEGRADED / UNSTABLE / CORRUPTED)
  • LineageValidator    — ancestry consistency, loop detection, orphan detection
  • IntegrityScanner    — continuous verification of all execution lineage in the store
"""

import sqlite3
import time
import logging
from enum import Enum
from typing import Dict, List, Optional, Tuple
from execution.store import ExecutionStore

logger = logging.getLogger("nexora.cognitive_integrity")


# ── Severity classification ────────────────────────────────────────────────────

class IntegritySeverity(str, Enum):
    LOW        = "LOW"
    DEGRADED   = "DEGRADED"
    UNSTABLE   = "UNSTABLE"
    CORRUPTED  = "CORRUPTED"


# ── Lineage validator ──────────────────────────────────────────────────────────

class LineageValidator:
    """
    Validates execution lineage graphs for structural correctness.
    Detects loops, orphans, invalid ancestry, and broken replay branches.
    """

    def __init__(self, store: ExecutionStore):
        self.store = store

    # ── Public checks ─────────────────────────────────────────────────────────

    def check_ancestry_consistency(self, execution_id: str) -> Dict:
        """Verify that every parent reference in the lineage chain is resolvable."""
        visited = []
        current = execution_id
        depth = 0
        issues = []

        with sqlite3.connect(self.store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            while current and depth < 50:
                row = conn.execute(
                    "SELECT execution_id, parent_execution_id, status FROM executions WHERE execution_id=?",
                    (current,)
                ).fetchone()

                if row is None:
                    if depth > 0:
                        issues.append(f"Ancestor '{current}' missing from store (orphan reference at depth {depth})")
                    break

                if current in visited:
                    issues.append(f"Dependency loop detected at node '{current}' (depth {depth})")
                    break

                visited.append(current)
                current = row["parent_execution_id"]
                depth += 1

        return {
            "execution_id": execution_id,
            "ancestry_depth": depth,
            "visited_chain": visited,
            "issues": issues,
            "valid": len(issues) == 0,
        }

    def detect_dependency_loops(self, all_executions: List[Dict]) -> List[Dict]:
        """Detect circular parent→child references across all known executions."""
        id_to_parent = {e["execution_id"]: e.get("parent_execution_id") for e in all_executions}
        loops = []

        for start_id in id_to_parent:
            visited = set()
            current = start_id
            while current:
                if current in visited:
                    loops.append({"start": start_id, "loop_node": current})
                    break
                visited.add(current)
                current = id_to_parent.get(current)

        return loops

    def detect_orphan_nodes(self, all_executions: List[Dict]) -> List[str]:
        """Identify executions that reference non-existent parents."""
        known_ids = {e["execution_id"] for e in all_executions}
        orphans = []
        for e in all_executions:
            parent = e.get("parent_execution_id")
            if parent and parent not in known_ids:
                orphans.append(e["execution_id"])
        return orphans

    def validate_replay_ancestry(self, execution_id: str) -> Dict:
        """
        Verify that the event log for an execution is temporally ordered
        and that all correlation references are internally consistent.
        """
        events = self.store.get_events(execution_id)
        issues = []

        if not events:
            return {"execution_id": execution_id, "valid": True, "issues": [], "event_count": 0}

        last_ts = events[0]["timestamp"]
        seen_ids = set()

        for i, evt in enumerate(events):
            ts = evt["timestamp"]
            eid = evt["event_id"]

            if ts < last_ts - 0.001:
                issues.append(f"Temporal ordering violation at event index {i}: ts={ts:.3f} < prev={last_ts:.3f}")
            if eid in seen_ids:
                issues.append(f"Duplicate event_id '{eid}' at index {i}")

            seen_ids.add(eid)
            last_ts = ts

        return {
            "execution_id": execution_id,
            "event_count": len(events),
            "valid": len(issues) == 0,
            "issues": issues,
        }


# ── Integrity scanner ──────────────────────────────────────────────────────────

class IntegrityScanner:
    """
    Continuously verifies the health of all execution lineage in the store.
    Classifies overall system integrity and emits severity verdicts.
    """

    def __init__(self, store: ExecutionStore):
        self.store = store
        self.validator = LineageValidator(store)
        self._last_scan: Optional[Dict] = None
        self._last_scan_ts: float = 0.0

    def scan(self, max_executions: int = 200) -> Dict:
        """
        Perform a full integrity scan. Returns severity, findings, and metrics.
        Results are cached for 60 seconds to avoid hammering SQLite.
        """
        now = time.time()
        if self._last_scan and (now - self._last_scan_ts) < 60:
            return self._last_scan

        findings = []
        total_checked = 0
        loop_count = 0
        orphan_count = 0
        temporal_violations = 0
        ancestry_issues = 0

        with sqlite3.connect(self.store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT execution_id, parent_execution_id, status FROM executions ORDER BY updated_at DESC LIMIT ?",
                (max_executions,)
            ).fetchall()

        all_executions = [dict(r) for r in rows]
        total_checked = len(all_executions)

        # Loop detection across all nodes
        loops = self.validator.detect_dependency_loops(all_executions)
        loop_count = len(loops)
        for lp in loops:
            findings.append({"type": "dependency_loop", "severity": IntegritySeverity.CORRUPTED, "detail": lp})

        # Orphan detection
        orphans = self.validator.detect_orphan_nodes(all_executions)
        orphan_count = len(orphans)
        for oid in orphans:
            findings.append({"type": "orphan_node", "severity": IntegritySeverity.DEGRADED, "detail": {"execution_id": oid}})

        # Ancestry + replay validation (sample up to 50 recent executions)
        for row in all_executions[:50]:
            eid = row["execution_id"]

            anc = self.validator.check_ancestry_consistency(eid)
            if not anc["valid"]:
                ancestry_issues += 1
                for issue in anc["issues"]:
                    findings.append({"type": "ancestry_inconsistency", "severity": IntegritySeverity.UNSTABLE, "detail": {"execution_id": eid, "issue": issue}})

            replay = self.validator.validate_replay_ancestry(eid)
            if not replay["valid"]:
                temporal_violations += len(replay["issues"])
                for issue in replay["issues"]:
                    findings.append({"type": "replay_temporal_violation", "severity": IntegritySeverity.DEGRADED, "detail": {"execution_id": eid, "issue": issue}})

        # Classify overall severity
        severity = self._classify_severity(loop_count, orphan_count, temporal_violations, ancestry_issues, total_checked)

        result = {
            "scanned_at": now,
            "total_executions_checked": total_checked,
            "severity": severity,
            "loop_count": loop_count,
            "orphan_count": orphan_count,
            "temporal_violations": temporal_violations,
            "ancestry_issues": ancestry_issues,
            "total_findings": len(findings),
            "findings": findings[:50],
        }
        self._last_scan = result
        self._last_scan_ts = now
        logger.info(
            "[CognitiveIntegrity] Scan complete — severity=%s | loops=%d | orphans=%d | violations=%d | checked=%d",
            severity, loop_count, orphan_count, temporal_violations, total_checked
        )
        return result

    def _classify_severity(
        self,
        loops: int,
        orphans: int,
        temporal_violations: int,
        ancestry_issues: int,
        total: int,
    ) -> IntegritySeverity:
        if loops > 0:
            return IntegritySeverity.CORRUPTED
        if ancestry_issues > max(3, total * 0.10):
            return IntegritySeverity.UNSTABLE
        if orphans > max(5, total * 0.15) or temporal_violations > 10:
            return IntegritySeverity.DEGRADED
        return IntegritySeverity.LOW

    def get_last_scan(self) -> Optional[Dict]:
        return self._last_scan
