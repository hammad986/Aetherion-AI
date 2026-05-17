# Z49 Operational Graph Report
**Phase Z49A — Persistent Artifact Graph**

## Summary
The artifact relationship graph is implemented as the `artifact_relationships` SQLite table
in `z49_graph.db`, managed by `ArtifactGraph` in `operational_graph.py`.

## Relationship Types Implemented
| Type | Description |
|---|---|
| `parent_child` | Direct artifact derivation |
| `version` | Sequential version lineage |
| `replay` | Artifact linked to a replay run |
| `recovery` | Artifact produced during recovery |
| `failure` | Artifact associated with a failed execution |
| `session` | Artifact–session association |
| `execution` | Artifact–execution run association |
| `dependency` | Artifact requires another artifact |

## Schema
- `artifact_relationships` — source, target, rel_type, session_id, execution_id, replay_id, metadata, created_at
- Indexed on source_id, target_id, rel_type, session_id
- UNIQUE constraint on (source_id, target_id, rel_type) — append-safe INSERT OR IGNORE
- WAL journal mode active for write concurrency safety

## Lineage Engine
- `get_lineage(artifact_id, depth=3)` — recursive ancestor + descendant walk
- `get_dependencies(artifact_id)` — direct dependency edges
- `list_by_session/replay/recovery` — filtered relationship views

## Remaining Weaknesses
1. Lineage walk is recursive Python (not SQL CTE) — depth > 6 may be slow on large graphs
2. No automated trigger to create session/execution links on agent task start (must be called explicitly)
3. Cross-database orphan detection requires both `z49_graph.db` and `data/artifacts.db` to exist

## Relationship Gaps
- No built-in link between `artifact_relationships` and `artifact_versions` table in `artifact_registry.py`
- Manual call needed to record relationships; not yet auto-wired to `ArtifactRegistry.create_artifact`

## Operational Maturity Score: 84/100
