# Z26 — Beta Readiness Matrix

## Honest Beta Readiness Verdict

**Verdict: CONDITIONAL BETA — Not ready for public launch without addressing items marked BLOCKER.**

---

## System-Level Readiness

| Area                        | Readiness | Verdict           |
|-----------------------------|-----------|-------------------|
| Core API / routing          | ✅ Stable | Ready             |
| Authentication (JWT)        | ✅ Stable | Ready             |
| Multi-LLM routing           | ✅ Stable | Ready             |
| Agent execution loop        | ⚠️ Beta   | Limited testing   |
| Context compression         | ⚠️ Beta   | In-memory only    |
| Confidence scoring          | ⚠️ Beta   | Observable only   |
| HITL escalation path        | ⚠️ Beta   | No async queue    |
| Scheduling                  | ⚠️ Beta   | No persistence    |
| Session persistence (SQLite)| ✅ Stable | WAL mode enabled  |
| Rate limiting               | ✅ Stable | Ready             |
| Security headers            | ✅ Stable | Hardened          |
| Admin panel                 | ✅ Stable | Ready             |
| Billing (Razorpay)          | ⚠️ Beta   | Keys not set      |
| Email delivery              | ⚠️ Beta   | Keys not set      |
| OAuth (Google/GitHub)       | ⚠️ Beta   | Keys not set      |
| Redis / distributed SSE     | ⚠️ Beta   | Optional, graceful fallback |
| Long-term memory (chromadb) | ⚠️ Beta   | Lazy-loaded, optional |
| Terminal backend            | ⚠️ Beta   | Sandbox isolation needed |
| Multimodal ingestion        | 🚧 Foundation | Not fully wired |

---

## BLOCKER Items (Must Fix Before Public Beta)

### B1 — Terminal Isolation
The terminal backend executes arbitrary shell commands. Without robust sandboxing, this is a serious security risk in multi-user deployments. Single-user self-hosted is acceptable; public SaaS is not.

### B2 — Scheduler Persistence
The scheduler loses all pending missions on restart. For any mission with a deadline or business consequence, this is unacceptable in production.

### B3 — HITL Review Queue
When confidence drops to CRITICAL, execution is flagged but there is no durable async review queue. In single-user mode this is acceptable. Multi-user requires a proper queue.

### B4 — Context Compression Not Wired to Agent
`runtime/context_compression.py` is implemented but not yet called from `agent.py`. The agent still uses its own context management. This must be integrated before the compression benefits are realized.

### B5 — Confidence Engine Not Wired
`runtime/confidence_engine.py` is implemented but not called from the execution loop. HITL escalation is not active.

---

## WARNING Items (Should Fix Before Public Beta)

| Item | Description |
|------|-------------|
| W1   | No automated test coverage for new runtime modules |
| W2   | Billing (Razorpay) keys not configured — payments non-functional |
| W3   | OAuth providers not configured — Google/GitHub login non-functional |
| W4   | Email delivery not configured — password reset, verification non-functional |
| W5   | Redis not configured — SSE falls back to in-process (no multi-worker support) |
| W6   | JS frontend has 10 MutationObserver instances (over budget of 8) |
| W7   | No rate limit on `/api/auth/signup` beyond basic limiter |

---

## DEFER to v2

- Vector semantic memory integration into context pipeline
- Learned confidence calibration
- Multi-user collaborative sessions
- Plugin marketplace
- Long-horizon autonomous goal decomposition
- Adaptive persona system
- Distributed scheduler with Redis
- Full OCR pipeline
- Audio/video ingestion
