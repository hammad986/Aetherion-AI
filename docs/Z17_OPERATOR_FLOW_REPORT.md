# Z17 Operator Flow Report
**Date: 2026-05-16**

---

## Operator Burden Analysis

### Before Z17 — Critical Burden Points

1. **Composer had no focus ring** — operator could not confirm input was active without clicking.
2. **Tab bar used hardcoded colors** — theme system couldn't adjust accent without inline override.
3. **Uncertainty modal at z-index 9999** — could be hidden behind SSE reconnect badge (z-index 9997). Operator missed critical decision requests.
4. **Screen readers received zero live updates** — log output, activity bar had no aria-live.
5. **HITL buttons had no minimum tap target** — on narrow viewports, 24px buttons were difficult to activate.

### After Z17 — Burden Reduction

1. **Composer focus ring** — instant confirmation, no cognitive check required.
2. **Tab bar tokenized** — theme-correct accent, visual consistency.
3. **Uncertainty modal at z-index 10100** — no overlay can hide it.
4. **aria-live on log + activity** — screen reader operators receive live execution status.
5. **HITL buttons min-height: 28px** — reliable tap target.

---

## Workflow Improvements

### Task Execution Flow

```
Operator types task → [BEFORE] No focus confirmation
                      [AFTER]  Accent border confirms input active

Operator clicks Run → [BEFORE] Green dot appears (no ARIA)
                      [AFTER]  Green dot + aria-label="Execute task" + aria-live status bar

Agent runs → [BEFORE] Log output updates silently (no screen reader)
             [AFTER]  role="log" aria-live="polite" — SR announces new output

Agent requests HITL → [BEFORE] Uncertainty modal could be hidden behind SSE badge
                       [AFTER]  Modal z-index:10100 always visible

Operator responds → [BEFORE] HITL buttons potentially too small
                    [AFTER]  min-height:28px — reliable activation
```

### Tab Navigation Flow

```
[BEFORE] No ARIA roles — tabs are just buttons to SR
         JS switches tabs by toggling .active class (no ARIA state sync yet)

[AFTER]  role="tablist" on container
         role="tab" on each button
         aria-selected="true/false" (static initial state; JS should update these)
         aria-controls="nxTab-X" links tab to panel
```

**Note:** The `aria-selected` attribute is set correctly in HTML for initial state (Output=true, others=false). For full compliance, the JS `nxSetTab()` function should update `aria-selected` when switching. This is a JS enhancement (deferred to next JS maintenance pass).

---

## Execution Clarity Metrics

| Metric | Z16 State | Z17 State |
|---|---|---|
| Interactive controls with aria-label | 0 (shell chrome) | 10 (all primary controls) |
| Live regions | 0 | 2 (log output + activity bar) |
| Modal z-index correctness | Partial (uncertainty modal below SSE) | Full (10100 > 9000 > 90) |
| Tab bar ARIA completeness | 0% | 80% (static; JS sync deferred) |
| Composer focus visibility | None | Accent ring + glow |
| HITL tap targets | Default (24px typical) | min-height: 28px |
| Exec toolbar signal | Dashed border (provisional) | Solid border (operational) |
| Content containment | None | contain:content on 3 scroll areas |

---

## Remaining Operator Burden (Deferred)

| Item | Burden | Effort | Status |
|---|---|---|---|
| HITL label language | Medium | Low (HTML text change) | Deferred |
| Tab keyboard navigation | High | Medium (JS) | Deferred |
| Idle hero 3rd chip | Low | Low | Deferred |
| Pipeline stage specificity | Medium | Medium (JS data) | Deferred |
| JS aria-selected sync | Medium | Low (JS) | Deferred |
| Focus trap in modals | High | High (JS) | Deferred |
