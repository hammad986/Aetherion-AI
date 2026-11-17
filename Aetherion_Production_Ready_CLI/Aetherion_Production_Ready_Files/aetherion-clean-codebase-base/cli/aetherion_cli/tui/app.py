"""
aetherion_cli/tui/app.py
Aetherion Terminal Workspace — Main Application

One App, many Screens rendered as Widgets inside a single layout.
Overlay swap (not stack): when an overlay is shown, it covers the
content area entirely via overlay:screen and display:none toggling.

Navigation:
  1–8          → switch screens (Dashboard / Chat / Sessions / ...)
  Ctrl+P       → command palette
  Ctrl+B       → toggle sidebar
  Ctrl+N       → new session (goes to chat)
  Esc          → back / dismiss overlay
  F1           → help overlay
  ?            → same as F1

Global chat:
  Any screen: typing a message and pressing Enter opens Chat automatically.

Crash recovery:
  Gateway installs sys.excepthook and threading.excepthook on start().
  State.error_message drives the error banner in the status bar.
"""
from __future__ import annotations

import logging
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Label, Static

from .gateway import gateway
from .overlays import ApprovalOverlay, CommandPaletteOverlay, NotificationBanner, StatusBar, ErrorOverlay
from .screens.chat import ChatScreen
from .screens.dashboard import DashboardScreen
from .screens.jobs import JobsScreen
from .screens.memory import MemoryScreen
from .screens.plugins import PluginsScreen
from .screens.providers import ProvidersScreen
from .screens.sessions import SessionBrowserScreen
from .screens.skills import SkillsScreen
from .screens.welcome import WelcomeScreen
from .screens.profile import ProfileScreen
from .sidebar import Sidebar
from .state import AgentStatus, Screen as AppScreen, Session, state
from .theme import AETHERION_CSS, Colors, Glyphs

log = logging.getLogger("aetherion.tui.app")

# ─────────────────────────────────────────────────────────────────────────────
# Screen registry — maps AppScreen enum → Widget class
# ─────────────────────────────────────────────────────────────────────────────

SCREEN_REGISTRY = {
    AppScreen.DASHBOARD: DashboardScreen,
    AppScreen.CHAT:      ChatScreen,
    AppScreen.SESSIONS:  SessionBrowserScreen,
    AppScreen.MEMORY:    MemoryScreen,
    AppScreen.PLUGINS:   PluginsScreen,
    AppScreen.SKILLS:    SkillsScreen,
    AppScreen.PROVIDERS: ProvidersScreen,
    AppScreen.JOBS:      JobsScreen,
    AppScreen.WELCOME:   WelcomeScreen,
    AppScreen.PROFILE:   ProfileScreen,
}


class ContentArea(Widget):
    """
    The main content area that swaps the active screen in/out.
    Uses Textual's internal children mounting approach (no push_screen overhead).
    """

    DEFAULT_CSS = """
    ContentArea {
        height: 1fr;
        width: 1fr;
        background: #0a0c12;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._current_screen: AppScreen | None = None
        self._current_widget: Widget | None    = None

    def on_mount(self) -> None:
        self._unsub = state.subscribe("current_screen", self._on_screen_changed)
        self._show_screen(state.current_screen)

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _on_screen_changed(self) -> None:
        """Called from state._notify — may be on any thread. Use call_from_thread."""
        import threading
        if threading.current_thread() is threading.main_thread():
            self._show_screen(state.current_screen)
        else:
            try:
                self.app.call_from_thread(self._show_screen, state.current_screen)
            except Exception:
                pass

    def _show_screen(self, screen: AppScreen) -> None:
        if screen == self._current_screen:
            return

        # Remove old screen widgets cleanly to prevent orphaned controls
        self.remove_children()
        self._current_widget = None

        # Mount new screen widget
        cls = SCREEN_REGISTRY.get(screen)
        if cls is None:
            fallback = Static(
                f"\n\n  {Glyphs.IDLE}  Screen '{screen.value}' is not yet implemented.\n\n"
                "  Press [1] to return to the Dashboard.",
                classes="empty-state"
            )
            self.mount(fallback)
            self._current_widget = fallback
        else:
            w = cls()
            self.mount(w)
            self._current_widget = w

        self._current_screen = screen


class BreadcrumbBar(Static):
    """1-row breadcrumb showing current screen and keyboard shortcuts."""

    DEFAULT_CSS = """
    BreadcrumbBar {
        height: 1;
        background: #0a0c12;
        border-bottom: solid #1e2433;
        padding: 0 1;
        color: #64748b;
    }
    """

    def on_mount(self) -> None:
        self._subs = [
            state.subscribe("current_screen",  self._refresh),
            state.subscribe("workspace_info",  self._refresh),
        ]

    def on_unmount(self) -> None:
        for u in getattr(self, "_subs", []):
            u()

    def _refresh(self) -> None:
        """Called from state._notify — may be on any thread. Use call_from_thread."""
        self.app.call_from_thread(self.refresh)

    def render(self):  # type: ignore[override]
        from rich.text import Text
        s = state.current_screen
        t = Text(no_wrap=True)
        t.append(f" {Glyphs.ARROW_RIGHT} {s.value.title()}", style=Colors.ACCENT_BRIGHT)
        # Show workspace + branch if available
        if state.workspace_path:
            dirty = "±" if state.git_dirty else ""
            branch = f"  {state.git_branch}{dirty}" if state.git_branch else ""
            t.append(f"   {state.workspace_path}{branch}", style=Colors.TEXT_DIM)
        t.append(
            f"   [{Colors.TEXT_DIM}]Ctrl+P[/{Colors.TEXT_DIM}] palette  "
            f"[{Colors.TEXT_DIM}]Ctrl+M[/{Colors.TEXT_DIM}] model  "
            f"[{Colors.TEXT_DIM}]Ctrl+B[/{Colors.TEXT_DIM}] sidebar  "
            f"[{Colors.TEXT_DIM}]Esc[/{Colors.TEXT_DIM}] back"
        )
        return t


# ─────────────────────────────────────────────────────────────────────────────
# Main Application
# ─────────────────────────────────────────────────────────────────────────────

class AetherionApp(App):
    """
    Aetherion Terminal Workspace.

    Single App, single layout.  Navigation swaps widgets inside ContentArea.
    All I/O lives in gateway daemon threads.  All state flows through TUIState.
    """

    TITLE    = "Aetherion"
    CSS      = AETHERION_CSS
    ENABLE_COMMAND_PALETTE: bool = False

    BINDINGS: ClassVar[list[Binding]] = [
        # Screen shortcuts
        Binding("1", "nav_dashboard",  "Dashboard",  show=False),
        Binding("2", "nav_chat",       "Chat",       show=False),
        Binding("3", "nav_sessions",   "Sessions",   show=False),
        Binding("4", "nav_memory",     "Memory",     show=False),
        Binding("5", "nav_plugins",    "Plugins",    show=False),
        Binding("6", "nav_skills",     "Skills",     show=False),
        Binding("7", "nav_providers",  "Providers",  show=False),
        Binding("8", "nav_jobs",       "Jobs",       show=False),
        Binding("9", "nav_profile",    "Profile",    show=False),

        # Global
        Binding("ctrl+p",  "open_palette",    "Palette",      show=True),
        Binding("ctrl+b",  "toggle_sidebar",  "Sidebar",      show=False),
        Binding("ctrl+n",  "new_session",     "New Session",  show=False),
        Binding("ctrl+l",  "clear_messages",  "Clear",        show=False),
        Binding("escape",  "handle_escape",   "Back",         show=False),
        Binding("f1",      "show_help",       "Help",         show=False),
    ]

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        # Overlays first — they float above everything
        yield CommandPaletteOverlay(id="cmd-palette")
        yield ApprovalOverlay(id="approval-overlay")
        yield ErrorOverlay(id="error-overlay")

        # Main layout
        with Vertical(id="app-root"):
            yield NotificationBanner(id="notification-banner")
            with Horizontal(id="workspace-container"):
                yield Sidebar(id="left-sidebar")
                with Vertical(id="main-area"):
                    yield BreadcrumbBar()
                    yield ContentArea(id="content-area")
            yield StatusBar(id="status-bar")

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        # 1. Start gateway threads (health + notifications)
        gateway.base_url = self._resolve_base_url()
        gateway.start()

        # 2. Attempt silent login in background before deciding which screen to show
        self.run_worker(self._boot_sequence(), name="aetherion-boot", exclusive=True)

        # 3. Welcome message (visible in chat, shown after navigation resolves)
        from .state import Message
        state.append_message(Message.system(
            f"{Glyphs.SPARKLE} Welcome to Aetherion.  "
            "Press [bold]1[/bold] for Dashboard, [bold]2[/bold] for Chat, "
            "[bold]Ctrl+P[/bold] for command palette."
        ))
        log.info("Aetherion TUI mounted")

    async def _boot_sequence(self) -> None:
        """
        Boot sequence (runs in background worker):
          1. Try silent login from stored refresh token.
          2. If successful → navigate to Dashboard, load initial data.
          3. If failed   → navigate to Welcome screen.
          4. Detect workspace / git info (always runs, non-blocking).
        """
        import asyncio
        # Give the layout a moment to settle
        await asyncio.sleep(0.15)

        # Detect workspace and git info in the background (non-blocking)
        self.run_worker(
            self._detect_workspace_info(),
            name="aetherion-workspace-detect",
            exclusive=False,
        )

        # Attempt silent re-auth
        ok = await asyncio.get_running_loop().run_in_executor(None, gateway.silent_login)

        if ok:
            # Authenticated — load workspace data
            gateway.load_user_profile()
            gateway.load_dashboard()
            gateway.list_local_sessions()
            gateway.load_plugins()
            if state.current_screen in (AppScreen.WELCOME, AppScreen.DASHBOARD):
                state.navigate(getattr(state, "saved_screen", AppScreen.CHAT))
        else:
            # No stored credentials — show login screen if not offline
            if not state.offline_mode:
                state.navigate(AppScreen.WELCOME)

    async def _detect_workspace_info(self) -> None:
        """
        Detect the current git branch, working directory, and dirty state.
        Runs subprocess calls asynchronously so the UI never blocks.
        All errors are silently swallowed — this is best-effort metadata.
        """
        import asyncio
        import os
        import subprocess

        cwd = os.getcwd()
        workspace_label = os.path.basename(cwd) or cwd
        branch     = ""
        is_dirty   = False

        try:
            # Get current branch name
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--abbrev-ref", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
            raw_branch = stdout.decode().strip()
            if raw_branch and raw_branch != "HEAD":
                branch = raw_branch[:30]  # cap at 30 chars

            # Check dirty state
            proc2 = await asyncio.create_subprocess_exec(
                "git", "status", "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=cwd,
            )
            stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=3.0)
            is_dirty = bool(stdout2.decode().strip())
        except Exception:
            # Not a git repo, git not installed, or timeout — silently skip
            pass

        state.set_workspace_info(
            workspace_path=workspace_label,
            git_branch=branch,
            git_dirty=is_dirty,
        )



    def _resolve_base_url(self) -> str:
        try:
            from ..config import get_server_url
            return get_server_url().rstrip("/")
        except Exception:
            import os
            from pathlib import Path
            return "http://app:5000" if Path("/.dockerenv").exists() else "http://localhost:5000"

    # ── Navigation actions ────────────────────────────────────────────────────

    def action_nav_dashboard(self) -> None:
        state.navigate(AppScreen.DASHBOARD)
    def action_nav_chat(self) -> None:
        state.navigate(AppScreen.CHAT)
    def action_nav_sessions(self) -> None:
        state.navigate(AppScreen.SESSIONS)
    def action_nav_memory(self) -> None:
        state.navigate(AppScreen.MEMORY)
    def action_nav_plugins(self) -> None:
        state.navigate(AppScreen.PLUGINS)
    def action_nav_skills(self) -> None:
        state.navigate(AppScreen.SKILLS)
    def action_nav_providers(self) -> None:
        state.navigate(AppScreen.PROVIDERS)
    def action_nav_jobs(self) -> None:
        state.navigate(AppScreen.JOBS)
    def action_nav_profile(self) -> None:
        state.navigate(AppScreen.PROFILE)

    # ── Global actions ────────────────────────────────────────────────────────

    def action_open_palette(self) -> None:
        state.set_palette(True)

    def action_toggle_sidebar(self) -> None:
        state.toggle_sidebar()
        sidebar = self.query_one("#left-sidebar", Sidebar)
        if state.left_sidebar_open:
            sidebar.remove_class("hidden")
        else:
            sidebar.add_class("hidden")

    def action_new_session(self) -> None:
        gateway.create_session()
        state.navigate(AppScreen.CHAT)

    def action_clear_messages(self) -> None:
        state.clear_messages()

    def action_handle_escape(self) -> None:
        # Priority: close palette → dismiss approval → go back → clear error
        if state.palette_open:
            state.set_palette(False)
            self.set_focus(None)
            return
        if state.pending_approval:
            gateway.approve_tool(state.pending_approval.request_id, "deny")
            return
        if state.active_notification:
            state.dismiss_notification()
            return
        if state.error_message:
            state.set_error(None)
            return
        state.navigate_back()

    def action_show_help(self) -> None:
        from .state import Message
        help_text = (
            "╔═ Aetherion Keyboard Reference ═══════════════════════════════╗\n"
            "║  1-8        Switch workspace screen                          ║\n"
            "║  Ctrl+P     Open command palette                             ║\n"
            "║  Ctrl+B     Toggle sidebar                                   ║\n"
            "║  Ctrl+N     New session                                      ║\n"
            "║  Escape     Back / dismiss overlay                           ║\n"
            "║                                                              ║\n"
            "║  In Chat:                                                    ║\n"
            "║    Enter          Submit message                             ║\n"
            "║    Shift+Enter    New line                                   ║\n"
            "║    Ctrl+U         Clear input                                ║\n"
            "║    Ctrl+L         Clear conversation                         ║\n"
            "║    ↑/↓            History (single-line mode)                ║\n"
            "║                                                              ║\n"
            "║  In lists:                                                   ║\n"
            "║    ↑/↓            Navigate                                  ║\n"
            "║    Enter          Select / open                              ║\n"
            "║    /  or type     Filter                                     ║\n"
            "║    n              New item                                   ║\n"
            "║    r              Refresh                                    ║\n"
            "║    d              Delete selected                            ║\n"
            "║                                                              ║\n"
            "║  Approvals:                                                  ║\n"
            "║    A              Allow tool execution                       ║\n"
            "║    D / Escape     Deny                                       ║\n"
            "╚══════════════════════════════════════════════════════════════╝"
        )
        state.navigate(AppScreen.CHAT)
        state.append_message(Message.system(help_text))

    # ── Chat submission (called by ChatInput) ─────────────────────────────────

    def handle_chat_submit(self, text: str) -> None:
        """Handle a submitted message from the chat input bar."""
        if state.status == AgentStatus.WAITING_APPROVAL:
            state.push_notification("Waiting for approval — press A to allow or D to deny.")
            return
        if state.status in (AgentStatus.RUNNING, AgentStatus.STREAMING):
            state.push_notification("Agent is busy — wait for it to finish or press Esc to cancel.")
            return

        from .state import Message
        state.navigate(AppScreen.CHAT)
        state.append_message(Message.user(text))
        gateway.start_stream(task=text)

    # ── Palette action dispatcher ─────────────────────────────────────────────

    def dispatch_palette_action(self, action_id: str) -> None:
        """Execute a command palette action by its id string."""
        if action_id.startswith("nav:"):
            screen_name = action_id[4:]
            try:
                state.navigate(AppScreen(screen_name))
            except ValueError:
                pass

        elif action_id == "session:new":
            self.action_new_session()

        elif action_id == "session:resume":
            state.navigate(AppScreen.SESSIONS)

        elif action_id == "session:rename":
            state.navigate(AppScreen.SESSIONS)
            state.push_notification("Select a session and press 'e' to rename")

        elif action_id == "session:duplicate":
            state.navigate(AppScreen.SESSIONS)
            state.push_notification("Select a session and press 'p' to duplicate")

        elif action_id == "session:save":
            sid = state.active_session_id
            if sid:
                gateway._post(f"/api/session/{sid}/save", {})
                state.push_notification("Session saved.")

        elif action_id == "session:delete":
            sid = state.active_session_id
            if sid:
                gateway.delete_session(sid)

        elif action_id == "chat:cancel":
            gateway.cancel_stream()
            state.finalize_streaming_message()
            state.set_status(AgentStatus.IDLE)

        elif action_id == "chat:clear":
            state.clear_messages()

        elif action_id == "chat:model":
            # Open the inline model picker — navigate to chat first
            state.navigate(AppScreen.CHAT)
            try:
                from .screens.chat import ModelPickerOverlay
                try:
                    self.query_one(ModelPickerOverlay)
                except Exception:
                    self.mount(ModelPickerOverlay())
            except Exception:
                pass

        elif action_id == "chat:compact":
            state.push_notification("Context compression requested…", "info")
            import threading
            def _compact():
                try:
                    gateway.compress_context(state.active_session_id)
                except Exception as exc:
                    state.push_notification(f"Compression failed: {exc}", "error")
            threading.Thread(target=_compact, daemon=True).start()

        elif action_id.startswith("model:"):
            # model:provider/model-name  — e.g. model:openai/gpt-4o
            parts = action_id[6:].split("/", 1)  # strip "model:" prefix
            if len(parts) == 2:
                provider, model = parts
                state.set_provider(provider)
                state.set_model(model)
                state.push_notification(f"Switched to {provider}/{model}", "success")
                state.navigate(AppScreen.CHAT)

        elif action_id == "sys:doctor":
            state.navigate(AppScreen.CHAT)
            state.append_message(
                __import__("aetherion_cli.tui.state", fromlist=["Message"]).Message.system(
                    "Running doctor…"
                )
            )
            gateway.run_doctor(
                lambda result: state.append_message(
                    __import__("aetherion_cli.tui.state", fromlist=["Message"]).Message.system(result)
                )
            )

        elif action_id == "sys:logout":
            gateway.logout()
            state.set_user(None)
            state.set_offline_mode(False)
            state.navigate(AppScreen.WELCOME)

        elif action_id == "sys:profile":
            state.navigate(AppScreen.PROFILE)

        elif action_id == "sys:sidebar":
            self.action_toggle_sidebar()

    # ── Session browser callback ──────────────────────────────────────────────

    def on_session_selected(self, session: Session) -> None:
        """Propagate selection from session list pane to detail pane."""
        try:
            from .screens.sessions import SessionDetailPane
            self.query_one(SessionDetailPane).update_session(session)
        except Exception:
            pass

    def on_memory_selected(self, *args) -> None:
        """Propagate memory selection from list pane to detail pane."""
        try:
            from .screens.memory import MemoryDetailPane
            if args:
                self.query_one(MemoryDetailPane).show_entry(args[0])
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_tui() -> None:
    """Launch the Aetherion terminal workspace."""
    import os
    # Ensure TERM is set for full color support
    if not os.environ.get("TERM"):
        os.environ["TERM"] = "xterm-256color"

    app = AetherionApp()
    app.run()
