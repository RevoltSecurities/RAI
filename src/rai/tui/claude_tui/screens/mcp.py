"""MCPScreen — modal showing MCP server connection status."""

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

_STATUS_MARKUP = {
    "connected": "[green]●[/green] connected",
    "failed": "[red]●[/red] failed",
    "needs-auth": "[yellow]●[/yellow] needs-auth",
    "pending": "[yellow]●[/yellow] pending",
    "disabled": "[dim]●[/dim] disabled",
}


class MCPScreen(ModalScreen[None]):
    """MCP server status modal — shows connection state and available tools."""

    BINDINGS = [Binding("escape", "dismiss_screen", "Close", priority=True)]

    CSS = """
    MCPScreen {
        align: center middle;
        background: $background 65%;
    }

    #mcp-container {
        width: 85%;
        max-width: 110;
        height: 75%;
        border: round $primary 50%;
        background: $surface;
        padding: 0;
    }

    #mcp-header {
        height: 3;
        background: $panel;
        border-bottom: solid $primary 20%;
        padding: 0 2;
        align: left middle;
    }

    #mcp-title {
        text-style: bold;
        width: 1fr;
        color: $primary;
    }

    #mcp-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }

    #mcp-status {
        height: 1;
        color: $text-muted;
        padding: 0 2;
    }

    #mcp-table {
        height: 1fr;
        margin: 0 1;
        scrollbar-size: 1 1;
    }

    #mcp-hint {
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
        with Container(id="mcp-container"):
            with Horizontal(id="mcp-header"):
                yield Static("MCP Servers", id="mcp-title")
                yield Static("[bold red]\\[x][/bold red]", id="mcp-close")
            yield Static("[dim]Loading…[/dim]", id="mcp-status")
            table = DataTable(id="mcp-table", cursor_type="row", zebra_stripes=True)
            yield table
            yield Static(
                "[dim]esc[/dim] close",
                id="mcp-hint",
            )

    def on_mount(self) -> None:
        table = self.query_one("#mcp-table", DataTable)
        table.add_columns("Name", "Status", "Tools", "Scope")
        self._load_data()

    @work
    async def _load_data(self) -> None:
        table = self.query_one("#mcp-table", DataTable)
        status_w = self.query_one("#mcp-status", Static)

        try:
            data = await self._client.sessions.get_mcp(self._session_id)
        except Exception as exc:
            err = str(exc)
            if "404" in err:
                status_w.update("[dim]Session not connected. Send a message first.[/dim]")
            else:
                status_w.update(f"[red]Error: {escape(str(exc)[:80])}[/red]")
            return

        servers = data.get("mcpServers", [])
        if not servers:
            status_w.update("[dim]No MCP servers configured[/dim]")
            return

        connected = sum(1 for s in servers if s.get("status") == "connected")
        status_w.update(f"[dim]{len(servers)} server{'s' if len(servers) != 1 else ''} · {connected} connected[/dim]")

        for srv in servers:
            name = escape(srv.get("name", "?"))
            st = srv.get("status", "?")
            st_markup = _STATUS_MARKUP.get(st, f"[dim]{escape(st)}[/dim]")
            tools = srv.get("tools", [])
            tool_count = str(len(tools)) if tools else "—"
            scope = escape(srv.get("scope", "—"))

            table.add_row(name, st_markup, tool_count, scope)

            if st == "failed" and srv.get("error"):
                table.add_row(
                    "", f"  [dim red]{escape(srv['error'][:60])}[/dim red]", "", ""
                )

    def on_click(self, event) -> None:
        if getattr(event.widget, "id", None) == "mcp-close":
            self.dismiss(None)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)
