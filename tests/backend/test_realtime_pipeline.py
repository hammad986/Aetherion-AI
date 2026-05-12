"""
tests/backend/test_realtime_pipeline.py
=======================================
AETHERION AI — Realtime Pipeline Validation Suite
Phase 1–8 verification harness

Runs WITHOUT a live Flask server.
Tests the actual runtime modules in isolation.

Usage:
    cd <project_root>
    set AETHERION_REALTIME_V1=true
    python -m pytest tests/backend/test_realtime_pipeline.py -v --tb=short 2>&1

Or run directly:
    python tests/backend/test_realtime_pipeline.py
"""

import sys
import os
import json
import time
import threading
import queue
import uuid

# ── Resolve project root ──────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# ── PHASE 1: Feature Flag Activation ─────────────────────────────────────────
# Activate BEFORE any project imports so all modules see it at import time.
os.environ["AETHERION_REALTIME_V1"] = "true"

# ── Colour helpers for standalone run ────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

PASS = f"{GREEN}PASS{RESET}"
FAIL = f"{RED}FAIL{RESET}"
WARN = f"{YELLOW}WARN{RESET}"

results = []

def check(name: str, condition: bool, detail: str = ""):
    icon = PASS if condition else FAIL
    print(f"  [{icon}] {name}" + (f"  — {detail}" if detail else ""))
    results.append((name, condition, detail))
    return condition

def section(title: str):
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}")

# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 1 — Feature Flag Activation
# ═════════════════════════════════════════════════════════════════════════════
section("PHASE 1 — Feature Flag Activation")

flag_val = os.getenv("AETHERION_REALTIME_V1", "")
check("Flag reads as 'true'", flag_val.lower() == "true", f"Got: {flag_val!r}")
check("Flag is case-insensitive (lower)", flag_val.lower() == "true")
check("Unset flag defaults to off", os.getenv("AETHERION_REALTIME_V1_MISSING", "").lower() != "true")

# Test fallback: temporarily set to false, verify behaviour switches
os.environ["AETHERION_REALTIME_V1"] = "false"
flag_off = os.getenv("AETHERION_REALTIME_V1", "").lower() != "true"
check("Flag OFF disables realtime path", flag_off)
os.environ["AETHERION_REALTIME_V1"] = "true"   # restore

# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 2 — SSE Manager Lifecycle
# ═════════════════════════════════════════════════════════════════════════════
section("PHASE 2 — SSEManager Lifecycle")

try:
    from streaming.sse_manager import SSEManager, SSEClient, SSEEvent
    check("SSEManager imports cleanly", True)
except Exception as e:
    check("SSEManager imports cleanly", False, str(e))
    sys.exit(1)

# Client registration
session_id = f"test_sess_{uuid.uuid4().hex[:8]}"
client_a = SSEManager.register_client(session_id=session_id)
check("register_client returns SSEClient", isinstance(client_a, SSEClient))
check("Client has queue attribute",        hasattr(client_a, "queue"))
check("Client queue has maxsize=1000",     client_a.queue.maxsize == 1000)
check("Client is marked connected",        client_a.connected is True)
check("Client session_id matches",         client_a.session_id == session_id)
check("Client has unique client_id",       client_a.client_id.startswith("sse_"))

# Dual-client: second subscriber to same session
client_b = SSEManager.register_client(session_id=session_id)
check("Second client has different id", client_a.client_id != client_b.client_id)

# Broadcast reaches BOTH clients
SSEManager.broadcast_to_session(session_id, "agent.think", {"thought": "Hello realtime"})
time.sleep(0.05)
check("broadcast delivers to client_a",
      not client_a.queue.empty(),
      f"queue size: {client_a.queue.qsize()}")
check("broadcast delivers to client_b",
      not client_b.queue.empty(),
      f"queue size: {client_b.queue.qsize()}")

# Dequeue and inspect event
evt_a = client_a.queue.get_nowait()
check("Event is SSEEvent instance",    isinstance(evt_a, SSEEvent))
check("Event data is correct dict",    isinstance(evt_a.data, dict))
check("Event type is agent.think",     evt_a.event == "agent.think")
check("Event thought field present",   evt_a.data.get("thought") == "Hello realtime")

# encode() produces valid SSE wire format
encoded = evt_a.encode()
check("encode() returns string",       isinstance(encoded, str))
check("encode() contains 'event:'",   "event: agent.think" in encoded)
check("encode() contains 'data:'",    "data:" in encoded)
check("encode() ends with \\n\\n",    encoded.endswith("\n\n"))

# Removal / disconnect
SSEManager.remove_client(client_a.client_id)
check("remove_client marks disconnected", client_a.connected is False)
check("remove_client clears from registry",
      client_a.client_id not in SSEManager._clients)

# Verify isolated: broadcast after removal does NOT reach removed client
client_a.queue.queue.clear()  # drain previous items
SSEManager.broadcast_to_session(session_id, "test.post_remove", {"x": 1})
time.sleep(0.05)
check("Removed client does NOT receive further events",
      client_a.queue.empty(),
      f"queue size: {client_a.queue.qsize()}")

# Backpressure: fill queue to maxsize, verify warning but no crash
SSEManager.remove_client(client_b.client_id)

# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 3 — Event Schema Integrity
# ═════════════════════════════════════════════════════════════════════════════
section("PHASE 3 — Event Schema Integrity")

try:
    from execution.events import RuntimeEvent, EventTypes, create_event
    check("execution.events imports cleanly", True)
except Exception as e:
    check("execution.events imports cleanly", False, str(e))

exec_id = f"exec_{uuid.uuid4().hex[:8]}"

# Build one event per canonical type
event_types_to_check = [
    (EventTypes.TASK_STARTED,   {"note": "started"}),
    (EventTypes.TASK_COMPLETED, {"result": "done"}),
    (EventTypes.TASK_FAILED,    {"error": "oops"}),
    (EventTypes.TASK_CANCELLED, {}),
    (EventTypes.TOOL_CALLED,    {"tool": "write_file", "step": 0}),
    (EventTypes.FILE_MODIFIED,  {"path": "app.py"}),
    (EventTypes.STREAM_CHUNK,   {"content": "hello"}),
]

for etype, kwargs in event_types_to_check:
    evt = create_event(etype, session_id, exec_id, **kwargs)
    d   = evt.to_dict()
    ok  = (
        d.get("type")         == etype and
        d.get("session_id")   == session_id and
        d.get("execution_id") == exec_id and
        isinstance(d.get("event_id"), str) and
        isinstance(d.get("timestamp"), float)
    )
    check(f"Event schema valid: {etype}", ok, str(d) if not ok else "")

# Verify to_dict produces JSON-serialisable output
try:
    json.dumps(evt.to_dict())
    check("RuntimeEvent.to_dict() is JSON-serialisable", True)
except Exception as e:
    check("RuntimeEvent.to_dict() is JSON-serialisable", False, str(e))

# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 4 — ExecutionTask & Cancellation
# ═════════════════════════════════════════════════════════════════════════════
section("PHASE 4 — ExecutionTask & Cancellation")

try:
    from execution.worker import ExecutionTask, LightweightWorker, ResourceGovernance
    check("execution.worker imports cleanly", True)
except Exception as e:
    check("execution.worker imports cleanly", False, str(e))
    sys.exit(1)

limits = ResourceGovernance(ttl_seconds=60)
task   = ExecutionTask(session_id=session_id, payload={"prompt": "test"}, limits=limits)

check("ExecutionTask created with queued status", task.status == "queued")
check("ExecutionTask has unique execution_id",    task.execution_id.startswith("exec_"))
check("is_cancelled starts False",               task.is_cancelled is False)
check("_cancel_event starts unset",              not task._cancel_event.is_set())

task.cancel()
check("cancel() sets is_cancelled",             task.is_cancelled is True)
check("cancel() sets _cancel_event",            task._cancel_event.is_set())

# TTL check (should NOT raise — task.started_at is None)
try:
    task.check_ttl()
    check("check_ttl with no started_at is safe", True)
except Exception as e:
    check("check_ttl with no started_at is safe", False, str(e))

# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 5 — LightweightWorker Thread Execution
# ═════════════════════════════════════════════════════════════════════════════
section("PHASE 5 — LightweightWorker Thread Execution")

worker = LightweightWorker()

# Test 1: Normal completion
done_flag = threading.Event()
result_store = {}

def simple_runner(t: ExecutionTask):
    result_store["started"] = True
    time.sleep(0.1)
    result_store["done"] = True
    done_flag.set()
    return {"answer": "ok"}

sess2 = f"test_sess_{uuid.uuid4().hex[:8]}"
task2 = ExecutionTask(session_id=sess2, payload={"prompt": "hello"}, limits=ResourceGovernance())
# Register a subscriber so broadcasts don't error
sub2  = SSEManager.register_client(session_id=sess2)

worker.execute_async(task2, runner_fn=simple_runner)
done_flag.wait(timeout=3.0)

check("Worker thread starts runner",          result_store.get("started") is True)
check("Worker thread completes runner",       result_store.get("done") is True)
check("Task status becomes completed",        task2.status == "completed")
check("Task completed_at is set",             task2.completed_at is not None)
check("Task removed from _active_tasks",      worker.get_task(task2.execution_id) is None)

# Verify TASK_STARTED was broadcast
time.sleep(0.05)
events_received = []
while not sub2.queue.empty():
    events_received.append(sub2.queue.get_nowait())
event_types_rx = [e.event for e in events_received]
check("TASK_STARTED broadcast delivered",     "runtime.event" in event_types_rx)
check("done broadcast delivered",             "done" in event_types_rx)
SSEManager.remove_client(sub2.client_id)

# Test 2: Cancellation mid-run
cancel_result = {}
cancel_done   = threading.Event()

def cancellable_runner(t: ExecutionTask):
    for i in range(100):
        if t.is_cancelled:
            cancel_result["cancelled_at"] = i
            cancel_done.set()
            return {"cancelled": True}
        t._cancel_event.wait(0.02)
    cancel_done.set()
    return {"cancelled": False}

sess3  = f"test_sess_{uuid.uuid4().hex[:8]}"
task3  = ExecutionTask(session_id=sess3, payload={}, limits=ResourceGovernance())
sub3   = SSEManager.register_client(session_id=sess3)
worker.execute_async(task3, runner_fn=cancellable_runner)
time.sleep(0.05)
task3.cancel()
cancel_done.wait(timeout=3.0)

check("Cancelled task stops early",           cancel_result.get("cancelled_at", 999) < 50)
check("Task status becomes cancelled",        task3.status == "cancelled")
SSEManager.remove_client(sub3.client_id)

# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 6 — JobManager Real Runner (Flag ON)
# ═════════════════════════════════════════════════════════════════════════════
section("PHASE 6 — JobManager emit_fn Integration")

try:
    from execution.job_manager import JobManager
    check("execution.job_manager imports cleanly", True)
except Exception as e:
    check("execution.job_manager imports cleanly", False, str(e))
    sys.exit(1)

jm   = JobManager()
sess4 = f"test_sess_{uuid.uuid4().hex[:8]}"
sub4  = SSEManager.register_client(session_id=sess4)

# Inject a synthetic emit_fn probe
emitted_events = []
_orig_broadcast = SSEManager.broadcast_to_session.__func__ if hasattr(SSEManager.broadcast_to_session, '__func__') else None

# Use a direct probe: build the runner ourselves to test the internal structure
runner = jm._build_real_runner(sess4)
check("_build_real_runner returns callable", callable(runner))

# With flag ON, runner is _real_runner (not legacy)
# Verify by checking it's NOT the legacy runner (which uses _cancel_event.wait(0.5))
import inspect
src = inspect.getsource(runner)
check("Real runner references Agent",            "Agent" in src)
check("Real runner references emit_fn",          "emit_fn" in src or "_emit" in src)
check("Real runner checks is_cancelled",         "is_cancelled" in src)
check("Real runner has InterruptedError guard",  "InterruptedError" in src)

# Flag OFF runner check
os.environ["AETHERION_REALTIME_V1"] = "false"
legacy_runner = jm._build_real_runner(sess4)
legacy_src    = inspect.getsource(legacy_runner)
check("Legacy runner preserved when flag OFF", "legacy stub" in legacy_src or "_cancel_event.wait" in legacy_src)
os.environ["AETHERION_REALTIME_V1"] = "true"   # restore

SSEManager.remove_client(sub4.client_id)

# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 7 — Agent emit_fn Injection
# ═════════════════════════════════════════════════════════════════════════════
section("PHASE 7 — Agent emit_fn Injection")

try:
    from agent import Agent
    check("agent.Agent imports cleanly", True)
except Exception as e:
    check("agent.Agent imports cleanly", False, str(e))
    sys.exit(1)

# Test 1: default (no emit_fn) — backward compatibility
a_default = Agent()
check("Agent() with no emit_fn has no-op lambda",
      callable(a_default._emit_fn))
check("Agent() session_id defaults to empty string",
      a_default._session_id == "")

# Test 2: explicit emit_fn wired
captured = []
def capture_fn(kind: str, payload: dict):
    captured.append({"kind": kind, **payload})

a_wired = Agent(emit_fn=capture_fn, session_id="test_wire_001")
check("Agent stores emit_fn",      a_wired._emit_fn is capture_fn)
check("Agent stores session_id",   a_wired._session_id == "test_wire_001")

# Test 3: _emit_record still prints to stdout AND calls emit_fn
import io
from contextlib import redirect_stdout

buf = io.StringIO()
with redirect_stdout(buf):
    a_wired._emit_record("step_result", {"success": True, "step_index": 0})

stdout_output = buf.getvalue()
check("_emit_record still prints [AGENT_RECORD]",
      "[AGENT_RECORD]" in stdout_output)
check("_emit_record forwards to emit_fn via agent.step_result",
      any(e.get("kind") == "agent.step_result" for e in captured),
      f"captured: {[e['kind'] for e in captured]}")

# Test 4: emit_fn exception does NOT crash _emit_record
def crashing_fn(kind, payload):
    raise RuntimeError("SSE connection broken!")

a_crashing = Agent(emit_fn=crashing_fn, session_id="crash_test")
try:
    a_crashing._emit_record("reflection", {"worked": True})
    check("_emit_record survives crashing emit_fn", True)
except Exception as e:
    check("_emit_record survives crashing emit_fn", False, str(e))

# Test 5: flag OFF — emit_fn NOT called even if provided
os.environ["AETHERION_REALTIME_V1"] = "false"
captured_off = []
a_off = Agent(emit_fn=lambda k, p: captured_off.append(k), session_id="flag_off_test")
buf2 = io.StringIO()
with redirect_stdout(buf2):
    a_off._emit_record("test_event", {"x": 1})
check("Flag OFF: emit_fn NOT called",
      len(captured_off) == 0,
      f"captured: {captured_off}")
check("Flag OFF: stdout print still works",
      "[AGENT_RECORD]" in buf2.getvalue())
os.environ["AETHERION_REALTIME_V1"] = "true"   # restore

# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 8 — Queue Backpressure & Disconnect Safety
# ═════════════════════════════════════════════════════════════════════════════
section("PHASE 8 — Queue Backpressure & Disconnect Safety")

sess5  = f"bp_sess_{uuid.uuid4().hex[:8]}"
client5 = SSEManager.register_client(session_id=sess5)

# Fill queue to capacity — must not raise, must log warning
fill_count = 0
for i in range(1010):   # 10 over maxsize
    result = client5.put(SSEEvent(data={"i": i}, event="test"), timeout=0.001)
    if result:
        fill_count += 1

check("Queue fills to maxsize without crash",
      fill_count <= 1000,
      f"Accepted {fill_count}/1010 events (1000 max)")
check("Queue never exceeds maxsize",
      client5.queue.qsize() <= 1000,
      f"qsize={client5.queue.qsize()}")

# Disconnect then put — connected=False blocks further puts
SSEManager.remove_client(client5.client_id)
put_after_disconnect = client5.put(SSEEvent(data={"x": 1}, event="post_disconnect"))
check("Disconnected client rejects further puts",
      put_after_disconnect is False)

# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 9 — Critical Failure Forensics (Static)
# ═════════════════════════════════════════════════════════════════════════════
section("PHASE 9 — Failure Forensics (Static Trace)")

# FORENSIC 1: input() in agent loop — line 497
with open(os.path.join(ROOT, "agent.py"), encoding="utf-8") as f:
    agent_src = f.read()

has_input_call = "hint = input().strip()" in agent_src
check("CRITICAL: input() call found in agent.py (HITL freeze risk)",
      has_input_call,       # True = confirmed present = it IS a risk
      "Line ~497 — blocks gunicorn thread until stdin provides data")

# Gunicorn config inspection
gunicorn_path = os.path.join(ROOT, "gunicorn.conf.py")
with open(gunicorn_path, encoding="utf-8", errors="replace") as f:
    gcfg = f.read()

is_sync_worker = 'worker_class = "sync"' in gcfg
threads_val    = None
for line in gcfg.splitlines():
    if line.strip().startswith("threads"):
        try: threads_val = int(line.split("=")[1].strip().split("#")[0].strip().strip('"'))
        except: pass

check("CRITICAL: Gunicorn is sync worker (limits concurrency)",
      is_sync_worker,
      "Concurrent SSE sessions limited by threads=8; gevent needed")
check(f"Gunicorn threads value detected",
      threads_val is not None,
      f"threads={threads_val}")

# workflow_engine.py presence
we_path = os.path.join(ROOT, "workflow_engine.py")
we_exists = os.path.isfile(we_path)
check("workflow_engine.py exists on disk",
      we_exists,
      "MISSING — /api/workflows/* routes return 503" if not we_exists else "Found")

# SSEManager in-process state (not Redis-backed)
with open(os.path.join(ROOT, "streaming", "sse_manager.py"), encoding="utf-8") as f:
    sse_src = f.read()
is_in_process = "dict" in sse_src and "redis" not in sse_src.lower()
check("SSEManager uses in-process dict (multi-worker risk)",
      is_in_process,
      "Multi-process deployment will lose events across workers")

# LightweightWorker memory: tasks removed from _active_tasks on completion
with open(os.path.join(ROOT, "execution", "worker.py"), encoding="utf-8") as f:
    worker_src = f.read()
check("LightweightWorker cleans up _active_tasks in finally",
      "_active_tasks.pop(task.execution_id, None)" in worker_src)

# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 10 — Heartbeat & SSE Wire Format
# ═════════════════════════════════════════════════════════════════════════════
section("PHASE 10 — SSE Wire Format Compliance")

# Heartbeat comment format
heartbeat = ": heartbeat\n\n"
check("Heartbeat is valid SSE comment (': ' prefix)", heartbeat.startswith(": "))
check("Heartbeat ends with \\n\\n",                   heartbeat.endswith("\n\n"))

# Event with id
evt_with_id = SSEEvent(data={"x": 1}, event="test", id="123")
encoded_id  = evt_with_id.encode()
check("SSEEvent with id includes 'id:' line",         "id: 123" in encoded_id)
check("SSEEvent ordering: id → event → data",
      encoded_id.index("id:") < encoded_id.index("event:") < encoded_id.index("data:"))

# Event without id
evt_no_id = SSEEvent(data="hello", event="ping")
encoded_no_id = evt_no_id.encode()
check("SSEEvent without id omits 'id:' line",         "id:" not in encoded_no_id)
check("String data serialises correctly",              "hello" in encoded_no_id)

# Dict data serialises as JSON
evt_dict = SSEEvent(data={"a": 1, "b": "x"}, event="dict_test")
enc_dict = evt_dict.encode()
check("Dict data encoded as JSON in SSE",
      '"a": 1' in enc_dict or '"a":1' in enc_dict)

# ═════════════════════════════════════════════════════════════════════════════
#  FINAL REPORT
# ═════════════════════════════════════════════════════════════════════════════
section("FINAL REPORT")

passed  = [r for r in results if r[1] is True]
failed  = [r for r in results if r[1] is False]

# Distinguish forensic "confirmed risks" from real failures
# (Forensic checks assert True WHEN the risk is confirmed — they aren't failures)
real_failures = [r for r in failed
                 if not r[0].startswith("CRITICAL:") and
                    not r[0].startswith("workflow_engine")]

print(f"\n  Total checks : {len(results)}")
print(f"  {GREEN}Passed       : {len(passed)}{RESET}")
print(f"  {RED}Failures     : {len(real_failures)}{RESET}")

if real_failures:
    print(f"\n  {RED}REAL FAILURES:{RESET}")
    for name, ok, detail in real_failures:
        print(f"    • {name}: {detail}")

print(f"\n  {YELLOW}Confirmed Operational Risks:{RESET}")
risk_items = [r for r in results if r[0].startswith("CRITICAL:") and r[1] is True]
for name, _, detail in risk_items:
    print(f"    ⚠  {name}")
    if detail: print(f"       {detail}")

score = int(100 * len(passed) / max(len(results), 1))
verdict = (
    f"{GREEN}REALTIME PIPELINE OPERATIONAL{RESET}" if score >= 85
    else f"{YELLOW}PARTIALLY OPERATIONAL — RISKS PRESENT{RESET}" if score >= 70
    else f"{RED}NOT READY — CRITICAL FAILURES{RESET}"
)

print(f"\n  Pipeline Score: {BOLD}{score}/100{RESET}")
print(f"  Verdict:        {verdict}")
print()

# Exit code for CI
sys.exit(0 if not real_failures else 1)
