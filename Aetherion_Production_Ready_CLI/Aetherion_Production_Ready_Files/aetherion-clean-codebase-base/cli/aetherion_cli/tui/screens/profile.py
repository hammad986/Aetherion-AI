"""
aetherion_cli/tui/screens/profile.py
Authenticated User Portal — Profile, API Keys, Billing, Usage, Plans, Security.

Uses the existing JWT session — no separate auth. All data fetched via gateway.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import ClassVar, Dict, List, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.widget import Widget
from textual.widgets import Button, DataTable, Input, Label, Static, TabbedContent, TabPane

from aetherion_cli.tui.state import Screen as AppScreen, state
from aetherion_cli.tui.theme import Colors, Glyphs


# ─────────────────────────────────────────────────────────────────────────────
# Sub-panes
# ─────────────────────────────────────────────────────────────────────────────

class ProfilePane(Widget):
    """User profile information and account settings."""

    DEFAULT_CSS = """
    ProfilePane { height: auto; padding: 1 2; }
    .profile-field { height: 1; margin-bottom: 1; }
    .profile-label { color: #64748b; width: 18; }
    .profile-value { color: #e2e8f0; }
    #profile-display-name { width: 35; margin-top: 1; margin-bottom: 1; }
    #profile-save-btn { margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Label(f" {Glyphs.USER_ICON}  Profile", classes="dash-section-title")
        with Container(id="profile-fields"):
            yield Label("Loading profile…", id="profile-content")
            yield Label("  Display Name:", classes="profile-label")
            yield Input(placeholder="Display name", id="profile-display-name")
        yield Button(f"{Glyphs.DONE} Save Changes", id="profile-save-btn")

    def on_mount(self) -> None:
        self._unsub = state.subscribe("user_email", self._refresh)
        self._refresh_direct()

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _refresh(self) -> None:
        """Called from background thread."""
        self.app.call_from_thread(self._refresh_direct)

    def _refresh_direct(self) -> None:
        """Called on asyncio event loop."""
        email = state.user_email or "Not logged in"
        plan  = state.user_plan or "Lite"
        uid   = state.user_id or "—"
        t = Text()
        t.append(f"  Email:     ", style=f"bold {Colors.TEXT_MUTED}")
        t.append(f"{email}\n",      style=Colors.TEXT_PRIMARY)
        t.append(f"  Plan:      ", style=f"bold {Colors.TEXT_MUTED}")
        t.append(f"{plan}\n",       style=Colors.ACCENT_BRIGHT)
        t.append(f"  User ID:   ", style=f"bold {Colors.TEXT_MUTED}")
        t.append(f"{uid}\n",        style=Colors.TEXT_DIM)
        t.append(f"  Status:    ", style=f"bold {Colors.TEXT_MUTED}")
        t.append("Active",          style=Colors.SUCCESS)
        try:
            self.query_one("#profile-content", Label).update(t)
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "profile-save-btn":
            inp = self.query_one("#profile-display-name", Input)
            val = inp.value.strip()
            if not val:
                state.push_notification("Display name cannot be empty", "warning")
                return
            from aetherion_cli.tui.gateway import gateway
            gateway.update_profile(val)


class ApiKeysPane(Widget):
    """Manage personal API keys for the Aetherion platform."""

    DEFAULT_CSS = """
    ApiKeysPane { height: 1fr; padding: 1 2; }
    #api-keys-table { height: 9; margin-bottom: 1; }
    #api-key-actions { height: 3; margin-bottom: 1; }
    #new-key-name { width: 35; margin-right: 1; }
    #api-key-reveal-box {
        display: none;
        border: solid #22c55e;
        background: #0d1f14;
        padding: 1 2;
        margin-bottom: 1;
        height: auto;
    }
    #api-key-reveal-box.visible {
        display: block;
    }
    #reveal-title {
        color: #22c55e;
        text-style: bold;
    }
    #reveal-sub {
        color: #94a3b8;
        margin-bottom: 1;
    }
    #reveal-key-value {
        color: #22c55e;
        text-style: bold;
        background: #05130b;
        border: solid #166534;
        padding: 0 1;
        margin-bottom: 1;
        height: 3;
    }
    #reveal-actions {
        height: 3;
    }
    #btn-copy-revealed-key {
        margin-right: 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._latest_generated_key: str = ""

    def compose(self) -> ComposeResult:
        yield Label(f" {Glyphs.LOCK}  API Keys", classes="dash-section-title")
        yield Label(
            "  Personal API keys authenticate your CLI and scripts against the Aetherion API.",
            classes="dash-label"
        )
        with Vertical(id="api-key-reveal-box"):
            yield Label(f" {Glyphs.SPARKLE}  New API Key Generated", id="reveal-title")
            yield Label("Make sure to copy your personal API key now. You won't be able to see it again!", id="reveal-sub")
            yield Static("", id="reveal-key-value")
            with Horizontal(id="reveal-actions"):
                yield Button("📋 Copy to Clipboard", id="btn-copy-revealed-key", variant="default")
                yield Button("Done", id="btn-dismiss-reveal", variant="default")
        yield DataTable(id="api-keys-table", show_cursor=True)
        with Horizontal(id="api-key-actions"):
            yield Input(placeholder="Key label (e.g. work-laptop)", id="new-key-name")
            yield Button(f"{Glyphs.BOLT} Generate Key", id="btn-gen-key")
        yield Button(f"{Glyphs.ERROR} Revoke Selected", id="btn-revoke-key", variant="default")

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        t.add_columns("Key ID", "Type", "Label", "Created", "Last Used", "Prefix")
        self._refresh_table()

    def _refresh_table(self) -> None:
        from aetherion_cli.tui.gateway import gateway
        t = self.query_one(DataTable)
        t.clear()
        keys = gateway.list_personal_api_keys()
        if not keys:
            t.add_row("default", "Local / Offline", "CLI Default", "—", "—", "aeth_****", key="default")
        else:
            for k in keys:
                kid = k.get("id", "key")
                ktype = k.get("type") or ("Local / Offline" if (str(kid).startswith("dev_") or kid == "default") else "Server Key")
                name = k.get("name", "unnamed")
                created = k.get("created", "—")
                used = k.get("last_used", "—")
                pfx = k.get("prefix", "aeth_****")
                t.add_row(kid, ktype, name, created, used, pfx, key=kid)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        from aetherion_cli.tui.gateway import gateway
        if bid == "btn-gen-key":
            inp = self.query_one("#new-key-name", Input)
            name = inp.value.strip()
            # Validate label: reject empty, "nan", "none", "null", "undefined"
            if not name or name.lower() in ("nan", "none", "null", "undefined"):
                state.push_notification("Please specify a valid label (e.g. work-laptop)", "warning")
                return
            if len(name) > 64:
                name = name[:64]
            entry = gateway.generate_personal_api_key(name)
            inp.value = ""
            self._refresh_table()
            raw_key = entry.get("key", "")
            self._latest_generated_key = raw_key
            reveal_box = self.query_one("#api-key-reveal-box")
            self.query_one("#reveal-key-value", Static).update(f"  {raw_key}")
            reveal_box.add_class("visible")
            ktype = entry.get("type", "Server Key")
            state.push_notification(f"Generated {ktype} '{name}' ({entry.get('prefix')})", "success")
        elif bid == "btn-copy-revealed-key":
            val = getattr(self, "_latest_generated_key", "")
            if val:
                copied = False
                try:
                    import pyperclip
                    pyperclip.copy(val)
                    copied = True
                except Exception:
                    pass
                if not copied:
                    try:
                        self.app.copy_to_clipboard(val)
                        copied = True
                    except Exception:
                        pass
                if copied:
                    state.push_notification("API key copied to clipboard!", "success")
                else:
                    state.push_notification("Could not access system clipboard", "warning")
        elif bid == "btn-dismiss-reveal":
            self._latest_generated_key = ""
            reveal_box = self.query_one("#api-key-reveal-box")
            self.query_one("#reveal-key-value", Static).update("")
            reveal_box.remove_class("visible")
        elif bid == "btn-revoke-key":
            t = self.query_one(DataTable)
            try:
                if t.row_count > 0:
                    row_key = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
                    if row_key and row_key != "default":
                        ok = gateway.revoke_personal_api_key(str(row_key))
                        if ok:
                            state.push_notification(f"Revoked API key {row_key}", "success")
                        else:
                            state.push_notification("Could not revoke selected key", "warning")
                        self._refresh_table()
                    else:
                        state.push_notification("Cannot revoke default system key", "warning")
                else:
                    state.push_notification("No key selected to revoke", "warning")
            except Exception:
                state.push_notification("No key selected to revoke", "warning")


class BillingPane(Widget):
    """Subscription, plan, and payment information."""

    DEFAULT_CSS = """
    BillingPane { height: 1fr; padding: 1 2; }
    .plan-card {
        border: solid #1e2433;
        background: #12151f;
        padding: 1 2;
        margin-bottom: 1;
        height: auto;
        width: 1fr;
    }
    .plan-card.active-plan { border: solid #7c3aed; }
    .plan-name { color: #9d5cf0; text-style: bold; }
    .plan-price { color: #e2e8f0; text-style: bold; }
    .plan-features { color: #64748b; }
    #billing-portal-btn { margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Label(f" {Glyphs.STAR}  Billing & Subscription", classes="dash-section-title")

        current_plan = state.user_plan or "Lite"

        plans = [
            ("Lite",  "Free",    "2 sessions/day · 50K tokens · Community support",   current_plan == "Lite"),
            ("Pro",   "$19/mo",  "Unlimited sessions · 2M tokens · Priority support", current_plan == "Pro"),
            ("Team",  "$49/mo",  "5 seats · Custom models · SSO · SLA",               current_plan == "Team"),
            ("Scale", "$149/mo", "25 seats · Custom domains · Dedicated infra",       current_plan == "Scale"),
        ]

        for name, price, features, is_active in plans:
            classes = "plan-card" + (" active-plan" if is_active else "")
            with Container(classes=classes):
                if is_active:
                    yield Label(f"  {Glyphs.DONE} {name} (Current)", classes="plan-name")
                else:
                    yield Label(f"  {name}", classes="plan-name")
                yield Label(f"  {price}", classes="plan-price")
                yield Label(f"  {features}", classes="plan-features")

        yield Button(f"{Glyphs.BOLT} Upgrade / Invoicing Contact", id="billing-portal-btn")
        yield Label(
            f"\n  Architecture: Razorpay Order Processor (database-backed).\n"
            f"  [{Colors.TEXT_DIM}]Notice: Self-service web checkout is deferred in the CLI release.\n"
            f"  All core tools operate in local developer mode. For enterprise plans, email sales@aetherion.ai.[/{Colors.TEXT_DIM}]",
            id="billing-note"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "billing-portal-btn":
            state.push_notification("Web billing portal deferred in CLI release. Contact sales@aetherion.ai for upgrades.", "info")


class UsagePane(Widget):
    """Token usage, request counts, and quota information."""

    DEFAULT_CSS = """
    UsagePane { height: 1fr; padding: 1 2; }
    .usage-bar-container { height: 3; margin-bottom: 1; }
    .usage-metric { height: 2; border: solid #1e2433; background: #12151f; padding: 0 1; margin-bottom: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Label(f" {Glyphs.METRICS}  Usage", classes="dash-section-title")
        m = state.dashboard_metrics

        metrics = [
            ("Sessions This Month", str(getattr(m, "sessions_this_month", 0)), Glyphs.SESSIONS),
            ("Total Tokens Used",   f"{getattr(m, 'total_tokens', 0):,}",     Glyphs.STREAMING),
            ("Agent Runs",          str(getattr(m, "agent_runs", 0)),         Glyphs.RUNNING),
            ("Tool Calls",          str(getattr(m, "tool_calls", 0)),         Glyphs.TOOL_ICON),
        ]

        for label, value, icon in metrics:
            with Container(classes="usage-metric"):
                t = Text()
                t.append(f"  {icon}  ", style=f"bold {Colors.ACCENT_BRIGHT}")
                t.append(f"{value}", style=f"bold {Colors.TEXT_PRIMARY}")
                t.append(f"   {label}", style=Colors.TEXT_MUTED)
                yield Static(t)

        yield Label(
            f"  [{Colors.TEXT_DIM}]Usage resets on your billing cycle date.[/{Colors.TEXT_DIM}]",
        )


class SecurityPane(Widget):
    """Security settings: sessions, 2FA, connected devices."""

    DEFAULT_CSS = """
    SecurityPane { height: 1fr; padding: 1 2; }
    .security-action { margin-bottom: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Label(f" {Glyphs.LOCK}  Security", classes="dash-section-title")
        yield Label(
            f"  [{Colors.TEXT_MUTED}]Active sessions are listed below. "
            f"Log out all devices to revoke tokens.[/{Colors.TEXT_MUTED}]"
        )
        yield Button(f"{Glyphs.ERROR} Log Out All Devices", id="btn-logout-all", classes="security-action")
        yield Button(f"{Glyphs.LOCK} Change Password", id="btn-change-password", classes="security-action")
        yield Label("\n  2FA is not yet available in this release.", classes="dash-label")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-logout-all":
            from aetherion_cli.tui.gateway import gateway
            state.push_notification("Logging out all sessions…", "warning")
            gateway.logout_everywhere()
        elif bid == "btn-change-password":
            if getattr(state, "offline_mode", False) or not state.connected:
                state.push_notification("Cannot reset password while offline", "warning")
            else:
                from aetherion_cli.config.settings import get_server_url
                try:
                    webbrowser.open(f"{get_server_url().rstrip('/')}/reset-password")
                    state.push_notification("Opening password reset in browser…", "info")
                except Exception:
                    state.push_notification("Could not open browser", "error")



# ─────────────────────────────────────────────────────────────────────────────
# Main Profile Screen
# ─────────────────────────────────────────────────────────────────────────────

class ProfileScreen(Widget):
    """
    Authenticated User Portal.

    Tabs: Profile | API Keys | Billing | Usage | Security
    All data sourced from the existing JWT session + backend API.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "back", "Back", show=True),
    ]

    DEFAULT_CSS = """
    ProfileScreen {
        height: 1fr;
        width: 1fr;
    }
    #profile-toolbar {
        height: 1;
        background: #12151f;
        border-bottom: solid #1e2433;
        padding: 0 1;
        color: #64748b;
    }
    #profile-tabs {
        height: 1fr;
    }
    TabbedContent { height: 1fr; }
    ContentSwitcher { height: 1fr; }
    TabPane { height: 1fr; padding: 0; }
    """

    def compose(self) -> ComposeResult:
        email = state.user_email or "Not logged in"
        yield Label(
            f" {Glyphs.USER_ICON} Portal  ·  {email}   "
            f"[{Colors.TEXT_DIM}]Esc[/{Colors.TEXT_DIM}] back",
            id="profile-toolbar"
        )
        with TabbedContent(id="profile-tabs"):
            with TabPane("Profile", id="tab-profile"):
                yield ProfilePane()
            with TabPane("API Keys", id="tab-apikeys"):
                yield ApiKeysPane()
            with TabPane("Billing", id="tab-billing"):
                yield BillingPane()
            with TabPane("Usage", id="tab-usage"):
                yield UsagePane()
            with TabPane("Security", id="tab-security"):
                yield SecurityPane()

    def action_back(self) -> None:
        state.navigate_back()
