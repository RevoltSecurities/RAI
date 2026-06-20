"""AgentFeed — per-agent scrollable activity feed showing step-by-step progress."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.markup import escape
from textual.widget import Widget
from textual.widgets import Rule, Static

_BLOOM = ("·", ".", "+", "✶", "✻", "✽", "✶", "+", "·", ".")


def _type_icon(task_type: str) -> str:
    if task_type == "local_bash":
        return "👁"
    if task_type == "remote_agent":
        return "🌐"
    return "🤖"


def _fmt_duration(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    secs = ms // 1000
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    return f"{mins}m {secs % 60}s"


def _fmt_tokens(tokens: int) -> str:
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}k"
    return str(tokens)


class AgentFeed(Widget):
    """Scrollable activity log for a single subagent.

    Steps are buffered if DOM isn't ready yet; replayed on mount.
    """

    DEFAULT_CSS = """
    AgentFeed {
        height: 1fr;
        padding: 0;
        layout: vertical;
    }

    .af-header {
        height: 3;
        padding: 0 2;
        background: $panel;
        align: left middle;
    }

    .af-header-title {
        width: 1fr;
        color: $accent;
        text-style: bold;
    }

    .af-header-status {
        width: auto;
        color: $text-muted;
    }

    .af-rule {
        height: 1;
        margin: 0;
        color: $primary 20%;
    }

    .af-scroll {
        height: 1fr;
        padding: 0;
        scrollbar-size: 1 1;
        scrollbar-color: $accent 25%;
    }

    .af-step {
        padding: 0 2 0 4;
        color: $text-muted;
        height: auto;
    }

    .af-step.--latest {
        color: $text;
    }

    .af-step.--new {
        background: $accent 6%;
    }

    .af-footer {
        height: 1;
        padding: 0 2;
        background: $panel;
        color: $text-muted;
    }
    """

    def __init__(self, task_id: str, description: str, task_type: str) -> None:
        super().__init__(id=f"af-{task_id}")
        self._task_id = task_id
        self._description = description
        self._task_type = task_type
        self._steps: list[dict] = []
        self._done = False
        self._status = ""
        self._summary = ""
        self._spin_pos = 0
        self._latest_widget: Static | None = None
        self._is_dom_ready = False

    def compose(self) -> ComposeResult:
        icon = _type_icon(self._task_type)
        with Horizontal(classes="af-header"):
            yield Static(
                f"{icon} [bold]{escape(self._description)}[/bold]",
                classes="af-header-title",
            )
            yield Static("[dim]running[/dim]", classes="af-header-status")
        yield Rule(classes="af-rule")
        yield VerticalScroll(classes="af-scroll")
        yield Static("[dim]0s · 0 tokens[/dim]", classes="af-footer")

    def on_mount(self) -> None:
        self._is_dom_ready = True
        self._timer = self.set_interval(0.15, self._tick_spinner)
        # Replay any steps buffered before mount
        for i, step in enumerate(self._steps):
            self._render_step(step, is_latest=(i == len(self._steps) - 1))
        if self._done:
            self._apply_done(self._status, self._summary)

    def _tick_spinner(self) -> None:
        if self._done:
            return
        self._spin_pos += 1
        if self._latest_widget is not None and self._steps:
            self._latest_widget.update(self._step_markup(self._steps[-1], is_latest=True))

    def _step_markup(self, step: dict, is_latest: bool = False) -> str:
        desc = escape(step["description"])
        dur = _fmt_duration(step["duration_ms"])
        if is_latest and not self._done:
            frame = _BLOOM[self._spin_pos % len(_BLOOM)]
            return f"  [yellow]{frame}[/yellow] {desc}  [dim]{dur}[/dim]"
        return f"  [green]●[/green] {desc}  [dim]{dur}[/dim]"

    def add_step(self, description: str, last_tool: str, tokens: int, duration_ms: int) -> None:
        if not description and not last_tool:
            return
        display_desc = description or last_tool
        step = {"description": display_desc, "last_tool": last_tool, "tokens": tokens, "duration_ms": duration_ms}
        self._steps.append(step)

        if not self._is_dom_ready:
            return
        self._render_step(step, is_latest=True)

    def _render_step(self, step: dict, is_latest: bool) -> None:
        try:
            scroll = self.query_one(".af-scroll", VerticalScroll)
        except NoMatches:
            return

        # Finalize previous latest widget
        if self._latest_widget is not None and is_latest and len(self._steps) > 1:
            prev_step = self._steps[-2]
            self._latest_widget.update(self._step_markup(prev_step, is_latest=False))
            self._latest_widget.remove_class("--latest")
            self._latest_widget.remove_class("--new")

        if is_latest and not self._done:
            new_widget = Static(self._step_markup(step, is_latest=True), classes="af-step --latest --new")
            self._latest_widget = new_widget
        else:
            new_widget = Static(self._step_markup(step, is_latest=False), classes="af-step")

        scroll.mount(new_widget)
        scroll.scroll_end(animate=False)

        if is_latest:
            self.set_timer(0.5, lambda: new_widget.remove_class("--new"))

        try:
            footer = self.query_one(".af-footer", Static)
            footer.update(f"[dim]{_fmt_duration(step['duration_ms'])} · {_fmt_tokens(step['tokens'])} tokens[/dim]")
        except NoMatches:
            pass

    def mark_done(self, status: str, summary: str) -> None:
        self._done = True
        self._status = status
        self._summary = summary

        if not self._is_dom_ready:
            return
        self._apply_done(status, summary)

    def _apply_done(self, status: str, summary: str) -> None:
        if self._latest_widget and self._steps:
            self._latest_widget.update(self._step_markup(self._steps[-1], is_latest=False))
            self._latest_widget.remove_class("--latest")

        try:
            header_status = self.query_one(".af-header-status", Static)
            if status == "completed":
                header_status.update("[green]✓ completed[/green]")
            elif status == "stopped":
                header_status.update("[yellow]■ stopped[/yellow]")
            else:
                header_status.update(f"[red]✗ {escape(status)}[/red]")
        except NoMatches:
            pass

        if summary:
            try:
                scroll = self.query_one(".af-scroll", VerticalScroll)
                summary_widget = Static(
                    f"\n  [dim]Summary:[/dim] {escape(summary[:200])}",
                    classes="af-step",
                )
                scroll.mount(summary_widget)
                scroll.scroll_end(animate=False)
            except NoMatches:
                pass

    def set_hitl_indicator(self, pending: bool) -> None:
        if not self._is_dom_ready:
            return
        try:
            header_status = self.query_one(".af-header-status", Static)
            if pending:
                header_status.update("[yellow bold]⚠ Awaiting permission[/yellow bold]")
            elif not self._done:
                header_status.update("[dim]running[/dim]")
        except NoMatches:
            pass
