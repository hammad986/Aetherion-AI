# Z48C — Replay Usability Audit

**Phase:** Z48C — Operational Replay Usability  
**Audit Date:** 2026-05-17  
**Status:** Delivered

---

## What Was Built

### Replay Minimap
- Horizontal strip injected immediately before `#z30ReplayBar` in the Live tab  
- Becomes visible when `dag.replay.started` fires; hidden on `dag.replay.stopped`  
- Shows a track with colored tick markers:  
  - 🔴 Failure events  
  - 🟣 HITL pause events  
  - 🟡 Recovery events  
  - 🟢 File write events  
  - 🔵 Bookmark markers  
- Minimap cursor (blue line) tracks current replay step via `dag.replay.step` events  
- Click on minimap track to jump to that step (calls `_z30.replayScrub(step)`)  

### Bookmarks
- ⚑ bookmark button in minimap bar  
- Adds a yellow marker at the current replay step  
- Bookmark markers clickable to jump  
- Not yet persisted across sessions (session-memory only)  

### Jump Buttons
- "Jump:" row appears below replay bar showing buttons for each notable event:  
  - `✗ Failure 1`, `✗ Failure 2`, etc.  
  - `⏸ HITL 1`, `↺ Recovery 1`, etc.  
- Each button jumps the replay scrubber directly to that step  
- Bar hidden when no notable events exist (no clutter)  

### Replay Summary
- Banner row below minimap showing: Steps, Failures, File writes, Duration  
- Pre-populated from `dag.replay.available` event (historical data)  
- Updated incrementally from `dag.replay.step` events  

### Pre-population from Historical Data
- When `dag.replay.available` fires with step metadata, failures are pre-stamped  
- Minimap and jump buttons render immediately on replay open, not only after scrubbing  

---

## Remaining Workspace Weaknesses

1. **Replay annotations** — no free-text annotation on individual steps  
2. **Bookmark persistence** — bookmarks lost on page reload (in-memory only)  
3. **Replay filters** — no filter to show only failures or only writes in the main DAG surface  
4. **Mission outcome summary** — no end-of-replay summary modal/banner ("Mission succeeded in 14 steps with 1 failure")  

## Remaining Workflow Friction

- Minimap requires NxBus `dag.replay.*` events; if replay system does not fire these, minimap stays empty  
- No scrubber tooltip showing the step label on hover  
- No replay speed control (1×, 2×, etc.)  

## Remaining Operational Gaps

- Replay minimap position (before replay bar) means it is only visible when user is on the Live tab  
- No minimap visible in the secondary split pane  

## Remaining Usability Inconsistencies

- Existing scrubber (`#z30ReplayScrubber`) and minimap are not visually linked — they feel like separate controls  
- Minimap width does not account for the 8px padding from the replay bar layout  

## Remaining Replay Weaknesses

- No replay quick filter chips ("Show only: Failures | Writes | HITL")  
- Replay annotation text not supported  

## Remaining Artifact Relationship Gaps

- File write markers in minimap do not link to the written file preview  

## Honest Workbench Maturity Score

| Dimension                | Score |
|--------------------------|-------|
| Minimap rendering        | 8/10  |
| Jump buttons             | 9/10  |
| Bookmark system          | 5/10  |
| Replay summary           | 7/10  |
| NxBus integration        | 8/10  |
| **Overall Z48C**         | **7.5/10** |
