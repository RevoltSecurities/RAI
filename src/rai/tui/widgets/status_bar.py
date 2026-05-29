"""StatusBar — mode badge / tokens / auto-approve / run status / branch."""

from __future__ import annotations

import os

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class StatusBar(Widget):
    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $surface 40%;
        border-top: solid $accent 20%;
        padding: 0 1;
        layout: horizontal;
    }
    """

    tokens_in:    reactive[int]  = reactive(0)
    tokens_out:   reactive[int]  = reactive(0)
    auto_approve: reactive[bool] = reactive(False)
    run_status:   reactive[str]  = reactive("idle")
    mode_label:   reactive[str]  = reactive("")    # "plan" | "hitl" | "ask_user" | ""
    branch:       reactive[str]  = reactive("")

    def compose(self) -> ComposeResult:
        yield Static("", id="status-content")

    def _refresh(self) -> None:
        cwd = os.getcwd()
        if len(cwd) > 28:
            cwd = "…" + cwd[-26:]

        # Mode badge (left side, amber when active)
        if self.mode_label == "plan":
            badge = "[bold yellow]◈ plan mode[/bold yellow]  [dim]·[/dim]  "
        elif self.mode_label == "hitl":
            badge = "[bold yellow]⚠ approval[/bold yellow]  [dim]·[/dim]  "
        elif self.mode_label == "ask_user":
            badge = "[bold yellow]? ask user[/bold yellow]  [dim]·[/dim]  "
        else:
            badge = ""

        # Token counts
        def _fmt(n: int) -> str:
            if n >= 1_000_000:
                return f"{n / 1_000_000:.1f}M"
            if n >= 1_000:
                return f"{n / 1_000:.1f}k"
            return str(n)

        tokens = (
            f"[dim]↓[/dim][cyan]{_fmt(self.tokens_in)}[/cyan]"
            f" [dim]↑[/dim][cyan]{_fmt(self.tokens_out)}[/cyan]"
        )

        auto = "[green]auto[/green]" if self.auto_approve else "[dim]manual[/dim]"

        status_color = {
            "idle": "dim", "running": "yellow",
            "error": "red", "done": "green", "cancelled": "dim",
        }.get(self.run_status, "dim")
        status = f"[{status_color}]{self.run_status}[/{status_color}]"

        # Branch at right (max 20 chars)
        branch_part = ""
        if self.branch:
            b = self.branch
            if len(b) > 20:
                b = b[:19] + "…"
            branch_part = f"  [dim]│[/dim]  [dim]{b}[/dim]"

        text = (
            f" {badge}{tokens}"
            f"  [dim]│[/dim]  {auto}"
            f"  [dim]│[/dim]  {status}"
            f"  [dim]│[/dim]  [dim]{cwd}[/dim]"
            f"{branch_part}"
        )
        try:
            self.query_one("#status-content", Static).update(text)
        except Exception:
            pass

    def watch_tokens_in(self,    _: int)  -> None: self._refresh()
    def watch_tokens_out(self,   _: int)  -> None: self._refresh()
    def watch_auto_approve(self, _: bool) -> None: self._refresh()
    def watch_run_status(self,   _: str)  -> None: self._refresh()
    def watch_mode_label(self,   _: str)  -> None: self._refresh()
    def watch_branch(self,       _: str)  -> None: self._refresh()

    def on_mount(self) -> None:
        self._refresh()

    def add_usage(self, tokens_in: int = 0, tokens_out: int = 0) -> None:
        self.tokens_in  += tokens_in
        self.tokens_out += tokens_out
