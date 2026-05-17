# Z52 Empty State Maturity Report

## Phase Z52E — Structured Operational Empty States

**Date:** 2026-05-17  
**Status:** COMPLETE (partial)  

---

## Empty State Audit

| Location | Before Z52 | After Z52 | Status |
|---|---|---|---|
| Idle hero — no recent sessions | `"No recent executions"` dim text | 3 structured mission cards | ✅ Fixed |
| Idle hero — model unset | Status strip shows `—` | Contextual hint card appears | ✅ Fixed |
| Idle hero — top banner | Generic "Ready for execution" | `"Nexora ready · all systems operational"` + time | ✅ Fixed |
| Log tab — no output | Blank dark panel | `"Awaiting execution output…"` placeholder | ✅ Fixed |
| Log tab — after completion | Execution output stays | (no change — correct behaviour) | ✓ Acceptable |
| Code/Files tab — no files | Completely blank | No change | ⚠ Remaining |
| Terminal — before connection | "Initializing..." | `"Terminal ready"` (text patch) | ✅ Improved |
| Right inspector — idle | Metrics at low opacity | No structural empty state | ⚠ Remaining |
| Forensics panel — no data | Default fallback text | No change | ⚠ Remaining |
| Sessions pane — no sessions | `"No sessions yet"` | No change | ⚠ Minor |

---

## Mission Cards Design Rationale

The three mission cards in the empty state were selected based on:
1. **High entry-point utility** — these are the 3 most common first tasks for a new user of an AI dev tool
2. **Skill signalling** — they communicate Nexora's capabilities implicitly (it builds, debugs, and audits)
3. **Cognitive load reduction** — users don't have to invent a task to see what the platform does

Each card has: icon, title, description, hover states, keyboard accessibility, and a pre-filled task that activates the composer on click.

When the session history loads with real data (via `nxIdleRecent` population), the `.nx-iw-recent-empty` container that holds these cards is replaced/hidden by the actual history rows. The mission cards do not persist once real context exists.

---

## Log Tab Placeholder

`"Awaiting execution output…"` is injected as a child of `#logArea` when:
1. `logArea` has no text content
2. `logArea.dataset.z52` has not been set

A MutationObserver watches for `logArea.children.length > 1` (real log content arriving) and removes the placeholder when triggered.

The placeholder is:
- `font-size: 11.5px`
- `color: rgba(72,79,88,0.6)` — clearly tertiary, not a real log entry
- No border, no card — just text (so it doesn't create a "fake log entry" impression)

---

## Remaining Empty-State Problems

### Code / Files Tab
When no workspace files exist, the code tab shows a blank dark panel. This is the most significant remaining dead-zone. A structured empty state here would show:
- "No files yet"
- "Run a task to generate files" CTA pointing to the composer
- Maybe a file tree skeleton/ghost

### Right Inspector Panel
The inspector panel has no empty state — it shows populated widgets at reduced opacity. What it should show when truly idle:
- A brief system health card (CPU, memory, queue)
- Or a "Session summary" placeholder suggesting the user start a task

### Sessions Pane
`"No sessions yet"` is correct text but there's no CTA — no "Start your first session" button. The composer is always visible so this is low priority.

### Forensics / Replay
The Z31 forensics panel has internal fallback text for empty states, but it's engineering-language. "No forensic data captured for this session" should read "No execution history yet."

---

## Empty State Maturity Score: 6.5/10

The highest-impact empty states (idle hero, log tab, terminal) are addressed. The Code/Files tab and right inspector remain dead-zones. The system no longer feels abandoned for first-time users — they see a clear invitation to act. For experienced users returning between sessions, the mission cards are redundant once history loads.
