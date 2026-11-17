"""
tests/test_tui_state.py
Aetherion TUI State — comprehensive test suite.

Covers: thread safety, all state setters, subscription system,
message stream lifecycle, approval, navigation, notifications,
sessions, memory, plugins, providers, jobs, context.

Strategy: direct importlib load of state.py bypasses textual dep.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

# ── Module loader (bypasses textual import in tui/__init__.py) ────────────────

def _load_module(rel_path: str, module_name: str):
    """Load a module by path without going through the package init chain."""
    base = Path(__file__).parent.parent / "aetherion_cli" / "tui"
    path = base / rel_path
    if module_name in sys.modules:
        del sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def state_mod():
    return _load_module("state.py", "aetherion_cli.tui.state")


@pytest.fixture
def fresh_state(state_mod):
    """A fresh TUIState instance per test."""
    return state_mod.TUIState()


# ── Import smoke test ─────────────────────────────────────────────────────────

def test_state_module_imports(state_mod):
    assert hasattr(state_mod, "TUIState")
    assert hasattr(state_mod, "state")
    assert hasattr(state_mod, "Message")
    assert hasattr(state_mod, "Session")
    assert hasattr(state_mod, "AgentStatus")
    assert hasattr(state_mod, "Screen")
    assert hasattr(state_mod, "NotifLevel")


# ── Screen navigation ─────────────────────────────────────────────────────────

def test_navigate(fresh_state, state_mod):
    fresh_state.navigate(state_mod.Screen.CHAT)
    assert fresh_state.current_screen == state_mod.Screen.CHAT
    assert fresh_state.previous_screen == state_mod.Screen.DASHBOARD


def test_navigate_back(fresh_state, state_mod):
    fresh_state.navigate(state_mod.Screen.SESSIONS)
    fresh_state.navigate_back()
    assert fresh_state.current_screen == state_mod.Screen.DASHBOARD


def test_navigate_back_twice(fresh_state, state_mod):
    fresh_state.navigate(state_mod.Screen.CHAT)
    fresh_state.navigate(state_mod.Screen.MEMORY)
    fresh_state.navigate_back()
    assert fresh_state.current_screen == state_mod.Screen.CHAT


# ── Subscription system ───────────────────────────────────────────────────────

def test_subscribe_fires_on_change(fresh_state, state_mod):
    fired = []
    fresh_state.subscribe("current_screen", lambda: fired.append(1))
    fresh_state.navigate(state_mod.Screen.CHAT)
    assert fired == [1]


def test_unsubscribe(fresh_state, state_mod):
    fired = []
    unsub = fresh_state.subscribe("current_screen", lambda: fired.append(1))
    unsub()
    fresh_state.navigate(state_mod.Screen.CHAT)
    assert fired == []


def test_multiple_subscribers(fresh_state, state_mod):
    results = []
    for i in range(5):
        fresh_state.subscribe("current_screen", lambda i=i: results.append(i))
    fresh_state.navigate(state_mod.Screen.MEMORY)
    assert len(results) == 5


def test_subscriber_exception_does_not_crash(fresh_state, state_mod):
    def bad_cb():
        raise RuntimeError("boom")
    fresh_state.subscribe("current_screen", bad_cb)
    fresh_state.navigate(state_mod.Screen.CHAT)  # must not raise


# ── Auth state ────────────────────────────────────────────────────────────────

def test_set_user(fresh_state):
    fresh_state.set_user("alice@example.com", plan="Pro", user_id="u-42")
    assert fresh_state.user_email == "alice@example.com"
    assert fresh_state.user_plan == "Pro"
    assert fresh_state.user_id == "u-42"


def test_set_connected(fresh_state):
    fresh_state.set_connected(True, latency_ms=42)
    assert fresh_state.connected is True
    assert fresh_state.latency_ms == 42


def test_set_disconnected(fresh_state):
    fresh_state.set_connected(False)
    assert fresh_state.connected is False


# ── Agent status ──────────────────────────────────────────────────────────────

def test_set_status(fresh_state, state_mod):
    fresh_state.set_status(state_mod.AgentStatus.RUNNING)
    assert fresh_state.status == state_mod.AgentStatus.RUNNING


def test_set_model(fresh_state):
    fresh_state.set_model("gpt-4o")
    assert fresh_state.active_model == "gpt-4o"


def test_set_session(fresh_state):
    fresh_state.set_session("sess-abc")
    assert fresh_state.active_session_id == "sess-abc"


# ── Messages ──────────────────────────────────────────────────────────────────

def test_append_message(fresh_state, state_mod):
    m = state_mod.Message.user("hello")
    fresh_state.append_message(m)
    assert len(fresh_state.messages) == 1
    assert fresh_state.messages[0].content == "hello"


def test_message_roles(fresh_state, state_mod):
    fresh_state.append_message(state_mod.Message.user("user msg"))
    fresh_state.append_message(state_mod.Message.assistant("ai msg"))
    fresh_state.append_message(state_mod.Message.system("sys msg"))
    fresh_state.append_message(state_mod.Message.tool_call("bash", "ls"))
    fresh_state.append_message(state_mod.Message.error("err"))
    assert len(fresh_state.messages) == 5
    roles = [m.role for m in fresh_state.messages]
    assert roles == ["user", "assistant", "system", "tool", "error"]


def test_streaming_message_lifecycle(fresh_state, state_mod):
    m = state_mod.Message.assistant("", streaming=True)
    fresh_state.append_message(m)
    fresh_state.update_last_assistant_message("Hello")
    fresh_state.update_last_assistant_message(", world")
    fresh_state.finalize_streaming_message(token_count=12)
    msg = fresh_state.messages[-1]
    assert msg.content == "Hello, world"
    assert msg.is_streaming is False
    assert msg.token_count == 12


def test_finalize_no_streaming_message_is_noop(fresh_state, state_mod):
    fresh_state.append_message(state_mod.Message.user("hi"))
    fresh_state.finalize_streaming_message()   # must not raise


def test_clear_messages(fresh_state, state_mod):
    for _ in range(5):
        fresh_state.append_message(state_mod.Message.user("x"))
    fresh_state.clear_messages()
    assert fresh_state.messages == []


# ── Context usage ─────────────────────────────────────────────────────────────

def test_context_usage(fresh_state, state_mod):
    fresh_state.update_context(50_000, 1_000, limit=200_000)
    ctx = fresh_state.context
    assert ctx.input_tokens == 50_000
    assert ctx.output_tokens == 1_000
    assert ctx.used_pct == 25


def test_context_fraction_capped_at_1(fresh_state, state_mod):
    fresh_state.update_context(300_000, 0, limit=200_000)
    assert fresh_state.context.used_fraction == 1.0


# ── Approval ──────────────────────────────────────────────────────────────────

def test_approval_set_and_clear(fresh_state, state_mod):
    req = state_mod.ApprovalRequest(
        request_id="r1", tool_name="bash", description="Run ls", risk_level="low"
    )
    fresh_state.set_approval(req)
    assert fresh_state.pending_approval is req
    fresh_state.set_approval(None)
    assert fresh_state.pending_approval is None


# ── Sessions ──────────────────────────────────────────────────────────────────

def test_set_sessions(fresh_state, state_mod):
    sessions = [
        state_mod.Session(id=str(i), title=f"S{i}", status="done", model="gpt-4o",
                          created_at=time.time())
        for i in range(5)
    ]
    fresh_state.set_sessions(sessions)
    assert len(fresh_state.sessions) == 5
    assert fresh_state.sessions_loading is False
    assert fresh_state.sessions_error is None


def test_set_sessions_loading(fresh_state):
    fresh_state.set_sessions_loading(True)
    assert fresh_state.sessions_loading is True


def test_set_sessions_with_error(fresh_state):
    fresh_state.set_sessions([], error="Network error")
    assert fresh_state.sessions_error == "Network error"


# ── Dashboard metrics ─────────────────────────────────────────────────────────

def test_set_dashboard_metrics(fresh_state, state_mod):
    m = state_mod.DashboardMetrics(sessions_today=10, tasks_done=42, tokens_used=100_000)
    fresh_state.set_dashboard_metrics(m)
    assert fresh_state.dashboard_metrics.sessions_today == 10
    assert fresh_state.dashboard_metrics.tasks_done == 42


def test_set_timeline(fresh_state, state_mod):
    events = [
        state_mod.TimelineEvent(id="e1", ts=time.time(), event="agent.start", level="info")
    ]
    fresh_state.set_timeline(events)
    assert len(fresh_state.timeline) == 1


# ── Memory ────────────────────────────────────────────────────────────────────

def test_set_memory(fresh_state, state_mod):
    entries = [
        state_mod.MemoryEntry(id="m1", content="Python is fast", importance=0.9)
    ]
    fresh_state.set_memory(entries)
    assert len(fresh_state.memory_entries) == 1
    assert fresh_state.memory_loading is False


def test_set_memory_loading(fresh_state):
    fresh_state.set_memory_loading(True)
    assert fresh_state.memory_loading is True


# ── Plugins ───────────────────────────────────────────────────────────────────

def test_set_plugins(fresh_state, state_mod):
    plugins = [
        state_mod.Plugin(id="p1", name="Git", description="Git plugin", installed=True)
    ]
    fresh_state.set_plugins(plugins)
    assert len(fresh_state.plugins) == 1
    assert fresh_state.plugins[0].installed is True


# ── Providers ─────────────────────────────────────────────────────────────────

def test_set_providers(fresh_state, state_mod):
    providers = [
        state_mod.Provider(key="openai", name="OpenAI", model="gpt-4o", available=True, latency=320)
    ]
    fresh_state.set_providers(providers)
    assert fresh_state.providers[0].available is True
    assert fresh_state.providers[0].latency == 320


# ── Jobs ──────────────────────────────────────────────────────────────────────

def test_set_jobs(fresh_state, state_mod):
    jobs = [
        state_mod.BackgroundJob(id="j1", name="Backup", status="running", progress=42)
    ]
    fresh_state.set_jobs(jobs)
    assert fresh_state.jobs[0].progress == 42


def test_upsert_job_new(fresh_state, state_mod):
    job = state_mod.BackgroundJob(id="j-new", name="Deploy", status="queued", progress=0)
    fresh_state.upsert_job(job)
    assert any(j.id == "j-new" for j in fresh_state.jobs)


def test_upsert_job_update(fresh_state, state_mod):
    job = state_mod.BackgroundJob(id="j-upd", name="Test", status="running", progress=30)
    fresh_state.set_jobs([job])
    updated = state_mod.BackgroundJob(id="j-upd", name="Test", status="done", progress=100)
    fresh_state.upsert_job(updated)
    match = next(j for j in fresh_state.jobs if j.id == "j-upd")
    assert match.status == "done"
    assert match.progress == 100


# ── Notifications ─────────────────────────────────────────────────────────────

def test_push_notification(fresh_state, state_mod):
    fresh_state.push_notification("Build complete", state_mod.NotifLevel.SUCCESS)
    assert fresh_state.active_notification is not None
    assert fresh_state.active_notification.message == "Build complete"
    assert fresh_state.active_notification.level == state_mod.NotifLevel.SUCCESS
    assert len(fresh_state.notifications) == 1


def test_dismiss_notification(fresh_state, state_mod):
    fresh_state.push_notification("Test")
    fresh_state.dismiss_notification()
    assert fresh_state.active_notification is None
    assert len(fresh_state.notifications) == 1   # kept in history


def test_multiple_notifications_stack(fresh_state, state_mod):
    for i in range(5):
        fresh_state.push_notification(f"Event {i}")
    assert len(fresh_state.notifications) == 5
    assert fresh_state.notifications[0].message == "Event 4"  # newest first


# ── UI toggles ────────────────────────────────────────────────────────────────

def test_toggle_sidebar(fresh_state):
    assert fresh_state.left_sidebar_open is True
    fresh_state.toggle_sidebar()
    assert fresh_state.left_sidebar_open is False
    fresh_state.toggle_sidebar()
    assert fresh_state.left_sidebar_open is True


def test_set_palette(fresh_state):
    fresh_state.set_palette(True, query="doc")
    assert fresh_state.palette_open is True
    assert fresh_state.palette_query == "doc"
    fresh_state.set_palette(False)
    assert fresh_state.palette_open is False


# ── Error state ───────────────────────────────────────────────────────────────

def test_set_error(fresh_state):
    fresh_state.set_error("Connection refused")
    assert fresh_state.error_message == "Connection refused"
    fresh_state.set_error(None)
    assert fresh_state.error_message is None


# ── Thread safety ─────────────────────────────────────────────────────────────

def test_concurrent_message_append(fresh_state, state_mod):
    N = 200
    barrier = threading.Barrier(N)

    def worker():
        barrier.wait()
        fresh_state.append_message(state_mod.Message.user("concurrent"))

    threads = [threading.Thread(target=worker) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(fresh_state.messages) == N


def test_concurrent_status_updates(fresh_state, state_mod):
    statuses = list(state_mod.AgentStatus)
    barrier = threading.Barrier(len(statuses))

    def worker(s):
        barrier.wait()
        fresh_state.set_status(s)

    threads = [threading.Thread(target=worker, args=(s,)) for s in statuses]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert fresh_state.status in set(state_mod.AgentStatus)


def test_concurrent_navigate(fresh_state, state_mod):
    screens = list(state_mod.Screen)
    barrier = threading.Barrier(len(screens))

    def worker(sc):
        barrier.wait()
        fresh_state.navigate(sc)

    threads = [threading.Thread(target=worker, args=(sc,)) for sc in screens]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert fresh_state.current_screen in set(state_mod.Screen)


def test_concurrent_subscriptions(fresh_state, state_mod):
    """Subscribe from many threads simultaneously — no deadlock."""
    fired = []
    lock = threading.Lock()
    barrier = threading.Barrier(20)

    def worker():
        barrier.wait()
        def cb():
            with lock:
                fired.append(1)
        fresh_state.subscribe("current_screen", cb)
        fresh_state.navigate(state_mod.Screen.CHAT)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(fired) >= 1   # at least one callback fired


# ── Singleton ─────────────────────────────────────────────────────────────────

def test_singleton_is_tui_state(state_mod):
    assert isinstance(state_mod.state, state_mod.TUIState)
