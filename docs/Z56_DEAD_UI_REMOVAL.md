# Z56 — Dead / Fake UI Removal Report
**Phase**: Z56 Interaction Stabilization + Trust Recovery  
**Date**: 2026-05-17  
**Scope**: All UI elements that render but perform no real action

---

## Definition

"Dead UI" in this context means any rendered interactive element (button, select, link, toggle) that:
- Has no event handler, OR  
- Has a handler that immediately returns / shows a "not available" toast without doing anything, OR  
- References a backend API endpoint that returns 404 or is not implemented

---

## Findings

### 1. Mode / Scope `<select>` — RESOLVED

**Before Z56**: Both selects had no `id`, no option `value` attributes. While `z50WireExecSelects()` did wire `change` handlers, the missing IDs made reliable targeting fragile, and the missing `value` attributes meant the saved localStorage key was based on option text only.

**After Z56**: IDs `nxExecModeSelect` / `nxExecScopeSelect` added. Option values added. `title` tooltips added. Wiring confirmed correct — selections persist to `localStorage` and reflect into `window.NX.execMode` / `window.NX.execScope`.

**Trust status**: Live.

---

### 2. `/api/scheduler/stats` — RESOLVED

**Before Z56**: `nx-z50.js` `z50UpdateIdleStats()` fetched `/api/scheduler/stats` every 12 seconds. The endpoint did not exist, returning HTTP 404 on every poll. The server access log filled with 404 entries. The idle dashboard showed `—` for the Scheduler field with no error surfaced to the user.

**After Z56**: Route `GET /api/scheduler/stats` added to `web_app.py`. Returns `{ total_enabled, total, running }` using the existing `_scheduler` object. Idle dashboard now shows real scheduler task count.

**Trust status**: Live.

---

### 3. Cookie Banner Duplicate Handler — RESOLVED

**Before Z56**: `window.nxAcceptCookies` was defined in two files:
1. `session.js` (early, used key `nx_cookie_ok`)
2. `nx-z50.js` (late, used key `nx_cookie_accepted`, added CSS animation)

Since scripts load in order, `nx-z50.js` always overwrote session.js's version. However, `session.js`'s init code checked `nx_cookie_ok` to decide whether to show the banner, while `nx-z50.js` init checked `nx_cookie_accepted`. A user who had dismissed via session.js would see the banner again after a reload in some race conditions.

**After Z56**:  
- `session.js` definition removed. `nx-z50.js` is the sole owner.  
- `z50InitCookieBanner()` now checks BOTH keys (`nx_cookie_accepted` OR `nx_cookie_ok`) — backward-compatible with existing users.  
- `z50DismissCookieBanner()` now writes BOTH keys on accept — forward-compatible.

**Trust status**: Single source of truth.

---

### 4. Beta-Blocked UI Elements (Payments, Coupons)

**Status**: Intentionally deferred.  
`nx-z51.js` gates payment and coupon buttons behind beta-lock toast messages. These are not "dead" — they are intentionally locked features with clear user messaging. No action taken.

---

### 5. Remaining Audit: Inline onclick Stubs

A grep for `onclick="return false"`, `onclick="void 0"`, `href="#"` patterns returned 0 results in interactive elements that were not within tab buttons or anchor-styled divs with real handlers.

---

## Summary

| Issue | File(s) | Action |
|---|---|---|
| Mode/Scope selects missing IDs+values | `index.html` | Fixed |
| `/api/scheduler/stats` 404 | `web_app.py` | Stub endpoint added |
| Duplicate `nxAcceptCookies` | `session.js`, `nx-z50.js` | Deduplicated, harmonized |
| Beta-locked payments/coupons | `nx-z51.js` | Intentional, no action |
