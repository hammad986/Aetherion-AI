# Z45_RUNTIME_PRESSURE_ANALYSIS.md
## Phase Z45D — Runtime Pressure Intelligence Analysis

---

### Pressure System Inventory (Pre-Z45)

| System | Location | What it tracks |
|--------|----------|---------------|
| Z36 PressureMemory | `nx-z36-cohesion.js` | Node-level retry/error accumulation |
| Z36 forensic panel | `nx-z36-cohesion.js` | `failureReasons`, `errors`, `retries` per node |
| Z35 pressure bar | `nx-z35-mission.js` | Session-level overall pressure (0-100%) |
| Z37 risk indicator | `nx-z37-causal.js` | LOW/MEDIUM/HIGH dependency risk |
| Z30 instability | `nx-z30-dag.js` | retry_storm, stuck_node detection |

The pressure infrastructure was already sophisticated. Z45 needed to SURFACE it, not build it.

---

### Z45D Pressure Intelligence Added

**CSS-level pressure surfacing:**
```css
[data-pressure-tier="critical"] { border-right: 2px solid #C0392B; }  /* DAG nodes */
[data-pressure-tier="high"]     { border-right: 2px solid #D97706; }
[data-pressure-tier="medium"]   { border-right: 2px solid #C28A00; }

.z33-tl-event[data-pressure-tier="critical"] { border-left: 2px solid #C0392B; }
.z33-tl-event[data-pressure-tier="high"]     { border-left: 2px solid #D97706; }
```

**JS-level pressure stamping:**
- `NxBus.on('z36.pressure.update')` → stamps `data-pressure-tier` on DAG/timeline elements
- `NxBus.on('z36.node.focus')` → enriches the forensic inspector with retry count hint

---

### Pressure Visualization Design

**Why right-edge stripes on DAG nodes?**
The right edge is chosen because:
- Left edge is used for type differentiation (retry, recovery, etc.)
- Right edge is the natural "trailing indicator" — where pressure accumulates
- Color hierarchy: critical (red) > high (amber) > medium (yellow) > none

**Why NOT large visual indicators?**
The spec says "operational awareness" not "fear-inducing visuals." A 2px right stripe communicates pressure without alarming the operator. The quantitative data (retry count) is in the inspector forensic section.

---

### Pressure Tiers

| Tier | Retry count | Color | Meaning |
|------|-------------|-------|---------|
| critical | ≥ 4 retries | #C0392B red | Node is unstable, likely to fail |
| high | 2-3 retries | #D97706 amber | Elevated failure risk |
| medium | 1 retry | #C28A00 yellow | Minor pressure, monitoring |
| low / none | 0 retries | (no indicator) | Healthy |

---

### Cascade Risk Indicators

Z36's forensic section already calculates cascade risk:
```javascript
const cascadeRisk = node.errors >= 3 ? 'high' : node.errors >= 1 ? 'medium' : 'none';
const retryAmp    = node.retries >= 4 ? 'amplified' : node.retries >= 1 ? 'active' : 'none';
```

Z45 surfaces these through:
1. The forensic panel (already rendered by Z36)
2. The Z45 pressure hint in the inspector (retry count + tier badge)
3. The Z35 pressure bar (color now matches Z44 state color)

---

### Remaining Pressure Intelligence Gaps

1. **Bottleneck ranking surface**: Z36 `PressureMemory.getHotspots()` returns ranked hotspots but only the top 3 are stamped with pressure tiers. No "Top 3 bottlenecks" summary panel exists in the inspector.

2. **Retry amplification warnings**: When retries cascade (node A retries → triggers node B retry → triggers node C retry), this "amplification" pattern is detected by Z36 but not shown as a visual chain in the workspace.

3. **Stabilization confidence**: After recovery, the confidence that the system won't fail again is not shown. Z32 has a confidence score but it's in the Z32 UI, not the main inspector.

4. **Failure probability hints**: The system doesn't show a "60% chance of failure on next step" type indicator — this would require ML inference data not currently in the status API.

5. **Runtime stress mapping**: A heat map of all nodes colored by pressure tier was considered but rejected — it would require touching the DAG canvas rendering code (Z30).

### Production Readiness Verdict

> **PASS** — Pressure tiers are surfaced on DAG nodes and timeline events via CSS right-edge stripes. Inspector forensic section shows retry counts and cascade risk. Z35 pressure bar tracks overall pressure. No fear-inducing visuals — all indicators are 2px stripes and small text badges.
