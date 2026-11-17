"""
aetherion_cli/tui/screens/jobs.py
Background Jobs — scheduler tasks, live progress, run/cancel controls.
"""
from __future__ import annotations

import time
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer
from textual.widget import Widget
from textual.widgets import Button, Label, Static

from aetherion_cli.tui.state import BackgroundJob, Screen as AppScreen, state
from aetherion_cli.tui.theme import Colors, Glyphs


class JobRow(Static):
    """Single background job row with progress bar."""

    DEFAULT_CSS = """
    JobRow {
        height: 3;
        padding: 0 2;
        border-bottom: solid #1e2433;
        width: 1fr;
    }
    JobRow.selected {
        background: #4c2288;
    }
    """

    def __init__(self, job: BackgroundJob, selected: bool = False) -> None:
        super().__init__()
        self._job = job
        if selected:
            self.add_class("selected")

    def render(self) -> Text:  # type: ignore[override]
        j   = self._job
        sel = "selected" in self.classes

        status_conf = {
            "running": (Glyphs.RUNNING, Colors.WARNING),
            "queued":  (Glyphs.QUEUED,  Colors.INFO),
            "done":    (Glyphs.DONE,    Colors.SUCCESS),
            "error":   (Glyphs.ERROR,   Colors.ERROR),
        }
        icon, color = status_conf.get(j.status, (Glyphs.IDLE, Colors.TEXT_DIM))

        # Mini progress bar (10 chars)
        if j.status in ("running", "queued"):
            filled = max(1, int(10 * (j.progress / 100)))
            bar = Glyphs.DOT_FULL * filled + Glyphs.DOT_EMPTY * (10 - filled)
        else:
            bar = None

        prefix = f"{Glyphs.CHEVRON_RIGHT} " if sel else "  "
        t = Text()
        t.append(prefix, style=f"bold {Colors.ACCENT}" if sel else Colors.TEXT_DIM)
        t.append(f"{icon} ", style=f"bold {color}")
        t.append(j.name[:32], style=f"bold {Colors.TEXT_PRIMARY}")
        if bar:
            t.append(f"  {bar}  {j.progress}%", style=color)
        t.append("\n")
        t.append(f"  id:{j.id[:12]}  ", style=Colors.TEXT_DIM)
        age = _age(j.updated_at)
        t.append(age, style=Colors.TEXT_DIM)
        if j.result:
            t.append(f"  {j.result[:40]}", style=Colors.TEXT_MUTED)
        return t


class JobsScreen(Widget):
    """Background Jobs / Scheduler task list."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("r",      "refresh", "Refresh", show=True),
        Binding("escape", "back",    "Back",    show=True),
    ]

    DEFAULT_CSS = """
    JobsScreen {
        height: 1fr;
        width: 1fr;
    }
    #jobs-toolbar {
        height: 1;
        background: #12151f;
        border-bottom: solid #1e2433;
        padding: 0 1;
        color: #64748b;
    }
    #jobs-scroll {
        height: 1fr;
        background: #0a0c12;
    }
    #jobs-summary {
        height: 1;
        background: #12151f;
        border-top: solid #1e2433;
        padding: 0 1;
        color: #64748b;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._selected_idx = 0

    def compose(self) -> ComposeResult:
        yield Label(
            f" {Glyphs.JOBS} Background Jobs   "
            f"[{Colors.TEXT_DIM}]↑↓[/] navigate  "
            f"[{Colors.TEXT_DIM}]r[/] refresh",
            id="jobs-toolbar"
        )
        yield ScrollableContainer(id="jobs-scroll")
        yield Label("", id="jobs-summary")

    def on_mount(self) -> None:
        self._unsub = state.subscribe("jobs", self._on_jobs_changed)
        from aetherion_cli.tui.gateway import gateway
        gateway.load_jobs()
        self.set_interval(15, self._auto_refresh)

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _auto_refresh(self) -> None:
        from aetherion_cli.tui.gateway import gateway
        gateway.load_jobs()

    def _on_jobs_changed(self) -> None:
        self.app.call_from_thread(self._rebuild)

    def _rebuild(self) -> None:
        scroll = self.query_one("#jobs-scroll")
        scroll.remove_children()
        jobs = state.jobs

        if not jobs:
            scroll.mount(Static(
                f"\n  {Glyphs.JOBS}  No background jobs running.",
                classes="empty-state"
            ))
            self.query_one("#jobs-summary", Label).update("")
            return

        self._selected_idx = max(0, min(self._selected_idx, len(jobs) - 1))
        for i, j in enumerate(jobs[:50]):
            scroll.mount(JobRow(j, selected=i == self._selected_idx))

        # Summary bar
        running = sum(1 for j in jobs if j.status == "running")
        queued  = sum(1 for j in jobs if j.status == "queued")
        done    = sum(1 for j in jobs if j.status == "done")
        errors  = sum(1 for j in jobs if j.status == "error")
        self.query_one("#jobs-summary", Label).update(
            f"  {Glyphs.RUNNING} {running} running  "
            f"{Glyphs.QUEUED} {queued} queued  "
            f"{Glyphs.DONE} {done} done  "
            f"{Glyphs.ERROR} {errors} errors"
        )

    def on_key(self, event) -> None:
        jobs = state.jobs
        n = len(jobs)
        if event.key == "up" and n:
            self._selected_idx = max(0, self._selected_idx - 1)
            self._rebuild()
            event.prevent_default()
        elif event.key == "down" and n:
            self._selected_idx = min(n - 1, self._selected_idx + 1)
            self._rebuild()
            event.prevent_default()

    def action_refresh(self) -> None:
        from aetherion_cli.tui.gateway import gateway
        gateway.load_jobs()

    def action_back(self) -> None:
        state.navigate_back()


def _age(ts) -> str:
    try:
        delta = time.time() - float(ts)
        if delta < 60:
            return f"{int(delta)}s ago"
        if delta < 3600:
            return f"{int(delta/60)}m ago"
        return f"{int(delta/3600)}h ago"
    except Exception:
        return "—"
