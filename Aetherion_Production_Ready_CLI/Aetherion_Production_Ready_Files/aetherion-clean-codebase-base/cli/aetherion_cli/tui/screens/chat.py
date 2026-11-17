"""
aetherion_cli/tui/screens/chat.py
Aetherion Chat Screen — production-grade streaming conversation workspace.

Gap closures vs baseline (evidence from Hermes analysis):
  [P0] Interrupt handling: Escape during streaming → interrupt/queue overlay
  [P0] Session persistence: messages saved to SQLite session store on completion
  [P0] Reasoning tag stripping: <think>, <thinking>, <reasoning>, <REASONING_SCRATCHPAD>
  [P1] Diff rendering: auto-detects unified diff, renders with Rich markup
  [P1] Input history persistence: saved to SQLite on every submit
  [P1] Token/duration formatting: compact K/M/B suffixes, elapsed time
  [P1] Slash command prefix: /model, /clear, /compact, /help recognized
  [P1] "Busy input" mode: while streaming, Enter queues next message
"""
from __future__ import annotations

import time
from typing import ClassVar, List, Optional

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.widget import Widget
from textual.widgets import Button, Label, Static, TextArea

from aetherion_cli.tui.state import AgentStatus, Message, Screen as AppScreen, state
from aetherion_cli.tui.theme import Colors, Glyphs
from aetherion_cli.rendering import (
    clean_assistant_text,
    detect_diff,
    format_duration,
    format_token_count,
    format_user_facing_response,
    render_diff_rich,
    strip_reasoning_tags,
)


# ─────────────────────────────────────────────────────────────────────────────
# Streaming split helpers (now backed by rendering module)
# ─────────────────────────────────────────────────────────────────────────────

def _split_streaming(content: str) -> tuple[str, str]:
    """
    Split streamed content at the last double-newline boundary outside a
    code fence. The stable prefix never re-renders; the suffix does.
    """
    in_fence = False
    fence_char = ""
    lines = content.split("\n")
    last_safe = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not in_fence:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = True
                fence_char = stripped[:3]
            elif stripped == "" and i > 0:
                last_safe = i
        else:
            if stripped.startswith(fence_char):
                in_fence = False
                last_safe = i + 1

    if last_safe == 0:
        return "", content
    return "\n".join(lines[:last_safe]), "\n".join(lines[last_safe:])


# ─────────────────────────────────────────────────────────────────────────────
# Slash command parser
# ─────────────────────────────────────────────────────────────────────────────

_SLASH_COMMANDS = {
    "/clear":   "Clear conversation",
    "/model":   "Switch model: /model gpt-4o",
    "/compact": "Compress context",
    "/session": "New session",
    "/help":    "Show commands",
}


def _parse_slash(text: str) -> Optional[tuple[str, str]]:
    """Return (command, args) if text is a slash command, else None."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split(None, 1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    return cmd, args


# ─────────────────────────────────────────────────────────────────────────────
# StreamingCursor
# ─────────────────────────────────────────────────────────────────────────────

class StreamingCursor(Static):
    """Blinking block cursor shown at end of active streaming message."""

    DEFAULT_CSS = """
    StreamingCursor {
        height: 1;
        width: 2;
        color: #9d5cf0;
    }
    """

    def on_mount(self) -> None:
        self._visible = True
        self._start_ts = time.monotonic()
        self.set_interval(0.42, self._blink)
        self.set_interval(1.0, self.refresh)

    def _blink(self) -> None:
        self._visible = not self._visible
        self.refresh()

    def render(self) -> Text:  # type: ignore[override]
        elapsed = time.monotonic() - self._start_ts
        dur = format_duration(elapsed) if elapsed > 1 else ""
        suffix = f" {dur}" if dur else ""
        char = "▍" if self._visible else " "
        t = Text()
        t.append(char, style=f"bold {Colors.ACCENT}")
        if suffix:
            t.append(suffix, style=Colors.TEXT_DIM)
        return t


# ─────────────────────────────────────────────────────────────────────────────
# MessageRow
# ─────────────────────────────────────────────────────────────────────────────

class MessageRow(Widget):
    """
    A single rendered message in the conversation transcript.

    New vs baseline:
      - Reasoning tags stripped from assistant content before display
      - Diff detection → Rich markup diff rendering
      - Token count formatted with K/M/B compact notation
      - Duration elapsed shown on streaming messages
    """

    DEFAULT_CSS = """
    MessageRow {
        padding: 1 2;
        width: 1fr;
        height: auto;
    }
    MessageRow.streaming {
        border-bottom: dashed #3d4a6b;
    }
    MessageRow.user {
        border-left: thick #3b82f6;
    }
    MessageRow.assistant {
        border-left: thick #7c3aed;
    }
    MessageRow.error {
        border-left: thick #ef4444;
        background: #1c1010;
    }
    """

    def __init__(self, msg: Message) -> None:
        super().__init__()
        self._msg    = msg
        self._msg_id = msg.id
        self.add_class(msg.role)

    def on_mount(self) -> None:
        if self._msg.is_streaming:
            self.add_class("streaming")
            self._unsub = state.subscribe("messages", self._on_messages_changed)

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _on_messages_changed(self) -> None:
        with state._lock:
            for m in state.messages:
                if m.id == self._msg_id:
                    self._msg = m
                    break
        if not self._msg.is_streaming:
            self.remove_class("streaming")
            try:
                self._unsub()
            except Exception:
                pass
        self.app.call_from_thread(self.refresh)

    def render(self) -> Text:  # type: ignore[override]
        msg = self._msg

        role_conf = {
            "user":      (Glyphs.USER_ICON,   Colors.USER,      "You"),
            "assistant": (Glyphs.AI_ICON,      Colors.ASSISTANT, "Aetherion"),
            "system":    (Glyphs.SYSTEM_ICON,  Colors.SYSTEM,    "System"),
            "tool":      (Glyphs.TOOL_ICON,    Colors.WARNING,   f"Tool: {msg.tool_name or '?'}"),
            "error":     (Glyphs.ERROR,        Colors.ERROR,     "Error"),
        }
        icon, color, label = role_conf.get(msg.role, (Glyphs.BULLET, Colors.TEXT_MUTED, msg.role))

        # ── Header
        t = Text()
        t.append(f" {icon} ", style=f"bold {color}")
        t.append(label, style=f"bold {color}")
        if msg.timestamp:
            ts = time.strftime("%H:%M", time.localtime(msg.timestamp))
            t.append(f"  {ts}", style=Colors.TEXT_DIM)
        if msg.is_streaming:
            t.append("  ▍", style=f"blink bold {Colors.ACCENT}")
        if msg.token_count:
            t.append(f"  {format_token_count(msg.token_count)}", style=Colors.TEXT_DIM)
        t.append("\n")

        # ── Content
        raw_content = msg.content

        if msg.role == "assistant":
            # Format clean user-facing response (strip reasoning tags and raw JSON envelopes)
            content = format_user_facing_response(raw_content)

            # Diff detection
            if detect_diff(content):
                diff_rich = render_diff_rich(content)
                t.append(diff_rich + "\n", style=Colors.TEXT_PRIMARY)
            elif msg.is_streaming:
                stable, unstable = _split_streaming(content)
                if stable:
                    t.append(stable + "\n", style=Colors.TEXT_PRIMARY)
                if unstable:
                    t.append(unstable, style=Colors.TEXT_PRIMARY)
            else:
                t.append(content + "\n", style=Colors.TEXT_PRIMARY)

        elif msg.role == "user":
            t.append(raw_content + "\n", style=Colors.TEXT_PRIMARY)
        elif msg.role == "tool":
            t.append(raw_content + "\n", style=Colors.WARNING)
        elif msg.role == "error":
            t.append(raw_content + "\n", style=Colors.ERROR)
        elif msg.role == "system":
            try:
                parsed = Text.from_markup(raw_content)
                t.append(parsed)
                t.append("\n")
            except Exception:
                t.append(raw_content + "\n", style=Colors.TEXT_MUTED)
        else:
            t.append(raw_content + "\n", style=Colors.TEXT_MUTED)

        return t


# ─────────────────────────────────────────────────────────────────────────────
# Interrupt Overlay (P0 gap closure)
# ─────────────────────────────────────────────────────────────────────────────

class InterruptOverlay(Widget):
    """
    Displayed when user presses Escape during a live generation.

    Three modes (from Hermes server.py line 5135):
      interrupt — stop generation immediately (default)
      queue     — let it finish, queue the next message
      steer     — inject into live turn (future: requires backend support)

    Keys: I = interrupt, Q = queue, Escape = dismiss
    """

    DEFAULT_CSS = """
    InterruptOverlay {
        layer: overlay;
        align: center middle;
        background: #0a0d18 80%;
        height: 100%;
        width: 100%;
    }
    #interrupt-box {
        width: 56;
        height: 10;
        background: #1a1e2e;
        border: double #7c3aed;
        padding: 1 2;
        align: center middle;
    }
    #interrupt-title {
        color: #9d5cf0;
        text-style: bold;
        text-align: center;
        height: 1;
    }
    #interrupt-subtitle {
        color: #64748b;
        text-align: center;
        height: 1;
        margin-bottom: 1;
    }
    .interrupt-btn {
        margin: 0 1;
    }
    #btn-interrupt { background: #7c3aed; color: white; }
    #btn-queue { background: #1e3a5f; color: #93c5fd; }
    #btn-dismiss { background: #1e1e2e; color: #64748b; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("i", "interrupt", show=False),
        Binding("q", "queue_msg", show=False),
        Binding("escape", "dismiss_overlay", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="interrupt-box"):
            yield Label("⚡ Generation in Progress", id="interrupt-title")
            yield Label("What would you like to do?", id="interrupt-subtitle")
            with Horizontal():
                yield Button("[ I ] Stop now", id="btn-interrupt", classes="interrupt-btn")
                yield Button("[ Q ] Queue message", id="btn-queue", classes="interrupt-btn")
                yield Button("[ Esc ] Continue", id="btn-dismiss", classes="interrupt-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-interrupt":
            self.action_interrupt()
        elif event.button.id == "btn-queue":
            self.action_queue_msg()
        elif event.button.id == "btn-dismiss":
            self.action_dismiss_overlay()

    def action_interrupt(self) -> None:
        from aetherion_cli.tui.gateway import gateway
        gateway.cancel_stream()
        state.finalize_streaming_message()
        state.set_status(AgentStatus.IDLE)
        state.push_notification("Generation interrupted", "warning")
        self.remove()

    def action_queue_msg(self) -> None:
        # Queue mode: let generation finish; next submit will wait
        state.set_queued_interrupt(True)
        state.push_notification("Next message queued — will send after generation completes", "info")
        self.remove()

    def action_dismiss_overlay(self) -> None:
        self.remove()


# ─────────────────────────────────────────────────────────────────────────────
# ConversationView
# ─────────────────────────────────────────────────────────────────────────────

class ConversationView(ScrollableContainer):
    """
    Scrollable conversation transcript.
    Auto-scrolls to bottom when streaming; pauses when user scrolls up.
    """

    DEFAULT_CSS = """
    ConversationView {
        height: 1fr;
        width: 1fr;
        padding: 0;
    }
    #empty-state {
        color: #64748b;
        text-align: center;
        padding: 4 2;
        width: 1fr;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._auto_scroll = True
        self._row_ids: dict[str, str] = {}

    def on_mount(self) -> None:
        self._unsub = state.subscribe("messages", self._on_messages_changed)
        self._rebuild()

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _on_messages_changed(self) -> None:
        self.app.call_from_thread(self._sync_messages)

    def _rebuild(self) -> None:
        self._sync_messages()

    def _sync_messages(self) -> None:
        with state._lock:
            msgs = list(state.messages)

        if not msgs:
            self.remove_children()
            self.mount(Static(
                f"\n{Glyphs.AI_ICON}  Start a conversation below.\n\n"
                "Type a task and press Enter — or Ctrl+P to open the command palette.",
                id="empty-state"
            ))
            self._row_ids.clear()
            return

        try:
            self.query_one("#empty-state").remove()
        except Exception:
            pass

        existing_ids = set(self._row_ids.keys())
        msg_ids = {m.id for m in msgs}

        for mid in existing_ids - msg_ids:
            wid = self._row_ids.pop(mid, None)
            if wid:
                try:
                    self.query_one(f"#{wid}").remove()
                except Exception:
                    pass

        for msg in msgs:
            if msg.id not in self._row_ids:
                row = MessageRow(msg)
                wid = f"msg-{msg.id}"
                row.id = wid
                self._row_ids[msg.id] = wid
                self.mount(row)

        if self._auto_scroll:
            self.scroll_end(animate=False)

    def on_scroll(self, event) -> None:
        self._auto_scroll = self.scroll_y >= self.max_scroll_y - 2


# ─────────────────────────────────────────────────────────────────────────────
# Context Heatmap
# ─────────────────────────────────────────────────────────────────────────────

class ContextHeatmap(Static):
    """
    Progressive-disclosure token usage bar with compact formatting.
    Now uses format_token_count() from rendering module (K/M/B suffixes).
    """

    DEFAULT_CSS = """
    ContextHeatmap {
        height: 1;
        width: 1fr;
        color: #64748b;
        padding: 0 1;
        background: #12151f;
    }
    """

    def on_mount(self) -> None:
        self._unsub = state.subscribe("context", self._refresh)
        self.set_interval(5, self.refresh)

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _refresh(self) -> None:
        import threading
        if hasattr(self, "app") and self.app and getattr(self.app, "_thread_id", None) == threading.get_ident():
            self.refresh()
        elif hasattr(self, "app") and self.app:
            try:
                self.app.call_from_thread(self.refresh)
            except Exception:
                pass

    def render(self) -> Text:  # type: ignore[override]
        ctx = state.context
        pct = ctx.used_pct
        frac = ctx.used_fraction

        if pct > 85:
            bar_color = Colors.ERROR
        elif pct > 60:
            bar_color = Colors.WARNING
        else:
            bar_color = Colors.SUCCESS

        bar_width = 20
        filled = int(bar_width * frac)
        bar = Glyphs.DOT_FULL * filled + Glyphs.DOT_EMPTY * (bar_width - filled)

        t = Text()
        t.append("CTX ", style=Colors.TEXT_DIM)
        t.append(bar, style=bar_color)
        t.append(f" {pct:3d}%", style=f"bold {bar_color}")

        # Compact token counts
        if ctx.input_tokens:
            t.append(f"  {format_token_count(ctx.input_tokens)} in", style=Colors.TEXT_DIM)
        if ctx.output_tokens:
            t.append(f"  {format_token_count(ctx.output_tokens)} out", style=Colors.TEXT_DIM)

        # Model label
        if state.active_model and state.active_model != "default":
            t.append(f"  {state.active_model}", style=Colors.TEXT_DIM)

        return t


# ─────────────────────────────────────────────────────────────────────────────
# Chat Input Bar
# ─────────────────────────────────────────────────────────────────────────────

class ChatTextArea(TextArea):
    """
    Ergonomic multi-line composer for terminal chat:
      - Enter          -> Submit message (or queue if busy)
      - Shift+Enter    -> Insert newline
      - Ctrl+J         -> Insert newline (terminal fallback)
      - Ctrl+P         -> Command palette
      - Escape         -> Interrupt / clear
      - Ctrl+U         -> Clear input
      - Ctrl+L         -> Clear conversation
      - Up / Down      -> History navigation (in single-line mode)
    """

    class Submitted(events.Message):
        """Emitted when Enter is pressed without Shift."""
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
            return
        elif event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return
        elif event.key == "ctrl+p":
            event.stop()
            event.prevent_default()
            if hasattr(self.app, "action_open_palette"):
                self.app.action_open_palette()
            return
        elif event.key == "escape":
            event.stop()
            event.prevent_default()
            if hasattr(self.app, "action_handle_escape"):
                self.app.action_handle_escape()
            elif (parent := self.parent) and hasattr(parent, "action_handle_escape"):
                parent.action_handle_escape()
            return
        elif event.key == "ctrl+u":
            event.stop()
            event.prevent_default()
            self.clear()
            return
        elif event.key == "ctrl+l":
            event.stop()
            event.prevent_default()
            state.clear_messages()
            return
        elif event.key in ("up", "down") and "\n" not in self.text:
            parent = self.parent
            if parent and hasattr(parent, "_navigate_history"):
                event.stop()
                event.prevent_default()
                parent._navigate_history(event.key)
                return

        await super()._on_key(event)


class ChatInput(Widget):
    """
    Keyboard-first multi-line composer.

    Gap closures vs baseline:
      - Escape during streaming → InterruptOverlay (P0)
      - Input history persisted to SQLite (P1)
      - History loaded from SQLite on mount (P1)
      - Slash command recognition → action routing (P1)
      - "Busy input" mode: Enter while streaming queues message (P1)

    Bindings:
      Enter           → submit (or queue if busy)
      Shift+Enter     → newline
      Ctrl+U          → kill to start of line
      Ctrl+K          → kill to end of line
      ↑ (single line) → history previous
      ↓ (single line) → history next
      Escape          → interrupt overlay / clear / dismiss
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "handle_escape", show=False),
    ]

    DEFAULT_CSS = """
    ChatInput {
        height: auto;
        min-height: 3;
        max-height: 10;
        background: #1a1e2e;
        border-top: solid #7c3aed;
        padding: 0 1;
    }
    ChatInput ChatTextArea {
        background: #1a1e2e;
        color: #e2e8f0;
        border: none;
        height: auto;
        min-height: 1;
    }
    ChatInput #composer-hint {
        height: 1;
        color: #334155;
    }
    ChatInput #input-status {
        height: 1;
        color: #64748b;
    }
    ChatInput #queued-badge {
        height: 1;
        color: #f59e0b;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._history: List[str] = []
        self._hist_idx = -1
        self._queued_text: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield ChatTextArea(id="tui-textarea")
        with Horizontal():
            yield Label("", id="input-status")
            yield Label("", id="queued-badge")
            yield Label(
                f"[{Colors.TEXT_DIM}]Enter[/] submit  "
                f"[{Colors.TEXT_DIM}]Shift+Enter[/] newline  "
                f"[{Colors.TEXT_DIM}]Ctrl+P[/] palette  "
                f"[{Colors.TEXT_DIM}]Esc[/] interrupt",
                id="composer-hint"
            )

    def on_mount(self) -> None:
        self.query_one("#tui-textarea", ChatTextArea).focus()
        state.subscribe("status", self._on_status_changed)
        # Load persisted input history
        self._load_history()

    def _load_history(self) -> None:
        """Load input history from SQLite on startup."""
        try:
            from aetherion_cli.session_store import get_input_history
            items = get_input_history(limit=200)
            # History is newest-first from DB; keep that order
            self._history = items
        except Exception:
            pass

    def _on_status_changed(self) -> None:
        self.app.call_from_thread(self._update_status)

    def _update_status(self) -> None:
        label = self.query_one("#input-status", Label)
        s = state.status
        icon_map = {
            AgentStatus.IDLE:             ("",                                       Colors.TEXT_DIM),
            AgentStatus.RUNNING:          (f"{Glyphs.RUNNING} running…",             Colors.WARNING),
            AgentStatus.STREAMING:        (f"{Glyphs.STREAMING} streaming…  Esc=interrupt", Colors.SUCCESS),
            AgentStatus.WAITING_APPROVAL: (f"{Glyphs.WAITING} awaiting approval  A/D", Colors.WARNING),
            AgentStatus.ERROR:            (f"{Glyphs.ERROR} error — press Enter to retry", Colors.ERROR),
            AgentStatus.DONE:             (f"{Glyphs.DONE} done",                   Colors.SUCCESS),
            AgentStatus.FAILED:           (f"{Glyphs.ERROR} failed",                Colors.ERROR),
            AgentStatus.BLOCKED:          (f"{Glyphs.WARNING} blocked",             Colors.WARNING),
            AgentStatus.BUDGET_EXHAUSTED: (f"{Glyphs.LOCK} budget exhausted",       Colors.ERROR),
            AgentStatus.CANCELLED:        (f"{Glyphs.CROSS} cancelled",             Colors.TEXT_DIM),
            AgentStatus.SUCCEEDED:        (f"{Glyphs.DONE} succeeded",              Colors.SUCCESS),
        }
        text, color = icon_map.get(s, ("", Colors.TEXT_DIM))
        label.update(f"[{color}]{text}[/]")

        # Show queued message badge
        queued_label = self.query_one("#queued-badge", Label)
        if self._queued_text:
            short = self._queued_text[:30] + ("…" if len(self._queued_text) > 30 else "")
            queued_label.update(f"[{Colors.WARNING}]⏳ queued: {short}[/]")
        else:
            queued_label.update("")

    def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        ta = self.query_one("#tui-textarea", ChatTextArea)
        s  = state.status
        text = event.text.strip()
        if not text:
            return

        # Check for slash commands
        slash = _parse_slash(text)
        if slash:
            ta.clear()
            self._handle_slash(*slash)
            return

        # If busy — queue
        is_busy = s in (AgentStatus.RUNNING, AgentStatus.STREAMING)
        if is_busy:
            self._queued_text = text
            ta.clear()
            self._update_status()
            return

        # Normal submit
        ta.clear()
        self._submit(text)

    def _navigate_history(self, direction: str) -> None:
        ta = self.query_one("#tui-textarea", ChatTextArea)
        if direction == "up" and self._history:
            self._hist_idx = min(self._hist_idx + 1, len(self._history) - 1)
            if 0 <= self._hist_idx < len(self._history):
                ta.load_text(self._history[self._hist_idx])
        elif direction == "down":
            self._hist_idx = max(self._hist_idx - 1, -1)
            if self._hist_idx == -1:
                ta.clear()
            elif 0 <= self._hist_idx < len(self._history):
                ta.load_text(self._history[self._hist_idx])

    def action_handle_escape(self) -> None:
        ta = self.query_one("#tui-textarea", ChatTextArea)
        s  = state.status

        if s in (AgentStatus.RUNNING, AgentStatus.STREAMING):
            # Show interrupt overlay
            try:
                self.app.query_one(InterruptOverlay)
                return  # already showing
            except Exception:
                pass
            self.app.mount(InterruptOverlay())
        elif ta.text.strip():
            ta.clear()

    def _submit(self, text: str) -> None:
        """Submit a message: add to history, persist, dispatch."""
        if text and (not self._history or self._history[0] != text):
            self._history.insert(0, text)
            if len(self._history) > 500:
                self._history.pop()
        self._hist_idx = -1
        # Persist to SQLite (best-effort)
        try:
            from aetherion_cli.session_store import append_input_history
            append_input_history(text, session_id=state.active_session_id)
        except Exception:
            pass
        self.app.handle_chat_submit(text)

    def _handle_slash(self, cmd: str, args: str) -> None:
        """Route slash commands to app actions."""
        if cmd == "/clear":
            state.clear_messages()
            state.push_notification("Conversation cleared", "info")
        elif cmd == "/model":
            if args.strip():
                state.set_model(args.strip())
                state.push_notification(f"Model switched to: {args.strip()}", "success")
            else:
                state.push_notification(
                    "Usage: /model <model-name>  e.g. /model gpt-4o", "info"
                )
        elif cmd == "/compact":
            state.push_notification("Context compression requested…", "info")
            # Backend API call in background
            import threading
            def _compress():
                try:
                    from aetherion_cli.tui.gateway import gateway
                    gateway.compress_context(state.active_session_id)
                except Exception as exc:
                    state.push_notification(f"Compression failed: {exc}", "error")
            threading.Thread(target=_compress, daemon=True).start()
        elif cmd == "/session":
            self.app.handle_chat_submit(f"/new-session {args}")
        elif cmd == "/help":
            help_lines = "\n".join(
                f"  {c}  —  {d}" for c, d in _SLASH_COMMANDS.items()
            )
            state.push_notification(f"Slash commands:\n{help_lines}", "info")
        else:
            state.push_notification(f"Unknown command: {cmd}", "warning")

    def on_generation_complete(self) -> None:
        """Called by gateway when streaming finishes — send queued message."""
        if self._queued_text:
            queued = self._queued_text
            self._queued_text = None
            self._update_status()
            self._submit(queued)

    def focus_input(self) -> None:
        self.query_one("#tui-textarea", ChatTextArea).focus()


# ─────────────────────────────────────────────────────────────────────────────
# Chat Screen
# ─────────────────────────────────────────────────────────────────────────────

class ChatScreen(Widget):
    """
    Full conversation workspace.

    Layout:
      ┌─────────────────────────────────────────────────┐
      │  ConversationView (1fr, scrollable)             │
      ├─────────────────────────────────────────────────┤
      │  Context heatmap (1 row)                        │
      ├─────────────────────────────────────────────────┤
      │  ChatInput (3–10 rows, auto-height)             │
      └─────────────────────────────────────────────────┘
    """

    DEFAULT_CSS = """
    ChatScreen {
        height: 1fr;
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield ConversationView(id="chat-conversation")
        with Container(id="heatmap-strip"):
            yield ContextHeatmap()
        yield ChatInput(id="chat-input")

    def on_mount(self) -> None:
        self.query_one(ChatInput).focus_input()
        # Listen for generation complete to flush queued messages
        state.subscribe("status", self._on_status_changed)

    def _on_status_changed(self) -> None:
        """When generation finishes, flush any queued message."""
        if state.status in (AgentStatus.IDLE, AgentStatus.DONE, AgentStatus.ERROR):
            try:
                chat_input = self.query_one(ChatInput)
                chat_input.call_from_thread(chat_input.on_generation_complete)
            except Exception:
                pass
