# Z52 Auth Experience Refinement Report

## Phase Z52A — Login / Signup Visual Redesign

**Date:** 2026-05-17  
**Status:** COMPLETE  

---

## Problem Diagnosis

The previous auth experience suffered from five identifiable categories of failure:

### 1. Tonal Deadness
The backdrop (`rgba(13,17,23,0.97)`) and card (`#161b22`) were visually indistinguishable at typical monitor brightness. Both were dark grey. The card floated on nothing — no apparent elevation, no depth hierarchy. The blur backdrop was applied but created no useful visual separation.

### 2. Generic SaaS Aesthetics
- Purple CTA button (`#bc8cff`) — identical to hundreds of dark-mode SaaS templates
- Blue segmented tab control — identical to GitHub/GitLab/generic dark UI components
- ALL-CAPS field labels with 0.5px letter-spacing — Bootstrap 4 aesthetic
- No product identity below the logo mark

### 3. Interaction Flatness
- Hover on primary button: `opacity: 0.88` — barely perceptible
- Focus state: thin border colour change only — no spatial feedback
- Tab switch: instant fill colour change — no transition feel
- OAuth buttons: same visual weight as primary button — wrong hierarchy

### 4. No Identity Statement
The auth card read: `[star icon] Nexora AI`. That was the entire product context. No indication of what Nexora is, what the user is signing into, or why it matters.

### 5. Error / Success Banners
Symmetrical rounded corners on error messages — looked like info cards, not errors. No clear severity signalling.

---

## Changes Applied

### Backdrop
`#060810` background with a subtle radial gradient (`rgba(88,130,255,0.06)`) at top-center. Creates genuine depth: the viewport is darker at edges, lighter behind the card. Not a gradient — a subtle glow that establishes context.

### Card
- Background: `#0c1018` — distinct from backdrop without being jarring
- Border: `rgba(255,255,255,0.07)` — barely-there but present
- Shadow: three-layer (ambient + elevation + inner glow highlight)
- Inner glow: `inset 0 1px 0 rgba(255,255,255,0.04)` — the top edge catches ambient light, creating a premium "raised slab" effect

### Logo
- Icon container: `rgba(188,140,255,0.07)` background with `0.14` border — logo glows softly against a transparent field
- Company name: `#d1d9e0` (not pure white — avoids harshness)
- "AI" suffix: `#b18aff` (slightly warmer purple, better harmony)

### Product Tagline (injected by z52.js)
`"Autonomous AI development workspace"` — 11.5px, muted. Sets context immediately. Users know what they're signing into.

### Tabs — Underline Style
Replaced pill/background style with Linear-style underline tabs:
- Container: no background, bottom border line
- Active: `border-bottom: 2px solid #58a6ff`, full-weight text
- Inactive: `rgba(139,148,158,0.6)`, medium weight
- No background fill on either state — cleaner, more focused

### Field Labels
- Sentence case (patched by z52.js)
- `font-weight: 500` not 600 — less aggressive
- No `text-transform: uppercase` — removed entirely
- `rgba(139,148,158,0.8)` — readable, not dominant

### Inputs
- Background: `rgba(255,255,255,0.025)` — barely tinted, not a grey box
- Border: `rgba(255,255,255,0.08)` — present but unobtrusive
- Focus: `border-color: rgba(88,166,255,0.45)` + `box-shadow: 0 0 0 3px rgba(88,166,255,0.09)` — spatial, like Claude/Notion

### Primary Button — White-on-Dark
`background: #d1d9e0; color: #0a0d12; font-weight: 600`

This is the highest-contrast UI element on the page. It immediately reads as "the action to take." No gradients, no glow, no purple. Confidence through contrast.

Hover: slightly brighter (`#e6edf3`). Active: slightly darker + `scale(0.993)`.

### Error / Success Banners
Left-side accent border (3px) instead of symmetric borders. Immediately reads as "directional information" vs "card content." Error uses `rgba(248,81,73,0.06)` background — present but not aggressive.

### OAuth Buttons
`background: transparent` — visually subordinate to primary button. Only border is present. The Google/GitHub logos provide all necessary visual weight.

---

## Remaining Weaknesses

1. **No loading state on Sign In button** — after click, button remains labeled "Sign In" while the fetch runs. A spinner or loading state would reduce perceived latency. Currently handled by `#nx-auth-err` showing on failure only.
2. **Password strength indicator** — absent on signup form. Users have no feedback until they submit with a weak password.
3. **No "remember me" option** — the platform auto-stores the refresh token cookie, but there's no UI control for this. Low friction for beta.
4. **Mobile viewport** — card padding on small screens (< 400px wide) still uses `40px` horizontal padding, which creates cramped fields. Needs responsive override.
5. **Card animation duration** — `0.25s` is appropriate but the `cubic-bezier(0.16,1,0.3,1)` (spring) may feel slightly bouncy on older hardware. Acceptable for now.

---

## Before / After

| Property | Before | After |
|---|---|---|
| Backdrop | `rgba(13,17,23,0.97)` flat | `#060810` + radial depth gradient |
| Card bg | `#161b22` | `#0c1018` |
| Card border | `#30363d` (20% grey) | `rgba(255,255,255,0.07)` (subtle) |
| Logo icon bg | `#0d1117` flat | `rgba(188,140,255,0.07)` glow |
| Product tagline | None | "Autonomous AI development workspace" |
| Tabs | Blue pill background | Underline, no background |
| Field labels | ALL-CAPS, 0.5px spacing | Sentence case, no spacing |
| Input focus | Border colour only | Border + box-shadow glow |
| Primary button | Purple `#bc8cff` | White `#d1d9e0` on dark |
| Error banner | Symmetric rounded | Left-border accent (3px) |
| OAuth buttons | Filled background | Transparent + border only |

---

## Auth Maturity Score: 8/10

The auth experience now reads as focused, premium, and operationally trustworthy. It is meaningfully closer to Linear/Claude/OpenAI calibre. The remaining gaps (loading state, password strength, mobile responsive) are v1.0 items.
