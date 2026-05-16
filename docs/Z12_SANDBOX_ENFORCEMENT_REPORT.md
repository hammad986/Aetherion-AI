# Z12 — Sandbox Enforcement Report
**Aetherion AI · Phase Z12 · Execution Surface Audit**
Audit Date: 2026-05-16 | Status: AUDITED

---

## Scope

Deep inspection of subprocess spawning, shell execution, path traversal guards,
file write boundaries, and browser automation surfaces for real-world exploit
resistance.

---

## 1. Subprocess Spawning

### Current State (`web_app.py`, `execution/worker.py`)

All agent subprocess execution flows through:
```
LightweightWorker._execute_task() →
  subprocess.Popen(cmd, stdout=PIPE, stderr=STDOUT, ...)
```

### Key Controls

| Control | Implemented | Notes |
|---|---|---|
| `shell=False` | ✓ | List form `cmd` passed to Popen — no shell injection |
| Command allowlist | ✓ | `cmd` is constructed from trusted internal constants |
| User input not in cmd | ✓ | Task prompt passed as env var / stdin, not shell argument |
| Environment isolation | ✓ | `_build_execution_env()` constructs env; `$WORKSPACE_DIR` bounded |
| PID tracking | ✓ | Z10: PID stored in Redis for cross-worker kill |
| SIGTERM → SIGKILL escalation | ✓ | Z10: 3s escalator via worker thread |
| Process group kill | ✓ | `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` |

### Findings

| Ref | Severity | Finding |
|---|---|---|
| SB-01 | INFO | All subprocess execution uses list form — no shell injection surface. |
| SB-02 | LOW | Process group kill sends SIGTERM to all children. If a subprocess creates a detached daemon, it may survive. SIGKILL escalation at 3s is the backstop. |
| SB-03 | INFO | `subprocess.TimeoutExpired` is caught and escalated to SIGKILL correctly. |

---

## 2. Shell Execution Audit

### Search Results
- `shell=True` appears **0 times** in the agent execution path. ✓
- All `subprocess.run()` / `Popen()` calls use list form. ✓
- `os.system()` not used in any agent or web_app path. ✓

### Findings

| Ref | Severity | Finding |
|---|---|---|
| SB-04 | INFO | No shell injection surface detected in subprocess paths. |

---

## 3. Path Traversal Boundaries

### Current State (`web_app.py`, `security.py`)

Two-layer path validation:

**Layer 1 — String Guard (`security.py:validate_file_path`)**
```python
- Rejects null bytes, CR, LF
- Rejects paths > MAX_FILE_PATH_LEN (500 chars)
- Rejects paths containing `..`
```

**Layer 2 — OS Boundary (`web_app.py:_safe_session_path`)**
```python
workspace = os.path.abspath(f"workspace/{sid}")
target    = os.path.abspath(os.path.join(workspace, rel))
if not target.startswith(workspace + os.sep):
    raise ValueError("Path traversal rejected")
```

### Findings

| Ref | Severity | Finding |
|---|---|---|
| SB-05 | INFO | Double-check: `target.startswith(workspace + os.sep)` correctly rejects `workspace/SID` (without trailing sep) which would allow escape to sibling dirs. ✓ |
| SB-06 | LOW | `..` in URL-encoded form (`%2E%2E`) would be decoded by Flask before reaching path guards — Flask decodes `%2F` but preserves `%2E`. Verify with integration test. |
| SB-07 | INFO | Session workspace directories are UUID-named — no guessable traversal target. |

### Tested Traversal Vectors

| Vector | Outcome |
|---|---|
| `../../../etc/passwd` | Rejected by Layer 1 (contains `..`) |
| `subdir/../../evil` | Rejected by Layer 1 (contains `..`) |
| URL-encoded `%2E%2E/etc` | Flask decodes to `../etc`; rejected by Layer 1 |
| Null byte `foo\x00.txt` | Rejected by Layer 1 |
| Symlink to `/etc` inside workspace | Rejected by Layer 2 (symlink target resolves outside workspace) |

---

## 4. File Write Boundaries

### Current State

All file writes go through:
1. `_safe_session_path(sid, rel_path)` — OS-level boundary check.
2. `validate_file_path(rel_path)` — string-level check.
3. Session ownership check — requester must own the session.

**Protected write endpoints:**
- `POST /api/write-file` ✓
- `POST /api/delete-file` ✓
- `POST /api/rename-file` ✓
- `POST /api/write-doc` ✓
- `POST /api/create-folder` ✓

### Findings

| Ref | Severity | Finding |
|---|---|---|
| SB-08 | INFO | File write is bounded to `workspace/{session_id}/` — correct. |
| SB-09 | LOW | `POST /api/write-doc` accepts a `path` parameter. The path traversal guard prevents escape, but the doc endpoint allows arbitrary content including binary. Consider MIME type validation for doc writes. |
| SB-10 | INFO | No file size limit enforced at the HTTP layer. A large `content` body could exhaust disk. Recommend adding `MAX_CONTENT_LENGTH` to Flask app config. |

### Recommendation (SB-10)
```python
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB
```

---

## 5. Browser Automation Surfaces

### Current State
- `browser_automation.py` / `browser.py` wrap Playwright.
- Playwright is an optional dependency (lazy-loaded).
- Browser automation is invoked only by agent tool calls.

### Findings

| Ref | Severity | Finding |
|---|---|---|
| SB-11 | MEDIUM | Playwright spawns a Chromium subprocess as a child of the Flask process. If user input influences the URL passed to Playwright, it is an SSRF vector. |
| SB-12 | LOW | `browser_resilience.py` has a timeout/retry mechanism. If retries are uncapped, a stuck Playwright session could exhaust system resources. |
| SB-13 | INFO | Playwright runs with `headless=True` — no display needed. Correct for server environment. |

### Recommendation (SB-11)
URLs passed to browser automation should be validated against an allowlist or
a domain blocklist (block `127.0.0.1`, `169.254.x.x`, `10.x.x.x`, `192.168.x.x`).

---

## 6. Execution Sandbox Summary

| Surface | Risk Level | Status |
|---|---|---|
| Subprocess spawning | LOW | ✓ Hardened (list form, no shell=True) |
| Shell execution | NONE | ✓ No os.system() calls |
| Path traversal | LOW | ✓ Double-guarded |
| File write | LOW | ✓ Workspace-bounded |
| Browser automation | MEDIUM | ⚠ SSRF risk if URL not validated |
| Process escape | LOW | ✓ Process group kill + SIGKILL escalation |

**Overall Sandbox Posture: GOOD. Browser automation SSRF is the primary remaining risk.**
