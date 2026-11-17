"""
aetherion_cli/tui/screens/memory.py
Memory Explorer — search, browse, and add memory entries.

2-pane: search + list left | entry detail right.
Keyboard: / to focus search, ↑↓ to navigate, Enter to view, a to add, d to delete.
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
from textual.widgets import Button, Input, Label, Static, TextArea

from aetherion_cli.tui.state import MemoryEntry, Screen as AppScreen, state
from aetherion_cli.tui.theme import Colors, Glyphs


class MemoryListPane(Widget):
    """Left pane: search + scrollable memory list."""

    DEFAULT_CSS = """
    MemoryListPane {
        width: 40;
        border-right: solid #1e2433;
        background: #12151f;
        height: 1fr;
    }
    MemoryListPane #memory-search-bar {
        height: 3;
        border-bottom: solid #1e2433;
        padding: 0 1;
    }
    MemoryListPane #memory-search-bar Input {
        border: none;
        background: #12151f;
    }
    MemoryListPane #memory-list-scroll {
        height: 1fr;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._selected_idx = 0
        self._query        = ""

    def compose(self) -> ComposeResult:
        with Container(id="memory-search-bar"):
            yield Input(placeholder=f"{Glyphs.MEMORY} Search memory…", id="memory-search-input")
        yield ScrollableContainer(id="memory-list-scroll")

    def on_mount(self) -> None:
        self._unsub = state.subscribe("memory_entries", self._on_memory_changed)
        self._rebuild()

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _on_memory_changed(self) -> None:
        self.app.call_from_thread(self._rebuild)

    def _rebuild(self) -> None:
        scroll = self.query_one("#memory-list-scroll")
        scroll.remove_children()

        if state.memory_loading:
            scroll.mount(Static(f"  {Glyphs.RUNNING} Loading memory…", classes="loading-indicator"))
            return

        if state.memory_error:
            scroll.mount(Static(f"  {Glyphs.ERROR} {state.memory_error}", classes="error-state"))
            return

        entries = state.memory_entries
        if not entries:
            scroll.mount(Static(
                f"\n  {Glyphs.MEMORY}  No memories found.\n\n"
                "  Press [a] to add one, or type to search.",
                classes="empty-state"
            ))
            return

        self._selected_idx = max(0, min(self._selected_idx, len(entries) - 1))
        for i, e in enumerate(entries[:60]):
            row = _MemoryRow(e, selected=i == self._selected_idx, idx=i)
            scroll.mount(row)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "memory-search-input":
            self._query = event.value
            self._selected_idx = 0
            from aetherion_cli.tui.gateway import gateway
            gateway.load_memory(query=self._query)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "memory-search-input":
            from aetherion_cli.tui.gateway import gateway
            gateway.load_memory(query=self._query)

    def on_key(self, event: events.Key) -> None:
        entries = state.memory_entries
        n = len(entries)
        if n == 0:
            return

        if event.key == "up":
            self._selected_idx = max(0, self._selected_idx - 1)
            self._rebuild()
            if 0 <= self._selected_idx < n:
                self.app.on_memory_selected(entries[self._selected_idx])
            event.prevent_default()

        elif event.key == "down":
            self._selected_idx = min(n - 1, self._selected_idx + 1)
            self._rebuild()
            if 0 <= self._selected_idx < n:
                self.app.on_memory_selected(entries[self._selected_idx])
            event.prevent_default()

    def selected_entry(self) -> Optional[MemoryEntry]:
        entries = state.memory_entries
        if 0 <= self._selected_idx < len(entries):
            return entries[self._selected_idx]
        return None


class _MemoryRow(Static):
    DEFAULT_CSS = """
    _MemoryRow {
        height: 2;
        padding: 0 1;
        border-bottom: solid #1e2433;
    }
    _MemoryRow.selected {
        background: #4c2288;
    }
    """

    def __init__(self, entry: MemoryEntry, selected: bool = False, idx: int = 0) -> None:
        super().__init__()
        self._entry    = entry
        self._selected = selected
        if selected:
            self.add_class("selected")

    def render(self) -> Text:  # type: ignore[override]
        e = self._entry
        imp = e.importance
        if imp > 0.8:
            imp_color = Colors.ERROR
            imp_icon  = Glyphs.FIRE
        elif imp > 0.5:
            imp_color = Colors.WARNING
            imp_icon  = Glyphs.STAR
        else:
            imp_color = Colors.TEXT_DIM
            imp_icon  = Glyphs.BULLET

        t = Text()
        prefix = f"{Glyphs.CHEVRON_RIGHT} " if self._selected else "  "
        t.append(prefix, style=f"bold {Colors.ACCENT}" if self._selected else Colors.TEXT_DIM)
        t.append(f"{imp_icon} ", style=imp_color)
        t.append(e.content[:36], style=Colors.TEXT_PRIMARY if self._selected else Colors.TEXT_PRIMARY)
        t.append("\n")
        if e.created_at:
            t.append(f"  {_age(e.created_at)}", style=Colors.TEXT_DIM)
        if e.tags:
            t.append(f"  {' '.join(e.tags[:3])}", style=Colors.ACCENT_DIM)
        return t


class MemoryDetailPane(Widget):
    """Right pane: full memory entry view + add form."""

    DEFAULT_CSS = """
    MemoryDetailPane {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
        background: #0a0c12;
    }
    #memory-detail-content {
        height: auto;
        min-height: 4;
        border: solid #1e2433;
        padding: 1;
        background: #12151f;
        color: #e2e8f0;
        margin-bottom: 1;
    }
    #memory-detail-meta {
        color: #64748b;
        height: auto;
        margin-bottom: 1;
    }
    #memory-add-area {
        height: auto;
        border-top: solid #1e2433;
        padding-top: 1;
    }
    #memory-add-input {
        height: 4;
        border: solid #3d4a6b;
        background: #12151f;
        margin-bottom: 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._entry: Optional[MemoryEntry] = None

    def compose(self) -> ComposeResult:
        yield Label(f" {Glyphs.MEMORY} Memory Entry", id="memory-detail-header",
                    classes="dash-section-title")
        yield Label("Select an entry to view it.", id="memory-detail-content")
        yield Label("", id="memory-detail-meta")
        with Container(id="memory-add-area"):
            yield Label(f" {Glyphs.SPARKLE} Add new memory:", classes="dash-section-title")
            yield TextArea(id="memory-add-input")
            with Horizontal():
                yield Button(f"{Glyphs.DONE} Save", id="btn-save-memory", variant="default")
                yield Button(f"{Glyphs.ERROR} Clear", id="btn-clear-memory", variant="default")

    def show_entry(self, e: MemoryEntry) -> None:
        self._entry = e
        self.query_one("#memory-detail-content", Label).update(e.content)
        imp_pct = int(e.importance * 100)
        tags    = " · ".join(e.tags) if e.tags else "no tags"
        meta    = f"Importance: {imp_pct}%  ·  {tags}"
        if e.created_at:
            meta += f"  ·  {_age(e.created_at)}"
        self.query_one("#memory-detail-meta", Label).update(meta)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save-memory":
            ta = self.query_one("#memory-add-input", TextArea)
            content = ta.text.strip()
            if content:
                from aetherion_cli.tui.gateway import gateway
                def _clear_input():
                    self.app.call_from_thread(ta.clear)
                gateway.add_memory(content, on_success=_clear_input)
        elif event.button.id == "btn-clear-memory":
            self.query_one("#memory-add-input", TextArea).clear()


class MemoryScreen(Widget):
    """Memory Explorer — 2-pane search + browse."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("r",      "refresh",  "Refresh",  show=True),
        Binding("escape", "back",     "Back",     show=True),
    ]

    DEFAULT_CSS = """
    MemoryScreen {
        height: 1fr;
        width: 1fr;
    }
    #memory-toolbar {
        height: 1;
        background: #12151f;
        border-bottom: solid #1e2433;
        padding: 0 1;
        color: #64748b;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(
            f" {Glyphs.MEMORY} Memory Explorer   "
            f"[{Colors.TEXT_DIM}]↑↓[/] navigate  "
            f"[{Colors.TEXT_DIM}]/ or type[/] search  "
            f"[{Colors.TEXT_DIM}]r[/] refresh",
            id="memory-toolbar"
        )
        with Horizontal():
            yield MemoryListPane(id="memory-list-pane")
            yield MemoryDetailPane(id="memory-detail-pane")

    def on_mount(self) -> None:
        from aetherion_cli.tui.gateway import gateway
        gateway.load_memory()

    def on_memory_selected(self, entry: MemoryEntry) -> None:
        self.query_one(MemoryDetailPane).show_entry(entry)

    def action_refresh(self) -> None:
        from aetherion_cli.tui.gateway import gateway
        gateway.load_memory(query="")

    def action_back(self) -> None:
        state.navigate_back()


def _age(ts) -> str:
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
