"""
execution/memory_governance.py — Phase Z39E: Memory Governance + DB Discipline
===============================================================================
Prevents cognition persistence from destabilising the platform through
safe pruning, WAL management, fragmentation detection, and retention tiers.

Subsystems:
  • RetentionPolicy        — classifies data into hot/warm/cold/archived tiers
  • SQLiteCompactionEngine — safe pruning and WAL growth control
  • FragmentationDetector  — detects broken lineage chains and hydration inefficiency
  • MemoryGovernor         — unified facade
"""

import sqlite3
import os
import time
import logging
from typing import Dict, List, Optional
from execution.store import ExecutionStore

logger = logging.getLogger("nexora.memory_governance")


# ── Retention policy ───────────────────────────────────────────────────────────

class RetentionPolicy:
    """
    Classifies execution data into four memory tiers based on recency and status.

    HOT      — last 24 hours  (always kept, never pruned)
    WARM     — 1–7 days       (kept, eligible for compression)
    COLD     — 7–30 days      (payload compressed, events summarised)
    ARCHIVED — >30 days       (events pruned, snapshot retained for audit)
    """

    HOT_SECS      = 24 * 3600
    WARM_SECS     = 7  * 86400
    COLD_SECS     = 30 * 86400

    def classify(self, updated_at: float) -> str:
        age = time.time() - updated_at
        if age < self.HOT_SECS:
            return "HOT"
        if age < self.WARM_SECS:
            return "WARM"
        if age < self.COLD_SECS:
            return "COLD"
        return "ARCHIVED"

    def classify_all(self, store: ExecutionStore, limit: int = 2000) -> Dict[str, List[str]]:
        """Return execution_ids bucketed by tier."""
        tiers: Dict[str, List[str]] = {"HOT": [], "WARM": [], "COLD": [], "ARCHIVED": []}
        with sqlite3.connect(store.db_path) as conn:
            rows = conn.execute(
                "SELECT execution_id, updated_at FROM executions ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        for eid, updated_at in rows:
            tier = self.classify(updated_at or 0)
            tiers[tier].append(eid)
        return tiers


# ── SQLite compaction engine ───────────────────────────────────────────────────

class SQLiteCompactionEngine:
    """
    Provides safe, replay-preserving compaction operations:
      • prune_archived_events()  — removes event_log rows for ARCHIVED executions
      • checkpoint_wal()         — forces WAL checkpoint to bound file growth
      • vacuum_if_needed()       — runs VACUUM when DB is significantly bloated
    """

    WAL_MAX_PAGES = 1000   # checkpoint after this many WAL pages

    def __init__(self, store: ExecutionStore, policy: RetentionPolicy):
        self.store = store
        self.policy = policy

    def prune_archived_events(self, dry_run: bool = False) -> Dict:
        """
        Safely removes event_log rows for ARCHIVED executions
        (those older than 30 days). Execution snapshot rows are kept.
        """
        tiers = self.policy.classify_all(self.store)
        archived_ids = tiers["ARCHIVED"]

        if not archived_ids:
            return {"pruned_event_rows": 0, "dry_run": dry_run, "archived_executions": 0}

        if dry_run:
            with sqlite3.connect(self.store.db_path) as conn:
                placeholders = ",".join("?" * len(archived_ids))
                count = conn.execute(
                    f"SELECT COUNT(*) FROM event_log WHERE execution_id IN ({placeholders})",
                    archived_ids
                ).fetchone()[0]
            return {"pruned_event_rows": count, "dry_run": True, "archived_executions": len(archived_ids)}

        pruned = 0
        # Process in batches of 100 to avoid locking
        for i in range(0, len(archived_ids), 100):
            batch = archived_ids[i:i + 100]
            placeholders = ",".join("?" * len(batch))
            with sqlite3.connect(self.store.db_path) as conn:
                cur = conn.execute(
                    f"DELETE FROM event_log WHERE execution_id IN ({placeholders})", batch
                )
                pruned += cur.rowcount

        logger.info("[MemoryGovernance] Pruned %d event rows from %d archived executions", pruned, len(archived_ids))
        return {"pruned_event_rows": pruned, "dry_run": False, "archived_executions": len(archived_ids)}

    def checkpoint_wal(self) -> Dict:
        """Forces a WAL checkpoint to prevent unbounded log growth."""
        try:
            with sqlite3.connect(self.store.db_path) as conn:
                result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            log_size, checkpointed, _ = result
            logger.info("[MemoryGovernance] WAL checkpoint: log_size=%d checkpointed=%d", log_size, checkpointed)
            return {"wal_log_size": log_size, "checkpointed_pages": checkpointed, "success": True}
        except Exception as exc:
            logger.warning("[MemoryGovernance] WAL checkpoint failed: %s", exc)
            return {"success": False, "error": str(exc)}

    def vacuum_if_needed(self) -> Dict:
        """Runs VACUUM only if the DB file exceeds a growth threshold."""
        db_path = self.store.db_path
        if not os.path.exists(db_path):
            return {"vacuumed": False, "reason": "db_not_found"}

        size_mb = os.path.getsize(db_path) / (1024 * 1024)
        if size_mb < 50:
            return {"vacuumed": False, "size_mb": round(size_mb, 2), "reason": "below_threshold"}

        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute("VACUUM")
            new_size = os.path.getsize(db_path) / (1024 * 1024)
            logger.info("[MemoryGovernance] VACUUM complete: %.1f MB → %.1f MB", size_mb, new_size)
            return {"vacuumed": True, "size_before_mb": round(size_mb, 2), "size_after_mb": round(new_size, 2)}
        except Exception as exc:
            logger.warning("[MemoryGovernance] VACUUM failed: %s", exc)
            return {"vacuumed": False, "error": str(exc)}

    def db_stats(self) -> Dict:
        """Returns current DB file size and page stats."""
        db_path = self.store.db_path
        stats: Dict = {"db_path": db_path}
        if os.path.exists(db_path):
            stats["size_mb"] = round(os.path.getsize(db_path) / (1024 * 1024), 3)
            wal_path = db_path + "-wal"
            stats["wal_size_mb"] = round(os.path.getsize(wal_path) / (1024 * 1024), 3) if os.path.exists(wal_path) else 0.0
        with sqlite3.connect(db_path) as conn:
            page_count = conn.execute("PRAGMA page_count").fetchone()[0]
            page_size  = conn.execute("PRAGMA page_size").fetchone()[0]
            free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
        stats["page_count"] = page_count
        stats["page_size_bytes"] = page_size
        stats["free_pages"] = free_pages
        stats["fragmentation_ratio"] = round(free_pages / max(page_count, 1), 4)
        return stats


# ── Fragmentation detector ─────────────────────────────────────────────────────

class FragmentationDetector:
    """
    Detects:
      • fragmented lineage chains  — executions with 0 events
      • replay hydration gaps      — executions with events but missing lifecycle events
      • stale evolution records    — executions stuck in non-terminal state for >6 hours
    """

    def __init__(self, store: ExecutionStore):
        self.store = store

    def scan(self, limit: int = 500) -> Dict:
        now = time.time()
        empty_lineage = []
        hydration_gaps = []
        stale_evolution = []

        with sqlite3.connect(self.store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            exec_rows = conn.execute(
                "SELECT execution_id, status, updated_at FROM executions ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            ).fetchall()

            for row in exec_rows:
                eid = row["execution_id"]
                age = now - (row["updated_at"] or now)

                event_count = conn.execute(
                    "SELECT COUNT(*) FROM event_log WHERE execution_id=?", (eid,)
                ).fetchone()[0]

                if event_count == 0:
                    empty_lineage.append(eid)
                else:
                    has_start = conn.execute(
                        "SELECT 1 FROM event_log WHERE execution_id=? AND event_type='task.started' LIMIT 1",
                        (eid,)
                    ).fetchone()
                    if not has_start:
                        hydration_gaps.append({"execution_id": eid, "event_count": event_count})

                if row["status"] in ("running", "queued") and age > 6 * 3600:
                    stale_evolution.append({"execution_id": eid, "status": row["status"], "stuck_hours": round(age / 3600, 1)})

        return {
            "scanned_at": now,
            "empty_lineage_count": len(empty_lineage),
            "empty_lineage_ids": empty_lineage[:20],
            "hydration_gap_count": len(hydration_gaps),
            "hydration_gaps": hydration_gaps[:20],
            "stale_evolution_count": len(stale_evolution),
            "stale_evolutions": stale_evolution[:20],
        }


# ── Unified memory governor ────────────────────────────────────────────────────

class MemoryGovernor:
    """Top-level facade combining all Z39E subsystems."""

    def __init__(self, store: ExecutionStore):
        self.store = store
        self.policy = RetentionPolicy()
        self.compaction = SQLiteCompactionEngine(store, self.policy)
        self.fragmentation = FragmentationDetector(store)

    def health_report(self) -> Dict:
        tiers = self.policy.classify_all(self.store)
        db_stats = self.compaction.db_stats()
        frag = self.fragmentation.scan()
        return {
            "generated_at": time.time(),
            "retention_tiers": {k: len(v) for k, v in tiers.items()},
            "db_stats": db_stats,
            "fragmentation": frag,
        }

    def run_maintenance(self, dry_run: bool = False) -> Dict:
        """Execute a full maintenance cycle: prune → checkpoint → vacuum if needed."""
        prune_result  = self.compaction.prune_archived_events(dry_run=dry_run)
        wal_result    = self.compaction.checkpoint_wal()
        vacuum_result = self.compaction.vacuum_if_needed()
        return {
            "maintenance_at": time.time(),
            "dry_run": dry_run,
            "prune": prune_result,
            "wal_checkpoint": wal_result,
            "vacuum": vacuum_result,
        }
