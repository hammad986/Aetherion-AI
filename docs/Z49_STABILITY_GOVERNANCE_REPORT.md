# Z49 Stability & Governance Report
**Phase Z49F — Operational Stability Pass + Z49G — Performance Governance**

## Z49F — Stability Auditor

### Audit Checks
| Check | Method | Action |
|---|---|---|
| Orphaned artifacts | Cross-reference z49_graph.db vs data/artifacts.db | Lists orphan IDs |
| Broken replay refs | Check replay_id in artifact_relationships vs replay_outcomes | Count reported |
| Stale relationships | Age > 90 days | Reported + prunable |
| Duplicate relationships | UNIQUE constraint + count query | Count reported |
| WAL mode validation | `PRAGMA journal_mode` check | Boolean flag |

### Scoring
Score starts at 100 and is deducted per finding:
- Orphan: -2 per entity (max -20)
- Broken replay ref: -3 per ref (max -15)
- Stale relationship: -1 per row (max -10)
- Duplicate: -2 per duplicate (max -10)
- WAL inactive: -5

### Stability Audit API
- `GET /api/graph/audit` — list audit history
- `GET /api/graph/audit?run=1` — execute fresh full audit

### Persistence
All audit results stored in `stability_audit_log` table for trend tracking.

---

## Z49G — Performance Governance

### Limits Enforced
| Resource | Limit | Method |
|---|---|---|
| Single query max results | 500 rows | `MAX_GRAPH_RESULTS` cap |
| Search results | 200 max | `MAX_SEARCH_RESULTS` cap |
| Summary events consumed | 1000 | `MAX_SUMMARY_EVENTS` cap |
| Stale relationships | Prune > 90 days | `ArtifactGraph.prune_stale()` |
| Search index entries | Vacuum at > 10,000 | `vacuum_search_index()` |
| Pressure trends per session | Cap at 500 | `vacuum_pressure_trends()` |
| Old summaries | Prune > 180 days | `prune_stale_summaries()` |

### Maintenance API
- `GET /api/graph/maintenance` — view current stats
- `GET /api/graph/maintenance?run=1` — execute all maintenance passes
- `GET /api/graph/stats` — raw database row counts

### WAL Safety
- All tables created with `PRAGMA journal_mode=WAL`
- Foreign keys enabled via `PRAGMA foreign_keys=ON`
- Connections use `timeout=10` to avoid lock starvation under concurrent load

## Remaining Stability Risks
1. No scheduled automatic maintenance — must be triggered via API or manual call
2. Orphan pruning only reports — does not auto-delete orphaned relationship rows
3. No cross-database VACUUM scheduled for `z49_graph.db` itself
4. Stale relationship pruning is time-based only — semantic staleness (invalid IDs) requires orphan check

## Remaining Operational Fragmentation
- `z49_graph.db` is a separate database from `sessions.db`, `memory.db` — no single JOIN possible
- Pressure trend sampling requires external caller — no built-in background collector

## Operational Maturity Score: 81/100
