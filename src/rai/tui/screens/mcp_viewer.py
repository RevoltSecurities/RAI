"""MCPViewerScreen — scrollable MCP server + tool viewer for /mcp command."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, Static


class MCPViewerScreen(ModalScreen):
    """Scrollable modal showing all loaded MCP servers and their tools."""

    DEFAULT_CSS = """
    MCPViewerScreen {
        align: center middle;
    }
    MCPViewerScreen > Vertical {
        width: 88;
        max-width: 95%;
        height: 85%;
        background: $surface;
        border: round $accent;
        padding: 1 2;
    }
    MCPViewerScreen #mcp-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
        height: auto;
    }
    MCPViewerScreen .mcp-scroll {
        height: 1fr;
        border: round $accent 20%;
        background: $background;
    }
    MCPViewerScreen #mcp-content {
        height: auto;
        padding: 0 1;
    }
    MCPViewerScreen .server-header {
        text-style: bold;
        color: $accent;
        margin-top: 1;
    }
    MCPViewerScreen .tool-row {
        height: 1;
        padding: 0 2;
    }
    MCPViewerScreen .tool-desc {
        height: 1;
        color: $text-muted;
        text-style: italic;
        padding: 0 4;
    }
    MCPViewerScreen #mcp-help {
        height: 1;
        color: $text-muted;
        text-align: center;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape",   "dismiss_screen", "Close"),
        Binding("q",        "dismiss_screen", "Close",    show=False),
        Binding("up",       "scroll_up",      "Up",       show=False, priority=True),
        Binding("down",     "scroll_down",    "Down",     show=False, priority=True),
        Binding("pageup",   "page_up",        "PgUp",     show=False, priority=True),
        Binding("pagedown", "page_down",      "PgDn",     show=False, priority=True),
        Binding("j",        "scroll_down",    "Down",     show=False),
        Binding("k",        "scroll_up",      "Up",       show=False),
        Binding("home",     "scroll_home",    "Top",      show=False),
        Binding("end",      "scroll_end",     "Bottom",   show=False),
    ]

    def __init__(self, server_infos: list[Any], **kwargs) -> None:
        super().__init__(**kwargs)
        self._server_infos = server_infos

    def compose(self) -> ComposeResult:
        total_tools = sum(len(s.tools) for s in self._server_infos)
        n = len(self._server_infos)

        with Vertical():
            yield Label(
                f"[bold]MCP Servers[/bold]  "
                f"[dim]{n} server{'s' if n != 1 else ''}  ·  "
                f"{total_tools} tool{'s' if total_tools != 1 else ''} loaded[/dim]",
                id="mcp-title",
            )
            with VerticalScroll(classes="mcp-scroll"):
                yield Container(id="mcp-content")
            yield Static(
                "[dim]↑↓/j/k[/dim] scroll  [dim]·[/dim]  [dim]PgUp/PgDn[/dim] page  "
                "[dim]·[/dim]  [dim]esc/q[/dim] close",
                id="mcp-help",
            )
            yield Footer()

    async def on_mount(self) -> None:
        container = self.query_one("#mcp-content", Container)
        widgets: list[Static | Label] = []

        if not self._server_infos:
            widgets.append(Static(
                "[dim]No MCP servers loaded.\n"
                "Config files checked:\n"
                "  ~/.rai/.mcp.json\n"
                "  ~/.rai/agents/<name>/mcp.json\n"
                "  <cwd>/.mcp.json[/dim]"
            ))
        else:
            for i, info in enumerate(self._server_infos):
                classes = "server-header"
                hdr = Static(
                    f"[bold][cyan]{info.name}[/cyan][/bold]  [dim]{info.transport}[/dim]",
                    classes=classes,
                )
                # Remove the top margin for the very first header
                if i == 0:
                    hdr.styles.margin = (0, 0, 0, 0)
                widgets.append(hdr)

                if info.tools:
                    for tool in info.tools:
                        widgets.append(Static(
                            f"  [green]└[/green] [bold]{tool.name}[/bold]",
                            classes="tool-row",
                        ))
                        if tool.description:
                            desc = tool.description
                            if len(desc) > 72:
                                desc = desc[:72] + "…"
                            widgets.append(Static(
                                f"    [dim]{desc}[/dim]",
                                classes="tool-desc",
                            ))
                else:
                    widgets.append(Static("  [dim](no tools)[/dim]", classes="tool-row"))

        await container.mount(*widgets)

    # ------------------------------------------------------------------
    # Scroll actions — drive the VerticalScroll container directly
    # ------------------------------------------------------------------

    def _scroller(self) -> VerticalScroll:
        return self.query_one(".mcp-scroll", VerticalScroll)

    def action_scroll_up(self) -> None:
        self._scroller().scroll_up()

    def action_scroll_down(self) -> None:
        self._scroller().scroll_down()

    def action_page_up(self) -> None:
        self._scroller().scroll_page_up()

    def action_page_down(self) -> None:
        self._scroller().scroll_page_down()

    def action_scroll_home(self) -> None:
        self._scroller().scroll_home(animate=False)

    def action_scroll_end(self) -> None:
        self._scroller().scroll_end(animate=False)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)
