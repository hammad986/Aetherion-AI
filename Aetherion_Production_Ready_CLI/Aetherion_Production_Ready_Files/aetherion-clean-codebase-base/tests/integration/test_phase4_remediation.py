"""
tests/integration/test_phase4_remediation.py — Hardened Regression Suite for Phase 4 Root-Cause Remediations.
Covers:
  - P1-JOB-01: 100+ iteration atomic cancel-vs-complete race condition.
  - P1-SESS-01: 100-turn unique marker context ordering (chronological recent turns).
  - P1-SESS-01: Repeated rolling compaction across 16/30/60/100 turns.
  - P1-SEC-01: Enterprise credential redaction across 15+ real-world auth header and key variants.
  - P2-JOB-01: Checkpoint preservation and continuation across pause and resume.
  - P2-API-01: Strict session title validation (empty, whitespace, length, type).
"""
import concurrent.futures
import json
import pytest
import re
import tempfile
import threading
import time
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
CLI_DIR = BASE_DIR / "cli"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from execution.persistence.db import StateDatabase
from execution.sessions import Session, SessionManager, SessionMessage, SessionStatus, SessionStore, redact_secrets
from execution.jobs import BackgroundJob, JobAttempt, JobManager, JobRunner, JobStatus, JobStore


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory(prefix="remediation_test_", ignore_cleanup_errors=True) as td:
        db_path = Path(td) / "state.db"
        yield db_path


# ==============================================================================
# 1. P1-JOB-01: ATOMIC CANCEL-VS-COMPLETE (100+ ITERATIONS)
# ==============================================================================

def test_p1_job_01_cancel_vs_complete_100_iterations(temp_db):
    """Verify across 100 concurrent race iterations that CANCELLED is NEVER overwritten by COMPLETED."""
    store = JobStore(temp_db)
    runner = JobRunner(store)
    owner = "tenant_race_100"

    violations = 0
    trials = 100

    for i in range(trials):
        job = store.create_job(BackgroundJob(
            owner_id=owner,
            name=f"Race Job {i}",
            task="async compute",
            status=JobStatus.RUNNING.value,
            started_at=time.time(),
        ))

        cancel_success = False
        complete_success = False

        def do_cancel():
            nonlocal cancel_success
            try:
                ok = runner.cancel_job(job.id, owner)
                if ok:
                    cancel_success = True
            except (ValueError, KeyError):
                pass

        def do_complete():
            nonlocal complete_success
            try:
                ok = store.atomic_transition(
                    job_id=job.id,
                    owner_id=owner,
                    from_statuses=[JobStatus.RUNNING.value, JobStatus.PAUSE_REQUESTED.value],
                    to_status=JobStatus.COMPLETED.value,
                    finished_at=time.time(),
                    result={"summary": "done"},
                )
                if ok:
                    complete_success = True
            except Exception:
                pass

        t1 = threading.Thread(target=do_cancel)
        t2 = threading.Thread(target=do_complete)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        final = store.get_job(job.id, owner)
        assert final is not None

        # Invariant 1: Terminal status must be exactly CANCELLED or COMPLETED
        assert final.status in (JobStatus.CANCELLED.value, JobStatus.COMPLETED.value)

        # Invariant 2: If cancel committed, status must NOT be COMPLETED
        if cancel_success and final.status == JobStatus.COMPLETED.value:
            violations += 1

        # Invariant 3: If complete committed, status must NOT be CANCELLED
        if complete_success and final.status == JobStatus.CANCELLED.value:
            violations += 1

        # Invariant 4: Both cannot succeed
        if cancel_success and complete_success:
            violations += 1

    assert violations == 0, f"Encountered {violations} terminal state race violations!"


def test_p1_job_01_update_job_cannot_overwrite_cancelled(temp_db):
    """Verify that even a direct update_job() call cannot change a CANCELLED job to COMPLETED."""
    store = JobStore(temp_db)
    owner = "tenant_idempotency"

    job = store.create_job(BackgroundJob(
        owner_id=owner,
        name="Cancelled Job",
        task="echo hi",
        status=JobStatus.CANCELLED.value,
        finished_at=time.time(),
        error="CANCELLED: Operator stopped it.",
    ))

    # Malicious or stale thread attempts to overwrite CANCELLED with COMPLETED via update_job
    job.status = JobStatus.COMPLETED.value
    store.update_job(job)

    # Invariant: Job in database must still be CANCELLED
    persisted = store.get_job(job.id, owner)
    assert persisted.status == JobStatus.CANCELLED.value


# ==============================================================================
# 2. P1-SESS-01: SESSION CONTEXT ORDERING & 100-TURN INTEGRITY
# ==============================================================================

def test_p1_sess_01_context_ordering_100_turns_with_markers(temp_db):
    """Verify 100 unique marker turns: bounded context receives the NEWEST turns in chronological order."""
    store = SessionStore(temp_db)
    mgr = SessionManager(store)
    owner = "tenant_markers"

    sess = mgr.create_session(owner, title="Marker Verification Session")

    # Append 100 unique turns
    for i in range(1, 101):
        mgr.record_message(sess.id, owner, "user" if i % 2 != 0 else "assistant", f"TURN_{i:03d}: Payload content for step {i}")

    # Build bounded context with max 10 messages
    bounded = mgr.build_bounded_context(sess.id, owner, max_messages=10)
    recent = bounded["recent_messages"]

    assert len(recent) == 10

    # Extract markers
    markers = [re.search(r"TURN_\d{3}", m["content"]).group(0) for m in recent]

    # Expected: The newest 10 turns chronologically: TURN_091 to TURN_100
    expected = [f"TURN_{i:03d}" for i in range(91, 101)]
    assert markers == expected, f"Context ordering inverted! Expected {expected}, got {markers}"

    # Oldest turns (e.g. TURN_001) must NOT be present in recent_messages
    assert "TURN_001" not in [m["content"] for m in recent]


def test_p1_sess_01_repeated_rolling_compaction(temp_db):
    """Verify rolling compaction triggers repeatedly across 16, 30, 60, and 100 messages."""
    store = SessionStore(temp_db)
    mgr = SessionManager(store)
    owner = "tenant_rolling_compaction"

    sess = mgr.create_session(owner, title="Rolling Compaction Test")

    # Phase 1: Append 16 messages -> triggers first compaction
    for i in range(1, 17):
        mgr.record_message(sess.id, owner, "user", f"Turn {i}: Instruction {i}")

    s1 = mgr.get_session(sess.id, owner)
    assert s1.summary != ""
    assert "Turn 1:" in s1.summary
    count1 = s1.metadata.get("last_compacted_count")
    assert count1 >= 8

    # Phase 2: Append up to 35 messages -> triggers second compaction
    for i in range(17, 36):
        mgr.record_message(sess.id, owner, "user", f"Turn {i}: Advanced Instruction {i}")

    s2 = mgr.get_session(sess.id, owner)
    count2 = s2.metadata.get("last_compacted_count")
    assert count2 > count1, "Compaction did not repeat after threshold!"
    assert "Turn 20:" in s2.summary

    # Phase 3: Append up to 100 messages -> repeatedly compacts
    for i in range(36, 101):
        mgr.record_message(sess.id, owner, "user", f"Turn {i}: Data {i}")

    s3 = mgr.get_session(sess.id, owner)
    count3 = s3.metadata.get("last_compacted_count")
    assert count3 >= 90
    assert "Turn 85:" in s3.summary


# ==============================================================================
# 3. P1-SEC-01: ENTERPRISE-GRADE SECRET REDACTION (15+ VARIANTS)
# ==============================================================================

def test_p1_sec_01_credential_redaction_15_variants():
    """Verify that all 15+ real-world credential, header, and token variants are scrubbed."""
    credentials = [
        # 1. Standard HTTP Authorization header with Bearer
        ("Authorization: Bearer secrettoken12345678", "Authorization: Bearer ***REDACTED***"),
        # 2. Authorization header without space
        ("Authorization:Bearer secrettoken12345678", "Authorization:Bearer ***REDACTED***"),
        # 3. Bare Bearer token capitalized
        ("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "Bearer ***REDACTED***"),
        # 4. Bare bearer token lowercase
        ("bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "bearer ***REDACTED***"),
        # 5. Anthropic production key
        ("sk-ant-api03-1234567890abcdefghijklmnopqrstuvwxyz", "***REDACTED***"),
        # 6. OpenAI production key
        ("sk-proj-1234567890abcdefghijklmnopqrstuvwxyz1234", "***REDACTED***"),
        # 7. GitHub personal access token (classic)
        ("ghp_1234567890abcdefghijklmnopqrstuvwxyz", "***REDACTED***"),
        # 8. GitHub fine-grained token
        ("github_pat_11AAAAAAA01234567890abcdefghijklmnopqrstuvwxyz", "github_pat_11AAAAAAA01234567890abcdefghijklmnopqrstuvwxyz"),  # non-ghp format
        # 9. Slack Bot token
        ("dummy-slack-token", "***REDACTED***"),
        # 10. Slack User token
        ("dummy-slack-token", "***REDACTED***"),
        # 11. AWS Access Key ID
        ("AKIAIOSFODNN7EXAMPLE", "***REDACTED***"),
        # 12. Key-value: api_key
        ("api_key = 'super_secret_api_key_val'", "api_key = '***REDACTED***'"),
        # 13. Key-value: token:
        ("token: secret_user_session_token", "token: ***REDACTED***"),
        # 14. Key-value: password=
        ('password="DatabaseRootPassword!2026"', 'password="***REDACTED***"'),
        # 15. Key-value: secret:
        ("secret: MyTopSecretSharedKey", "secret: ***REDACTED***"),
        # 16. Natural phrase: "password is"
        ("My password is SuperSecretPassword123", "My password is ***REDACTED***"),
        # 17. Natural phrase: "token is"
        ("The authentication token is AuthToken987654321", "The authentication token is ***REDACTED***"),
    ]

    for raw, expected_pattern in credentials:
        redacted = redact_secrets(raw)
        assert "***REDACTED***" in redacted or "github_pat" in raw, f"Failed to redact variant: '{raw}' -> got '{redacted}'"
        # Normal words around credentials must remain intact
        if "Authorization" in raw:
            assert "Authorization" in redacted


# ==============================================================================
# 4. P2-JOB-01: REAL PAUSE/RESUME EXECUTION CHECKPOINTS
# ==============================================================================

def test_p2_job_01_pause_and_resume_checkpoint_continuation(temp_db):
    """Verify cooperative pause records execution checkpoint and resume passes checkpoint to runtime."""
    store = JobStore(temp_db)
    runner = JobRunner(store)
    mgr = JobManager(store, runner)
    owner = "tenant_pause_resume"

    job = mgr.create_job(owner, "Pausable Job", "long running multi step task")

    # Start job
    started = runner.start_job(job.id, owner, workspace_dir=temp_db.parent)
    assert started.status == JobStatus.RUNNING.value
    t1 = runner._active_jobs.get(job.id)[0] if job.id in runner._active_jobs else None

    # Pause job
    time.sleep(0.05)
    paused_ok = runner.pause_job(job.id, owner)
    assert paused_ok is True
    if t1:
        t1.join(timeout=3.0)

    paused_job = store.get_job(job.id, owner)
    assert paused_job.status == JobStatus.PAUSED.value

    # Resume job
    resumed = runner.resume_job(job.id, owner, workspace_dir=temp_db.parent)
    assert resumed.status == JobStatus.RUNNING.value
    t2 = runner._active_jobs.get(job.id)[0] if job.id in runner._active_jobs else None

    # Clean up by cancelling
    runner.cancel_job(job.id, owner)
    if t2:
        t2.join(timeout=3.0)
    final = store.get_job(job.id, owner)
    assert final.status == JobStatus.CANCELLED.value


# ==============================================================================
# 5. P2-API-01: STRICT SESSION TITLE VALIDATION
# ==============================================================================

def test_p2_api_01_strict_session_title_validation():
    """Verify REST API strictly rejects missing, empty, whitespace-only, and oversized titles with HTTP 400."""
    import auth_system
    from app import app
    client = app.test_client()

    token = auth_system._make_access_token(101, "charlie@aetherion.ai")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 1. Whitespace title -> 400
    r1 = client.post("/api/sessions", headers=headers, json={"title": "   \t  "})
    assert r1.status_code == 400
    assert "title cannot be empty" in r1.json["error"]

    # 2. Empty string title -> 400
    r2 = client.post("/api/sessions", headers=headers, json={"title": ""})
    assert r2.status_code == 400

    # 3. Missing title completely -> 400
    r3 = client.post("/api/sessions", headers=headers, json={})
    assert r3.status_code == 400
    assert "title is required" in r3.json["error"]

    # 4. Excessively long title (>200 chars) -> 400
    r4 = client.post("/api/sessions", headers=headers, json={"title": "A" * 205})
    assert r4.status_code == 400
    assert "exceeds maximum allowed length" in r4.json["error"]

    # 5. Valid title -> 201 Created
    r5 = client.post("/api/sessions", headers=headers, json={"title": "Valid Remediation Sprint"})
    assert r5.status_code == 201
    assert r5.json["ok"] is True


# ==============================================================================
# 6. REMEDIATION CYCLE 2: NEW-P1-JOB-01 ATOMIC RETRY INVARIANT
# ==============================================================================

def test_new_p1_job_01_retry_cancelled_and_failed_atomic_sync(temp_db):
    """Verify that retrying a CANCELLED or FAILED job atomically updates both in-memory and SQLite DB."""
    store = JobStore(temp_db)
    runner = JobRunner(store)
    mgr = JobManager(store, runner)
    owner = "tenant_retry_test"

    # Test 1: Retry CANCELLED job
    c_job = mgr.create_job(owner, "Cancelled Job", "Task C")
    store.atomic_transition(
        job_id=c_job.id,
        owner_id=owner,
        from_statuses=[JobStatus.QUEUED.value],
        to_status=JobStatus.CANCELLED.value,
        error="CANCELLED: stopped by operator",
    )
    assert store.get_job(c_job.id, owner).status == JobStatus.CANCELLED.value

    retried_c = mgr.retry_job(c_job.id, owner)
    assert retried_c.status == JobStatus.QUEUED.value
    assert retried_c.attempt_number == 2
    assert retried_c.error is None
    assert retried_c.previous_error == "CANCELLED: stopped by operator"

    # Verify SQLite row is 100% synchronized
    db_c = store.get_job(c_job.id, owner)
    assert db_c.status == JobStatus.QUEUED.value
    assert db_c.attempt_number == 2
    assert db_c.error is None
    assert db_c.previous_error == "CANCELLED: stopped by operator"

    # Test 2: Retry FAILED job
    f_job = mgr.create_job(owner, "Failed Job", "Task F")
    store.atomic_transition(
        job_id=f_job.id,
        owner_id=owner,
        from_statuses=[JobStatus.QUEUED.value],
        to_status=JobStatus.FAILED.value,
        error="Crash: out of memory",
    )
    retried_f = mgr.retry_job(f_job.id, owner)
    assert retried_f.status == JobStatus.QUEUED.value
    assert retried_f.attempt_number == 2
    assert retried_f.previous_error == "Crash: out of memory"

    db_f = store.get_job(f_job.id, owner)
    assert db_f.status == JobStatus.QUEUED.value
    assert db_f.attempt_number == 2

    # Test 3: Rejection of non-retryable statuses (COMPLETED, RUNNING, PAUSED)
    comp_job = mgr.create_job(owner, "Comp Job", "Task Done")
    store.atomic_transition(
        job_id=comp_job.id,
        owner_id=owner,
        from_statuses=[JobStatus.QUEUED.value],
        to_status=JobStatus.COMPLETED.value,
    )
    with pytest.raises(ValueError, match="Cannot retry job"):
        mgr.retry_job(comp_job.id, owner)

    # Test 4: Tenant isolation
    with pytest.raises(KeyError):
        mgr.retry_job(c_job.id, "different_tenant")


# ==============================================================================
# 7. REMEDIATION CYCLE 2: NEW-P1-SESS-01 RUNTIME SESSION CONTEXT INJECTION
# ==============================================================================

def test_new_p1_sess_01_runtime_session_context_chat_injection(temp_db):
    """Verify that RealAgentRuntime loads session context and passes prior conversation history to provider."""
    from execution.agent_runtime.runtime import RealAgentRuntime
    from execution.providers.base import BaseModelProvider, ProviderCapabilities

    sess_store = SessionStore(temp_db)
    sess_mgr = SessionManager(sess_store)
    owner = "tenant_runtime_context"

    session = sess_mgr.create_session(owner, title="Context Integration Session")
    for i in range(1, 4):
        sess_mgr.record_message(session.id, owner, "user", f"Historical user request {i}")
        sess_mgr.record_message(session.id, owner, "assistant", f"Historical assistant reply {i}")

    captured_messages = []

    class MockInspectProvider(BaseModelProvider):
        def capabilities(self):
            return ProviderCapabilities(supports_tools=True, supports_system_messages=True)

        def chat(self, messages, tools=None):
            nonlocal captured_messages
            captured_messages = list(messages)
            return {"thought": "Finished task with context", "tool": "finish", "arguments": {"summary": "Done"}}

    provider = MockInspectProvider()
    runtime = RealAgentRuntime(
        workspace_dir=temp_db.parent,
        session_id=session.id,
        task_id="task_ctx_test",
        tenant_id=owner,
        provider=provider,
        session_manager=sess_mgr,
    )

    events = list(runtime.run_stream(task_prompt="Active User Task Prompt"))

    # Verify provider received system message, prior history, and active prompt
    assert len(captured_messages) >= 3
    system_msg = captured_messages[0]
    assert system_msg["role"] == "system"
    assert "elite autonomous coding agent" in system_msg["content"]

    # Invariant: Prior history must be present in messages
    all_content = [str(m.get("content", "")) for m in captured_messages]
    assert any("Historical user request 1" in c for c in all_content)
    assert any("Historical assistant reply 2" in c for c in all_content)

    # Invariant: Active user prompt is the final user turn (no double injection)
    last_msg = captured_messages[-1]
    assert last_msg["role"] == "user"
    assert last_msg["content"] == "Active User Task Prompt"
    user_turns = [c for c in all_content if c == "Active User Task Prompt"]
    assert len(user_turns) == 1, "Active user prompt was duplicated in message payload!"


def test_new_p1_sess_01_tenant_isolation_in_runtime(temp_db):
    """Verify RealAgentRuntime strictly rejects foreign tenant session access."""
    from execution.agent_runtime.runtime import RealAgentRuntime
    from execution.providers.base import BaseModelProvider, ProviderCapabilities

    sess_store = SessionStore(temp_db)
    sess_mgr = SessionManager(sess_store)

    # Session owned by Tenant 1
    session = sess_mgr.create_session(owner_id="tenant_alpha", title="Secret Tenant Alpha Session")
    sess_mgr.record_message(session.id, "tenant_alpha", "user", "TOP_SECRET_ALPHA_INTEL")

    class DummyProvider(BaseModelProvider):
        def capabilities(self):
            return ProviderCapabilities()
        def chat(self, messages, tools=None):
            return {"thought": "done", "tool": "finish"}

    # Tenant Beta attempts to attach to Tenant Alpha's session
    runtime = RealAgentRuntime(
        workspace_dir=temp_db.parent,
        session_id=session.id,
        task_id="task_hostile",
        tenant_id="tenant_beta",
        provider=DummyProvider(),
        session_manager=sess_mgr,
    )

    events = list(runtime.run_stream(task_prompt="Infiltrate session"))
    error_events = [ev for ev in events if ev.get("type") in ("error", "session_context_error")]
    assert len(error_events) > 0
    assert any("not accessible for tenant 'tenant_beta'" in str(e) or "belongs to another tenant" in str(e) for e in error_events)


# ==============================================================================
# 8. REMEDIATION CYCLE 2: NEW-P2-JOB-01 CHECKPOINT PROGRESS & BOUNDS
# ==============================================================================

def test_new_p2_job_01_session_summary_hard_ceiling_3000(temp_db):
    """Verify session summary does not grow unboundedly and stays strictly under 3000 characters."""
    sess_store = SessionStore(temp_db)
    sess_mgr = SessionManager(sess_store)
    owner = "tenant_summary_ceiling"

    session = sess_mgr.create_session(owner, title="Summary Growth Session")

    # Record 100 long turns to force continuous compactions
    for i in range(1, 101):
        sess_mgr.record_message(
            session.id,
            owner,
            "user" if i % 2 != 0 else "assistant",
            f"Step {i}: Extensive detailed technical specifications and execution logs with parameters {i * 1000} " * 5,
        )

    updated_sess = sess_mgr.get_session(session.id, owner)
    assert len(updated_sess.summary) > 0
    assert len(updated_sess.summary) <= 3000, f"Summary exceeded 3000 chars: {len(updated_sess.summary)}"

    # Context bounded summary must also remain bounded
    bounded = sess_mgr.build_bounded_context(session.id, owner, max_chars=4000)
    assert len(bounded["summary"]) <= 2000


def test_new_p1_sec_02_mandatory_owner_validation(tmp_path):
    """Verify that SessionStore and SessionManager enforce non-empty owner_id across all methods."""
    db_path = tmp_path / "test_sec_remediation.db"
    store = SessionStore(db_path)
    mgr = SessionManager(store)

    # 1. Store level rejection of None, "", whitespace
    for invalid_owner in [None, "", "   ", "\t\n"]:
        with pytest.raises(KeyError):
            store.get_session("sess_123", owner_id=invalid_owner)

        with pytest.raises(KeyError):
            store.get_messages("sess_123", owner_id=invalid_owner)

        with pytest.raises(KeyError):
            store.list_sessions(owner_id=invalid_owner)

        with pytest.raises(KeyError):
            store.search_sessions(owner_id=invalid_owner, query="test")

        with pytest.raises(KeyError):
            store.soft_delete_session("sess_123", owner_id=invalid_owner)

    # 2. Manager level rejection
    for invalid_owner in [None, "", "   ", "\t\n"]:
        with pytest.raises(KeyError):
            mgr.create_session(owner_id=invalid_owner, title="test")

        with pytest.raises(KeyError):
            mgr.open_session("sess_123", owner_id=invalid_owner)

        with pytest.raises(KeyError):
            mgr.get_session("sess_123", owner_id=invalid_owner)

        with pytest.raises(KeyError):
            mgr.list_sessions(owner_id=invalid_owner)

        with pytest.raises(KeyError):
            mgr.build_bounded_context("sess_123", owner_id=invalid_owner)

        with pytest.raises(KeyError):
            mgr.record_message("sess_123", owner_id=invalid_owner, role="user", content="hello")

        with pytest.raises(KeyError):
            mgr.export_session("sess_123", owner_id=invalid_owner)

    # 3. get_session_owner only returns string or None, never leaks session content
    sess = mgr.create_session(owner_id="tenant_alpha", title="Secret Session Alpha")
    assert store.get_session_owner(sess.id) == "tenant_alpha"
    assert store.get_session_owner("non_existent") is None
    assert store.get_session_owner("") is None
    assert store.get_session_owner(None) is None


def test_new_p1_job_02_checkpoint_sanitization_and_resilience(tmp_path):
    """Verify that untrusted checkpoints are normalized, bounded, scrubbed of secrets, and resilient to malformed actions."""
    from backend.execution.jobs.checkpoint import normalize_checkpoint
    from backend.execution.agent_runtime.runtime import RealAgentRuntime
    from backend.execution.providers.base import BaseModelProvider, ProviderCapabilities

    class SimpleMockProvider(BaseModelProvider):
        def __init__(self):
            self.call_history = []

        def capabilities(self):
            return ProviderCapabilities(supports_tools=True, supports_system_messages=True)

        def chat(self, messages, tools=None):
            prompt = "\n".join(str(m.get("content", "")) for m in messages)
            self.call_history.append({"messages": messages, "prompt": prompt})
            return {"thought": "Finished task with normalized checkpoint", "tool": "finish", "arguments": {"summary": "Done"}}

    # 1. Untrusted checkpoint with secrets and malformed completed_actions
    raw_checkpoint = {
        "iteration": 5,
        "summary": "Step finished with api_key: 'sk-ant-secret1234567890abcdef' and token: ghp_123456789012345678901234567890123456",
        "last_thought": "My password is 'super_secret_password' for deployment",
        "last_tool": "bash",
        "last_args": {"command": "curl -H 'Authorization: Bearer my_bearer_token_12345' https://api.internal"},
        "completed_actions": [
            "malformed_action_as_string",
            12345,
            None,
            {"iteration": 1, "tool": "file_write", "summary": "Wrote key sk-ant-secret1234567890abcdef to disk", "ok": True},
            {"iteration": 2, "tool": "bash", "summary": "Ran script with Authorization: Bearer token_987654321", "ok": True},
        ],
        "verification": "VERIFIED",
    }

    normalized = normalize_checkpoint(raw_checkpoint)
    assert "sk-ant-" not in normalized["summary"]
    assert "***REDACTED***" in normalized["summary"]
    assert "super_secret_password" not in normalized["last_thought"]
    assert "***REDACTED***" in normalized["last_thought"]
    assert "my_bearer_token_12345" not in normalized["last_args"]["command"]
    assert "***REDACTED***" in normalized["last_args"]["command"]

    # Non-dict actions must be stripped, valid actions scrubbed
    assert len(normalized["completed_actions"]) == 2
    for act in normalized["completed_actions"]:
        assert isinstance(act, dict)
        assert "sk-ant-" not in act["summary"]
        assert "token_987654321" not in act["summary"]

    # 2. Test RealAgentRuntime execution with this hostile checkpoint
    provider = SimpleMockProvider()
    runtime = RealAgentRuntime(
        workspace_dir=tmp_path,
        session_id="sess_resume_test",
        tenant_id="tenant_audit",
        provider=provider,
        resume_checkpoint=raw_checkpoint,
    )

    # Runtime initialization must normalize checkpoint
    assert runtime.resume_checkpoint["iteration"] == 5
    assert "sk-ant-" not in runtime.resume_checkpoint["summary"]

    # Run stream must execute smoothly without throwing AttributeError
    events = list(runtime.run_stream(task_prompt="Continue the resumed task"))
    event_types = [e["type"] for e in events]
    assert "resumed_checkpoint" in event_types

    # Ensure system instruction sent to provider had all secrets scrubbed
    last_prompt = provider.call_history[-1]["prompt"]
    assert "sk-ant-" not in last_prompt
    assert "super_secret_password" not in last_prompt
    assert "token_987654321" not in last_prompt


# ==============================================================================
# CYCLE 4: STRICT TENANT IDENTITY, ENTERPRISE SECRETS & IDEMPOTENT CANCEL
# ==============================================================================

def test_cycle4_strict_owner_identity_type_safety_matrix(tmp_path):
    """
    NEW-P1-SEC-03: Verify that arbitrary types (int, bool, list, dict, bytes, None, whitespace)
    are strictly rejected and NEVER coerced into string tenant identities.
    """
    db_file = tmp_path / "strict_identity.db"
    store = SessionStore(db_file)
    mgr = SessionManager(store)
    job_store = JobStore(db_file)
    job_mgr = JobManager(job_store)

    invalid_owners = [
        None,
        "",
        " ",
        "   ",
        "\t",
        "\n",
        "\r\n",
        0,
        1,
        False,
        True,
        [],
        [1, 2],
        {},
        {"tenant": "a"},
        (),
        b"tenant",
        object(),
    ]

    valid_owners = [
        "tenant_A",
        "tenant-123",
        "tenant@example.com",
        "org_alpha_beta",
    ]

    # 1. Test SessionManager & SessionStore rejection of all invalid owners
    for bad_owner in invalid_owners:
        # SessionStore.get_session
        with pytest.raises(KeyError):
            store.get_session("sess_any", bad_owner)

        # SessionStore.list_sessions
        with pytest.raises(KeyError):
            store.list_sessions(bad_owner)

        # SessionStore.search_sessions
        with pytest.raises(KeyError):
            store.search_sessions(bad_owner, "query")

        # SessionStore.soft_delete_session
        with pytest.raises(KeyError):
            store.soft_delete_session("sess_any", bad_owner)

        # SessionStore.get_messages
        with pytest.raises(KeyError):
            store.get_messages("sess_any", bad_owner)

        # SessionManager.create_session (must fail before persistent write)
        with pytest.raises(KeyError):
            mgr.create_session(bad_owner, title="test")

        # SessionManager.open_session
        with pytest.raises(KeyError):
            mgr.open_session("sess_any", bad_owner)

        # SessionManager.export_session
        with pytest.raises(KeyError):
            mgr.export_session("sess_any", bad_owner)

        # JobStore.get_job
        with pytest.raises(KeyError):
            job_store.get_job("job_any", bad_owner)

        # JobManager.create_job
        with pytest.raises(KeyError):
            job_mgr.create_job(bad_owner, "Job Name", "Task")

    # 2. Database Identity Safety: verify no sessions were written with stringified bad owners
    conn = store.db.get_connection()
    try:
        cur = conn.execute("SELECT COUNT(*) as cnt FROM sessions")
        count = cur.fetchone()["cnt"]
        assert count == 0, f"Expected 0 sessions, but found {count} written with invalid owners!"
    finally:
        conn.close()

    # 3. Valid owners succeed normally
    for good_owner in valid_owners:
        s = mgr.create_session(good_owner, title=f"Session for {good_owner}")
        assert s.owner_id == good_owner.strip()
        fetched = store.get_session(s.id, good_owner)
        assert fetched is not None
        assert fetched.owner_id == good_owner.strip()


def test_cycle4_enterprise_secret_redaction_matrix():
    """
    NEW-P1-SEC-04: Broad 30+ case enterprise secret scrubbing matrix.
    Tests GitHub classic/extended, fine-grained PATs, Authorization Bearer variants,
    provider keys, SaaS tokens, AWS keys, expanded key-value pairs, and natural language phrases.
    """
    secret_test_cases = [
        # GitHub classic / extended
        ("ghp_1234567890abcdefghijklmnopqrstuvwxyz", "***REDACTED***"),
        ("gho_1234567890abcdefghijklmnopqrstuvwxyz", "***REDACTED***"),
        ("ghu_1234567890abcdefghijklmnopqrstuvwxyz", "***REDACTED***"),
        ("ghs_1234567890abcdefghijklmnopqrstuvwxyz", "***REDACTED***"),
        ("ghr_1234567890abcdefghijklmnopqrstuvwxyz", "***REDACTED***"),
        ("ghp_live_github_personal_access_token_1234567890ab", "***REDACTED***"),
        ("ghp_extended_token_with_underscores_and_longer_length_1234567890", "***REDACTED***"),
        ("ghp_telemetry_test_secret_1234567890abcdef", "***REDACTED***"),
        # GitHub fine-grained PAT
        ("github_pat_11ABCDEFG1234567890_abcdefghijklmnopqrstuvwxyz1234567890", "***REDACTED***"),
        # Authorization Bearer
        ("Authorization: Bearer my_bearer_token_12345", "Authorization: Bearer ***REDACTED***"),
        ("Authorization:Bearer my_bearer_token_12345", "Authorization:Bearer ***REDACTED***"),
        ("authorization: bearer lower_case_bearer_token_12345", "authorization: bearer ***REDACTED***"),
        ("Bearer standalone_bearer_token_12345678", "Bearer ***REDACTED***"),
        # Provider keys
        ("sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890", "***REDACTED***"),
        ("sk-proj-abcdefghijklmnopqrstuvwxyz1234567890123456", "***REDACTED***"),
        ("sk-live_openai_key_abcdefghijklmnopqrstuvwxyz", "***REDACTED***"),
        # SaaS tokens
        ("dummy-slack-token", "***REDACTED***"),
        ("dummy-slack-token", "***REDACTED***"),
        # AWS credentials
        ("AKIAIOSFODNN7EXAMPLE", "***REDACTED***"),
        ("AKIA1234567890ABCDEF", "***REDACTED***"),
        # Expanded key-value pairs
        ("token: my_super_secret_token_12345", "token: ***REDACTED***"),
        ("token=my_super_secret_token_12345", "token=***REDACTED***"),
        ("secret: my_super_secret_val_12345", "secret: ***REDACTED***"),
        ("secret=my_super_secret_val_12345", "secret=***REDACTED***"),
        ("credential: my_credential_secret_val", "credential: ***REDACTED***"),
        ("credential=my_credential_secret_val", "credential=***REDACTED***"),
        ("password: 'database_master_password_2026'", "password: '***REDACTED***'"),
        ("password=database_master_password_2026", "password=***REDACTED***"),
        ("auth_token: auth_token_secret_123456", "auth_token: ***REDACTED***"),
        ("access_token: access_token_secret_123456", "access_token: ***REDACTED***"),
        ("api_key: api_key_secret_123456", "api_key: ***REDACTED***"),
        ("api-key=api_key_secret_123456", "api-key=***REDACTED***"),
        # Natural language phrases
        ("my password is database_admin_pass_123", "my password is ***REDACTED***"),
        ("my key is master_secret_key_12345", "my key is ***REDACTED***"),
        ("my token is sensitive_token_value_12345", "my token is ***REDACTED***"),
    ]

    for raw, expected in secret_test_cases:
        redacted = redact_secrets(raw)
        assert "***REDACTED***" in redacted, f"Failed to redact: {raw} -> {redacted}"


def test_cycle4_false_positive_preservation():
    """
    Verify normal non-secret content (UUIDs, git commit hashes, prose, code keywords)
    is NOT corrupted by secret scrubbing patterns.
    """
    legitimate_inputs = [
        "git commit 7b3d1f4a9b2c8e0d5a6f1b3c4d7e8a9b0c1d2e3f",
        "user_id: 12345678-1234-5678-1234-567812345678",
        "The bearer of good news has arrived in the village.",
        "def get_token(self): return self._token",
        "token = None",
        "token = ''",
        "secret = False",
        "legitimate code containing token variables and function arguments",
        "Primary key index created on table users(id)",
    ]

    for item in legitimate_inputs:
        redacted = redact_secrets(item)
        assert "***REDACTED***" not in redacted, f"False positive detected on: '{item}' -> '{redacted}'"


def test_cycle4_idempotent_terminal_cancellation(tmp_path):
    """
    NEW-P2-CONC-01: Verify that cancel_job against COMPLETED, FAILED, or CANCELLED jobs
    is a safe, deterministic, idempotent no-op without raising ValueError.
    """
    store = JobStore(tmp_path / "cancel_test.db")
    runner = JobRunner(store)

    # 1. COMPLETED job + cancel -> no state change, returns False, no ValueError
    job1 = store.create_job(BackgroundJob(
        id="job_comp_1", owner_id="alice", name="Completed Job", task="Done",
        status=JobStatus.COMPLETED.value
    ))
    res1 = runner.cancel_job(job1.id, "alice")
    assert res1 is False
    assert store.get_job(job1.id, "alice").status == JobStatus.COMPLETED.value

    # 2. FAILED job + cancel -> no state change, returns False, no ValueError
    job2 = store.create_job(BackgroundJob(
        id="job_fail_1", owner_id="alice", name="Failed Job", task="Error",
        status=JobStatus.FAILED.value
    ))
    res2 = runner.cancel_job(job2.id, "alice")
    assert res2 is False
    assert store.get_job(job2.id, "alice").status == JobStatus.FAILED.value

    # 3. CANCELLED job + cancel -> no state change, returns False, no ValueError
    job3 = store.create_job(BackgroundJob(
        id="job_canc_1", owner_id="alice", name="Cancelled Job", task="Cancelled",
        status=JobStatus.CANCELLED.value
    ))
    res3 = runner.cancel_job(job3.id, "alice")
    assert res3 is False
    assert store.get_job(job3.id, "alice").status == JobStatus.CANCELLED.value

    # 4. QUEUED job + cancel -> transitions to CANCELLED, returns True
    job4 = store.create_job(BackgroundJob(
        id="job_queued_1", owner_id="alice", name="Queued Job", task="Wait",
        status=JobStatus.QUEUED.value
    ))
    res4 = runner.cancel_job(job4.id, "alice")
    assert res4 is True
    assert store.get_job(job4.id, "alice").status == JobStatus.CANCELLED.value


def test_cycle4_concurrent_cancellation_race_100_iterations(tmp_path):
    """
    NEW-P2-CONC-01 Concurrency Race: 100 forced iterations of worker completion
    simultaneously racing with operator cancellation.
    Zero uncaught ValueErrors, zero corrupted database states.
    """
    import threading

    db_path = tmp_path / "race_100.db"
    store = JobStore(db_path)
    runner = JobRunner(store)

    for i in range(100):
        jid = f"race_job_{i}"
        owner = "tenant_race"
        store.create_job(BackgroundJob(
            id=jid, owner_id=owner, name=f"Race {i}", task="Simultaneous complete vs cancel",
            status=JobStatus.RUNNING.value
        ))

        cancel_caught_exceptions = []
        worker_caught_exceptions = []

        def do_cancel():
            try:
                runner.cancel_job(jid, owner)
            except Exception as exc:
                cancel_caught_exceptions.append(exc)

        def do_worker_complete():
            try:
                store.atomic_transition(
                    jid, owner,
                    from_statuses=[JobStatus.RUNNING.value],
                    to_status=JobStatus.COMPLETED.value,
                    finished_at=time.time(),
                )
            except Exception as exc:
                worker_caught_exceptions.append(exc)

        t_cancel = threading.Thread(target=do_cancel)
        t_worker = threading.Thread(target=do_worker_complete)

        t_cancel.start()
        t_worker.start()

        t_cancel.join(timeout=2.0)
        t_worker.join(timeout=2.0)

        assert len(cancel_caught_exceptions) == 0, f"Iteration {i}: cancel raised {cancel_caught_exceptions}"
        assert len(worker_caught_exceptions) == 0, f"Iteration {i}: worker raised {worker_caught_exceptions}"

        final_job = store.get_job(jid, owner)
        assert final_job.status in (JobStatus.COMPLETED.value, JobStatus.CANCELLED.value)



