# Z48B — Split Workspace Report

**Phase:** Z48B — Split Workspace Mode  
**Audit Date:** 2026-05-17  
**Status:** Delivered

---

## What Was Built

### Split Modes
| Mode | Activation | Layout |
|------|-----------|--------|
| Horizontal | ⊟ Split button / `Ctrl+Shift+H` | Main pane top, secondary pane bottom |
| Vertical   | `Ctrl+Shift+V` | Main pane left, secondary pane right |
| Off        | Click active button again | Single pane (default) |

### Implementation Approach
- **Pure CSS-based** — body-level class `z48-split-h` / `z48-split-v` applied via JS  
- **No Split.js nesting** — avoids conflicts with the existing three-panel `window.nxSplit` instance  
- **CSS custom property `--z48-split-pct`** for drag-adjustable split ratio  
- **Drag resizer** — mouse drag handle between panes; updates split ratio in real-time  
- **Persistent** — split mode and secondary tab choice saved to `localStorage` key `nx_z48_split`  
- **Restored on load** — applied 1s after DOMContentLoaded to avoid race with workspace init  

### Secondary Pane
- Mini tab bar with: Output, Code, Terminal, Diff, Intel, Live  
- Each secondary tab renders a contextual helper panel  
- Diff secondary tab shows a quick file comparison form that opens in the main diff tab  
- Other secondary tabs show a "Switch to X" shortcut button (they mirror instructions rather than duplicating complex panels)  

### Keyboard Shortcuts
| Shortcut | Action |
|----------|--------|
| `Ctrl+Shift+H` | Toggle horizontal split |
| `Ctrl+Shift+V` | Toggle vertical split |

### Split Toggle Button
- Injected into `#nxTabActions` in the tab bar  
- `active` state (blue tint) when split is active  

---

## Remaining Workspace Weaknesses

1. **Full panel mirroring** — the secondary pane does not fully mirror Output/Terminal/Code content; it shows instructions instead. Full content duplication would require significant re-architecture.  
2. **Resize handle on touch** — drag resizer uses `mousedown/mousemove/mouseup`; no touch events wired  
3. **Minimum size enforcement** — panes can be collapsed to near-zero; no minimum height constraint enforced on drag  

## Remaining Workflow Friction

- No preset layout buttons (e.g., "50/50", "70/30", "30/70")  
- No per-mode layout memory (horizontal and vertical ratios share one `--z48-split-pct` value)  

## Remaining Operational Gaps

- Vertical split not validated on narrow screens (< 800px); may cause layout issues  
- No "close secondary pane" button inside the pane itself  

## Remaining Usability Inconsistencies

- The split button shows only "⊟ Split" with no indication of current mode (H vs V)  
- Tab bar adapts poorly on very narrow center panels  

## Remaining Replay Weaknesses

- Replay controls do not resize correctly when split mode changes the center panel height  

## Remaining Artifact Relationship Gaps

- None specific to split workspace  

## Honest Workbench Maturity Score

| Dimension               | Score |
|-------------------------|-------|
| Split mode correctness  | 8/10  |
| Drag resize             | 7/10  |
| Persistence             | 9/10  |
| Secondary pane content  | 5/10  |
| Keyboard navigation     | 8/10  |
| **Overall Z48B**        | **7.5/10** |
