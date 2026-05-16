# Z32 Skill Memory Analysis

**Phase:** Z32D — Procedural Skill Memory  
**Status:** AUDITED  
**Date:** 2026-05-16

---

## Skill Extraction Logic

```python
_extract_skill(session_id, nodes, metrics):
  done_nodes = [n for n in nodes if n["state"] == "done"]
  if len(done_nodes) < 2: return None
  fp = sha256(sorted([n["stage"] for n in done_nodes]))
  return Skill(
    fingerprint=fp,
    name="Workflow:<stage1>-<stage2>-...",
    workflow_json=[{stage, state}],
    validation_rate=done_nodes / total_nodes,
    avg_retries=total_retries / done_nodes,
    provider=<most common provider>,
  )
```

---

## Skill Storage Schema

```sql
skills (
    fingerprint TEXT UNIQUE,   -- workflow pattern hash
    name TEXT,                 -- human-readable workflow name
    description TEXT,          -- auto-generated summary
    workflow_json TEXT,        -- ordered node stages
    validation_rate REAL,      -- success ratio (0–1)
    avg_retries REAL,          -- average retries per node
    provider TEXT,             -- preferred provider
    success_count INTEGER,     -- times this workflow succeeded
    last_used REAL,            -- last usage timestamp
)
```

---

## Skill Recall

- Exact match: `SELECT * FROM skills WHERE fingerprint = ?`
- Fallback: top 5 skills by `validation_rate DESC, success_count DESC`

Recall is presented to operators as workflow suggestions — not automatically applied.

---

## Governance Safety Constraints

The skill recall system enforces the following safety constraints:

| Constraint | Mechanism |
|-----------|-----------|
| No governance bypass | Skills are workflow patterns only — they do not encode HITL approval states |
| No replay corruption | Skill recall does not write to `replay_events` or `dag_snapshots` |
| No execution autonomy | Skills are advisory — the execution engine must explicitly choose to use them |
| No state smuggling | `workflow_json` contains only `{stage, state}` — no tool credentials or secrets |
| Provider suggestions are optional | Provider preference from skills is a hint, not an override |

---

## Remaining Skill Memory Risks

1. **Skill pollution**: A successful workflow on a lucky/anomalous run could be extracted as a skill and recalled on future runs where it is inappropriate. Mitigation: require `validation_rate >= 0.80` and `success_count >= 3` before surfacing a skill as a primary recommendation.
2. **Overfitting to session context**: Skills are extracted from session node stages only. They do not capture the task type or user prompt. Two sessions with the same node stages but different goals will produce the same fingerprint. Mitigation: include task type in fingerprint.
3. **Stale skills**: Skills are never automatically expired. A skill that was reliable 3 months ago may be outdated if the provider or tool behavior changed. Mitigation: add `TTL` or `last_used` expiry (remove skills not used in 30 days).
4. **No semantic deduplication**: Two slightly different workflow patterns that achieve the same goal will produce different fingerprints and be stored as separate skills. Mitigation: semantic similarity clustering (requires embedding model).
5. **skills table grows unbounded**: No cleanup policy. Mitigation: max 500 skills, prune by `validation_rate ASC LIMIT N`.

---

## Production-Readiness Verdict

**SAFE for advisory skill recall.** Should not be used for autonomous skill application until governance integration, validation_rate gating, and staleness expiry are implemented.
