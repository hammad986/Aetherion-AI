# Z43_PANEL_COHESION_ANALYSIS.md
## Phase Z43C — Panel Cohesion System Analysis

---

### Panel Inventory

| Panel | ID/Class | Type | Z43 Cohesion Applied |
|-------|----------|------|---------------------|
| AI Thinking / Left | `.nx-panel.nx-left` | Slide | ✅ Panel hdr, session card, think sections |
| Center workspace | `.nx-panel.nx-center` | Fixed | ✅ Tab bar, composer, running border |
| Inspector / Right | `.nx-panel.nx-right` | Slide | ✅ Inspector sections, stat grid, model info |
| Bottom terminal | `.nx-bottom-wrap` | Strip | ✅ Border, header height, typography |
| Nav rail | `.nx-shell-navrail` | Rail | ✅ Icon sizing, active states, left indicator |
| Slide panels | `.nx-slide-panel` | Overlay | ✅ Panel header, close button |
| Settings drawer | `.p57-drawer` | Drawer | ✅ Border, shadow, background |
| Observability | `#nx-obs-panel` | Sub-panel | ✅ Feed headers, count badges, conn strip |

---

### Shared Spacing Rhythm

All panels now share:

```
Section label height:  28px   (--z43-section-label-h)
Section padding H:     12px   (--z43-section-pad-h)
Section padding V:      9px   (--z43-section-pad-v)
Section gap:            1px   (--z43-section-gap — border width between sections)
```

**Before Z43**: Section padding varied from 6px to 18px across phase files. Labels had no consistent height. Some sections had `padding: 12px`, others `padding: 10px 14px 8px`.

---

### Shared Elevation Logic

All panels use the Z42 6-tier surface system:
- Workspace (#131620) — tab content areas, log pane, stat cells
- Active panel (#1A1D27) — panel headers, tab bars, statusbar
- Subtle border (#1E2130) — row dividers, content separators
- Frame border (#252836) — panel outer borders
- Active border (#2F3347) — inputs, interactive elements

---

### Shared Border Governance

| Border type | Token | Usage |
|-------------|-------|-------|
| Panel outer frame | `--z42-border-frame` (#252836) | Panel outer edges |
| Content divider | `--z42-border-subtle` (#1E2130) | Within-panel row separators |
| Interactive border | `--z42-border-active` (#2F3347) | Inputs, buttons, cards |

**Removed**: Mix of `#27272A`, `#30363d`, `#21262d`, `rgba(255,255,255,0.06)` across phase files (all replaced by token system).

---

### Shared Operational Metadata Positioning

Metadata elements (counts, timestamps, session IDs) are consistently:
- Right-aligned within their row
- `font-family: var(--nds-mono)`
- `font-size: 10–11px`
- `color: var(--nds-text-lo)` or `var(--nds-text-dim)`

**Before Z43**: Some metadata was left-aligned, some used sans-serif, some were `0.7rem`, `0.78rem`, `10px`, `12px` without pattern.

---

### Shared Interaction Timing

All interactive elements use:
- Hover transitions: `0.10–0.12s` (fast, not jarring)
- Execution state transitions: `0.20–0.40s` (deliberate but responsive)
- No keyframe animations on hover states
- No spring/bounce easing (reserved for modals only)

---

### Remaining Cohesion Gaps

1. **Phase 8 plan modal**: Uses inline padding/margin styles — not fully token-governed. Visible only during billing flow.
2. **Phase 16/17/18 DAG/timeline**: DAG visualization uses canvas element — not CSS-governed. Cannot be cohesively styled without touching the JS rendering.
3. **xterm.js terminal**: Injects its own DOM structure — panel cohesion does not extend into terminal output.
4. **Tooltip elements**: No canonical tooltip pattern across phases — some phases use `title=""`, others use custom floating divs.

### Production Readiness Verdict

> **PASS** — Core panel system (left, center, right, rail, statusbar, drawer) shares consistent spacing, elevation, border, and timing. Deep sub-panels (xterm, Monaco, DAG canvas) are intentionally excluded from CSS-level cohesion enforcement.
