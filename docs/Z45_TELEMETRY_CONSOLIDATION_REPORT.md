# Z45_TELEMETRY_CONSOLIDATION_REPORT.md
## Phase Z45E — Telemetry Consolidation Report

---

### Pre-Z45 Telemetry Fragmentation Inventory

| Indicator | Location | Overlaps With |
|-----------|----------|--------------|
| Z35 mission bar | `#z35MissionBar` — injected by Z35 JS | Z44 mission strip |
| Z44 mission strip | `#nx-mission-strip` — injected by Z44 JS | Z35 mission bar |
| Z43 exec state | `body[data-nx-exec]` set by Z43 JS | Z44 also sets this |
| Z43 runBtn observer | `nx-z43-exec-state.js` | Z44 runBtn observer |
| Z36 forensic panel | `#z36ForensicSection` | Z37 causal section |
| Z37 causal section | `#z37CausalSection` | Z36 forensic panel |
| `#pulse` element | Legacy runtime pulse | Z43/Z44 `.nx-run-dot` pulse |
| Z33 memory sidebar | `.z33-sidebar-memory` | — |
| Z38 patterns feed | Z38 inspector section | — |
| Z34 replay panel | `#z34InspectorBody` | Z36 forensic panel |

---

### Consolidations Applied in Z45

#### 1. Mission Surface: Z35 bar as canonical (highest impact)

**Problem**: Z44 injected `#nx-mission-strip` (24px) between composer and tab bar. Z35 already had `#z35MissionBar` (26px) serving the same function — showing mission objective + phase + confidence + pressure.

**Resolution**: Z45 detects both elements. When `#z35MissionBar` is present:
1. `#nx-mission-strip` receives `data-z45-consolidated="true"` → `display:none`
2. Z44's MutationObserver on `.nx-mission-text` is patched to route updates to `#z35MissionObjective`
3. Z35 bar styled to Z44/Z45 token system — same visual quality

**Net result**: One mission surface instead of two. Z35 bar now shows log narrative + structured telemetry.

#### 2. Runbtn Observer: Documented, not removed

**Problem**: `nx-z43-exec-state.js` and `nx-z44-runtime.js` both observe `#runBtn.is-running` and both set `body[data-nx-exec]`.

**Analysis**: Both scripts produce identical output (`data-nx-exec = "running" | "idle"`). Z44 also sets `data-nx-state`. There is no conflict — the Z43 script is redundant but harmless.

**Resolution**: Documented via `html[data-z45-exec-observers="2"]` attribute. Not removed — removing the script link could cause unexpected state timing issues if the load order changes.

**Risk of removal**: Low. The Z43 script is 38 lines. Recommended for removal in a dedicated cleanup phase when all Z43/Z44 integration is validated.

#### 3. `#pulse` element suppressed

Z44G had already applied `#pulse { display: none !important; }`. Z45 stamps it with `data-z45-suppressed="true"` for audit traceability.

#### 4. Z36 + Z37 forensic panels: both preserved (intentional)

**Problem**: Z36's `#z36ForensicSection` shows decision chain + failure pressure. Z37's `#z37CausalSection` shows dependency traces.

**Resolution**: These are complementary, not duplicate:
- Z36 forensic: WHAT happened (decision chain, retry storm)
- Z37 causal: WHY it happened (dependency trace, risk indicator)

Both preserved. Positioned naturally by their mount order in `#nxRightBody`.

---

### Remaining Telemetry Fragmentation

| Fragmentation | Severity | Status |
|---------------|----------|--------|
| Z43 runBtn observer duplicate | Low | Documented, deferred removal |
| Z44 story feed vs Z35 mission bar | Medium | Partially consolidated |
| Z36 + Z37 forensic overlap | Low | Accepted (complementary) |
| Z34 + Z36 replay panels | Medium | Documented, not merged |
| Multiple session lists (P4, Z31) | Medium | Out of scope (different data) |

---

### Inspector Clutter Reduction Results

| Before Z45 | After Z45 |
|------------|-----------|
| 2 mission surfaces | 1 (Z35 canonical) |
| `#pulse` visible | Suppressed |
| `#nx-mission-strip` visible | Hidden when Z35 bar present |
| Z43 exec-state observer active | Active but harmless (documented) |
| Z36 forensic unstyled | Styled with Z45 token system |
| Decision chain unreadable | Causal arrows + state colors |
| Pressure tiers invisible | Right-edge stripes on DAG/timeline |

### Production Readiness Verdict

> **PASS** — The most impactful consolidation (Z35 mission bar vs Z44 mission strip duplication) is resolved. Observer redundancy is documented and low-risk. Inspector is cleaned up with normalized typography and suppressed legacy elements. Full observer deduplication is a safe future-phase cleanup.
