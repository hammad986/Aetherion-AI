"""
execution/replay_compression_governance.py — Phase Z40E: Replay Compression Governance
========================================================================================
Prevents replay systems from becoming operationally heavy.

Subsystems:
  • ReplayTierClassifier    — HOT / ACTIVE / HISTORICAL / ARCHIVED classification
  • ReplayCompactor         — compresses historical low-risk replay branches
  • HydrationDisciplineEngine — ensures only high-priority chains are fully hydrated
"""

import sqlite3
import time
import logging
from enum import Enum
from typing import Dict, List, Optional, Tuple
from execution.store import ExecutionStore

logger = logging.getLogger("nexora.replay_compression_governance")


# ── Replay tier ────────────────────────────────────────────────────────────────

class ReplayTier(str, Enum):
    HOT        = "HOT"         # active, being used right now
    ACTIVE     = "ACTIVE"      # recently accessed, kept full
    HISTORICAL = "HISTORICAL"  # older, candidate for compression
    ARCHIVED   = "ARCHIVED"    # very old, compressed to summary only


# ── Tier classifier ────────────────────────────────────────────────────────────

class ReplayTierClassifier:
    """
    Classifies execution replay chains into operational tiers based on recency,
    status, and access patterns.
    """

    HOT_SECS        = 5   * 60     # < 5 minutes old → HOT
    ACTIVE_SECS     = 30  * 60     # < 30 minutes    → ACTIVE
    HISTORICAL_SECS = 6   * 3600   # < 6 hours       → HISTORICAL
    # Older → ARCHIVED

    def classify(self, execution: Dict) -> ReplayTier:
        status     = execution.get("status", "")
        updated_at = execution.get("updated_at", 0) or 0
        age        = time.time() - updated_at

        if status in ("running", "queued"):
            return ReplayTier.HOT

        if age < self.HOT_SECS:
            return ReplayTier.HOT
        if age < self.ACTIVE_SECS:
            return ReplayTier.ACTIVE
        if age < self.HISTORICAL_SECS:
            return ReplayTier.HISTORICAL
        return ReplayTier.ARCHIVED

    def classify_all(self, store: ExecutionStore, limit: int = 1000) -> Dict[str, List[str]]:
        tiers: Dict[str, List[str]] = {t.value: [] for t in ReplayTier}
        with sqlite3.connect(store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT execution_id, status, updated_at FROM executions ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        for row in rows:
            tier = self.classify(dict(row))
            tiers[tier.value].append(row["execution_id"])
        return tiers


# ── Replay compactor ───────────────────────────────────────────────────────────

class ReplayCompactor:
    """
    Compresses HISTORICAL and ARCHIVED replay chains into minimal summaries.
    Never touches HOT or ACTIVE chains.
    Source event log remains append-only — this produces a summary index only.
    """

    def compact_chain(self, execution_id: str, events: List[Dict]) -> Dict:
        """
        Produces a compact summary of a replay chain.
        Preserves: start time, end time, event count, event type histogram, lineage.
        """
        if not events:
            return {"execution_id": execution_id, "compacted": False, "reason": "no_events"}

        event_types: Dict[str, int] = {}
        for e in events:
            t = e.get("event_type", "unknown")
            event_types[t] = event_types.get(t, 0) + 1

        start_ts = events[0]["timestamp"]
        end_ts   = events[-1]["timestamp"]

        return {
            "execution_id":   execution_id,
            "compacted":      True,
            "event_count":    len(events),
            "duration_secs":  round(end_ts - start_ts, 2),
            "start_ts":       start_ts,
            "end_ts":         end_ts,
            "event_histogram": event_types,
            "first_event_id":  events[0]["event_id"],
            "last_event_id":   events[-1]["event_id"],
            "note": "Source event log is UNCHANGED. This is a read-only compact view.",
        }

    def compact_tier(
        self,
        store: ExecutionStore,
        execution_ids: List[str],
        max_compact: int = 50,
    ) -> List[Dict]:
        """Compact a list of execution IDs. Returns compact summaries."""
        results = []
        for eid in execution_ids[:max_compact]:
            try:
                events = store.get_events(eid)
                summary = self.compact_chain(eid, events)
                results.append(summary)
            except Exception as exc:
                logger.warning("[ReplayCompactor] Could not compact %s: %s", eid, exc)
        return results


# ── Hydration discipline engine ────────────────────────────────────────────────

class HydrationDisciplineEngine:
    """
    Controls which replay chains receive full hydration (all events loaded)
    versus summary-only hydration.

    High-priority chains: HOT, ACTIVE, recently failed executions.
    Low-priority chains:  HISTORICAL, ARCHIVED, completed long ago.
    """

    def should_hydrate_fully(
        self,
        execution_id: str,
        tier: ReplayTier,
        event_count: int,
        resource_severity: str = "LIGHT",
    ) -> Tuple[bool, str]:
        """
        Returns (should_hydrate_fully: bool, reason: str).
        Under resource pressure, only HOT chains get full hydration.
        """
        if tier == ReplayTier.HOT:
            return True, "hot_chain_always_hydrated"

        if resource_severity in ("SATURATED", "CRITICAL"):
            if tier == ReplayTier.ACTIVE and event_count < 200:
                return True, "active_under_500_events_ok_in_pressure"
            return False, f"resource_pressure_{resource_severity}_suppresses_hydration"

        if tier == ReplayTier.ACTIVE:
            return True, "active_chain_hydrated"

        if tier == ReplayTier.HISTORICAL and event_count < 500:
            return True, "historical_small_chain_ok"

        return False, f"tier_{tier}_deferred_to_summary"

    def hydration_plan(
        self,
        tiers: Dict[str, List[str]],
        resource_severity: str,
        store: ExecutionStore,
    ) -> Dict:
        """
        Returns a plan dict showing which executions will be fully hydrated
        vs summary-only.
        """
        fully_hydrate  = []
        summary_only   = []

        for tier_name, eids in tiers.items():
            tier = ReplayTier(tier_name)
            for eid in eids[:20]:   # sample first 20 per tier for planning
                try:
                    with sqlite3.connect(store.db_path) as conn:
                        ec = conn.execute(
                            "SELECT COUNT(*) FROM event_log WHERE execution_id=?", (eid,)
                        ).fetchone()[0]
                except Exception:
                    ec = 0

                ok, reason = self.should_hydrate_fully(eid, tier, ec, resource_severity)
                if ok:
                    fully_hydrate.append({"execution_id": eid, "tier": tier_name, "reason": reason})
                else:
                    summary_only.append({"execution_id": eid, "tier": tier_name, "reason": reason})

        return {
            "planned_at":       time.time(),
            "resource_severity": resource_severity,
            "fully_hydrated":   len(fully_hydrate),
            "summary_only":     len(summary_only),
            "fully_hydrate":    fully_hydrate,
            "summary_only_list": summary_only,
        }


# ── Unified replay governance manager ─────────────────────────────────────────

class ReplayCompressionGovernor:
    """Top-level facade for Z40E."""

    def __init__(self, store: ExecutionStore):
        self.store     = store
        self.classifier = ReplayTierClassifier()
        self.compactor  = ReplayCompactor()
        self.hydration  = HydrationDisciplineEngine()

    def tier_report(self, limit: int = 500) -> Dict:
        tiers = self.classifier.classify_all(self.store, limit)
        return {
            "reported_at": time.time(),
            "tier_counts": {k: len(v) for k, v in tiers.items()},
            "tiers":       tiers,
        }

    def compact_historical(self, max_compact: int = 50) -> Dict:
        tiers = self.classifier.classify_all(self.store)
        targets = tiers.get("HISTORICAL", []) + tiers.get("ARCHIVED", [])
        summaries = self.compactor.compact_tier(self.store, targets, max_compact)
        return {
            "compacted_at":  time.time(),
            "compacted_count": len(summaries),
            "summaries":     summaries,
        }

    def hydration_plan(self, resource_severity: str = "LIGHT") -> Dict:
        tiers = self.classifier.classify_all(self.store, 200)
        return self.hydration.hydration_plan(tiers, resource_severity, self.store)
