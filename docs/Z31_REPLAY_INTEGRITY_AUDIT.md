# Z31 Replay Integrity Audit

**Phase:** Z31B — Deterministic Replay Validation  
**Status:** AUDITED  
**Date:** 2026-05-16

---

## Fingerprinting System

### Node Fingerprint

```python
def _fingerprint_nodes(nodes):
    canonical = sorted(
        [{"id": n["id"], "state": n["state"], "retries": n["retries"]}
         for n in nodes],
        key=lambda x: x["id"]
    )
    return sha256(json.dumps(canonical, sort_keys=True)).hexdigest()[:24]
```

- **Deterministic:** Input sorted by node ID before hashing. JSON serialized with `sort_keys=True`. SHA-256 truncated to 24 hex chars.
- **Covers:** Node ID, terminal state, retry count.
- **Does NOT cover:** Provider metadata, token counts (non-deterministic runtime values).
- **Rationale:** Fingerprint must be deterministic across devices. Provider and token data are stored in node payload but excluded from fingerprint computation.

### Event Fingerprint

```python
def _fingerprint_event(event_type, node_id, payload):
    content = json.dumps({"t": event_type, "n": node_id, "p": payload}, sort_keys=True)
    return sha256(content).hexdigest()[:16]
```

### Session Fingerprint

Folds all per-event fingerprints in chronological order: `sha256("|".join(all_event_fps))`.

---

## Drift Detection

### Detection Mechanism

`GET /api/z31/integrity/<sid>?fingerprint=<client_fp>` compares the client-submitted fingerprint against the server-computed session fingerprint.

| Condition | Risk | Description |
|-----------|------|-------------|
| `match=true` | NONE | Client and server replay are identical |
| `match=false, n_events > 0` | HIGH | Replay state diverged — possible missing or corrupted events |
| `match=false, n_events = 0` | UNKNOWN | Server has no events to compare against |

### Drift Triggers

1. **Out-of-order events:** Client delivers events in different order than server recorded.
2. **Missing events:** SSE reconnect skipped some events.
3. **Duplicate injection:** Retry storms caused the same event to be re-processed twice.
4. **Worker restart:** If the server-side event store was partially written before restart.

---

## Integrity Scoring (0–100)

| Deduction | Condition |
|-----------|-----------|
| -10 per gap | Non-contiguous snapshot index sequence |
| -3 per duplicate | Duplicate replay event fingerprint |
| -5 per ooo | Out-of-order timestamp pair |
| -100 | No snapshots found for session |

| Score | Verdict |
|-------|---------|
| ≥90 | HEALTHY |
| 60–89 | DEGRADED |
| 30–59 | WARNING |
| <30 | CORRUPT |

---

## Remaining Integrity Risks

1. **Clock skew:** Server timestamps use `unixepoch('now', 'subsec')`. Client sends `Date.now()` (ms). Cross-device clock drift can produce false out-of-order warnings. Mitigation: normalize to server clock on receipt.
2. **Truncated fingerprint:** SHA-256 truncated to 16–24 hex chars. Theoretical collision probability per session: ~1 in 2^64. Acceptable for operational use; not acceptable for cryptographic audit.
3. **Payload exclusion from event fingerprint:** Fingerprint covers type + node_id + payload only. If payload schema changes between export and import, fingerprint may match incorrectly.
4. **No cross-snapshot delta fingerprinting:** Current fingerprints are per-snapshot or per-session, not per-delta. A corrupted snapshot that is internally consistent but has wrong state changes will not be detected.
5. **Replay validation requires active server:** Drift detection requires `GET /api/z31/integrity`. Offline replay inspection cannot validate against server fingerprint.

---

## Production-Readiness Verdict

**ADEQUATE for operational forensics.** Not suitable as a cryptographic audit log. Full cross-snapshot delta fingerprinting should be added for high-stakes audit requirements.
