"""
aetherion_cli/tui/overlays.py
Aetherion TUI Overlays — command palette, approval center, notification banner.

All overlays are mounted once inside the App and toggled via CSS class.
None of them do I/O — they call gateway and mutate state only.

CommandPaletteOverlay:
  - Ctrl+P to open; Esc to close
  - Categorized commands with keyboard shortcuts
  - Fuzzy filter as you type
  - ↑↓ to navigate, Enter to execute

ApprovalOverlay:
  - Shown when state.pending_approval is set
  - A/D or button press to allow/deny
  - Risk level visual tiers (green/amber/red border)
  - Full tool description + argument preview

NotificationBanner:
  - Auto-dismisses after 4s (sticky messages require explicit dismiss)
  - Level-colored: info/success/warning/error
  - Press any key to dismiss, or wait for TTL
"""
from __future__ import annotations

import time
import threading
from typing import ClassVar, List, Optional, Tuple

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Static

from .state import AgentStatus, NotifLevel, RuntimeState, Screen as AppScreen, state
from .theme import Colors, Glyphs


# ─────────────────────────────────────────────────────────────────────────────
# Command Palette
# ─────────────────────────────────────────────────────────────────────────────

# (label, hint/shortcut, action_id, group)
PALETTE_COMMANDS: List[Tuple[str, str, str, str]] = [
    # Navigation
    (f"{Glyphs.SPARKLE} Dashboard",         "1",      "nav:dashboard",  "Navigate"),
    (f"{Glyphs.AI_ICON} Chat",              "2",      "nav:chat",       "Navigate"),
    (f"{Glyphs.SESSIONS} Session Browser",  "3",      "nav:sessions",   "Navigate"),
    (f"{Glyphs.MEMORY} Memory Explorer",    "4",      "nav:memory",     "Navigate"),
    (f"{Glyphs.PLUGINS} Plugin Manager",    "5",      "nav:plugins",    "Navigate"),
    (f"{Glyphs.PROVIDERS} Providers",       "7",      "nav:providers",  "Navigate"),
    (f"{Glyphs.JOBS} Background Jobs",      "8",      "nav:jobs",       "Navigate"),
    (f"{Glyphs.USER_ICON} Profile & Billing","9",     "nav:profile",    "Navigate"),
    (f"{Glyphs.HELP} Help",                 "F1",     "nav:help",       "Navigate"),

    # Session actions
    (f"{Glyphs.SPARKLE} New Session",       "Ctrl+N", "session:new",    "Session"),
    (f"{Glyphs.SESSIONS} Resume Session",   "",       "session:resume", "Session"),
    (f"✏ Rename Session",                 "",       "session:rename", "Session"),
    (f"{Glyphs.SPARKLE} Duplicate Session", "",       "session:duplicate","Session"),
    (f"{Glyphs.DONE} Save Session",         "",       "session:save",   "Session"),
    (f"{Glyphs.ERROR} Delete Session",      "",       "session:delete", "Session"),

    # Chat / model actions
    (f"{Glyphs.AI_ICON} Switch Model",      "Ctrl+M", "chat:model",     "Model"),
    (f"○ openai  / gpt-4o",               "",       "model:openai/gpt-4o",        "Model"),
    (f"○ openai  / gpt-4o-mini",          "",       "model:openai/gpt-4o-mini",   "Model"),
    (f"○ anthropic / claude-sonnet-4-5",  "",       "model:anthropic/claude-sonnet-4-5", "Model"),
    (f"○ anthropic / claude-opus-4-5",    "",       "model:anthropic/claude-opus-4-5",   "Model"),
    (f"○ google / gemini-2.5-pro",        "",       "model:google/gemini-2.5-pro",  "Model"),
    (f"○ google / gemini-2.5-flash",      "",       "model:google/gemini-2.5-flash","Model"),
    (f"○ ollama / llama3",                "",       "model:ollama/llama3",          "Model"),
    (f"○ groq / llama-3.3-70b",           "",       "model:groq/llama-3.3-70b-versatile", "Model"),

    # Chat actions
    (f"{Glyphs.RUNNING} Cancel Stream",     "Ctrl+C", "chat:cancel",    "Chat"),
    (f"{Glyphs.DASH} Clear Conversation",   "Ctrl+L", "chat:clear",     "Chat"),
    (f"{Glyphs.MEMORY} Compact Context",    "",       "chat:compact",   "Chat"),

    # System
    (f"{Glyphs.BOLT} Doctor / Health Check","",       "sys:doctor",     "System"),
    (f"{Glyphs.USER_ICON} My Profile",      "9",      "sys:profile",    "System"),
    (f"{Glyphs.OPEN_LOCK} Logout",          "",       "sys:logout",     "System"),
    (f"{Glyphs.SESSIONS} Toggle Sidebar",   "Ctrl+B", "sys:sidebar",    "System"),
]


class _PaletteRow(Static):
    DEFAULT_CSS = """
    _PaletteRow {
        height: 1;
        padding: 0 2;
    }
    _PaletteRow.highlighted {
        background: #4c2288;
        color: #9d5cf0;
    }
    """

    def __init__(self, label: str, hint: str, index: int = 0, highlighted: bool = False) -> None:
        super().__init__()
        self._label = label
        self._hint  = hint
        self.row_index = index
        if highlighted:
            self.add_class("highlighted")

    def render(self) -> Text:  # type: ignore[override]
        t = Text()
        hi = "highlighted" in self.classes
        t.append(self._label[:44], style=f"bold {Colors.ACCENT_BRIGHT}" if hi else Colors.TEXT_PRIMARY)
        if self._hint:
            t.append(f"  {self._hint}", style=Colors.TEXT_DIM)
        return t

    def on_click(self) -> None:
        try:
            palette = self.app.query_one(CommandPaletteOverlay)
            palette.select_and_execute(self.row_index)
        except Exception:
            pass


class CommandPaletteOverlay(Widget):
    """
    Ctrl+P command palette.
    Fuzzy filter, grouped results, ↑↓ + Enter.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", show=False),
        Binding("enter",  "execute", show=False),
        Binding("up",     "move_up", show=False),
        Binding("down",   "move_down", show=False),
    ]

    DEFAULT_CSS = """
    CommandPaletteOverlay {
        align: center top;
        background: rgba(10,12,18,0.94);
        overlay: screen;
        width: 72;
        height: auto;
        max-height: 28;
        border: solid #7c3aed;
        margin-top: 2;
        display: none;
    }
    CommandPaletteOverlay.visible {
        display: block;
    }
    #palette-header {
        height: 3;
        background: #1a1e2e;
        border-bottom: solid #3d4a6b;
        padding: 0 1;
    }
    #palette-search-input {
        background: #1a1e2e;
        border: none;
        color: #e2e8f0;
        height: 1;
        margin-top: 1;
        width: 1fr;
    }
    #palette-results {
        height: auto;
        max-height: 22;
        background: #0a0c12;
    }
    .palette-group-label {
        height: 1;
        background: #12151f;
        color: #334155;
        padding: 0 2;
        text-style: italic;
    }
    #palette-footer {
        height: 1;
        background: #12151f;
        color: #334155;
        padding: 0 2;
        border-top: solid #1e2433;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._query        = ""
        self._selected_idx = 0
        self._filtered: List[Tuple[str, str, str, str]] = []

    def compose(self) -> ComposeResult:
        with Container(id="palette-header"):
            yield Input(placeholder=f"{Glyphs.SPARKLE} Search commands…", id="palette-search-input")
        yield ScrollableContainer(id="palette-results")
        yield Label(
            f"[{Colors.TEXT_DIM}]↑↓[/] navigate  "
            f"[{Colors.TEXT_DIM}]Enter[/] run  "
            f"[{Colors.TEXT_DIM}]Esc[/] close",
            id="palette-footer"
        )

    def on_mount(self) -> None:
        self._unsub = state.subscribe("palette_open", self._on_toggle)

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _on_toggle(self) -> None:
        import threading
        if threading.current_thread() is threading.main_thread():
            self._sync_visibility()
        else:
            try:
                self.app.call_from_thread(self._sync_visibility)
            except Exception:
                pass

    def _sync_visibility(self) -> None:
        if state.palette_open:
            self.add_class("visible")
            inp = self.query_one("#palette-search-input", Input)
            inp.value = ""
            inp.focus()
            self._query        = ""
            self._selected_idx = 0
            self._rebuild()
        else:
            self.remove_class("visible")
            if getattr(self.app, "focused", None) and getattr(self.app.focused, "id", None) == "palette-search-input":
                self.app.set_focus(None)

    def _get_filtered(self) -> List[Tuple[str, str, str, str]]:
        q = self._query.lower().strip()
        if not q:
            return PALETTE_COMMANDS
        return [cmd for cmd in PALETTE_COMMANDS
                if q in cmd[0].lower() or q in cmd[2].lower() or q in cmd[3].lower()]

    def _update_highlight(self) -> None:
        rows = list(self.query(_PaletteRow))
        for i, row in enumerate(rows):
            if i == self._selected_idx:
                row.add_class("highlighted")
                row.scroll_visible(animate=False)
            else:
                row.remove_class("highlighted")
            row.refresh()

    def _rebuild(self) -> None:
        try:
            results = self.query_one("#palette-results")
        except Exception:
            return
        results.remove_children()
        self._filtered = self._get_filtered()
        cmds = self._filtered

        if not cmds:
            results.mount(Static("  No commands match.", classes="empty-state"))
            return

        self._selected_idx = max(0, min(self._selected_idx, len(cmds) - 1))
        current_group = None
        new_widgets: List[Widget] = []

        for i, (label, hint, action_id, group) in enumerate(cmds):
            if group != current_group:
                current_group = group
                new_widgets.append(Static(f"  {group}", classes="palette-group-label"))
            row = _PaletteRow(label, hint, index=i, highlighted=(i == self._selected_idx))
            new_widgets.append(row)

        results.mount_all(new_widgets)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "palette-search-input":
            self._query        = event.value
            self._selected_idx = 0
            self._rebuild()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "palette-search-input":
            event.stop()
            self.action_execute()

    def action_move_up(self) -> None:
        if not self._filtered:
            return
        self._selected_idx = max(0, self._selected_idx - 1)
        self._update_highlight()

    def action_move_down(self) -> None:
        if not self._filtered:
            return
        self._selected_idx = min(len(self._filtered) - 1, self._selected_idx + 1)
        self._update_highlight()

    def select_and_execute(self, idx: int) -> None:
        self._selected_idx = idx
        self.action_execute()

    def action_execute(self) -> None:
        if 0 <= self._selected_idx < len(self._filtered):
            action_id = self._filtered[self._selected_idx][2]
            state.set_palette(False)
            self.app.dispatch_palette_action(action_id)

    def action_close(self) -> None:
        state.set_palette(False)
        self.app.set_focus(None)


# ─────────────────────────────────────────────────────────────────────────────
# Approval Overlay
# ─────────────────────────────────────────────────────────────────────────────

class ApprovalOverlay(Widget):
    """
    Blocking approval modal shown when agent requests permission.

    Risk level determines border color:
      low → green border
      medium → amber border
      high → red border + pulsing label

    A/D or button press to resolve. Esc = deny.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("y", "allow", "Accept"),
        Binding("r", "deny", "Reject"),
        Binding("a", "always_allow", "Apply All"),
        Binding("n", "next", "Next Change"),
        Binding("p", "prev", "Prev Change"),
        Binding("escape", "dismiss", "Dismiss"),
    ]

    DEFAULT_CSS = """
    ApprovalOverlay {
        layer: overlay;
        align: center middle;
        background: rgba(10,12,18,0.96);
        overlay: screen;
        width: 76;
        height: auto;
        border: thick #7c3aed;
        padding: 1 2;
        display: none;
    }
    ApprovalOverlay.visible {
        display: block;
    }
    ApprovalOverlay.risk-high {
        border: thick #ef4444;
    }
    ApprovalOverlay.risk-low {
        border: thick #10b981;
    }
    #approval-header {
        height: 1;
        color: #f59e0b;
        text-style: bold;
        margin-bottom: 1;
    }
    #approval-tool-name {
        height: 1;
        text-style: bold;
        color: #e2e8f0;
        margin-bottom: 1;
    }
    #approval-desc {
        height: auto;
        color: #94a3b8;
        margin-bottom: 1;
    }
    #approval-risk-line {
        height: 1;
        margin-bottom: 1;
    }
    #approval-buttons {
        height: 3;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(f"{Glyphs.AI_ICON} Review Change", id="approval-header")
        yield Label("", id="approval-tool-name")
        yield Label("", id="approval-desc")
        yield Label("", id="approval-risk-line")
        with Horizontal(id="approval-buttons"):
            yield Button(f"{Glyphs.DONE} Accept [Y]", id="btn-allow", variant="success")
            yield Button(f"{Glyphs.ERROR} Reject [R]", id="btn-deny", variant="error")
            yield Button(f"Apply All [A]", id="btn-always-allow", variant="primary")
            yield Button(f"Next [N]", id="btn-next", variant="default")
            yield Button(f"Prev [P]", id="btn-prev", variant="default")

    def on_mount(self) -> None:
        self._unsub = state.subscribe("pending_approval", self._on_approval_changed)

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _on_approval_changed(self) -> None:
        import threading
        if threading.current_thread() is threading.main_thread():
            self._update()
        else:
            try:
                self.app.call_from_thread(self._update)
            except Exception:
                pass

    def _update(self) -> None:
        req = state.pending_approval
        if req:
            self.add_class("visible")
            self.remove_class("risk-high", "risk-low")
            if req.risk_level == "high":
                self.add_class("risk-high")
                self.query_one("#approval-header", Label).update(
                    f"{Glyphs.FIRE} HIGH RISK — Tool Approval Required"
                )
            elif req.risk_level == "low":
                self.add_class("risk-low")
                self.query_one("#approval-header", Label).update(
                    f"{Glyphs.BOLT} Tool Approval Required"
                )
            else:
                self.query_one("#approval-header", Label).update("⚠  Tool Approval Required")

            self.query_one("#approval-tool-name", Label).update(
                f"Tool: [{Colors.ACCENT_BRIGHT}]{req.tool_name}[/{Colors.ACCENT_BRIGHT}]"
            )
            self.query_one("#approval-desc", Label).update(req.description)
            risk_color = {
                "high":   Colors.ERROR,
                "medium": Colors.WARNING,
                "low":    Colors.SUCCESS,
            }.get(req.risk_level, Colors.TEXT_MUTED)
            self.query_one("#approval-risk-line", Label).update(
                f"Risk Level: [{risk_color}]{req.risk_level.upper()}[/{risk_color}]  "
                f"Request ID: {req.request_id}"
            )
        else:
            self.remove_class("visible")

    def action_allow(self) -> None:
        req = state.pending_approval
        if req:
            from .gateway import gateway
            gateway.approve_tool(req.request_id, "allow")

    def action_always_allow(self) -> None:
        req = state.pending_approval
        if req:
            from .gateway import gateway
            gateway.approve_tool(req.request_id, "always")

    def action_deny(self) -> None:
        req = state.pending_approval
        if req:
            from .gateway import gateway
            gateway.approve_tool(req.request_id, "deny")

    def action_next(self) -> None:
        # Backend frozen: UI hint only if list not implemented
        state.push_notification("Next change (no other pending changes)", "info")

    def action_prev(self) -> None:
        state.push_notification("Previous change (at beginning)", "info")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-allow":
            self.action_allow()
        elif event.button.id == "btn-always-allow":
            self.action_always_allow()
        elif event.button.id == "btn-deny":
            self.action_deny()
        elif event.button.id == "btn-next":
            self.action_next()
        elif event.button.id == "btn-prev":
            self.action_prev()


# ─────────────────────────────────────────────────────────────────────────────
# Notification Banner
# ─────────────────────────────────────────────────────────────────────────────

class NotificationBanner(Widget):
    """
    1-row notification banner above the status bar.
    Shows clean level labels (Info / Success / Warning / Error).
    Auto-dismisses after 4s. Click [✕] or anywhere to dismiss.
    """

    DEFAULT_CSS = """
    NotificationBanner {
        height: 1;
        padding: 0 1;
        display: none;
        layout: horizontal;
    }
    NotificationBanner.visible {
        display: block;
    }
    NotificationBanner.info    { background: #1d4ed8; color: white; }
    NotificationBanner.success { background: #059669; color: white; }
    NotificationBanner.warning { background: #d97706; color: #0a0c12; }
    NotificationBanner.error   { background: #dc2626; color: white; }
    #notif-text {
        width: 1fr;
        height: 1;
    }
    #notif-close {
        width: 3;
        height: 1;
        background: transparent;
        border: none;
        text-style: bold;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._dismiss_timer: Optional[threading.Timer] = None

    def compose(self) -> ComposeResult:
        from textual.widgets import Label, Button
        yield Label("", id="notif-text")
        yield Button("✕", id="notif-close")

    def on_mount(self) -> None:
        self._unsub_notif   = state.subscribe("active_notification", self._on_notification)
        self._unsub_offline = state.subscribe("offline_mode",        self._on_offline_change)

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub_notif"):   self._unsub_notif()
        if hasattr(self, "_unsub_offline"): self._unsub_offline()
        self._cancel_timer()

    def _on_offline_change(self) -> None:
        """Called from state subscriber (background thread)."""
        self.app.call_from_thread(self._show)

    def _on_notification(self) -> None:
        """Called from state subscriber (background thread)."""
        self.app.call_from_thread(self._show)

    def _show(self) -> None:
        level_labels = {"info": "Info", "success": "Success", "warning": "Warning", "error": "Error"}
        notif = state.active_notification

        if notif is None:
            if getattr(state, "offline_mode", False) and not getattr(state, "offline_banner_dismissed", False):
                self.remove_class("info", "success", "error")
                self.add_class("warning", "visible")
                self.query_one("#notif-text").update("  ⚡ Offline Mode — limited functionality")
                self._cancel_timer()
            else:
                self.remove_class("visible")
                self._cancel_timer()
            return

        level_label = level_labels.get(notif.level.value, "Notification")
        self.remove_class("info", "success", "warning", "error")
        self.add_class(notif.level.value, "visible")
        self.query_one("#notif-text").update(f"  [{level_label}]  {notif.message}")

        # Auto-dismiss after 4s
        self._cancel_timer()
        self._dismiss_timer = threading.Timer(4.0, self._auto_dismiss)
        self._dismiss_timer.daemon = True
        self._dismiss_timer.start()

    def _auto_dismiss(self) -> None:
        """Called from threading.Timer (background thread) — use call_from_thread."""
        state.dismiss_notification()
        self.app.call_from_thread(self._hide)

    def _hide(self) -> None:
        if getattr(state, "offline_mode", False) and not getattr(state, "offline_banner_dismissed", False):
            self._show()
            return
        self.remove_class("visible")
        self._cancel_timer()

    def _cancel_timer(self) -> None:
        if self._dismiss_timer:
            self._dismiss_timer.cancel()
            self._dismiss_timer = None

    def on_button_pressed(self, event) -> None:
        if event.button.id == "notif-close":
            if getattr(state, "offline_mode", False):
                state.offline_banner_dismissed = True
            state.dismiss_notification()
            self.remove_class("visible")
            self._cancel_timer()
            event.stop()

    def on_click(self, event) -> None:
        if getattr(state, "offline_mode", False):
            state.offline_banner_dismissed = True
        state.dismiss_notification()
        self.remove_class("visible")
        self._cancel_timer()


# ─────────────────────────────────────────────────────────────────────────────
# Progressive-Disclosure Status Bar
# ─────────────────────────────────────────────────────────────────────────────

class StatusBar(Widget):
    """
    1-row status bar using a greedy-budget algorithm (Hermes pattern, original implementation).

    Essential fields (always shown, never truncated):
      - Agent status icon + label
      - Active model name

    Tail fields (dropped when terminal too narrow):
      - Connection + latency
      - Context usage pct
      - Session ID (short)
      - User email / plan
      - Error excerpt

    Order matters: higher priority fields come first in the tail list.
    """

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: #12151f;
        border-top: solid #1e2433;
        padding: 0 1;
    }
    """

    def on_mount(self) -> None:
        self._subs = [
            state.subscribe("status",           self._refresh),
            state.subscribe("connected",        self._refresh),
            state.subscribe("runtime_state",    self._refresh),
            state.subscribe("context",          self._refresh),
            state.subscribe("active_model",     self._refresh),
            state.subscribe("active_provider",  self._refresh),
            state.subscribe("workspace_info",   self._refresh),
            state.subscribe("error_message",    self._refresh),
            state.subscribe("user_email",       self._refresh),
            state.subscribe("active_session_id",self._refresh),
        ]
        self.set_interval(10, self._timer_refresh)

    def on_unmount(self) -> None:
        for u in getattr(self, "_subs", []):
            u()

    def _timer_refresh(self) -> None:
        """Called from the asyncio event loop by set_interval — refresh directly."""
        self.refresh()

    def _refresh(self) -> None:
        """Called from background threads via state subscribers — use call_from_thread."""
        self.app.call_from_thread(self.refresh)

    def render(self) -> Text:  # type: ignore[override]
        t = Text(no_wrap=True, overflow="ellipsis")

        # ── Essential (always shown) ──────────────────────────────────────
        status = state.status
        icon_map = {
            AgentStatus.IDLE:             (Glyphs.IDLE,    Colors.TEXT_DIM),
            AgentStatus.RUNNING:          (Glyphs.RUNNING, Colors.WARNING),
            AgentStatus.STREAMING:        (Glyphs.STREAMING, Colors.SUCCESS),
            AgentStatus.WAITING_APPROVAL: (Glyphs.WAITING, Colors.WARNING),
            AgentStatus.ERROR:            (Glyphs.ERROR,   Colors.ERROR),
            AgentStatus.DONE:             (Glyphs.DONE,    Colors.SUCCESS),
        }
        icon, color = icon_map.get(status, (Glyphs.IDLE, Colors.TEXT_DIM))
        t.append(f" {icon} {status.value} ", style=f"bold {color}")
        t.append(Glyphs.V, style=Colors.BORDER_DIM)

        # Workspace
        ws = state.workspace_path
        if ws:
            t.append(f" █ {ws} ", style=Colors.ACCENT)
            t.append(Glyphs.V, style=Colors.BORDER_DIM)

        # Model + provider
        model    = state.active_model or "default"
        provider = state.active_provider
        if provider:
            t.append(f" ○ {provider}/{model} ", style=Colors.INFO)
        else:
            t.append(f" ⎇ {model} ", style=Colors.INFO)
        t.append(Glyphs.V, style=Colors.BORDER_DIM)

        # ── Tail (greedy budget) ──────────────────────────────────────────
        tail_segments = []

        # Segment 1: structured runtime state + latency
        rs = getattr(state, "runtime_state", RuntimeState.ONLINE if state.connected else RuntimeState.OFFLINE)
        lat = f" {state.latency_ms}ms" if state.latency_ms else ""
        if rs == RuntimeState.ONLINE:
            tail_segments.append((f"{Glyphs.V} {Glyphs.DONE} ONLINE{lat} ", Colors.SUCCESS))
        elif rs == RuntimeState.DEGRADED:
            tail_segments.append((f"{Glyphs.V} ⚠ DEGRADED{lat} ", Colors.WARNING))
        elif rs == RuntimeState.AUTH_REQUIRED:
            tail_segments.append((f"{Glyphs.V} 🔒 AUTH_REQUIRED ", Colors.WARNING))
        elif rs == RuntimeState.ERROR:
            tail_segments.append((f"{Glyphs.V} {Glyphs.ERROR} ERROR ", Colors.ERROR))
        else:
            tail_segments.append((f"{Glyphs.V} {Glyphs.ERROR} OFFLINE ", Colors.ERROR))

        # Segment 2: git branch
        branch = state.git_branch
        if branch:
            dirty = "±" if state.git_dirty else ""
            b_color = Colors.WARNING if state.git_dirty else Colors.TEXT_DIM
            tail_segments.append((f"{Glyphs.V}  {branch}{dirty} ", b_color))

        # Segment 2b: provider
        if hasattr(state, "active_provider") and state.active_provider:
            tail_segments.append((f"{Glyphs.V} {state.active_provider} ", Colors.TEXT_DIM))

        # Segment 3: context & memory usage
        ctx = state.context
        if ctx.input_tokens:
            pct = ctx.used_pct
            ctx_color = Colors.ERROR if pct > 85 else Colors.WARNING if pct > 60 else Colors.TEXT_DIM
            mem_str = f" {ctx.memory_usage_mb:.1f}MB" if getattr(ctx, "memory_usage_mb", 0) > 0 else ""
            tail_segments.append((f"{Glyphs.V} ctx {pct}%{mem_str} ", ctx_color))

        # Segment 4: session id short
        if state.active_session_id:
            sid = state.active_session_id[:12]
            tail_segments.append((f"{Glyphs.V} {Glyphs.SESSIONS}{sid} ", Colors.TEXT_DIM))

        # Segment 5: user plan
        if state.user_email:
            plan = state.user_plan
            tail_segments.append((f"{Glyphs.V} {state.user_email.split('@')[0]}·{plan} ", Colors.TEXT_DIM))

        # Segment 6: error excerpt
        if state.error_message:
            err = state.error_message[:35]
            tail_segments.append((f"{Glyphs.V} {Glyphs.ERROR} {err} ", Colors.ERROR))

        for seg_text, seg_color in tail_segments:
            t.append(seg_text, style=seg_color)

        return t


# ─────────────────────────────────────────────────────────────────────────────
# Error Recovery Overlay (P0 gap closure)
# ─────────────────────────────────────────────────────────────────────────────

class ErrorOverlay(Widget):
    """
    Human-readable crash/error interception card.
    Replaces raw stack traces with recovery actions.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss", "Dismiss"),
        Binding("r",      "retry",   "Retry"),
    ]

    DEFAULT_CSS = """
    ErrorOverlay {
        layer: overlay;
        align: center middle;
        background: rgba(10,12,18,0.96);
        overlay: screen;
        width: 60;
        height: auto;
        border: thick #ef4444;
        padding: 1 2;
        display: none;
    }
    ErrorOverlay.visible {
        display: block;
    }
    #error-header {
        height: 1;
        color: #ef4444;
        text-style: bold;
        margin-bottom: 1;
    }
    #error-desc {
        height: auto;
        color: #e2e8f0;
        margin-bottom: 1;
    }
    #error-buttons {
        height: 3;
        margin-top: 1;
        margin-bottom: 1;
    }
    #error-buttons Button {
        margin-right: 2;
    }
    #error-action {
        height: 1;
        color: #94a3b8;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(f"{Glyphs.ERROR} Runtime Failure", id="error-header")
        yield Label("", id="error-desc")
        with Horizontal(id="error-buttons"):
            yield Button("Retry Action", id="btn-error-retry", variant="primary")
            yield Button("Dismiss", id="btn-error-dismiss", variant="default")
        yield Label("[R] Retry Action  [Esc] Dismiss", id="error-action")

    def on_mount(self) -> None:
        self._unsub = state.subscribe("error_message", self._on_error_changed)

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _on_error_changed(self) -> None:
        import threading
        if threading.current_thread() is threading.main_thread():
            self._update()
        else:
            try:
                self.app.call_from_thread(self._update)
            except Exception:
                pass

    def _update(self) -> None:
        err = state.error_message
        if err:
            self.add_class("visible")
            # Parse error to make it human readable
            parts = err.split(":", 1)
            err_type = parts[0].replace("[crash]", "").replace("[thread]", "").strip()
            desc = parts[1].strip() if len(parts) > 1 else err
            
            friendly_desc = desc
            if "ConnectionRefusedError" in err_type or "ConnectError" in err_type:
                friendly_desc = "Could not connect to Aetherion backend. Ensure the server is running."
            elif "Timeout" in err_type:
                friendly_desc = "The request timed out. The backend might be overloaded."
            
            self.query_one("#error-desc", Label).update(
                f"[{Colors.ERROR}]{err_type}[/]\n\n{friendly_desc}"
            )
        else:
            self.remove_class("visible")

    def action_dismiss(self) -> None:
        state.set_error(None)
        state.set_failed_action(None, None)

    def action_retry(self) -> None:
        retry_fn = state.last_failed_action
        action_label = state.last_failed_action_label or "operation"
        state.set_error(None)
        if retry_fn:
            state.push_notification(f"Retrying {action_label}...", NotifLevel.INFO)
            retry_fn()
        else:
            state.push_notification("No retry action available", NotifLevel.WARNING)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-error-retry":
            self.action_retry()
        elif event.button.id == "btn-error-dismiss":
            self.action_dismiss()


