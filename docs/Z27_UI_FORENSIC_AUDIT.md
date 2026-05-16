# Z27 — UI Forensic Audit

## Scope

Complete forensic audit of the Nexora UI as of Phase Z27.

---

## 1. Remaining Visual Instability

| Issue | Severity | Location | Status |
|-------|----------|----------|--------|
| MutationObserver budget exceeded (10 vs 8) | LOW | `nx-perf-observer.js` | Known warning, not a crash |
| Idle workspace previously empty/black | MEDIUM | `nxIdleHero` | Fixed in Z27B |
| Cookie banner excessive visual weight | LOW | `nx-cookie-banner` | Fixed in Z27C |
| Nav-rail slide panels had no `nxTogglePanel` handler | HIGH | `workspace.js` | Fixed in Z26 session |
| `PALETTE_OPEN` event guard missing on cold start | MEDIUM | `nx-polish.js:323` | Fixed in Z26 session |

---

## 2. Remaining Runtime Disconnects

| Disconnect | Severity | Status |
|------------|----------|--------|
| Z26 context compression not wired to agent | HIGH | Fixed in Z27A |
| Z26 confidence engine not wired to agent | HIGH | Fixed in Z27A |
| Z26 explainability not wired to agent | HIGH | Fixed in Z27A |
| Z26 scheduler background checker not started | MEDIUM | Fixed in Z27 (web_app.py) |
| `agent.context_state` SSE event not emitted | HIGH | Fixed in Z27A |
| `agent.confidence_warning` SSE event not emitted | HIGH | Fixed in Z27A |
| `agent.runtime_telemetry` SSE event not emitted | HIGH | Fixed in Z27A |
| Runtime API endpoints not exposed | MEDIUM | Fixed in Z27 (web_app.py) |

---

## 3. Remaining Dead/Unused UI Components

| Component | Status |
|-----------|--------|
| `nxTab-preview` — empty div with no content loader | In use when preview server active |
| `nxTab-agents` — agents panel rarely populated | Used by Phase 7 agents |
| `nxTab-timeline` — populated only during long tasks | Intentionally lazy |
| `nxTab-steps` — populated only during active execution | Intentionally lazy |
| Metrics tab — shows mock data in some states | Partially real data |
| AI Learning Dashboard widgets — low real signal | Known limitation |

---

## 4. Remaining Accessibility Issues

| Issue | Severity |
|-------|----------|
| Some dynamic toast notifications lack `aria-live` | MEDIUM |
| Monaco editor ARIA integration is incomplete | LOW |
| Slider inputs in settings lack `aria-valuetext` | LOW |
| Modal dialogs don't trap focus consistently | MEDIUM |
| Cookie banner dismiss button is icon-only (✕) without aria-label | LOW |

---

## 5. Remaining Execution Visibility Weaknesses

| Gap | Status |
|-----|--------|
| No live confidence score in the UI during execution | Partially addressed by `agent.confidence_warning` event |
| No context compression indicator during execution | `agent.context_state` event now emitted; UI pickup pending |
| HITL wait state visible but not prominently flagged | Existing HITL panel sufficient for beta |
| Scheduler queue not visible in main workspace | Added in Z27B idle workspace |
| Provider switch not surfaced to operator in real time | Decision logged in explainability system |

---

## 6. Remaining Operational UX Gaps

| Gap | Priority |
|-----|----------|
| No drag-and-drop upload zone yet wired to backend | MEDIUM — foundation in Z26 docs |
| No persistent session execution history in idle view | LOW — recent sessions loaded from `/api/sessions` |
| No inline mission scheduling from idle workspace | MEDIUM |
| Admin panel lacks runtime telemetry view | LOW |
| No operator explanation feed in UI (Z26 explainability) | MEDIUM — API available, UI deferred |

---

## 7. Honest Beta-Readiness Verdict

**CONDITIONAL BETA** — same verdict as Z26 risk report.

Z27 significantly reduces the runtime disconnect risk. The Z26 modules are now live in the agent execution loop. The idle workspace is functional. Core runtime UX is stable.

Remaining blockers before public beta:
- Terminal execution sandboxing
- OAuth/billing/email keys configuration
- Scheduler persistence
- Redis for multi-worker deployment
