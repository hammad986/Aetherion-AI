"""
aetherion_cli/tui/screens/plugins.py
Plugin Manager — marketplace + installed, 2-pane with category filter.
"""
from __future__ import annotations

from typing import ClassVar, List, Optional

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Static

from aetherion_cli.tui.state import Plugin, Screen as AppScreen, state
from aetherion_cli.tui.theme import Colors, Glyphs


class PluginCard(Static):
    """Single plugin card in the list."""

    DEFAULT_CSS = """
    PluginCard {
        height: 3;
        padding: 0 1;
        border-bottom: solid #1e2433;
    }
    PluginCard.selected {
        background: #4c2288;
    }
    PluginCard:hover {
        background: #1a1e2e;
    }
    """

    def __init__(self, plugin: Plugin, selected: bool = False) -> None:
        super().__init__()
        self._plugin = plugin
        if selected:
            self.add_class("selected")

    def render(self) -> Text:  # type: ignore[override]
        p   = self._plugin
        t   = Text()
        sel = "selected" in self.classes
        prefix = f"{Glyphs.CHEVRON_RIGHT} " if sel else "  "
        t.append(prefix, style=f"bold {Colors.ACCENT}" if sel else Colors.TEXT_DIM)

        if p.installed:
            t.append(f"{Glyphs.DONE} ", style=Colors.SUCCESS)
        else:
            t.append(f"{Glyphs.PLUGINS} ", style=Colors.TEXT_DIM)

        t.append(p.name[:28], style=f"bold {Colors.TEXT_PRIMARY}")
        t.append(f"  v{p.version}", style=Colors.TEXT_DIM)
        t.append("\n")
        t.append(f"  {p.description[:52]}", style=Colors.TEXT_MUTED)
        return t


class PluginDetailPane(Widget):
    """Right pane: plugin details + install/uninstall."""

    DEFAULT_CSS = """
    PluginDetailPane {
        width: 1fr;
        padding: 1 2;
        background: #0a0c12;
        height: 1fr;
    }
    #plugin-title {
        color: #9d5cf0;
        text-style: bold;
        height: 2;
    }
    #plugin-meta {
        color: #64748b;
        height: auto;
        margin-bottom: 1;
    }
    #plugin-desc {
        color: #e2e8f0;
        height: auto;
        border: solid #1e2433;
        padding: 1;
        margin-bottom: 1;
        background: #12151f;
    }
    #plugin-actions {
        height: 3;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._plugin: Optional[Plugin] = None

    def compose(self) -> ComposeResult:
        yield Label(f"{Glyphs.PLUGINS} Select a plugin →", id="plugin-title")
        yield Label("", id="plugin-meta")
        yield Label("", id="plugin-desc")
        with Horizontal(id="plugin-actions"):
            yield Button(f"{Glyphs.PLUGINS} Install", id="btn-install", variant="default")
            yield Button(f"{Glyphs.ERROR} Uninstall", id="btn-uninstall", variant="default")

    def show_plugin(self, p: Plugin) -> None:
        self._plugin = p
        self.query_one("#plugin-title", Label).update(f"{Glyphs.PLUGINS} {p.name}  v{p.version}")
        status = f"{Glyphs.DONE} Installed" if p.installed else f"{Glyphs.IDLE} Not installed"
        status_color = Colors.SUCCESS if p.installed else Colors.TEXT_MUTED
        self.query_one("#plugin-meta", Label).update(
            f"Author: {p.author or '—'}   Category: {p.category}\n"
            f"[{status_color}]{status}[/{status_color}]"
        )
        self.query_one("#plugin-desc", Label).update(p.description or "No description.")
        install_btn   = self.query_one("#btn-install", Button)
        uninstall_btn = self.query_one("#btn-uninstall", Button)
        install_btn.disabled   = p.installed
        uninstall_btn.disabled = not p.installed

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if not self._plugin:
            return
        from aetherion_cli.tui.gateway import gateway
        if event.button.id == "btn-install" and not self._plugin.installed:
            state.push_notification(f"Installing {self._plugin.name}…")
            gateway.install_plugin(self._plugin.id, self._plugin.name)
        elif event.button.id == "btn-uninstall" and self._plugin.installed:
            state.push_notification(f"Uninstalling {self._plugin.name}…")
            gateway.uninstall_plugin(self._plugin.id, self._plugin.name)



class PluginsScreen(Widget):
    """Plugin Manager: filter bar + scrollable card list + detail pane."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("r",      "refresh", "Refresh", show=True),
        Binding("escape", "back",    "Back",    show=True),
        Binding("i",      "show_installed", "Installed only", show=True),
        Binding("a",      "show_all",       "All",            show=True),
    ]

    DEFAULT_CSS = """
    PluginsScreen {
        height: 1fr;
        width: 1fr;
    }
    #plugins-toolbar {
        height: 1;
        background: #12151f;
        border-bottom: solid #1e2433;
        padding: 0 1;
        color: #64748b;
    }
    #plugins-left {
        width: 44;
        border-right: solid #1e2433;
        background: #12151f;
        height: 1fr;
    }
    #plugins-filter-bar {
        height: 3;
        border-bottom: solid #1e2433;
        padding: 0 1;
    }
    #plugins-filter-bar Input {
        border: none;
        background: #12151f;
    }
    #plugins-list-scroll {
        height: 1fr;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._filter       = ""
        self._selected_idx = 0
        self._installed_only = False

    def compose(self) -> ComposeResult:
        yield Label(
            f" {Glyphs.PLUGINS} Plugins   "
            f"[{Colors.TEXT_DIM}]↑↓[/] navigate  "
            f"[{Colors.TEXT_DIM}]i[/] installed  "
            f"[{Colors.TEXT_DIM}]a[/] all  "
            f"[{Colors.TEXT_DIM}]r[/] refresh",
            id="plugins-toolbar"
        )
        with Horizontal():
            with Vertical(id="plugins-left"):
                with Container(id="plugins-filter-bar"):
                    yield Input(placeholder=f"{Glyphs.BULLET} Filter plugins…", id="plugins-filter")
                yield ScrollableContainer(id="plugins-list-scroll")
            yield PluginDetailPane(id="plugins-detail")

    def on_mount(self) -> None:
        self._unsub = state.subscribe("plugins", self._on_plugins_changed)
        from aetherion_cli.tui.gateway import gateway
        gateway.load_plugins()

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _filtered(self) -> List[Plugin]:
        q = self._filter.lower()
        ps = state.plugins
        if self._installed_only:
            ps = [p for p in ps if p.installed]
        if q:
            ps = [p for p in ps if q in p.name.lower() or q in p.description.lower()]
        return ps

    def _on_plugins_changed(self) -> None:
        self.app.call_from_thread(self._rebuild)

    def _rebuild(self) -> None:
        scroll = self.query_one("#plugins-list-scroll")
        scroll.remove_children()
        plugins = self._filtered()

        if state.plugins_loading:
            scroll.mount(Static(f"  {Glyphs.RUNNING} Loading plugins…", classes="loading-indicator"))
            return

        if state.plugins_error:
            scroll.mount(Static(f"  {Glyphs.ERROR} {state.plugins_error}", classes="error-state"))
            return

        if not plugins:
            scroll.mount(Static(
                f"\n  {Glyphs.PLUGINS}  No plugins found.\n\n  Try [a] to show all.",
                classes="empty-state"
            ))
            return

        self._selected_idx = max(0, min(self._selected_idx, len(plugins) - 1))
        for i, p in enumerate(plugins[:80]):
            card = PluginCard(p, selected=i == self._selected_idx)
            scroll.mount(card)

        # Show selected in detail pane
        if 0 <= self._selected_idx < len(plugins):
            self.query_one(PluginDetailPane).show_plugin(plugins[self._selected_idx])

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "plugins-filter":
            self._filter = event.value
            self._selected_idx = 0
            self._rebuild()

    def on_key(self, event: events.Key) -> None:
        plugins = self._filtered()
        n = len(plugins)
        if event.key == "up" and n:
            self._selected_idx = max(0, self._selected_idx - 1)
            self._rebuild()
            event.prevent_default()
        elif event.key == "down" and n:
            self._selected_idx = min(n - 1, self._selected_idx + 1)
            self._rebuild()
            event.prevent_default()
        elif event.key == "enter" and n:
            pass  # detail already updated in _rebuild

    def action_show_installed(self) -> None:
        self._installed_only = True
        self._selected_idx   = 0
        self._rebuild()

    def action_show_all(self) -> None:
        self._installed_only = False
        self._selected_idx   = 0
        self._rebuild()

    def action_refresh(self) -> None:
        from aetherion_cli.tui.gateway import gateway
        gateway.load_plugins()

    def action_back(self) -> None:
        state.navigate_back()
