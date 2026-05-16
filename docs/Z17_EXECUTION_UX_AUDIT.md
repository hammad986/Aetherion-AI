# Z17 Execution UX Audit
**Date: 2026-05-16**
**Scope: Execution workflow ergonomics, operator decision surfaces, signal/noise ratio**

---

## EXECUTIVE SUMMARY

The execution workflow suffers from three primary UX failures: (1) the composer is visually isolated from the execution context with no clear hierarchy, (2) the HITL controls use debug-language ("Pause", "Retry") rather than operator-language, and (3) the idle state gives two generic chips that don't reflect the platform's capabilities. The inspection and timeline surfaces are structurally sound but visually dim.

---

## 1. EXECUTION COMPOSER

### Current State

```html
<div class="nx-composer" style="padding:16px;border-bottom:1px solid #27272A;
  background:#121212;flex-shrink:0;">
  <!-- + button -->
  <!-- textarea: min-height:56px, font-size:14px -->
  <!-- exec toolbar: mode select, scope select, target indicator -->
  <!-- voice button -->
</div>
```

### Issues

**Spacing imbalance:**
- `padding:16px` uniform — same padding on all sides. The top padding creates dead space between topbar and composer.
- Exec toolbar has `margin-top:12px;padding-top:10px` — double spacing (22px gap between textarea and toolbar).
- `gap:12px` between toolbar items is too wide for 11px text labels.

**Hierarchy weakness:**
- The textarea (56px min) is primary but has no visual weight distinction from the toolbar below.
- `border-top:1px dashed #27272A` on exec toolbar — dashed borders signal "incomplete" or "optional" to users. Wrong signal for a primary execution control.
- Mode/Scope selects sit at the same visual level as the Target indicator — no hierarchy between execution configuration and execution context.

**Action density:**
- "+" button (36×36px) and voice button (36×36px) are oversized relative to the 56px textarea. At 64% of the textarea height each, they dominate the visual field.
- Mode and Scope selects provide no indication they affect execution behavior. No icon, no emphasis.
- The Target indicator ("Target: Local Shell") uses a green dot + plain text — effective but small (11px, muted color).

**Command ergonomics:**
- No character count, no hint about prompt length limits.
- Textarea has no minimum contrast focus state — inline `border:1px solid #27272A` has no `:focus` rule (the class `.nx-hitl-input:focus` exists but `.nx-composer textarea` has no focus rule).
- `⌘+Enter` shortcut in placeholder text is the only affordance for keyboard execution. No visible shortcut badge.

---

## 2. EXECUTION TIMELINE

### Current State

**Pipeline bar** (shown during execution):
```html
<div class="nx-exec-pipeline" id="nxLogsPipeline" style="display:none">
  <div class="nx-exec-stage" id="nlp-planning"><span class="nx-stage-dot"></span>Planning</div>
  <div class="nx-exec-stage" id="nlp-coding">...</div>
  <div class="nx-exec-stage" id="nlp-debugging">...</div>
  <div class="nx-exec-stage" id="nlp-done">...</div>
</div>
```

**Issues:**
- Pipeline bar uses `display:none` toggle — abrupt appearance with no fade-in. Disorienting during execution start.
- Stage labels ("Planning", "Coding", "Debugging", "Done") are generic. "Coding" doesn't tell the operator what the AI is currently doing.
- No elapsed time per stage. No indication of how many steps have occurred within the current stage.
- "⚡ Full View" button at 0.68rem font-size — extremely small target for a primary navigation action.
- The pipeline appears in TWO places: `nxLogsPipeline` (in the logs tab) and `nxLivePipeline` (in the live tab). Different IDs but identical structure — operators may not understand the duplication.

**Log output readability:**
- Log output container (`nxTab-logs`) has no visible CSS class for typography — inherits from parent.
- Log lines use `.log-line.error`, `.log-line.success` etc. (stability.css) — appropriate semantic coloring.
- No timestamp column visible in HTML. No step number prefix.
- Log chunks have no visual grouping separator between planning/coding/debugging phases.

---

## 3. HITL UX

### Current State

```html
<div class="nx-hitl-strip" id="nxHitlStrip" style="display:none">
  <div style="font-size:10px;font-weight:600;color:var(--text-muted);...">Agent Control</div>
  <div class="nx-hitl-row">
    <button class="nx-tiny-btn" id="hitlPauseBtn" onclick="hitlPause()" style="flex:1">⏸ Pause</button>
    <button class="nx-tiny-btn" id="hitlResumeBtn" onclick="hitlResume()" style="flex:1;display:none">▶ Resume</button>
    <button class="nx-tiny-btn" onclick="hitlRetry()" style="flex:1">↻ Retry</button>
  </div>
  <div class="nx-hitl-inject">
    <input class="nx-hitl-input" id="nxHitlInput" placeholder="Inject instruction...">
    <button class="nx-tiny-btn" onclick="...">Send</button>
  </div>
  <div class="nx-hitl-status">
    <span class="hitl-dot" id="hitlDot"></span>
    <span id="hitlStatusText" style="font-size:10px;color:var(--text-muted)">Idle</span>
  </div>
</div>
```

**Issues:**

**Label language:**
- "Agent Control" — bureaucratic. Operators need to know what they can *do*, not a category name.
- "⏸ Pause" and "▶ Resume" — media player metaphors. Operators think of pausing a process, not a media track. No context about what pausing means for the agent's current task.
- "↻ Retry" — from which point? The current step? The whole task? No tooltip or context.
- "Inject instruction..." placeholder — "inject" is a developer term. Operators say "add instruction" or "guide the agent".

**Cognitive load:**
- 3 buttons + 1 input + 1 status indicator crammed in a small strip in the left panel.
- The HITL strip is hidden (`display:none`) and appears only when execution is running — no preparation time for operator.
- Status text ("Idle" when running) is `font-size:10px` muted — nearly invisible.

**Decision surfaces:**
- No escalation context. When the agent pauses/requires HITL (from the uncertainty modal), the operator must decide based on a floating modal (`uncertaintyModal`) with no connection to the HITL strip. Two separate UX surfaces for the same concern.
- Uncertainty modal z-index 9999 but uses full inline styles — no connection to the design system.

---

## 4. TERMINAL INTEGRATION

### Current State

```html
<div class="nx-tab-content" id="nxTab-terminal" style="display:flex; flex-direction:column; height:100%;">
  <div style="display:flex; align-items:center; padding:4px 8px; border-bottom:1px solid var(--panel-border); 
    background:var(--panel); gap:8px;">
    <div id="xtermStatus" style="font-size:11px; color:var(--text-muted);">Initializing...</div>
    <input type="text" id="xtermQuickInput" ...  style="font-size:11px; padding:2px 6px; 
      background:var(--bg); border:1px solid var(--panel-border); color:var(--text); width:200px;"/>
    <button id="xtermRunBtn" class="nx-tiny-btn" onclick="xtermRunQuick()">▶ Run</button>
    <button class="nx-tiny-btn" onclick="xtermClear()">Clear</button>
  </div>
</div>
```

**Issues:**
- Terminal header uses mix of inline and token styles — partially correct.
- `xtermStatus` label says "Initializing..." — no update to "Connected" or "Ready" visible.
- Quick input width `200px` is fixed — on narrow viewports, this overflows.
- No visual connection between the tab and execution context. When execution runs a command, there's no indicator in the Terminal tab that something is happening.
- "▶ Run" and "Clear" labels are ambiguous — "Run" what exactly? The quick input value.
- No session ID or execution context displayed in terminal header.

**Mission linkage:**
- Terminal tab has no reference to the currently active mission/session.
- No breadcrumb or banner indicating "Running task: X — 3 commands executed".
- When execution spawns terminal commands, they appear in xterm but there's no callout or marker in the terminal header.

---

## 5. INSPECTOR UX

### Current State

Inspector uses well-structured CSS classes in layout.css:
- `.nx-inspector-section` — correct
- `.nx-insp-label` — correct (10px, 700, uppercase, letter-spacing)
- `.nx-stat-grid` / `.nx-stat` / `.nx-stat-val` — correct

**Issues:**

**Chain readability:**
- The model chain (P5 route info) uses `p5-ri-chain` div with no visible CSS (it's defined somewhere in base.css but not easily scannable).
- Provider routing info (`p5RouteInfo`) is hidden by default and appears mid-inspector — disorienting when it appears.

**Validation grouping:**
- Multiple inspector sections (Status, Model, Memory, Agents, Metrics, Learning, Decisions, Output, Downloads) appear in a fixed order regardless of execution state.
- During execution: Agents and Metrics sections are most relevant but they're mid-list.
- Idle: Status section shows "—" values for everything — dead information.

**Confidence clarity:**
- Intelligence score section (`p10MemSection`) shows a "Grade" badge — text grade (A, B, C?) but no numeric confidence, no trend.
- Memory metrics (`p10MemSection`) shows numeric values but no context about what's normal or expected.

**Visual density:**
- `.nx-insp-label` border-bottom creates visual weight between sections — good for separation but sections have no padding between them (only `margin-bottom:10px` on `.nx-inspector-section`).
- The observability panel (thoughts + actions feed) sits above the inspector sections in the HTML but below them visually in the right panel — ordering unclear without reading full HTML.

---

## 6. EMPTY/IDLE STATES

### Current State

```html
<div class="nx-idle-hero" id="nxIdleHero" style="display:flex;...height:100%;color:#8b949e;...">
  <!-- SVG icon (opacity 0.3) -->
  <div style="font-size:13px;font-weight:500;color:#c9d1d9;">Ready for execution</div>
  <div style="font-size:12px;color:#6e7681;display:flex;gap:12px;">
    <span><kbd>⌘K</kbd> Commands</span>
    <span>|</span>
    <span><kbd>⌘↵</kbd> Execute</span>
  </div>
  <div class="nx-hero-chips" style="...opacity:0.7;">
    <button class="nx-hero-chip" onclick="nxSetTask('Run full test suite')">Run Tests</button>
    <button class="nx-hero-chip" onclick="nxSetTask('Audit workspace for errors')">Audit Workspace</button>
  </div>
</div>
```

**Issues:**

**Operational calmness:**
- `opacity:0.3` on the hero icon creates a "dead" feeling — ghost icon rather than calm readiness.
- "Ready for execution" is passive. An operational platform should say what's ready and for what.
- `opacity:0.7` on chips — demoted rather than invited.

**Guidance quality:**
- Only 2 action chips — "Run Tests" and "Audit Workspace". These are specific to project debugging, not general enough for a first-time operator.
- No reference to the current session state (active session, model configured, tokens remaining).
- The idle hero hides completely when a task runs — no graceful transition. It appears/disappears abruptly.

**Dead feeling:**
- The CSS `.nx-idle-hero` has `animation: nxFadeInUp 0.35s` — this fires once on appearance. No ambient motion while idle (the `.nx-hero-logo` breathe animation exists in CSS but the logo in idle HTML is an SVG, not the `.nx-hero-logo` class element).
- The `nxHeroBreathe` keyframe animation (CSS) is defined but its target class `.nx-hero-logo` is never instantiated in the idle hero HTML — wasted animation definition.

---

## 7. ACTIVITY BAR

### Current State

```html
<div id="nxActivityBar" style="display:none">
  <span class="nx-ab-dot"></span>
  <span id="nxAbText" style="color:var(--text-muted);font-size:11px">AI is working...</span>
  <!-- file label, open button, steps badge, pause/resume/stop buttons -->
</div>
```

**Issues:**
- Good: uses CSS classes for dots, buttons.
- Concern: `display:none` on activity bar with no transition — abrupt appearance at execution start.
- "AI is working..." text is always the same — never updates to reflect what the AI is doing.
- Steps badge shows "X writes" — "writes" is developer language. Operators understand "file changes".

---

## SIGNAL / NOISE RATIO ASSESSMENT

| Surface | Signal Density | Noise Sources |
|---|---|---|
| Composer | Medium | Dashed toolbar border, large buttons, identical visual weight |
| Pipeline bar | Low | Generic stage names, abrupt show/hide, tiny "Full View" button |
| HITL strip | Low | Debug language, hidden by default, two separate decision surfaces |
| Terminal header | Medium | "Initializing..." permanent status, fixed-width input |
| Inspector | Medium-High | Good classes, but wrong ordering by execution relevance |
| Idle hero | Low | Ghost icon, 2 generic chips, no session context |
| Activity bar | Medium | Generic "AI is working..." text |

---

## IMPLEMENTATION TARGETS

1. **Composer**: Reduce padding (12px top), fix textarea focus ring, upgrade toolbar from dashed to solid border, use solid surface for mode/scope selects
2. **Pipeline bar**: Add fade-in transition, richer stage labels
3. **HITL**: Rename "Agent Control" → operator language, add inject input `aria-label`
4. **Idle hero**: Fix chip opacity, add 3rd chip, use CSS colors (not inline), add session context slot
5. **Activity bar**: CSS-based show/hide transition
6. **Inspector**: No structural changes, add `aria-label` and `role` to section toggles
7. **Terminal**: Fix status label, make quick input responsive width
