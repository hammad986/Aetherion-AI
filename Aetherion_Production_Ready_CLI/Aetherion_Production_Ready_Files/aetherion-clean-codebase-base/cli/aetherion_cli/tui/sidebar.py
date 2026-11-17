"""
aetherion_cli/tui/sidebar.py
Left navigation sidebar — always visible unless toggled.

Design:
- Top section: user info + plan badge
- Nav items with icons, keyboard shortcuts, and active indicator
- Bottom section: connection status dot + version
- Collapses gracefully on narrow terminals
"""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Label, Static

from .state import Screen as AppScreen, state
from .theme import Colors, Glyphs

# Ordered nav items: (label, screen, icon, key_hint)
NAV_ITEMS = [
    ("Dashboard",     AppScreen.DASHBOARD,  Glyphs.SPARKLE,   "1"),
    ("Chat",          AppScreen.CHAT,       Glyphs.AI_ICON,   "2"),
    ("Sessions",      AppScreen.SESSIONS,   Glyphs.SESSIONS,  "3"),
    ("Memory",        AppScreen.MEMORY,     Glyphs.MEMORY,    "4"),
    ("Plugins",       AppScreen.PLUGINS,    Glyphs.PLUGINS,   "5"),
    ("Skills",        AppScreen.SKILLS,     Glyphs.SKILLS,    "6"),
    ("Providers",     AppScreen.PROVIDERS,  Glyphs.PROVIDERS, "7"),
    ("Jobs",          AppScreen.JOBS,       Glyphs.JOBS,      "8"),
    ("Profile",       AppScreen.PROFILE,    Glyphs.USER_ICON, "9"),
]


class NavItem(Static):
    """Single navigation item in the sidebar."""

    DEFAULT_CSS = """
    NavItem {
        height: 1;
        padding: 0 1;
        color: #64748b;
    }
    NavItem:hover {
        background: #1a1e2e;
        color: #e2e8f0;
    }
    NavItem.active {
        background: #4c2288;
        color: #9d5cf0;
        text-style: bold;
    }
    """

    def __init__(self, label: str, screen: AppScreen, icon: str, key_hint: str) -> None:
        super().__init__()
        self._label    = label
        self._screen   = screen
        self._icon     = icon
        self._key_hint = key_hint

    def on_mount(self) -> None:
        self._unsub = state.subscribe("current_screen", self._on_screen_changed)
        self._sync_active()

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _on_screen_changed(self) -> None:
        self.app.call_from_thread(self._sync_active)

    def _sync_active(self) -> None:
        if state.current_screen == self._screen:
            self.add_class("active")
        else:
            self.remove_class("active")
        self.refresh()

    def render(self) -> Text:  # type: ignore[override]
        active  = "active"  in self.classes
        compact = "compact" in self.classes
        t = Text(no_wrap=True)
        if compact:
            # Icon-only mode for narrow sidebar rail
            t.append(f" {self._icon} ", style=Colors.ACCENT if active else Colors.TEXT_MUTED)
            return t
        if active:
            t.append("┃ ", style=f"bold {Colors.ACCENT}")
        else:
            t.append("  ")
        t.append(f"{self._icon} ", style=Colors.ACCENT if active else Colors.TEXT_MUTED)
        t.append(self._label, style=f"bold {Colors.ACCENT_BRIGHT}" if active else Colors.TEXT_MUTED)
        return t

    def on_click(self, event) -> None:
        state.navigate(self._screen)


class SidebarHeader(Static):
    """Top section: Aetherion logo + user info."""

    DEFAULT_CSS = """
    SidebarHeader {
        height: 4;
        border-bottom: solid #1e2433;
        padding: 0 1;
        background: #0a0c12;
    }
    """

    def on_mount(self) -> None:
        self._unsub = state.subscribe("user_email", self._refresh)

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _refresh(self) -> None:
        self.app.call_from_thread(self.refresh)

    def render(self) -> Text:  # type: ignore[override]
        t = Text()
        t.append(f" {Glyphs.SPARKLE} Aetherion\n", style=f"bold {Colors.ACCENT_BRIGHT}")
        t.append("  AI Workspace\n", style=Colors.TEXT_DIM)
        if state.user_email:
            t.append(f"  {state.user_email[:18]}\n", style=Colors.TEXT_MUTED)
            plan_color = Colors.ACCENT if state.user_plan != "Lite" else Colors.TEXT_DIM
            t.append(f"  {Glyphs.STAR} {state.user_plan}", style=plan_color)
        else:
            t.append("  Not logged in", style=Colors.TEXT_DIM)
        return t

    def on_click(self, event) -> None:
        """Click header to open Profile portal."""
        state.navigate(AppScreen.PROFILE)


class SidebarFooter(Static):
    """Bottom section: connection status + shortcut hint."""

    DEFAULT_CSS = """
    SidebarFooter {
        height: 3;
        border-top: solid #1e2433;
        padding: 0 1;
        background: #0a0c12;
        dock: bottom;
    }
    """

    def on_mount(self) -> None:
        self._unsub = state.subscribe("connected", self._refresh)

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _refresh(self) -> None:
        self.app.call_from_thread(self.refresh)

    def render(self) -> Text:  # type: ignore[override]
        t = Text()
        if state.connected:
            dot   = Glyphs.DONE
            color = Colors.SUCCESS
            label = "Connected"
            if state.latency_ms:
                label += f" {state.latency_ms}ms"
        else:
            dot   = Glyphs.ERROR
            color = Colors.ERROR
            label = "Offline"
        t.append(f"  {dot} {label}\n", style=color)
        t.append(f"  Ctrl+P palette  Ctrl+M model\n", style=Colors.TEXT_DIM)
        t.append(f"  Ctrl+B toggle sidebar", style=Colors.TEXT_DIM)
        return t


class Sidebar(Widget):
    """Left navigation sidebar with adaptive auto-collapse.

    At < 100 terminal columns the sidebar enters a compact icon-only rail
    (width=4) to preserve horizontal space for the chat area.
    The user can always toggle back with Ctrl+B regardless of mode.
    """

    DEFAULT_CSS = """
    Sidebar {
        width: 22;
        background: #12151f;
        border-right: solid #1e2433;
        height: 1fr;
    }
    Sidebar.compact {
        width: 4;
    }
    Sidebar #nav-section {
        height: 1fr;
        margin-top: 0;
    }
    Sidebar #nav-hint {
        height: 1;
        color: #334155;
        padding: 0 2;
        margin-bottom: 1;
    }
    Sidebar.compact #nav-hint {
        display: none;
    }
    """

    def compose(self) -> ComposeResult:
        yield SidebarHeader()
        with Vertical(id="nav-section"):
            yield Label("  WORKSPACE", id="nav-hint")
            for label, screen, icon, key_hint in NAV_ITEMS:
                yield NavItem(label, screen, icon, key_hint)
        yield SidebarFooter()

    def on_mount(self) -> None:
        # Subscribe to terminal resize to auto-adapt
        self._unsub_ws = state.subscribe("left_sidebar_open", self._on_sidebar_toggle)
        # Check initial terminal size
        self._check_width()

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub_ws"):
            self._unsub_ws()

    def _on_sidebar_toggle(self) -> None:
        """Called when user explicitly toggles via Ctrl+B."""
        self.app.call_from_thread(self._check_width)

    def on_resize(self, event) -> None:
        """Auto-collapse when terminal narrows below 100 columns."""
        self._check_width()

    def _check_width(self) -> None:
        """Enter compact icon-only mode if terminal is narrow."""
        try:
            # app.size is a Size(cols, rows) namedtuple in Textual
            cols = self.app.size.width
            if cols < 100 and state.left_sidebar_open:
                self.add_class("compact")
                for item in self.query(NavItem):
                    item.add_class("compact")
            else:
                self.remove_class("compact")
                for item in self.query(NavItem):
                    item.remove_class("compact")
        except Exception:
            pass
