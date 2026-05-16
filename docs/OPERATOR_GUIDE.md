# Nexora Operator Guide — v0.9-beta

> Concise operational reference. Not marketing copy.

---

## What Nexora Does

Nexora is an autonomous execution workspace. You define a goal; the agent plans, executes, validates, and reports back. You intervene only when needed.

---

## Quick Start

```bash
# 1. Validate deployment
python nx_startup_check.py

# 2. Start server
gunicorn -c gunicorn.conf.py web_app:app

# 3. Open browser
http://localhost:5000
```

---

## Workspace Layout

```
┌─ Top Bar ─────────────────────────────────────────────────┐
│  [Nexora]  [Session selector]  [Model]  [Plan]  [Status]  │
├─ Nav ─┬─── Center (Execution) ───────────────┬─ Inspector ─┤
│  Files│  [Output] [Terminal] [Preview] [Files]│  (Ctrl+\)  │
│  Chat │                                       │            │
│  Hist │     ← Active mission runs here →      │  Trace /   │
│  Set  │                                       │  Evidence  │
└───────┴───────────────────────────────────────┴────────────┘
         [Execution Composer — type task here]
              [Runtime strip — live status]
```

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+1` | Output tab |
| `Ctrl+2` | Terminal tab |
| `Ctrl+3` | Preview tab |
| `Ctrl+4` | Files tab |
| `Ctrl+\` | Toggle inspector |
| `Ctrl+I` | Toggle inspector (alt) |
| `Ctrl+.` | Stop execution |
| `Ctrl+Enter` | Submit task |
| `Ctrl+Shift+D` | Operator diagnostics panel |
| `Ctrl+K` | Command palette (if enabled) |

---

## Execution Flow

```
You type a task
    ↓
PLAN — agent outlines approach
    ↓
REASONING — agent thinks through steps
    ↓
ACTION — agent executes (file writes, commands, API calls)
    ↓
VALIDATION — agent checks its own output
    ↓
RESULT — outcome, files, summary
```

Each step is labeled and grouped in the Output tab. Sealed (completed) steps dim automatically.

---

## Trust & Confidence

The inspector shows a **confidence score** (0–100%) for each execution phase.

| Score | Meaning |
|-------|---------|
| 80–100% | High confidence — validated output |
| 50–79% | Moderate — agent proceeded but flagged uncertainty |
| < 50% | Low — possible retry or escalation |

Trust pills:
- 🟢 **OK** — validated
- 🟡 **WARN** — needs review
- 🔴 **FAIL** — failed, recovery attempted

---

## HITL (Human-in-the-Loop)

When the agent is uncertain or encounters a decision boundary, it pauses and shows an **approval card**.

**What you see:** "Agent needs your decision" card in the inspector.

**Your options:**
- **Proceed** — approve the proposed action
- **Cancel action** — reject and let the agent try an alternative
- **Review trace** — inspect the full reasoning before deciding

The agent waits indefinitely. You will not lose work by taking time to review.

---

## Recovery Behaviors

| Situation | What Nexora does |
|-----------|-----------------|
| Stream error | Auto-reconnects with exponential backoff (max 20 attempts) |
| Agent failure | Logs failure reason, attempts recovery or escalates |
| Session lost | Restores from replay — last checkpoint is preserved |
| SSE disconnect | Reconnect banner shown — session state is kept in memory |
| Page refresh | Session selector allows manual reconnect to active session |

---

## Panels & Navigation

**Nav rail (left, 48px):**
- Files — session file tree
- Chat — message history
- History — past sessions
- Settings — provider, plan, auth

**Inspector (right, slide-over):**
- Opens: `Ctrl+\` or click any trust pill
- Shows: trace, validation evidence, HITL cards, retry history
- Hidden by default — does not occupy permanent space

**Runtime strip (bottom, 24px):**
- Always visible
- Shows: active phase, operation label, connection status

---

## Diagnostics (Operator Only)

Press `Ctrl+Shift+D` to open the diagnostics panel.

Shows:
- Bus listener count
- Monaco model count
- SSE connection state
- Mission phase
- Trust confidence
- Inspector/timeline node counts
- JS heap (Chrome only)
- Event history (last 10)

Access analytics from browser console:
```js
NxClarity.analytics.report()   // array of event counts
NxClarity.analytics.reset()    // clear analytics
NxDiag.snapshot()              // capture forensics snapshot
```

---

## Deployment

### Single-server (default)
```bash
cp .env.example .env          # fill in secrets
python nx_startup_check.py    # validate
gunicorn -c gunicorn.conf.py web_app:app
```

### Multi-worker (Redis required)
```env
REDIS_URL=redis://localhost:6379/0
WORKERS=4
```

### Production checklist
- [ ] `JWT_SECRET` set to 64-char random hex
- [ ] `SESSION_SECRET` set
- [ ] nginx in front of port 5000 (TLS + `proxy_buffering off`)
- [ ] `ALLOW_DEV_AUTH=0`
- [ ] File permissions: `saas_platform.db` writable by gunicorn user
- [ ] Reverse proxy: `proxy_read_timeout 3600` for SSE connections

---

## Deployment Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Agent never starts | No AI key | Check `OPENAI_API_KEY` etc in `.env` |
| SSE reconnects repeatedly | Proxy buffering on | Set `proxy_buffering off` in nginx |
| Port collision error | Port already in use | `kill $(lsof -t -i:5000)` before startup |
| Trust score stuck at 0% | nx-trust-intel.js not loading | Check `/static/js/nx-trust-intel.js` HTTP 200 |
| HITL card not appearing | Inspector closed | Press `Ctrl+\` to open inspector |
| Redis connection failed | Missing module or offline | `pip install redis`, check `redis-server` status |

---

## Runtime Recovery Procedures

If the execution workspace encounters a failure:
1. **Stream Error (Disconnect)**: The system automatically attempts exponential backoff reconnection. Your UI state is preserved.
2. **Page Refresh**: Use the "Session Selector" drop-down in the top bar to re-attach to the still-running background mission.
3. **Flaky Workflow detected**: If a mission fails identically 3 times, `NxFailureIntel` flags it. Export the forensics JSON (`Ctrl+Shift+D`) and review the timeline to manually intervene via a precise Task instruction.
4. **Agent Thrashing**: If retries spike, press `Ctrl+.` (Stop), clear the task, and break the objective down into smaller, explicit steps.

---

## Known Beta Limitations

1. **SQLite**: single-writer — concurrent users may experience write contention. Upgrade to PostgreSQL for >20 users.
2. **SSE auth**: session ID in URL param (EventSource limitation) — visible in server logs. Use fetch+ReadableStream for GA.
3. **Session memory**: trust and mission memory resets on page reload. Not persisted.
4. **Rate limiting**: in-memory only — resets on server restart. Redis-backed limiting needed for production.
5. **Max concurrent users**: ~10–20 on SQLite/single-worker. ~50–100 with Redis + 4 workers.
6. **Monaco models**: GC runs on 30s timer, not on tab close. Brief memory spikes possible in heavy multi-file sessions.
7. **Preview iframe**: does not auto-reload on agent file changes. Manual refresh required.

---

## Architecture Map

```
Browser
  NxBus (event bus)
    ├── nx-sse-runtime.js    ← SSE connection + reconnect
    ├── nx-chunker.js        ← groups SSE events into execution chunks
    ├── nx-orchestrator.js   ← cross-surface sync (timeline/terminal/preview/inspector)
    ├── nx-trust-intel.js    ← confidence, validation, HITL cards
    ├── nx-mission.js        ← mission lifecycle, phase tracking
    ├── nx-surface-fusion.js ← auto-focus, contextual markers
    ├── nx-polish.js         ← ergonomics, keyboard map, idle states
    ├── nx-hardening.js      ← memory GC, SSE storm guard, DOM bounds
    ├── nx-diagnostics.js    ← operator diagnostics panel
    └── nx-clarity.js        ← first-run, analytics, messaging

Backend (web_app.py)
  Flask + gunicorn
    ├── auth_system.py       ← JWT, OAuth, brute-force protection
    ├── security.py          ← rate limiting, CORS, CSP, sanitisation
    ├── streaming/           ← SSE manager + Redis bridge
    ├── sessions.db          ← SQLite session persistence
    └── workspace/<sid>/     ← isolated per-session file system
```

---

*Nexora v0.9-beta — Single-user dev-grade stable*
*Architecture locked at Phase Q. Evolves through real usage.*
