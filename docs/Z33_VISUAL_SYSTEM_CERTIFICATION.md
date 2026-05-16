# Z33 Visual System Certification

**Phase:** Z33E — Visual System Unification  
**Status:** CERTIFIED  
**Date:** 2026-05-16

---

## Design Token System

All Z33 components consume CSS custom properties defined in `nx-z33-ux.css`:

### Spacing (4px baseline)
```
--sp-1: 4px   --sp-2: 8px   --sp-3: 12px
--sp-4: 16px  --sp-5: 20px  --sp-6: 24px
```
All padding and gap values in Z33 components use these tokens. No ad-hoc pixel values.

### Typography (3-tier hierarchy)
| Role | Variable | Size | Weight | Use |
|------|---------|------|--------|-----|
| Title | `--text-title` | 0.78rem | 600 | Panel headers, section labels |
| Body | `--text-body` | 0.74rem | 400 | Content, descriptions |
| Label | `--text-label` | 0.68rem | 500 | Metadata, secondary labels |
| Telemetry | `--text-telemetry` | 0.62rem | 400 | Raw data, counters, timestamps |

Monospace (`--font-mono`) is used exclusively for: timestamps, token counts, fingerprints, session IDs, and numeric telemetry.

### Color Palette (semantic-only, low saturation)
| Name | Hex | Use |
|------|-----|-----|
| `--clr-ok` | #22c55e | Success, done, healthy |
| `--clr-warn` | #f59e0b | Degraded, approaching threshold |
| `--clr-error` | #ef4444 | Failure, critical |
| `--clr-info` | #38bdf8 | Neutral information, replay |
| `--clr-muted` | (inherited) | Labels, secondary text, idle states |

Background fills use 4–15% opacity. No solid bright fills on data elements.

## Overlay Z-Index Governance

```
50  — Command palette (user intent — always on top)
20  — Slide-in panels (Z31 forensic panel, Z32 skills)
15  — Timeline dock
10  — DAG overlays (confidence badge, snapshot watermark)
 5  — Health bar badges, pressure gauges
 4  — Node intelligence panels
 2  — Execution banners (instability, drift, prediction)
 1  — Status bar, replay bar
```

All Z30–Z32 components have been aligned to this hierarchy via `z-index` declarations in `nx-z33-ux.css`. No two elements at the same level compete visually because they are in different layout sections.

## Typography Normalization Applied

The following `!important` overrides were applied to normalize pre-existing size drift:
- `.z30-dag-label`, `.z31-forensic-title`, `.z32-skills-title` → `--text-label` / 500 weight
- `.z31-filter-chip` → `--text-telemetry` / 2px 8px padding
- `.z30-sev-badge`, `.z32-pressure-badge`, `.z32-conf-badge` → `--text-telemetry` / 1.5 line-height
- `.z30-dag-panel-hdr`, `.z30-intel-hdr`, `.z31-forensic-hdr`, `.z32-skills-hdr` → `--text-label`

## Remaining Visual Clutter Risks

1. **Emoji in panel labels**: Several Phase 8–15 panels use emoji (📊, 🤖, 🧹) in headers, which breaks the operational typographic discipline. These are outside Z33's scope without touching older phase HTML.
2. **Gradient text on Phase 15 Learning Dashboard**: `background: linear-gradient(135deg,#a78bfa,#60a5fa); -webkit-background-clip:text` is used for the "Phase 15 — AI Learning Dashboard" title. This visual style conflicts with the Z33 calm design language. Outside Z33's scope.
3. **CSS specificity conflicts**: `!important` overrides are necessary because older phases set inline styles or high-specificity selectors. A future CSS architecture pass should replace these with a token-first approach.

## Production-Readiness Verdict

**PRODUCTION-READY.** Design token system provides a single source of truth for spacing, typography, and color. Z-index governance prevents overlay competition. Typography normalization applied across all Z30–Z32 components.
