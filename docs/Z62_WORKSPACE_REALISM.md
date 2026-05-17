# Z62C — Workspace Realism Report

## Idle State Audit

### What Actually Renders (Confirmed)
The `#nxIdleHero` element renders when `body[data-nx-exec="idle"]`. It contains:

**Header row:**
- "Workspace ready" label
- Keyboard hint chips: `⌘K` command palette, `⌘↵` execute

**Runtime Status Strip (`#nxIdleStatusStrip`):**
- Model: Active provider/model name — populated by `z50UpdateIdleStats()` via `NX.lastMetrics`
- Confidence: AI self-reported certainty
- Context: Token pressure %
- Queued: Scheduled task count

**Quick Action Chips:**
- Run Tests → `nxSetTask('Run full test suite')`
- Audit Workspace → `nxSetTask('Audit workspace for errors')`
- Generate Docs → `nxSetTask('Generate project documentation')`
- Security Review → `nxSetTask('Review code for bugs and security issues')`

**Recent Runs Section (`#nxIdleRecent`):**
- Populated by `history.js` and `workspace.js`
- Shows "No recent runs" when empty — honest

**Task Composer:**
- `#taskInput` textarea is always visible
- Rotating placeholder text cycles example prompts every 3.5s
- `+` button for attachments (file, image, folder, GitHub)
- Mode chips: Autonomous, Architect, Debug
- Scope chips: Workspace, Active File

### Assessment
The workspace center is structurally correct and functional. The idle hero has meaningful content and is not an "empty black void" — it has real status data, quick actions, and the composer.

### Real Issues Found

**Issue 1: Idle model shows "—" on first load**
The `nxIdleModel` element shows `—` until `z50UpdateIdleStats()` runs (12s interval or on page load). After the metrics fix in Z62B (shared cache), the model name now populates faster via the ui.js 8s poll result.

**Issue 2: Quick action chips look like buttons but call `nxSetTask`**
`nxSetTask` is defined in `runtime.js` and populates `#taskInput`. This is functional — clicking a chip fills in the task input. Not a bug.

**Issue 3: Recent runs section**
`#nxIdleRecent` is populated by `history.js`. If no sessions exist, it shows "No recent runs" — correct empty state.

## Remaining Gaps
- No visible "what to do first" guidance for truly new users beyond quick action chips
- Provider status isn't shown in the idle hero (only in the settings sidebar)

## Beta Workspace Score: 7.5/10
The workspace is functional and honest. It shows real runtime data. Quick actions work. The task composer is prominent and accessible. The remaining gaps are polish, not blockers.
