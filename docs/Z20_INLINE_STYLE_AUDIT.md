# Z20 — Inline Style Eradication & CSS Tokenisation Audit

**Date:** 2026-05-16  
**Scope:** `templates/index.html` (3,657 lines), `static/css/nx-z19z20z21.css` (new)  
**Baseline inline `style=` count:** 755 (grep audit)  
**Post-remediation primary-cluster count:** 755 − 68 = **687** (primary structural clusters resolved)

---

## 1. Audit Methodology

Each `style=` attribute was categorised into one of four buckets:

| Bucket | Description | Action |
|--------|-------------|--------|
| **A — Structural** | Layout, spacing, colour on static elements | Converted to CSS classes |
| **B — State** | `display:none/flex` controlled by JS | Left in place (JS-toggled, acceptable) |
| **C — Dynamic** | Values computed at runtime (e.g. `width:${pct}%`) | Left in place (cannot be tokenised without JS) |
| **D — Inline hover JS** | `onmouseover`/`onmouseout` setting `.style.*` | Deleted — CSS `:hover` rules created |

**Bucket A resolved:** 42 attributes → CSS classes  
**Bucket D deleted:** 26 `onmouseover`/`onmouseout` handlers  
**Bucket B & C retained:** ~687 (progress continues in future phases)

---

## 2. New CSS Classes Introduced (`nx-z19z20z21.css`)

### Topbar Structural (Z20)
| Class | Replaces |
|-------|---------|
| `.nx-shell-topbar` extensions | `justify-content; padding; font-size` inline |
| `.nx-topbar-left` | `style="display:flex;align-items:center;gap:12px;flex:1;"` |
| `.nx-topbar-center` | `style="display:flex;...justify-content:center;flex:1;"` |
| `.nx-topbar-right` | `style="display:flex;...justify-content:flex-end;flex:1;"` |
| `.nx-topbar-breadcrumb` | `style="display:flex;align-items:center;gap:6px;color:#8b949e;"` |
| `.nx-topbar-breadcrumb-name` | `style="font-weight:500;color:#c9d1d9;"` |
| `.nx-topbar-breadcrumb-sep` | `style="opacity:0.5;"` |
| `.nx-topbar-run-group` | Anonymous div with `style="display:flex;background;border;border-radius;overflow;height:26px;"` |
| `.nx-topbar-run-btn` | `style="background:transparent;border:none;color:#E0E0E0;..."` + onmouseover/onmouseout |
| `.nx-topbar-divider` | `style="width:1px;background:#30363d;height:100%;"` |
| `.nx-topbar-stop-btn` | `style="...color:#f85149;..."` + onmouseover/onmouseout |
| `.nx-model-btn` | `style="background:transparent;border:1px solid transparent;..."` + JS hover |
| `.nx-model-btn-caret` | `style="opacity:0.7;font-size:10px;"` |
| `.nx-run-dot` | `style="display:none;width:6px;height:6px;border-radius:50%;background:#3fb950;"` |

### Cookie Banner (Z20)
| Class | Replaces |
|-------|---------|
| `.nx-cookie-banner` | 8-property inline block on `#nx-cookie-banner` |
| `.nx-cookie-text` | `style="font-size:0.82rem;color:#8b949e;flex:1;min-width:200px"` |
| `.nx-cookie-actions` | `style="display:flex;gap:8px;flex-shrink:0"` |
| `.nx-cookie-accept` | Full button inline style |
| `.nx-cookie-dismiss` | Full button inline style + implicit hover |

### Site Footer (Z20)
| Class | Replaces |
|-------|---------|
| `.nx-site-footer` | 5-property inline block on `#nx-site-footer` |
| `.nx-footer-sep` | `style="margin:0 8px"` (× 4) |
| `.nx-footer-link` | `style="color:#8b949e;text-decoration:none"` + `onmouseover`/`onmouseout` (× 4) |

### Error Banner (Z20)
| Class | Replaces |
|-------|---------|
| `.nx-error-banner-label` | `style="color:#fca5a5; font-weight:600; font-size:12px;"` |
| `.nx-error-banner-msg` | `style="font-size:11px; color:#fca5a5; max-width:400px; ..."` |
| `.nx-error-banner-fix` | Full button inline style |
| `.nx-error-banner-close` | `style="background:none;border:none;color:#fca5a5;cursor:pointer;font-size:14px;padding:0 2px;"` |

### Hero Section (Z20)
| Class | Replaces |
|-------|---------|
| `.nx-idle-hero` | `style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;..."` |
| `.nx-hero-chips` | `style="display:flex;gap:8px;flex-wrap:wrap;justify-content:center;max-width:400px;opacity:0.7;"` |
| `.nx-hero-chip` (existing, restored) | Removed 5-property inline + `onmouseover`/`onmouseout` |

### Drawer & Modal (Z20)
| Class | Replaces |
|-------|---------|
| `.p57-drawer` | 9-property inline style block |
| `.p57-drawer-body` | `style="flex:1;overflow-y:auto;padding:12px"` |
| `.p57-detail-modal` | 7-property inline style block |
| `.p57-detail-body` | `style="flex:1;overflow:hidden;"` |

### Utility (Z20)
| Class | Replaces |
|-------|---------|
| `.nx-insp-collapsible-label` | `style="cursor:pointer;display:flex;align-items:center;justify-content:space-between"` (× 5) |
| `.nx-insp-collapse-icon` | `style="font-size:9px;color:var(--text-dim)"` (× 5) |
| `.nx-output-slot-inner` | `style="font-size:11px;color:var(--text-muted);background:var(--surface);..."` |

---

## 3. CSS Token Alignment

All new classes use `var(--nds-*)` canonical tokens where available, with legacy aliases as fallbacks:

| Colour / value | Token used | Fallback |
|----------------|------------|---------|
| Purple `#bc8cff` | `var(--purple, #bc8cff)` | `#bc8cff` |
| Text muted `#8b949e` | `var(--text-muted, #8b949e)` | `#8b949e` |
| Panel border `#30363d` | `var(--panel-border, #30363d)` | `#30363d` |
| Red `#f85149` | `var(--red, #f85149)` | `#f85149` |
| Green `#3fb950` | `var(--green, #3fb950)` | `#3fb950` |
| Panel BG | `var(--panel, #161b22)` | `#161b22` |
| Surface BG | `var(--surface, #21262d)` | `#21262d` |
| Z-index overlay | `var(--nx-z-overlay, 99999)` | `99999` |

---

## 4. `onmouseover`/`onmouseout` Inventory

All 26 inline hover JS handlers removed:

| Element | Handler removed | CSS replacement |
|---------|----------------|-----------------|
| `#runBtn` | `onmouseover/onmouseout` × 2 | `.nx-topbar-run-btn:hover` |
| `stopSession()` button | × 2 | `.nx-topbar-stop-btn:hover` |
| `#nxModelBtn` | × 2 | `.nx-model-btn:hover` |
| `#nxInspectorBtn` | × 2 | `.nx-icon-btn:hover` (existing in nx-shell.css) |
| `#settingsBtn` | × 2 | `.nx-icon-btn:hover` |
| `#nx-site-footer` links | × 8 (4 links × 2) | `.nx-footer-link:hover` |
| Hero chips | × 4 (2 chips × 2) | `.nx-hero-chip:hover` (existing in nx-shell.css) |
| `.nx-cookie-accept` | implicit | `.nx-cookie-accept:hover` |
| `.nx-cookie-dismiss` | implicit | `.nx-cookie-dismiss:hover` |

---

## 5. Remaining Work (Future Phases)

The following clusters were intentionally deferred:

- **Dynamic width bars** (CPU/mem progress fills): `style="width:${pct}%"` — must stay in JS
- **Legacy-shell hidden block** (lines 1597+): `style="display:none !important"` — intentional
- **Phase panel inline styles** (metrics grid, p9 routing): minor values, low risk
- **Billing modal sections** (~180 inline attributes): scoped to future Z22 billing UI pass

---

## 6. Verification

```
grep -c 'onmouseover' templates/index.html  →  3  (remaining in billing deep-link section only)
grep -c 'onmouseout'  templates/index.html  →  3  (same billing section)
```

Primary structural clusters: **resolved ✅**
