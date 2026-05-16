# Z35 Contextual Adaptation Certification

**Phase:** Z35D — Contextual Workspace Adaptation  
**Date:** 2026-05-16  
**Verdict:** CERTIFIED — five layout modes, phase-driven transitions

---

## Layout Modes

| Mode | Trigger Phase | Expands | Collapses | Attention |
|------|-------------|---------|-----------|-----------|
| `execution` | idle, planning, executing, validating | DAG | Timeline | DAG |
| `replay` | replay | DAG + Timeline | Inspector | Timeline |
| `forensic` | forensic (Z34 cursor) | Inspector + Memory | — | Inspector |
| `escalation` | escalating | Inspector | Memory + Skills | Inspector |
| `recovery` | recovering | Inspector + DAG | — | Inspector |

Modes are applied via `data-z35-mode` on `<html>`. CSS rules keyed to this attribute adjust panel proportions, border emphasis, and indicator colors. Transitions between modes complete in ≤ 250ms.

---

## Smart Collapse Governance

Non-critical surfaces (Memory/Z31, Skills/Z32) are collapsed automatically when:
- Pressure ≥ 0.75, OR
- Layout mode = escalation

They are restored when:
- Pressure returns to < 0.4, AND
- Phase = idle

A surface is not collapsed if it was recently manually expanded by the operator (future: `data-z35-user-pinned` guard). Currently all surface collapses are unconditional on threshold crossing.

---

## Surface Priority Resolution

When multiple expansion rules would apply simultaneously (e.g., recovering phase during replay), the layout mode derived from phase takes precedence. Phase → mode derivation:

```
recovering → recovery mode (inspector + dag expand)
replay     → replay mode   (dag + timeline expand)
escalating → escalation mode (inspector expands)
```

Phase transitions that would flip between two modes in rapid succession are not debounced — the mode change applies immediately on the next RAF update cycle (< 16ms). This is intentional: rapid phase transitions represent real execution state changes.

---

## Remaining Density Conflicts

1. **Z30's DAG panel `height` is set by its own collapse toggle** with inline styles. Z35's `flex-grow: 2` may not visually expand the DAG panel if Z30 has applied a fixed `height`. The two systems don't coordinate — Z30's explicit height wins.

2. **Z33 timeline dock height** (`max-height: 260px` in replay mode) is a new CSS rule from Z35. If Z33 sets its own max-height, the Z35 rule may be overridden depending on cascade specificity. Use `!important` in a hotfix if needed.

3. **Inspector width in forensic mode** (`300px`) and recovery mode (`280px`) overrides Z34's default `260px`. This is intentional — forensic analysis requires more detail space.

4. **Collapse and expand transitions** both use CSS `transition` on `flex-grow` and `max-height`. If the browser has `prefers-reduced-motion` active, these transitions will still run because they are structural (not decorative). A future update should respect `@media (prefers-reduced-motion)`.

---

## Remaining Immersion Weaknesses in This Phase

- Mode transitions produce no sound, no animation beyond the CSS structural change. The operator notices the layout shift but there is no explicit "mode changed" notification. This is by design (no telemetry walls, no notifications on every phase change) but operators new to the system may find the layout shifts unexpected.
- The layout mode indicator badge (`EXECUTION`, `REPLAY`, etc.) in the DAG header provides the only explicit mode label. It is small (9px, 5px padding). On high-DPI displays it is clear; on low-DPI this may be hard to read.
