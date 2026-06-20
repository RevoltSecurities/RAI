"""ContextScreen — modal showing context window usage breakdown."""

from __future__ import annotations

from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.markup import escape
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from rai.client.claude import ClaudeServeClient


class ContextScreen(ModalScreen[None]):
    """Context window usage modal — token breakdown, skills, tools, agents."""

    BINDINGS = [Binding("escape", "dismiss_screen", "Close", priority=True)]

    CSS = """
    ContextScreen {
        align: center middle;
        background: $background 65%;
    }

    #ctx-container {
        width: 88%;
        max-width: 120;
        height: 82%;
        border: round $primary 50%;
        background: $surface;
        padding: 0;
    }

    #ctx-header {
        height: 3;
        background: $panel;
        border-bottom: solid $primary 20%;
        padding: 0 2;
        align: left middle;
    }

    #ctx-title {
        text-style: bold;
        width: 1fr;
        color: $primary;
    }

    #ctx-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }

    #ctx-scroll {
        height: 1fr;
        padding: 1 2;
        scrollbar-size: 1 1;
    }

    #ctx-bar {
        height: auto;
        margin-bottom: 1;
    }

    #ctx-table {
        height: auto;
        max-height: 12;
        margin: 0 0 1 0;
    }

    .ctx-section-hdr {
        color: $accent;
        text-style: bold;
        margin-top: 1;
    }

    .ctx-section-body {
        height: auto;
        color: $text-muted;
        padding: 0 0 0 2;
    }

    #ctx-hint {
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
        with Container(id="ctx-container"):
            with Horizontal(id="ctx-header"):
                yield Static("Context Window", id="ctx-title")
                yield Static("[bold red]\\[x][/bold red]", id="ctx-close")
            with VerticalScroll(id="ctx-scroll"):
                yield Static("[dim]Loading…[/dim]", id="ctx-bar")
                table = DataTable(id="ctx-table", cursor_type="none", zebra_stripes=True)
                yield table
                yield Static("", id="ctx-compact-status")
                yield Static("", classes="ctx-section-hdr", id="ctx-skills-hdr")
                yield Static("", classes="ctx-section-body", id="ctx-skills")
                yield Static("", classes="ctx-section-hdr", id="ctx-tools-hdr")
                yield Static("", classes="ctx-section-body", id="ctx-tools")
                yield Static("", classes="ctx-section-hdr", id="ctx-agents-hdr")
                yield Static("", classes="ctx-section-body", id="ctx-agents")
            yield Static("[dim]esc[/dim] close", id="ctx-hint")

    def on_mount(self) -> None:
        table = self.query_one("#ctx-table", DataTable)
        table.add_columns("Category", "Tokens", "%")
        self._load_data()

    @work
    async def _load_data(self) -> None:
        bar_w = self.query_one("#ctx-bar", Static)

        try:
            data = await self._client.sessions.get_context(self._session_id)
        except Exception as exc:
            err = str(exc)
            if "404" in err:
                bar_w.update("Session not connected. Send a message first.")
            else:
                bar_w.update(f"[red]Error: {escape(str(exc)[:80])}[/red]")
            return

        total = data.get("totalTokens", 0)
        maximum = data.get("maxTokens", 0)
        pct = data.get("percentage", 0.0)
        categories = data.get("categories", [])
        auto_compact = data.get("isAutoCompactEnabled", False)
        threshold = data.get("autoCompactThreshold")

        bar_len = 40
        filled = int(bar_len * pct / 100)
        bar_str = "▓" * filled + "░" * (bar_len - filled)
        color = "green" if pct < 60 else ("yellow" if pct < 85 else "red")
        bar_w.update(
            f"[bold]{total:,}[/bold] / {maximum:,} tokens  "
            f"[{color}]{bar_str}[/{color}]  [{color}]{pct:.1f}%[/{color}]"
        )

        table = self.query_one("#ctx-table", DataTable)
        for cat in categories:
            name = cat.get("name", "?")
            tokens = cat.get("tokens", 0)
            cat_pct = (tokens / maximum * 100) if maximum else 0
            table.add_row(escape(name), f"{tokens:,}", f"{cat_pct:.1f}%")

        compact_w = self.query_one("#ctx-compact-status", Static)
        if auto_compact:
            if threshold and threshold > 100:
                thr_str = f" (threshold: {threshold:,} tokens)"
            elif threshold:
                thr_str = f" (threshold: {threshold}%)"
            else:
                thr_str = ""
            compact_w.update(f"[green]●[/green] Auto-compact enabled{thr_str}")
        else:
            compact_w.update("[dim]●[/dim] Auto-compact disabled")

        self._render_skills(data)
        self._render_tools(data)
        self._render_agents(data)

    def _render_skills(self, data: dict[str, Any]) -> None:
        skills_data = data.get("skills", {})
        hdr = self.query_one("#ctx-skills-hdr", Static)
        body = self.query_one("#ctx-skills", Static)

        if isinstance(skills_data, dict):
            skill_list = skills_data.get("skillFrontmatter", [])
            total_skills = skills_data.get("totalSkills", len(skill_list))
            if skill_list:
                hdr.update(f"Skills ({total_skills})")
                names = [escape(s.get("name", "?")) for s in skill_list[:15]]
                body.update("  ".join(f"[cyan]{n}[/cyan]" for n in names))
                return
        elif skills_data:
            hdr.update(f"Skills ({len(skills_data)})")
            names = [escape(s) if isinstance(s, str) else escape(s.get("name", "?")) for s in skills_data[:15]]
            body.update("  ".join(f"[cyan]{n}[/cyan]" for n in names))
            return

        hdr.display = False
        body.display = False

    def _render_tools(self, data: dict[str, Any]) -> None:
        system_tools = data.get("systemTools", [])
        mcp_tools = data.get("mcpTools", [])
        hdr = self.query_one("#ctx-tools-hdr", Static)
        body = self.query_one("#ctx-tools", Static)

        lines = []
        if system_tools:
            names = [t.get("name", t) if isinstance(t, dict) else t for t in system_tools[:12]]
            lines.append(f"[dim]System ({len(system_tools)}):[/dim] " + ", ".join(escape(str(n)) for n in names))
        if mcp_tools:
            names = [t.get("name", "?") if isinstance(t, dict) else t for t in mcp_tools[:12]]
            lines.append(f"[dim]MCP ({len(mcp_tools)}):[/dim] " + ", ".join(escape(str(n)) for n in names))

        if lines:
            hdr.update(f"Tools ({len(system_tools) + len(mcp_tools)})")
            body.update("\n".join(lines))
        else:
            hdr.display = False
            body.display = False

    def _render_agents(self, data: dict[str, Any]) -> None:
        agents_data = data.get("agents", [])
        hdr = self.query_one("#ctx-agents-hdr", Static)
        body = self.query_one("#ctx-agents", Static)

        if agents_data:
            hdr.update(f"Agents ({len(agents_data)})")
            names = []
            for a in agents_data[:15]:
                if isinstance(a, dict):
                    names.append(a.get("name") or a.get("agentType") or a.get("type") or "?")
                else:
                    names.append(str(a))
            body.update("  ".join(f"[magenta]{escape(n)}[/magenta]" for n in names))
        else:
            hdr.display = False
            body.display = False

    def on_click(self, event) -> None:
        if getattr(event.widget, "id", None) == "ctx-close":
            self.dismiss(None)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)
