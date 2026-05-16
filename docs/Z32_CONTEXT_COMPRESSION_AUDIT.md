# Z32 Context Compression Audit

**Phase:** Z32A — Context Compression Engine  
**Status:** AUDITED  
**Date:** 2026-05-16

---

## Hot / Warm / Cold Memory Architecture

| Layer | Definition | Retention | Storage |
|-------|-----------|-----------|---------|
| Hot   | Last 30 log rows of active session | Always in memory | Runtime only |
| Warm  | Summarized operational state (phase counts, provider counts, ts range) | Persisted as compression summary | `compression_events.summary_json` |
| Cold  | All replay snapshots, full event history | Permanently persisted | `forensics.db dag_snapshots` |

Compression never destroys:
- Error/CRITICAL log rows (recovery lineage preserved)
- DAG node states (in snapshot store)
- Replay events (in `replay_events` table)

---

## Compression Triggers

| Trigger | Condition | Action |
|---------|-----------|--------|
| `token_pressure` | token estimate > 8,000 | Compress to hot + critical rows |
| `node_overflow` | node count > 30 | Compress warm, emit warning |
| `replay_growth` | snapshot count > 200 | Archive old snapshots to cold |
| `retry_loop` | retry count ≥ 5 | Compress redundant retry logs |
| `context_redundancy` | redundancy ratio > 40% | Deduplicate near-duplicate rows |

---

## Compression Algorithm

```
_compress_rows(rows):
  hot   = rows[-30:]
  older = rows[:-30]
  critical_kept = [r for r in older if "error"/"failed" in r]
  warm  = [r for r in older if r not in critical_kept]
  summary = {phase_counts, provider_counts, archived_row_count, ts_range}
  return critical_kept + hot, summary
```

Warm summary is written to `compression_events` with full audit metadata (rows_before, rows_after, tokens_saved, ts).

---

## Replay Safety

- Every compression emits a `compression_events` row — fully replay-safe.
- Compressed rows are never deleted from the DB (`dag_snapshots` + `replay_events` untouched).
- Recovery lineage (error rows) are preserved across compression boundaries.

---

## Remaining Compression Risks

1. **Token estimation**: `len(text) / 4` is a rough estimate. Actual token counts from the model provider are more precise. Mitigation: use `tiktoken` for provider-specific tokenization.
2. **Summary fidelity**: Warm summary captures phase/provider counts but not causal reasoning chains. A replanning event that depends on a warm-archived row may lose context. Mitigation: mark replanning-adjacent rows as critical (preserve them in hot layer).
3. **Compression race**: If two concurrent requests trigger compression simultaneously, both may compress the same window independently. Mitigation: per-session compression lock (Redis or DB mutex).
4. **No cold-layer recall**: Currently, cold-layer rows (in `forensics.db`) cannot be recalled into active context. Mitigation: add `GET /api/z32/compress/<sid>/recall` endpoint.

---

## Production-Readiness Verdict

**OPERATIONALLY SAFE.** Compression is additive (never destructive). Token estimation is approximate — suitable for operational purposes but not for billing calculations.
