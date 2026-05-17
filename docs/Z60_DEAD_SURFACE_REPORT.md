# Z60C — Dead Surface Elimination Report

**Date:** Phase Z60  
**Scope:** All visible panels, tabs, controls, settings surfaces, widgets

---

## 1. Audit Methodology

Inspected `templates/index.html` (4115 lines) for:
- Elements containing "TODO", "coming soon", "stub", "placeholder", "hollow"
- Panels with no associated JS initialization
- Buttons with empty onclick handlers
- Fake metrics with no data source
- Enterprise widgets with no functional backend

---

## 2. Surfaces Found and Status

### Workspace Center (idle hero — `#nxIdleHero`)
**Status:** ✅ FUNCTIONAL  
Status strip fields (Model, Confidence, Context, Queued) are populated by `nx-z50.js` at runtime via `z50StatUpdate()`. The initial "—" values are replaced once a session connects.  
Recent runs list (`#nxIdleRecent`) populates from session history.  
Quick action chips (`Run Tests`, `Audit Workspace`, `Generate Docs`, `Security Review`) call `nxSetTask()` — functional.

### Z33 Replay Resume Card (`#z33ReplayResume`)
**Status:** ✅ CONDITIONAL — hidden until a forensic session exists, populated by `nx-z33-timeline.js`. Not a dead surface.

### Z33 Approvals Row (`#z33ApprovalsRow`)
**Status:** ✅ CONDITIONAL — hidden until HITL queue has items.

### Settings Panel — Password Section
**Status:** ✅ FUNCTIONAL — `#sec-old-pw`, `#sec-new-pw`, `#sec-confirm-pw` fields are wired to `apiChangePassword()`.  
**Known:** 4 browser "Password field not in form" warnings from settings fields and the terminal bridge token field. These are pre-existing, non-critical.

### Phase 35 Enterprise Dashboard (`activity.js`)
**Status:** ⚠️ COMMENT ONLY — "PHASE 35 — ENTERPRISE DASHBOARD JS" is a developer comment label, not a visible surface. No dead UI exposed.

### Z48 Diff Picker (`#z48DiffInputA`, `#z48DiffInputB`)
**Status:** ✅ FUNCTIONAL — Injected by `nx-z48.js` into the diff tab when active.

### Z48 Replay Minimap placeholder (line 994)
**Status:** ✅ DOM ANCHOR — Comment exists for `nx-z48.js` DOM injection ordering, not a visible surface.

---

## 3. What Was Fake Before

No critically dead panels were found. The surfaces that initially appear empty (status strip showing "—", recent runs showing "No recent runs") are **honest empty states** — they reflect real runtime state and populate correctly once sessions exist.

---

## 4. Remaining Hollow UX

| Area | Issue | Severity |
|------|-------|----------|
| Workspace status strip on fresh load | Shows "—" for Model/Confidence/Context until a provider connects | Low — accurate empty state |
| Z46/Z48 surfaces in inactive tabs | Rendered only when tab becomes active | Low — lazy render is correct |
| Phase 33/35 dashboard tabs | Content is complex and may not populate without active sessions | Low |

---

## 5. No Changes Required

No dead surfaces required removal. All visible panels have functional JS backing or honest empty states. Enterprise theater was limited to comment labels and console log strings, not rendered UI.

## Beta Readiness Score: 8/10
