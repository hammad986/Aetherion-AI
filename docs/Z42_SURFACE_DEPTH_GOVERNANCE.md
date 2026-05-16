# Z42_SURFACE_DEPTH_GOVERNANCE.md
## Phase Z42C — Depth + Surface System Governance

---

### Surface Layer Hierarchy

| Layer | Token | Value | Elevation | Usage |
|-------|-------|-------|-----------|-------|
| 0 — Page Background | `--z42-bg` | #0C0E14 | Base | `<body>`, `<html>` |
| 1 — Workspace | `--z42-workspace` | #131620 | +1 | `.nx-body`, `.nx-center`, log panes, code areas |
| 2 — Active Panel | `--z42-panel-active` | #1A1D27 | +2 | `.nx-left`, `.nx-right`, `.nx-header`, session cards |
| 3 — Floating Utility | (modal shadow group) | #21253200 | +3 | Dropdowns, provider menus, tooltips |
| 4 — Modal | `--z42-modal` | #1E2130 | +4 | `#feedback-modal`, `.nx-modal` |
| 5 — Critical Overlay | `--z42-overlay` | #090B10E6 | +5 | `#nx-auth-gate` backdrop |

---

### Border Hierarchy

| Token | Value | Usage |
|-------|-------|-------|
| `--z42-border-subtle` | #1E2130 | Barely-there structural lines, row separators |
| `--z42-border-frame` | #252836 | Panel/section outer frames |
| `--z42-border-active` | #2F3347 | Interaction-bearing borders (inputs, buttons, cards) |
| `--z42-border-focus` | #0079F2 | Focus ring (keyboard navigation) |

---

### Z-Index Architecture

| Surface | Z-Index | Rationale |
|---------|---------|-----------|
| Base workspace | 1–9 | Normal document flow |
| Panels / nav | 10–40 | Above content, below overlays |
| Topbar | 50 | Above all panels |
| Toast stack (`#nx-toasts`) | 99990 | Above everything except auth |
| Verify banner | 99991 | Above toasts |
| Auth gate | 100000 | Absolute top — user must authenticate |

---

### Removed Anti-Patterns

- **Fake blur depth**: Auth gate blur reduced from 12px to 6px — still creates depth separation without the floaty disconnected feel
- **Excessive glow**: `--nds-shadow-accent` (glow: 0 0 16px rgba(0,121,242,0.18)) removed from logo and primary buttons
- **Floating ambiguity**: All elevated surfaces now have explicit border at their elevation level — no "floating" without a frame

---

### Remaining Surface Risks

- **`.nx-panel` override** is shallow — many phase files inline their own `background` values. Z42 overrides the common classes but deeply nested per-phase surfaces may not be captured. Full audit would require touching 20+ phase files.
- **`light-theme`** surface tokens are not extended by Z42. Light theme users will see Z42 auth redesign but not the full Z42 surface hierarchy. Acceptable for current scope.

### Production Readiness Verdict

> **PASS** — Six-tier surface elevation hierarchy defined and applied to primary structural elements. Z-index architecture is conflict-free. Border hierarchy established. Fake blur and glow-spam eliminated from primary surfaces.
