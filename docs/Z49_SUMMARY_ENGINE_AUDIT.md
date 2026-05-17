# Z49 Summary Engine Audit
**Phase Z49D — Execution Summary Engine**

## Summary
The `SummaryEngine` in `operational_graph.py` generates structured summaries derived
entirely from persisted event data. No LLM calls, no hallucination, no inference beyond
what is recorded in the database.

## Summary Types
| Type | Source Data | Key Moments |
|---|---|---|
| `execution` | retry_history + recovery_log + escalations | First attempt, escalations, first success |
| `failure` | recovery_log (failures) + escalations | Failed recovery triggers, escalation chain |
| `recovery` | recovery_log (successes) | Recovery action → outcome |
| `replay` | replay_outcomes + bookmarks + relationships | Bookmarked events, divergence notes |
| `artifact` | artifact lineage + relationships + annotations | Ancestor/descendant counts, rel types |
| `mission` | (composite — calls execution + artifact) | Combined key moments |

## Anti-Hallucination Guarantees
- All body text is composed from database field values using f-strings
- Key moments are extracted exclusively from recorded timestamps and labels
- No LLM calls in any summary generation path
- Missing data produces "unknown" / zero values — never fabricated estimates

## Storage
- All summaries persisted in `execution_summaries` table
- Indexed on `session_id`, `summary_type`
- `key_moments` stored as JSON array: `[{ts, label, detail}]`

## Remaining Weaknesses
1. Summaries are not automatically regenerated when new events arrive — stale if called only once
2. No deduplication check — calling `generate_execution_summary(sid)` twice creates two rows
3. `mission` summary type has no dedicated generator — currently an alias for execution type
4. Timeline condensation is basic (top-N events) — no significance ranking

## Remaining Replay Inconsistencies
- If `replay_outcomes` table is empty for a replay_id, replay summary reports "unknown" outcome
- Divergence detection is note-based (text field), not structural comparison

## Operational Maturity Score: 79/100
