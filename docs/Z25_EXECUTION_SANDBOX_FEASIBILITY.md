# Z25 — Execution Sandbox Feasibility Analysis

**Phase:** Z25D — Engineering Directive: Real Execution Isolation  
**Date:** 2026-05-16  
**Status:** FORENSIC ANALYSIS ONLY — NO IMPLEMENTATION  
**Classification:** Operational Planning

---

## Directive

Perform a forensic feasibility analysis for real execution isolation. **Do not implement.** Document risks, paths, and constraints only.

---

## 1. Current Execution Model: Risks Inventory

### 1.1 How Code Currently Executes

The Nexora platform executes AI-generated code via Python's `subprocess.Popen` (or equivalent shell invocation) from within the Flask web process. The process tree is:

```
flask worker (web_app.py)
  └─ agent.py / orchestrator.py
       └─ subprocess.Popen(shell=True, ...)  ← UNSAFE
            └─ arbitrary user/AI code
```

### 1.2 Current Execution Risks

| Risk | Severity | Description |
|---|---|---|
| Host filesystem access | CRITICAL | Subprocess has full read/write access to all files accessible to the Flask process user |
| Unbounded CPU | HIGH | No CPU quota enforced; a runaway AI-generated loop can saturate the host |
| Unbounded memory | HIGH | No memory limit; a memory-hungry subprocess can OOM the host process |
| Network unrestricted | HIGH | Subprocess can make arbitrary outbound connections |
| Process escalation | HIGH | If Flask runs as root (common in containers), subprocess inherits root |
| No timeout enforcement | HIGH | Long-running commands block the agent indefinitely |
| No process accounting | MEDIUM | Cannot attribute resource usage to a specific session/user |
| Shared secrets exposure | CRITICAL | Subprocess inherits Flask env vars including `JWT_SECRET`, `SECRET_KEY`, API keys |

### 1.3 Current Sandbox Weaknesses

1. **Zero isolation boundary.** The subprocess and the Flask process share the same user, filesystem namespace, network namespace, and IPC namespace.
2. **No seccomp filtering.** Dangerous syscalls (fork bomb, ptrace, mount) are available to executed code.
3. **No cgroup limits.** CPU, memory, disk I/O, and PID count are unrestricted.
4. **Environment variable leakage.** `subprocess.Popen` inherits the full environment unless explicitly scrubbed. Current code does not scrub.
5. **No filesystem chroot.** AI-generated file writes can overwrite application source files, the database, or credentials.

---

## 2. Isolation Technologies: Feasibility Assessment

### 2.1 Docker Worker Isolation

**Concept:** Each execution session spawns a dedicated Docker container. AI-generated code runs inside the container. Results are streamed back via stdin/stdout or a mounted volume.

**Feasibility on Replit:** LOW
- Replit's container does not expose the Docker socket (`/var/run/docker.sock`) to user processes.
- `docker run` is not available inside the Replit environment.
- Docker-in-Docker (DinD) requires `--privileged` mode, which Replit does not grant.
- **Verdict:** Not feasible in the Replit hosted environment without a custom VPS deployment.

**Feasibility on VPS (self-hosted):** HIGH
- Full Docker daemon available.
- Worker containers can be pre-built and pool-warmed for low latency.
- Container lifecycle: create → exec → destroy per session.
- Image: minimal Python + project dependencies, no credentials, read-only source mount.
- Networking: `--network none` for most executions; selectively enabled for fetch-capable tasks.

**Implementation cost:** Medium-High. Requires a container orchestration layer, image registry, volume management, and a container-to-Flask IPC protocol (unix socket or HTTP).

---

### 2.2 gVisor Compatibility

**Concept:** gVisor (`runsc`) is a user-space kernel that intercepts syscalls from containerized workloads, providing a second layer of isolation even if the container is compromised.

**Feasibility on Replit:** NOT FEASIBLE
- gVisor requires kernel-level support (`KVM` or `ptrace` sandbox mode).
- Replit containers run on a host that does not expose KVM or allow ptrace-based guest kernels.

**Feasibility on VPS:** CONDITIONAL
- Works on GCP (native support), AWS (with configuration), and bare-metal KVM hosts.
- Adds ~10–30% syscall overhead versus plain Docker — acceptable for execution latency at the 1–60 second scale.
- Requires Docker daemon configured with `--runtime=runsc`.
- **Verdict:** Viable on VPS with the right host setup. Overkill for beta; recommended for production.

---

### 2.3 CPU Limits

**Mechanism:** Linux cgroups v2 `cpu.max` (absolute quota) or Docker `--cpus` flag.

**Replit feasibility:** PARTIAL
- Replit runs within a cgroup; subprocesses inherit it.
- Setting child cgroup limits from within the user process requires `CAP_SYS_ADMIN` — generally not granted.
- **Workaround available:** `resource.setrlimit(resource.RLIMIT_CPU, (soft, hard))` via Python's `resource` module limits CPU time per process without cgroup privilege. This is a soft protection — it limits total CPU seconds, not concurrent cores.

**VPS feasibility:** FULL
- Docker `--cpus=0.5` restricts to 0.5 core equivalents.
- Recommended limit: 1 vCPU per execution worker.

---

### 2.4 Memory Limits

**Mechanism:** cgroups v2 `memory.max` or Docker `--memory` flag.

**Replit feasibility:** PARTIAL
- Same cgroup privilege constraint as CPU.
- **Workaround:** `resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024))` limits virtual address space. Effective for Python workloads.

**VPS feasibility:** FULL
- Docker `--memory=512m --memory-swap=512m` (no swap) enforced by kernel.
- Recommended: 512 MB per execution worker for typical AI-generated code tasks.

---

### 2.5 Filesystem Isolation

**Replit feasibility:** LOW
- `chroot` requires root.
- `unshare --mount` requires `CAP_SYS_ADMIN`.
- **Partial workaround:** Create a per-session temp directory, run subprocess with `cwd` set to it, and disallow `..` in AI-generated paths via a path sanitiser in the orchestrator.

**VPS feasibility:** FULL
- Docker provides overlay filesystem by default — container writes are isolated to a copy-on-write layer.
- Mount the session workspace as a read-write volume; mount source code as read-only; do not mount `~/.env` or credentials.

---

### 2.6 Restricted Networking

**Replit feasibility:** NOT FEASIBLE for hard network isolation
- Subprocess inherits the host network namespace; `unshare --net` requires privilege.
- **Partial workaround:** Firewall rules or `iptables` (if available) could block outbound from the subprocess by PID — but iptables is not available in Replit containers.
- **Practical workaround:** AI system prompt explicitly prohibits network calls; agent code-review layer detects `requests`, `urllib`, `socket` usage before execution.

**VPS feasibility:** FULL
- Docker `--network none` for sandboxed runs.
- A `--network` policy layer can selectively allow outbound to specific domains (e.g., package registries) via a DNS-based allowlist proxy.

---

### 2.7 Process Timeout Enforcement

**Replit feasibility:** HIGH (fully available now)
- `subprocess.Popen` with `timeout=N` parameter on `.communicate()` or `.wait()`.
- `signal.alarm(N)` in the parent process.
- Both are available without privilege escalation.
- **Recommended immediate action:** Enforce a 120-second hard timeout on all subprocess executions. This is the single most impactful safety improvement available without infrastructure changes.

**VPS feasibility:** FULL
- Docker `--stop-timeout` and `docker stop` with SIGKILL fallback.

---

## 3. Realistic Beta-Grade Isolation Path

### Stage 0 — Immediate (No infrastructure change required)

Available now in the Replit environment:

1. **Scrub subprocess environment.** Pass `env={...}` with only the variables the subprocess needs — never inherit the full Flask environment.
2. **Enforce `timeout=120`** on all subprocess calls.
3. **Set `RLIMIT_CPU` and `RLIMIT_AS`** on each worker process via `resource.setrlimit`.
4. **Session-scoped working directory.** All file I/O constrained to `/tmp/nx-session-{id}/`.
5. **Path sanitiser.** Reject `..`, absolute paths outside the session dir, and symlinks in AI-generated file writes.

Estimated effort: 2–3 days.

**Risk reduction:** Eliminates credential leakage, adds soft CPU/memory cap, bounds filesystem access.

### Stage 1 — Beta (Self-hosted VPS, ~1 month effort)

1. Deploy a separate "executor" service (Python FastAPI or Flask) on the same VPS as a distinct process.
2. Flask → executor communication via Unix socket or loopback HTTP.
3. Each execution request spawns a Docker container from a pre-warmed pool.
4. Container configuration:
   - `--network none`
   - `--memory=512m --memory-swap=512m`
   - `--cpus=0.5`
   - `--read-only` root filesystem with a tmpfs `/tmp`
   - Session workspace mounted as `/workspace`
5. Results streamed back via Docker `exec` stdout capture.

**Risk reduction:** True isolation boundary. Compromised code cannot reach Flask, credentials, or the database.

### Stage 2 — Production (3–6 months)

1. Container pool management (pre-warm N containers, reuse after cleanup).
2. gVisor runtime for defence-in-depth.
3. Seccomp profile restricting dangerous syscalls (ptrace, mount, reboot, etc.).
4. Per-session resource accounting for billing.
5. Execution result caching for identical inputs.

---

## 4. Operational Cost Analysis

| Stage | Infrastructure Cost | Latency Impact | Engineering Effort |
|---|---|---|---|
| Stage 0 (RLIMIT + scrub) | $0 | None | 2–3 days |
| Stage 1 (Docker executor) | +$20–$60/mo VPS | +300–800ms cold start | ~4 weeks |
| Stage 2 (gVisor + pool) | +$60–$200/mo | +50ms (warm) | ~3 months |

Container cold-start latency (Stage 1) is the primary UX concern. Pre-warming a pool of 3–5 idle containers reduces this to ~50ms for warm starts.

---

## 5. Deployment Implications

### Replit Hosted Deployment
- Stage 0 only is feasible.
- Stages 1–2 require migration off Replit's managed hosting to a VPS.
- Replit does not expose Docker, does not allow kernel namespacing, and does not permit outbound socket binding to secondary processes in a reliable way.

### VPS Deployment (Recommended for Production)
- All stages feasible.
- Recommended stack: Ubuntu 22.04 LTS, Docker CE 24+, Caddy reverse proxy, Gunicorn + Flask.
- For gVisor: GCP e2-standard-2 or equivalent with nested virtualisation enabled.

### Replit Compatibility Concerns
- `resource.setrlimit` availability: confirmed available in Replit Python 3.11 environment.
- `subprocess.Popen(timeout=...)` availability: confirmed.
- Environment scrubbing: confirmed — `env={}` dict supported.
- Docker socket: **NOT available** in Replit. Do not attempt.
- `unshare`, `chroot`, `iptables`: **NOT available**.

---

## 6. Migration Difficulty Assessment

| Migration Step | Difficulty | Blocking Dependencies |
|---|---|---|
| Stage 0 env scrub + timeout | LOW | None |
| Stage 0 RLIMIT | LOW | None |
| Stage 0 session working dir | LOW | Orchestrator refactor |
| Stage 1 executor service | MEDIUM | VPS deployment required |
| Stage 1 Docker integration | MEDIUM | Container image build pipeline |
| Stage 1 IPC protocol | MEDIUM | Protocol design + streaming |
| Stage 2 gVisor | HIGH | Host kernel support + testing |
| Stage 2 container pool | HIGH | Pool manager + health checks |

---

## 7. Recommended Staged Rollout Strategy

```
Month 0 (Now)
  └─ Stage 0: Env scrub + timeout + RLIMIT + session dir
       Impact: Credential safety, DoS resistance
       Risk: Minimal (additive only)

Month 1–2
  └─ Stage 1 planning + VPS migration prep
  └─ Docker executor prototype (offline testing)

Month 3
  └─ Stage 1 beta: Docker executor in production for paying users
       Single-container pool initially (no warm pool)

Month 4–5
  └─ Container pool (5 warm containers)
  └─ Per-session billing metrics via cgroup accounting

Month 6+
  └─ Stage 2 evaluation (gVisor + seccomp)
       Only if customer-reported security incidents warrant it
```

---

## 8. Summary Verdict

**Current risk level:** HIGH — no meaningful isolation between AI-generated code and the host Flask process.

**Most impactful immediate action:** Environment variable scrubbing on subprocess spawn (prevents credential exposure with zero infrastructure change).

**Second most impactful:** 120-second hard timeout (prevents DoS from runaway AI-generated loops).

**Long-term path:** Docker executor service on VPS. This is the minimum standard for a production-grade AI execution platform.

**gVisor:** Warranted only at production scale with established security SLAs. Do not prioritise in beta.

*Z25D Feasibility Analysis complete. No implementation performed.*
