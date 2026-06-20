"""AgentPanel — two-mode overlay: clickable agent list + per-agent detail feed."""

from __future__ import annotations

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.markup import escape
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from rai.tui.claude_tui.widgets.agent_feed import AgentFeed, _type_icon


class AgentRow(Widget, can_focus=False):
    """Clickable row representing one subagent — hover highlight + click to select."""

    DEFAULT_CSS = """
    AgentRow {
        height: 3;
        padding: 0 1;
        border-bottom: solid $primary 10%;
        layout: horizontal;
    }

    AgentRow.-hovered {
        background: $accent 12%;
    }

    AgentRow.-selected {
        background: $primary 15%;
        border-left: thick $accent;
    }

    .ar-icon {
        width: 3;
        height: 3;
        content-align: center middle;
    }

    .ar-name {
        width: 1fr;
        height: 3;
        padding: 0 1;
        content-align: left middle;
        color: $accent;
        text-style: bold;
    }

    .ar-status {
        width: auto;
        min-width: 14;
        height: 3;
        padding: 0 2;
        content-align: right middle;
        color: $text-muted;
    }
    """

    class Selected(Message):
        def __init__(self, task_id: str) -> None:
            super().__init__()
            self.task_id = task_id

    def __init__(self, task_id: str, description: str, task_type: str, status: str = "running") -> None:
        super().__init__(id=f"ar-{task_id}")
        self._task_id = task_id
        self._description = description
        self._task_type = task_type
        self._status = status

    def compose(self) -> ComposeResult:
        icon = _type_icon(self._task_type)
        yield Static(icon, classes="ar-icon")
        yield Static(f"[bold]{escape(self._description)}[/bold]", classes="ar-name")
        yield Static(self._status_markup(), classes="ar-status")

    @on(events.Enter)
    @on(events.Leave)
    def _handle_hover(self, event: events.Enter | events.Leave) -> None:
        event.stop()
        self.set_class(self.is_mouse_over, "-hovered")

    def _on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Selected(self._task_id))

    def update_status(self, status: str, summary: str = "") -> None:
        self._status = status
        try:
            st = self.query_one(".ar-status", Static)
            st.update(self._status_markup())
        except NoMatches:
            pass

    def _status_markup(self) -> str:
        if self._status == "completed":
            return "[green]✓ completed[/green]"
        if self._status == "running":
            return "[dim]running[/dim]"
        if self._status == "stopped":
            return "[yellow]■ stopped[/yellow]"
        return f"[red]✗ {escape(self._status)}[/red]"


class AgentPanel(Widget):
    """Overlay panel with two modes: agent list (default) and detail feed view.

    All AgentFeed widgets are mounted permanently (hidden) so they can
    receive steps without DOM query errors. show_detail() toggles visibility.
    """

    DEFAULT_CSS = """
    AgentPanel {
        layer: agents;
        dock: bottom;
        height: 50%;
        offset-y: 100%;
        background: $surface;
        border-top: double $accent;
        transition: offset 250ms in_out_cubic;
        layout: vertical;
    }

    AgentPanel.-visible {
        offset-y: 0;
    }

    #ap-header {
        height: 3;
        background: $panel;
        border-bottom: solid $primary 15%;
        padding: 0 2;
        layout: horizontal;
        align: left middle;
    }

    #ap-back {
        width: 8;
        color: $accent;
        text-style: bold;
        content-align: left middle;
        height: 3;
        display: none;
    }

    AgentPanel.-detail #ap-back {
        display: block;
    }

    #ap-title {
        width: 1fr;
        color: $accent;
        text-style: bold;
        content-align: left middle;
        height: 3;
    }

    #ap-hint {
        width: auto;
        margin-right: 2;
        color: $text-muted;
        content-align: right middle;
        height: 3;
    }

    #ap-close {
        width: 5;
        text-align: right;
        color: $error;
        text-style: bold;
        content-align: right middle;
        height: 3;
    }

    #ap-list {
        height: 1fr;
        padding: 0;
        scrollbar-size: 1 1;
        scrollbar-color: $accent 25%;
    }

    AgentPanel.-detail #ap-list {
        display: none;
    }

    #ap-feeds {
        height: 1fr;
        display: none;
    }

    AgentPanel.-detail #ap-feeds {
        display: block;
    }

    #ap-feeds AgentFeed {
        display: none;
    }

    #ap-feeds AgentFeed.-active {
        display: block;
    }
    """

    _mode = reactive("list")

    def __init__(self) -> None:
        super().__init__()
        self._feeds: dict[str, AgentFeed] = {}
        self._rows: dict[str, AgentRow] = {}
        self._feed_order: list[str] = []
        self._detail_task_id: str = ""

    def compose(self) -> ComposeResult:
        with Horizontal(id="ap-header"):
            yield Static("[bold]← Back[/bold]", id="ap-back")
            yield Static("[bold]Subagents[/bold]", id="ap-title")
            yield Static("[dim]esc to close · click agent to view[/dim]", id="ap-hint")
            yield Static("[bold red]\\[x][/bold red]", id="ap-close")
        yield VerticalScroll(id="ap-list")
        yield Vertical(id="ap-feeds")

    class Closed(Message):
        """Posted when user clicks [x] to dismiss the panel."""

    def on_click(self, event: events.Click) -> None:
        widget_id = getattr(event.widget, "id", None)
        if widget_id == "ap-close":
            self.remove_class("-visible")
            self.post_message(self.Closed())
            try:
                self.app.query_one("SlashInput").focus()
            except Exception:
                pass
        elif widget_id == "ap-back":
            self.show_list()

    @on(AgentRow.Selected)
    def _on_row_selected(self, event: AgentRow.Selected) -> None:
        event.stop()
        self.show_detail(event.task_id)

    def watch__mode(self, mode: str) -> None:
        self.set_class(mode == "detail", "-detail")

    def add_feed(self, task_id: str, description: str, task_type: str) -> None:
        if task_id in self._rows:
            return
        # Create and mount the row in the list
        row = AgentRow(task_id, description, task_type)
        self._rows[task_id] = row
        self._feed_order.append(task_id)
        try:
            self.query_one("#ap-list", VerticalScroll).mount(row)
        except NoMatches:
            pass

        # Create and permanently mount the feed (hidden via CSS)
        feed = AgentFeed(task_id, description, task_type)
        self._feeds[task_id] = feed
        try:
            self.query_one("#ap-feeds", Vertical).mount(feed)
        except NoMatches:
            pass

    def show_list(self) -> None:
        self._mode = "list"
        # Hide current feed
        if self._detail_task_id and self._detail_task_id in self._feeds:
            self._feeds[self._detail_task_id].remove_class("-active")
        self._detail_task_id = ""
        try:
            self.query_one("#ap-title", Static).update("[bold]Subagents[/bold]")
            self.query_one("#ap-hint", Static).update("[dim]esc to close · click agent to view[/dim]")
        except NoMatches:
            pass

    def show_detail(self, task_id: str) -> None:
        if task_id not in self._feeds:
            return
        # Hide previous feed
        if self._detail_task_id and self._detail_task_id in self._feeds:
            self._feeds[self._detail_task_id].remove_class("-active")
        self._detail_task_id = task_id
        self._mode = "detail"
        # Show target feed
        self._feeds[task_id].add_class("-active")
        try:
            icon = _type_icon(self._feeds[task_id]._task_type)
            desc = escape(self._feeds[task_id]._description)
            self.query_one("#ap-title", Static).update(f"{icon} [bold]{desc}[/bold]")
            self.query_one("#ap-hint", Static).update("[dim]esc to close · ←/→ switch[/dim]")
        except NoMatches:
            pass

    def show_agent(self, task_id: str) -> None:
        self.show_detail(task_id)

    def get_feed(self, task_id: str) -> AgentFeed | None:
        return self._feeds.get(task_id)

    def next_agent(self) -> None:
        if len(self._feed_order) < 2 or not self._detail_task_id:
            return
        idx = self._feed_order.index(self._detail_task_id) if self._detail_task_id in self._feed_order else 0
        idx = (idx + 1) % len(self._feed_order)
        self.show_detail(self._feed_order[idx])

    def prev_agent(self) -> None:
        if len(self._feed_order) < 2 or not self._detail_task_id:
            return
        idx = self._feed_order.index(self._detail_task_id) if self._detail_task_id in self._feed_order else 0
        idx = (idx - 1) % len(self._feed_order)
        self.show_detail(self._feed_order[idx])

    def update_agent_status(self, task_id: str, status: str, summary: str = "") -> None:
        row = self._rows.get(task_id)
        if row:
            row.update_status(status, summary)

    def clear_all(self) -> None:
        self._feeds.clear()
        self._rows.clear()
        self._feed_order.clear()
        self._detail_task_id = ""
        self._mode = "list"
        try:
            self.query_one("#ap-list", VerticalScroll).remove_children()
            self.query_one("#ap-feeds", Vertical).remove_children()
        except NoMatches:
            pass
        try:
            self.query_one("#ap-title", Static).update("[bold]Subagents[/bold]")
        except NoMatches:
            pass
