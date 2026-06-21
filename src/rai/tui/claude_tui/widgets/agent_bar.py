"""AgentBar — bottom-docked collapsed/expanded agent switcher bar."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.markup import escape
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


def _fmt_duration(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    secs = ms // 1000
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m"


def _fmt_tokens(tokens: int) -> str:
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}k"
    return str(tokens)


def _type_icon(task_type: str) -> str:
    if task_type == "local_bash":
        return "👁"
    if task_type == "remote_agent":
        return "🌐"
    return "🤖"


@dataclass
class AgentEntry:
    task_id: str
    description: str
    task_type: str = ""
    status: str = "running"
    last_tool: str = ""
    tokens: int = 0
    duration_ms: int = 0
    hitl_pending: bool = False


class _BarEntry(Static):
    """Clickable entry in the expanded agent bar."""

    def __init__(self, task_id: str, markup: str, **kwargs: Any) -> None:
        super().__init__(markup, **kwargs)
        self._task_id = task_id

    @on(events.Enter)
    @on(events.Leave)
    def _handle_hover(self, event: events.Enter | events.Leave) -> None:
        event.stop()
        self.set_class(self.is_mouse_over, "-hovered")

    def _on_click(self, event: events.Click) -> None:
        event.stop()
        bar = self.screen.query_one(AgentBar)
        bar.post_message(AgentBar.AgentSelected(self._task_id))


class AgentBar(Widget, can_focus=True):
    """Bottom-docked bar showing active subagents. Collapsed: summary line.
    Expanded: navigable list with status dots, durations, token counts."""

    BINDINGS = [
        Binding("down", "move_down", "Next agent", show=False),
        Binding("up", "move_up", "Prev agent", show=False),
        Binding("j", "move_down", "Next agent", show=False),
        Binding("k", "move_up", "Prev agent", show=False),
        Binding("enter", "open_agent", "View agent", show=False),
        Binding("x", "stop_agent", "Stop agent", show=False),
        Binding("escape", "collapse", "Close", show=False),
    ]

    DEFAULT_CSS = """
    AgentBar {
        dock: bottom;
        height: auto;
        max-height: 12;
        background: $surface;
        border-top: solid $primary 20%;
        display: none;
        padding: 0;
    }

    AgentBar.-has-agents {
        display: block;
    }

    AgentBar:focus {
        border-top: solid $accent;
    }

    #ab-summary {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }

    AgentBar.-expanded #ab-summary {
        display: none;
    }

    #ab-list {
        display: none;
        max-height: 10;
        padding: 0;
        scrollbar-size: 0 0;
    }

    AgentBar.-expanded #ab-list {
        display: block;
    }

    .ab-entry {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }

    .ab-entry.-hovered {
        background: $accent 12%;
    }

    .ab-entry.--selected {
        background: $primary 12%;
        color: $text;
    }

    .ab-entry.--hitl {
        color: $warning;
    }
    """

    expanded = reactive(False)

    class AgentSelected(Message):
        def __init__(self, task_id: str) -> None:
            super().__init__()
            self.task_id = task_id

    class AgentStopped(Message):
        def __init__(self, task_id: str) -> None:
            super().__init__()
            self.task_id = task_id

    def __init__(self) -> None:
        super().__init__()
        self._agents: dict[str, AgentEntry] = {}
        self._order: list[str] = []
        self._selected: int = 0

    def compose(self) -> ComposeResult:
        yield Static("", id="ab-summary")
        yield VerticalScroll(id="ab-list")

    def add_agent(self, entry: AgentEntry) -> None:
        if entry.task_id in self._agents:
            return
        self._agents[entry.task_id] = entry
        self._order.append(entry.task_id)
        self.add_class("-has-agents")
        self._refresh_display()

    def update_agent(self, task_id: str, last_tool: str, tokens: int, duration_ms: int) -> None:
        entry = self._agents.get(task_id)
        if not entry:
            return
        entry.last_tool = last_tool
        entry.tokens = tokens
        entry.duration_ms = duration_ms
        # Only update summary line — don't rebuild list (avoids flicker + hover loss)
        self._refresh_summary()

    def done_agent(self, task_id: str, status: str) -> None:
        entry = self._agents.get(task_id)
        if not entry:
            return
        entry.status = status
        self._refresh_display()

    def set_hitl(self, task_id: str, pending: bool) -> None:
        entry = self._agents.get(task_id)
        if not entry:
            return
        entry.hitl_pending = pending
        self._refresh_display()

    def remove_all(self) -> None:
        self._agents.clear()
        self._order.clear()
        self._selected = 0
        self.remove_class("-has-agents")
        self.remove_class("-expanded")
        self.expanded = False
        self._refresh_display()

    def watch_expanded(self, expanded: bool) -> None:
        self.set_class(expanded, "-expanded")
        if expanded:
            self._refresh_list()

    def on_click(self, event: events.Click) -> None:
        widget_id = getattr(event.widget, "id", None)
        if widget_id == "ab-summary":
            self.expanded = True
            self.focus()

    def action_move_down(self) -> None:
        if not self.expanded:
            self.expanded = True
            self.focus()
            return
        if self._order:
            self._selected = min(self._selected + 1, len(self._order) - 1)
            self._refresh_list()

    def action_move_up(self) -> None:
        if self._order:
            self._selected = max(self._selected - 1, 0)
            self._refresh_list()

    def action_open_agent(self) -> None:
        if self._order and 0 <= self._selected < len(self._order):
            task_id = self._order[self._selected]
            self.post_message(self.AgentSelected(task_id))

    def action_stop_agent(self) -> None:
        if self._order and 0 <= self._selected < len(self._order):
            task_id = self._order[self._selected]
            entry = self._agents.get(task_id)
            if entry and entry.status == "running":
                self.post_message(self.AgentStopped(task_id))

    def action_collapse(self) -> None:
        self.expanded = False
        self.app.query_one("SlashInput").focus()

    def _refresh_summary(self) -> None:
        running = sum(1 for e in self._agents.values() if e.status == "running")
        total = len(self._agents)
        if total == 0:
            return
        summary = self.query_one("#ab-summary", Static)
        if running == 0:
            summary.update(f"  [dim]{total} agent{'s' if total != 1 else ''} · click to view[/dim]")
        else:
            summary.update(f"  [green]{running}[/green] agent{'s' if running != 1 else ''} running · click to view")

    def _refresh_display(self) -> None:
        self._refresh_summary()
        if self.expanded:
            self._refresh_list()

    def _refresh_list(self) -> None:
        scroll = self.query_one("#ab-list", VerticalScroll)
        scroll.remove_children()
        for i, task_id in enumerate(self._order):
            entry = self._agents[task_id]
            markup = self._entry_markup(entry, selected=(i == self._selected))
            classes = "ab-entry"
            if i == self._selected:
                classes += " --selected"
            if entry.hitl_pending:
                classes += " --hitl"
            scroll.mount(_BarEntry(task_id, markup, classes=classes))

    def _entry_markup(self, entry: AgentEntry, selected: bool) -> str:
        icon = _type_icon(entry.task_type)
        desc = escape(entry.description[:40])
        dur = _fmt_duration(entry.duration_ms)
        tok = _fmt_tokens(entry.tokens)

        if entry.hitl_pending:
            dot = "[yellow bold]⚠[/yellow bold]"
        elif entry.status == "running":
            dot = "[green]●[/green]"
        elif entry.status == "completed":
            dot = "[green dim]✓[/green dim]"
        else:
            dot = "[red]✗[/red]"

        metrics = f"[dim]{dur} · ↓{tok}[/dim]" if entry.tokens else f"[dim]{dur}[/dim]"
        hint = "  [dim]Enter to view · x to stop[/dim]" if selected and entry.status == "running" else ""
        return f"  {dot} {icon} {desc}  {metrics}{hint}"
