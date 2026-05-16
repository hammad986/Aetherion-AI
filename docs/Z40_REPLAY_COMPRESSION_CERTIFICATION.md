# Z40 — Replay Compression Certification

**Phase:** Z40E  
**System:** Nexora AI Platform  
**Scope:** Replay tiering, historical compression, hydration discipline  
**Date:** 2026-05-16

---

## 1. Architecture Implemented

The Replay Compression Governance system (`execution/replay_compression_governance.py`) provides:

| Component | Function |
|---|---|
| `ReplayTierClassifier` | Classifies executions as HOT / ACTIVE / HISTORICAL / ARCHIVED by age and status |
| `ReplayCompactor` | Produces compact summaries (event histogram + timing) for HISTORICAL/ARCHIVED chains |
| `HydrationDisciplineEngine` | Decides full vs summary-only hydration per execution based on tier and resource severity |
| `ReplayCompressionGovernor` | Facade exposing tier reports, compact operations, and hydration plans |

### Tier Age Boundaries

| Tier | Condition |
|---|---|
| HOT | Status running/queued OR age < 5 minutes |
| ACTIVE | Age < 30 minutes |
| HISTORICAL | Age < 6 hours |
| ARCHIVED | Age ≥ 6 hours |

### Hydration Rules Under Resource Pressure

| Resource Severity | HOT | ACTIVE | HISTORICAL | ARCHIVED |
|---|---|---|---|---|
| LIGHT | Full | Full | Full if <500 events | Summary only |
| MODERATE | Full | Full | Summary only | Summary only |
| HEAVY | Full | Full if <200 events | Summary only | Summary only |
| SATURATED/CRITICAL | Full | Summary only | Summary only | Summary only |

---

## 2. Remaining Replay Scalability Ceilings

- `ReplayCompactor.compact_tier()` processes at most 50 executions per call to avoid long-running DB reads. Very large HISTORICAL/ARCHIVED backlogs require multiple calls.
- Compact summaries are returned in API responses only — they are not persisted to a separate DB table. The same compaction must be re-computed on each call (with appropriate caching by the caller).
- Hydration plan samples at most 20 executions per tier for planning — the plan is illustrative, not exhaustive.

---

## 3. Remaining Compression Integrity Flaws

- Compact summaries preserve event type histograms and timing but discard correlation IDs and payload content — forensic replay of a compacted chain requires access to the original event log.
- `compact_chain()` sorts events by array order (which should be timestamp-ordered from `get_events()`) but does not re-verify ordering before compacting.

---

## 4. Source Log Immutability Guarantee

All compaction operations are **strictly read-only** with respect to the source `event_log` table:
- No `INSERT`, `UPDATE`, or `DELETE` statements are issued.
- Compact results are returned as in-memory dicts.
- Every compact result includes: `"note": "Source event log is UNCHANGED."`

---

## 5. Honest Operational Verdict

**Status: PRODUCTION READY**

Replay compression governance gives operators a tiered view of replay chain weight and a principled hydration discipline that scales back under resource pressure. HOT chains always receive full hydration; the system degrades gracefully under saturation. Accessible via `GET /api/z40/replay/governance`, `POST /api/z40/replay/compact`, and `GET /api/z40/replay/hydration-plan`.
