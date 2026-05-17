# Z57_ONBOARDING_MATURITY.md
Phase Z57D — Onboarding + Login Maturity Audit
Date: 2026-05-17

## Objective
Refine the first-user experience so that signing in and the first workspace
view feels premium, calm, and production-minded. No SaaS gradients, no neon.

---

## Auth Screen Audit

### Current Auth Architecture
- Gate element: `#nx-auth-gate` — full-screen overlay, z-index 10000
- Card: `.nx-auth-card` — centered card, styled by `nx-z52.css` (Phase Z52A)
- Logo: `.nx-auth-logo-icon` (animated SVG star) + `.nx-auth-logo-text`
- Tabs: Sign In / Create Account (`.nx-auth-tabs` / `.nx-auth-tab`)
- Fields: `.nx-auth-field label + input` pattern
- Submit: button with class `nx-auth-submit`
- OAuth: Google / GitHub buttons with `.nx-auth-oauth-btn`
- Footer: terms of service + privacy policy links

### Pre-Z57 Strengths
The Z52 auth card was already materially better than the original:
- Clean dark card on near-black backdrop
- Purple accent tab underline
- JetBrains Mono logo text
- Reasonable field spacing

### Pre-Z57 Weaknesses

| Issue | Severity |
|---|---|
| Backdrop was flat `#060810` — no depth gradient | MEDIUM |
| Auth card `box-shadow` was subtle, card felt flat | MEDIUM |
| Submit button was a plain off-white pill — too generic | MEDIUM |
| Input focus ring was thin and low-contrast | LOW |
| OAuth buttons had no hover differentiation | LOW |

---

## Z57D Improvements

### Backdrop
```css
#nx-auth-gate {
  background: radial-gradient(ellipse 80% 60% at 50% 0%,
    rgba(20,20,35,1) 0%, var(--z57-bg) 70%) !important;
}
```
A subtle radial gradient emanating from the top center creates depth and warmth
without being a flashy SaaS hero gradient. It reads as depth, not marketing.

### Auth Card Depth
```css
.nx-auth-card {
  box-shadow:
    0 0 0 1px rgba(255,255,255,0.06),   /* hairline border */
    0 24px 56px rgba(0,0,0,0.50),       /* primary depth shadow */
    0 4px 12px rgba(0,0,0,0.30) !important; /* close ambient */
  background: var(--z57-surface-1) !important;
}
```
The three-layer shadow creates genuine physical depth. The `surface-1` background
(`#111116`) is slightly warmer than the previous `#0c1018`.

### Input Focus State
```css
.nx-auth-field input:focus {
  border-color: rgba(188,140,255,0.30) !important;
  box-shadow: 0 0 0 3px rgba(188,140,255,0.07) !important;
}
```
Subtle purple ring. Enough to communicate focus without being loud.

### Submit Button
```css
button.nx-auth-submit {
  background: rgba(188,140,255,0.12) !important;
  border: 1px solid rgba(188,140,255,0.22) !important;
  color: rgba(255,255,255,0.88) !important;
  font-weight: 600 !important;
}
```
The button now reads as a primary action using the brand accent, rather than a
generic white pill. Hover state adds glow: `box-shadow: 0 2px 12px rgba(188,140,255,0.12)`.

### OAuth Buttons
Subtle `background: rgba(255,255,255,0.025)` base with hover brightening.
No longer flat invisible rectangles.

---

## Onboarding Panel (NxOnboard)

The first-time onboarding panel (`#ndsOnboard`, managed by `nx-onboard.js`) was
not directly restyled in Z57 because:
1. It uses a separate `nds-onboard-card` class system in `nx-z42.css`
2. The panel is only shown once per user device (localStorage gated)
3. Functional and structurally sound — does not need redesign

The onboarding panel is flagged for a warmth pass in Z58 if user testing shows
cold reception on first use.

---

## Remaining Weaknesses

1. **No loading state between auth submit and workspace open** — After clicking
   Sign In, there is a brief white flash / blank period before the workspace mounts.
   A proper loading overlay transition is missing.

2. **"Sign In" button text is generic** — Could be "Continue to Workspace" to
   communicate purpose and reduce cognitive friction.

3. **No password strength indicator on Create Account** — Standard trust signal
   for new account creation flows.

4. **Form is not wrapped in a `<form>` element** — Confirmed by browser `[DOM]
   Password field is not contained in a form` warning. This breaks native browser
   password manager integration. A structural fix is required (out of scope for
   pure visual pass).

---

## Beta Maturity Score — Onboarding + Login

| Dimension | Score (1–10) | Notes |
|---|---|---|
| Visual warmth | 7/10 | Radial backdrop + depth shadows now present |
| Brand consistency | 8/10 | Purple accent threaded through all interactive states |
| Credibility signals | 6/10 | Good layout; form-tag issue undermines password manager trust |
| Onboarding flow | 5/10 | First-run panel exists but not tested for reception |
| Transition quality | 5/10 | Auth → workspace transition still jarring |
| **Overall** | **6.2/10** | Substantially improved from 4.5 pre-Z57 |

---

## Files Modified
- `static/css/nx-z57.css` — Z57D section: auth gate backdrop, card depth, focus ring, submit button, OAuth buttons
