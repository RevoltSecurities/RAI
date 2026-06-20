"""SubagentRow — animated inline subagent progress with auto-remove on done."""

from __future__ import annotations

from typing import Any

from textual.markup import escape
from textual.message import Message
from textual.widgets import Static

_BLOOM = ("·", ".", "+", "✶", "✻", "✽", "✶", "+", "·", ".")


class SubagentRow(Static):
    """One-liner subagent progress — animated spinner, updates live, auto-removes after done."""

    class Selected(Message):
        def __init__(self, task_id: str) -> None:
            super().__init__()
            self.task_id = task_id

    DEFAULT_CSS = """
    SubagentRow {
        margin: 0 0 0 2;
        height: auto;
        color: $text-muted;
    }
    SubagentRow.--done-ok {
        color: $success;
    }
    SubagentRow.--done-err {
        color: $error;
    }
    """

    def __init__(self, task_id: str, description: str, task_type: str = "") -> None:
        self._task_id     = task_id
        self._description = description
        self._task_type   = task_type  # "local_bash" | "local_agent" | "remote_agent" | ""
        self._last_tool:  str   = ""
        self._tokens:     int   = 0
        self._done:       bool  = False
        self._status:     str   = ""
        self._summary:    str   = ""
        self._spin_pos:   int   = 0
        self._timer:      Any   = None
        super().__init__(self._markup(), id=f"sub-{task_id}")

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.15, self._tick_spinner)

    def _tick_spinner(self) -> None:
        if self._done:
            if self._timer:
                self._timer.stop()
                self._timer = None
            return
        self._spin_pos += 1
        self.update(self._markup())

    def _icon(self) -> str:
        if self._task_type == "local_bash":
            return "👁"
        if self._task_type == "remote_agent":
            return "🌐"
        return "🤖"

    def _markup(self) -> str:
        desc = escape(self._description)
        icon = self._icon()
        if self._done:
            if self._status == "completed":
                summary = escape(self._summary[:80])
                return f"{icon} [bold]{desc}[/bold] — {summary}"
            return f"{icon} [bold]{desc}[/bold] — {escape(self._status)}"

        frame = _BLOOM[self._spin_pos % len(_BLOOM)]
        if self._last_tool:
            tool = escape(self._last_tool)
            tok  = f"  [dim]{self._tokens} tok[/dim]" if self._tokens else ""
            return f"[yellow]{frame}[/yellow] {icon} [bold]{desc}[/bold]  [dim]{tool}[/dim]{tok}"
        return f"[yellow]{frame}[/yellow] {icon} [bold]{desc}[/bold]"

    def update_progress(self, last_tool: str, tokens: int) -> None:
        self._last_tool = last_tool
        self._tokens    = tokens

    def on_click(self) -> None:
        self.post_message(self.Selected(self._task_id))

    def mark_done(self, status: str, summary: str, tokens: int, *, auto_remove: bool = True) -> None:
        self._done    = True
        self._status  = status
        self._summary = summary
        self._tokens  = tokens
        icon = self._icon()
        if status == "completed":
            self.add_class("--done-ok")
            self.update(f"[green]✓[/green] {icon} [bold]{escape(self._description)}[/bold]"
                        + (f" — {escape(summary[:80])}" if summary else ""))
        else:
            self.add_class("--done-err")
            self.update(f"[red]✗[/red] {icon} [bold]{escape(self._description)}[/bold] — {escape(status)}")
        if auto_remove:
            self.set_timer(5, self._auto_remove)

    def _auto_remove(self) -> None:
        self.remove()
