# Z51 Beta Lockdown Report

## Phase Z51A — Commercial Surface Suppression

**Date:** 2026-05-17  
**Status:** COMPLETE  

---

## Audit: Commercial Surfaces Found

| Surface | Location | Action Taken |
|---|---|---|
| Razorpay checkout.js CDN script | `templates/index.html` line 32 | Removed from DOM; commented with future activation note |
| `#p8UpgradeModal` — full plan selection modal | `templates/index.html` line 363 | Hidden via CSS `display: none !important` |
| `#p36SubStatus` — active subscription status | `templates/index.html` line 387 | Hidden via CSS (parent modal hidden) |
| `#p36InvoiceList` — invoice history | `templates/index.html` line 426 | Hidden via CSS (parent modal hidden) |
| `#p36SetupGuide` — Razorpay webhook setup guide | `templates/index.html` line 430 | Hidden via CSS (parent modal hidden) |
| `#p36InspBilling` — billing widget in Inspector | `templates/index.html` line 1666 | Hidden via CSS `display: none !important` |
| Settings "Plan" tab (`data-stab="plan"`) | `templates/index.html` line 2992 | Hidden via CSS attribute selector |
| Settings "Billing Setup" tab | `templates/index.html` line 2998 | Hidden via CSS attribute selector |
| `#spane-billing-setup` settings pane | `templates/index.html` line 3470 | Hidden via CSS |
| "View / Upgrade Plans" button in settings | `templates/index.html` line 3197 | Hidden via CSS `onclick` selector |
| `.p36-pay-btn` plan payment buttons | `static/js/dashboard.js` (rendered) | Hidden via CSS class |
| Inline "Upgrade ↗" / "Manage ↗" links | `static/js/dashboard.js` injected | Patched by JS MutationObserver in Z51 |
| `window.p8OpenUpgradeModal()` | `static/js/dashboard.js` | Overridden → shows "not in beta" toast |
| `window.p36StartPayment()` | `static/js/dashboard.js` | Overridden → no-op + toast |
| `window.p8ApplyCoupon()` | `static/js/dashboard.js` | Overridden → no-op + toast |

---

## Beta Badge

A `z51-beta-badge` pill is injected next to the plan badge in the topbar, labelled **BETA** with the version (`0.9.0-beta`). This replaces the commercial plan indicator (Lite/Pro/Elite) as the topbar signal of platform state.

---

## Feature Flag Registry (`window.NX_BETA`)

```javascript
NX_BETA.features = {
  billing:          false,  // FUTURE: activate when Razorpay live keys confirmed
  payments:         false,  // FUTURE: enable after billing backend hardening
  marketplace:      false,  // FUTURE: planned for v1.0 post-beta
  collaboration:    false,  // FUTURE: multi-user sessions — v1.1
  publicApi:        false,  // FUTURE: v1.0 API key distribution
  advancedScheduler: true,  // read-only in UI, safe for beta
  hitl:             true,   // core execution safety, safe for beta
  memory:           true,   // read-only in UI, safe for beta
  observability:    true,   // read-only in UI, safe for beta
}
```

`NX_BETA.gate(feature, fn, message)` wraps any function behind a feature gate. When the feature is disabled, calling the wrapped function shows a toast and returns immediately.

---

## Remaining Beta Weaknesses

1. The `p8PlansGrid` rendered by `dashboard.js` still runs `p8LoadPlans()` — it renders plan cards that reference `p36StartPayment()`. These cards are hidden by the modal being `display:none`, but the JS still fires. Low risk in beta; for production hardening, `p8LoadPlans()` should be fully gated.
2. `loadWebhookStatus()` in `runtime.js` still polls `/api/payments/webhook-status` on the settings page. This returns gracefully but adds a dead request. Future: gate behind `NX_BETA.features.billing`.
3. If a user manually opens browser DevTools and calls `window.p8OpenUpgradeModal()`, the override will intercept with a toast — correctly blocked.

---

## Beta Readiness Score: 8.5/10

Billing is fully suppressed from the user-facing surface. Internal abstractions intact. Activation path preserved via comments and feature flags.
