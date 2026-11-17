"""
aetherion_cli/tui/screens/providers.py
Provider Manager — live status, latency, multi-adapter configuration, BYOK encrypted keys.

Supported backend adapter protocols:
  - OPENAI_COMPATIBLE
  - ANTHROPIC_COMPATIBLE
  - CHAT_COMPATIBLE
"""
from __future__ import annotations

from typing import ClassVar, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Static

from aetherion_cli.tui.state import NotifLevel, Provider, Screen as AppScreen, state
from aetherion_cli.tui.theme import Colors, Glyphs

SUPPORTED_PROTOCOLS = ("OPENAI_COMPATIBLE", "ANTHROPIC_COMPATIBLE", "CHAT_COMPATIBLE")


class ProviderRow(Static):
    """Single provider status row."""

    DEFAULT_CSS = """
    ProviderRow {
        height: 3;
        padding: 0 2;
        border-bottom: solid #1e2433;
        width: 1fr;
    }
    ProviderRow.selected {
        background: #4c2288;
    }
    ProviderRow:hover {
        background: #1a1e2e;
    }
    """

    def __init__(self, provider: Provider, idx: int = 0, selected: bool = False) -> None:
        super().__init__()
        self._p = provider
        self._idx = idx
        if selected:
            self.add_class("selected")

    def render(self) -> Text:  # type: ignore[override]
        p = self._p
        sel = "selected" in self.classes
        prefix = f"{Glyphs.CHEVRON_RIGHT} " if sel else "  "

        if p.available:
            status_icon  = Glyphs.DONE
            status_color = Colors.SUCCESS
        else:
            status_icon  = Glyphs.ERROR
            status_color = Colors.ERROR

        default_tag = " [default]" if p.is_default else ""

        t = Text()
        t.append(prefix, style=f"bold {Colors.ACCENT}" if sel else Colors.TEXT_DIM)
        t.append(f"{status_icon} ", style=f"bold {status_color}")
        t.append(p.name[:22], style=f"bold {Colors.TEXT_PRIMARY}")
        t.append(f"  {p.model[:18]}", style=Colors.TEXT_MUTED)
        t.append(default_tag, style=Colors.ACCENT_BRIGHT)
        if p.latency is not None:
            lat_color = Colors.SUCCESS if p.latency < 500 else Colors.WARNING if p.latency < 2000 else Colors.ERROR
            t.append(f"  {p.latency}ms", style=lat_color)
        t.append("\n")
        ptype = getattr(p, "provider_type", "OPENAI_COMPATIBLE")
        t.append(f"  protocol={ptype}  quality={p.quality}  cost={p.cost}", style=Colors.TEXT_DIM)
        if p.error:
            t.append(f"  {Glyphs.ERROR} {p.error[:40]}", style=Colors.ERROR)
        return t

    def on_click(self) -> None:
        try:
            screen = self.app.query_one(ProvidersScreen)
            screen.select_provider_by_index(self._idx)
        except Exception:
            pass


class ProviderDetailPane(Widget):
    """Right pane: provider detail and full configuration management."""

    DEFAULT_CSS = """
    ProviderDetailPane {
        width: 1fr;
        padding: 1 2;
        background: #0a0c12;
        height: 1fr;
    }
    #prov-title {
        color: #9d5cf0;
        text-style: bold;
        height: 2;
    }
    #prov-status {
        height: auto;
        margin-bottom: 1;
        color: #e2e8f0;
    }
    #prov-form-section {
        border-top: solid #1e2433;
        padding-top: 1;
        height: 1fr;
    }
    .prov-input-row {
        height: 3;
        margin-bottom: 1;
    }
    .prov-label {
        color: #94a3b8;
        width: 16;
        height: 1;
        margin-top: 1;
    }
    .prov-field {
        width: 1fr;
        height: 3;
        border: solid #3d4a6b;
        background: #12151f;
    }
    #btn-toggle-key-vis {
        min-width: 8;
        width: 8;
        height: 3;
        margin-left: 1;
    }
    #prov-key-note {
        color: #64748b;
        height: 1;
        margin-bottom: 1;
    }
    #prov-actions {
        height: 3;
        margin-top: 1;
    }
    #prov-actions Button {
        margin-right: 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._provider: Optional[Provider] = None

    def compose(self) -> ComposeResult:
        yield Label(f"{Glyphs.PROVIDERS} Select a provider →", id="prov-title")
        yield Label("", id="prov-status")
        with Container(id="prov-form-section"):
            yield Label(f" {Glyphs.LOCK} LLM Model Provider (BYOK) Configuration:", classes="dash-section-title")

            with Horizontal(classes="prov-input-row"):
                yield Label("Display Name:", classes="prov-label")
                yield Input(placeholder="Display name (e.g. OpenAI, Anthropic, AgentRouter)", id="prov-name-input", classes="prov-field")

            with Horizontal(classes="prov-input-row"):
                yield Label("Protocol:", classes="prov-label")
                yield Input(placeholder="OPENAI_COMPATIBLE | ANTHROPIC_COMPATIBLE | CHAT_COMPATIBLE", id="prov-type-input", classes="prov-field")

            with Horizontal(classes="prov-input-row"):
                yield Label("Base URL:", classes="prov-label")
                yield Input(placeholder="Base URL (e.g. https://api.openai.com/v1)", id="prov-url-input", classes="prov-field")

            with Horizontal(classes="prov-input-row"):
                yield Label("Default Model:", classes="prov-label")
                yield Input(placeholder="Model identifier (e.g. gpt-4o, claude-3-5-sonnet)", id="prov-model-input", classes="prov-field")

            with Horizontal(classes="prov-input-row"):
                yield Label("API Key (BYOK):", classes="prov-label")
                yield Input(placeholder="Paste provider key (e.g. sk-...) — Stored encrypted", password=True, id="prov-key-input", classes="prov-field")
                yield Button("Show", id="btn-toggle-key-vis", variant="default")

            yield Label("Stored key: not configured", id="prov-key-note")

            with Horizontal(id="prov-actions"):
                yield Button(f"{Glyphs.DONE} Save Provider", id="btn-save-key", variant="primary")
                yield Button(f"{Glyphs.BOLT} Test Connection", id="btn-test-key", variant="default")
                yield Button("Disable", id="btn-toggle-enable", variant="default")
                yield Button(f"{Glyphs.SPARKLE} Set Default", id="btn-default-key", variant="default")
                yield Button(f"{Glyphs.ERROR} Remove", id="btn-remove-key", variant="default")

    def show_provider(self, p: Provider) -> None:
        self._provider = p
        avail_str = f"[{Colors.SUCCESS}]{Glyphs.DONE} Available[/{Colors.SUCCESS}]" \
            if p.available else f"[{Colors.ERROR}]{Glyphs.ERROR} Unavailable[/{Colors.ERROR}]"

        default_str = " (Default Provider)" if p.is_default else ""
        self.query_one("#prov-title", Label).update(f"{Glyphs.PROVIDERS} {p.name}{default_str}")
        self.query_one("#prov-status", Label).update(
            f"ID: {p.id or p.key}   Protocol: {p.provider_type}\n"
            f"Model: {p.model or 'default'}   Status: {avail_str}\n"
            f"Quality: {p.quality}   Cost tier: {p.cost}"
            + (f"\nLatency: {p.latency}ms" if p.latency else "")
            + (f"\nError: [{Colors.ERROR}]{p.error}[/{Colors.ERROR}]" if p.error else "")
        )

        self.query_one("#prov-name-input", Input).value = p.name
        self.query_one("#prov-type-input", Input).value = p.provider_type
        self.query_one("#prov-url-input", Input).value = p.base_url
        self.query_one("#prov-model-input", Input).value = p.model
        self.query_one("#prov-key-input", Input).value = ""
        self.query_one("#prov-key-input", Input).password = True
        try:
            self.query_one("#btn-toggle-key-vis", Button).label = "Show"
            self.query_one("#btn-toggle-enable", Button).label = "Disable" if p.is_enabled else "Enable"
        except Exception:
            pass

        masked = getattr(p, "api_key_masked", "")
        if masked:
            self.query_one("#prov-key-note", Label).update(f"Stored encrypted key: {masked}")
        else:
            self.query_one("#prov-key-note", Label).update("No encrypted key configured")

    def show_new_template(self) -> None:
        self._provider = Provider(
            key="new",
            name="New Provider",
            model="gpt-4o",
            provider_type="OPENAI_COMPATIBLE",
            base_url="https://api.openai.com/v1",
            available=True,
            id="new_"
        )
        self.show_provider(self._provider)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if not self._provider:
            return
        from aetherion_cli.tui.gateway import gateway

        bid = event.button.id
        name = self.query_one("#prov-name-input", Input).value.strip()
        ptype = self.query_one("#prov-type-input", Input).value.strip().upper()
        base_url = self.query_one("#prov-url-input", Input).value.strip()
        model = self.query_one("#prov-model-input", Input).value.strip()
        key_val = self.query_one("#prov-key-input", Input).value.strip()

        if bid == "btn-toggle-key-vis":
            key_inp = self.query_one("#prov-key-input", Input)
            key_inp.password = not key_inp.password
            event.button.label = "Hide" if not key_inp.password else "Show"

        elif bid == "btn-toggle-enable":
            pid = self._provider.id or self._provider.key
            if pid and not pid.startswith("new_"):
                new_state = not self._provider.is_enabled
                gateway.toggle_provider(pid, new_state)
            else:
                state.push_notification("Save provider before toggling enable status.", NotifLevel.WARNING)

        elif bid == "btn-save-key":
            if not name:
                state.push_notification("Provider name is required.", NotifLevel.WARNING)
                return
            if ptype not in SUPPORTED_PROTOCOLS:
                state.push_notification(f"Invalid protocol '{ptype}'. Must be one of: {', '.join(SUPPORTED_PROTOCOLS)}", NotifLevel.WARNING)
                return
            if not base_url:
                state.push_notification("Base URL is required.", NotifLevel.WARNING)
                return

            payload = {
                "name": name,
                "provider_type": ptype,
                "base_url": base_url,
                "default_model": model,
                "is_enabled": True,
            }
            if self._provider.id and not self._provider.id.startswith("new_"):
                payload["id"] = self._provider.id
            if key_val:
                payload["api_key"] = key_val

            gateway.save_provider(payload)
            self.query_one("#prov-key-input", Input).value = ""
            self.query_one("#prov-key-input", Input).password = True
            try:
                self.query_one("#btn-toggle-key-vis", Button).label = "Show"
            except Exception:
                pass

        elif bid == "btn-remove-key":
            pid = self._provider.id or self._provider.key
            if pid and not pid.startswith("new_"):
                gateway.delete_provider(pid)
            else:
                state.push_notification("Provider has not been saved yet.", NotifLevel.WARNING)

        elif bid == "btn-test-key":
            pid = self._provider.id or self._provider.key
            if pid and not pid.startswith("new_"):
                gateway.test_provider(pid)
            else:
                state.push_notification("Save provider before testing connection.", NotifLevel.WARNING)

        elif bid == "btn-default-key":
            pid = self._provider.id or self._provider.key
            if pid and not pid.startswith("new_"):
                gateway.set_default_provider(pid)
            else:
                state.push_notification("Save provider before marking as default.", NotifLevel.WARNING)


class ProvidersScreen(Widget):
    """Provider Manager: live status list + BYOK management."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("r",      "refresh",      "Refresh",      show=True),
        Binding("n",      "new_provider", "New Provider", show=True),
        Binding("escape", "back",         "Back",         show=True),
    ]

    DEFAULT_CSS = """
    ProvidersScreen {
        height: 1fr;
        width: 1fr;
    }
    #providers-toolbar {
        height: 1;
        background: #12151f;
        border-bottom: solid #1e2433;
        padding: 0 1;
        color: #64748b;
    }
    #providers-list-scroll {
        height: 1fr;
        width: 44;
        border-right: solid #1e2433;
        background: #12151f;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._selected_idx = 0

    def compose(self) -> ComposeResult:
        yield Label(
            f" {Glyphs.PROVIDERS} Providers   "
            f"[{Colors.TEXT_DIM}]↑↓[/] navigate  "
            f"[{Colors.TEXT_DIM}]n[/] new provider  "
            f"[{Colors.TEXT_DIM}]r[/] refresh",
            id="providers-toolbar"
        )
        with Horizontal():
            yield ScrollableContainer(id="providers-list-scroll")
            yield ProviderDetailPane(id="providers-detail")

    def on_mount(self) -> None:
        self._unsub = state.subscribe("providers", self._on_providers_changed)
        self._rebuild()
        from aetherion_cli.tui.gateway import gateway
        gateway.load_providers()

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _on_providers_changed(self) -> None:
        self.app.call_from_thread(self._rebuild)

    def _rebuild(self) -> None:
        scroll = self.query_one("#providers-list-scroll")
        scroll.remove_children()
        providers = state.providers

        if state.providers_loading:
            scroll.mount(Static(f"  {Glyphs.RUNNING} Loading providers…", classes="loading-indicator"))
            return

        if not providers:
            scroll.mount(Static(
                f"\n  {Glyphs.PROVIDERS}  No providers configured.\n\n"
                "  Press [n] to add a new provider.",
                classes="empty-state"
            ))
            return

        self._selected_idx = max(0, min(self._selected_idx, len(providers) - 1))
        for i, p in enumerate(providers):
            row = ProviderRow(p, idx=i, selected=i == self._selected_idx)
            scroll.mount(row)

        if 0 <= self._selected_idx < len(providers):
            self.query_one(ProviderDetailPane).show_provider(providers[self._selected_idx])

    def select_provider_by_index(self, idx: int) -> None:
        providers = state.providers
        if 0 <= idx < len(providers):
            self._selected_idx = idx
            self._rebuild()

    def on_key(self, event) -> None:
        providers = state.providers
        n = len(providers)
        if event.key == "up" and n:
            self._selected_idx = max(0, self._selected_idx - 1)
            self._rebuild()
            event.prevent_default()
        elif event.key == "down" and n:
            self._selected_idx = min(n - 1, self._selected_idx + 1)
            self._rebuild()
            event.prevent_default()

    def action_new_provider(self) -> None:
        self.query_one(ProviderDetailPane).show_new_template()

    def action_refresh(self) -> None:
        from aetherion_cli.tui.gateway import gateway
        gateway.load_providers()

    def action_back(self) -> None:
        state.navigate_back()
