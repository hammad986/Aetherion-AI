# Z26 — Context Retention Policy

## Scope

This policy governs what is retained, compressed, and discarded during long-running sessions in the Nexora runtime context pipeline.

## Retention Tiers

### Tier 1 — Always Retained (Critical Notes)

The following are never compressed or discarded (subject to hard cap):

| Type         | Description                                      | Retention |
|--------------|--------------------------------------------------|-----------|
| `goal`       | The user's stated mission objective              | Always    |
| `constraint` | Hard constraints set by user or operator policy  | Always    |
| `decision`   | Major execution decisions made during session    | Always    |
| `error`      | Unrecovered errors that affect mission state     | Always    |

Hard cap: 20 notes. When exceeded, the oldest note is evicted. The system logs an audit event for every eviction.

### Tier 2 — Compressed (Episode Summaries)

Verbatim messages that age out of the active window are summarized into episode records. Summaries retain:

- The gist of what was discussed or executed
- Tool outcomes (success/failure)
- Any errors or unexpected results
- Provenance hash for integrity verification

Summaries do **not** retain:
- Full verbatim content
- Intermediate reasoning steps
- Redundant context

Default rolling budget: 8 episode summaries.

### Tier 3 — Active Window (Verbatim)

The most recent N messages (default: 40) are kept verbatim for full LLM context access.

### Tier 4 — Discarded

When the episode budget is exceeded, the oldest episode summary is evicted entirely. Its provenance hash is preserved in the audit log for traceability.

## Compression Trigger Policy

Auto-compression fires when:
```
(active_tokens + episode_tokens + note_tokens) > token_budget (28,000)
```

Manual compression can also be triggered explicitly via `SessionContext.compress()`.

## Audit Requirements

Every compression event MUST produce an audit log entry containing:
- Session ID
- Episode index
- Number of messages compressed
- Summary token count
- Provenance hash
- Timestamp

## Data Boundaries

This context pipeline is **runtime-only**. It does not write to the long-term memory database (`long_term_memory.py` / chromadb). Those systems remain completely separate.

## Operator Rights

Operators may:
- Query current token usage via `SessionContext.token_usage()`
- Inspect episode summaries and their provenance
- Add critical notes at any time
- Trigger manual compression

Operators may NOT (by policy, not code):
- Retrieve discarded verbatim messages
- Access raw model intermediate outputs
- Override provenance hash verification
