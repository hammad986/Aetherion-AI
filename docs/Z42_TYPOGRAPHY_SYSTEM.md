# Z42_TYPOGRAPHY_SYSTEM.md
## Phase Z42B — Operational Typography System

---

### Type Scale

| Role | Token | Size | Weight | Transform | Family | Usage |
|------|-------|------|--------|-----------|--------|-------|
| Display | `--z42-type-display` | 17px | 700 | none | IBM Plex Sans | Page-level identity, logo text |
| Heading | `--z42-type-heading` | 14px | 600 | none | IBM Plex Sans | Panel/section titles |
| Section Label | `--z42-type-section` | 11px | 600 | UPPERCASE | IBM Plex Sans | Section identifiers, field labels |
| Body | `--z42-type-body` | 13px | 400 | none | IBM Plex Sans | Standard UI text, input text |
| Operational Label | `--z42-type-op-label` | 11px | 500 | none | IBM Plex Sans | Tab labels, button-adjacent labels |
| Runtime Metadata | `--z42-type-meta` | 11px | 400 | none | JetBrains Mono | Token counts, timestamps, stats |
| Forensic Text | `--z42-type-forensic` | 10px | 400 | none | JetBrains Mono | Log lines, trace output, footers |

---

### Line Height Standards

| Context | Token | Value |
|---------|-------|-------|
| Display/Logo | `--z42-leading-display` | 1.2 |
| Headings | `--z42-leading-heading` | 1.3 |
| Body copy | `--z42-leading-body` | 1.5 |
| Dense operational UI | `--z42-leading-dense` | 1.35 |

---

### Semantic CSS Classes

```css
.z42-type-display    /* 17px/700 — page-level identity */
.z42-type-heading    /* 14px/600 — section/panel titles */
.z42-type-section    /* 11px/600 UPPERCASE — section labels */
.z42-type-op-label   /* 11px/500 — UI control labels */
.z42-type-meta       /* 11px/400 mono — runtime metadata */
.z42-type-forensic   /* 10px/400 mono — log/trace text */
```

---

### Standards Enforced

1. **Field labels**: Always 11px / 600 / UPPERCASE / 0.06em tracking — via `.nx-auth-field label` override
2. **Section headers**: Capped at 11px — `.nx-section-title`, `.panel-section-label` overrides prevent font drift
3. **Log output**: Always forensic — `var(--nds-mono)` at 10px
4. **Tab labels**: Always op-label — `var(--z42-type-op-label)` via `.nx-tab` override
5. **Toasts**: Title at meta (11px/600), message at forensic (10px) — compact stack discipline

---

### Removed Anti-Patterns

- **Oversized labels**: Phase 4–9 used `font-size: 0.72rem–0.82rem` inconsistently across section labels — now standardized
- **Inconsistent text density**: Mix of px and rem units across phase files — Z42 uses px exclusively for predictability
- **Visual shouting**: Some phase labels were 0.9rem uppercase — reduced to 11px cap
- **Random letter-spacing**: Values ranged from `0.04em` to `0.09em` — standardized to `0.07em` for section labels

---

### Remaining Typography Issues

- **`base.css` font-size declarations** on `body` (13px) are correct but older phase files still use `rem` relative values. Full normalization would require touching 20+ phase files — deferred.
- **Monaco editor** typography is not governed by Z42 tokens (intentional — editor fonts are user-controlled).

### Production Readiness Verdict

> **PASS** — Coherent operational typography language defined and applied to all primary UI surfaces. Section labels, field labels, tab labels, and log output are now consistent. Display hierarchy is clear and free of visual shouting.
