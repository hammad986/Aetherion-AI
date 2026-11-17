"""
aetherion_cli/tui/screens/skills.py
Skills Manager — browse, enable/disable, and create skills.

2-pane layout:
  Left:  category filter + searchable skill list
  Right: skill detail + YAML preview + enable/disable toggle

Unique feature: "Create Skill" button launches the workflow-skill-creator
which introspects the current conversation and packages it as a reusable
skill under ~/.aetherion/config/skills/<name>/SKILL.md
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

from aetherion_cli.tui.state import Screen as AppScreen, Skill, state
from aetherion_cli.tui.theme import Colors, Glyphs


class SkillRow(Static):
    """Single skill entry in the list."""

    DEFAULT_CSS = """
    SkillRow {
        height: 2;
        padding: 0 1;
        border-bottom: solid #1e2433;
    }
    SkillRow.selected { background: #4c2288; }
    SkillRow:hover    { background: #1a1e2e; }
    """

    def __init__(self, skill: Skill, selected: bool = False) -> None:
        super().__init__()
        self._skill = skill
        if selected:
            self.add_class("selected")

    def render(self) -> Text:  # type: ignore[override]
        s   = self._skill
        sel = "selected" in self.classes
        prefix = f"{Glyphs.CHEVRON_RIGHT} " if sel else "  "

        t = Text()
        t.append(prefix, style=f"bold {Colors.ACCENT}" if sel else Colors.TEXT_DIM)

        if s.enabled:
            t.append(f"{Glyphs.DONE} ", style=Colors.SUCCESS)
        else:
            t.append(f"{Glyphs.IDLE} ", style=Colors.TEXT_DIM)

        if s.builtin:
            t.append(f"{Glyphs.SKILLS} ", style=Colors.INFO)

        t.append(s.name[:28], style=f"bold {Colors.TEXT_PRIMARY}" if sel else Colors.TEXT_PRIMARY)
        t.append(f"  v{s.version}", style=Colors.TEXT_DIM)
        t.append("\n")
        t.append(f"  {s.description[:50]}", style=Colors.TEXT_MUTED)
        return t


class SkillDetailPane(Widget):
    """Right pane: skill detail, enable/disable, and create-new form."""

    DEFAULT_CSS = """
    SkillDetailPane {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
        background: #0a0c12;
    }
    #skill-title      { color: #9d5cf0; text-style: bold; height: 2; }
    #skill-meta       { color: #64748b; height: auto; margin-bottom: 1; }
    #skill-desc       { color: #e2e8f0; height: auto; border: solid #1e2433;
                        padding: 1; background: #12151f; margin-bottom: 1; }
    #skill-actions    { height: 3; margin-bottom: 1; }
    #create-skill-sep { height: 1; color: #334155; border-top: solid #1e2433;
                        padding-top: 1; margin-bottom: 1; }
    #create-name      { height: 3; margin-bottom: 1; }
    #create-desc      { height: 4; margin-bottom: 1; }
    #create-actions   { height: 3; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._skill: Optional[Skill] = None

    def compose(self) -> ComposeResult:
        yield Label(f"{Glyphs.SKILLS} Select a skill →", id="skill-title")
        yield Label("", id="skill-meta")
        yield Label("", id="skill-desc")
        with Horizontal(id="skill-actions"):
            yield Button(f"{Glyphs.DONE} Enable",  id="btn-enable",  variant="default")
            yield Button(f"{Glyphs.IDLE} Disable", id="btn-disable", variant="default")

        # ── Create-new-skill panel ──
        yield Label(
            f" {Glyphs.SPARKLE} Create New Skill from Conversation",
            id="create-skill-sep"
        )
        yield Input(
            placeholder="Skill name (e.g. git-workflow)",
            id="create-name"
        )
        yield TextArea(
            id="create-desc",
            # placeholder text via a Static hint below
        )
        yield Static(
            "  Describe what this skill does (becomes the SKILL.md description).",
            classes="loading-indicator"
        )
        with Horizontal(id="create-actions"):
            yield Button(
                f"{Glyphs.SPARKLE} Create Skill", id="btn-create-skill", variant="default"
            )

    def show_skill(self, s: Skill) -> None:
        self._skill = s
        self.query_one("#skill-title", Label).update(
            f"{Glyphs.SKILLS} {s.name}  v{s.version}"
        )
        builtin_tag = "  [builtin]" if s.builtin else ""
        enabled_tag = (
            f"[{Colors.SUCCESS}]{Glyphs.DONE} Enabled[/{Colors.SUCCESS}]"
            if s.enabled
            else f"[{Colors.TEXT_DIM}]{Glyphs.IDLE} Disabled[/{Colors.TEXT_DIM}]"
        )
        self.query_one("#skill-meta", Label).update(
            f"Status: {enabled_tag}{builtin_tag}"
        )
        self.query_one("#skill-desc", Label).update(
            s.description or "No description."
        )

        enable_btn  = self.query_one("#btn-enable",  Button)
        disable_btn = self.query_one("#btn-disable", Button)
        enable_btn.disabled  = s.enabled
        disable_btn.disabled = not s.enabled

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-enable" and self._skill:
            skill_name = self._skill.name
            from aetherion_cli.tui.gateway import gateway
            gateway.enable_skill(skill_name)

        elif bid == "btn-disable" and self._skill:
            skill_name = self._skill.name
            from aetherion_cli.tui.gateway import gateway
            gateway.disable_skill(skill_name)

        elif bid == "btn-create-skill":
            name = self.query_one("#create-name", Input).value.strip()
            desc = self.query_one("#create-desc", TextArea).text.strip()
            if not name:
                state.push_notification("Skill name is required.", level=__import__(
                    "aetherion_cli.tui.state", fromlist=["NotifLevel"]
                ).NotifLevel.WARNING)
                return
            self._launch_skill_creator(name, desc)

    def _launch_skill_creator(self, name: str, description: str) -> None:
        """Delegate to the skill creator module."""
        from aetherion_cli.tui.skill_creator import create_skill_from_conversation
        state.push_notification(f"Creating skill '{name}'…")
        create_skill_from_conversation(
            name        = name,
            description = description,
            messages    = list(state.messages),
        )


class SkillsScreen(Widget):
    """Skills Manager — 2-pane with search, category filter, create-new panel."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("r",      "refresh", "Refresh", show=True),
        Binding("escape", "back",    "Back",    show=True),
        Binding("e",      "toggle_enabled", "Enabled only", show=True),
        Binding("a",      "show_all",       "All",          show=True),
    ]

    DEFAULT_CSS = """
    SkillsScreen {
        height: 1fr;
        width: 1fr;
    }
    #skills-toolbar {
        height: 1;
        background: #12151f;
        border-bottom: solid #1e2433;
        padding: 0 1;
        color: #64748b;
    }
    #skills-left {
        width: 40;
        border-right: solid #1e2433;
        background: #12151f;
        height: 1fr;
    }
    #skills-filter-bar {
        height: 3;
        border-bottom: solid #1e2433;
        padding: 0 1;
    }
    #skills-filter-bar Input { border: none; background: #12151f; }
    #skills-list-scroll { height: 1fr; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._filter       = ""
        self._selected_idx = 0
        self._enabled_only = False

    def compose(self) -> ComposeResult:
        yield Label(
            f" {Glyphs.SKILLS} Skills   "
            f"[{Colors.TEXT_DIM}]↑↓[/] navigate  "
            f"[{Colors.TEXT_DIM}]e[/] enabled  "
            f"[{Colors.TEXT_DIM}]a[/] all  "
            f"[{Colors.TEXT_DIM}]r[/] refresh",
            id="skills-toolbar"
        )
        with Horizontal():
            with Vertical(id="skills-left"):
                with Container(id="skills-filter-bar"):
                    yield Input(
                        placeholder=f"{Glyphs.BULLET} Filter skills…",
                        id="skills-filter"
                    )
                yield ScrollableContainer(id="skills-list-scroll")
            yield SkillDetailPane(id="skills-detail")

    def on_mount(self) -> None:
        self._unsub = state.subscribe("skills", self._on_skills_changed)
        from aetherion_cli.tui.gateway import gateway
        gateway.load_skills()

    def on_unmount(self) -> None:
        if hasattr(self, "_unsub"):
            self._unsub()

    def _filtered(self) -> list[Skill]:
        q  = self._filter.lower()
        sk = state.skills
        if self._enabled_only:
            sk = [s for s in sk if s.enabled]
        if q:
            sk = [s for s in sk if q in s.name.lower() or q in s.description.lower()]
        return sk

    def _on_skills_changed(self) -> None:
        self.app.call_from_thread(self._rebuild)

    def _rebuild(self) -> None:
        scroll  = self.query_one("#skills-list-scroll")
        scroll.remove_children()
        skills  = self._filtered()

        if state.skills_loading:
            scroll.mount(Static(f"  {Glyphs.RUNNING} Loading skills…", classes="loading-indicator"))
            return

        if not skills:
            scroll.mount(Static(
                f"\n  {Glyphs.SKILLS}  No skills found.\n\n  Try [a] to show all.",
                classes="empty-state"
            ))
            return

        self._selected_idx = max(0, min(self._selected_idx, len(skills) - 1))
        for i, s in enumerate(skills[:100]):
            row = SkillRow(s, selected=i == self._selected_idx)
            scroll.mount(row)

        if 0 <= self._selected_idx < len(skills):
            self.query_one(SkillDetailPane).show_skill(skills[self._selected_idx])

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "skills-filter":
            self._filter       = event.value
            self._selected_idx = 0
            self._rebuild()

    def on_key(self, event: events.Key) -> None:
        skills = self._filtered()
        n = len(skills)
        if event.key == "up" and n:
            self._selected_idx = max(0, self._selected_idx - 1)
            self._rebuild()
            event.prevent_default()
        elif event.key == "down" and n:
            self._selected_idx = min(n - 1, self._selected_idx + 1)
            self._rebuild()
            event.prevent_default()

    def action_toggle_enabled(self) -> None:
        self._enabled_only = True
        self._selected_idx = 0
        self._rebuild()

    def action_show_all(self) -> None:
        self._enabled_only = False
        self._selected_idx = 0
        self._rebuild()

    def action_refresh(self) -> None:
        from aetherion_cli.tui.gateway import gateway
        gateway.load_skills()

    def action_back(self) -> None:
        state.navigate_back()
