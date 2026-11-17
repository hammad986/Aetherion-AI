"""
aetherion_cli/tui/screens/dashboard.py
Aetherion Dashboard — the first thing users see when they launch `aetherion tui`.

Design: not an empty prompt, not a welcome message — a live intelligence hub.
Shows: live metrics, recent sessions, timeline, memory stats, quick-launch tasks.
"""
from __future__ import annotations

import time
from textual.app import ComposeResult
from textual.containers import Container, Grid, Horizontal, ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Button, DataTable, Label, Static

from aetherion_cli.tui.state import AgentStatus, DashboardMetrics, Screen as AppScreen, Session, state
from aetherion_cli.tui.theme import Colors, Glyphs


class MetricCard(Static):
    """A single KPI card: icon + big number + label."""

    DEFAULT_CSS = """
    MetricCard {
        border: solid #1e2433;
        background: #12151f;
        height: 5;
        padding: 0 1;
        width: 1fr;
    }
    """

    def __init__(self, icon: str, label: str, field: str) -> None:
        super().__init__()
        self._icon  = icon
        self._label = label
        self._field = field

    def on_mount(self) -> None:
        self._unsub = state.subscribe("dashboard_metrics", self._on_change)

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _on_change(self) -> None:
        self.app.call_from_thread(self.refresh)

    def render(self):  # type: ignore[override]
        from rich.text import Text
        m = state.dashboard_metrics
        value = getattr(m, self._field, 0)
        if isinstance(value, float):
            val_str = f"{value:.1f}"
        elif isinstance(value, int) and value >= 1000:
            val_str = f"{value:,}"
        else:
            val_str = str(value)

        t = Text()
        t.append(f" {self._icon}\n", style=f"bold {Colors.ACCENT_BRIGHT}")
        t.append(f" {val_str}\n", style=f"bold {Colors.TEXT_PRIMARY}")
        t.append(f" {self._label}", style=Colors.TEXT_MUTED)
        return t


class RecentSessionsTable(Widget):
    """Last N sessions shown as a compact table."""

    DEFAULT_CSS = """
    RecentSessionsTable {
        height: 12;
        border: solid #1e2433;
        background: #12151f;
    }
    RecentSessionsTable DataTable {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(f" {Glyphs.SESSIONS} Recent Sessions", classes="dash-section-title")
        yield DataTable(id="dash-sessions-table", show_cursor=True)

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        t.add_columns("Status", "Title", "Model", "Age")
        self._unsub = state.subscribe("sessions", self._on_sessions_changed)
        self._rebuild()

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _on_sessions_changed(self) -> None:
        self.app.call_from_thread(self._rebuild)

    def _rebuild(self) -> None:
        t = self.query_one(DataTable)
        t.clear()
        for s in state.sessions[:12]:
            status_map = {
                "done":    (Glyphs.DONE,    Colors.SUCCESS),
                "running": (Glyphs.RUNNING, Colors.WARNING),
                "error":   (Glyphs.ERROR,   Colors.ERROR),
                "queued":  (Glyphs.QUEUED,  Colors.INFO),
            }
            icon, color = status_map.get(s.status, (Glyphs.IDLE, Colors.TEXT_MUTED))

            from rich.text import Text
            status_cell = Text(f"{icon} {s.status}", style=f"bold {color}")
            title_cell  = Text(s.title[:36], style=Colors.TEXT_PRIMARY)
            model_cell  = Text(s.model or "—", style=Colors.TEXT_MUTED)
            age_cell    = Text(_age(s.created_at), style=Colors.TEXT_DIM)
            t.add_row(status_cell, title_cell, model_cell, age_cell, key=s.id)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        session_id = str(event.row_key.value)
        state.set_session(session_id)
        state.navigate(AppScreen.CHAT)


class TimelineWidget(Widget):
    """Live activity timeline from /api/dashboard/timeline."""

    DEFAULT_CSS = """
    TimelineWidget {
        height: 10;
        border: solid #1e2433;
        background: #12151f;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(f" {Glyphs.TIMELINE} Activity Timeline", classes="dash-section-title")
        yield ScrollableContainer(id="timeline-scroll")

    def on_mount(self) -> None:
        self._unsub = state.subscribe("timeline", self._on_timeline_changed)
        self._rebuild()

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _on_timeline_changed(self) -> None:
        self.app.call_from_thread(self._rebuild)

    def _rebuild(self) -> None:
        sc = self.query_one("#timeline-scroll")
        sc.remove_children()

        if not state.timeline:
            sc.mount(Static("  No timeline events yet.", classes="empty-state"))
            return

        level_color = {
            "success": Colors.SUCCESS,
            "warning": Colors.WARNING,
            "error":   Colors.ERROR,
            "info":    Colors.INFO,
        }

        for ev in state.timeline[:15]:
            color = level_color.get(ev.level, Colors.TEXT_MUTED)
            ts_str = _fmt_ts(ev.ts)
            from rich.text import Text
            t = Text()
            t.append(f"  {ts_str} ", style=Colors.TEXT_DIM)
            t.append(f"● ", style=f"bold {color}")
            t.append(ev.event, style=Colors.TEXT_PRIMARY)
            if ev.detail:
                t.append(f"  {ev.detail[:50]}", style=Colors.TEXT_MUTED)
            sc.mount(Static(t))


class DashboardActionBar(Widget):
    """Context action bar for the dashboard (avoids duplicating sidebar navigation)."""

    DEFAULT_CSS = """
    DashboardActionBar {
        height: 3;
        background: #12151f;
        border-bottom: solid #1e2433;
        padding: 0 1;
    }
    DashboardActionBar Horizontal {
        height: 1fr;
        align-vertical: middle;
    }
    #dash-btn-new {
        margin-right: 1;
    }
    #dash-header-title {
        color: #9d5cf0;
        text-style: bold;
        width: 1fr;
        content-align: left middle;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Label(f" {Glyphs.DASH} Workspace Overview", id="dash-header-title")
            yield Button(f"{Glyphs.AI_ICON} + New Session", id="dash-btn-new", variant="default")
            yield Button(f"↻ Refresh", id="dash-btn-refresh", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        from aetherion_cli.tui.gateway import gateway
        if bid == "dash-btn-new":
            gateway.new_session()
            state.navigate(AppScreen.CHAT)
        elif bid == "dash-btn-refresh":
            gateway.load_dashboard()
            gateway.load_sessions()
            state.push_notification("Refreshed dashboard metrics", "info")


class DashboardScreen(Widget):
    """
    The landing screen — what users see immediately on launch.

    Layout:
      ┌── Action bar ────────────────────────────────────────────┐
      │  Workspace Overview                 [+ New Session] [↻]  │
      ├──────────────────────────────────────────────────────────┤
      │ KPI row: Sessions | Tasks | Tokens | Cost | Uptime        │
      ├──────────────────────────┬───────────────────────────────┤
      │ Recent Sessions table    │ Timeline                       │
      └──────────────────────────┴───────────────────────────────┘
    """

    DEFAULT_CSS = """
    DashboardScreen {
        height: 1fr;
        width: 1fr;
    }
    DashboardScreen .dash-section-title {
        color: #9d5cf0;
        height: 1;
        padding: 0 1;
        background: #0a0c12;
        text-style: bold;
    }
    #kpi-row {
        height: 5;
        margin: 0 0 1 0;
    }
    #lower-row {
        height: 1fr;
    }
    #lower-left {
        width: 1fr;
        margin-right: 1;
    }
    #lower-right {
        width: 1fr;
    }
    #dash-error {
        color: #ef4444;
        height: 1;
        padding: 0 1;
        display: none;
    }
    #dash-error.visible {
        display: block;
    }
    """

    def compose(self) -> ComposeResult:
        yield DashboardActionBar()
        with Horizontal(id="kpi-row"):
            yield MetricCard(Glyphs.SESSIONS, "Sessions Today",  "sessions_today")
            yield MetricCard(Glyphs.DONE,     "Tasks Completed", "tasks_done")
            yield MetricCard(Glyphs.SPARKLE,  "Tokens Used",     "tokens_used")
            yield MetricCard(Glyphs.BOLT,     "Active Agents",   "active_agents")
            yield MetricCard(Glyphs.MEMORY,   "Memory Entries",  "memory_entries")
        with Horizontal(id="lower-row"):
            with Vertical(id="lower-left"):
                yield RecentSessionsTable()
            with Vertical(id="lower-right"):
                yield TimelineWidget()
        yield Label("", id="dash-error")

    def on_mount(self) -> None:
        from aetherion_cli.tui.gateway import gateway
        gateway.load_dashboard()
        gateway.load_sessions()
        self._sub_err = state.subscribe("error_message", self._on_error)

    def on_unmount(self) -> None:
        if hasattr(self, "_sub_err"):
            self._sub_err()

    def _on_error(self) -> None:
        def _update():
            label = self.query_one("#dash-error", Label)
            if state.error_message:
                label.update(f"{Glyphs.ERROR} {state.error_message}")
                label.add_class("visible")
            else:
                label.remove_class("visible")
        self.app.call_from_thread(_update)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _age(ts: float | None) -> str:
    if not ts:
        return "—"
    try:
        delta = time.time() - float(ts)
        if delta < 60:
            return f"{int(delta)}s"
        if delta < 3600:
            return f"{int(delta / 60)}m"
        if delta < 86400:
            return f"{int(delta / 3600)}h"
        return f"{int(delta / 86400)}d"
    except Exception:
        return "—"


def _fmt_ts(ts: float) -> str:
    import datetime
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    except Exception:
        return "—"
