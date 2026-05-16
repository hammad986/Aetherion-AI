# NEXORA_ARCHIVE_INDEX.md
# Phase Y — Part 5: Archive Index
# Generated: 2026-05-15

---

## PHASE Y ARCHIVE OPERATIONS LOG

All moves are reversible. Nothing was permanently deleted.

---

## MOVED: Root → /tools/

These were test/tooling scripts incorrectly located at project root.
They are not imported by any runtime module.

| File | Destination | Reason |
|------|-------------|--------|
| `nx_stress_harness.py` | `tools/nx_stress_harness.py` | Stress test harness |
| `infra_smoke_test.py` | `tools/infra_smoke_test.py` | Infrastructure smoke test |
| `security_adversarial_test.py` | `tools/security_adversarial_test.py` | Security adversarial tests |

---

## MOVED: Root → /docs/

Documentation files moved to /docs/ to reduce root directory clutter.

| File | Destination | Reason |
|------|-------------|--------|
| `BENCHMARK_SUITE.md` | `docs/BENCHMARK_SUITE.md` | Benchmark documentation |
| `TECHNICAL_AUDIT.md` | `docs/TECHNICAL_AUDIT.md` | Technical audit report |
| `EXECUTIVE_SUMMARY.md` | `docs/EXECUTIVE_SUMMARY.md` | Executive summary |
| `OPERATOR_GUIDE.md` | `docs/OPERATOR_GUIDE.md` | Operations guide |
| `DEPLOYMENT_VPS.md` | `docs/DEPLOYMENT_VPS.md` | VPS deployment guide |
| `Nexora_AI_Platform_Documentation.docx` | `docs/Nexora_AI_Platform_Documentation.docx` | Platform documentation |

---

## ARCHIVED: /archive/css_orphans/

CSS files confirmed to have no references in index.html, JS, or other CSS files.

| File | Destination | Evidence |
|------|-------------|---------|
| `static/css/nx-workspace-tokens.css` | `archive/css_orphans/nx-workspace-tokens.css` | No references found in templates/, static/js/, or static/css/ |

---

## RETAINED: /archive/ (pre-existing — 66 files)

The `/archive/` directory was already correctly populated from previous engineering
phases. All 66 files are injection scripts, audit scripts, and operational tooling
from Phases G through Z. They are correctly placed and require no further action.

**Content classification:** ARCHIVE_ONLY — these were the shell reconstruction
and injection scripts used during Nexora frontend reconstruction phases.

---

## NOT MOVED / PRESERVED

| Item | Reason |
|------|--------|
| `sessions.db`, `memory.db`, `saas_platform.db` | Live production data |
| `billing.db`, `feedback.db`, `scheduler.db`, `support.db` | Live production data |
| `.env` | Production secrets |
| `checkpoint.json`, `memory.json` | Active runtime state |
| `app.log`, `agent.log` | Active logs |
| `workspace/` | Active agent workspaces |
| `uploads/` | User file uploads |
| `snapshots/` | Session snapshots |
| `workspaces/` | Agent workspace data |
| `.replit`, `replit.md` | Kept (Replit deployment still possible) |
| `package.json`, `package-lock.json` | Kept (Node tooling may be needed) |
| `playwright.config.js` | Kept (E2E test config) |
| `nx-shell.css` | Kept — confirmed referenced by index.html and JS files |

---

## ROLLBACK PROCEDURE

To reverse any archive operation:

```bash
# Restore from tools/ to root
cp tools/nx_stress_harness.py .
cp tools/infra_smoke_test.py .
cp tools/security_adversarial_test.py .

# Restore docs
cp docs/BENCHMARK_SUITE.md .
# etc.

# Restore orphaned CSS
cp archive/css_orphans/nx-workspace-tokens.css static/css/
```

---

## IMPACT ASSESSMENT

| Category | Files Moved | Runtime Impact |
|----------|------------|----------------|
| Root tooling → /tools/ | 3 files | ZERO — not imported by any runtime module |
| Root docs → /docs/ | 6 files | ZERO — documentation only |
| CSS orphan → /archive/ | 1 file | ZERO — not referenced in index.html |
| **Total** | **10 files** | **ZERO runtime impact** |
