# NEXORA_SAFE_CLEANUP_PLAN.md
# Phase Y — Safe Cleanup Plan
# Generated: 2026-05-15

> [!IMPORTANT]
> DO NOT execute any moves/deletes until code changes in Parts 2–4 are validated.
> All moves are to /archive/ (already exists) — nothing is deleted permanently.

---

## PHASE Y.5 — CLEANUP EXECUTION PLAN

### STEP 1: Archive Operational Tooling from /archive/ subdirectory
All 66 files in `/archive/` are already archived. They are ARCHIVE_ONLY.
No additional action needed — they are correctly placed.

### STEP 2: Move Root-Level Tooling to /tools/
These files exist at project root and are NOT runtime imports:

| File | Current Location | Move To | Reason |
|------|-----------------|---------|--------|
| `nx_stress_harness.py` | root | `/tools/` | Stress test harness — not runtime |
| `infra_smoke_test.py` | root | `/tools/` | Smoke test — not runtime |
| `security_adversarial_test.py` | root | `/tools/` | Security test — not runtime |
| `BENCHMARK_SUITE.md` | root | `/docs/` | Documentation |
| `TECHNICAL_AUDIT.md` | root | `/docs/` | Documentation |
| `EXECUTIVE_SUMMARY.md` | root | `/docs/` | Documentation |
| `OPERATOR_GUIDE.md` | root | `/docs/` | Documentation |
| `DEPLOYMENT_VPS.md` | root | `/docs/` | Documentation |

### STEP 3: Verify CSS orphans before action
Before any action on:
- `static/css/nx-shell.css`
- `static/css/nx-workspace-tokens.css`

Run: `grep -r "nx-shell\|nx-workspace-tokens" templates/ static/js/ static/css/`
If found in `@import` or JS dynamic injection → keep.
If not found anywhere → safe to archive.

### STEP 4: Log file management
- `app.log` (4.1MB) — active log file, NEVER delete, but rotate
- `agent.log` (23KB) — active log, NEVER delete

### STEP 5: `.replit` file
- `replit.md` and `.replit` — Replit deployment artifacts
- Safe to move to `/archive/replit_artifacts/` if deploying on VPS/Docker only
- KEEP if Replit deployment is still used

---

## WHAT NOT TO TOUCH

| File/Dir | Reason |
|----------|--------|
| `sessions.db` | Live production data |
| `memory.db` | Live agent memory |
| `saas_platform.db` | Live user/auth data |
| `billing.db` | Live billing data |
| `feedback.db` | Live feedback data |
| `scheduler.db` | Live scheduled tasks |
| `support.db` | Live support tickets |
| `checkpoint.json` | Session checkpoint |
| `memory.json` | Memory state |
| `.env` | Production secrets |
| `workspace/` | Agent workspaces |
| `uploads/` | User uploads |
| `snapshots/` | Session snapshots |

---

## RISK ASSESSMENT

| Risk | Level | Mitigation |
|------|-------|-----------|
| Accidental deletion of runtime file | CRITICAL | Only move to /archive/, never delete |
| Breaking a blueprint import | HIGH | Validate after each move |
| Log rotation causing data loss | MEDIUM | Rotate with logrotate, not delete |
| CSS/JS missing from page | HIGH | Verify index.html references before archiving |

---

## EXECUTION ORDER

1. ✅ Generate all reports (this file + NEXORA_DEPENDENCY_MAP.md)
2. ✅ Fix critical bugs (WAL bypass, duplicate blueprint)
3. ✅ Harden SSE bridge
4. ✅ Harden SQLite concurrency
5. ⏳ Verify CSS orphans (manual check needed)
6. ⏳ Move tooling files to /tools/ and /docs/
7. ⏳ Generate NEXORA_ARCHIVE_INDEX.md after moves
8. ⏳ Run NEXORA_STABILITY_CERTIFICATION.md validation

---

## DEPRECATED_SAFE FILES (Confirmed by import analysis)

These files have NO import references in any active runtime module:

| File | Evidence |
|------|---------|
| `checkpoint.json` | JSON data file, not imported |
| `Nexora_AI_Platform_Documentation.docx` | Binary doc, not runtime |
| `replit.md` | Replit deployment doc |
| `.replit` | Replit config (only needed on Replit) |
| `package.json` / `package-lock.json` | Node.js manifest (if no Node build step) |
| `playwright.config.js` | Test config, not runtime |
