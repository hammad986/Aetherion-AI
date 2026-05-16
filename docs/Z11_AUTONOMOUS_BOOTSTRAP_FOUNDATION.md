# Z11 — Autonomous Bootstrap Intelligence Foundation
**Aetherion AI · Phase Z11 · Future Architecture Blueprint**
Status: DESIGN ONLY — No implementation. Future V2/V3 target.

---

## Overview

This document defines the future architecture for Aetherion AI to evolve into
a fully autonomous, environment-aware execution platform capable of ingesting
arbitrary repositories, inferring project structure, resolving dependencies, and
bootstrapping itself to a running state without human intervention.

**This is a forward-looking design contract.** Nothing described here is
implemented in V1. Engineers adding new capabilities in V2/V3 should treat this
document as the authoritative integration surface map.

---

## 1. GitHub Repository Ingestion

### Surface
`POST /api/bootstrap/ingest` — accepts a GitHub URL or tarball.

### Pipeline (future)
```
URL → Clone (shallow, depth=1) → Workspace staging area
    → File tree scan (recursive, ignore: node_modules, .git, __pycache__)
    → Binary detection (skip non-text assets)
    → Raw file index written to bootstrap_context.db
    → Ingestion event published to SSE: "bootstrap.ingested"
```

### Design Constraints
- Workspace isolation: each ingestion gets its own UUID-scoped directory.
- Size limit: 500 MB soft cap; 2 GB hard cap.
- Private repos: require GITHUB_TOKEN injection via Secret Vault.
- Rate limiting: max 3 concurrent ingestions per tenant.

---

## 2. Project Structure Inference

### Detection Heuristics (priority order)
1. Manifest file detection: `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`,
   `pom.xml`, `build.gradle`, `composer.json`, `Gemfile`.
2. Entry point detection: `main.py`, `index.js`, `main.rs`, `main.go`, `App.js`,
   `app.py`, `server.py`, `manage.py`.
3. Directory convention scoring: presence of `src/`, `lib/`, `app/`, `cmd/`.
4. Dockerfile analysis: `CMD`/`ENTRYPOINT` extraction.
5. CI/CD signal: `.github/workflows/*.yml` → extract `run:` commands.

### Output Schema
```json
{
  "project_type": "python_flask | nodejs_express | rust_axum | ...",
  "entry_points": ["web_app.py", "app.py"],
  "test_command": "pytest tests/",
  "build_command": null,
  "runtime": "python3.12",
  "framework": "flask",
  "confidence": 0.94
}
```

---

## 3. Framework Detection

### Framework Fingerprint Registry (to be maintained in `bootstrap/frameworks.json`)

| Framework | Fingerprint Files | Fingerprint Imports |
|---|---|---|
| Flask | `app.py`, `web_app.py` | `from flask import` |
| Django | `manage.py`, `settings.py` | `django.conf` |
| FastAPI | `main.py` | `from fastapi import` |
| Express.js | `server.js`, `app.js` | `require('express')` |
| Next.js | `next.config.js` | `next/app` |
| React+Vite | `vite.config.ts` | `react` in package.json |
| Rust/Axum | `Cargo.toml` + `axum` dep | `use axum::` |

### Confidence Scoring
Each fingerprint match scores 0.0–1.0. Signals are aggregated with weighted sum.
Threshold: `confidence ≥ 0.75` to proceed autonomously; below → escalate to HITL.

---

## 4. Dependency Resolution

### Language-Specific Resolvers (future `bootstrap/resolvers/`)

**Python:**
1. Parse `requirements.txt` → pinned packages list.
2. Parse `pyproject.toml` [project.dependencies] → semver ranges.
3. Cross-reference against known-good version matrix (cached).
4. Generate install plan; validate against security advisory DB.

**Node.js:**
1. Parse `package.json` dependencies + devDependencies.
2. Detect lock file: `package-lock.json` (npm), `yarn.lock`, `pnpm-lock.yaml`.
3. Lock file takes precedence; fallback to semver resolution.

**Rust:**
1. `Cargo.lock` is authoritative — use it directly.
2. Features flags must be preserved.

### Conflict Resolution Policy
- Hard conflict (incompatible versions): escalate to HITL with diff.
- Soft conflict (overlapping ranges): pick highest compatible version.
- Unknown package: quarantine; do not install; flag for human review.

---

## 5. Runtime Startup Inference

### Startup Command Resolution Chain
```
1. Read Procfile → web: line
2. Read Dockerfile → ENTRYPOINT / CMD
3. Read .replit → run command
4. Read package.json → scripts.start / scripts.dev
5. Infer from framework: flask → `python app.py`, express → `node server.js`
6. Fallback: HITL escalation with suggested command
```

### Health Probe Strategy
- HTTP: `GET /health` or `GET /` — expect 2xx within 10 s.
- TCP: port connectivity check on declared port.
- Retry: 3 attempts at 3 s intervals before declaring startup failed.

---

## 6. Health Validation

### Validation Checks (post-startup)
1. **Process alive**: PID active, not zombie.
2. **Port bound**: OS-level socket check.
3. **HTTP health**: `GET /health` → `{"status":"ok"}` or any 200.
4. **Memory within limits**: RSS < configured `max_memory_mb`.
5. **No crash loop**: fewer than 3 restarts in 60 s window.

### Validation Report Schema
```json
{
  "validation_id": "val_abc123",
  "timestamp": 1716000000.0,
  "checks": {
    "process_alive": true,
    "port_bound": true,
    "http_health": true,
    "memory_ok": true,
    "crash_loop": false
  },
  "overall": "healthy",
  "screenshot_hash": "sha256:..."
}
```

---

## 7. Environment Adaptation

### Target Environments

| Environment | Detection Signal | Adaptation |
|---|---|---|
| Replit | `REPLIT_DEV_DOMAIN` env var | Use `0.0.0.0:5000`, no browser open |
| Docker | `/.dockerenv` file exists | Respect `PORT` env, stdout logging only |
| VPS/bare metal | Neither above | Bind to `0.0.0.0:PORT`, write PID file |
| GitHub Codespaces | `CODESPACES=true` | Port forwarding via Codespaces API |
| Railway/Render | `RAILWAY_ENVIRONMENT` | Use provided `DATABASE_URL`, `PORT` |

### Adaptation Actions
- Rewrite bind address based on target.
- Inject/override `DATABASE_URL` when managed DB is detected.
- Enable/disable browser auto-open.
- Set appropriate `LOG_FORMAT` (JSON for container, human for dev).

---

## 8. Automatic Repair Loops

### Repair Triggers
- Startup failure (health check fails within 30 s).
- Crash loop detected (≥3 crashes in 60 s).
- Memory OOM signal.
- Port conflict.

### Repair Actions (ordered, non-destructive first)
1. **Restart** — simple process restart with same command.
2. **Port reassignment** — try `PORT+1` if bind fails.
3. **Dependency reinstall** — `pip install -r requirements.txt` clean.
4. **Environment reset** — clear session DBs, restart fresh.
5. **HITL escalation** — if all above fail, surface to human with repair log.

### Repair Loop Safety
- Max 5 automated repair attempts before mandatory HITL.
- Each attempt logged to `bootstrap_repair_log` table.
- Repair history exposed via `/api/bootstrap/repair-log`.

---

## 9. Screenshot Validation

### Integration Point
Post-startup, trigger a headless browser screenshot via Playwright.

### Validation Criteria
- Page renders (no white screen, no error page).
- Title tag matches expected project name.
- No `500` error text in body.
- Perceptual hash compared to previous known-good screenshot.

### Failure Actions
- Screenshot diff > threshold → append to repair log.
- Trigger `bootstrap.screenshot_failed` SSE event.
- Present diff to user in HITL panel.

---

## 10. Deployment Diagnostics

### Runtime Diagnostic Probes (future `/api/bootstrap/diagnostics`)

```json
{
  "runtime": "python3.12",
  "dependencies": {"installed": 42, "missing": 0, "outdated": 3},
  "startup_time_ms": 1823,
  "port": 5000,
  "health": "ok",
  "last_repair": null,
  "environment": "replit",
  "framework": "flask",
  "recommendations": [
    "Update tiktoken from 0.7.0 to 0.8.0 for performance improvement"
  ]
}
```

---

## Integration Surface Map

Future V2 engineers must wire these mount points:

| Mount Point | Location | Signal |
|---|---|---|
| Bootstrap trigger button | Topbar utility bar (right) | `<!-- FUTURE-V2: Autonomous runtime bootstrap surface -->` |
| Ingestion progress panel | Left nav slide panel (new "Bootstrap" tab) | `<!-- FUTURE-V2: Bootstrap ingestion panel mount -->` |
| Validation status bar | Below topbar context bar | `<!-- FUTURE-V2: Health validation status surface -->` |
| Repair log viewer | Inspector panel (new tab) | `<!-- FUTURE-V2: Repair log inspector surface -->` |
| Screenshot diff modal | Overlay layer | `<!-- FUTURE-V3: Visual validation canvas mount -->` |
| Execution canvas | Main center panel (new tab) | `<!-- FUTURE-V3: Visual execution canvas mount -->` |
