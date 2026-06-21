"""AgentsScreen — modal showing available agents."""

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


class AgentsScreen(ModalScreen[None]):
    """Agents list modal — shows available agent definitions."""

    BINDINGS = [Binding("escape", "dismiss_screen", "Close", priority=True)]

    CSS = """
    AgentsScreen {
        align: center middle;
        background: $background 65%;
    }

    #agents-container {
        width: 72%;
        max-width: 90;
        height: 65%;
        border: round $primary 50%;
        background: $surface;
        padding: 0;
    }

    #agents-header {
        height: 3;
        background: $panel;
        border-bottom: solid $primary 20%;
        padding: 0 2;
        align: left middle;
    }

    #agents-title {
        text-style: bold;
        width: 1fr;
        color: $primary;
    }

    #agents-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }

    #agents-status {
        height: 1;
        color: $text-muted;
        padding: 0 2;
    }

    #agents-table {
        height: 1fr;
        margin: 0 1;
        scrollbar-size: 1 1;
    }

    #agents-hint {
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
        with Container(id="agents-container"):
            with Horizontal(id="agents-header"):
                yield Static("Available Agents", id="agents-title")
                yield Static("[bold red]\\[x][/bold red]", id="agents-close")
            yield Static("[dim]Loading…[/dim]", id="agents-status")
            table = DataTable(id="agents-table", cursor_type="row", zebra_stripes=True)
            yield table
            yield Static("[dim]esc[/dim] close", id="agents-hint")

    def on_mount(self) -> None:
        table = self.query_one("#agents-table", DataTable)
        table.add_columns("Agent", "Type", "Source")
        self._load_data()

    @work
    async def _load_data(self) -> None:
        table = self.query_one("#agents-table", DataTable)
        status_w = self.query_one("#agents-status", Static)

        try:
            data = await self._client.sessions.get_context(self._session_id)
        except Exception as exc:
            err = str(exc)
            if "404" in err:
                status_w.update("[dim]Session not connected. Send a message first.[/dim]")
            else:
                status_w.update(f"[red]Error: {escape(str(exc)[:80])}[/red]")
            return

        agents = data.get("agents", [])
        if not agents:
            status_w.update("[dim]No agents configured[/dim]")
            return

        status_w.update(f"[dim]{len(agents)} agent{'s' if len(agents) != 1 else ''}[/dim]")

        for a in agents:
            if isinstance(a, dict):
                name = a.get("name") or a.get("agentType") or a.get("type") or "?"
                agent_type = a.get("agentType") or a.get("type") or "—"
                source = a.get("source") or "—"
            else:
                name = str(a)
                agent_type = "—"
                source = "—"
            table.add_row(escape(name), escape(agent_type), escape(source))

    def on_click(self, event) -> None:
        if getattr(event.widget, "id", None) == "agents-close":
            self.dismiss(None)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)
