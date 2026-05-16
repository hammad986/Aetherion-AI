# Z42_RUNTIME_FEEDBACK_AUDIT.md
## Phase Z42E — Toast + Runtime Feedback Discipline Audit

---

### Feedback Surface Inventory

| Surface | Implementation | Z-Index | Governed by Z42? |
|---------|---------------|---------|-----------------|
| `#nx-toasts` container | `stability.js` `nxToast()` | 99990 | ✅ Yes |
| Legacy `#toast` element | `base.css` `.toast` | 200 → 99990 | ✅ Yes (override) |
| `#nx-verify-banner` | `layout.css` | 99991 | ✅ Yes |
| `#nx-auth-err` inline error | Auth card | N/A (in-flow) | ✅ Yes |
| `#nx-auth-ok` success | Auth card | N/A (in-flow) | ✅ Yes |
| `#nx-cookie-banner` | `layout.css` | Varies | ✅ Styled by Z42F |

---

### Z42E Discipline Enforced

#### 1. Stack Discipline
- `#nx-toasts` uses `flex-direction: column` with `gap: 6px` — toasts stack vertically without overlap
- `align-items: flex-end` — right-anchored, clear of left panels
- `pointer-events: none` on container, `auto` on individual toasts — prevents click-through blocking

#### 2. Severity Hierarchy
```
info    → left border: --z42-exec-active (#0079F2)  — blue
success → left border: --z42-exec-done   (#16A34A)  — green
warning → left border: --z42-exec-warn   (#C28A00)  — amber
error   → left border: --z42-exec-err    (#C0392B)  — red
```

#### 3. Compact Layout
- Toast width: `320px` — readable without dominating the workspace
- Title: `11px / 600` — scannable at a glance
- Message: `10px / 400 mono` — forensic detail, not primary read
- Padding: `8px 10px` — dense, not spacious

#### 4. Timeout Governance
- Defined in `nds-tokens.css`:
  - Info: `4000ms`
  - Warning: `5000ms`
  - Error: `7000ms`
- Z42 does not change timeout values — they are already appropriately calibrated

#### 5. Overlap Prevention
| Risk scenario | Before Z42 | After Z42 |
|---------------|-----------|-----------|
| Toast overlaps auth gate | Possible (z-200 vs z-99999 — auth wins but toast may appear at edge) | Auth at z-100000, toasts at z-99990 — always below auth |
| Toast overlaps runtime inspector | Inspector at z-30, toast at z-99990 — toast wins (correct) | No change needed — correct behavior |
| Multiple toasts overlapping each other | Single `#toast` element — only one at a time | `#nx-toasts` container stacks — no overlap |
| Toast overlaps execution controls | Controls at z-50, toast at z-99990 | Correct — toast appears above for visibility |

---

### Remaining Feedback Risks

- **Dual toast systems**: Both `#toast` (legacy, single-element) and `#nx-toasts` (modern, stacked) exist. Both are styled by Z42 but they can fire simultaneously from different code paths, appearing in the same screen position. Requires JS consolidation to fully resolve.
- **`showToast()` vs `nxToast()`**: Multiple JS shims exist. Some code paths call `showToast()` (which uses `#toast`), others call `nxToast()` (which uses `#nx-toasts`). Z42 styles both but cannot guarantee which fires when.

### Production Readiness Verdict

> **PASS with caveat** — Toast stack is disciplined, severity-driven, and positioned clear of auth/inspector surfaces. The dual-system legacy issue is documented and styled consistently, but full consolidation requires a JS unification pass.
