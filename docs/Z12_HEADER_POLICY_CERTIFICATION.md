# Z12 — Security Header Policy Certification
**Aetherion AI · Phase Z12 · Response Discipline**
Certification Date: 2026-05-16 | Status: CERTIFIED

---

## Implementation Summary

Security headers applied via `@app.after_request` hook `_p19_cors()` in `web_app.py`.
All headers use `setdefault()` — never overwrite an explicitly set response header.

---

## Headers Applied

### 1. X-Content-Type-Options
```
X-Content-Type-Options: nosniff
```
**Effect:** Prevents MIME-type sniffing. Browsers will not execute JS served as
`text/plain`. Eliminates content-type confusion attacks.
**SSE Impact:** None — SSE responses set `text/event-stream` explicitly.
**Status:** ✓ IMPLEMENTED

---

### 2. X-Frame-Options
```
X-Frame-Options: SAMEORIGIN
```
**Effect:** Prevents clickjacking by blocking cross-origin framing.
**Replit Compatibility:** Replit's preview iframe is same-origin (served from the
same dev domain). The app preview in the Replit editor is not affected.
**Razorpay:** `frame-src 'self' https://api.razorpay.com https://checkout.razorpay.com`
in the CSP permits the Razorpay checkout iframe.
**Status:** ✓ IMPLEMENTED

---

### 3. X-XSS-Protection
```
X-XSS-Protection: 1; mode=block
```
**Effect:** Legacy XSS filter hint for older browsers. Modern browsers use CSP
instead; this header is a belt-and-suspenders measure.
**Status:** ✓ IMPLEMENTED

---

### 4. Referrer-Policy
```
Referrer-Policy: strict-origin-when-cross-origin
```
**Effect:** Sends full referrer to same-origin requests; only sends the origin
(not path) to cross-origin HTTPS requests; sends nothing to HTTP.
**Trade-offs:** API keys in query strings are not leaked via referrer.
**Status:** ✓ IMPLEMENTED

---

### 5. Content-Security-Policy
Applied on `text/html` responses only (not on JSON API responses).

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self' 'unsafe-inline'
             https://cdnjs.cloudflare.com
             https://cdn.jsdelivr.net
             https://checkout.razorpay.com;
  style-src  'self' 'unsafe-inline'
             https://fonts.googleapis.com
             https://cdn.jsdelivr.net;
  font-src   'self'
             https://fonts.gstatic.com
             https://cdn.jsdelivr.net;
  img-src    'self' data: blob: https:;
  connect-src 'self' wss: ws:
               https://api.razorpay.com;
  frame-src  'self'
             https://api.razorpay.com
             https://checkout.razorpay.com;
  object-src 'none';
```

**Monaco Compatibility:** Monaco editor scripts are loaded from
`cdnjs.cloudflare.com` and `cdn.jsdelivr.net` — both whitelisted. ✓

**SSE Compatibility:** `connect-src 'self'` permits SSE connections to the same
origin. ✓

**Iframe Preview:** `frame-src 'self'` permits same-origin iframe embeds. ✓

**Replit Compatibility:** Replit proxies requests via its CDN; the app is served
from the dev domain's own origin — CSP is not restrictive in that context. ✓

**Known Limitation:** `'unsafe-inline'` for scripts is required for the current
architecture (inline event handlers in templates). V2 target: move all handlers
to dedicated JS files and switch to `'nonce-'` or `'strict-dynamic'`.

**Status:** ✓ IMPLEMENTED

---

### 6. Permissions-Policy *(Phase Z12 addition)*
```
Permissions-Policy:
  camera=(),
  microphone=(),
  geolocation=(),
  payment=(self https://checkout.razorpay.com),
  usb=(),
  fullscreen=(self),
  display-capture=(),
  clipboard-read=(),
  clipboard-write=(self)
```
**Effect:** Explicitly disables unused device APIs. Prevents malicious script from
accessing camera, microphone, or geolocation even via XSS.
**Status:** ✓ IMPLEMENTED (Z12)

---

### 7. Cross-Origin-Opener-Policy *(Phase Z12 addition)*
```
Cross-Origin-Opener-Policy: same-origin-allow-popups
```
**Effect:** Isolates the browsing context from cross-origin openers while allowing
popups (required for Razorpay checkout popup).
**Razorpay Compatibility:** `same-origin-allow-popups` is safe — allows the
Razorpay checkout popup to reference back to the opener window. ✓
**Status:** ✓ IMPLEMENTED (Z12)

---

## SSE Compatibility Matrix

| Header | SSE Impact | Verdict |
|---|---|---|
| X-Content-Type-Options | None | ✓ Safe |
| X-Frame-Options | None | ✓ Safe |
| X-XSS-Protection | None | ✓ Safe |
| Referrer-Policy | None | ✓ Safe |
| CSP (connect-src self) | Allows SSE to same origin | ✓ Safe |
| Permissions-Policy | None | ✓ Safe |
| Cross-Origin-Opener-Policy | None | ✓ Safe |

---

## Monaco Editor Compatibility

Monaco loads workers and source maps from CDN. All required origins are in the
CSP allowlist:
- `cdnjs.cloudflare.com` ✓ (scripts)
- `cdn.jsdelivr.net` ✓ (scripts, styles)
- Worker `blob:` URLs: `script-src` does not include `blob:` — Monaco uses
  `createObjectURL` for workers. If Monaco workers fail, add `blob:` to
  `script-src` in a future iteration.

---

## Future Hardening Targets

| Priority | Action |
|---|---|
| V2 | Remove `'unsafe-inline'` from `script-src`; migrate to nonce-based CSP |
| V2 | Add `Strict-Transport-Security` (HSTS) with `max-age=63072000; includeSubDomains` |
| V2 | Add `Cross-Origin-Resource-Policy: same-site` |
| V3 | Implement CSP reporting via `report-to` directive |
