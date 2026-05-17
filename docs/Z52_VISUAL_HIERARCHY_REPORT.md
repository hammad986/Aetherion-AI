# Z52 Visual Hierarchy Report

## Phase Z52D — Attention Hierarchy Rebalancing

**Date:** 2026-05-17  
**Status:** COMPLETE  

---

## Problem: Hierarchy Collapse

Before Z52, operators could not immediately identify "what matters most" because:
- Primary action (run button) and secondary controls (settings, history, model selector) had similar visual weight
- Section labels competed with content text
- Panel depth was flat — all panels shared the same dark grey tone
- Error states had the same border-radius as informational cards
- Active model / plan display was no more prominent than idle metric labels

---

## Hierarchy Model Applied

Z52 implements a four-tier visual hierarchy:

```
Tier 1: Primary actions   (run button, primary CTA)
         — Full opacity, maximum contrast, accent or white

Tier 2: Active content    (current model, running status, HITL queue)
         — 85–100% opacity, primary text colour

Tier 3: Contextual info   (section labels, metric values, recent history)
         — 55–75% opacity, secondary text colour

Tier 4: Infrastructure    (dividers, panel borders, separators, muted hints)
         — 25–45% opacity, near-invisible
```

---

## Changes Applied

### Primary Action Elevation
- Run button: `font-weight: 600`, `letter-spacing: 0.01em` — slightly more typographic weight than surrounding controls
- During idle: `box-shadow: none !important` (from Z51) prevents it glowing unnecessarily

### Section Labels
All `.nx-insp-section-label` and `.nx-panel-section-title` elements:
- `10px / 600 weight / uppercase / 0.08em spacing / rgba(72,79,88,0.85)` — clearly tertiary, not competing with content

### Panel Background Depth
Left panel background: `rgba(8,11,16,0.6)` — fractionally darker than the main workspace (`--bg`). Creates the impression of the left panel being "behind" the composition area without a harsh border line.

Right inspector: border-left `rgba(255,255,255,0.05)` — near-invisible but present.

Topbar: `border-bottom: rgba(255,255,255,0.06)` — structural hint, not a dividing wall.

### Separator Muting
`.nx-sep`, `.nx-divider-line`: `opacity: 0.35`. Separators are infrastructure, not content. They should guide the eye, not stop it.

### Log Area During Idle
`body:not(.nx-running) #logArea { opacity: 0.85 }` — the log area is less prominent when there's nothing running. It still reads clearly but doesn't compete with the composer. Transitions to `opacity: 1` when execution begins.

### Muted Text Cleanup
`[class*="muted"]`: `rgba(139,148,158,0.65)` — standardises all "muted" class instances to a consistent value, removing inconsistencies where some muted elements were `#484f58` (too dark) and others were `#8b949e` (too bright for a muted tier).

---

## Remaining Hierarchy Issues

1. **Model selector dropdown** — the plan/mode selector in the composer area has the same visual weight as the submit button on small widths. The selector should be visually subordinate.
2. **Notification bell + user badge** — both in the topbar at full opacity compete with the product logo and page title. They should be at 70% opacity until hovered.
3. **Inspector section content vs labels** — some sections have content that's the same `font-size` as their label, making it unclear which is which.
4. **Keyboard shortcut hints** (added in Z51) — currently hidden until hover. On first use, operators don't know they exist. A one-time subtle hint on focus would help.
5. **The queue count badge** — the zero-count badge (0) is always visible at full opacity. It should only be visible when count > 0.

---

## Visual Hierarchy Score: 7.5/10

The four-tier hierarchy model is now applied consistently. Primary actions are elevated. Infrastructure is muted. Panel depth creates spatial separation. Key remaining gaps are in the topbar (notification/badge prominence) and composer area (mode selector weight).
