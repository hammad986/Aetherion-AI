# Z35 Execution Density Report

**Phase:** Z35B — Execution Density + Operator Flow  
**Date:** 2026-05-16  
**Verdict:** FOUNDATIONAL — density scaling operational, attention routing live

---

## Intelligent Density Scaling

Density is governed by three inputs: execution phase, runtime pressure (0-1 composite), and layout mode. As pressure increases, non-critical surfaces are collapsed (memory, skills). As pressure decreases and phase returns to idle, surfaces are restored. This prevents the workspace from becoming cluttered during high-load execution while keeping all surfaces accessible during quiet periods.

Pressure composite formula:
```
pressure = (tokenPressure × 0.45) + (retryPressure × 0.25) + (errorPressure × 0.20) + (confPressure × 0.10)
```

Where:
- `tokenPressure` = tokens / 80,000 (capped at 1.0)
- `retryPressure` = retries / 8 (capped at 1.0)
- `errorPressure` = errors / 4 (capped at 1.0)
- `confPressure`  = max(0, 1 - confidence) × 0.3

At pressure ≥ 0.75, memory and skills surfaces collapse.  
At pressure < 0.4 + idle phase, they are restored.

---

## Contextual Surface Expansion

| Trigger | Expands | Collapses |
|---------|---------|-----------|
| `executing` / `planning` phase | DAG | — |
| `replay` phase | DAG, Timeline | Inspector |
| `recovering` / `escalating` phase | Inspector | — |
| Pressure ≥ 0.75 | — | Memory, Skills |

Expansion applies `flex-grow: 2` within the flex container, giving the relevant surface proportionally more space without hard pixel values. This adapts gracefully to different viewport heights.

---

## Attention Routing

Attention is routed by dimming non-primary surfaces (opacity: 0.6) and highlighting the active surface with a faint blue border:

| Phase | Active Surface | Dimmed |
|-------|---------------|--------|
| executing / planning | DAG | — |
| recovering / escalating | Inspector | DAG |
| replay | Timeline | — |

Dimming is applied via CSS class `z35-attention-dimmed`. It is intentionally light (0.6 opacity) — the operator should still be able to read dimmed surfaces. Full opacity is restored immediately on phase change.

---

## Remaining Density Conflicts

1. **Surface expansion conflicts with Z30's explicit `height` bindings.** The DAG panel height is set by Z30's collapse/expand toggle. `flex-grow: 2` will be overridden if Z30 applies inline `height` styles. In practice, expansion via flex-grow works unless the operator has manually resized the DAG panel.

2. **Memory and skills surface collapse targets** (`z31ForensicPanel`, `.z32-skill-panel`) may not always be present in the DOM simultaneously. The collapse is silently skipped if the element is absent.

3. **Collapsed surfaces** (`max-height: 32px`) still show their header bars. If Z31/Z32 panels don't have a 32px header, the collapsed state may appear completely blank rather than showing a collapse indicator.

4. **Attention dimming** applies to entire panel elements. If Z30 renders the DAG panel and the forensic panel inside the same flex container with no clear element boundaries, dimming one may dim the other unintentionally.

---

## Remaining Operator Overload Risks

- Automatic surface collapse during pressure spikes may feel unexpected to operators who have manually scrolled to the memory panel. Adding a `data-z35-user-pinned` guard would prevent auto-collapse on pinned surfaces — not yet implemented.
- Flex-grow expansion transitions use a `250ms ease` CSS transition. If the operator rapidly switches phases (planning → recovering → planning in <500ms), the surface may appear to "bounce". This is a cosmetic issue only.
