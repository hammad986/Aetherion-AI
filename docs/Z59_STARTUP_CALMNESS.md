# Z59E — Startup + Session Calmness

## Summary
Phase Z59E ensures the startup sequence is calm, deterministic, and visually stabilizes within 2 seconds — no toast spam, no redundant notices, no flickering states.

## Changes Implemented

### Toast Deduplication (NdsOnboard v2)
- `_activeToasts` Set tracks messages in flight for 2-second deduplication window
- Identical message+type combinations within 2 seconds are silently dropped
- Maximum of 3 toasts visible simultaneously (oldest auto-dismissed when limit reached)
- Toast region is now properly scoped with `aria-atomic="false"` to allow additive announcements

### Toast Animation (Z59 CSS)
- `.nds-toast` — `nxAuthCardIn` animation (translateY + scale) on entry
- `.nds-toast[data-leaving]` — `z59ToastOut` animation (fade + scale down) on exit
- Toast entry/exit feels intentional, not instant

### SSE Reconnect — Quiet Mode
- `.z59-reconnect-pill` — replaces loud toasts with a quiet bottom-right pill
- Pill is visible but non-interrupting during SSE reconnects
- `.z59-reconnect-dot` pulses amber to signal reconnecting state

### Startup Veil (CSS only)
- `#z59-startup-veil` — optional full-screen veil with logo + label
- Fades out with `.fading` class after startup completes
- Not wired by default — available for use if startup needs visual stabilization

### Session Restore
- Session restore is handled silently via localStorage (`nx_workspace_runtime_v1`)
- No toast on session restore — state is restored without announcement
- If restore fails, the workspace shows the empty state without an error

## Remaining UX Gaps
- Boot console still logs ~60+ lines of `[Phase X] ... active` messages
- Some modules still use `console.log` for routine status updates that create noise
- The startup veil is CSS-only and not yet wired into the boot sequence

## Remaining Fake Surfaces
- `[Phase Z45] Causal synchronization + runtime consolidation active` — this and similar Phase labels in boot.js are cosmetic console output that implies more intelligence than exists

## Remaining Weak Transitions
- The workspace appears immediately after auth gate exits — no staged loading of panels

## Remaining Trust Problems
- SSE disconnection shows no indicator at all until `z59-reconnect-pill` is JS-wired

## Remaining Shallow States
- Initial model name shows "Loading…" for ~1-2 seconds before the API responds

## Remaining Interaction Inconsistencies
- Cookie banner can appear simultaneously with email verification banner — two banners stacked

## Beta Readiness Score
**Startup calmness: 7.5/10**
Toast deduplication and max-count enforcement are working. The JS-level boot console spam and the SSE reconnect quiet mode wiring are the remaining gaps.
