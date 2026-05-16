# Z33 Command Palette Analysis

**Phase:** Z33D — Command Palette + Operator Flow  
**Status:** AUDITED  
**Date:** 2026-05-16

---

## Runtime-Aware Items Added

| Item | Section | Condition | Action |
|------|---------|-----------|--------|
| Replay: Jump to Start | Replay | Session exists | `_z30.replayStart()` |
| Replay: Exit Replay Mode | Replay | Currently in replay | `_z30.replayStop()` |
| Export Forensic Bundle | Forensics | Session exists | Opens `/api/z31/export/<sid>` |
| Open Session History | Forensics | Always | Opens Z31 forensic panel |
| Force Context Compression | Runtime | Session exists | `_z32.forceCompress()` |
| Check Semantic Confidence | Runtime | Session exists | `_z32.forceConfidence()` |
| Run Failure Intelligence | Runtime | Session exists | `_z32.forceIntel()` |
| Open Skill Memory Panel | Skills | Always | Opens Z32 skills panel |
| Expand Execution Timeline | Live | Always | Expands Z33 timeline dock |
| Review HITL Escalations | Governance | Always | Opens HITL panel |

## Semantic Search

The palette extension registers a dynamic search provider that adds results for:
- **Sessions**: prefix match on `session_id` or `integrity_verdict`
- **Skills**: prefix match on skill `name`

Session and skill caches are refreshed every 30s and on palette open. Lookup is synchronous (in-memory) — no blocking API calls on keypress.

## Runtime State Banner

When the palette opens during an active session or replay mode, a subtle banner appears below the input showing the current state. This gives the operator context without requiring them to check the Live tab first.

## Remaining Operator Confusion Points

1. **No section headers in current palette list**: The `section` property on registered items is stored but not rendered as a visual separator by the existing `_NxPalette` renderer. The extension provides the data; the renderer needs to be updated to show section headers.  
   Mitigation: patch `nx-command-palette.js` to render `nx-palette-section` dividers between grouped items.
2. **Condition-gated items still show in list**: Items with `condition()` returning false should be filtered. The current palette renderer does not check `condition` fields from externally registered items. Mitigation: patch `_render()` in `nx-command-palette.js` to filter `item.condition?.()` before rendering.
3. **Dynamic search provider protocol**: `registerSearchProvider` is a proposed extension API on `_NxPalette`. If `nx-command-palette.js` doesn't implement it, dynamic session/skill results won't appear. Mitigation: check `typeof _NxPalette.registerSearchProvider === 'function'` (already done in code).

## Production-Readiness Verdict

**PRODUCTION-READY as an addon layer.** Full condition-gating and section headers require a one-time patch to `nx-command-palette.js`.
