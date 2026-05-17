# Z60E — Workspace Realism Report

**Date:** Phase Z60  
**Scope:** Workspace center emotional presence, empty state, operational clarity

---

## 1. Current State Assessment

### Idle Hero (`#nxIdleHero`)

The primary workspace surface when no task is running contains:

**Header row:**  
Icon + "Workspace ready" title + keyboard hint (`⌘K` / `⌘↵`) — functional, visible, relevant.

**Runtime status strip:**  
4 metrics — Model, Confidence, Context, Queued — populated by `nx-z50.js` at runtime. Shows "—" on fresh load, which is an honest empty state. Values fill in once a provider is configured.

**Quick action chips:**  
- Run Tests
- Audit Workspace  
- Generate Docs
- Security Review

These call `nxSetTask()` and inject text directly into the task input. They are real, functional shortcuts, not decorative.

**Recent runs list:**  
Shows "No recent runs" on first use — accurate empty state. Populates from `nxIdleRecent` as sessions accumulate.

**Z33 Replay resume card:**  
Hidden by default, shows when a resumable forensic session exists. Honest conditional surface.

---

## 2. What Was Fake Before

The workspace center was never critically fake — it did not contain dashboards with hardcoded metrics or placeholder charts. The main issue was **emotional emptiness**: the "—" values in the status strip on first load gave a feeling of incompleteness.

This is an honest representation of state: until a provider is configured, there is no model to display. The fix is user action (configuring a provider), not UI theater.

---

## 3. What Was Improved

- **Onboarding panel readiness text** changed from "Runtime ready — all systems operational" to "Ready" — the previous text was performative fake confidence, not a real system check.
- **Restore toast** changed from "Workspace restored / N systems synced" to "Session resumed" — eliminates fake "systems synced" claim.

---

## 4. Remaining Hollow UX

| Area | Issue | Recommendation |
|------|-------|----------------|
| Status strip on cold start | Shows "—" for all fields | Acceptable honest empty state |
| Recent runs on first visit | "No recent runs" text | Acceptable honest empty state |
| Quick actions not customizable | Fixed set of 4 chips | Low priority |

---

## 5. What the Workspace Now Feels Like

- **Focused:** Single composer input is the primary interaction
- **Honest:** Empty states reflect real state, not placeholders
- **Calm:** No startup toasts, no blinking banners, no fake progress
- **Operational:** Quick actions provide immediate value on first load
- **Trustworthy:** Status strip shows real values when connected

---

## Beta Readiness Score: 7/10

The workspace is functional and honest. The primary gap is that a fresh user sees "—" across all status fields until they configure a provider — which is a real configuration requirement, not a UI bug. Future improvement: add a single "Configure a provider to get started" prompt when no providers are active.
