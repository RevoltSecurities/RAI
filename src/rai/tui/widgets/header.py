"""HeaderBar — one-line top bar: run status / agent / server / thread / branch."""

from __future__ import annotations

import subprocess

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


def _git_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        return result.stdout.strip() or ""
    except Exception:
        return ""


class HeaderBar(Widget):
    DEFAULT_CSS = """
    HeaderBar {
        height: 1;
        background: $primary 30%;
        border-bottom: solid $accent 20%;
        padding: 0 2;
        layout: horizontal;
    }
    """

    agent:      reactive[str]  = reactive("rai")
    server:     reactive[str]  = reactive("http://127.0.0.1:8000")
    thread_id:  reactive[str]  = reactive("")
    server_ok:  reactive[bool] = reactive(False)
    run_mode:   reactive[str]  = reactive("idle")  # "idle" | "running" | "bg:N"
    plan_mode:  reactive[bool] = reactive(False)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._branch = _git_branch()

    def compose(self) -> ComposeResult:
        yield Static("", id="header-content")

    def _refresh_content(self) -> None:
        # Run status dot (left of agent name)
        if self.run_mode == "running":
            run_dot = "[yellow]●[/yellow]"
        elif self.run_mode.startswith("bg:"):
            n = self.run_mode[3:]
            run_dot = f"[blue]●[/blue][dim]{n}[/dim]"
        else:
            run_dot = "[green]●[/green]"

        server_dot = "[green]●[/green]" if self.server_ok else "[red dim]●[/red dim]"
        thread     = f" [dim]│[/dim] [cyan]{self.thread_id[:12]}[/cyan]" if self.thread_id else ""
        branch     = f" [dim]│[/dim] [yellow]{self._branch}[/yellow]" if self._branch else ""
        plan_badge = "  [bold yellow]◈ plan[/bold yellow]" if self.plan_mode else ""

        text = (
            f" {run_dot} [bold]{self.agent}[/bold]{plan_badge}"
            f"  {server_dot} [dim]{self.server}[/dim]"
            f"{thread}{branch}"
        )
        try:
            self.query_one("#header-content", Static).update(text)
        except Exception:
            pass

    def watch_agent(self,     _: str)  -> None: self._refresh_content()
    def watch_server(self,    _: str)  -> None: self._refresh_content()
    def watch_thread_id(self, _: str)  -> None: self._refresh_content()
    def watch_server_ok(self, _: bool) -> None: self._refresh_content()
    def watch_run_mode(self,  _: str)  -> None: self._refresh_content()
    def watch_plan_mode(self, _: bool) -> None: self._refresh_content()

    def on_mount(self) -> None:
        self._refresh_content()
