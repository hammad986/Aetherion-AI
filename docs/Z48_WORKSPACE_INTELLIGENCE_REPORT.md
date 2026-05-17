# Z48D — Workspace Intelligence Report

**Phase:** Z48D — Workspace Intelligence Layer  
**Audit Date:** 2026-05-17  
**Status:** Delivered

---

## What Was Built

### Suggestion Bar (`#z48SuggestBar`)
- Injected into `#nxIdleHero` below the status strip  
- Styled non-intrusively: thin blue-tinted bar with icon, text, optional action button, dismiss (×)  
- Shows **maximum 1 suggestion at a time** (no stacking, no spam)  
- Auto-dismisses after 12 seconds  
- Dismissed suggestions are remembered in `localStorage.nx_ws_state_v1.dismissedSuggestions`  
- Animated slide-in on appearance (`z48-suggest-in` keyframe)  

### Suggestion Types (in priority order)

| Priority | Trigger | Text | Action |
|----------|---------|------|--------|
| 1 | Last session exists, none active | "Resume session {sid}…" | Emit `dag.replay.start` |
| 2 | Last diff comparison exists | "Reopen diff: {A} vs {B}" | Open diff viewer |
| 3 | Open file tabs exist from last session | "Reopen {filename}" | Open file preview |

- Suggestions fire **3 seconds after page load** (not competing with init)  
- Evaluated only once per page load (no re-evaluation loop)  

### Dismissed State Management
- Dismissal keyed per-suggestion (e.g. `resume_last_a1b2c3d4`)  
- Persisted in localStorage alongside workspace state  
- Not cleared on page reload (intentional — respects user's "not now" decision)  

---

## Remaining Workspace Weaknesses

1. **Recovery suggestions** — no detection of failed sessions from the API to suggest retry  
2. **Frequently accessed artifacts** — no access frequency tracking; cannot suggest "you often open auth.py"  
3. **Replay continuation** — no suggestion to continue a replay at the last scrubber position  
4. **Recent command recommendations** — command palette has recent history, but suggestions don't cross-reference it  

## Remaining Workflow Friction

- Dismissed suggestions are permanent (no "snooze for 1 hour" option)  
- Only one suggestion priority chain; if top priority fires, lower ones are never shown  
- Suggestions only show in the idle state; no suggestions during active execution  

## Remaining Operational Gaps

- No backend API call to detect failed sessions for recovery suggestions  
- Suggestions don't know if a session is currently running vs idle  

## Remaining Usability Inconsistencies

- The suggestion bar sits below the status strip but above the readiness chips — might feel disconnected from the main prompt area  
- "Resume session" suggestion fires even if the session had no tasks  

## Remaining Replay Weaknesses

- No suggestion to replay a failed session ("Your last session failed. Replay to diagnose?")  

## Remaining Artifact Relationship Gaps

- No suggestion to open recently generated artifacts  

## Honest Workbench Maturity Score

| Dimension                     | Score |
|-------------------------------|-------|
| Non-intrusiveness             | 9/10  |
| Suggestion accuracy           | 6/10  |
| Persistence                   | 8/10  |
| Suggestion variety            | 5/10  |
| Dismissed state management    | 8/10  |
| **Overall Z48D**              | **7/10** |
