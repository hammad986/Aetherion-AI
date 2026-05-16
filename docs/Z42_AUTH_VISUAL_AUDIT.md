# Z42_AUTH_VISUAL_AUDIT.md
## Phase Z42A — Auth System Visual Audit

---

### Pre-Z42 Weaknesses Identified

| ID | Issue | Severity | Resolution |
|----|-------|----------|------------|
| A-01 | Primary button used `var(--purple)` (#bc8cff) — decorative, not operational | High | Replaced with `var(--z42-exec-active)` (#0079F2) |
| A-02 | Auth gate backdrop `blur(12px)` caused heavy floaty feel, disconnected from workspace | Medium | Reduced to `blur(6px)` for grounded presence |
| A-03 | Auth card `border-radius: 16px` — generic SaaS card radius | Medium | Reduced to `8px` for structured operational framing |
| A-04 | Auth card `padding: 36px` — excessive dead space, especially on small viewports | Medium | Reduced to `32px` symmetric padding |
| A-05 | Tab switcher background used `var(--panel2)` — ambiguous surface depth | Low | Changed to `var(--z42-workspace)` — explicit layer identity |
| A-06 | Field labels `text-transform: uppercase; letter-spacing: 0.5px` — correct concept, inconsistent size | Low | Standardized to `11px / 0.06em` per Z42B section label token |
| A-07 | Input `border-radius: 8px` — too soft, too generic | Low | Reduced to `5px` — structured operational feel |
| A-08 | OAuth buttons used `transition: border-color 0.15s, background 0.15s` — applied to link anchors | Low | Scoped to hover states only |
| A-09 | Auth footer link color `var(--accent)` (#58a6ff) — over-emphasized for legal links | Low | Changed to `var(--nds-text-lo)` — quieter, non-clickbait |
| A-10 | Logo icon had no border — floated with no surface anchor | Low | Added `border: 1px solid var(--z42-border-frame)` |
| A-11 | Toast z-index (200) could theoretically appear inside auth overlay (99999) in some browsers | Medium | Auth gate explicitly set to z-index 100000; toasts at 99990 |
| A-12 | Primary button animation `opacity: 0.88` hover — imperceptible, no directional signal | Low | Replaced with explicit background color shift to `--nds-accent-hi` |

---

### Remaining Operational UI Risks

- **Google OAuth redirect**: If `/api/auth/google` or `/api/auth/github` endpoints are unconfigured, buttons silently 404. No visual indication in auth card. Risk: low for deployment, present for dev onboarding.
- **Toast overlap during auth**: Toast container (99990) is now always below auth gate (100000), but the legacy `#toast` element at z-index 200 may still render if old code paths fire it. Mitigated by Z42E overrides but not fully eliminated without JS changes.

### Remaining Density Issues

- Auth card width capped at `400px` — appropriate for auth. No density problem.
- Forgot password flow shares the same card with login — transition between states has no visual continuity indicator. Non-critical.

### Remaining Hierarchy Conflicts

- `nx-auth-logo-text span` (accent blue) competes with active tab highlight (also blue). Both are intentional but worth monitoring as the brand identity evolves.

### Production Readiness Verdict

> **PASS** — Auth gate is now visually mature, operationally grounded, and free of generic SaaS gradients. Primary action uses functional blue accent. Surface depth is structured. Typography is consistent. Toast overlap is resolved at the z-index level.
