"""SkillsScreen — modal showing available skills."""

from __future__ import annotations

from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.markup import escape
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from rai.client.claude import ClaudeServeClient


class SkillsScreen(ModalScreen[None]):
    """Skills list modal — shows available slash-command skills."""

    BINDINGS = [Binding("escape", "dismiss_screen", "Close", priority=True)]

    CSS = """
    SkillsScreen {
        align: center middle;
        background: $background 65%;
    }

    #skills-container {
        width: 72%;
        max-width: 95;
        height: 70%;
        border: round $primary 50%;
        background: $surface;
        padding: 0;
    }

    #skills-header {
        height: 3;
        background: $panel;
        border-bottom: solid $primary 20%;
        padding: 0 2;
        align: left middle;
    }

    #skills-title {
        text-style: bold;
        width: 1fr;
        color: $primary;
    }

    #skills-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }

    #skills-status {
        height: 1;
        color: $text-muted;
        padding: 0 2;
    }

    #skills-table {
        height: 1fr;
        margin: 0 1;
        scrollbar-size: 1 1;
    }

    #skills-hint {
        height: 2;
        padding: 0 2;
        color: $text-muted;
        border-top: solid $primary 15%;
        content-align: left middle;
    }
    """

    def __init__(self, client: ClaudeServeClient, session_id: str) -> None:
        super().__init__()
        self._client = client
        self._session_id = session_id

    def compose(self) -> ComposeResult:
        with Container(id="skills-container"):
            with Horizontal(id="skills-header"):
                yield Static("Available Skills", id="skills-title")
                yield Static("[bold red]\\[x][/bold red]", id="skills-close")
            yield Static("[dim]Loading…[/dim]", id="skills-status")
            table = DataTable(id="skills-table", cursor_type="row", zebra_stripes=True)
            yield table
            yield Static("[dim]esc[/dim] close", id="skills-hint")

    def on_mount(self) -> None:
        table = self.query_one("#skills-table", DataTable)
        table.add_columns("Skill", "Description")
        self._load_data()

    @work
    async def _load_data(self) -> None:
        table = self.query_one("#skills-table", DataTable)
        status_w = self.query_one("#skills-status", Static)

        try:
            data = await self._client.sessions.get_context(self._session_id)
        except Exception as exc:
            err = str(exc)
            if "404" in err:
                status_w.update("[dim]Session not connected. Send a message first.[/dim]")
            else:
                status_w.update(f"[red]Error: {escape(str(exc)[:80])}[/red]")
            return

        skills_data = data.get("skills", {})
        skill_list: list[dict[str, Any]] = []

        if isinstance(skills_data, dict):
            skill_list = skills_data.get("skillFrontmatter", [])
        elif isinstance(skills_data, list):
            for s in skills_data:
                if isinstance(s, dict):
                    skill_list.append(s)
                else:
                    skill_list.append({"name": str(s)})

        if not skill_list:
            status_w.update("[dim]No skills available[/dim]")
            return

        status_w.update(f"[dim]{len(skill_list)} skill{'s' if len(skill_list) != 1 else ''}[/dim]")

        for s in skill_list:
            name = s.get("name") or "?"
            desc = s.get("description") or s.get("desc") or "—"
            table.add_row(f"[cyan]{escape(name)}[/cyan]", escape(desc[:80]))

    def on_click(self, event) -> None:
        if getattr(event.widget, "id", None) == "skills-close":
            self.dismiss(None)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)
