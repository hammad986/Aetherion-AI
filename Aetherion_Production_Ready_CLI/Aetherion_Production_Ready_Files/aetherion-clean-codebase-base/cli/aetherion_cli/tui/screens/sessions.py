"""
aetherion_cli/tui/screens/sessions.py
Session Browser — 2-pane layout: list on left, detail on right.

Keyboard:
  ↑/↓     navigate list
  Enter   open session in chat (Resume)
  n       new session
  e       rename selected session (inline input)
  p       duplicate selected session
  d       delete selected (with confirmation)
  r       refresh list
  /       search/filter
  Esc     back / cancel rename
"""
from __future__ import annotations

import time
from typing import ClassVar, Optional

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Static

from aetherion_cli.tui.state import Screen as AppScreen, Session, state
from aetherion_cli.tui.theme import Colors, Glyphs


class SessionListPane(Widget):
    """Left pane: searchable, keyboard-navigable session list."""

    DEFAULT_CSS = """
    SessionListPane {
        width: 40;
        border-right: solid #1e2433;
        background: #12151f;
        height: 1fr;
    }
    SessionListPane #sessions-search {
        height: 3;
        border-bottom: solid #1e2433;
        padding: 0 1;
    }
    SessionListPane #sessions-search Input {
        border: none;
        background: #12151f;
    }
    SessionListPane #sessions-rename-bar {
        height: 3;
        border: solid #7c3aed;
        padding: 0 1;
        background: #1a1e2e;
        display: none;
    }
    SessionListPane #sessions-rename-bar.visible {
        display: block;
    }
    SessionListPane #sessions-rename-bar Input {
        border: none;
        background: #1a1e2e;
        color: #e2e8f0;
    }
    SessionListPane #sessions-list-scroll {
        height: 1fr;
    }
    SessionListPane #sessions-loading {
        color: #64748b;
        padding: 1;
        height: 1;
    }
    SessionListPane #sessions-error {
        color: #ef4444;
        padding: 1;
        height: auto;
        display: none;
    }
    SessionListPane #sessions-error.visible {
        display: block;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._selected_idx = 0
        self._filter       = ""
        self._renaming     = False

    def compose(self) -> ComposeResult:
        with Container(id="sessions-search"):
            yield Input(placeholder=f"{Glyphs.BULLET} Search sessions…", id="session-filter")
        with Container(id="sessions-rename-bar"):
            yield Input(placeholder="New name — Enter to save, Esc to cancel", id="session-rename-input")
        yield Label(f"  {Glyphs.RUNNING} Loading sessions…", id="sessions-loading")
        yield Label("", id="sessions-error")
        yield ScrollableContainer(id="sessions-list-scroll")

    def on_mount(self) -> None:
        self._subs = [
            state.subscribe("sessions", self._on_sessions_changed),
        ]
        self._rebuild()

    def on_unmount(self) -> None:
        for u in getattr(self, "_subs", []):
            u()

    def _filtered(self) -> list[Session]:
        q = self._filter.lower()
        return [s for s in state.sessions
                if not q or q in s.title.lower() or q in s.id.lower()
                or (s.provider and q in s.provider.lower())
                or (s.model and q in s.model.lower())]

    def _on_sessions_changed(self) -> None:
        self.app.call_from_thread(self._rebuild)

    def _rebuild(self) -> None:
        loading_lbl = self.query_one("#sessions-loading", Label)
        err_lbl     = self.query_one("#sessions-error", Label)
        scroll      = self.query_one("#sessions-list-scroll")

        if state.sessions_loading:
            loading_lbl.display = True
            return
        loading_lbl.display = False

        if state.sessions_error:
            err_lbl.update(f"{Glyphs.ERROR} {state.sessions_error}")
            err_lbl.add_class("visible")
        else:
            err_lbl.remove_class("visible")

        sessions = self._filtered()
        scroll.remove_children()

        if not sessions:
            scroll.mount(Static(
                f"\n  {Glyphs.SESSIONS}  No sessions found.\n\n  Press [n] to create one.",
                classes="empty-state"
            ))
            return

        self._selected_idx = max(0, min(self._selected_idx, len(sessions) - 1))

        for i, s in enumerate(sessions):
            selected = i == self._selected_idx
            row = _SessionRow(s, selected=selected, idx=i)
            scroll.mount(row)

        self._notify_selection(sessions)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "session-filter":
            self._filter = event.value
            self._selected_idx = 0
            self._rebuild()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "session-rename-input":
            new_name = event.value.strip()
            sessions = self._filtered()
            if not new_name:
                state.push_notification("Session name cannot be empty", "warning")
                self._exit_rename()
                return
            if len(new_name) > 100:
                new_name = new_name[:100]
            if 0 <= self._selected_idx < len(sessions):
                s = sessions[self._selected_idx]
                from aetherion_cli.tui.gateway import gateway
                try:
                    gateway.rename_session(s.id, new_name)
                    state.push_notification(f'Renamed session to "{new_name}"', "success")
                except Exception as exc:
                    log.error("Failed to rename session: %s", exc)
                    state.push_notification(f"Failed to rename session: {exc}", "error")
            self._exit_rename()
            self._rebuild()

    def on_key(self, event: events.Key) -> None:
        # If rename input active, route Esc only
        if self._renaming:
            if event.key == "escape":
                self._exit_rename()
                event.prevent_default()
            return

        sessions = self._filtered()
        n = len(sessions)

        if event.key == "up":
            self._selected_idx = max(0, self._selected_idx - 1)
            self._rebuild()
            self._notify_selection(sessions)
            event.prevent_default()

        elif event.key == "down":
            self._selected_idx = min(n - 1, self._selected_idx + 1)
            self._rebuild()
            self._notify_selection(sessions)
            event.prevent_default()

        elif event.key == "enter":
            if n > 0 and 0 <= self._selected_idx < n:
                s = sessions[self._selected_idx]
                state.set_session(s.id)
                state.navigate(AppScreen.CHAT)
                from aetherion_cli.tui.gateway import gateway
                gateway.resume_session(s.id)

        elif event.key == "e":
            # Inline rename
            if n > 0 and 0 <= self._selected_idx < n:
                self._enter_rename(sessions[self._selected_idx])
            event.prevent_default()

        elif event.key == "p":
            # Duplicate/clone
            if n > 0 and 0 <= self._selected_idx < n:
                s = sessions[self._selected_idx]
                from aetherion_cli.tui.gateway import gateway
                gateway.branch_session(s.id, f"{s.title} (copy)")
                state.push_notification(f'Duplicated session "{s.title}"', "success")
            event.prevent_default()

    def _enter_rename(self, session: Session) -> None:
        self._renaming = True
        bar   = self.query_one("#sessions-rename-bar")
        inp   = self.query_one("#session-rename-input", Input)
        bar.add_class("visible")
        inp.value = session.title
        inp.focus()

    def _exit_rename(self) -> None:
        self._renaming = False
        bar   = self.query_one("#sessions-rename-bar")
        inp   = self.query_one("#session-rename-input", Input)
        bar.remove_class("visible")
        inp.value = ""
        self.query_one("#session-filter", Input).focus()

    def _notify_selection(self, sessions: list[Session]) -> None:
        if 0 <= self._selected_idx < len(sessions):
            self.app.on_session_selected(sessions[self._selected_idx])

    def selected_session(self) -> Optional[Session]:
        sessions = self._filtered()
        if 0 <= self._selected_idx < len(sessions):
            return sessions[self._selected_idx]
        return None


class _SessionRow(Static):
    """A single row in the session list pane."""

    DEFAULT_CSS = """
    _SessionRow {
        height: 3;
        padding: 0 1;
        border-bottom: solid #1e2433;
    }
    _SessionRow.selected {
        background: #4c2288;
    }
    _SessionRow:hover {
        background: #1a1e2e;
    }
    """

    def __init__(self, session: Session, selected: bool = False, idx: int = 0) -> None:
        super().__init__()
        self._session  = session
        self._selected = selected
        self._idx      = idx
        if selected:
            self.add_class("selected")

    def render(self) -> Text:  # type: ignore[override]
        s = self._session
        status_map = {
            "done":    (Glyphs.DONE,    Colors.SUCCESS),
            "running": (Glyphs.RUNNING, Colors.WARNING),
            "error":   (Glyphs.ERROR,   Colors.ERROR),
        }
        icon, color = status_map.get(s.status, (Glyphs.IDLE, Colors.TEXT_DIM))

        t = Text()
        prefix = f"{Glyphs.CHEVRON_RIGHT} " if self._selected else "  "
        t.append(prefix, style=f"bold {Colors.ACCENT}" if self._selected else Colors.TEXT_DIM)
        t.append(f"{icon} ", style=f"bold {color}")
        t.append(s.title[:30], style=f"bold {Colors.TEXT_PRIMARY}" if self._selected else Colors.TEXT_PRIMARY)

        # Compressed badge
        if s.compressed:
            t.append(" 🗜", style=Colors.TEXT_DIM)

        t.append("\n")

        # Second line: model / provider / age
        meta_parts = []
        if s.model:
            meta_parts.append(s.model[:18])
        if s.provider and s.provider != (s.model or "").split("/")[0]:
            meta_parts.append(s.provider[:10])
        if s.updated_at:
            meta_parts.append(_age(s.updated_at))
        t.append("  " + "  ·  ".join(meta_parts) if meta_parts else "  ", style=Colors.TEXT_DIM)
        t.append("\n")

        # Third line: session ID (dim)
        t.append(f"  {s.id[:20]}", style=Colors.TEXT_DIM)
        return t


class SessionDetailPane(Widget):
    """Right pane: detail view for the selected session."""

    DEFAULT_CSS = """
    SessionDetailPane {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
        background: #0a0c12;
    }
    SessionDetailPane #detail-title {
        color: #9d5cf0;
        text-style: bold;
        height: 2;
    }
    SessionDetailPane #detail-meta {
        color: #64748b;
        height: auto;
        margin-bottom: 1;
    }
    SessionDetailPane #detail-actions {
        height: 3;
    }
    SessionDetailPane #detail-task {
        color: #e2e8f0;
        height: auto;
        border: solid #1e2433;
        padding: 1;
        margin-bottom: 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._session: Optional[Session] = None

    def compose(self) -> ComposeResult:
        yield Label("Select a session →", id="detail-title")
        yield Label("", id="detail-meta")
        yield Label("", id="detail-task")
        with Horizontal(id="detail-actions"):
            yield Button(f"{Glyphs.AI_ICON} Open in Chat", id="btn-open", variant="default")
            yield Button("✏ Rename  [e]", id="btn-rename", variant="default")
            yield Button(f"{Glyphs.SPARKLE} Duplicate  [p]", id="btn-duplicate", variant="default")
            yield Button(f"{Glyphs.ERROR} Delete", id="btn-delete", variant="default")

    def update_session(self, s: Session) -> None:
        self._session = s
        self.query_one("#detail-title", Label).update(
            f"{Glyphs.SESSIONS} {s.title}"
        )

        # Build metadata block
        lines = [
            f"ID:        {s.id}",
            f"Status:    {s.status}",
        ]
        if s.model:
            lines.append(f"Model:     {s.model}")
        if s.provider:
            lines.append(f"Provider:  {s.provider}")
        if s.workspace:
            lines.append(f"Workspace: {s.workspace}")
        lines.append(f"Created:   {_fmt_ts(s.created_at)}")
        if s.updated_at:
            lines.append(f"Updated:   {_fmt_ts(s.updated_at)}  ({_age(s.updated_at)})")
        if s.compressed:
            lines.append("Compressed: Yes 🗜 (context was auto-compacted)")
        if s.tags:
            lines.append(f"Tags:      {', '.join(s.tags)}")

        self.query_one("#detail-meta", Label).update("\n".join(lines))

        task_lbl = self.query_one("#detail-task", Label)
        if s.task:
            task_lbl.update(f"Task:\n{s.task[:300]}")
            task_lbl.display = True
        else:
            task_lbl.display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if not self._session:
            return

        if event.button.id == "btn-open":
            state.set_session(self._session.id)
            state.navigate(AppScreen.CHAT)
            from aetherion_cli.tui.gateway import gateway
            gateway.resume_session(self._session.id)

        elif event.button.id == "btn-rename":
            # Delegate to list pane
            try:
                pane = self.app.query_one(SessionListPane)
                pane._enter_rename(self._session)
            except Exception:
                pass

        elif event.button.id == "btn-duplicate":
            from aetherion_cli.tui.gateway import gateway
            gateway.branch_session(self._session.id, f"{self._session.title} (copy)")
            state.push_notification(f'Duplicated "{self._session.title}"', "success")

        elif event.button.id == "btn-delete":
            from aetherion_cli.tui.gateway import gateway
            gateway.delete_session(self._session.id)
            self._session = None
            self.query_one("#detail-title", Label).update("Session deleted.")
            self.query_one("#detail-meta", Label).update("")


class SessionBrowserScreen(Widget):
    """2-pane session browser with full session lifecycle management."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter",  "open_session",   "Open",      show=True),
        Binding("n",      "new_session",    "New",       show=True),
        Binding("e",      "rename_session", "Rename",    show=True),
        Binding("p",      "dup_session",    "Duplicate", show=True),
        Binding("r",      "refresh",        "Refresh",   show=True),
        Binding("escape", "back",           "Back",      show=True),
    ]

    DEFAULT_CSS = """
    SessionBrowserScreen {
        height: 1fr;
        width: 1fr;
    }
    #sessions-toolbar {
        height: 1;
        background: #12151f;
        border-bottom: solid #1e2433;
        padding: 0 1;
        color: #64748b;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(
            f" {Glyphs.SESSIONS} Sessions   "
            f"[{Colors.TEXT_DIM}]↑↓[/] navigate  "
            f"[{Colors.TEXT_DIM}]Enter[/] open  "
            f"[{Colors.TEXT_DIM}]n[/] new  "
            f"[{Colors.TEXT_DIM}]e[/] rename  "
            f"[{Colors.TEXT_DIM}]p[/] duplicate  "
            f"[{Colors.TEXT_DIM}]r[/] refresh",
            id="sessions-toolbar"
        )
        with Horizontal():
            yield SessionListPane(id="session-list-pane")
            yield SessionDetailPane(id="session-detail-pane")

    def on_mount(self) -> None:
        from aetherion_cli.tui.gateway import gateway
        gateway.list_local_sessions()
        try:
            self.query_one(SessionListPane).focus()
        except Exception:
            pass

    def on_session_selected(self, session: Session) -> None:
        self.query_one(SessionDetailPane).update_session(session)

    def action_open_session(self) -> None:
        try:
            pane = self.query_one(SessionListPane)
            s = pane.selected_session()
            if s:
                state.set_session(s.id)
                state.navigate(AppScreen.CHAT)
                from aetherion_cli.tui.gateway import gateway
                gateway.resume_session(s.id)
        except Exception:
            pass

    def action_new_session(self) -> None:
        from aetherion_cli.tui.gateway import gateway
        gateway.create_session("New session")
        state.navigate(AppScreen.CHAT)

    def action_rename_session(self) -> None:
        try:
            pane = self.query_one(SessionListPane)
            s = pane.selected_session()
            if s:
                pane._enter_rename(s)
        except Exception:
            pass

    def action_dup_session(self) -> None:
        try:
            pane = self.query_one(SessionListPane)
            s = pane.selected_session()
            if s:
                from aetherion_cli.tui.gateway import gateway
                gateway.branch_session(s.id, f"{s.title} (copy)")
                state.push_notification(f'Duplicated "{s.title}"', "success")
        except Exception:
            pass

    def action_refresh(self) -> None:
        from aetherion_cli.tui.gateway import gateway
        gateway.list_local_sessions()

    def action_back(self) -> None:
        state.navigate_back()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _age(ts) -> str:
    if not ts:
        return "—"
    try:
        delta = time.time() - float(ts)
        if delta < 60:
            return f"{int(delta)}s ago"
        if delta < 3600:
            return f"{int(delta/60)}m ago"
        if delta < 86400:
            return f"{int(delta/3600)}h ago"
        return f"{int(delta/86400)}d ago"
    except Exception:
        return "—"


def _fmt_ts(ts) -> str:
    if not ts:
        return "—"
    try:
        import datetime
        return datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"
